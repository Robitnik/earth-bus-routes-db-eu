"""The extracts the database is built from (Geofabrik) and their outlines.

An extract is one country (EU) or one state (US). Its `cc` is the key under data/: "CZ", "US-AL". The outline (polygon of the
Geofabrik index) decides which timetable lines belong to the extract: those whose first stop is inside it.
"""
import json
import pathlib

EU = {
    "AT": "austria", "BE": "belgium", "BG": "bulgaria", "HR": "croatia", "CY": "cyprus", "CZ": "czech-republic", "DK": "denmark",
    "EE": "estonia", "FI": "finland", "FR": "france", "DE": "germany", "GR": "greece", "HU": "hungary",
    "IE": "ireland-and-northern-ireland", "IT": "italy", "LV": "latvia", "LT": "lithuania", "LU": "luxembourg", "MT": "malta",
    "NL": "netherlands", "PL": "poland", "PT": "portugal", "RO": "romania", "SK": "slovakia", "SI": "slovenia", "ES": "spain",
    "SE": "sweden",
}
# beyond the EU, when asked for by name
EXTRA = {"GB": "great-britain", "NO": "norway", "CH": "switzerland"}

US = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas", "CA": "california", "CO": "colorado", "CT": "connecticut",
    "DE": "delaware", "DC": "district-of-columbia", "FL": "florida", "GA": "georgia", "HI": "hawaii", "ID": "idaho", "IL": "illinois",
    "IN": "indiana", "IA": "iowa", "KS": "kansas", "KY": "kentucky", "LA": "louisiana", "ME": "maine", "MD": "maryland",
    "MA": "massachusetts", "MI": "michigan", "MN": "minnesota", "MS": "mississippi", "MO": "missouri", "MT": "montana",
    "NE": "nebraska", "NV": "nevada", "NH": "new-hampshire", "NJ": "new-jersey", "NM": "new-mexico", "NY": "new-york",
    "NC": "north-carolina", "ND": "north-dakota", "OH": "ohio", "OK": "oklahoma", "OR": "oregon", "PA": "pennsylvania",
    "RI": "rhode-island", "SC": "south-carolina", "SD": "south-dakota", "TN": "tennessee", "TX": "texas", "UT": "utah",
    "VT": "vermont", "VA": "virginia", "WA": "washington", "WV": "west-virginia", "WI": "wisconsin", "WY": "wyoming",
}


# The same extracts, much faster, from download.openstreetmap.fr (about 40 MB/s where Geofabrik gives 1.4): the countries it has
# (europe/<name>.osm.pbf, rebuilt daily). Its Ireland is the Republic only. Used first; Geofabrik is the fallback and the source of
# everything else (the US by state).
FR_MIRROR = {
    "AT": "austria", "BE": "belgium", "CZ": "czech_republic", "DK": "denmark", "FI": "finland", "FR": "france", "DE": "germany",
    "IE": "ireland", "IT": "italy", "LU": "luxembourg", "NL": "netherlands", "PL": "poland", "PT": "portugal", "SK": "slovakia",
    "ES": "spain", "SE": "sweden", "GB": "united_kingdom", "NO": "norway", "CH": "switzerland",
}


def mirror_url(cc):
    """The fast mirror's link for an extract, or None when it does not have it."""
    name = FR_MIRROR.get(cc)
    return f"https://download.openstreetmap.fr/extracts/europe/{name}.osm.pbf" if name else None


def all_extracts(group="all"):
    """{cc: geofabrik id} for 'eu', 'us' or 'all'."""
    out = {}
    if group in ("eu", "all"):
        out.update(EU)
    if group in ("us", "all"):
        out.update({f"US-{k}": f"us/{v}" for k, v in US.items()})
    if group == "extra":
        out.update(EXTRA)
    return out


class Geofabrik:
    """The Geofabrik index (index-v1.json: ids, download links, outlines)."""

    def __init__(self, path):
        self.path = pathlib.Path(path)
        self.by_id = {}
        for f in json.loads(self.path.read_text(encoding="utf-8"))["features"]:
            self.by_id[f["properties"]["id"]] = f

    def pbf_url(self, gid):
        return self.by_id[gid]["properties"]["urls"]["pbf"]

    def polygon(self, gid):
        """A shapely geometry (lon, lat) of the extract's outline."""
        from shapely.geometry import shape
        return shape(self.by_id[gid]["geometry"])

    def bbox(self, gid):
        """(min lat, min lon, max lat, max lon)"""
        x0, y0, x1, y1 = self.polygon(gid).bounds
        return y0, x0, y1, x1


def inside_test(polygon):
    """A function (lat, lon) -> bool for a shapely polygon (prepared: fast for thousands of points)."""
    from shapely import prepared
    from shapely.geometry import Point
    pp = prepared.prep(polygon)
    return lambda lat, lon: pp.contains(Point(lon, lat))
