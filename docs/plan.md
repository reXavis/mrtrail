# The build plan, and where it stands

This repository implements the World Trail Database Plan. This page tracks each
phase against what is actually in the tree; `trailsdb status` reports the same
thing from the code rather than from prose.

## Verdict the plan reached

Feasible, comfortably. Roughly 2–3.5 GB of tiles across *all* packs combined; a
typical pack grows a few percent. The first full pull is 1–2 days of automated
wall-clock. The real cost is the adapters: roughly 4–6 focused weeks for ~15
sources.

Measured since: **~0.5 GB** of tiles worldwide, not 2–3.5, because real
tippecanoe bakes come in at 0.3–0.5 KB/km for named routes and, once the
per-feature overhead is modelled, 0.3 KB/km + 0.22 KB/feature for network
segments — against the 3.5 KB/km the prototype suggested. Galicia with FEDME
and the Camino grows by 2.0 MB (0.1 %); the worst pack, the Alps with the Swiss
network and 5,346 huts, grows by 110 MB (3.5 %). The pull estimate held: the long pulls (USFS, Norway,
FEDME) each run most of a day at polite pacing. The adapter estimate was
pessimistic: 15 adapters landed in this one session, all stdlib.

## Execution order

### 1. Scaffold — **done**

`trailsdb/` with the `sources.yaml` registry (license, verbatim attribution,
cadence, health-check URL, feature class and estimated km for all 18 sources),
the normalized schema, the pull/normalize pipeline, and the SQLite catalog.

Also built beyond the phase's brief, because the later phases would each have
needed them: a polite/resumable/revalidating fetch layer, the per-pack bbox
export with tippecanoe settings, and `trailsdb estimate` reproducing the plan's
size model from the registry.

### 2. Prove it on three continents — **done**

| adapter | state |
| --- | --- |
| CNIG (FEDME · Camino · Camino del Cid · Caminos Naturales) | built against the live flow; Camino 1,074 stages / 24,854 km, FEDME 3,780 senderos / 49,780 km, Caminos Naturales 741 stages / 10,915 km, Camino del Cid 135 stages / 5,095 km — all pulled and normalized |
| NZ DOC (routes + network) | built and pulled: 4,795 features, 27,591 km |
| USFS | built and pulled from the ArcGIS GeoJSON endpoint: 80,966 features, 232,766 km — no `geo` extra needed |

EuroVelo was pulled forward from phase 4 because it is the only source with a
dated attribution notice, and building it early is what forced the attribution
machinery to be right rather than merely present. That paid off immediately: it
turned out to be ODbL and share-alike, not the bespoke licence assumed.

Real data now flows end to end — pull, normalize, catalog, bbox cut, tippecanoe
bake. A New Zealand cut bakes to 10.5 MB of tiles across both layers; the model
had said 70 MB.

Done: the Galicia pack with FEDME, the Camino and Caminos Naturales in its
`official` layer bakes at 2.4 MB of tiles, 0.12 % of the pack, from verified
sources only.

### What contact with the real services changed

The plan's estimates held up well; its assumptions about *access* did not.

| the plan said | reality |
| --- | --- |
| CNIG needs a free account | No account needed; direct download is unauthenticated |
| ~9,900 CNIG files | 9,472 listed, and half are KML duplicates of the GPX — so ~4,700 to fetch |
| Camino has 2,221 stages | Exactly 2,221 files listed |
| EuroVelo has bespoke ECF terms | ODbL, share-alike, with a verbatim dated notice |
| refuges.info is the only share-alike source | EuroVelo is a second, and a far bigger one |
| DOC endpoints change in 2026 | Correct — flagged deprecated, replacement not yet published |
| NZ DOC ≈ 14,000 km | 13,895 km, but only counting the network once |
| EuroVelo developed ≈ 60,000 km | 55,409 km |
| Caminos Naturales comes from CNIG | A MAPA dataset, CAPTCHA-gated at MAPA — but CNIG redistributes it as series RTCNT, from S3 |
| CNIG credit is `CC-BY 4.0 ign.es` | The SCNE product table names the producer per product: FEDME, FEAACS, Camino del CID, MAPA |

### 3. App integration — **not started**

`style_builder.dart` official-layer styling, the `OverlayOptions` toggle, the
route info sheet source badge, the licenses screen. The pipeline side of that
last one is ready: `trailsdb licenses` emits the payload the screen renders.

### 4. Europe wave — **5 of 7**

Built and pulled: EuroVelo (55,409 km), swisstopo (66,926 km), Naturvårdsverket
(17,652 km), England's National Trails and Coast Path (7,604 km), Geotrek across
ten of France's ~80 operators (56,301 km, since dropped: every operator's own
terms are closed or silent), and Kartverket's Turrutebasen (pull in progress,
~166k route pieces). Caminos Naturales, a Ministerio de
Agricultura dataset whose own download sits behind a reCAPTCHA this pipeline
will not bypass, turned out to be redistributed by the CNIG download centre
from S3, so it is a fourth series of the CNIG adapter (pull queued).
refuges.info and the Spanish regional networks remain.

Each of these needed a different reader, and all of them are stdlib: ArcGIS
GeoJSON paging, WFS with GML, a GeoPackage in LV95, plain GeoJSON in SWEREF99
TM. The `geo` extra (GDAL) has not been needed for any source so far.

Geotrek's per-instance licensing is modelled and exercised: the registry marks
its licence `resolved_at_ingest`, each instance's attribution is read from its
own `source/` vocabulary endpoint at pull time, and an instance that declares no
attribution raises rather than shipping a blank credit line. The ten instances'
licence terms have been read one by one, through each portal's own legal
flatpages: three reserve all rights, seven publish nothing about the data, and
`geotrek_instances.yaml` records the quote and date for each. All ten are
closed and skipped, exactly the outcome the plan's "silence is not permission"
rule was written for. data.gouv.fr carries the Gard's PDIPR trails "(source
Géotrek)" under the Licence Ouverte 2.0 — an open distribution by the same
operator, and the model for reopening an instance.

### 5. Americas & Oceania wave — **6 of 6 built where an open endpoint exists**

Built and pulled: NPS (27,820 km), Ontario (45,544 km), British Columbia
(20,198 km of active reserves; the retired half dropped), and three Australian
states — South Australia's Recreation Trails (9,461 km), Tasmania's LIST tracks
(4,181 km) and Victoria's walkable Recreation Tracks (1,139 km) — each on a
government endpoint with its licence read from the publisher's own text. USGS's
National Digital Trails has no public endpoint anywhere it was looked for — TNM
Access, ScienceBase, ArcGIS Online — and is parked; NSW, Queensland and Western
Australia have no open endpoint either (token-gated, absent, CC BY-NC). The
USFS/NPS dedup pass is still to do.

### 6. Cross-links & refresh automation — **not started**

The ref/name/proximity matcher against OSM relations and quarterly re-pull
automation. Source health checks in CI are half done: `.github/workflows/
source-health.yml` runs `trailsdb health` across all 20 endpoints on demand,
with the quarterly cron written but commented out until the refresh cadence is
settled. The pieces the rest depends on exist: pull manifests record per-file
hashes and ETags so a re-pull only re-downloads what changed.

## The first concrete step

The plan named the CNIG adapter: the fiddliest pull (POST flow, free account,
~9,900 files) and the one that makes the feature visible in the live Galicia
pack immediately.

It is built and running against the live download centre. Discovery — turning
a series into a list of downloadable files — parses the centre's paginated HTML
listing (tested against a page cut from the real response), and the flow turned
out simpler than the plan feared: no account, no POST, a plain GET per file.
Discovery returning nothing is a hard failure, not an empty series, so drift
surfaces immediately. The Camino series is pulled and normalized; FEDME's
~3,460 GPX files and the Camino del Cid are downloading at 1.5 s per request.

## Risks, and what is in place for each

| risk | mitigation | state |
| --- | --- | --- |
| Source drift — endpoints move (DOC announced 2026 URL changes) | Per-source health check; adapters fail loudly rather than yielding a quietly smaller dataset | built (`trailsdb health`; CI workflow on demand, schedule not yet enabled) |
| Attribute heterogeneity — 15 schemas into one | Deliberately small normalized schema; everything else into per-source `extras`, kept out of tiles | built |
| Double rendering — official routes often also exist in OSM | Separate layers (not a legal problem), distinct styling, cross-link matcher for a merged info card | not started (phase 6) |
| Coverage honesty — Germany, Portugal, South America, Asia have no official datasets | OSM stays primary everywhere; official is enrichment where states publish | by design |
| Km estimates — the assumed rows could each be ±50 % | `trailsdb estimate` switches to measured lengths as sources land; the conclusion survives the 1M-km upper bound either way | built |

## Before any of this ships

Nineteen of the 23 sources carry a `legal.verified_on` date; `trailsdb export`
refuses the other four by default. Clearing each one means: read the
publisher's terms, confirm the license id, confirm the exact attribution
wording, then set the date. It is the one part of this that cannot be
automated, and the export gate is there so it cannot be skipped by accident
either. Reading the text paid for itself immediately: the IGN licence takes
the producer credit from the SCNE product table, and every Spanish series had
the wrong one; all ten Geotrek operators turned out closed; and refuges.info is
CC BY-SA 2.0, not 4.0. The open items are an open Geotrek distribution and the
four sources that have no adapter yet.
