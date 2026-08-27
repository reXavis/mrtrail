# The build plan, and where it stands

This repository implements the World Trail Database Plan. This page tracks each
phase against what is actually in the tree; `trailsdb status` reports the same
thing from the code rather than from prose.

## Verdict the plan reached

Feasible, comfortably. Roughly 2–3.5 GB of tiles across *all* packs combined; a
typical pack grows a few percent. The first full pull is 1–2 days of automated
wall-clock. The real cost is the adapters: roughly 4–6 focused weeks for ~15
sources.

## Execution order

### 1. Scaffold — **done**

`trailsdb/` with the `sources.yaml` registry (license, verbatim attribution,
cadence, health-check URL, feature class and estimated km for all 18 sources),
the normalized schema, the pull/normalize pipeline, and the SQLite catalog.

Also built beyond the phase's brief, because the later phases would each have
needed them: a polite/resumable/revalidating fetch layer, the per-pack bbox
export with tippecanoe settings, and `trailsdb estimate` reproducing the plan's
size model from the registry.

### 2. Prove it on three continents — **partly done**

| adapter | state |
| --- | --- |
| CNIG trio (FEDME · Camino · Caminos Naturales) | built — normalization tested end to end; the live discovery flow is marked UNVERIFIED and needs confirming against the download centre |
| NZ DOC | built — paginated ArcGIS pull, category→kind mapping, point assets skipped |
| USFS | not started — needs the `geo` extra for file geodatabases |

EuroVelo was pulled forward from phase 4 because it is the only source with a
dated attribution notice, and building it early is what forced the attribution
machinery to be right rather than merely present.

Not done: baking the Galicia pack with the `official` layer, and a small NZ or
Colorado proof pack.

### 3. App integration — **not started**

`style_builder.dart` official-layer styling, the `OverlayOptions` toggle, the
route info sheet source badge, the licenses screen. The pipeline side of that
last one is ready: `trailsdb licenses` emits the payload the screen renders.

### 4. Europe wave — **1 of 7**

EuroVelo built. Geotrek, swisstopo, UK, Norway, Sweden and refuges.info are
registered with licenses and health checks, and tagged with this phase.

Geotrek's per-instance licensing is already modelled: the registry marks its
license `resolved_at_ingest`, and an instance that declares no attribution
raises rather than shipping a blank credit line.

### 5. Americas & Oceania wave — **not started**

USGS/NPS dedup, Ontario/BC, Australian states. All registered and tagged.

### 6. Cross-links & refresh automation — **not started**

The ref/name/proximity matcher against OSM relations, quarterly re-pull
automation, source health checks in CI. The pieces those depend on exist: pull
manifests record per-file hashes and ETags so a re-pull only re-downloads what
changed, and `trailsdb health` checks all 18 endpoints.

## The first concrete step

The plan named the CNIG adapter: the fiddliest pull (POST flow, free account,
~9,900 files) and the one that makes the feature visible in the live Galicia
pack immediately.

It is built, and its normalization is tested against GPX, zipped GPX,
multi-track files, ref extraction and Camino stage grouping. What is *not*
settled is discovery — turning a series into a list of downloadable files —
because that depends on the download centre's exact request shape. So it has two
paths: a committed `files.json` index (deterministic, offline, what the tests
use), and a live query whose endpoint constants are marked UNVERIFIED in
`trailsdb/adapters/cnig.py`. Discovery returning nothing is a hard failure, not
an empty series, so drift surfaces immediately.

## Risks, and what is in place for each

| risk | mitigation | state |
| --- | --- | --- |
| Source drift — endpoints move (DOC announced 2026 URL changes) | Per-source health check; adapters fail loudly rather than yielding a quietly smaller dataset | built (`trailsdb health`); CI wiring not done |
| Attribute heterogeneity — 15 schemas into one | Deliberately small normalized schema; everything else into per-source `extras`, kept out of tiles | built |
| Double rendering — official routes often also exist in OSM | Separate layers (not a legal problem), distinct styling, cross-link matcher for a merged info card | not started (phase 6) |
| Coverage honesty — Germany, Portugal, South America, Asia have no official datasets | OSM stays primary everywhere; official is enrichment where states publish | by design |
| Km estimates — the assumed rows could each be ±50 % | `trailsdb estimate` switches to measured lengths as sources land; the conclusion survives the 1M-km upper bound either way | built |

## Before any of this ships

Every one of the 18 sources has `legal.verified_on: null`, and `trailsdb export`
refuses them all by default. Clearing that means, per source: read the
publisher's terms, confirm the license id, confirm the exact attribution
wording, then set the date. It is the one part of this that cannot be automated,
and the export gate is there so it cannot be skipped by accident either.
