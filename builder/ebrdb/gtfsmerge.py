"""Timetables (the live service's sqlite GTFS index) merged into the OpenStreetMap lines.

A timetable pattern (one line's one way of going: its stops with their times from the start, and its departures) is matched with
the OSM routes of the same number: when 60 % of its stops are at stops of a route (and it runs the same way), the line gets the
road and the stop ids of OSM and the order and times of the timetable. An OSM route that a pattern was matched with is replaced
by those lines (as the live service does); one that no pattern matched stays as it is, a pattern without a route stays a line
of its own without a road (the service asks the routing servers for it the first time and keeps the answer).
"""
import datetime
import math
import sqlite3
import unicodedata

from . import ids, poly

MATCH_RATIO = 0.6
STOP_M = 60              # a timetable stop this near a stop of the OSM route is that stop
SAME_STOP_M = 40         # a timetable stop this near any OSM stop takes its id (and its name)
MIN_TRIPS = 4            # a pattern with fewer trips in the 28 days of the index is left out (one-off and dated variants)
TYPICAL = (("wd", 3), ("sa", 6), ("su", 7))      # the typical Wednesday, Saturday, Sunday


def fold(text):
    t = unicodedata.normalize("NFKD", (text or "").lower())
    return "".join(c for c in t if not unicodedata.combining(c)).strip()


def read_patterns(db_path, min_trips=MIN_TRIPS):
    """[{ref, name, agency, head, stops: [{sid, name, lat, lon, off}], times: {wd, sa, su: minutes of the day}}] from the index."""
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    meta = dict(db.execute("SELECT k, v FROM meta").fetchall())
    day0 = datetime.datetime.strptime(meta["today"], "%Y%m%d").date()
    bits = {}
    for i in range(min(int(meta.get("days", "0")), 14)):
        bits.setdefault((day0 + datetime.timedelta(days=i)).isoweekday(), []).append(i)
    stops = {r[0]: r[1:] for r in db.execute("SELECT sid, name, lat, lon FROM stops")}
    out = []
    for pid, ref, name, agency, head, sl, offs, trips in db.execute(
            "SELECT pid, ref, name, agency, head, stops, offs, trips FROM patterns WHERE trips >= ? ORDER BY pid", (min_trips,)):
        sids, offs = sl.split(","), [int(x) for x in offs.split(",")]
        if any(s not in stops for s in sids):
            continue
        per = {}
        for start, mask in db.execute("SELECT start, mask FROM deps WHERE pid=?", (pid,)):
            for wd, bs in bits.items():
                for b in bs:
                    if mask >> b & 1:
                        per.setdefault((wd, b), []).append(start)
        times = {}
        for key, wd in TYPICAL:
            best = max((per.get((wd, b), []) for b in bits.get(wd, [])), key=len, default=[])
            times[key] = sorted({s // 60 for s in best})
        if not any(times.values()):
            continue
        out.append({"pid": pid, "ref": ref, "name": name, "agency": agency, "head": head, "trips": trips,
                    "stops": [{"sid": s, "name": stops[s][0], "lat": stops[s][1], "lon": stops[s][2], "off": offs[k]}
                              for k, s in enumerate(sids)], "times": times})
    db.close()
    return out


class StopGrid:
    """OSM stops by 0.001 degree cell, for nearest-stop lookups."""

    def __init__(self, stops):
        self.cells = {}
        for s in stops.values():
            self.cells.setdefault((int(s["lat"] * 1000), int(s["lon"] * 1000)), []).append(s)

    def near(self, lat, lon, radius):
        """The nearest platform or stop position (a bus station is a whole yard: not a stop to take the id of)."""
        best, bd = None, radius
        cy, cx = int(lat * 1000), int(lon * 1000)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                for s in self.cells.get((cy + dy, cx + dx), ()):
                    if s["k"] == "s":
                        continue
                    d = poly.hav((lat, lon), (s["lat"], s["lon"]))
                    if d <= bd:
                        best, bd = s, d
        return best


def match(pattern, rec, stops):
    """How much of the pattern's stops are at the stops of the OSM route `rec` (any order: the list of an OSM relation is not
    to be trusted, the direction is told by the road, see `runs_along`)."""
    pts = [(stops[s["s"]]["lat"], stops[s["s"]]["lon"]) for s in rec["stops"]]
    hits = 0
    for st in pattern["stops"]:
        if any(poly.hav((st["lat"], st["lon"]), p) <= STOP_M for p in pts):
            hits += 1
    return hits / max(1, len(pattern["stops"]))


def chain(ways):
    """The points of a route's ways joined as the mod joins them (each way turned to the end nearest the chain)."""
    path = []
    for w in ways:
        g = poly.decode(w["g"])
        if len(g) < 2:
            continue
        if path and poly.hav(path[-1], g[0]) > poly.hav(path[-1], g[-1]):
            g = g[::-1]
        path += g if not path or path[-1] != g[0] else g[1:]
    return path


def project(path, cum, lat, lon):
    """(along, distance in metres) of the nearest point of the polyline to a place."""
    k = math.cos(math.radians(lat)) * 111320
    best, along = float("inf"), 0.0
    for i in range(len(path) - 1):
        ax, ay = (path[i][1] - lon) * k, (path[i][0] - lat) * 110574
        bx, by = (path[i + 1][1] - lon) * k, (path[i + 1][0] - lat) * 110574
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        t = 0.0 if L2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / L2))
        d = math.hypot(ax + t * dx, ay + t * dy)
        if d < best:
            best, along = d, cum[i] + t * (cum[i + 1] - cum[i])
    return along, best


def runs_along(ways, pattern):
    """True when the road of `ways` goes the way the pattern's stops are listed, False when the other way, None when it cannot
    be told (a road without stops of the pattern on it)."""
    path = chain(ways)
    if len(path) < 2:
        return None
    cum = [0.0]
    for a, b in zip(path, path[1:]):
        cum.append(cum[-1] + poly.hav(a, b))
    inc = dec = 0
    last = None
    for st in pattern["stops"]:
        a, d = project(path, cum, st["lat"], st["lon"])
        if d > STOP_M:
            continue
        if last is not None:
            if a > last + 20:
                inc += 1
            elif a < last - 20:
                dec += 1
        last = a
    if inc == dec:
        return None
    return inc > dec


def reverse_ways(ways):
    """The same road driven the other way: the list and every way turned round (a direction of a way's tags swaps)."""
    out = []
    swap = {"forward": "backward", "backward": "forward"}
    for w in reversed(ways):
        n = {"g": poly.encode(poly.decode(w["g"])[::-1])}
        if w.get("r"):
            n["r"] = swap.get(w["r"], w["r"])
        if w.get("t"):
            t = dict(w["t"])
            f, b = t.pop("maxspeed:forward", None), t.pop("maxspeed:backward", None)
            if b is not None:
                t["maxspeed:forward"] = b
            if f is not None:
                t["maxspeed:backward"] = f
            n["t"] = t
        out.append(n)
    return out


def merge(records, stops, patterns, feed="cz", cc="CZ"):
    """-> (records, report). `records` are the OSM lines (modified list returned), `stops` the stops dict (timetable-only stops
    are added to it)."""
    by_ref = {}
    for r in records:
        by_ref.setdefault(fold(r["t"].get("ref") or r["t"].get("name")), []).append(r)
    grid = StopGrid(stops)
    out, consumed, merged, alone, duplicates, by_id = [], set(), 0, 0, 0, {}
    turned = 0

    def stop_for(st):
        s = grid.near(st["lat"], st["lon"], SAME_STOP_M)
        if s is not None:
            if not s.get("n"):
                s["n"] = st["name"]                          # (a stop position of the map often has no name)
            return s["id"], (st["name"] if len(s.get("n", "")) <= 3 and st["name"] else None)
        sid = ids.gtfs_stop_id(feed, st["sid"])
        if sid not in stops:
            stops[sid] = {"id": sid, "lat": round(st["lat"], 6), "lon": round(st["lon"], 6), "k": "p", "n": st["name"], "g": 1}
        return sid, None

    for p in patterns:
        best, bratio = None, 0.0
        for rec in by_ref.get(fold(p["ref"]), ()):
            ratio = match(p, rec, stops)
            if ratio >= MATCH_RATIO and ratio > bratio:
                best, bratio = rec, ratio
        first, last = p["stops"][0]["name"], p["stops"][-1]["name"]
        stop_list = []
        for st in p["stops"]:
            sid, name = stop_for(st)
            item = {"s": sid, "o": st["off"]}
            if name:                                         # a platform named "3": the line shows the timetable's name of the stop
                item["n"] = name
            stop_list.append(item)
        src = [{"k": "gtfs", "feed": feed, "id": str(p["pid"]), "lic": "CIS-JR"}]
        if best is not None:
            consumed.add(best["id"])
            merged += 1
            t = dict(best["t"])
            t["public_transport:version"] = "2"
            key = f"{best['id']}|{','.join(str(s['s']) for s in stop_list)}"
            ways = best["ways"]
            if runs_along(ways, p) is False:                 # OSM has the line the other way round: turn the road
                ways = reverse_ways(ways)
                turned += 1
            rec = {"v": 1, "id": ids.gtfs_route_id(feed, key), "cc": cc, "mode": best["mode"], "t": t, "ways": ways,
                   "stops": stop_list, "times": p["times"], "src": best["src"] + src}
        else:
            alone += 1
            key = f"{p['ref']}|{','.join(s['sid'] for s in p['stops'])}"
            t = {"ref": p["ref"], "name": f"{p['ref']}: {first} - {last}", "from": first, "to": last, "operator": p["agency"],
                 "route": "bus", "public_transport:version": "2"}
            rec = {"v": 1, "id": ids.gtfs_route_id(feed, key), "cc": cc, "mode": "bus", "t": t, "ways": [], "stops": stop_list,
                   "times": p["times"], "src": src}
        # two patterns with the same number and the same stops (another operator's route id, another calendar) are one line
        # with the departures of both
        same = by_id.get(rec["id"])
        if same is not None:
            for k, v in p["times"].items():
                same["times"][k] = sorted(set(same["times"].get(k, [])) | set(v))
            same["src"] += [x for x in src if x not in same["src"]]
            duplicates += 1
            continue
        by_id[rec["id"]] = rec
        out.append(rec)
    keep = [r for r in records if r["id"] not in consumed]
    return keep + out, {"patterns": len(patterns), "merged_with_osm": merged, "gtfs_only": alone, "osm_replaced": len(consumed),
                        "osm_kept": len(keep), "same_stops_joined": duplicates, "roads_turned": turned}
