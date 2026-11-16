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


def read_patterns(db_path, min_trips=MIN_TRIPS, inside=None, feed="", lic=""):
    """[{ref, name, agency, head, stops: [{sid, name, lat, lon, off}], times: {wd, sa, su: minutes of the day}, feed, lic}] from the
    index. `inside(lat, lon)`: only lines whose first stop is inside (a feed that covers several extracts)."""
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    meta = dict(db.execute("SELECT k, v FROM meta").fetchall())
    day0 = datetime.datetime.strptime(meta["today"], "%Y%m%d").date()
    bits = {}
    for i in range(min(int(meta.get("days", "0")), 14)):
        bits.setdefault((day0 + datetime.timedelta(days=i)).isoweekday(), []).append(i)
    stops = {r[0]: r[1:] for r in db.execute("SELECT sid, name, lat, lon FROM stops")}
    bad = {sid for sid, (_, la, lo) in stops.items() if (la == 0 and lo == 0) or not (-90 <= la <= 90 and -180 <= lo <= 180)}
    out = []
    for pid, ref, name, agency, head, sl, offs, trips in db.execute(
            "SELECT pid, ref, name, agency, head, stops, offs, trips FROM patterns WHERE trips >= ? ORDER BY pid", (min_trips,)):
        sids, offs = sl.split(","), [int(x) for x in offs.split(",")]
        if any(s not in stops for s in sids):
            continue
        if bad.intersection(sids):                       # a stop without a place (a feed that gives 0, 0): left out, with its time
            keep = [k for k, x in enumerate(sids) if x not in bad]
            sids, offs = [sids[k] for k in keep], [offs[k] for k in keep]
            if len(sids) < 2:
                continue
        if inside is not None and not inside(stops[sids[0]][1], stops[sids[0]][2]):
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
        out.append({"pid": pid, "ref": ref, "name": name, "agency": agency, "head": head, "trips": trips, "feed": feed, "lic": lic,
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


class StopsOf:
    """The stops of an OSM route on a grid of 100 m cells (a local flat frame), to ask "is this place within STOP_M of one of them"
    without measuring against every stop. Made once per route, however many patterns are compared with it."""

    def __init__(self, rec, stops):
        pts = [(stops[x["s"]]["lat"], stops[x["s"]]["lon"]) for x in rec["stops"]]
        self.lat0 = pts[0][0]
        self.kx = math.cos(math.radians(self.lat0)) * 111320
        self.cells = {}
        for la, lo in pts:
            x, y = lo * self.kx, la * 110574
            self.cells.setdefault((int(x // 100), int(y // 100)), []).append((x, y))
        self.bbox = (min(p[0] for p in pts) - 0.002, min(p[1] for p in pts) - 0.002, max(p[0] for p in pts) + 0.002, max(p[1] for p in pts) + 0.002)

    def hit(self, lat, lon, radius=STOP_M):
        x, y = lon * self.kx, lat * 110574
        cx, cy, r2 = int(x // 100), int(y // 100), radius * radius
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for px, py in self.cells.get((cx + dx, cy + dy), ()):
                    if (px - x) ** 2 + (py - y) ** 2 <= r2:
                        return True
        return False


def pattern_bbox(pattern):
    b = pattern.get("bbox")
    if b is None:
        la = [x["lat"] for x in pattern["stops"]]
        lo = [x["lon"] for x in pattern["stops"]]
        b = pattern["bbox"] = (min(la), min(lo), max(la), max(lo))
    return b


def match(pattern, rec, stops, cache=None):
    """How much of the pattern's stops are at the stops of the OSM route `rec` (any order: the list of an OSM relation is not
    to be trusted, the direction is told by the road, see `runs_along`). `cache`: {route id: StopsOf} kept by the caller."""
    idx = None if cache is None else cache.get(rec["id"])
    if idx is None:
        idx = StopsOf(rec, stops)
        if cache is not None:
            cache[rec["id"]] = idx
    pb, rb = pattern_bbox(pattern), idx.bbox
    if pb[2] < rb[0] or pb[0] > rb[2] or pb[3] < rb[1] or pb[1] > rb[3]:      # the two do not even share a place
        return 0.0
    hits = 0
    for st in pattern["stops"]:
        if idx.hit(st["lat"], st["lon"]):
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


class Road:
    """A route's road as arrays (a local flat frame in metres) for projecting places on it, made once per route."""

    def __init__(self, ways):
        import numpy as np
        self.np = np
        path = chain(ways)
        self.ok = len(path) >= 2
        if not self.ok:
            return
        lat0 = path[0][0]
        self.kx = math.cos(math.radians(lat0)) * 111320
        a = np.array(path, dtype=float)
        self.x, self.y = a[:, 1] * self.kx, a[:, 0] * 110574
        self.dx, self.dy = np.diff(self.x), np.diff(self.y)
        self.len = np.hypot(self.dx, self.dy)
        self.cum = np.concatenate(([0.0], np.cumsum(self.len)))
        self.l2 = np.where(self.len > 0, self.len ** 2, 1.0)

    def project(self, lat, lon):
        """(along in metres, distance in metres) of the nearest point of the road to a place."""
        np = self.np
        px, py = lon * self.kx, lat * 110574
        t = np.clip(((px - self.x[:-1]) * self.dx + (py - self.y[:-1]) * self.dy) / self.l2, 0.0, 1.0)
        d = np.hypot(self.x[:-1] + t * self.dx - px, self.y[:-1] + t * self.dy - py)
        i = int(d.argmin())
        return float(self.cum[i] + t[i] * self.len[i]), float(d[i])


def runs_along(ways, pattern, cache=None, key=None):
    """True when the road of `ways` goes the way the pattern's stops are listed, False when the other way, None when it cannot
    be told (a road without stops of the pattern on it). `cache` / `key`: the caller's {route id: Road}."""
    road = None if cache is None or key is None else cache.get(key)
    if road is None:
        road = Road(ways)
        if cache is not None and key is not None:
            cache[key] = road
    if not road.ok:
        return None
    inc = dec = 0
    last = None
    for st in pattern["stops"]:
        a, d = road.project(st["lat"], st["lon"])
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


def ends_key(p):
    """What tells one direction of a line from another and survives a reissued timetable: the names of its first and last stop (of a
    loop, also the second: its two directions differ there). A stop added in the middle or a renumbered stop id changes nothing."""
    names = [fold(s["name"]) or s["sid"] for s in p["stops"]]
    return "|".join([names[0], names[-1]] + ([names[1]] if names[0] == names[-1] and len(names) > 2 else []))


def merge(records, stops, patterns, feed="cz", cc="CZ", lic="CIS-JR"):
    """-> (records, report). `records` are the OSM lines (modified list returned), `stops` the stops dict (timetable-only stops
    are added to it)."""
    by_ref = {}
    for r in records:
        by_ref.setdefault(fold(r["t"].get("ref") or r["t"].get("name")), []).append(r)
    grid = StopGrid(stops)
    out, consumed, merged, alone, duplicates, by_id = [], set(), 0, 0, 0, {}
    turned = 0
    stops_of, roads = {}, {}

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

    # (the busiest way of a line is first: of two ways with the same ends it keeps the plain id)
    for p in sorted(patterns, key=lambda q: (-q.get("trips", 0), q.get("feed") or "", q["pid"])):
        feed, lic = p.get("feed") or feed, p.get("lic") or lic
        best, bratio = None, 0.0
        for rec in (by_ref.get(fold(p["ref"]), ()) if fold(p["ref"]) else ()):
            ratio = match(p, rec, stops, stops_of)
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
        src = [{"k": "gtfs", "feed": feed, "id": str(p["pid"]), "lic": lic}]
        if best is not None:
            consumed.add(best["id"])
            merged += 1
            t = dict(best["t"])
            t["public_transport:version"] = "2"
            key = f"{best['id']}|{ends_key(p)}"
            ways = best["ways"]
            if runs_along(ways, p, roads, best["id"]) is False:                 # OSM has the line the other way round: turn the road
                ways = reverse_ways(ways)
                turned += 1
            rec = {"v": 1, "id": ids.gtfs_route_id(feed, key), "cc": cc, "mode": best["mode"], "t": t, "ways": ways,
                   "stops": stop_list, "times": p["times"], "src": best["src"] + src}
        else:
            alone += 1
            key = f"{p['ref']}|{ends_key(p)}"                                  # (hashed with the feed)
            t = {"ref": p["ref"], "name": f"{p['ref']}: {first} - {last}", "from": first, "to": last, "operator": p["agency"],
                 "route": "bus", "public_transport:version": "2"}
            rec = {"v": 1, "id": ids.gtfs_route_id(feed, key), "cc": cc, "mode": "bus", "t": t, "ways": [], "stops": stop_list,
                   "times": p["times"], "src": src}
        # the id depends on the number and the ends of the way, not on the stops between: it stays when a stop is added or a feed
        # renumbers its stops. Two ways with the same ends but other stops between get the id of their whole stop list.
        same = by_id.get(rec["id"])
        if same is not None and [s["s"] for s in same["stops"]] != [s["s"] for s in rec["stops"]]:
            rec["id"] = ids.gtfs_route_id(feed, f"{key}|{','.join(str(s['s']) for s in rec['stops'])}")
            same = by_id.get(rec["id"])
        # two patterns with the same number and the same stops (another operator's route id, another calendar) are one line
        # with the departures of both
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
