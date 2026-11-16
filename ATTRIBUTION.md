# Attribution

## Map data

Contains information from **OpenStreetMap**, © OpenStreetMap contributors, available under the
[Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/). Lines, stops and road shapes are derived from
extracts of OpenStreetMap made available by [Geofabrik](https://download.geofabrik.de/) and [openstreetmap.fr](https://download.openstreetmap.fr/). This database is a *derivative database*
and is offered under the same licence (see `LICENSE`). Every line says where it comes from in its `src` field.

## Timetables

Timetables come only from feeds with an explicitly open licence (CC0, CC BY, ODbL, ODC-By / PDDL, Etalab 2.0, Open Government Licence 3.0,
Datenlizenz Deutschland and a few national open-data licences). The complete list, with the licence of each feed and its source, is
[`sources/FEEDS.md`](sources/FEEDS.md); the same as data: `sources/used.json`. Every line with times names its feed in the `src` field.
Feeds whose licence is not stated or not open are never used; `sources/review.json` lists them for a human to check.

Manual feeds are described in `sources/feeds.json`; for the Czech Republic that is the timetable register CIS JR of the Ministry of
Transport (JrUtil conversion, data of CIS JR without conditions).

Only the departure times of a typical weekday, Saturday and Sunday and the times between stops are kept, not the timetables themselves.
