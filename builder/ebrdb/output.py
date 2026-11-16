"""Writing the database: one JSON file per line (data/<cc>/routes/<xxx>/<id>.json), tiles of stops and lines for "nearby"
(data/<cc>/tiles/<ty>_<tx>.json) and manifest.json. The output is deterministic (the same input gives the same bytes), so a
rebuild shows in git only what really changed, and the files are laid out for reading by people (one stop per line)."""
import json
import math
import pathlib
import shutil

from . import ids

TILE_DEG = 0.1
SCHEMA = 1


def dumps(o):
    return json.dumps(o, ensure_ascii=False, separators=(",", ":"))


def tile_of(lat, lon):
    return int(math.floor((lat + 90) / TILE_DEG)), int(math.floor((lon + 180) / TILE_DEG))


def route_lines(rec):
    """The text of a line file: each top-level key on its own line, the ways and stops one to a line."""
    head = ["v", "id", "cc", "mode", "t"]
    rows = []
    for k in head:
        if k in rec:
            rows.append(f'"{k}":{dumps(rec[k])}')
    for k in ("ways", "stops"):
        items = rec.get(k, [])
        rows.append(f'"{k}":[' + ("\n  " + ",\n  ".join(dumps(x) for x in items) + "\n" if items else "") + "]")
    for k in ("times", "src", "q"):
        if k in rec:
            rows.append(f'"{k}":{dumps(rec[k])}')
    return "{\n" + ",\n".join(rows) + "\n}\n"


def stop_sequence(rec):
    """The stops of a line as the data service numbers them (osm.lua's order of preference): the platforms, else the stop
    positions, else all. Returns the list of stop ids in the listed order."""
    for prefix in ("platform", "stop"):
        seq = [s["s"] for s in rec["stops"] if (s.get("r") or "").startswith(prefix)]
        if len(seq) >= 2:
            return seq
    return [s["s"] for s in rec["stops"]]


TILE_TAGS = ("ref", "name", "from", "to", "network", "operator", "colour", "route", "public_transport:version", "night_service", "bus")


def write(out_dir, cc, records, stops, sources, built):
    """Writes data/<cc>/ and returns the country's manifest entry. The folder is replaced as a whole."""
    out_dir = pathlib.Path(out_dir)
    root = out_dir / "data" / cc
    if root.exists():
        shutil.rmtree(root)
    (root / "routes").mkdir(parents=True)
    (root / "tiles").mkdir(parents=True)
    tiles = {}
    for rec in sorted(records, key=lambda r: r["id"]):
        for s in rec["stops"]:                       # the stops carry their own place and name: opening a line needs one file
            st = stops.get(s["s"])
            if st is not None:
                s["la"], s["lo"] = st["lat"], st["lon"]
                if st.get("n") and "n" not in s:
                    s["n"] = st["n"]
                if st.get("q"):
                    s["q"] = 1
        d = root / "routes" / ids.shard(rec["id"])
        d.mkdir(exist_ok=True)
        (d / f"{rec['id']}.json").write_text(route_lines(rec), encoding="utf-8", newline="\n")
        seq = stop_sequence(rec)
        places = {}
        for i, sid in enumerate(seq, 1):
            places.setdefault(sid, []).append(i)
        entry_t = {k: rec["t"][k] for k in TILE_TAGS if k in rec["t"]}
        per_tile = {}
        for sid in dict.fromkeys(seq):
            st = stops.get(sid)
            if st is None:
                continue
            p = places[sid]
            item = [sid, p[0]] + ([p[-1]] if len(p) > 1 else [])
            per_tile.setdefault(tile_of(st["lat"], st["lon"]), []).append(item)
        for tk, items in per_tile.items():
            tiles.setdefault(tk, {"stops": [], "routes": []})["routes"].append({"id": rec["id"], "t": entry_t, "n": len(seq), "s": items})
    for sid in sorted(stops):
        st = stops[sid]
        tiles.setdefault(tile_of(st["lat"], st["lon"]), {"stops": [], "routes": []})["stops"].append(st)
    names = []
    for (ty, tx), t in sorted(tiles.items()):
        name = f"{ty}_{tx}"
        names.append(name)
        lines = [f'"v":{SCHEMA}', f'"cc":"{cc}"', f'"tile":[{ty},{tx}]',
                 '"stops":[' + ("\n  " + ",\n  ".join(dumps(s) for s in t["stops"]) + "\n" if t["stops"] else "") + "]",
                 '"routes":[' + ("\n  " + ",\n  ".join(dumps(r) for r in t["routes"]) + "\n" if t["routes"] else "") + "]"]
        (root / "tiles" / f"{name}.json").write_text("{\n" + ",\n".join(lines) + "\n}\n", encoding="utf-8", newline="\n")
    la = [s["lat"] for s in stops.values()]
    lo = [s["lon"] for s in stops.values()]
    return {"routes": len(records), "stops": len(stops), "tiles": names, "tile_deg": TILE_DEG,
            "bbox": [min(la), min(lo), max(la), max(lo)] if la else None, "sources": sources, "built": built}


def write_manifest(out_dir, countries):
    p = pathlib.Path(out_dir) / "manifest.json"
    old = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"countries": {}}
    old["v"] = SCHEMA
    old["countries"].update(countries)
    p.write_text(json.dumps(old, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return old
