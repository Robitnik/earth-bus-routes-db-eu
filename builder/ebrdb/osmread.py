"""OpenStreetMap -> bus stops and `ebr-route/1` line records, read from a country's .pbf extract (Geofabrik).

Pass 1 reads the route relations (route = bus / trolleybus / minibus / share_taxi) and notes which nodes and ways they use.
Pass 2 reads nodes and ways with their locations: the stops of the map, the road shapes of the routes.

A record keeps the relation as OpenStreetMap lists it (ways in order, stops in the listed order, the few tags the mod reads).
The mod's own `osm.parseRoute` puts the stops in order along the road, so one piece of logic does that for the database,
the live Overpass answers and the timetables alike.
"""
from . import ids, poly

MODES = ("bus", "trolleybus", "minibus", "share_taxi")
ROUTE_TAGS = ("ref", "name", "from", "to", "network", "operator", "colour", "public_transport:version", "complete",
              "night_service", "bus", "route", "via")
WAY_ROLES = ("", "forward", "backward", "hail_and_ride")
SHAPE_TOL_M = 2.0

_NOT_BUS = ("railway", "tram", "subway", "train", "light_rail", "monorail", "funicular", "ferry", "aerialway")


def stop_kind(tags, role=""):
    """'s' station, 'x' stop position, 'p' platform or None: the same rules as the mod's osm.lua nodeKind."""
    if tags.get("amenity") == "bus_station" or (tags.get("public_transport") == "station" and tags.get("bus") == "yes"):
        return "s"
    pt = tags.get("public_transport")
    if pt == "stop_position":
        return "x"
    if pt == "platform" or tags.get("highway") == "bus_stop":
        return "p"
    if role.startswith("platform"):
        return "p"
    if role.startswith("stop"):
        return "x"
    return None


def is_bus_stop(tags):
    """A tagged node/way of the map that is a stop for buses (not a rail or tram one)."""
    kind = stop_kind(tags)
    if kind is None or (tags.get("bus") == "no" and tags.get("trolleybus") != "yes"):
        return None
    if kind == "x":
        return kind if (tags.get("bus") == "yes" or tags.get("trolleybus") == "yes" or tags.get("minibus") == "yes") else None
    if kind == "p" and tags.get("highway") != "bus_stop" and tags.get("bus") != "yes":
        if any(tags.get(k) for k in _NOT_BUS) or tags.get("tram") == "yes" or tags.get("train") == "yes":
            return None
    return kind


def stop_entry(eid, lat, lon, tags, kind):
    e = {"id": eid, "lat": round(lat, 6), "lon": round(lon, 6), "k": kind}
    if tags.get("name"):
        e["n"] = tags["name"]
    if tags.get("ref"):
        e["ref"] = tags["ref"]
    if tags.get("request_stop") == "yes":
        e["q"] = 1
    return e


def way_tags(tags):
    t = {k: tags[k] for k in ("maxspeed", "maxspeed:forward", "maxspeed:backward", "zone:maxspeed", "maxspeed:type") if k in tags}
    if tags.get("highway") in ("motorway", "living_street"):
        t["highway"] = tags["highway"]
    return t


def read_pbf(path, progress=None):
    """-> (relations, stops): relations = {id: {tags, members}} of bus routes; stops = {id: stop entry} (all the bus stops of the
    file, plus any node a route lists as a stop); each route's ways (polyline) are under relations[id]['ways']."""
    import osmium
    say = progress or (lambda *_: None)
    rels = {}
    for r in osmium.FileProcessor(path, osmium.osm.RELATION):
        tg = r.tags
        if tg.get("type") == "route" and tg.get("route") in MODES:
            rels[r.id] = {"tags": {k: tg[k] for k in ROUTE_TAGS if tg.get(k)},
                          "members": [(m.type, m.ref, m.role) for m in r.members if m.type in ("n", "w")]}
    say(f"relations: {len(rels)}")
    want_n, want_w = set(), set()
    for r in rels.values():
        for t, ref, role in r["members"]:
            if t == "n":
                want_n.add(ref)
            elif role in WAY_ROLES or role.startswith("platform"):
                want_w.add(ref)
    ways, nodes, stops = {}, {}, {}
    count = 0
    fp = osmium.FileProcessor(path, osmium.osm.NODE | osmium.osm.WAY).with_locations()
    for o in fp:
        count += 1
        if o.is_node():
            tg = o.tags
            keep = o.id in want_n
            if not keep and len(tg) == 0:
                continue
            if not o.location.valid():
                continue
            kind = is_bus_stop(tg)
            if kind:
                stops[o.id] = stop_entry(o.id, o.location.lat, o.location.lon, tg, kind)
            if keep:
                nodes[o.id] = (o.location.lat, o.location.lon, {k: tg[k] for k in ("name", "ref", "request_stop", "public_transport",
                                                                                  "highway", "amenity", "bus", "trolleybus") if k in tg})
        else:
            tg = o.tags
            station = len(tg) and is_bus_stop(tg)
            if o.id not in want_w and not station:
                continue
            try:
                pts = [(n.lat, n.lon) for n in o.nodes]
            except osmium.InvalidLocationError:
                continue
            if len(pts) < 2:
                continue
            ways[o.id] = (pts, {k: tg[k] for k in ("name", "ref", "request_stop", "public_transport", "highway", "amenity", "bus",
                                                 "trolleybus", "maxspeed", "maxspeed:forward", "maxspeed:backward", "zone:maxspeed",
                                                 "maxspeed:type") if k in tg})
            if station:
                la = [p[0] for p in pts]
                lo = [p[1] for p in pts]
                stops[ids.way_stop_id(o.id)] = stop_entry(ids.way_stop_id(o.id), (min(la) + max(la)) / 2, (min(lo) + max(lo)) / 2, tg, station)
        if count % 5000000 == 0:
            say(f"objects: {count // 1000000} M")
    say(f"stops: {len(stops)}, route ways: {len(ways)}")
    return rels, nodes, ways, stops


def _joinable(a, b):
    """Two ways of a route (points, role, tags) are one way when they carry the same tags, none of them about one direction, and
    the second continues the first (it may be listed reversed)."""
    if a[1] != b[1] or a[2] != b[2] or "maxspeed:forward" in a[2] or "maxspeed:backward" in a[2]:
        return None
    end = a[0][-1]
    if poly.hav(end, b[0][0]) < 0.5:
        return b[0]
    if poly.hav(end, b[0][-1]) < 0.5:
        return b[0][::-1]
    return None


def merge_ways(parts):
    """[(points, role, tags)] -> the same with consecutive continuing ways of equal tags made one."""
    out = []
    for p in parts:
        if out:
            nxt = _joinable(out[-1], p)
            if nxt is not None:
                out[-1] = (out[-1][0] + nxt[1:], out[-1][1], out[-1][2])
                continue
        out.append(p)
    return out


def build_routes(rels, nodes, ways, stops, cc, shape_tol=SHAPE_TOL_M):
    """-> [record]: the line records of the relations (see docs/FORMAT.md). A node a route lists as a stop but the map does not
    tag as one is added to `stops`. Relations with fewer than two stops or no stop coordinates are skipped (counted)."""
    out, skipped = [], 0
    for rid in sorted(rels):
        r = rels[rid]
        parts, sl = [], []
        for t, ref, role in r["members"]:
            if t == "w" and (role in WAY_ROLES) and ref in ways:
                parts.append((ways[ref][0], role, way_tags(ways[ref][1])))
                continue
            if t == "n":
                nd = nodes.get(ref)
                if nd is None:
                    continue
                kind = stop_kind(nd[2], role)
                sid = ref
                lat, lon, tg = nd
            elif role.startswith("platform") and ref in ways:
                pts, tg = ways[ref][0], ways[ref][1]
                la = [p[0] for p in pts]
                lo = [p[1] for p in pts]
                kind, sid = stop_kind(tg, role), ids.way_stop_id(ref)
                lat, lon = (min(la) + max(la)) / 2, (min(lo) + max(lo)) / 2
            else:
                continue
            if kind is None:
                continue
            if sid not in stops:
                stops[sid] = stop_entry(sid, lat, lon, tg, kind)
            s = {"s": sid}
            if role:
                s["r"] = role
            sl.append(s)
        if len(sl) < 2:
            skipped += 1
            continue
        wl = []
        for pts, role, tg in merge_ways(parts):
            w = {"g": poly.encode(poly.simplify(pts, shape_tol))}
            if role:
                w["r"] = role
            if tg:
                w["t"] = tg
            wl.append(w)
        out.append({"v": 1, "id": rid, "cc": cc, "mode": r["tags"].get("route", "bus"), "t": r["tags"], "ways": wl, "stops": sl,
                    "src": [{"k": "osm", "id": f"r{rid}"}]})
    return out, skipped
