# Labor pipeline (occupation data, US + Canada)

Phases 1–2 of `Occupation_Labor_Data_Plan.md`: clean, joinable occupation datasets (Phase 1), then derived labor
metrics and map layers (Phase 2), in JSON with CSV alongside. Python 3 (`py -3`), run from this folder. Raw downloads
go to `raw/` and outputs to `out/`. Both are git-ignored, so nothing here ships with the scoring API except what
`build_labor_runtime.py` writes to `Projects/labor_data/`.

## Phase 3: scoring (in the API, not this folder)

- `Projects/labor.py`: occupation labor scoring. Five sub-scores (availability, competition, cost, growth,
  turnover), default weights, the hiring-shed rule and tight-pool damping are documented in its docstring.
- `scorer.py`: `workforce.occupations` in a ProjectCriteria blends the labor score into the workforce
  dimension (`LABOR_WORKFORCE_SHARE` = 0.5). Each result carries a `labor` block and the rationale gets a
  "For the roles requested" sentence. `compare_sites()` is the Site Labor Comparison.
- `app.py`: `POST /labor/rank` (every US metro and Canadian ER), `POST /labor/compare` (2–5 sites),
  `GET /labor/occupations?q=` (title lookup).
- Tests: `py -3 test_labor.py` (fixed fixture plus the three sample projects) and `py -3 test_scorer.py`.

Request shape:

```json
{"workforce": {"occupations": [
  {"soc_code": "51-4041", "noc_code": "72100", "title_raw": "CNC machinists", "headcount": 200, "wage_ceiling": 55000}
], "labor_weights": {"availability": 0.3, "competition": 0.25, "cost": 0.2, "growth": 0.15, "turnover": 0.1}}}
```

After any Phase 1–2 rebuild, re-run `build_labor_runtime.py`.

**AI+Plus only.** `/labor/*` and `workforce.occupations` in `/match` need the header `X-FL-Labor-Key` equal to
the env var `FL_LABOR_KEY` (set it on Railway; the EDO Portal's `api.php` sends it). Unset = closed, except
when `app.py` is run directly (local dev).

## Phase 4: plain-English intake

`Projects/labor_intake.py` (`POST /labor/parse`): splits "we need 120 forklift operators and 10 maintenance
techs" into roles and headcounts, then matches each role against ~72,000 job titles (O*NET 30.2 + NOC example
titles, `labor_data/titles.json.gz`). Exact or clear matches are picked deterministically; close calls go to
Claude in the EDO Portal (`api.php labor_parse`), which may only choose among the listed codes. The user always
sees and can change the matched code. The portal page is `EDO/labor.html` (FastLabor).

## Run order

| # | Script | Output (`out/`) | Source |
|---|---|---|---|
| 0 | `build_crosswalks.py` | `crosswalks/county_to_area`, `noc_soc`, `onet_titles`, `noc_titles`, `er_names`, `cd_to_er` | OMB CBSA 2023, BLS area definitions, StatCan NOC concordances, O*NET 30.2, Job Bank |
| 1 | `fetch_oews.py` | `oews_2025_area`, `oews_2025_industry_mix` | BLS OEWS May 2025 |
| 2 | `fetch_acs_occupation.py` | `acs_occ_county`, `acs_occ_tract`, `acs_occ_groups` | Census ACS 2020-2024 5-year, C24010 |
| 3 | `fetch_jobbank_wages.py` | `jobbank_wages_er` | ESDC Job Bank wages 2025 |
| 4 | `fetch_statcan_occupation.py` | `statcan_occ_cma`, `statcan_occ_cd` | StatCan 2021 Census 98-10-0593, 98-10-0591 |
| 5 | `fetch_qwi_turnover.py` | `qwi_county_naics` | Census QWI (LEHD), last 8 quarters |
| ✓ | `validate_phase1.py` | prints counts, null rates, samples, spot checks | |
| 6 | `fetch_projections.py` | `projections_ca_national`, `projections_us_national`, `projections_us_state` | ESDC COPS 2025–2035; BLS EP 2025–2035 (national); Projections Central 2024–2034 (states, used first) |
| 7 | `build_labor_metrics.py` | `labor_metrics_cbsa`, `labor_metrics_county`, `labor_metrics_ca_er`, `labor_metrics_ca_cd` | everything above |
| 8 | `build_labor_geo.py` (run with `py -3 -P`, not `-I`, so the user-installed shapely/pyshp/pyproj load) | `geo/labor_areas_us`, `labor_counties_us`, `labor_er_ca` GeoJSON; `--occ CODE` adds choropleth layers | Census cartographic counties 2023; StatCan 2021 ER boundaries from its public map service (cached in `raw/statcan/er_2021_cbf.geojson`) |
| ✓ | `check_phase2.py` | top-10 LQ metros for known clusters, county add-back, null rates | |
| 9 | `build_labor_runtime.py` | `../../labor_data/` (ships with the API): `index.json` + one gzipped file per SOC (`us/`) and NOC (`ca/`), ~20 MB | the Phase 2 tables + crosswalks |

Run `build_crosswalks.py` first. Everything else reads its output.

Metrics tables are columnar JSON (`{"_meta", "fields", "rows"}`) because the county table has ~1.2M rows. What
each metric means, and how the modelled ones are built, is in the docstring of `build_labor_metrics.py`. In short:
- US county jobs are modelled (`modelled: true`): each area's OEWS jobs are split by ACS resident workers.
- `competition_ratio` is computed at scoring time (`emp` ÷ requested headcount).
- `turnover_proxy` is QWI sector turnover weighted by the occupation's industry mix. It's a proxy and has no Canadian equivalent.
- Canadian ER and CD unit-group counts are modelled from census-division minor groups.

## Files to download by hand

BLS and StatCan's www12 site refuse scripted downloads, and the scripts don't work around that. Open each link
in a browser and save the file at the path shown. If one is missing, the script that needs it stops and prints
the link.

| Save as | Link | Used by |
|---|---|---|
| `raw/bls/oesm25nat.zip` | https://www.bls.gov/oes/special-requests/oesm25nat.zip | fetch_oews |
| `raw/bls/oesm25st.zip` | https://www.bls.gov/oes/special-requests/oesm25st.zip | fetch_oews |
| `raw/bls/oesm25ma.zip` | https://www.bls.gov/oes/special-requests/oesm25ma.zip | fetch_oews |
| `raw/bls/oesm25in4.zip` | https://www.bls.gov/oes/special-requests/oesm25in4.zip | fetch_oews (industry mix) |
| `raw/bls/area_definitions_m2025.xlsx` | https://www.bls.gov/oes/area_definitions_m2025.xlsx  (listed on https://www.bls.gov/oes/2025/may/msa_def.htm) | build_crosswalks (nonmetro areas) |
| `raw/statcan/2021_98260004.csv` | DGRF, data download on https://www150.statcan.gc.ca/n1/pub/98-26-0003/982600032021001-eng.htm | build_crosswalks (CD → ER) |
| `raw/bls/occupation.xlsx` | https://www.bls.gov/emp/ind-occ-matrix/occupation.xlsx (Employment Projections, Table 1.2) | fetch_projections (US national growth) |
| `raw/projections/<export>.csv` or `.xlsx` (optional) | Projections Central long-term export, all states: https://projectionscentral.org/Projections/LongTerm | fetch_projections (US state growth) |

After adding them, re-run `build_crosswalks.py`, then `fetch_oews.py`. For the Phase 2 files, re-run
`fetch_projections.py`, `build_labor_metrics.py`, and then `build_labor_geo.py`.

## Conventions

- Source field names are kept (`OCC_CODE`, `A_MEDIAN`, `AREA`, `NOC_CNP`, `TurnOvrS`, …), and derived fields sit alongside them.
- Every row carries `country` (`US`/`CA`), `occ_code_native` (SOC or NOC) and `soc_code_harmonized`.
- Suppressed values are null. `suppressed_flag` marks the row, and `suppressed_fields` lists the fields. An OEWS `#` (top-coded wage) is null with `topcoded_flag`, because it's a floor, not a missing value.
- Canadian employment counts come from the 2021 Census (`emp_source: "census_2021"`). Wages stay in CAD, and comparisons use each country's own national median. Label Canadian figures "comparable, not identical".
- NOC → SOC is one-to-many and mostly partial. `soc_primary` is the closest single SOC (the only full link, or else the best title match), and every candidate link is kept in `noc_soc.json`.
- The Census key is read from `../../tool/refresh_config.json` (`census_api_key`) or `CENSUS_API_KEY`.
