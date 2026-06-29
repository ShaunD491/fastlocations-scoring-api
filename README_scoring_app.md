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
- Data the scorer loads: `county_features.json`, `edo_master_table_dual.json`,
  `edo_fips_index.json`, `incentives_index.json`.

## Dimensions
Live: workforce, demographics, logistics, incentives.
Pending data (return `null`, never penalize): infrastructure (power), real_estate.

## API
`POST /match?top=5` with a ProjectCriteria JSON body → ranked results, each with
per-dimension sub-scores, weighted total, and `serving_edos`.
