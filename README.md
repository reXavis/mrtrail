# mrtrail — the world trail database

A pipeline that joins every legally usable **official** trail database into one
normalized dataset, cuts it per region pack, and bakes it into the packs'
vector tiles alongside the existing OpenStreetMap routes layer.

OSM is already a world trail database, and mrspot already ships it. What nobody
ships is the joined *official* layer — homologated GRs, national-park systems,
DOC Great Walks, federal trail inventories — offline, DEM-enriched, and
cross-linked to the community layer. That is what this builds.

## Where this is

This repository is the phase-1 scaffold plus the first three adapters. What
exists and what does not is not a matter of reading the code — ask it:

```bash
trailsdb status
```

| built | not yet |
| --- | --- |
| Source registry for 23 sources | 4 without an adapter: refuges.info (points, needs schema support), Spain regional, NSW/QLD/WA (no open endpoint), USGS NDT (no endpoint) |
| Normalized schema, master-database format, SQLite catalog | Cross-link matcher against OSM relations |
| Polite fetch layer; stdlib readers for ArcGIS, WFS (JSON and GML), GeoPackage/WKB, GPX; LV95 and transverse-Mercator transforms | Shapefile + file-geodatabase readers (the `geo` extra) |
| CNIG ×4 (FEDME, Camino, Camino del Cid, Caminos Naturales), NZ DOC ×2, EuroVelo, USFS, NPS, Ontario, England, swisstopo, Norway, Sweden, BC, Australia ×3 (SA, VIC, TAS), Geotrek fleet (ten operators pulled, all ten closed on their own terms) | App-side overlay toggle, source badges, licenses screen |
| Per-pack bbox export with tippecanoe settings | Quarterly refresh automation in CI |
| Size model, validated against real pulled data and five tippecanoe bakes; 19 of 23 sources legally verified from their publishers' own text | An open Geotrek operator (all ten read so far refuse or say nothing) |

### Data actually pulled

**Every volume estimate the plan made landed close.** Figures are measured
from the normalized data.

| source | features | km | plan estimated |
| --- | ---: | ---: | ---: |
| USFS National Forest System Trails | 80,966 | 232,766 | 257,000 |
| swisstopo Wanderwege | 409,276 | 66,926 | 65,000 |
| EuroVelo (developed sections) | 1,337 | 55,409 | 60,000 |
| Ontario Trail Network | 6,991 | 45,544 | 35,000 (with BC) |
| NPS park trails | 31,156 | 27,820 | 40,000 |
| CNIG Camino de Santiago | 1,074 | 24,854 | 25,000 |
| BC recreation trail reserves (active) | 9,886 | 20,198 | — |
| NZ DOC network | 3,248 | 13,896 | 14,000 |
| NZ DOC routes | 1,547 | 13,696 | — |
| England: National Trails + Coast Path | 16,457 | 7,604 | 5,000 |
| France: Geotrek, 10 of ~80 operators — pulled, then dropped: every operator's terms are closed | 3,321 | 56,301 | 65,000 (all of France) |
| Sweden: Naturvårdsverket leder | 12,013 | 17,652 | 15,000 |
| South Australia: DEW Recreation Trails | 7,113 | 9,461 | 30,000 (all states) |
| Tasmania: the LIST tracks | 12,057 | 4,181 | — |
| Victoria: DEECA Recreation Tracks (walkable) | 292 | 1,139 | — |
| refuges.info huts, shelters and water points | 8,467 spots | — | — |
| Norway: Kartverket Turrutebasen | 166,434 | 83,824 | 80,000 |
| CNIG FEDME senderos | 3,780 | 49,780 | 50,000 |
| Caminos Naturales (MAPA, via CNIG) | 741 | 10,915 | 10,300 |
| CNIG Camino del Cid (walking, MTB, road-bike stages) | 135 | 5,095 | 2,500 |

The Camino pulled 1,074 stages across 80 route variants in three countries.
USFS dropped 5,337 of 86,303 raw features, all null-geometry attribute rows;
NPS drops trails it marks Proposed or Abandoned; the Coast Path drops sections
not yet opened.

A Galicia cut — the pack that ships today — bakes to **2.4 MB of PMTiles,
+0.12 % of the pack**, against the plan's predicted ~1 %: 263 FEDME, Camino
and Caminos Naturales routes over 3,975 km in the `official` layer, plus
EuroVelo, all legally verified and exported without any override. Seven
packs have been baked with tippecanoe; see [The size model](#the-size-model).

**Nineteen of 23 sources are legally verified** from their publishers' own
text: USFS, NPS, swisstopo, EuroVelo, Ontario, BC, England, Sweden, NZ DOC ×2,
Norway, the four CNIG series, South Australia, Victoria, Tasmania and
refuges.info (CC BY-SA 2.0, not the 4.0 the plan assumed; points, so its
adapter waits on point support). The CNIG ones carry the credit the IGN
licence prescribes and the SCNE product table spells out per product —
`Obra derivada de FEDME 2020-2026 CC-BY 4.0 FEDME`, not the `ign.es` the plan
assumed — and Norway's is the verbatim `© Kartverket`. Geotrek's ten operators
stay refused by `trailsdb export` until each one's terms are read; see
[Legal architecture](#legal-architecture).

### What contact with the real services changed

The plan's size estimates held up. Its assumptions about *access* mostly did not:

- **CNIG needs no account.** Direct download is unauthenticated, and half of
  every listing is KML duplicates of the GPX, so the pull is about 4,700 files
  rather than the ~9,900 budgeted.
- **EuroVelo is ODbL and share-alike**, not the bespoke ECF terms assumed. That
  makes it a second share-alike source alongside refuges.info, and a far bigger
  one — it now takes its own tile layer automatically.
- **Neither USFS nor swisstopo needs GDAL.** The plan reserved geodatabase
  parsing for USFS's 118 MB `.gdb`; the same data comes back as GeoJSON from
  the EDW REST service. swisstopo ships a GeoPackage — SQLite with WKB inside,
  in LV95 — and the stdlib reads it, with swisstopo's own published conversion
  formulas (accurate to ~1 m) doing the reprojection.
- **DOC's 2026 drift is real.** Its datasets are flagged deprecated with no
  replacement published; the adapter re-reads that notice on every pull.
- **Caminos Naturales is a MAPA dataset that CNIG redistributes.** The
  ministry's own download is reCAPTCHA-gated, which an unattended pipeline
  must not pass; the CNIG download centre carries the same 1,483 files as
  series RTCNT, served from S3 through a pre-signed URL, and that is the
  flow the adapter speaks. The Camino del Cid had quietly moved to S3 too:
  all 147 of its "GPX" downloads were HTML pages until the adapter learned
  to notice a page where a file should be and take the S3 route instead.
- **Half of BC's recreation lines are retired** tenure records, not trails, and
  are dropped. Sweden's 12,013 rows are pieces of 3,657 trails; Norway's WFS
  speaks only GML, which is why there is a GML reader. USGS's National Digital
  Trails has no public endpoint anywhere it was looked for.
- **Geotrek is a fleet, not a source, and none of it is open.** ~80 operators
  run it; ten answered with working APIs and were pulled as one source with
  per-instance attribution. The API carries no licence, so each operator's
  own legal page was read through its API: three reserve all rights in writing
  (Écrins: "tous les droits de reproduction sont réservés"), seven say nothing
  about the data. Silence is not permission, so all ten are marked closed with
  the quote and date on each, and their 56,301 km left the master database.
  The way back in is an open publication by the same operator: data.gouv.fr
  carries the Gard's PDIPR trails "(source Géotrek)" under the Licence Ouverte
  2.0, which is a different distribution to read and record before that
  instance reopens.
- **Australia is three states, not six.** South Australia, Victoria and
  Tasmania publish official tracks on government endpoints under CC BY (the
  LIST even prescribes its credit wording); NSW's walking-tracks service wants
  a token, Western Australia's long trails are CC BY-NC, and Queensland's
  QPWS tracks are not on its portals. Victoria's 5,065 km shrink to 1,139
  once the four-wheel-drive tours are left out.
- **The plan's attribution strings were wrong for every Spanish series.**
  The IGN licence takes the producer from a published product table; FEDME,
  the Camino federations, the Camino del Cid consortium and MAPA are each
  credited by name, and the download centre's own product pages say the same.

## Install

```bash
pip install -e .
```

Python 3.10+. Runtime dependencies are `requests` and `PyYAML`; everything
geometric is stdlib maths on GeoJSON coordinate arrays, so no GDAL or shapely is
needed to run the pipeline. Sources that publish shapefiles or file
geodatabases need the optional extra:

```bash
pip install -e '.[geo]'
```

## Use

```bash
trailsdb status                          # what is built, pulled, normalized, verified
trailsdb health                          # are all 18 source endpoints still there
trailsdb pull cnig --limit 5             # download (an adapter name expands to its sources)
trailsdb normalize cnig_fedme            # raw tier -> master database + catalog
trailsdb export --pack galicia           # cut a pack, ready for tippecanoe
trailsdb licenses -o licenses.json       # the app's data-sources screen payload
trailsdb estimate                        # the size model over the registry
trailsdb search "Camino Frances"         # look through the catalog
```

Source selection is the same everywhere: source ids (`cnig_fedme`), adapter
names (`cnig`, expanding to all three series), or nothing for every source.

Data lives under `./data` by default; set `TRAILSDB_DATA` or pass `--data DIR`.

```
data/
  raw/{source}/          downloads exactly as published, plus _pull.json
  normalized/{source}.geojsonl.gz    the master database, one file per source
  catalog.sqlite         metadata, provenance, bounding boxes — no geometry
  exports/{pack}/        per-pack layer files + attribution.json + export.json
```

`raw/` is the 8–20 GB disposable tier. `normalized/` plus `catalog.sqlite` are
the ~1.2 GB that matter.

## How it fits together

```
sources.yaml ──> registry ──> adapter.fetch  ──> raw/{source}/
                    │                              │
                    │         adapter.normalize <──┘
                    │                  │
                    └── license, ──────┤
                        attribution    ▼
                                 normalized/{source}.geojsonl.gz + catalog.sqlite
                                                │
                                    bbox cut per pack
                                                ▼
                              exports/{pack}/official.geojsonl      → tippecanoe z8–14
                                             official_net.geojsonl  → tippecanoe z8–13
```

Two feature classes, because sources come in two shapes. **Routes** are named
things a user browses — a FEDME sendero, a DOC Great Walk, one Camino stage —
and render like the existing OSM routes layer. **Segments** are network
infrastructure — swisstopo Wanderwege, USFS centerlines, the Ontario Trail
Network — and render like the ways layer.

Ascent, descent and elevation profiles are deliberately **not** in the master
database. They are computed at pack-bake time against that pack's own DEM,
exactly as OSM routes get today.

## Legal architecture

These are non-negotiables, and they are enforced in code rather than documented
and hoped for.

- **Every feature carries `source` and `license`**, stamped centrally by
  `Adapter.feature()`. An adapter cannot forget to attach provenance because it
  never constructs a `Feature` itself.
- **Every source carries a verbatim attribution string** in `sources.yaml`,
  reproduced as the publisher prescribes it. Templated ones resolve at pull
  time: EuroVelo's ECF notice must carry the retrieval date, so it comes from
  the pull manifest, not from whenever a pack is baked.
- **Nothing unverified ships.** `legal.verified_on` stays null until a human has
  read the publisher's terms and confirmed both the license id and the exact
  attribution wording. `trailsdb export` refuses unverified sources unless you
  pass `--allow-unverified` — which is for development, not for a release.
- **Share-alike sources get their own layer.** refuges.info is CC BY-SA;
  merging it into a shared layer would drag the whole layer under those terms.
- **No geometric merging with OSM, ever.** Separate source-layers in one
  pmtiles file is a collective database and is fine. Merging geometries across
  licenses is a derivative database and is not. The two layers get cross-linked
  by ref/name/proximity instead, so the info sheet can say "also in OSM as
  GR 53" without creating a derivative.
- **US data comes from government endpoints only.** Esri-hosted mirrors attach
  Esri's terms, which the public-domain status of the underlying work does not
  override.
- **The app's licenses screen renders from the registry** (`trailsdb licenses`),
  so adding a country is a registry entry and a rebuild, never new legal UI work.

## The size model

`trailsdb estimate` is not a guess. The plan's coefficients came from the
Galicia pack pipeline: 550 routes / 11,176 km / 51.5 points per km → 17.5 MB of
enriched GeoJSONL (**1.56 KB/km**) → 39.1 MB of z8–14 PMTiles (**3.5 KB/km**),
2.0 % of the 1.96 GB pack. Both have since been re-measured on the real data
with `trailsdb bake`, which runs tippecanoe over an export and reports bytes per
km — and per feature — per layer:

| pack | layer | class · zooms | features | km | PMTiles | KB/km | KB/feature |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| Alps | official_net (swisstopo) | segment · z8–13 | 409,276 | 66,926 | 105.5 MB | 1.61 | 0.26 |
| Southern Norway | official (Turrutebasen, as routes) | route · z8–14 | 143,703 | 68,940 | 88.2 MB | 1.31 | 0.63 |
| Southern Norway | official_net (Turrutebasen, as segments) | segment · z8–13 | 141,833 | 66,592 | 59.1 MB | 0.91 | 0.43 |
| Colorado | official_net (USFS + NPS) | segment · z8–13 | 7,358 | 22,987 | 7.9 MB | 0.35 | 1.10 |
| New Zealand | official_net | segment · z8–13 | 3,230 | 13,819 | 5.0 MB | 0.37 | 1.58 |
| New Zealand | official | route · z8–14 | 1,543 | 13,687 | 5.0 MB | 0.37 | 3.31 |
| Pyrenees | official (FEDME + Camino) | route · z8–14 | 589 | 8,962 | 4.3 MB | 0.50 | 7.5 |
| Galicia | official (FEDME + Camino + Caminos Naturales) | route · z8–14 | 263 | 3,975 | 2.0 MB | 0.51 | 7.8 |
| Alps / Pyrenees / Galicia | eurovelo | route · z8–14 | 176 / 19 / 17 | 6,960 / 767 / 904 | 3.0 / 0.3 / 0.4 MB | 0.43–0.45 | 14–24 |
| Alps / Pyrenees | refuges_info | spot · z8–14 | 5,346 / 1,112 | — | 1.7 / 0.4 MB | — | 0.33 / 0.37 |

Two things fall out. First, everything costs **a third to a tenth of the 3.5
KB/km the plan carried**, because tippecanoe's simplification and
`--drop-densest-as-needed` do most of their work below z12 and the export only
carries the ten tile attributes. Second, **the per-km coefficient is the wrong
shape**: Norway's route pieces cost 1.31 KB/km and a FEDME sendero 0.5, on the
same settings, because a tile carries a header per feature and Norway has one
feature every half kilometre. Fitting every bake gives two terms:

    z8–14 routes    KB ≈ 0.4 × km + 0.45 × features
    z8–13 segments  KB ≈ 0.3 × km + 0.22 × features
    spots           KB ≈ 0.4 × features

which reproduces the two layers that dominate any pack (Norway 88 MB, the
Swiss network 106 MB) within 3 % and the sparse ones within about 20 %.
`estimate` uses the two-term form whenever a feature count is known — every
layer export, every normalized source — and the per-km fallback (0.4 KB/km
for routes, 1.2 for segments, the Swiss density) for sources not yet pulled.
The per-feature term is also why Turrutebasen is a *segment* source despite
carrying route names: its pieces are network-shaped, and stopping them at z13
takes southern Norway from 88 MB to 59 MB with the name and parent intact.

Applied worldwide: **~1.5 GB** of master database and **~0.5 GB** of tiles
across *all* packs combined. A typical pack grows well under one percent,
because routes are vector lines and packs are dominated by elevation rasters.
The Alps pack is the honest worst case: the Swiss network, EuroVelo and 5,346
huts come to **110 MB, 3.5 % of its 3.15 GB**, and Norway's network would be
about the same again for a pack that covered it.

**The master coefficient survived contact with real data, and the reasoning
behind it held up better than the number.** Measured across 315,767 km of
pulled official data, the master database runs at 1.98 KB/km uncompressed
against the 1.56 baseline. The gap is point density, not a flaw: EuroVelo at
10.5 points/km costs 0.24 KB/km, USFS at 87.0 costs 2.50, and geometry is 87 %
of every file exactly as the plan argues. Spots cost 0.52 KB each.

That last fact is also the biggest lever available. Sources hand back full IEEE
doubles — about 39 bytes per position to store 17 significant digits of a
measurement good to a few metres. Rounding to six decimal places (~11 cm, finer
than consumer GPS and finer than a z14 tile resolves) **cut the master database
by 57 %**, from 322 MB to 140 MB, with nothing lost that a map can draw.

Segments carry no profile attributes at all, and every segment bake above ran
z8–13, so `--cap-segments-at-z13` is accepted for compatibility and changes
nothing.

Once a source has actually been normalized, `estimate` uses its measured length
and feature count from the catalog instead of the registry's estimate, and
labels the row `measured`.

## Adding a source

1. Add an entry to `trailsdb/sources.yaml` — license, verbatim attribution,
   cadence, health-check URL, feature class, estimated km.
2. Write an adapter with two methods: `fetch` (bytes onto disk, no
   transformation) and `normalize` (bytes → features, no network). The split is
   what makes a bad normalizer a re-run rather than a re-download.
3. Register it in `trailsdb/adapters/__init__.py`.
4. Confirm the license and attribution against the publisher, then set
   `legal.verified_on`.

`tests/test_registry.py` checks the shipped registry itself: every adapter is
implemented or scheduled, every source has a health check, every license
resolves.

## Testing

```bash
python -m unittest discover -s tests -t .
```

138 tests, no network. Every HTTP interaction goes through an injected
transport, and the pacing clock is advanced by hand rather than slept through.

## Docs

- [`docs/plan.md`](docs/plan.md) — the build plan this implements, and where
  each phase stands.
- [`docs/schema.md`](docs/schema.md) — the normalized schema, field by field.
