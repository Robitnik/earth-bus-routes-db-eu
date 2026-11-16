#!/usr/bin/env python3
"""Checks every file of data/ and edits/ against the schemas, and that the lines and tiles agree:
every stop of a line is in a tile, every line of a tile has its file, file names match ids.

  python builder/validate.py [--root .] [--cc CZ]        exit code 1 on any error
"""
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from ebrdb import ids, poly  # noqa: E402


def validate(root, ccs=None, limit=20):
    from jsonschema import Draft202012Validator
    root = pathlib.Path(root)
    rs = Draft202012Validator(json.loads((root / "schema" / "route.schema.json").read_text(encoding="utf-8")))
    ts = Draft202012Validator(json.loads((root / "schema" / "tile.schema.json").read_text(encoding="utf-8")))
    errors = []

    def err(msg):
        if len(errors) < limit:
            errors.append(msg)
        elif len(errors) == limit:
            errors.append("... more errors")

    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    for cc in sorted(manifest["countries"]):
        if ccs and cc not in ccs:
            continue
        base = root / "data" / cc
        stop_ids, line_ids = set(), set()
        for f in sorted((base / "tiles").glob("*.json")):
            t = json.loads(f.read_text(encoding="utf-8"))
            for e in ts.iter_errors(t):
                err(f"{f.name}: {e.message[:120]}")
            if f"{t['tile'][0]}_{t['tile'][1]}" != f.stem:
                err(f"{f.name}: tile coordinates differ from the file name")
            stop_ids.update(s["id"] for s in t["stops"])
        for f in sorted((base / "routes").glob("*/*.json")):
            r = json.loads(f.read_text(encoding="utf-8"))
            for e in rs.iter_errors(r):
                err(f"{f.name}: {e.message[:120]}")
            if str(r.get("id")) != f.stem or f.parent.name != ids.shard(r.get("id", 0)):
                err(f"{f.relative_to(root)}: the id does not match the file name / folder")
            line_ids.add(r["id"])
            for w in r.get("ways", []):
                try:
                    poly.decode(w["g"])
                except Exception:
                    err(f"{f.name}: bad polyline")
                    break
            missing = [s["s"] for s in r["stops"] if s["s"] not in stop_ids]
            if missing:
                err(f"{f.name}: stops not in any tile: {missing[:3]}")
        for f in sorted((base / "tiles").glob("*.json")):
            for r in json.loads(f.read_text(encoding="utf-8"))["routes"]:
                if r["id"] not in line_ids:
                    err(f"{f.name}: line {r['id']} has no file")
        e = manifest["countries"][cc]
        if e["routes"] != len(line_ids):
            err(f"{cc}: manifest says {e['routes']} lines, found {len(line_ids)}")
        idx = base / "index.json"
        names = sorted(f.stem for f in (base / "tiles").glob("*.json"))
        if not idx.exists() or sorted(json.loads(idx.read_text(encoding="utf-8"))["tiles"]) != names:
            err(f"{cc}: index.json does not list the tiles that are there")
        if e.get("tile_count", len(names)) != len(names):
            err(f"{cc}: manifest says {e['tile_count']} tiles, found {len(names)}")
    return errors


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(pathlib.Path(__file__).resolve().parent.parent))
    ap.add_argument("--cc", action="append")
    a = ap.parse_args(argv)
    errors = validate(a.root, a.cc)
    for e in errors:
        print("ERROR", e)
    print("OK" if not errors else f"{len(errors)} errors")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
