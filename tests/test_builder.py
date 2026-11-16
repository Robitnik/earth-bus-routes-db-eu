"""Tests of the builder on small made-up data (no .pbf needed): python -m unittest discover -s tests -t . -v"""
import json
import pathlib
import sqlite3
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "builder"))
from ebrdb import edits, gtfsmerge, ids, osmread, output, poly  # noqa: E402
import validate  # noqa: E402

# a straight road east along lat 50.0 from lon 16.00 to 16.04, three bus stops on it
A, B, C = (50.0003, 16.005), (50.0003, 16.02), (50.0003, 16.035)


def road(lon0, lon1, n=5):
    return [(50.0, lon0 + (lon1 - lon0) * i / (n - 1)) for i in range(n)]


def stop(sid, p, name, kind="p"):
    return {"id": sid, "lat": p[0], "lon": p[1], "k": kind, "n": name}


def osm_fixture():
    """(rels, nodes, ways, stops) as read_pbf returns them: a line 7 with two ways (the second listed reversed) and 3 stops."""
    w1, w2 = road(16.0, 16.02), road(16.02, 16.04)
    ways = {101: (w1, {"maxspeed": "50"}), 102: (w2[::-1], {"maxspeed": "50"})}
    nodes = {1: (*A, {"name": "Alpha", "highway": "bus_stop"}), 2: (*B, {"name": "Beta", "highway": "bus_stop"}),
             3: (*C, {"name": "Gamma", "highway": "bus_stop", "request_stop": "yes"})}
    rels = {7: {"tags": {"ref": "7", "name": "7: Alpha - Gamma", "route": "bus", "public_transport:version": "2"},
                "members": [("w", 101, ""), ("w", 102, ""), ("n", 1, "platform"), ("n", 2, "platform"), ("n", 3, "platform")]}}
    stops = {1: stop(1, A, "Alpha"), 2: stop(2, B, "Beta"), 3: stop(3, C, "Gamma")}
    return rels, nodes, ways, stops


class PolyTests(unittest.TestCase):
    def test_roundtrip(self):
        pts = [(50.123456, 16.654321), (50.1235, 16.6544), (50.1, 16.6)]
        back = poly.decode(poly.encode(pts))
        for a, b in zip(pts, back):
            self.assertAlmostEqual(a[0], b[0], places=5)
            self.assertAlmostEqual(a[1], b[1], places=5)

    def test_known_value(self):
        # the example of Google's documentation
        self.assertEqual(poly.encode([(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]), "_p~iF~ps|U_ulLnnqC_mqNvxq`@")

    def test_simplify_keeps_ends_and_drops_straight_points(self):
        pts = road(16.0, 16.01, 50)
        out = poly.simplify(pts, 2)
        self.assertEqual(out, [pts[0], pts[-1]])


class OsmTests(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(osmread.is_bus_stop({"highway": "bus_stop"}), "p")
        self.assertEqual(osmread.is_bus_stop({"amenity": "bus_station"}), "s")
        self.assertIsNone(osmread.is_bus_stop({"public_transport": "stop_position", "tram": "yes"}))
        self.assertEqual(osmread.is_bus_stop({"public_transport": "stop_position", "bus": "yes"}), "x")
        self.assertIsNone(osmread.is_bus_stop({"public_transport": "platform", "railway": "platform"}))
        self.assertIsNone(osmread.is_bus_stop({"highway": "bus_stop", "bus": "no"}))

    def test_ways_with_equal_tags_are_joined(self):
        rels, nodes, ways, stops = osm_fixture()
        recs, skipped = osmread.build_routes(rels, nodes, ways, stops, "CZ")
        self.assertEqual(skipped, 0)
        r = recs[0]
        self.assertEqual(len(r["ways"]), 1)                    # two ways with maxspeed 50, the second one listed reversed
        pts = poly.decode(r["ways"][0]["g"])
        self.assertAlmostEqual(pts[0][1], 16.0, places=4)
        self.assertAlmostEqual(pts[-1][1], 16.04, places=4)
        self.assertEqual([s["s"] for s in r["stops"]], [1, 2, 3])

    def test_ways_of_different_speed_stay_apart(self):
        rels, nodes, ways, stops = osm_fixture()
        ways[102] = (ways[102][0], {"maxspeed": "30"})
        r = osmread.build_routes(rels, nodes, ways, stops, "CZ")[0][0]
        self.assertEqual([w.get("t") for w in r["ways"]], [{"maxspeed": "50"}, {"maxspeed": "30"}])

    def test_line_with_one_stop_is_skipped(self):
        rels, nodes, ways, stops = osm_fixture()
        rels[7]["members"] = rels[7]["members"][:3]
        recs, skipped = osmread.build_routes(rels, nodes, ways, stops, "CZ")
        self.assertEqual((len(recs), skipped), (0, 1))


def pattern(ref, pts, names, offs, starts=(360, 420)):
    return {"pid": 1, "ref": ref, "name": f"{ref}: x", "agency": "Op", "head": "", "trips": 10,
            "stops": [{"sid": f"g{i}", "name": n, "lat": p[0], "lon": p[1], "off": o} for i, (p, n, o) in enumerate(zip(pts, names, offs))],
            "times": {"wd": list(starts), "sa": [], "su": []}}


class GtfsTests(unittest.TestCase):
    def build(self):
        rels, nodes, ways, stops = osm_fixture()
        recs, _ = osmread.build_routes(rels, nodes, ways, stops, "CZ")
        return recs, stops

    def test_merge_gives_osm_road_and_gtfs_times(self):
        recs, stops = self.build()
        p = pattern("7", [A, B, C], ["Alpha", "Beta", "Gamma"], [0, 300, 600])
        out, rep = gtfsmerge.merge(recs, stops, [p], "t", "CZ")
        self.assertEqual((rep["merged_with_osm"], rep["gtfs_only"], rep["osm_replaced"]), (1, 0, 1))
        self.assertEqual(len(out), 1)
        r = out[0]
        self.assertEqual([s["s"] for s in r["stops"]], [1, 2, 3])          # the ids of the map
        self.assertEqual([s["o"] for s in r["stops"]], [0, 300, 600])
        self.assertEqual(r["times"]["wd"], [360, 420])
        self.assertTrue(r["ways"])
        self.assertEqual({x["k"] for x in r["src"]}, {"osm", "gtfs"})

    def test_road_is_turned_to_the_direction_of_the_timetable(self):
        recs, stops = self.build()
        p = pattern("7", [C, B, A], ["Gamma", "Beta", "Alpha"], [0, 300, 600])      # the line goes the other way
        out, rep = gtfsmerge.merge(recs, stops, [p], "t", "CZ")
        self.assertEqual(rep["roads_turned"], 1)
        pts = poly.decode(out[0]["ways"][0]["g"])
        self.assertGreater(pts[0][1], pts[-1][1])                         # starts in the east now
        self.assertFalse(gtfsmerge.runs_along(out[0]["ways"], p) is False)

    def test_unmatched_pattern_is_a_line_of_its_own_without_a_road(self):
        recs, stops = self.build()
        far = [(50.5, 17.0), (50.5, 17.01)]
        p = pattern("99", far, ["X", "Y"], [0, 120])
        out, rep = gtfsmerge.merge(recs, stops, [p], "t", "CZ")
        self.assertEqual((rep["gtfs_only"], rep["osm_kept"]), (1, 1))
        line = next(r for r in out if r["t"]["ref"] == "99")
        self.assertEqual(line["ways"], [])
        self.assertGreaterEqual(line["id"], ids.ROUTE_BASE)
        self.assertTrue(all(s["s"] in stops for s in line["stops"]))

    def test_same_stops_are_one_line_with_both_departures(self):
        recs, stops = self.build()
        a = pattern("99", [(50.5, 17.0), (50.5, 17.01)], ["X", "Y"], [0, 120], starts=(360,))
        b = pattern("99", [(50.5, 17.0), (50.5, 17.01)], ["X", "Y"], [0, 120], starts=(480,))
        out, rep = gtfsmerge.merge(recs, stops, [a, b], "t", "CZ")
        line = next(r for r in out if r["t"]["ref"] == "99")
        self.assertEqual(line["times"]["wd"], [360, 480])
        self.assertEqual(rep["same_stops_joined"], 1)

    def line_ids(self, patterns):
        recs, stops = self.build()
        out, _ = gtfsmerge.merge(recs, stops, patterns, "t", "CZ")
        return sorted(r["id"] for r in out if r["t"].get("ref") == "99")

    def test_id_stays_when_a_stop_is_added_or_the_feed_renumbers_its_stops(self):
        P = [(50.5, 17.0), (50.5, 17.01), (50.5, 17.02), (50.5, 17.03)]
        before = self.line_ids([pattern("99", [P[0], P[1], P[3]], ["X", "Y", "Z"], [0, 100, 200])])
        after = pattern("99", P, ["X", "Y", "W", "Z"], [0, 60, 120, 200])           # a stop added in the middle
        for i, s in enumerate(after["stops"]):
            s["sid"] = f"renumbered{i}"
        self.assertEqual(len(before), 1)
        self.assertEqual(before, self.line_ids([after]))

    def test_two_directions_of_a_loop_have_two_ids(self):
        P = [(50.5, 17.0), (50.5, 17.01), (50.5, 17.02)]
        there = pattern("99", [P[0], P[1], P[2], P[0]], ["X", "Y", "Z", "X"], [0, 60, 120, 180])
        back = pattern("99", [P[0], P[2], P[1], P[0]], ["X", "Z", "Y", "X"], [0, 60, 120, 180])
        back["pid"] = 2
        self.assertEqual(len(self.line_ids([there, back])), 2)

    def test_ways_with_the_same_ends_but_other_stops_between_stay_two_lines(self):
        P = [(50.5, 17.0), (50.5, 17.01), (50.5, 17.02), (50.5, 17.03)]
        a = pattern("99", [P[0], P[1], P[3]], ["X", "Y", "Z"], [0, 100, 200])
        b = pattern("99", [P[0], P[2], P[3]], ["X", "Y", "Z"], [0, 100, 200])      # the same ends and names, another middle stop
        b["pid"] = 2
        b["stops"][1]["sid"] = "other"
        self.assertEqual(len(self.line_ids([a, b])), 2)
        self.assertEqual(self.line_ids([a, b]), self.line_ids([b, a]))

    def test_nameless_stop_takes_the_name_of_the_timetable(self):
        recs, stops = self.build()
        del stops[2]["n"]
        p = pattern("7", [A, B, C], ["Alpha", "Beta", "Gamma"], [0, 300, 600])
        gtfsmerge.merge(recs, stops, [p], "t", "CZ")
        self.assertEqual(stops[2]["n"], "Beta")

    def test_index_reader(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = pathlib.Path(d) / "g.db"
            db = sqlite3.connect(db_path)
            db.executescript("""CREATE TABLE stops(sid TEXT PRIMARY KEY, name TEXT, fname TEXT, lat REAL, lon REAL, refs TEXT);
              CREATE TABLE patterns(pid INTEGER PRIMARY KEY, ref TEXT, name TEXT, agency TEXT, head TEXT, n INTEGER, stops TEXT, offs TEXT, trips INTEGER);
              CREATE TABLE deps(pid INTEGER, start INTEGER, mask INTEGER);
              CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT);""")
            db.execute("INSERT INTO stops VALUES ('a','Alpha','alpha',50.0,16.0,'7'),('b','Beta','beta',50.0,16.01,'7')")
            db.execute("INSERT INTO patterns VALUES (1,'7','7: A - B','Op','',2,'a,b','0,300',20)")
            db.execute("INSERT INTO patterns VALUES (2,'8','8: A - B','Op','',2,'a,b','0,300',1)")      # too few trips
            db.execute("INSERT INTO deps VALUES (1, 21600, 16383),(1, 25200, 16383)")                      # 06:00, 07:00 every day
            db.execute("INSERT INTO meta VALUES ('today','20261111'),('days','28'),('schema','2')")
            # a feed that gives no place for some stops (0, 0): they are left out of a line, with their times; a line left with one stop goes
            db.execute("INSERT INTO stops VALUES ('z','Nowhere','nowhere',0.0,0.0,'9'),('c','Gamma','gamma',50.0,16.02,'9')")
            db.execute("INSERT INTO patterns VALUES (3,'9','9: A - C','Op','',3,'a,z,c','0,100,300',20)")
            db.execute("INSERT INTO patterns VALUES (4,'10','10: A - Z','Op','',2,'a,z','0,300',20)")
            db.execute("INSERT INTO deps VALUES (3, 21600, 16383),(4, 21600, 16383)")
            db.commit()
            db.close()
            pats = gtfsmerge.read_patterns(db_path, 4)
        by_ref = {p["ref"]: p for p in pats}
        self.assertEqual(sorted(by_ref), ["7", "9"])
        pats = [by_ref["7"]]
        self.assertEqual(len(pats), 1)
        self.assertEqual(pats[0]["times"], {"wd": [360, 420], "sa": [360, 420], "su": [360, 420]})
        self.assertEqual(pats[0]["stops"][1]["off"], 300)
        self.assertEqual([(x["name"], x["off"]) for x in by_ref["9"]["stops"]], [("Alpha", 0), ("Gamma", 300)])


class EditTests(unittest.TestCase):
    def setUp(self):
        rels, nodes, ways, stops = osm_fixture()
        self.recs, _ = osmread.build_routes(rels, nodes, ways, stops, "CZ")
        self.stops = stops

    def test_set_remove_insert_and_hide(self):
        ed = {"lines": {7: {"set": {"name": "Seven"}, "stops.remove": [2], "stops.insert": [{"after": 1, "s": 9, "o": 60}],
                            "by": "me", "date": "2026-11-16"}}, "new": [], "stops": {2: {"n": "Beta!"}}}
        out, orphans = edits.apply(self.recs, self.stops, ed)
        r = out[0]
        self.assertEqual(r["t"]["name"], "Seven")
        self.assertEqual([s["s"] for s in r["stops"]], [1, 9, 3])
        self.assertEqual(r["src"][-1]["k"], "edit")
        self.assertEqual(self.stops[2]["n"], "Beta!")
        self.assertEqual(orphans, [])
        out2, _ = edits.apply(out, self.stops, {"lines": {7: {"hidden": True}}, "new": [], "stops": {}})
        self.assertEqual(out2, [])

    def test_edit_of_a_vanished_line_is_reported(self):
        _, orphans = edits.apply(self.recs, self.stops, {"lines": {123: {"hidden": True}}, "new": [], "stops": {}})
        self.assertEqual(orphans, [123])

    def test_new_line(self):
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "new").mkdir()
            rec = {"cc": "CZ", "mode": "bus", "t": {"ref": "X1"}, "stops": [{"s": 1}, {"s": 2}]}
            (pathlib.Path(d) / "new" / "x1.json").write_text(json.dumps(rec), encoding="utf-8")
            ed = edits.load(d)
        out, _ = edits.apply([], self.stops, ed)
        self.assertEqual(out[0]["t"]["ref"], "X1")
        self.assertGreaterEqual(out[0]["id"], ids.ROUTE_BASE)


class OutputTests(unittest.TestCase):
    def test_written_database_is_valid_and_stable(self):
        rels, nodes, ways, stops = osm_fixture()
        recs, _ = osmread.build_routes(rels, nodes, ways, stops, "CZ")
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            (root / "schema").mkdir()
            for f in (ROOT / "schema").glob("*.json"):
                (root / "schema" / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
            entry = output.write(root, "CZ", recs, stops, {"osm": "x"}, "2026-11-16")
            output.write_manifest(root, {"CZ": entry})
            self.assertEqual(validate.validate(root), [])
            f = root / "data" / "CZ" / "routes" / ids.shard(7) / "7.json"
            first = f.read_text(encoding="utf-8")
            self.assertEqual(json.loads(first)["stops"][0]["n"], "Alpha")            # the stop carries its own name
            output.write(root, "CZ", recs, stops, {"osm": "x"}, "2026-11-16")          # a second build: the same bytes
            self.assertEqual(f.read_text(encoding="utf-8"), first)
            # a stop missing from the tiles is caught by the validator
            tile = next((root / "data" / "CZ" / "tiles").glob("*.json"))
            t = json.loads(tile.read_text(encoding="utf-8"))
            t["stops"] = t["stops"][1:]
            tile.write_text(json.dumps(t), encoding="utf-8")
            self.assertTrue(validate.validate(root))

    def test_tile_lists_the_places_of_a_stop_listed_twice(self):
        rels, nodes, ways, stops = osm_fixture()
        rels[7]["members"].append(("n", 1, "platform"))                                 # a loop: Alpha again at the end
        recs, _ = osmread.build_routes(rels, nodes, ways, stops, "CZ")
        with tempfile.TemporaryDirectory() as d:
            output.write(d, "CZ", recs, stops, {}, "x")
            tiles = [json.loads(f.read_text(encoding="utf-8")) for f in (pathlib.Path(d) / "data" / "CZ" / "tiles").glob("*.json")]
        items = [i for t in tiles for r in t["routes"] for i in r["s"] if i[0] == 1]
        self.assertEqual(items, [[1, 1, 4]])


if __name__ == "__main__":
    unittest.main()
