"""One extract from start to end: download the .pbf, make sure its timetable feeds are indexed, build, write, clean up.

Run in worker processes by batch.py (everything here is module level and takes plain arguments)."""
import concurrent.futures as cf
import datetime
import json
import os
import pathlib
import sys
import time
import urllib.error

from . import edits as edits_mod, feeds, gtfsmerge, osmread, output, regions

SERVICE_DIR = pathlib.Path(__file__).resolve().parents[3] / "earth-bus-routes" / "companion"      # D:/Projects/earth-bus-routes/companion
LOCK_WAIT_S = 3 * 3600


def service_module():
    """The data service's module (its GTFS indexer is the one place that reads a feed's tables)."""
    d = os.environ.get("EBR_SERVICE_DIR") or str(SERVICE_DIR)
    if d not in sys.path:
        sys.path.insert(0, d)
    import bus_routes_data
    return bus_routes_data


def plain_ref(short, long_name):
    """The number of a line in a feed: its short name (long names are sentences)."""
    return (short or "").strip()


def index_feed(feed, work, today):
    """Downloads a feed and indexes it (once: the result is kept in work/gtfs/); another process doing the same is waited for."""
    db = feeds.feed_path(work, feed)
    note = db.with_suffix(".json")
    lock = db.with_suffix(".lock")
    db.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    while not note.exists():
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
        except FileExistsError:
            if time.time() - t0 > LOCK_WAIT_S:
                return {"ok": False, "why": "waited too long for another process"}
            time.sleep(2)
            continue
        try:
            svc = service_module()
            ref_fn = svc.gtfs_ref if feed.get("ref") == "jdf" else plain_ref
            builder = lambda zp, dbp, td: svc.build_gtfs_index(zp, dbp, td, ref_fn=ref_fn)       # noqa: E731
            feeds.prepare(feed, work, today, builder)
        finally:
            lock.unlink(missing_ok=True)
    return json.loads(note.read_text(encoding="utf-8"))


_FEED_WORKER = {}


def _feed_worker_init(geofabrik_path, gid):
    gf = regions.Geofabrik(geofabrik_path)
    _FEED_WORKER["inside"] = regions.inside_test(gf.polygon(gid))


def _feed_job(feed, work, today):
    """In a worker process: index a feed and read its patterns inside the extract. -> (index result, patterns or None)."""
    res = index_feed(feed, pathlib.Path(work), today)
    pats = None
    if res.get("ok"):
        pats = gtfsmerge.read_patterns(feeds.feed_path(pathlib.Path(work), feed), inside=_FEED_WORKER["inside"], feed=feed["id"], lic=feed["license"])
    return res, pats


FEED_POOL_MIN = 6                  # fewer feeds than this are done one after another


def _geofabrik_date():
    """YYMMDD of Geofabrik's latest extracts (read from where the redirect of a small extract goes)."""
    import urllib.request

    class Stop(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    req = urllib.request.Request("https://download.geofabrik.de/europe/luxembourg-latest.osm.pbf", method="HEAD", headers={"User-Agent": feeds.UA})
    try:
        urllib.request.build_opener(Stop).open(req, timeout=30)
    except urllib.error.HTTPError as e:                      # the 30x we did not follow
        import re
        m = re.search(r"-(\d{6})\.osm\.pbf", e.headers.get("Location", ""))
        if m:
            return m.group(1)
    return None


def download_pbf(gf, gid, work, cc=None):
    """Downloads an extract's .pbf (continues a partial file of the same source). Returns the size. The fast mirror first
    (regions.FR_MIRROR), when it has the country, then Geofabrik. A few Geofabrik extracts are redirected to a mirror whose
    certificate chain Python cannot check (Germany: gwdg.de): those are taken under their dated name from geofabrik.de itself."""
    mirror = regions.mirror_url(cc) if cc else None
    if mirror:
        try:
            return feeds.fetch(mirror, pbf_path(work, gid), max_bytes=40 * 1024 ** 3, timeout=120, attempts=4)
        except OSError:
            pbf_path(work, gid).with_name(pbf_path(work, gid).name + ".part").unlink(missing_ok=True)      # (not a file of Geofabrik's)
    url = gf.pbf_url(gid)
    try:
        return feeds.fetch(url, pbf_path(work, gid), max_bytes=40 * 1024 ** 3, timeout=120, attempts=2)
    except OSError as e:
        if "CERTIFICATE" not in str(e):
            raise
    date = _geofabrik_date()
    if not date:
        raise OSError("no dated name for " + url)
    return feeds.fetch(url.replace("-latest.", f"-{date}."), pbf_path(work, gid), max_bytes=40 * 1024 ** 3, timeout=120, attempts=8)


def pbf_path(work, gid):
    return pathlib.Path(work) / "src" / (gid.replace("/", "_") + ".osm.pbf")


def build_extract(cc, gid, root, work, catalog_path, geofabrik_path, manual_feeds, keep_pbf=False, log=print, wait_for_pbf=False, feed_workers=1):
    """Builds data/<cc>/ of the database in `root`. Returns the entry for manifest.json (plus the build report under "report")."""
    t0 = time.time()
    root, work = pathlib.Path(root), pathlib.Path(work)

    def say(msg):
        log(f"[{cc} {time.time() - t0:5.0f}s] {msg}")

    gf = regions.Geofabrik(geofabrik_path)
    poly = gf.polygon(gid)
    inside = regions.inside_test(poly)
    catalog = feeds.load_catalog(catalog_path)
    skip = {h for m in manual_feeds for h in m.get("replaces", [])}
    catalog = [f for f in catalog if not any(h in f["url"] or h in f["direct"] for h in skip)]
    for m in manual_feeds:
        catalog.append({"id": m["id"], "cc": m.get("cc", ""), "provider": m.get("provider", m["id"]), "name": m.get("name", ""),
                        "url": m["url"], "direct": "", "auth": False, "license_url": m.get("license_url", ""),
                        "license": m["license"], "bbox": m["bbox"], "official": True, "ref": m.get("ref")})
    allowed, review = feeds.candidates(catalog, poly)
    say(f"{len(allowed)} feeds with an open licence touch it ({len(review)} need review)")

    pbf = pbf_path(work, gid)
    if not pbf.exists():
        if wait_for_pbf:                                  # the batch's downloader brings the files one at a time
            while not pbf.exists():
                time.sleep(5)
        else:
            n = download_pbf(gf, gid, work, cc)
            say(f"downloaded {n / 1e6:.0f} MB")
    today = datetime.date.today()
    # the feeds are indexed and read by other processes while this one reads the map (the biggest countries have hundreds)
    pool, futures = None, {}
    if feed_workers > 1 and len(allowed) >= FEED_POOL_MIN:
        pool = cf.ProcessPoolExecutor(max_workers=min(feed_workers, len(allowed)), initializer=_feed_worker_init,
                                      initargs=(str(geofabrik_path), gid))
        futures = {f["id"]: pool.submit(_feed_job, f, str(work), today) for f in allowed}
    rels, nodes, ways, stops = osmread.read_pbf(str(pbf), lambda m: say(m), shape_tol=osmread.SHAPE_TOL_M)
    records, skipped = osmread.build_routes(rels, nodes, ways, stops, cc)
    del rels, nodes, ways
    report = {"osm_lines": len(records), "osm_skipped": skipped, "feeds": {}, "feeds_review": [f["id"] for f in review]}
    sources = {"osm": f"{gid.replace('/', '_')}-latest ({datetime.date.today().isoformat()})"}
    patterns = []
    used = []
    for f in allowed:                                    # (in the catalog's order, so the result does not depend on who finished first)
        if f["id"] in futures:
            res, pats = futures[f["id"]].result()
        else:
            res = index_feed(f, work, today)
            pats = gtfsmerge.read_patterns(feeds.feed_path(work, f), inside=inside, feed=f["id"], lic=f["license"]) if res.get("ok") else None
        report["feeds"][f["id"]] = {k: res.get(k) for k in ("ok", "why", "patterns")}
        if not res["ok"]:
            continue
        if pats:
            patterns += pats
            used.append({"id": f["id"], "provider": f["provider"], "license": f["license"], "license_url": f["license_url"],
                         "url": f["url"], "patterns": len(pats)})
    if pool is not None:
        pool.shutdown()
    say(f"OSM lines {len(records)}, stops {len(stops)}; timetable patterns {len(patterns)} from {len(used)} feeds")
    if patterns:
        records, rep = gtfsmerge.merge(records, stops, patterns, "x", cc, "")
        report["gtfs"] = rep
        sources["gtfs"] = [u["id"] for u in used]
    ed = edits_mod.load(root / "edits" / cc)
    records, orphans = edits_mod.apply(records, stops, ed)
    report["orphan_edits"] = orphans
    entry = output.write(root, cc, records, stops, sources, datetime.date.today().isoformat())
    report.update({"routes": entry["routes"], "stops": entry["stops"], "tiles": entry["tile_count"], "secs": round(time.time() - t0)})
    report["used_feeds"] = used
    if not keep_pbf:
        pbf.unlink(missing_ok=True)
    say(f"written: {entry['routes']} lines, {entry['stops']} stops, {entry['tile_count']} tiles")
    return {"entry": entry, "report": report}
