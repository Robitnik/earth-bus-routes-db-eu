# Earth Bus Routes — route database

Bus lines and stops of whole countries, prepared in advance as small JSON files, for the
[Earth Bus Routes](https://github.com/Robitnik/earth-bus-routes) add-on of BeamNG.Earth.

Why: asking OpenStreetMap servers live is slow (minutes on a bad day) and downloading a country's timetables takes ten minutes
the first time. Here the work is done once, by a builder, and the add-on fetches one file of a few KB for the place the player
stands in. The files are plain JSON in git: you can read them, correct them by hand (see `edits/`) and send a pull request.

- **What is in it:** see [`manifest.json`](manifest.json) (countries, number of lines and stops, build date, sources).
- **Format:** [`docs/FORMAT.md`](docs/FORMAT.md), schemas in [`schema/`](schema).
- **Correcting a line:** put a file under `edits/<country>/` (format in FORMAT.md). A rebuild keeps it.
- **Licence:** the data is derived from OpenStreetMap and is under the [ODbL](LICENSE); see [`ATTRIBUTION.md`](ATTRIBUTION.md).
  The builder code is MIT ([`builder/LICENSE`](builder/LICENSE)).

## Building a country

Needs Python 3.10+ and `pip install osmium jsonschema`.

```
# 1. the country's OSM extract: https://download.geofabrik.de/europe/czech-republic-latest.osm.pbf
# 2. (optional) the timetables' sqlite index made by the data service of the add-on (gtfs_<CC>.db)
python builder/build.py --cc CZ --pbf czech-republic.osm.pbf --gtfs gtfs_CZ.db --gtfs-feed cz-jrutil --use-cache
python builder/validate.py
```

It rewrites `data/CZ/` and `manifest.json`, and writes `reports/CZ.json` (counts, orphan edits). A build of Czechia takes about
ten minutes (the pass over the extract is five).

## How the add-on uses it

The data service of the add-on asks `manifest.json`, then the tile (`data/<CC>/tiles/<ty>_<tx>.json`) of the place for "nearby",
and one line file when a line is picked. It tries `raw.githubusercontent.com` first and the jsDelivr mirror when that fails,
keeps files on disk, and falls back to live data (Overpass, timetables, routing servers) for places that are not in the database.
`BNE_BUSDATA_DB=<folder>` points the service at a local copy.
