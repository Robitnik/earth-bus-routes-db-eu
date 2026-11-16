#!/usr/bin/env python3
"""Builds many extracts (countries of the EU, states of the US) in parallel, one finished extract at a time into manifest.json.

  python builder/batch.py --group eu --jobs 3                  every EU country
  python builder/batch.py --only LU,MT,US-DE --jobs 2          the named ones
  python builder/batch.py --group us --skip-done               states that have no entry in manifest.json yet

Needs: pyosmium, shapely, jsonschema (builder/requirements.txt) and the add-on's data service (companion/bus_routes_data.py:
its GTFS indexer) in ../earth-bus-routes/companion or EBR_SERVICE_DIR. Downloads go to --work (the .pbf of an extract is deleted
once it is built); a feed is downloaded and indexed once for all extracts.
"""
import argparse
import concurrent.futures as cf
import json
import os
import pathlib
import sys
import threading
import time
import traceback

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from ebrdb import feeds, output, pipeline, regions  # noqa: E402


def log_to(work, cc):
    d = pathlib.Path(work) / "log"
    d.mkdir(parents=True, exist_ok=True)
    f = open(d / f"{cc}.log", "a", encoding="utf-8", buffering=1)

    def log(msg):
        f.write(msg + "\n")
    return log


def run_one(cc, gid, root, work, catalog, geofabrik, manual):
    """In a worker process. Returns (cc, result dict or None, error text)."""
    log = log_to(work, cc)
    try:
        return cc, pipeline.build_extract(cc, gid, root, work, catalog, geofabrik, manual, log=log, wait_for_pbf=True), ""
    except Exception:
        log(traceback.format_exc())
        return cc, None, traceback.format_exc().splitlines()[-1]


def downloader(todo, geofabrik, work, ahead, stop):
    """Brings the .pbf files, one at a time per source (Geofabrik slows a client that opens several; the fast mirror and Geofabrik
    do not disturb each other): in the order of the build, never more than `ahead` files on disk that are not built yet."""
    gf = regions.Geofabrik(geofabrik)
    src = pathlib.Path(work) / "src"
    for cc, gid in todo.items():
        dest = pipeline.pbf_path(work, gid)
        while not stop.is_set() and not dest.exists() and len(list(src.glob("*.osm.pbf"))) >= ahead:
            time.sleep(10)
        if dest.exists():
            continue
        for attempt in range(5):
            try:
                n = pipeline.download_pbf(gf, gid, work, cc)
                print(f"downloaded {cc}: {n / 1e6:.0f} MB", flush=True)
                break
            except OSError as e:
                print(f"download {cc} failed ({e}); try {attempt + 1}/5", flush=True)
                time.sleep(30)


def fresh(path, url, max_age_s=86400):
    path = pathlib.Path(path)
    if path.exists() and time.time() - path.stat().st_mtime < max_age_s:
        return
    feeds.fetch(url, path, max_bytes=200 * 1024 * 1024)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", default="eu", choices=["eu", "us", "all", "extra"])
    ap.add_argument("--only", help="comma separated codes (CZ,US-AL), instead of --group")
    ap.add_argument("--skip-done", action="store_true")
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--out", default=str(HERE.parent))
    ap.add_argument("--work", default=str(HERE.parent / "work"))
    a = ap.parse_args(argv)
    root, work = pathlib.Path(a.out), pathlib.Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    catalog, geofabrik = work / "feeds_v2.csv", work / "geofabrik-index.json"
    fresh(catalog, feeds.CATALOG_URL)
    fresh(geofabrik, "https://download.geofabrik.de/index-v1.json", 7 * 86400)
    manual = json.loads((root / "sources" / "feeds.json").read_text(encoding="utf-8")).get("manual", [])
    todo = regions.all_extracts("all" if a.only else a.group)
    if a.only:
        want = [x.strip() for x in a.only.split(",") if x.strip()]
        todo = {k: v for k, v in {**regions.all_extracts("all"), **regions.all_extracts("extra")}.items() if k in want}
    mf = root / "manifest.json"
    done = set(json.loads(mf.read_text(encoding="utf-8"))["countries"]) if mf.exists() else set()
    if a.skip_done:
        todo = {k: v for k, v in todo.items() if k not in done}
    for lock in (work / "gtfs").glob("*.lock"):                  # left by a crashed run
        lock.unlink()
    big = ["DE", "FR", "IT", "ES", "PL", "NL", "SE", "FI", "AT", "RO", "BE", "CZ", "DK", "PT", "HU", "GR", "BG", "HR", "SK", "LT", "LV", "EE", "IE",
           "SI", "CY", "LU", "MT", "US-CA", "US-TX", "US-FL", "US-NY", "US-PA", "US-IL", "US-OH", "US-MI", "US-NC", "US-GA", "US-WA", "US-MA",
           "US-VA", "US-NJ", "US-CO", "US-OR", "US-MN", "US-WI", "US-IN", "US-AZ", "US-MO", "US-TN", "US-MD"]      # the largest first: they take longest
    todo = dict(sorted(todo.items(), key=lambda kv: big.index(kv[0]) if kv[0] in big else len(big)))
    print(f"{len(todo)} extracts, {a.jobs} at a time: {', '.join(todo)}", flush=True)
    t0 = time.time()
    failed = []
    stop = threading.Event()
    fast = {cc: gid for cc, gid in todo.items() if regions.mirror_url(cc)}
    slow = {cc: gid for cc, gid in todo.items() if not regions.mirror_url(cc)}
    for group in (fast, slow):
        if group:
            threading.Thread(target=downloader, args=(group, geofabrik, work, a.jobs + 3, stop), daemon=True).start()
    with cf.ProcessPoolExecutor(max_workers=a.jobs) as ex:
        futs = [ex.submit(run_one, cc, gid, str(root), str(work), str(catalog), str(geofabrik), manual) for cc, gid in todo.items()]
        for fu in cf.as_completed(futs):
            cc, res, err = fu.result()
            if res is None:
                failed.append(cc)
                print(f"FAILED {cc}: {err}", flush=True)
                continue
            output.write_manifest(root, {cc: res["entry"]})
            rp = root / "reports"
            rp.mkdir(exist_ok=True)
            (rp / f"{cc}.json").write_text(json.dumps(res["report"], indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
            e = res["entry"]
            print(f"done {cc}: {e['routes']} lines, {e['stops']} stops, {e['tile_count']} tiles, {res['report']['secs']} s "
                  f"({(time.time() - t0) / 60:.0f} min since start)", flush=True)
    print("failed:", failed or "none", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
