# FastLocations — Local Scoring App

Connects the intake form to the deterministic scorer so **Generate matches** returns
ranked counties + the EDO to route each lead to (instead of just the criteria payload).

## Run
```
pip install -r requirements.txt
python app.py
```
Then open **http://127.0.0.1:8000** and fill out the form.

## What's in the folder
- `app.py` — Flask server: serves the form at `/`, scores at `POST /match?top=5`, status at `/health`.
- `scorer.py` — deterministic scorer (`run(criteria, top)`); loads its data from this folder.
- `site_selection_intake.html` — the intake form, wired to `/match`.
- `intake.js` — builds the ProjectCriteria from the form. On load it also looks for a prefill seed left by
  Stay or Grow in `localStorage['fl_project_seed_v1']`, applies it, and shows a banner listing the fields it
  filled. Details and the offer rules are in `StayOrGrow/README.md` under *Where next*.
  It also owns three things the form can do with its answers: **Strategic plan (Word)** builds a draft
  expansion plan from the criteria, the last matches and the Stay or Grow seed if there is one (see
  `StayOrGrow/README.md`, *The strategic plan*); **Save scenario / Open scenario** write and read a JSON
  file (`format: fastlocations-project-scenario`) holding the criteria, the matches on screen and the seed,
  so a reader can reopen it, change one input and generate again to compare; and an unsaved draft is
  kept in `localStorage['fl_project_draft_v1']` and restored with a banner on the next visit.
  `applyCriteria()` is the inverse of `buildCriteria()` and is what all three use.
- Data the scorer loads: `county_features.json`, `edo_master_table_dual.json`,
  `edo_fips_index.json`, `incentives_index.json`, plus the per-dimension overlays listed in
  `tool/refresh_registry.py` (for example `county_dai.json`, built from `DAI.csv` by `build_county_dai.py`).

## Dimensions
Eleven, all live: workforce, demographics, infrastructure, logistics, incentives, real_estate, cost,
safety, market_size, livability, innovation. Defaults are `scorer.DEFAULT_WEIGHTS`; the use-type
presets in `intake.js` override them per project type.

`cost` is percentile-ranked within market-size tiers (regional catchment under 100k, to 500k, to 2M, and
over 2M; see `COST_TIERS` in `scorer.py`), so a metro's wages are compared with other metros rather than
with rural counties. Every other dimension ranks nationally. Canada is a single tier.

The county DAI score (0-100, higher is better; US counties only) is part of `workforce`, not a factor of
its own, and the form does not name it. It holds a fixed share of the workforce dimension
(`DAI_WORKFORCE_SHARE` = 7/23 in `scorer.py`), so it moves with the Workforce slider. At the default
weights that is the same 7% of the final score it carried when it had its own slider. Canada and Alaska
have no DAI, so workforce there is scored on its other metrics (a data gap is never a penalty). Scenarios
saved with a separate `dai` weight still open: the form and the scorer add it to workforce. To load a new DAI table, run
`python build_county_dai.py DAI.csv` (or drop the CSV in `tool/staging/county_dai/` and use the refresh
tool) and redeploy.

## Which results surface
Only counties served by a customer EDO can appear in Top Matches; the best of the rest are listed as
Other Notable Matches. The Top-N then takes at most two results per state or province
(`MAX_PER_REGION`) and, first, one per region (`MAX_PER_DIVISION`: US Census divisions and Statistics
Canada's standard regions), backfilling by rank when a search is confined to fewer regions. Scores are
not changed by either rule. The regional rule is skipped when the user names preferred regions, and the
response carries `trace.division_spread` when it applied (the form then says so under the results).

Because customer-EDO coverage drives which regions can appear, re-check the balance whenever the EDO
list changes: `python check_regional_balance.py` reports each region's share of the Top 5 (with and
without the regional rule) against its share of population, plus EDO coverage by region and the states
it barely reaches. `--save` keeps a dated snapshot in `reports/regional_balance/`; `--compare` shows the
change since the latest one. The 2026-09-10 snapshot is the pre-launch baseline.

## API
`POST /match?top=5` with a ProjectCriteria JSON body → ranked results, each with
per-dimension sub-scores, weighted total, and `serving_edos`.
