"""The hand-made layer: files under edits/<cc>/ laid over the generated lines at every build, so a rebuild never loses them.

  edits/<cc>/<line id>.json      changes to one generated line (the id is the file name, as in data/<cc>/routes/)
      {"set": {"name": "...", "from": "...", "to": "...", "ref": "...", "operator": "...", "network": "...", "colour": "#rrggbb"},
       "tags": {"public_transport:version": "2"},          tags of the line (a null value removes one)
       "stops.remove": [stop id, ...],
       "stops.insert": [{"after": stop id | null, "s": stop id, "o": seconds from the start}],
       "times": {"wd": [minutes], "sa": [...], "su": [...]},
       "hidden": true,                                      the line is left out of the database
       "why": "...", "by": "...", "date": "..."}            for the people reading it
  edits/<cc>/new/<name>.json     a whole new line (a record as in docs/FORMAT.md; "id" may be omitted: it is made from the name)
  edits/<cc>/stops.json          {"<stop id>": {"n": "name", "lat": .., "lon": .., "k": "p|x|s", "remove": true}, ...} stop changes

An edit whose line is no longer generated (the source dropped it) is not lost: it is listed in the build report as an orphan.
"""
import json
import pathlib

from . import ids

SET_KEYS = {"name", "from", "to", "ref", "operator", "network", "colour"}


def load(directory):
    """-> {"lines": {id: edit}, "new": [record], "stops": {id: change}} from a cc's edits folder (missing folder: empty)."""
    d = pathlib.Path(directory)
    out = {"lines": {}, "new": [], "stops": {}}
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.json")):
        if f.name == "stops.json":
            out["stops"] = {int(k): v for k, v in json.loads(f.read_text(encoding="utf-8")).items()}
        else:
            out["lines"][int(f.stem)] = json.loads(f.read_text(encoding="utf-8"))
    for f in sorted((d / "new").glob("*.json")) if (d / "new").is_dir() else []:
        rec = json.loads(f.read_text(encoding="utf-8"))
        rec.setdefault("id", ids.gtfs_route_id("hand", f.stem))
        out["new"].append(rec)
    return out


def apply(records, stops, edits):
    """-> (records, orphans): records with the edits applied (edited lines get {"k": "edit"} in their `src`)."""
    by_id = {r["id"]: r for r in records}
    orphans = [i for i in edits["lines"] if i not in by_id]
    hidden = set()
    for rid, e in edits["lines"].items():
        r = by_id.get(rid)
        if r is None:
            continue
        if e.get("hidden"):
            hidden.add(rid)
            continue
        for k, v in (e.get("set") or {}).items():
            if k in SET_KEYS:
                r["t"][k] = v
        for k, v in (e.get("tags") or {}).items():
            if v is None:
                r["t"].pop(k, None)
            else:
                r["t"][k] = v
        rm = set(e.get("stops.remove") or ())
        if rm:
            r["stops"] = [s for s in r["stops"] if s["s"] not in rm]
        for ins in e.get("stops.insert") or ():
            item = {"s": ins["s"]}
            if "o" in ins:
                item["o"] = ins["o"]
            after = ins.get("after")
            pos = 0
            if after is not None:
                pos = next((k + 1 for k, s in enumerate(r["stops"]) if s["s"] == after), len(r["stops"]))
            r["stops"].insert(pos, item)
        if "times" in e:
            r["times"] = e["times"]
        r["src"] = r.get("src", []) + [{"k": "edit", "by": e.get("by", ""), "date": e.get("date", "")}]
    for sid, ch in edits["stops"].items():
        if ch.get("remove"):
            stops.pop(sid, None)
            continue
        cur = stops.setdefault(sid, {"id": sid, "k": "p"})
        for k in ("n", "lat", "lon", "k", "ref"):
            if k in ch:
                cur[k] = ch[k]
    out = [r for r in records if r["id"] not in hidden]
    for rec in edits["new"]:
        rec.setdefault("v", 1)
        rec.setdefault("ways", [])
        rec.setdefault("src", [{"k": "edit"}])
        out.append(rec)
    return out, orphans
