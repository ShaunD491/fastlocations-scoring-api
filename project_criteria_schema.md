# FastLocations — Site Selection Project Criteria Schema (v1)

This is the contract between the three parts of the matching system:

```
Intake form  ──emits──▶  ProjectCriteria JSON  ──consumed by──▶  Scoring engine  ──▶  AI rationale
```

Every downstream component is built against this object. The intake form's job is to
produce a valid instance of it; the scorer's job is to consume it. Change it here first,
then propagate — don't let the form and the scorer drift.

Conventions:
- `null` means "not specified / no constraint" — the scorer must treat absent criteria as
  non-penalizing, not as zero.
- Ranges are `{ "min": <n|null>, "max": <n|null>}`.
- All distances in **miles**, area in **acres**, building area in **sqft**, power in **MW**,
  water/sewer in **GPD** (gallons/day), money in **USD**.
- `weights` must sum to `1.0` (the form normalizes before emit).

---

## Structure

```json
{
  "schema_version": "1.0",
  "project": {
    "project_id": "string (uuid, generated on submit)",
    "project_name": "string",
    "submitted_by": { "name": "", "firm": "", "email": "", "phone": "" },
    "confidentiality": "named | blind",
    "timeline": { "decision_by": "YYYY-MM-DD|null", "operational_by": "YYYY-MM-DD|null" },
    "notes": "string (free text — also fed to AI parser for anything the fields miss)"
  },

  "use_type": {
    "primary": "manufacturing | warehouse_distribution | data_center | office | r_and_d | flex | mixed",
    "naics": "string|null",
    "description": "string"
  },

  "facility": {
    "type": "existing_building | build_to_suit | greenfield | any",
    "building_sqft": { "min": null, "max": null },
    "site_acres": { "min": null, "max": null },
    "ceiling_clear_height_ft": null,
    "expandability_required": false
  },

  "workforce": {
    "headcount": { "initial": null, "year_5": null },
    "skill_profile": ["general_labor","skilled_trades","technicians","engineers","professional"],
    "shift_pattern": "single | two | three | continuous",
    "target_wage": { "value": null, "basis": "hourly | annual", "relation": "at_or_below_market | flexible" }
  },

  "infrastructure": {
    "power_mw": null,
    "power_reliability": "standard | redundant | critical",
    "natural_gas_required": false,
    "water_gpd": null,
    "sewer_gpd": null,
    "rail": "none | served | adjacent",
    "broadband_min_gbps": null,
    "highway_access_max_miles": null,
    "commercial_airport_max_miles": null,
    "port_required": false
  },

  "demographics": {
    "labor_draw_radius_miles": null,
    "min_population": null,
    "min_labor_force": null,
    "education_priority": "none | hs | some_college | bachelors_plus"
  },

  "incentives": {
    "priorities": ["property_tax_abatement","job_training_grant","cash_grant","tax_credit","tif","utility_rate","fast_track_permitting"],
    "min_value_target_usd": null
  },

  "geography": {
    "countries": ["US","CA"],
    "required_regions": ["state/province codes that MUST be in results"],
    "preferred_regions": ["soft preference, scored up not filtered"],
    "excluded_regions": ["hard filter, removed from results"],
    "market_proximity": [ { "to": "Chicago, IL", "max_miles": 300 } ]
  },

  "budget": {
    "capex_usd": null,
    "annual_opex_target_usd": null
  },

  "weights": {
    "workforce": 0.25,
    "infrastructure": 0.25,
    "incentives": 0.15,
    "real_estate": 0.15,
    "demographics": 0.10,
    "logistics": 0.10
  }
}
```

---

## Field notes for the scorer

- **required_regions / excluded_regions** are *filters* (applied before scoring).
  **preferred_regions** is a *score bonus* (applied during scoring). Keep these distinct —
  conflating them is the most common matching bug.
- **weights** map to the six scoring dimensions. The scorer computes a 0–100 sub-score per
  dimension per candidate, then the project score is the weighted sum. Surfacing each
  sub-score (not just the total) is what lets the AI write an honest rationale.
- **Coverage gaps:** when a candidate has no data for a weighted dimension (e.g. power
  capacity unknown for a county-level candidate), the scorer should return `null` for that
  sub-score and record it, rather than scoring 0. The rationale then states the gap instead
  of silently penalizing. This is the honesty mechanism we discussed.
- **EDO rollup:** the scorer ranks geographies/sites, then joins each to the EDO whose
  territory (FIPS set) contains it. The same ranked list yields "best places" and
  "best customer to route the lead to."

## Worked example (a filled instance)

```json
{
  "schema_version": "1.0",
  "project": {
    "project_id": "auto",
    "project_name": "Project Cardinal",
    "submitted_by": { "name": "J. Rivera", "firm": "Apex Site Advisors", "email": "jr@apex.example", "phone": "" },
    "confidentiality": "blind",
    "timeline": { "decision_by": "2026-09-30", "operational_by": "2028-06-01" },
    "notes": "EV components supplier. Needs proximity to an OEM assembly plant in the Midwest. Union environment acceptable."
  },
  "use_type": { "primary": "manufacturing", "naics": "3363", "description": "EV battery module assembly" },
  "facility": {
    "type": "build_to_suit",
    "building_sqft": { "min": 250000, "max": 400000 },
    "site_acres": { "min": 40, "max": 80 },
    "ceiling_clear_height_ft": 36,
    "expandability_required": true
  },
  "workforce": {
    "headcount": { "initial": 300, "year_5": 550 },
    "skill_profile": ["skilled_trades","technicians"],
    "shift_pattern": "three",
    "target_wage": { "value": 28, "basis": "hourly", "relation": "at_or_below_market" }
  },
  "infrastructure": {
    "power_mw": 15, "power_reliability": "redundant",
    "natural_gas_required": true,
    "water_gpd": 200000, "sewer_gpd": 150000,
    "rail": "served", "broadband_min_gbps": 1,
    "highway_access_max_miles": 5, "commercial_airport_max_miles": 60, "port_required": false
  },
  "demographics": {
    "labor_draw_radius_miles": 45, "min_population": 150000, "min_labor_force": 75000,
    "education_priority": "some_college"
  },
  "incentives": {
    "priorities": ["job_training_grant","property_tax_abatement","cash_grant"],
    "min_value_target_usd": 5000000
  },
  "geography": {
    "countries": ["US"],
    "required_regions": [],
    "preferred_regions": ["IN","OH","MI","KY","TN"],
    "excluded_regions": ["CA"],
    "market_proximity": [ { "to": "Detroit, MI", "max_miles": 250 } ]
  },
  "budget": { "capex_usd": 220000000, "annual_opex_target_usd": null },
  "weights": {
    "workforce": 0.30, "infrastructure": 0.25, "incentives": 0.15,
    "real_estate": 0.10, "demographics": 0.10, "logistics": 0.10
  }
}
```
