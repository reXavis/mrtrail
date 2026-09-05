# Prompt for the mrspot pipeline session

Paste the block below into a Claude Code session opened on the **mrspot pipeline
repository** (the one that builds `topo.pmtiles` for the region packs).

````text
Bake the official trail layers from trailsdb into the region packs, starting with Galicia.

## Where the data is

Repository https://github.com/reXavis/mrtrail holds the trail database pipeline (`trailsdb`). Read `docs/handoff.md` and `docs/schema.md` there first; they are the contract for this job. Two ways to get the pack inputs:

- Small and ready: the `exports/` directory on the default branch has Galicia and the Pyrenees.
- Everything: the orphan branch `data-2026-09-04` has the master database, the catalog and the exports for all seven packs baked so far.
  ```
  git clone --single-branch --branch data-2026-09-04 https://github.com/reXavis/mrtrail mrtrail-data
  ./mrtrail-data/reassemble.sh ./data
  ```
  For a pack that has no export yet, `pip install -e .` the code repo and run `TRAILSDB_DATA=./data trailsdb export --pack <name> --bbox=W,S,E,N`.

An export is a directory per pack: one `<layer>.geojsonl` per layer (one tile-shaped GeoJSON feature per line, WGS84, rounded to six decimals), `export.json` (per layer: feature class, counts, km, sources and the exact tippecanoe arguments), and `attribution.json` (the credit line to show for each source in that pack). `exports/licenses.json` is the payload for the licences screen.

## What to build

1. First read how this pipeline currently builds `topo.pmtiles` and its OSM `routes` layer, and how `enrich_routes.py` adds ascent, descent and profiles from the pack DEM. Fit the new layers into that flow; do not build a second one.
2. For each layer file in a pack's export, add a source-layer to the pack's `topo.pmtiles` using the tippecanoe arguments from `export.json`, verbatim:
   - `official`      routes,   z8–14  (`--layer=official --minimum-zoom=8 --maximum-zoom=14 --drop-densest-as-needed --no-tile-size-limit --include=...`)
   - `official_net`  segments, z8–13
   - `eurovelo`      routes,   z8–14  (ODbL; must stay its own layer)
   - `refuges_info`  points,   z8–14  (CC BY-SA 2.0; must stay its own layer)
   Run tippecanoe once per layer and `tile-join` into the pack, or pass each file as a named layer in one run. The `--include` list is the whole set of attributes allowed in the tiles: `id`, `source`, `license`, `kind`, `ref`, `name`, `official_status`, `parent_id`, `stage_no`, `category`. Add nothing else.
3. Run the same DEM enrichment on `official` and `eurovelo` lines that OSM routes get. Do not enrich `official_net` segments (they carry no profile by design) or points.
4. App side (Flutter), following the existing patterns for the OSM routes layer:
   - an "Official trails" toggle in `OverlayOptions`, styling for the four layers in `style_builder.dart` that reads as distinct from OSM routes (segments quieter than routes; `refuges_info` as icons chosen by `category`: hut, shelter, gite, bivouac, water, summit, pass, lake, other);
   - a source badge and the credit line on the route info sheet, using `source` from the tile and the matching row of `attribution.json`;
   - a "Data sources & licenses" screen rendered from `licenses.json` (`attribution_resolved` is the string to show; null means not pulled). The CNIG lines beginning "Obra derivada de …" and EuroVelo's dated ODbL notice must appear exactly as given.
5. Bake Galicia, measure, and check on the device: the official layer should add about 2.4 MB to the 1.96 GB pack (263 routes, 3,975 km: FEDME senderos, Camino de Santiago stages, Caminos Naturales stages, plus EuroVelo and one hut). Then the Pyrenees (about 5 MB, 589 routes and 1,112 huts) and the Alps (about 110 MB: the whole Swiss network, EuroVelo, 5,346 huts).

## Rules that are not negotiable

- Never merge these layers with the OSM `routes` layer or with each other, and never copy geometry between them. Separate source-layers in one PMTiles file are fine; merged geometry across licences is a derivative database and is not. Cross-linking by `ref`, name and proximity for a "also in OSM as GR 53" line is fine and is a later step.
- Show the credit lines from `attribution.json`; do not paraphrase or shorten them, and do not rewrite the date in EuroVelo's notice. Kartverket asks for a link to kartverket.no where possible; `homepage` in `licenses.json` has it.
- Packs stay DRM-free.
- Use the tippecanoe arguments as given; do not raise `--maximum-zoom` for segments or add attributes to `--include`.

## Reading the attributes

- `id` is `source:local_id` and is stable across refreshes; key favourites and cross-links on it.
- `parent_id` groups the stages of one route (a Camino variant, a Camino Natural, a Norwegian route number); `stage_no` orders them; the parent has no geometry of its own, so a route sheet for a parent lists its stages.
- `kind` is the activity (`hiking`, `cycling`, `mtb`, `ski`, `horse`, `paddle`, `mixed`, `other`); `official_status` is the publisher's own word (`homologado`, `camino_natural`, `nps_park_trail`, …); `category` exists only on points.

## Known gaps, so you do not go looking

- FEDME holds only 23 Galician PR-G routes; the regional PR-G network is not published by any open source yet. Galicia's official layer is mostly the Camino and the Caminos Naturales, and that is correct.
- The French side of the Pyrenees and the Alps is empty: every Geotrek operator's terms are closed.
- Ascent/descent/profiles are absent from the data on purpose; they come from the pack DEM at bake time.

Work on a branch, keep each pack's bake reproducible from the export directory, and report the measured tile bytes per layer per pack next to the estimates above.
````
