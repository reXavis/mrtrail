# The normalized schema

Every adapter produces `trailsdb.schema.Feature` objects, serialized one per line
as compact GeoJSON in `normalized/{source}.geojsonl.gz`.

The schema is deliberately small. Fifteen sources with fifteen different
attribute vocabularies cannot be merged into one rich schema without either
losing information or inventing it; so the shared part stays minimal and
everything else goes into `extras`, which never reaches the tiles.

## Feature classes

| class | what it is | renders like | tile zooms |
| --- | --- | --- | --- |
| `route` | A named thing a user browses and picks: a FEDME homologated sendero, a DOC Great Walk, a EuroVelo corridor, one Camino stage. | the existing OSM `routes` layer — labelled, tappable | z8–14 |
| `segment` | Network infrastructure: swisstopo Wanderwege, USFS centerlines, the Ontario Trail Network. Hundreds of thousands of short lines with no browsable identity. | the ways layer | z8–13 |
| `spot` | A point a walker plans around: a hut, a shelter, a water point, a pass. refuges.info is the one source, and being share-alike it gets a layer of its own. | icons | z8–14 |

The zoom difference is the main size lever in the whole project. Segments are
the bulk of the worldwide kilometre count and dropping them a level roughly
halves their tile cost.

## Fields

| field | required | notes |
| --- | --- | --- |
| `id` | yes | `"{source}:{stable local id}"`. The local part must survive a re-publish — adapters derive it from a file name or another immutable property, never from row order. It is what lets a quarterly refresh diff instead of replace, and what the OSM cross-link matcher stores matches against. |
| `source` | yes | Registry key. Lowercase snake_case. |
| `license` | yes | A license id from the registry. Stamped by `Adapter.feature()`. |
| `attribution` | yes | The publisher's verbatim credit line, templates already resolved. |
| `feature_class` | yes | `route`, `segment` or `spot`. |
| `geometry` | yes | `LineString` or `MultiLineString` for routes and segments, `Point` for spots; WGS84 lon/lat, 2D. Validation rejects out-of-range coordinates, which is how an unreprojected source gets caught. |
| `kind` | defaulted | `hiking`, `cycling`, `mtb`, `ski`, `horse`, `paddle`, `running`, `mixed`, `other`. |
| `ref` | no | Waymarking code: `GR 11`, `PR-G 100`, `EV15`. |
| `name` | no | |
| `parent_id`, `parent_name`, `stage_no` | no | Stage grouping. A Camino variant has ~30 stages; each is its own feature pointing at a shared parent id. The parent is a grouping key, not a second copy of the geometry. |
| `official_status` | no | Source-specific: `homologado`, `Great Walk`, `camino_natural`, `eurovelo_developed`. |
| `category` | spots only | What the point is, for the icon: `hut`, `shelter`, `gite`, `bivouac`, `water`, `summit`, `pass`, `lake`, `other`. Lines never carry it. |
| `source_url` | no | Where a user can see the publisher's own page. Catalog only, never tiled. |
| `country`, `admin` | no | ISO 3166-1 alpha-2, and the publisher's own region name. |
| `extras` | no | Everything the source said that has no column here. Catalog only, never tiled. |

## What is deliberately absent

**Ascent, descent, and elevation profiles.** They are computed at pack-bake time
by `enrich_routes.py` against that pack's own DEM, exactly as OSM routes get
today. Storing them here would multiply the master database several times over
and bind it to one elevation model. `tests/test_schema.py` asserts these fields
never appear.

## What reaches the tiles

Only `TILE_ATTRIBUTES`: `id`, `source`, `license`, `kind`, `ref`, `name`,
`official_status`, `parent_id`, `stage_no`, and for spots `category`.

`attribution` is per-source and comes from the registry at render time.
`extras` is unbounded. `source_url` is a catalog concern. Keeping this list
short is what keeps the measured 1.56 KB/km and 3.5 KB/km coefficients — and so
the per-pack growth numbers — honest.

## The catalog

`catalog.sqlite` holds the same metadata *without* geometry, plus each feature's
length, point count and bounding box. That split is why the catalog is a
80–150 MB artifact rather than a second copy of the master database, and it
makes two things cheap: the per-pack bbox cut, and offline route search later
(an FTS index over names and refs is already wired up where SQLite has FTS5).
