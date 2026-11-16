"""Timetable feeds: the Mobility Database catalog, which of them the database may use (licence), downloading and indexing.

A feed is used only when its licence is on the allow-list below (an open licence that lets the data be reused with attribution).
Feeds with no licence, a licence that is not on the list, or that need a key, are not downloaded: they go to sources/review.json
so a person can decide. The list is short on purpose and every entry says what it is.
"""
import csv
import hashlib
import io
import json
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

CATALOG_URL = "https://files.mobilitydatabase.org/feeds_v2.csv"
UA = "earth-bus-routes-db-builder/1 (+https://github.com/Robitnik/earth-bus-routes-db-eu)"
MAX_ZIP_BYTES = 1500 * 1024 * 1024

# (regex on the lower-case licence URL without scheme, licence name). Order matters: the first match wins.
ALLOW = [
    (r"creativecommons\.org/publicdomain/zero", "CC0-1.0"),
    (r"creativecommons\.org/licenses/by-(sa|nc|nd)", None),                 # not allowed: share-alike / non-commercial / no-derivatives
    (r"creativecommons\.org/licenses/by/", "CC-BY"),
    (r"opendefinition\.org/licenses/cc-by(?!-sa)", "CC-BY"),
    (r"opendatacommons\.org/licenses/odbl", "ODbL-1.0"),
    (r"opendatacommons\.org/licenses/(by|pddl)", "ODC-By/PDDL"),
    (r"data\.gouv\.fr/pages/legal/licences/etalab", "Etalab-2.0"),
    (r"nationalarchives\.gov\.uk/doc/open-government-licence", "OGL-UK-3.0"),
    (r"nap\.transportes\.gob\.es/licencia-datos", "ES-MITRAMS-open"),        # Spanish ministry's open data licence (re-use, also commercial)
    (r"govdata\.de/dl-de/(by|zero)", "DL-DE"),
]
# licences read by a person and added here: (regex, name, who checked, when)
CHECKED = [
]


def classify(url):
    """The licence name for a licence URL, or None when it is not on the allow-list."""
    u = re.sub(r"^https?://(www\.)?", "", (url or "").strip().lower())
    if not u:
        return None
    for rx, name in ALLOW:
        if re.search(rx, u):
            return name
    for rx, name, _who, _when in CHECKED:
        if re.search(rx, u):
            return name
    return None


def fetch(url, dest, max_bytes=MAX_ZIP_BYTES, timeout=60, attempts=4):
    """Downloads a file (to dest.part, then renamed). Returns the size. Retries a failed connection (a long download over a
    flaky link); raises OSError when all attempts fail."""
    last = None
    for k in range(attempts):
        try:
            return _fetch_once(url, dest, max_bytes, timeout)
        except OSError as e:
            last = e
            if str(e) == "too big":
                break
            time.sleep(5 * (k + 1))
    raise last


def _fetch_once(url, dest, max_bytes, timeout, resume=True):
    """One try. A dest.part left by an earlier try is continued (Range) when the server allows it."""
    dest = pathlib.Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if resume and part.exists() else 0
    headers = {"User-Agent": UA}
    if have:
        headers["Range"] = f"bytes={have}-"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if have and r.status != 206:
                have = 0                                      # the server sent the whole file again
            n = have
            with open(part, "ab" if have else "wb") as f:
                while True:
                    b = r.read(1 << 20)
                    if not b:
                        break
                    n += len(b)
                    if n > max_bytes:
                        part.unlink(missing_ok=True)
                        raise OSError("too big")
                    f.write(b)
    except urllib.error.HTTPError as e:
        if e.code == 416 and have:                              # asked past the end: the file is complete
            part.replace(dest)
            return have
        raise OSError(f"HTTP {e.code}") from e
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise OSError(str(e)) from e                            # (the part stays: the next try continues it)
    part.replace(dest)
    return n


def load_catalog(path):
    """[feed dict] of the active GTFS feeds in the Mobility Database CSV, with the licence classified."""
    out = []
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if r["data_type"] != "gtfs" or r["status"] not in ("active", "") or r["redirect.id"]:
                continue
            try:
                bbox = [float(r["location.bounding_box.minimum_latitude"]), float(r["location.bounding_box.minimum_longitude"]),
                        float(r["location.bounding_box.maximum_latitude"]), float(r["location.bounding_box.maximum_longitude"])]
            except ValueError:
                bbox = None
            lic_url = r["urls.license"].strip()
            out.append({
                "id": r["id"], "cc": r["location.country_code"], "provider": r["provider"], "name": r["name"],
                "url": r["urls.latest"].strip() or r["urls.direct_download"].strip(), "direct": r["urls.direct_download"].strip(),
                "auth": r["urls.authentication_type"].strip() not in ("", "0"), "license_url": lic_url, "license": classify(lic_url),
                "bbox": bbox, "official": r["is_official"] == "True"})
    return out


def intersects(bbox, other, pad=0.0):
    """Do two (min lat, min lon, max lat, max lon) boxes touch?"""
    if not bbox or not other:
        return False
    return not (bbox[2] + pad < other[0] or bbox[0] - pad > other[2] or bbox[3] + pad < other[1] or bbox[1] - pad > other[3])


def candidates(catalog, polygon):
    """(allowed, review) feeds that may have lines in an extract: the feed's box touches the extract's outline (shapely);
    allowed = open licence and no key."""
    from shapely.geometry import box
    allowed, review = [], []
    for f in catalog:
        b = f["bbox"]
        if not f["url"] or not b or not polygon.intersects(box(b[1], b[0], b[3], b[2])):
            continue
        (allowed if f["license"] and not f["auth"] else review).append(f)
    return allowed, review


def feed_path(work, feed):
    return pathlib.Path(work) / "gtfs" / f"{feed['id']}.db"


def prepare(feed, work, today, builder, log=print):
    """Downloads a feed and builds its sqlite index with `builder` (the service's build_gtfs_index); returns
    {"ok": bool, "why": str, "patterns": int}. A finished index is kept (and a failure is kept too: it is not tried twice a run)."""
    db = feed_path(work, feed)
    note = db.with_suffix(".json")
    if note.exists():
        return json.loads(note.read_text(encoding="utf-8"))
    zp = db.with_suffix(".zip")
    res = {"ok": False, "why": "", "patterns": 0}
    try:
        t0 = time.time()
        size = fetch(feed["url"], zp)
        if not zipfile_ok(zp) and feed["direct"] and feed["direct"] != feed["url"]:
            size = fetch(feed["direct"], zp)
        st = builder(zp, db, today)
        res.update({"ok": st["patterns"] > 0, "patterns": st["patterns"], "trips": st["trips"], "mb": round(size / 1e6, 1),
                    "why": "" if st["patterns"] else "no running bus trips in the next 28 days", "secs": round(time.time() - t0)})
    except Exception as e:                    # a broken feed must not stop the others
        res["why"] = f"{type(e).__name__}: {str(e)[:120]}"
    finally:
        zp.unlink(missing_ok=True)
        if db.exists() and not res["ok"]:
            db.unlink()
    note.write_text(json.dumps(res), encoding="utf-8")
    return res


def zipfile_ok(path):
    import zipfile
    try:
        return zipfile.is_zipfile(path)
    except OSError:
        return False
