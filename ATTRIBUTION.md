# Attribution

## Map data

Contains information from **OpenStreetMap**, © OpenStreetMap contributors, available under the
[Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/). Lines, stops and road shapes are derived from
extracts of OpenStreetMap made available by [Geofabrik](https://download.geofabrik.de/). This database is a *derivative database*
and is offered under the same licence (see `LICENSE`). Every line says where it comes from in its `src` field.

## Timetables

Each timetable source is listed in `sources/feeds.json` with its licence and the attribution it asks for. The ones used so far:

| Feed | Country | Source | Licence |
|---|---|---|---|
| `cz-jrutil` | CZ | Timetable register CIS JŘ of the Czech Ministry of Transport, converted to GTFS by the JrUtil project (published at `data.jr.ggu.cz`) | data of CIS JŘ without conditions; stop positions from OpenStreetMap (ODbL) and open data of towns (CC BY, CC BY-SA, CC0) |

Only the departure times of a typical weekday, Saturday and Sunday and the times between stops are kept, not the timetables themselves.
