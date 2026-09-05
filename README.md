# Trail data snapshot, 2026-09-04 (data branch)

The data behind [`trailsdb`](https://github.com/reXavis/mrtrail) as pulled and normalized on 2026-09-04: 20 official sources, 690,761 km of routes and network segments plus 8,467 huts and shelters, every feature carrying its `source`, `license` and the publisher's exact credit line. Everything here is regenerable with `trailsdb pull` / `normalize` / `export`; this snapshot saves the day of polite pulls.

## What is on this branch

This is an orphan branch: it shares no history with the code and exists only to
carry the data GitHub's 100 MB file limit would otherwise keep out of the
repository. Clone the code with `--single-branch` if you do not want it.

```
normalized/<source>.geojsonl.gz     the master database, one gzipped GeoJSONL per source
                                    (usfs_trails is split: cat the .part-* files back together)
catalog/catalog.sqlite.gz.part-*    the SQLite catalog, gzipped and split into 95 MB parts
exports/<pack>/                     pack inputs: <layer>.geojsonl.gz (split when large),
                                    export.json (tippecanoe args), attribution.json, bake.json
```

The raw tier (3.2 GB of source pulls) is not here: it is the disposable tier
and `trailsdb pull` rebuilds it in about a day, re-downloading only what
changed once the manifests exist. The ten Geotrek operators' data is nowhere in
this snapshot because their terms are closed.

## Reassembling into a `trailsdb` data directory

```bash
git clone --single-branch --branch data-2026-09-04 https://github.com/reXavis/mrtrail mrtrail-data
./mrtrail-data/reassemble.sh /path/to/data     # writes data/normalized, data/catalog.sqlite, data/exports
TRAILSDB_DATA=/path/to/data trailsdb status
```

## Licences and credit lines


Each source is redistributed here under its own licence, exactly as its publisher states it; the per-feature `attribution` field and `exports/licenses.json` in the repository carry the same strings. Share-alike sources (EuroVelo, ODbL; refuges.info, CC BY-SA 2.0) are separate files and separate tile layers, never merged with anything else. Anyone reusing this snapshot must show these credit lines and keep the layers separate in the same way; see `docs/handoff.md`.

| source | licence | credit line to show |
| --- | --- | --- |
| `au_sa_trails` | [cc-by-4.0](https://creativecommons.org/licenses/by/4.0/) | Recreation Trails © Government of South Australia (Department for Environment and Water), CC BY 4.0 |
| `au_tas_tracks` | [cc-by-3.0-au](https://creativecommons.org/licenses/by/3.0/au/) | Transport Segments from theLIST ©State of Tasmania |
| `au_vic_tracks` | [cc-by-4.0](https://creativecommons.org/licenses/by/4.0/) | Recreation Tracks © State of Victoria (Department of Energy, Environment and Climate Action), CC BY 4.0 |
| `bc_recreation` | [ogl-bc-2.0](https://www2.gov.bc.ca/gov/content?id=A519A56BC2BF44E4A008B33FCF527F61) | Contains information licensed under the Open Government Licence - British Columbia. |
| `caminos_naturales` | [cc-by-4.0](https://creativecommons.org/licenses/by/4.0/) | Obra derivada de CNT 2024 CC-BY 4.0 MAPA |
| `cnig_camino` | [cc-by-4.0](https://creativecommons.org/licenses/by/4.0/) | Obra derivada de Rutas de Caminos de Santiago 2020-2026 CC-BY 4.0 FEAACS |
| `cnig_camino_cid` | [cc-by-4.0](https://creativecommons.org/licenses/by/4.0/) | Obra derivada de RCE_CDC 2018-2020 CC-BY 4.0 Camino del CID |
| `cnig_fedme` | [cc-by-4.0](https://creativecommons.org/licenses/by/4.0/) | Obra derivada de FEDME 2020-2026 CC-BY 4.0 FEDME |
| `eurovelo` | [odbl-1.0](https://opendatacommons.org/licenses/odbl/1-0/) | Contains information from EuroVelo GPX tracks downloaded from www.EuroVelo.com on 2026-08-27, which is made available here under the Open Database License (ODbL). |
| `kartverket_turrutebasen` | [cc-by-4.0](https://creativecommons.org/licenses/by/4.0/) | © Kartverket |
| `naturvardsverket` | [cc0-1.0](https://creativecommons.org/publicdomain/zero/1.0/) | Naturvardsverket (CC0) |
| `nps_trails` | [us-public-domain](https://www.usa.gov/government-works) | US National Park Service |
| `nz_doc` | [cc-by-4.0](https://creativecommons.org/licenses/by/4.0/) | Crown copyright (c) Department of Conservation (DOC) and the New Zealand Government |
| `nz_doc_network` | [cc-by-4.0](https://creativecommons.org/licenses/by/4.0/) | Crown copyright (c) Department of Conservation (DOC) and the New Zealand Government |
| `ontario_otn` | [ogl-ontario-1.0](https://www.ontario.ca/page/open-government-licence-ontario) | Contains information licensed under the Open Government Licence - Ontario. |
| `refuges_info` | [cc-by-sa-2.0](https://creativecommons.org/licenses/by-sa/2.0/) | © refuges.info contributors, CC BY-SA 2.0 |
| `swisstopo_wanderwege` | [opendata-swiss-by](https://www.geocatalog.ch/terms-of-use) | Federal Office of Topography swisstopo |
| `uk_national_trails` | [ogl-uk-3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/) | (c) Natural England. Contains public sector information licensed under the Open Government Licence v3.0. |
| `usfs_trails` | [us-public-domain](https://www.usa.gov/government-works) | USDA Forest Service |

Sources without a verified licence (`geotrek`, `usgs_ndt`, `spain_regional`, `australia_states`) contain no data in this snapshot.
