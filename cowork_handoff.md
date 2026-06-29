# FastLocations — Site-Matching Engine · Cowork Handoff Brief

Paste this into the Cowork project's **Instructions**, and drop this file (plus
`project_criteria_schema.md` and `site_selection_intake.html`) into the project folder.

---

## Goal

Turn FastLocations from a data aggregator into a matching engine. A site consultant fills
out an intake form describing a location project; the system compares those requirements
against demographic, infrastructure, real-estate, and incentive data, and returns the
**5 best places + the EDO customer to route each lead to**, each with a written rationale.

## Architecture (decided — do not re-derive)

The matching is **deterministic scoring with AI only on the ends**. Do NOT ask an LLM to
"pick the top 5" from raw data — it will invent demographic and infrastructure facts.

```
intake form → ProjectCriteria JSON → deterministic scorer → AI writes rationale for top 5
                                          ▲ facts come from data, never the model
```

- The **scorer** computes a 0–100 sub-score per dimension per candidate, then a weighted
  total using the `weights` block. It must surface each sub-score, not just the total.
- The **AI** does two jobs only: (1) parse messy intake free-text into the structured
  criteria object, and (2) write the rationale *after* ranking is done.

## Non-negotiable rules

1. **FIPS is the join spine.** Datasets live at different granularities (counties by FIPS,
   incentives by state, sites by lat/long). FIPS ties them together and rolls candidates up
   to the EDO that serves them.
2. **required / excluded / preferred regions are three different operations** — required is a
   pre-score filter, excluded is removal, preferred is a score bonus. Never conflate them.
3. **Coverage gaps → `null`, never `0`.** If a candidate has no data for a weighted dimension,
   the sub-score is `null` and the rationale states the gap honestly. Do not silently penalize.
4. **`logistics` weight is derived**, not a schema block: compute it from the infrastructure
   rail/highway/airport/port fields + `geography.market_proximity`.

## Data inputs (read directly from local OneDrive — confirm exact paths)

- `All_Counties.json` — 3,235 county records, has `county_fips`. The geographic base layer.
- State/Provincial incentive JSONs — the incentives dimension. (Note: ND missing from source.)
- Sites & buildings property JSONs — the real-estate candidates (lat/long).
- **EDO customer list** — the org list to match leads to. **Not yet FIPS-tagged. This is the gap.**
- ArcGIS demographic / infrastructure layers — via GeoEnrichment for any geography.

## Current state

- ✅ `ProjectCriteria` schema v1 — see `project_criteria_schema.md` (the contract).
- ✅ Intake form — `site_selection_intake.html`, emits a valid criteria object.
- ⬜ EDO master table (the spine) — **not started. This is the first task.**
- ⬜ Deterministic scorer.
- ⬜ AI parse + rationale layers.

## First task

Build the **EDO master table**: take the EDO customer list and tag every organization with
the set of county FIPS codes its territory covers, joinable to `All_Counties.json`. Output as
JSON (and a GeoJSON if territories are polygons). Flag any EDO whose territory can't be
resolved to FIPS so I can fix the source. Nothing downstream works until this exists.

## Output conventions

- Code: complete and ready-to-run, no placeholder stubs to fill in.
- Data: JSON for tabular/property data, GeoJSON for spatial. Treat me as technically
  proficient — no filler, no hand-holding.
