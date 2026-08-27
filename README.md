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
| Source registry for all 20 planned sources | 13 of 17 adapters (each tagged with the wave it lands in) |
| Normalized schema, master-database format, SQLite catalog | Cross-link matcher against OSM relations |
| Polite/resumable/revalidating fetch layer | Shapefile + file-geodatabase readers (the `geo` extra) |
| CNIG ×3, NZ DOC ×2, EuroVelo, USFS | App-side overlay toggle, source badges, licenses screen |
| Per-pack bbox export with tippecanoe settings | Quarterly refresh automation in CI |
| Size model, validated against real pulled data | Legal verification of 17 of 20 sources |

### Data actually pulled

| source | features | km | notes |
| --- | ---: | ---: | --- |
| EuroVelo (developed) | 1,337 | 55,409 | all 17 corridors; plan estimated 60,000 km |
| NZ DOC routes | 1,547 | 13,696 | walking, tramping and mountain bike experiences |
| NZ DOC network | 3,248 | 13,895 | EAM asset network; overlaps the above almost entirely |
| CNIG Camino de Santiago | — | — | 2,221 files listed, pull running |
| USFS National Forest System Trails | 86,303 | — | pull running; official figure 160,000 miles |

Four sources are legally verified (NZ DOC ×2, EuroVelo, USFS). The rest are
refused by `trailsdb export` until a human confirms their terms — see
[Legal architecture](#legal-architecture).

### What contact with the real services changed

The plan's size estimates held up. Its assumptions about *access* mostly did not:

- **CNIG needs no account.** Direct download is unauthenticated, and half of
  every listing is KML duplicates of the GPX, so the pull is about 4,700 files
  rather than the ~9,900 budgeted.
- **EuroVelo is ODbL and share-alike**, not the bespoke ECF terms assumed. That
  makes it a second share-alike source alongside refuges.info, and a far bigger
  one — it now takes its own tile layer automatically.
- **USFS needs no GDAL.** The plan reserved geodatabase parsing for its 118 MB
  `.gdb`; the same data comes back as GeoJSON from the EDW REST service.
- **DOC's 2026 drift is real.** Its datasets are flagged deprecated with no
  replacement published; the adapter re-reads that notice on every pull.
- **Caminos Naturales isn't a CNIG dataset at all.** It's published separately
  by the Ministerio de Agricultura.

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

`trailsdb estimate` is not a guess. Its coefficients were measured on the live
Galicia pack pipeline: 550 routes / 11,176 km / 51.5 points per km → 17.5 MB of
enriched GeoJSONL (**1.56 KB/km**) → 39.1 MB of z8–14 PMTiles (**3.5 KB/km**),
2.0 % of the 1.96 GB pack.

Applied to ~810,000 km of official trails worldwide: **~1.2 GB** of master
database, **~2.7 GB** of tiles spread across *all* packs combined. A typical
pack grows a few percent, because routes are vector lines and packs are
dominated by elevation rasters.

**The coefficient survived contact with real data.** NZ DOC's two layers
measure 1.61 KB/km uncompressed against the 1.56 baseline, and the spread
between them tracks point density exactly as the geometry-dominated reasoning
predicts: the sparse experience layer (10.1 points/km) costs 0.48 KB/km, the
dense asset network (68.3 points/km) costs 2.72, and the blend lands on the
baseline. Stored gzipped, as the pipeline does, it is about a third of that
again.

The worst cases (Alps ≈ +12 %, a US mountain-state pack ≈ +10 %) are dominated
by network segments. Two levers, both implemented: segments stop at z13 rather
than z14, which roughly halves their tile cost and pulls the worst case under
~7 %, and segments carry no profile attributes at all.

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
