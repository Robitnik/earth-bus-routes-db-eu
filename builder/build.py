#!/usr/bin/env python3
"""Builds a country of the route database.

  python builder/build.py --cc CZ --pbf work/src/czech-republic.osm.pbf [--gtfs <index.db>] [--out .]

Reads the OSM extract (routes, stops, road shapes), merges the timetables of the GTFS index when given, lays the hand-made
edits over, and writes data/<cc>/ and manifest.json. Run with the venv's Python (pyosmium).
"""
import argparse
import datetime
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from ebrdb import edits as edits_mod, gtfsmerge, osmread, output  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--cc", required=True)
    ap.add_argument("--pbf", required=True)
    ap.add_argument("--gtfs", help="the service's sqlite timetable index of the country (gtfs_<CC>.db)")
    ap.add_argument("--gtfs-feed", default=None, help="feed name for ids and sources (default: the country code in lower case)")
    ap.add_argument("--min-trips", type=int, default=gtfsmerge.MIN_TRIPS)
    ap.add_argument("--out", default=str(pathlib.Path(__file__).resolve().parent.parent))
    ap.add_argument("--osm-label", default=None)
    ap.add_argument("--use-cache", action="store_true", help="keep the result of the pass over the .pbf next to it (for repeated builds)")
    a = ap.parse_args(argv)
    t0 = time.time()

    def say(msg):
        print(f"[{time.time() - t0:6.0f}s] {msg}", flush=True)

    cache = pathlib.Path(a.pbf).with_suffix(".read.pickle")
    if a.use_cache and cache.exists() and cache.stat().st_mtime >= pathlib.Path(a.pbf).stat().st_mtime:
        import pickle
        rels, nodes, ways, stops = pickle.loads(cache.read_bytes())
        say("read from the cache of the last pass over the file")
    else:
        rels, nodes, ways, stops = osmread.read_pbf(a.pbf, say)
        if a.use_cache:
            import pickle
            cache.write_bytes(pickle.dumps((rels, nodes, ways, stops), protocol=4))
    records, skipped = osmread.build_routes(rels, nodes, ways, stops, a.cc)
    del ways, nodes
    say(f"OSM lines: {len(records)} (skipped, fewer than 2 stops: {skipped}), stops: {len(stops)}")
    report = {"osm_lines": len(records), "osm_skipped": skipped}
    sources = {"osm": a.osm_label or pathlib.Path(a.pbf).name}
    if a.gtfs:
        feed = a.gtfs_feed or a.cc.lower()
        pats = gtfsmerge.read_patterns(a.gtfs, a.min_trips)
        say(f"timetable patterns: {len(pats)}")
        records, rep = gtfsmerge.merge(records, stops, pats, feed, a.cc)
        report["gtfs"] = rep
        sources["gtfs"] = feed
        say(f"merged: {rep}")
    ed = edits_mod.load(pathlib.Path(a.out) / "edits" / a.cc)
    records, orphans = edits_mod.apply(records, stops, ed)
    report["orphan_edits"] = orphans
    if orphans:
        say(f"ORPHAN EDITS (their lines are gone from the sources): {orphans}")
    built = datetime.date.today().isoformat()
    entry = output.write(a.out, a.cc, records, stops, sources, built)
    output.write_manifest(a.out, {a.cc: entry})
    report.update({"routes": entry["routes"], "stops": entry["stops"], "tiles": len(entry["tiles"])})
    rp = pathlib.Path(a.out) / "reports"
    rp.mkdir(exist_ok=True)
    (rp / f"{a.cc}.json").write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    say(f"written: {report}")


if __name__ == "__main__":
    main()
