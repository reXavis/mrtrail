# Handing the official layers to the app

This is the contract between `trailsdb` (this repository) and the mrspot pack
pipeline. Everything the app needs to bake and show the official trail layers is
in `exports/`, and everything here can be regenerated with three commands.

## What is in `exports/`

```
exports/
  licenses.json              the "Data sources & licenses" screen, all 23 sources
  galicia/                   the pack that ships today
    official.geojsonl        263 routes, 3,975 km  (FEDME, Camino, Caminos Naturales)
    eurovelo.geojsonl        17 routes, 904 km     (ODbL — its own layer)
    refuges_info.geojsonl    1 hut                 (CC BY-SA 2.0 — its own layer)
    export.json              per layer: feature class, counts, km, sources, tippecanoe args
    attribution.json         the credit line to show for each source in this pack
  pyrenees/                  the showcase pack: 589 routes, 1,112 huts, EuroVelo
```

Every `.geojsonl` line is one tile-shaped feature: a `LineString`,
`MultiLineString` or (spots) `Point` in WGS84 rounded to six decimals, and
**only** these properties — `id`, `source`, `license`, `kind`, `ref`, `name`,
`official_status`, `parent_id`, `stage_no`, `category`. Nothing else may reach
the tiles; the export already writes nothing else and the tippecanoe args in
`export.json` whitelist the same names.

Packs not in `exports/` (the Alps cut is 400 MB of GeoJSONL) are regenerated,
see below.

## Baking

One source-layer per file, into the pack's existing `topo.pmtiles`, next to the
OSM `routes` layer, with the arguments recorded in `export.json`:

| layer | class | zooms | what it holds | licence family |
| --- | --- | --- | --- | --- |
| `official` | route | z8–14 | named official routes: FEDME senderos, Camino stages, DOC tracks… | attribution |
| `official_net` | segment | z8–13 | network pieces: swisstopo, USFS, Norway, Ontario, Tasmania… | attribution |
| `eurovelo` | route | z8–14 | EuroVelo, alone because it is ODbL (share-alike) | share-alike |
| `refuges_info` | spot | z8–14 | huts, shelters, water points, alone because it is CC BY-SA 2.0 | share-alike |

The arguments are `--layer=<name> --minimum-zoom=8 --maximum-zoom=<13|14>
--drop-densest-as-needed --no-tile-size-limit --include=<attr>…`. Run
tippecanoe once per layer and `tile-join` them into the pack, or pass each file
with `-L name:file` in one run; either way the layers stay separate.

Ascent, descent and profiles are not in the data on purpose. Compute them at
bake time against the pack's DEM with `enrich_routes.py`, exactly as the OSM
routes get them; `official` and `eurovelo` features are lines like any route.

## Three rules the app must keep

1. **Never merge these layers with the OSM `routes` layer or with each other.**
   Separate source-layers in one PMTiles file are a collective database and
   fine; merged geometry across licences is a derivative database and is not.
   Cross-link instead (phase 6): match by `ref`, name and proximity so the info
   sheet can say "also in OSM as GR 53".
2. **Show the credit lines.** `attribution.json` carries, per source in the
   pack, the exact string the publisher prescribes. The CNIG lines ("Obra
   derivada de FEDME 2020-2026 CC-BY 4.0 FEDME" and the like) must be visible
   with the map or in its credits screen; EuroVelo's line carries the date its
   data was retrieved and must not be rewritten; Kartverket asks for a link to
   kartverket.no where possible (`homepage` in `licenses.json`). Render the
   licences screen from `licenses.json`; a source's `attribution_resolved` is
   the string to show, and null means it has not been pulled.
3. **Packs stay DRM-free** (ODbL §4.7).

## Reading the attributes

- `id` is `source:local_id` and is stable across refreshes; store cross-links
  and favourites against it.
- `parent_id` groups stages of one route (a Camino variant, a Camino Natural,
  a Norwegian route number); `stage_no` orders them. The parent has no
  geometry of its own.
- `kind` is the activity (`hiking`, `cycling`, `mtb`, `ski`, `horse`,
  `paddle`, `mixed`, `other`); `official_status` is the publisher's own
  status word (`homologado`, `camino_natural`, `nps_park_trail`…).
- `category` exists only on spots: `hut`, `shelter`, `gite`, `bivouac`,
  `water`, `summit`, `pass`, `lake`, `other`. Pick the icon from it.

`docs/schema.md` has the full field reference.

## Regenerating, and adding packs

```bash
pip install -e .
trailsdb pull <sources>          # a day for everything; resumable with --resume
trailsdb normalize <sources>     # master database + catalog
trailsdb export --pack galicia   # exports/<pack>/ inputs; add packs in trailsdb/packs.yaml
trailsdb export --pack alps --bbox=5,43.5,16.5,48   # or an ad-hoc box
trailsdb bake --pack galicia     # optional: tippecanoe per layer, to measure
trailsdb licenses -o exports/licenses.json
```

`export` refuses any source whose licence has not been read from the
publisher's own text (`legal.verified_on` in `trailsdb/sources.yaml`); the
`--allow-unverified` override is for development only and must never feed a
release.

## What to expect on the map

- Galicia: +2.4 MB of tiles (0.12 % of the pack). The Camino and the Caminos
  Naturales are complete; FEDME holds only 23 Galician PR-G routes, so the
  regional PR-G network is still missing (it would come from the Xunta or the
  Galician federation, not yet found under a usable licence).
- Pyrenees: +5 MB; FEDME on the Spanish side, 1,112 huts. The French side is
  empty: every Geotrek operator read so far is closed.
- Alps: +110 MB (3.5 %): the whole Swiss network, EuroVelo, 5,346 huts.
- Worldwide, all packs combined: about 0.5 GB of tiles.
