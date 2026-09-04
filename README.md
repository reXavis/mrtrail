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
| Source registry for all 20 planned sources | 4 sources without an adapter (Australia, Spain regional, refuges.info; USGS NDT has no endpoint) |
| Normalized schema, master-database format, SQLite catalog | Cross-link matcher against OSM relations |
| Polite fetch layer; stdlib readers for ArcGIS, WFS (JSON and GML), GeoPackage/WKB, GPX; LV95 and transverse-Mercator transforms | Shapefile + file-geodatabase readers (the `geo` extra) |
| CNIG ×4 (FEDME, Camino, Camino del Cid, Caminos Naturales), NZ DOC ×2, EuroVelo, USFS, NPS, Ontario, England, swisstopo, Norway, Sweden, BC, Geotrek ×10 | App-side overlay toggle, source badges, licenses screen |
| Per-pack bbox export with tippecanoe settings | Quarterly refresh automation in CI |
| Size model, validated against real pulled data and five tippecanoe bakes | Legal verification of 5 of 20 sources (Geotrek's ten operators, refuges.info, USGS, Australia, Spain regional) |

### Data actually pulled

**Nine sources, 12 layers, and every volume estimate the plan made landed
close.** Figures are measured from the normalized data.

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
| France: Geotrek, 10 of ~80 operators | 3,321 | 56,301 | 65,000 (all of France) |
| Sweden: Naturvårdsverket leder | 12,013 | 17,652 | 15,000 |
| Norway: Kartverket Turrutebasen | — | — | 80,000 (pull running) |
| CNIG FEDME senderos | — | — | 50,000 (pull running) |

The Camino pulled 1,074 stages across 80 route variants in three countries.
USFS dropped 5,337 of 86,303 raw features, all null-geometry attribute rows;
NPS drops trails it marks Proposed or Abandoned; the Coast Path drops sections
not yet opened.

A Galicia cut — the pack that ships today — bakes to **1.6 MB of PMTiles,
+0.08 % of the pack**, against the plan's predicted ~1 %, before FEDME. Five
packs have been baked with tippecanoe; see [The size model](#the-size-model).

**Fifteen sources are legally verified** from their publishers' own text:
USFS, NPS, swisstopo, EuroVelo, Ontario, BC, England, Sweden, NZ DOC ×2,
Norway, and the four CNIG series. The CNIG ones carry the credit the IGN
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
  flow the adapter speaks.
- **Half of BC's recreation lines are retired** tenure records, not trails, and
  are dropped. Sweden's 12,013 rows are pieces of 3,657 trails; Norway's WFS
  speaks only GML, which is why there is a GML reader. USGS's National Digital
  Trails has no public endpoint anywhere it was looked for.
- **Geotrek is a fleet, not a source.** ~80 operators run it; ten answered with
  working APIs and are pulled as one source with per-instance attribution. The
  API carries no licence and the one operator legal page read so far is silent
  on the data, so no instance ships until its terms are read.
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
2.0 % of the 1.96 GB pack. Both coefficients have since been re-measured on the
real data with `trailsdb bake`, which runs tippecanoe over an export and reports
bytes per km per layer:

| pack | layer | class | features | km | PMTiles | KB/km |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| Switzerland | official_net | segment | 409,276 | 66,926 | 110.6 MB | 1.61 |
| Colorado | official_net | segment | 7,358 | 22,987 | 8.3 MB | 0.35 |
| New Zealand | official_net | segment | 3,230 | 13,819 | 5.2 MB | 0.37 |
| New Zealand | official | route | 1,543 | 13,687 | 5.2 MB | 0.37 |
| Pyrenees | official | route | 1,281 | 20,851 | 6.6 MB | 0.31 |
| Galicia | official | route | 119 | 2,386 | 1.2 MB | 0.50 |
| Switzerland / Pyrenees / Galicia | eurovelo | route | 65 / 19 / 17 | 2,176 / 767 / 904 | 1.0 / 0.4 / 0.4 MB | 0.43–0.45 |

Named routes cost **0.3–0.5 KB/km** of tiles and network segments
**0.35–1.6 KB/km** — a third to a tenth of the 3.5 the plan carried, because
tippecanoe's simplification and `--drop-densest-as-needed` do most of their
work below z12, and because the export only carries the nine tile attributes.
The model now uses one coefficient per feature class, **0.4 KB/km for routes
and 1.2 KB/km for segments**, both set just above the measurements' middle.
Switzerland is the honest worst case: 409,276 short segments averaging 164 m
each, so every tile at z13 carries far more feature headers per km than a
long named route does.

Applied to ~800,000 km of official trails worldwide: **~1.5 GB** of master
database, **~0.7 GB** of tiles spread across *all* packs combined. A typical
pack grows well under one percent, because routes are vector lines and packs
are dominated by elevation rasters; the 110 MB Swiss network is the outlier,
and it is 3.5 % of the 3.15 GB Alps pack.

**The coefficient survived contact with real data, and the reasoning behind it
held up better than the number.** Measured across 315,767 km of pulled official
data, the master database runs at 1.98 KB/km uncompressed against the 1.56
baseline. The gap is point density, not a flaw: EuroVelo at 10.5 points/km costs
0.24 KB/km, USFS at 87.0 costs 2.50, and geometry is 87 % of every file exactly
as the plan argues.

That last fact is also the biggest lever available. Sources hand back full IEEE
doubles — about 39 bytes per position to store 17 significant digits of a
measurement good to a few metres. Rounding to six decimal places (~11 cm, finer
than consumer GPS and finer than a z14 tile resolves) **cut the master database
by 57 %**, from 322 MB to 140 MB, with nothing lost that a map can draw.

The worst cases are dominated by network segments, and two levers keep them
small: segments stop at z13 rather than z14, and they carry no profile
attributes at all. Both are already inside the measured segment coefficient
(every segment bake above ran z8–13), so `--cap-segments-at-z13` is accepted
for compatibility and changes nothing.

Once a source has actually been normalized, `estimate` uses its measured length
from the catalog instead of the registry's estimate, and labels the row
`measured`.

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
