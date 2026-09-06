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
Twelve, all live: workforce, demographics, infrastructure, logistics, incentives, real_estate, cost,
safety, market_size, livability, innovation, dai. Defaults are `scorer.DEFAULT_WEIGHTS`; the use-type
presets in `intake.js` override them per project type.

`cost` is percentile-ranked within market-size tiers (regional catchment under 100k, to 500k, to 2M, and
over 2M; see `COST_TIERS` in `scorer.py`), so a metro's wages are compared with other metros rather than
with rural counties. Every other dimension ranks nationally. Canada is a single tier.

`dai` is the county DAI score (0-100, higher is better; US counties only). It is held at about 7% of
the final score under every scenario: the default weights and every use-type preset carry it at 7, and
`test_scorer.py` enforces that. Canada has no DAI, so Canadian results are scored on the other eleven
factors with the weights renormalised (a data gap is never a penalty). To load a new DAI table, run
`python build_county_dai.py DAI.csv` (or drop the CSV in `tool/staging/county_dai/` and use the refresh
tool) and redeploy.

## API
`POST /match?top=5` with a ProjectCriteria JSON body → ranked results, each with
per-dimension sub-scores, weighted total, and `serving_edos`.
