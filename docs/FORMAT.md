# Format `ebr-route/1`

Every file is UTF-8 JSON with LF line endings. The schemas are in [`schema/`](../schema) and are checked by
`python builder/validate.py` (and by CI on every pull request).

## Layout

```
manifest.json                       what is in the database: per country (`CZ`, a US state `US-AL`) the number of lines, stops and tiles, the bounding box, the build date, the sources
data/<CC>/routes/<xxx>/<id>.json    one line going one way; <xxx> = id % 1000 (so a folder holds a thousand files at most)
data/<CC>/index.json                the names of the tiles that exist (the service asks it once per country)
data/<CC>/tiles/<ty>_<tx>.json      the stops and the lines calling at them in one tile (0.05 x 0.05 degrees)
edits/<CC>/...                      the hand-made layer (see below)
sources/feeds.json                  the timetable feeds the builder may use, with their licences
```

`data/` is **generated**. Do not edit it: the next build writes it again. Edit under `edits/`.

## A tile

`ty = floor((lat + 90) / 0.05)`, `tx = floor((lon + 180) / 0.05)`. The file name is `<ty>_<tx>.json`.

```json
{
"v":1, "cc":"CZ", "tile":[1400,1944],
"stops":[
  {"id":3847822802,"lat":50.5365,"lon":16.2461,"k":"p","n":"Police nad Metují, aut.st.","ref":"3"},
  ...
],
"routes":[
  {"id":6978584,"t":{"ref":"360","name":"360: ...","from":"...","to":"...","network":"IREDO","route":"bus","public_transport:version":"2"},
   "n":16,"s":[[3847822802,2],[8021928782,1]]},
  ...
]
}
```

- `stops[].k`: `p` platform (bus stop), `x` stop position, `s` bus station. `q:1` request stop, `g:1` a stop that only a timetable has.
- `routes[]`: a line that calls at a stop of this tile. `n` = how many stops the line has, `s` = its stops that are in this
  tile as `[stop id, place in the line (1 = first), place of the last call if the line lists the stop twice]`.
- A line that crosses several tiles is in each of them (with the stops of that tile).

## A line

```json
{
"v":1,
"id":6978584,
"cc":"CZ",
"mode":"bus",
"t":{"ref":"360","name":"360: A - B","from":"A","to":"B","network":"IREDO","operator":"...","public_transport:version":"2"},
"ways":[
  {"g":"}bmsH}kabBkCB"},
  {"g":"gfmsHsqabBa@xD","t":{"maxspeed":"50"}}
],
"stops":[
  {"s":3847822802,"r":"platform","la":50.5365,"lo":16.2461,"n":"A"},
  {"s":8021928782,"r":"platform","o":240,"la":50.5401,"lo":16.2511,"n":"B"}
],
"times":{"wd":[355,425,785],"sa":[480],"su":[]},
"src":[{"k":"osm","id":"r6978584"},{"k":"gtfs","feed":"cz-jrutil","id":"1234","lic":"CIS-JR"}]
}
```

| Field | Meaning |
|---|---|
| `id` | A number. The OSM relation id for lines of the map; `3·10¹² + hash` for lines made from a timetable or by hand (the hash is of the feed, the line's number and the names of the first and last stop of the way, two ways of a loop also of the second stop: so a line keeps its id between builds, also when a stop is added or the feed renumbers its stops). |
| `t` | The tags of the line the mod reads (`ref`, `name`, `from`, `to`, `network`, `operator`, `colour`, `public_transport:version`, …). |
| `ways` | The road. Ways **in the order the map lists them**; the mod joins them. `g` is a [Google polyline](https://developers.google.com/maps/documentation/utilities/polylinealgorithm) (precision 1e-5, simplified to 2 m). `t` = the tags of the way that give a speed limit. **Empty = the line has no road here**: the data service asks a routing server for it the first time and keeps the answer. |
| `stops` | The stops **in the order the source lists them**. The mod puts them in order along the road (`osm.parseRoute`), so there is one such logic for the database, for live map answers and for timetables. `la`, `lo`, `n` are copies of the tile's stop, written by the builder. `o` = seconds from the start of the trip (lines with a timetable). |
| `times` | Departures from the first stop on a **typical** weekday (Wednesday), Saturday and Sunday, in minutes of the day (may pass 1440). Not a calendar: holidays and one-off days are not in the database. |
| `src` | Where the line comes from. A line from both has both. `k:"edit"` = a hand edit was applied. |

### Stop ids

| Source | Id |
|---|---|
| OSM node | its OSM id |
| OSM way (a platform or station drawn as an area) | way id + 5·10¹² |
| a timetable stop that the map does not have | 6·10¹² + a stable hash of feed and stop |

## How lines are made

1. **OSM** (country extract from Geofabrik): every relation `type=route`, `route=bus|trolleybus|minibus|share_taxi` with at least two stops.
2. **Timetables** (GTFS): a timetable pattern is matched with the OSM routes of the same number (60 % of its stops within 60 m of the route's stops and the same direction). A match gives a line with the **road and stop ids of OSM and the order and times of the timetable**; the OSM route is then replaced by such lines. A pattern without a match is a line of its own without a road. Patterns with fewer than 4 trips in 28 days are left out.
3. **Edits** are laid over (below). Then the files are written.

## Edits (the hand-made layer)

A rebuild never loses them. Anyone can send a pull request; CI checks the schema.

`edits/<CC>/<line id>.json` changes one generated line:

```json
{
  "set": {"name": "360: Police - Adršpach", "from": "...", "to": "..."},
  "tags": {"public_transport:version": "2"},
  "stops.remove": [3847822796],
  "stops.insert": [{"after": 3847822802, "s": 6000000000123, "o": 300}],
  "times": {"wd": [355, 425], "sa": [], "su": []},
  "hidden": false,
  "why": "the stop of the school is missing in OSM", "by": "name", "date": "2026-11-20"
}
```

`edits/<CC>/new/<name>.json` is a whole new line (a line record as above; `id` may be left out). `edits/<CC>/stops.json`
changes stops: `{"<stop id>": {"n": "new name", "lat": 50.1, "lon": 16.2, "k": "p"}, "<id>": {"remove": true}}`.

An edit whose line is gone from the sources is not dropped silently: the build report lists it under `orphan_edits`.
