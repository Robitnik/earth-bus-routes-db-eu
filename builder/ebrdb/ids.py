"""Numeric ids. The mod reads ids as numbers, so every stop and line of the database has one:

  OSM node            its OSM id
  OSM way (platform)  way id + WAY_BASE        (a way's id may equal a node's)
  OSM relation        its OSM id               (below 10**12)
  line only in GTFS   ROUTE_BASE + hash        (a stable hash of feed + pattern key: the same line keeps its id between builds)
  stop only in GTFS   STOP_BASE  + hash
The bands stay clear of the live service's own (10**12 .. 2*10**12 + row numbers).
"""
import hashlib

WAY_BASE = 5 * 10 ** 12
ROUTE_BASE = 3 * 10 ** 12
STOP_BASE = 6 * 10 ** 12
SPAN = 10 ** 11


def _h(text):
    return int(hashlib.sha1(text.encode("utf-8")).hexdigest()[:12], 16) % SPAN


def gtfs_stop_id(feed, sid):
    return STOP_BASE + _h(f"s|{feed}|{sid}")


def gtfs_route_id(feed, key):
    return ROUTE_BASE + _h(f"r|{feed}|{key}")


def way_stop_id(way_id):
    return WAY_BASE + way_id


def shard(route_id):
    """The folder of a route file under data/<cc>/routes/ (a thousand files at most in one)."""
    return f"{route_id % 1000:03d}"
