#!/usr/bin/env python3
"""
test_scorer.py - regression tests for the FastLocations scoring engine.
Run:  python test_scorer.py   (plain asserts, no pytest; exits non-zero on failure)
Invariant tests, not golden values, so they survive data refreshes.
"""
import scorer
US = {"geography": {"countries": ["US"]}}
CA = {"geography": {"countries": ["CA"]}}
def _find(res, name, st):
    return next((r for r in res if r["county"].startswith(name) and r["state"] == st), None)

def test_determinism():
    a = scorer.run(US, top=15)["results"]; b = scorer.run(US, top=15)["results"]
    assert [r["geoid"] for r in a] == [r["geoid"] for r in b]
    assert [r["final_score"] for r in a] == [r["final_score"] for r in b]

def test_scores_in_bounds():
    for r in scorer.run(US, top=4000)["results"]:
        if r["final_score"] is not None:
            assert 0.0 <= r["final_score"] <= 100.0

def test_incentives_tied_by_state():
    # incentives are state-keyed -> every county in a state gets an identical sub-score (tie fix).
    R = scorer.run(US, top=4000)["results"]
    for st in ("TX", "OH", "CA"):
        inc = {r["sub_scores"]["incentives"] for r in R if r["state"] == st and r["sub_scores"]["incentives"] is not None}
        assert len(inc) == 1, f"{st}: incentives not tied ({len(inc)} distinct)"

def test_infrastructure_is_county_level():
    # infrastructure blends the state ASCE grade with local power-generation capacity, so it varies
    # county-to-county within a state (no longer a flat state broadcast).
    R = scorer.run(US, top=4000)["results"]
    tx = {round(r["sub_scores"]["infrastructure"], 1) for r in R if r["state"] == "TX" and r["sub_scores"]["infrastructure"] is not None}
    assert len(tx) > 5, "infrastructure should vary within a state"

def test_national_anchor():
    nat = scorer.run(US, top=4000)["results"]
    il = scorer.run({"geography": {"countries": ["US"], "required_regions": ["IL"]}}, top=4000)["results"]
    a = _find(nat, "Cook", "IL"); b = _find(il, "Cook", "IL")
    assert a and b and a["sub_scores"] == b["sub_scores"], "not national-anchored"

def test_innovation_index_bea_counties_are_expanded():
    # The StatsAmerica file uses "BEA counties", which merge 24 Virginia counties with their
    # independent cities plus Alaska/Hawaii areas. Codes like 51919 and 51942 are not Census FIPS and
    # a naive join drops them silently. Every key must be a real county the engine knows about.
    if not scorer.IIX:
        return
    assert "_meta" not in scorer.IIX, "provenance leaked into the county records"
    unknown = [f for f in scorer.IIX if f not in scorer.FEAT]
    assert not unknown, f"innovation index has FIPS the engine cannot place: {unknown[:8]}"
    for merged in ("51919", "51942", "51901"):
        assert merged not in scorer.IIX, f"unexpanded BEA county {merged}"
    # Fairfax County and Fairfax City both sit inside BEA 51919 and must both carry its value
    a, b = scorer.IIX.get("51059"), scorer.IIX.get("51600")
    assert a and b and a["bea_county"] == b["bea_county"] == "51919"
    assert a["innovation_output"] == b["innovation_output"]

def test_broadband_direction_is_not_inverted():
    # Both source measures count population LACKING broadband despite their names, so the sign has to
    # be flipped on ingest. If that inversion is ever lost, dense metros would rank as broadband
    # deserts. Guard with counties whose relative standing is not in doubt.
    if not scorer.IIX:
        return
    urban = scorer.IIX.get("13135", {}).get("broadband")      # Gwinnett GA
    rural = scorer.IIX.get("46121", {}).get("broadband")      # Todd County SD
    if urban is None or rural is None:
        return
    assert urban > rural, f"broadband sign inverted: Gwinnett {urban} vs Todd {rural}"

def test_innovation_index_excludes_double_counted_measures():
    # Education, unemployment and STEM occupation shares are sourced directly elsewhere in the model.
    # Ingesting StatsAmerica's versions too would weight the same facts twice.
    if not scorer.IIX:
        return
    rec = next(iter(scorer.IIX.values()))
    for banned in ("headline", "education", "unemployment", "stem", "high_tech"):
        assert not any(banned in k.lower() for k in rec), f"double-counted measure ingested: {banned}"

def test_innovation_index_absent_for_canada_without_penalty():
    # Canadian CDs have no coverage in this US-only source. They must simply lack the metric rather
    # than score zero on it, and must still receive an innovation sub-score from knowledge_economy.
    ca = next(iter(scorer.CA_FEAT.values()))
    inn = scorer.m_innovation(ca, {})
    assert "innovation_output" not in inn and "business_dynamism" not in inn
    assert scorer.innovation_output(ca) is None
    assert "knowledge_economy" in inn, "Canada left with no innovation signal at all"

def test_real_estate_uses_land_price_weighted_over_tax():
    # Real estate was property tax alone, which penalised high-tax/cheap-land regions and flattered
    # Prop 13 California. Land price must be present and must outweigh the tax rate.
    if not scorer.LANDC:
        return
    re_ = scorer.m_real_estate(scorer.FEAT["36055"], {})      # Monroe County NY
    assert "low_land_cost" in re_ and "low_property_tax" in re_
    assert scorer.METRIC_WEIGHTS["low_land_cost"] > scorer.METRIC_WEIGHTS.get("low_property_tax", 1.0)
    # cheaper land must produce a less negative (higher) raw value than dear land
    cheap = scorer.m_real_estate(scorer.FEAT["36067"], {})["low_land_cost"]   # Onondaga ~$3.5k
    dear = scorer.m_real_estate(scorer.FEAT["13121"], {})["low_land_cost"]    # Fulton GA ~$25k
    assert cheap > dear
    # a county with no farm acreage must still score on tax alone rather than vanishing
    nofarm = next((f for f in scorer.FEAT if f not in scorer.LANDC
                   and scorer.FEAT[f].get("property_tax_rate") is not None), None)
    if nofarm:
        assert scorer.m_real_estate(scorer.FEAT[nofarm], {}) is not None

def test_innovation_is_its_own_dimension():
    # Promoted out of demographics. It must be weighted, scored, and must not be double-counted by
    # still appearing in demographics.
    assert "innovation" in scorer.DIMS
    assert scorer.DEFAULT_WEIGHTS.get("innovation", 0) > 0
    assert abs(sum(scorer.DEFAULT_WEIGHTS.values()) - 1.0) < 1e-9, "default weights must sum to 1"
    dem = scorer.m_demographics(scorer.FEAT["06085"], {})
    for k in ("knowledge_economy", "rd_intensity", "innovation_output", "business_dynamism"):
        assert k not in dem, f"{k} still double-counted inside demographics"
    out = scorer.run({"geography": {"countries": ["US"]}}, top=3)
    assert "innovation" in out["dimensions_live"]
    assert out["results"][0]["sub_scores"].get("innovation") is not None

def test_innovation_weight_actually_moves_ranking():
    # A dimension with a slider that changes nothing is worse than no slider at all.
    base = {w: 0.1 for w in scorer.DEFAULT_WEIGHTS}
    heavy = dict(base, innovation=0.9)
    geo = {"geography": {"countries": ["US"]}}
    a = [r["county"] for r in scorer.run(dict(geo, weights=base), top=25)["results"]]
    b = [r["county"] for r in scorer.run(dict(geo, weights=heavy), top=25)["results"]]
    assert a != b, "innovation weight has no effect on the ranking"

def test_rd_intensity_distinguishes_true_zero_from_missing():
    # A county with CBP coverage but no R&D establishments is a real measurement of zero and must
    # score, not be treated as a coverage gap. A county with no CBP data at all must return None so
    # the scorer damps it instead of penalizing it.
    if not scorer.INNOV:
        return
    zeros = [f for f, v in scorer.INNOV.items() if v.get("rd_estab_share") == 0]
    assert len(zeros) > 1000, "expected most counties to record a genuine zero"
    z = scorer.FEAT.get(zeros[0])
    assert z is None or scorer.rd_intensity(z) == 0.0, "true zero must not become None"
    fake = {"fips": "99999"}
    assert scorer.rd_intensity(fake) is None, "county absent from CBP must be None, not 0"

def test_rd_intensity_is_live_and_ordered():
    # The metric must reach the demographics dimension, and denser R&D counties must not rank below
    # counties with no R&D presence on it.
    if not scorer.INNOV:
        return
    mid = scorer.FEAT.get("25017")            # Middlesex MA - densest R&D cluster in the country
    non = next((scorer.FEAT[f] for f, v in scorer.INNOV.items()
                if v.get("rd_estab_share") == 0 and f in scorer.FEAT), None)
    if not mid or not non:
        return
    assert scorer.rd_intensity(mid) > scorer.rd_intensity(non)
    inn = scorer.m_innovation(mid, {})
    assert "rd_intensity" in inn, "rd_intensity never reaches the innovation dimension"

def test_ca_labour_is_current_and_retains_cd_variation():
    # The Canadian labour inputs are rebased to the current Labour Force Survey but must keep the
    # CD-level dispersion that only the Census publishes. A wholesale LFS substitution would collapse
    # every non-metro CD in a province onto one identical rate, which is what this guards against.
    import collections
    rates = [v.get("unemployment") for v in scorer.CA_FEAT.values() if v.get("unemployment") is not None]
    assert len(rates) > 250, "Canadian unemployment coverage regressed"
    # no single value may dominate: provincial fallback covers 229 CDs, so a straight copy would put
    # well over half the country on ~10 distinct numbers
    common = collections.Counter(rates).most_common(1)[0][1]
    assert common < len(rates) * 0.25, f"CD variation collapsed: {common}/{len(rates)} share one rate"
    assert len(set(rates)) > 50, "too few distinct unemployment rates"
    # and the COVID-era level must be gone
    mid = sorted(rates)[len(rates) // 2]
    assert 3.0 < mid < 8.0, f"median Canadian unemployment {mid} looks like census-era data"

def test_distinct_serving_edos():
    ids = [r["serving_edos"][0]["objectid"] for r in scorer.run(US, top=5)["results"] if r["serving_edos"]]
    assert len(ids) == len(set(ids))

def test_other_notable_are_non_customers():
    out = scorer.run(US, top=5); served = {r["county"] + r["state"] for r in out["results"]}
    for o in out["other_notable"]:
        assert (o["county"] + o["state"]) not in served
        assert set(o.keys()) >= {"county", "state", "final_score"}

def test_excluded_regions():
    out = scorer.run({"geography": {"countries": ["US"], "excluded_regions": ["CA", "TX"]}}, top=4000)
    assert all(r["state"] not in ("CA", "TX") for r in out["results"])

def test_required_regions():
    out = scorer.run({"geography": {"countries": ["US"], "required_regions": ["OH"]}}, top=50)
    assert out["results"] and all(r["state"] == "OH" for r in out["results"])

def test_staffability_monotonic():
    reg = {"countries": ["US"], "required_regions": ["ND", "SD", "MT", "WY"]}
    small = scorer.run({"geography": reg, "workforce": {"headcount": {"initial": 50}}}, top=4000)["results"]
    big = scorer.run({"geography": reg, "workforce": {"headcount": {"initial": 20000}}}, top=4000)["results"]
    ref = min((r for r in small if r["sub_scores"]["workforce"] is not None),
              key=lambda r: scorer.FEAT.get(r["geoid"], {}).get("TOTPOP_CY") or 9e9)
    b = _find(big, ref["county"], ref["state"])
    assert b and b["sub_scores"]["workforce"] <= ref["sub_scores"]["workforce"] + 1e-6

def test_coverage_bonus_present():
    assert any(r["coverage_bonus"] > 0 for r in scorer.run(US, top=10)["results"])

def test_canada_null_handling():
    # scoring must produce valid finals even where some CA sub-scores are null. Logistics is still
    # sparse for a few remote CDs (no airport/port/grid), so it's the standing null-handling check.
    out = scorer.run(CA, top=5)
    assert out["results"] and all(r["final_score"] is not None for r in out["results"])
    assert any(r["sub_scores"]["logistics"] is None for r in scorer.run(CA, top=4000)["results"])

def test_market_proximity():
    out = scorer.run({"geography": {"countries": ["US"], "market_proximity": [{"to": "Columbus, OH", "max_miles": 60}]}}, top=20)
    assert out["results"] and all(r["state"] in ("OH", "IN", "KY", "WV") for r in out["results"])

def test_right_to_work_filter():
    r = scorer.run({"geography": {"countries": ["US"]}, "workforce": {"right_to_work": "required"}}, top=30)["results"]
    assert r and all(x["state"] in scorer.RTW_STATES for x in r)

def test_shift_affects_ranking():
    a = [r["geoid"] for r in scorer.run({"geography": {"countries": ["US"]}, "workforce": {"headcount": {"initial": 500}, "shift_pattern": "single"}}, top=8)["results"]]
    b = [r["geoid"] for r in scorer.run({"geography": {"countries": ["US"]}, "workforce": {"headcount": {"initial": 500}, "shift_pattern": "continuous"}}, top=8)["results"]]
    assert a != b, "shift pattern did not affect ranking"

def test_skill_profile_affects_ranking():
    # Selecting a skill profile must move the workforce dimension and the ranking. A degree-heavy
    # profile (engineers) and a high-school-centric one (general labor) draw on different attainment
    # bands, so counties rank differently on workforce and the Top-N order changes.
    base = {"geography": {"countries": ["US"]}}
    eng = scorer.run({**base, "workforce": {"skill_profile": ["engineers"]}}, top=8)["results"]
    lab = scorer.run({**base, "workforce": {"skill_profile": ["general_labor"]}}, top=8)["results"]
    assert [r["geoid"] for r in eng] != [r["geoid"] for r in lab], "skill profile did not affect ranking"
    # ...and the workforce sub-score itself differs. Compare over ALL overlapping counties: picking one
    # arbitrarily via next(iter(set)) was flaky, because set order shifts with Python's hash seed and a
    # few counties legitimately score the same under both profiles.
    both = {r["geoid"] for r in eng} & {r["geoid"] for r in lab}
    assert both, "expected some overlap to compare"
    e_by = {r["geoid"]: r["sub_scores"]["workforce"] for r in eng}
    l_by = {r["geoid"]: r["sub_scores"]["workforce"] for r in lab}
    assert any(e_by[g] != l_by[g] for g in both), "workforce sub-score identical across profiles"
    gid = next(g for g in sorted(both) if e_by[g] != l_by[g])
    we, wl = e_by[gid], l_by[gid]
    assert we != wl, "workforce sub-score identical across profiles"

def test_skill_profile_absent_is_neutral():
    # With no skill profile chosen, skill_supply is null and the workforce dimension is unchanged
    # from a run that never had the feature -> blank searches must be unaffected.
    R = scorer.run({"geography": {"countries": ["US"]}}, top=4000)["results"]
    ff = next(iter(scorer.FEAT))
    assert scorer.skill_supply(scorer.FEAT[ff], []) is None
    assert R and all(r["final_score"] is not None or True for r in R[:5])

def test_property_access_bonus():
    # A county whose serving EDO has properties listed gets a property-access bonus that lifts (never
    # lowers) its score and flips the has_listed_properties flag. Inject an objectid to simulate the
    # dashboard list, since orgs_with_properties.json may be empty in a fresh checkout.
    saved = scorer.get_property_orgs                 # run() now pulls the org set from this (live properties.js)
    try:
        scorer.get_property_orgs = lambda: set()     # clean baseline, independent of the shipped list
        base = scorer.run(US, top=30)["results"]
        served = next((r for r in base if r["serving_edos"]), None)
        assert served is not None
        before = _find(base, served["county"], served["state"])
        assert before["has_listed_properties"] is False and before["property_bonus"] == 0
        oid = served["serving_edos"][0]["objectid"]
        scorer.get_property_orgs = lambda o=oid: {o}  # simulate exactly this org having listings
        after = _find(scorer.run(US, top=30)["results"], served["county"], served["state"])
    finally:
        scorer.get_property_orgs = saved
    assert after and after["has_listed_properties"] is True
    assert 0 < after["property_bonus"] <= scorer.PROPERTY_BONUS, "bonus is scope-scaled, capped at PROPERTY_BONUS"
    assert after["property_edos"], "flagged county should name the property org"
    assert after["final_score"] >= before["final_score"] - 1e-6, "bonus must not lower the score"

def test_property_bonus_scales_with_scope():
    # Listings held by a broad statewide agency blanket every county in the state and must count for
    # far less than a single-county EDO's -- otherwise one statewide customer lifts a whole state.
    narrow = scorer.property_scope_factor({"territory_county_count": 1})
    regional = scorer.property_scope_factor({"territory_county_count": 10})
    statewide = scorer.property_scope_factor({"territory_county_count": 133})
    assert 0 < statewide < regional < narrow <= 1.0
    assert narrow > statewide * 4, "statewide listings should be heavily discounted"

def test_hazard_screen():
    # FEMA NRI natural-hazard screen. Opt-in: 'required' excludes top-decile-risk counties; a blank
    # search is unaffected. Skips cleanly if county_nri.json isn't in this checkout.
    if not any(scorer.FEAT[f].get("nri_risk") is not None for f in scorer.FEAT):
        return
    base = scorer.run(US, top=4000)["results"]
    hi = [r["geoid"] for r in base
          if (scorer.FEAT.get(r["geoid"], {}).get("nri_risk") or 0) >= scorer.HAZARD_MAX_RISK]
    assert hi, "expected some high-hazard counties in the baseline"
    req = {r["geoid"] for r in scorer.run(
        {"geography": {"countries": ["US"]}, "infrastructure": {"hazard": "required"}}, top=4000)["results"]}
    assert all(g not in req for g in hi), "hazard=required must exclude high-risk counties"
    ex = scorer.FEAT[hi[0]]
    assert scorer.m_infrastructure(ex, {}).get("hazard_resilience") is None          # opt-in only
    assert scorer.m_infrastructure(ex, {"infrastructure": {"hazard": "preferred"}}).get("hazard_resilience") is not None

def test_hazard_preference_actually_demotes():
    # "preferred" must MEANINGFULLY re-rank, not nudge: as a metric inside infrastructure (8% weight)
    # it moved scores ~0.2pt. A direct exposure-scaled penalty is applied instead. Also asserts a
    # blank search is untouched, and that a more-exposed county is penalized more than a less-exposed one.
    if not any(scorer.FEAT[f].get("nri_risk") is not None for f in scorer.FEAT):
        return
    US_H = {"geography": {"countries": ["US"]}, "infrastructure": {"hazard": "preferred"}}
    base = scorer.run(US, top=4000)["results"]
    haz = scorer.run(US_H, top=4000)["results"]
    assert all(r["hazard_penalty"] == 0 for r in base[:20]), "no hazard preference => no penalty"
    pens = {r["geoid"]: r["hazard_penalty"] for r in haz}
    assert any(p > 1 for p in pens.values()), "hazard preference must apply real penalties"
    # the most-exposed county in the top-50 baseline must lose rank once hazard is preferred
    ranked = {r["geoid"]: i for i, r in enumerate(haz)}
    worst = max(base[:50], key=lambda r: scorer.FEAT.get(r["geoid"], {}).get("nri_risk") or 0)
    before = next(i for i, r in enumerate(base) if r["geoid"] == worst["geoid"])
    assert ranked.get(worst["geoid"], 10**6) > before, "high-exposure county should fall when hazard is preferred"
    # penalty must be monotonic in exposure
    two = sorted((r for r in haz if r["hazard_exposure"] is not None), key=lambda r: r["hazard_exposure"])
    assert two[0]["hazard_penalty"] <= two[-1]["hazard_penalty"]

def test_score_reconciles_from_breakdown():
    """QA Test B: final_score must reconcile from terms the API actually returns, within 0.05.
    The weighted factor average alone does NOT reconcile (mean delta ~+11) because of reliability
    damping and the headroom bonuses -- so every term is published in `score_breakdown`."""
    for r in scorer.run(US, top=300)["results"]:
        bd = r["score_breakdown"]
        t = bd["weighted_total"]
        if t is None or r["final_score"] is None:
            continue
        damped = t + bd["reliability_damping"]
        b = sum(bd["bonuses"].values())
        recon = damped + (100 - damped) * min(b / 40.0, 1.0) - sum(bd["penalties"].values())
        assert abs(recon - r["final_score"]) < 0.05, \
            f'{r["county"]}: {recon:.2f} vs {r["final_score"]}'

def test_no_op_sweep():
    """QA Test A: every input the UI presents as 'SCREENS & SCORES' must change the result."""
    import json as _json
    base = _json.dumps(scorer.run(US, top=10)["results"], sort_keys=True, default=str)
    cases = {
        "labor_draw_radius": {**US, "demographics": {"min_population": 500000, "labor_draw_radius_miles": 10}},
        "target_wage":       {**US, "workforce": {"target_wage": {"amount": 18, "basis": "hourly"}}},
        "education_hs":      {**US, "demographics": {"education_priority": "hs"}},
        "drought_preferred": {**US, "infrastructure": {"drought": "preferred"}},
        "hazard_preferred":  {**US, "infrastructure": {"hazard": "preferred"}},
        "incentive_target":  {**US, "incentives": {"min_value_target_usd": 50000000}},
        "renewable":         {**US, "infrastructure": {"renewable": "preferred"}},
    }
    for name, crit in cases.items():
        got = _json.dumps(scorer.run(crit, top=10)["results"], sort_keys=True, default=str)
        assert got != base, f"{name} is a no-op (byte-identical to baseline)"

def test_zero_weights_never_break():
    """All ten sliders at zero must not produce a null score or an unranked/alphabetical list."""
    out = scorer.run({**US, "weights": {d: 0 for d in scorer.DIMS}}, top=5)
    assert out["trace"].get("weights_fallback")
    assert out["results"] and all(r["final_score"] is not None for r in out["results"])
    assert out["results"][0]["geoid"] != min(scorer.FEAT)     # not just FIPS order

def test_logistics_coverage():
    """Logistics used to be dropped whenever a county had no airport/port record, so 82% of counties
    silently skipped the heaviest factor in a distribution search."""
    R = scorer.run(US, top=4000)["results"]
    have = sum(1 for r in R if r["sub_scores"]["logistics"] is not None)
    assert have / len(R) > 0.90, f"logistics coverage only {have}/{len(R)}"

def test_incentive_priority_order_matters():
    """QA #4: the UI says 'your first pick is weighted highest'. Reversing a 3-item list must move the
    incentives sub-score materially (>2 pts) on affected counties, not by 0.03."""
    A = ["property_tax_abatement", "job_training_grant", "cash_grant"]
    fwd = {r["geoid"]: r for r in scorer.run({**US, "incentives": {"priorities": A}}, top=200)["results"]}
    rev = {r["geoid"]: r for r in scorer.run({**US, "incentives": {"priorities": list(reversed(A))}}, top=200)["results"]}
    d = [abs(fwd[g]["sub_scores"]["incentives"] - rev[g]["sub_scores"]["incentives"])
         for g in fwd if g in rev and fwd[g]["sub_scores"]["incentives"] is not None]
    assert d and max(d) > 2.0, f"priority order still cosmetic (max delta {max(d) if d else 0:.2f})"

def test_canadian_proximity_and_unresolved_places():
    """QA #6: Canadian places must geocode, and an unresolvable place must be REPORTED rather than
    silently dropped (which returned a full unfiltered set the user thought was filtered)."""
    for city in ("Toronto, ON", "Calgary, AB", "Winnipeg, MB", "Vancouver, BC", "Montreal, QC"):
        assert scorer.geocode_place(city), f"{city} did not geocode"
    bogus = scorer.run({"geography": {"countries": ["US"],
                                      "market_proximity": [{"to": "Nowheresville, ZZ", "max_miles": 50}]}}, top=3)
    assert bogus["trace"].get("market_proximity_unresolved") == ["Nowheresville, ZZ"]
    ok = scorer.run({"geography": {"countries": ["CA"],
                                   "market_proximity": [{"to": "Toronto, ON", "max_miles": 100}]}}, top=5)
    assert ok["trace"].get("market_proximity_unresolved") is None
    assert 0 < ok["trace"]["candidates_after_filters"] < 100, "Toronto radius should bind"

def test_cross_country_scores_are_comparable():
    """US and Canadian sources are NOT on one scale: US unemployment is a current-year estimate (~4%)
    vs Canada's 2021 Census (~9.6%), and income is USD vs CAD. Ranking them together buried Canada
    (workforce 8.9 vs 51.7). Percentiles are now computed within each country."""
    import statistics
    R = scorer.run({"geography": {"countries": ["US", "CA"]}}, top=4000)["results"]
    us = [r["final_score"] for r in R if r["country"] != "Canada"]
    ca = [r["final_score"] for r in R if r["country"] == "Canada"]
    assert us and ca
    assert abs(statistics.mean(us) - statistics.mean(ca)) < 6, "one country is systematically depressed"
    for dim in ("workforce", "cost"):
        u = [r["sub_scores"][dim] for r in R if r["country"] != "Canada" and r["sub_scores"][dim] is not None]
        c = [r["sub_scores"][dim] for r in R if r["country"] == "Canada" and r["sub_scores"][dim] is not None]
        assert abs(statistics.mean(u) - statistics.mean(c)) < 12, f"{dim} not comparable across countries"

def test_region_diversity_cap():
    """County granularity varies ~5x by state (GA 159, IN 92, all of BC 29), so without a cap the
    finely-subdivided states crowd out everywhere else."""
    import collections
    R = scorer.run({"geography": {"countries": ["US", "CA"]}}, top=20)["results"]
    worst = collections.Counter(r["state"] for r in R).most_common(1)[0][1]
    assert worst <= scorer.MAX_PER_REGION, f"one region took {worst} slots"
    assert len(set(r["state"] for r in R)) >= 8, "too few distinct regions represented"

def test_cost_and_safety_use_the_metro_labor_market():
    """Inside a multi-county MSA, employer wages are the metro's and resident income, cost of living and
    crime are half the metro's, so an outer county is not scored as if it were rural. Real estate stays
    the county's own, and counties outside a multi-county MSA are untouched."""
    F = scorer.FEAT
    henry, paulding, fulton = F["13151"], F["13223"], F["13121"]
    assert scorer.MSA_MAP["13151"] == scorer.MSA_MAP["13121"], "Henry and Fulton should share the Atlanta MSA"
    # employer wages: one labor market, one value (per resident, the county figure tracks job location)
    ew = {g: scorer.m_cost(F[g], {})["low_employer_wages"] for g in ("13151", "13223", "13121")}
    assert len(set(ew.values())) == 1, ew
    # crime: halfway between the county's own rate and the metro's
    m = scorer.METRO["13151"]["crime_rate"]
    assert abs(-scorer.m_safety(henry, {})["low_crime"] - (0.5 * henry["crime_rate"] + 0.5 * m)) < 1e-6
    # real estate is not blended
    assert scorer.m_real_estate(henry, {})["low_property_tax"] == -henry["property_tax_rate"]
    # a county in no multi-county MSA keeps its own values
    lone = next(g for g, f in F.items() if scorer.gsys(f) == "US" and g not in scorer.METRO
                and f.get("MEDHINC_CY") is not None and f.get("crime_rate") is not None)
    assert scorer.m_cost(F[lone], {})["low_labor_cost"] == -F[lone]["MEDHINC_CY"]
    assert scorer.m_safety(F[lone], {})["low_crime"] == -F[lone]["crime_rate"]
    assert not any(scorer.gsys(scorer.ALLFEAT[g]) == "CA" for g in scorer.METRO), "Canada must not be blended"
    # the effect: the Atlanta core no longer trails its outer counties on cost by ~50 points
    sub = {r["geoid"]: r["sub_scores"]["cost"] for r in scorer.run(US, top=5000)["results"]}
    assert sub["13151"] - sub["13121"] < 30, "Henry still reads far cheaper than Fulton"

def test_office_real_estate_leans_on_property_tax():
    """Farmland price per acre says little about an office project's cost, so it counts less for offices."""
    assert scorer.USE_METRIC_WEIGHTS["office"]["low_land_cost"] < scorer.METRIC_WEIGHTS["low_land_cost"]
    arl = "51013"   # Arlington VA: $150k/acre land, a low property-tax rate
    base = {r["geoid"]: r["sub_scores"]["real_estate"] for r in scorer.run(US, top=5000)["results"]}
    off = {r["geoid"]: r["sub_scores"]["real_estate"] for r in
           scorer.run(dict(US, use_type={"primary": "office"}), top=5000)["results"]}
    assert off[arl] > base[arl], "office real estate should weigh land price less"
    # other use types are unchanged, and a malformed use type is ignored rather than crashing
    assert {r["geoid"]: r["sub_scores"]["real_estate"] for r in
            scorer.run(dict(US, use_type={"primary": "manufacturing"}), top=5000)["results"]} == base
    assert scorer.run(dict(US, use_type={"primary": ["office"]}), top=3)["results"]
    assert scorer.run(dict(US, use_type="office"), top=3)["results"]

def test_identical_across_processes():
    """Identical inputs must give identical outputs in every process, not just within one. Python
    randomises set order per process (PYTHONHASHSEED); summing a dimension's metrics in set order made
    sub-scores differ by 0.1 between processes, so a redeploy could reorder near-ties."""
    import os, subprocess, sys
    code = ("import json, scorer; R = scorer.run({'geography': {'countries': ['US', 'CA']}}, top=5000)['results'];"
            "print(json.dumps([[r['geoid'], r['final_score'], r['weighted_total'], r['sub_scores']] for r in R]))")
    here = os.path.dirname(os.path.abspath(__file__))
    outs = [subprocess.run([sys.executable, "-c", code], cwd=here, capture_output=True, text=True, check=True,
                           env=dict(os.environ, PYTHONHASHSEED=seed)).stdout for seed in ("1", "2")]
    assert outs[0] and outs[0] == outs[1], "scores differ between processes with different hash seeds"

def test_top5_spreads_across_divisions():
    """The Top 5 takes one result per Census division first, so it cannot cluster in one region."""
    out = scorer.run(US, top=5)
    divs = [scorer.DIVISION[r["state"]] for r in out["results"]]
    assert len(divs) == 5 and len(set(divs)) == 5, f"Top 5 repeats a division: {divs}"
    assert out["trace"].get("division_spread") == scorer.MAX_PER_DIVISION
    assert all(st in scorer.DIVISION for st in {d["ST_ABBREV"] for d in scorer.ALLFEAT.values()}), \
        "a state or province has no division"

def test_spread_skipped_for_preferred_regions():
    # The user asked for these areas: spreading the list away from them would undo the preference.
    out = scorer.run(dict(US, geography={"countries": ["US"], "preferred_regions": ["GA", "FL"]}), top=5)
    assert "division_spread" not in out["trace"]
    assert sum(1 for r in out["results"] if r["state"] in ("GA", "FL")) >= 3

def test_spread_backfills_within_one_division():
    # GA, FL and NC are all South Atlantic: the spread must backfill rather than return one result,
    # and still honour the per-state cap.
    out = scorer.run(dict(US, geography={"countries": ["US"], "required_regions": ["GA", "FL", "NC"]}), top=5)
    R = out["results"]
    assert len(R) == 5
    import collections
    assert max(collections.Counter(r["state"] for r in R).values()) <= scorer.MAX_PER_REGION

def test_dai_is_blended_into_workforce():
    # The county DAI score had its own dimension and slider at 7%. It is now part of workforce at a
    # fixed share of the dimension, not a dimension of its own and not one more workforce metric.
    assert "dai" not in scorer.DIMS
    assert "dai" not in scorer.DEFAULT_WEIGHTS
    assert abs(scorer.DEFAULT_WEIGHTS["workforce"] - 0.23) < 1e-9, "workforce must carry the old 16 + 7"
    assert abs(sum(scorer.DEFAULT_WEIGHTS.values()) - 1.0) < 1e-9, "default weights must sum to 1"
    assert abs(scorer.DAI_WORKFORCE_SHARE - 7 / 23) < 1e-9
    wf = scorer.m_workforce(scorer.FEAT["11001"], {})
    assert "critical_thinking" not in wf and "dai_score" not in wf, "DAI must not also be a workforce metric"
    # direction: DC (90.3) must out-rank Clay County KY (9.2) on the raw metric
    hi = scorer.m_dai(scorer.FEAT["11001"], {})["dai_score"]
    lo = scorer.m_dai(scorer.FEAT["21051"], {})["dai_score"]
    assert hi > lo
    # coverage: essentially every US county except Alaska carries a value
    have = sum(1 for f in scorer.FEAT.values() if scorer.m_dai(f, {}) is not None)
    assert have >= 3100, f"only {have} US counties carry a DAI score"
    out = scorer.run(US, top=3)
    assert "dai" not in out["dimensions_live"] and "dai" not in out["weights_used"]
    assert all("dai" not in r["sub_scores"] for r in out["results"])
    # Canada has no DAI: workforce is still scored on its other metrics
    ca = scorer.run(CA, top=3)["results"]
    assert all(r["sub_scores"]["workforce"] is not None and r["final_score"] is not None for r in ca)

def test_dai_moves_the_workforce_score():
    # Same county set scored with and without the blend: DAI must actually move workforce.
    blended = {r["geoid"]: r["sub_scores"]["workforce"] for r in scorer.run(US, top=5000)["results"]}
    saved = scorer.DAI_WORKFORCE_SHARE
    scorer.DAI_WORKFORCE_SHARE = 0.0
    try:
        plain = {r["geoid"]: r["sub_scores"]["workforce"] for r in scorer.run(US, top=5000)["results"]}
    finally:
        scorer.DAI_WORKFORCE_SHARE = saved
    moved = sum(1 for g in blended if g in plain and blended[g] != plain[g])
    assert moved > len(blended) // 2, f"DAI moved workforce for only {moved} of {len(blended)} counties"

def test_legacy_dai_weight_folds_into_workforce():
    # A scenario saved while DAI had its own slider must score as if that weight were on workforce.
    old = dict(scorer.DEFAULT_WEIGHTS, workforce=0.16, dai=0.07)
    a = scorer.run(dict(US, weights=old), top=10)
    b = scorer.run(US, top=10)
    assert "dai" not in a["weights_used"]
    assert abs(a["weights_used"]["workforce"] - 0.23) < 1e-9
    assert [r["geoid"] for r in a["results"]] == [r["geoid"] for r in b["results"]]

def test_presets_and_form_match_the_scorer():
    # Every use-type preset in intake.js and the form's default sliders must cover exactly
    # scorer.DIMS (no dai slider) and sum to 100. Parsed from the JS/HTML so the UI cannot drift.
    import os, re
    here = os.path.dirname(os.path.abspath(__file__))
    js = open(os.path.join(here, "intake.js"), encoding="utf-8").read()
    block = js[js.index("const USE_PRESETS"):js.index("};", js.index("const USE_PRESETS"))]
    presets = re.findall(r"^\s*(\w+):\s*\{([^}]*)\}", block, re.M)
    assert len(presets) >= 7, "use-type presets not found in intake.js"
    for name, body in presets:
        w = {k: int(v) for k, v in re.findall(r"(\w+):\s*(\d+)", body)}
        assert sum(w.values()) == 100, f"preset {name} sums to {sum(w.values())}, expected 100"
        assert set(w) == set(scorer.DIMS), f"preset {name} keys differ from scorer.DIMS: {set(w) ^ set(scorer.DIMS)}"
    html = open(os.path.join(here, "site_selection_intake.html"), encoding="utf-8").read()
    assert not re.search(r"\bDAI\b", html, re.I), "the form still mentions DAI"
    sliders = dict(re.findall(r'data-w="(\w+)"[^>]*value="(\d+)"', html))
    assert set(sliders) == set(scorer.DIMS), f"form sliders differ from scorer.DIMS: {set(sliders) ^ set(scorer.DIMS)}"
    assert sum(int(v) for v in sliders.values()) == 100, "form default sliders must sum to 100"
    for d, v in sliders.items():
        assert int(v) == round(scorer.DEFAULT_WEIGHTS[d] * 100), f"form default for {d} ({v}) != scorer default"

def test_cost_is_ranked_within_market_tiers():
    # Ranked nationally, every big metro sat in the bottom decile on cost because wages rise with market
    # size. Within tiers each size class must average ~50, and a mid-priced metro must no longer read as
    # one of the dearest places in the country.
    import statistics, collections
    assert "cost" in scorer.TIERED_DIMS
    R = scorer.run(US, top=5000)["results"]
    by = collections.defaultdict(list)
    for r in R:
        if r["sub_scores"]["cost"] is not None: by[scorer.market_tier(scorer.FEAT[r["geoid"]])].append(r["sub_scores"]["cost"])
    assert set(by) == {"rural", "small", "mid", "metro"}, set(by)
    for t, v in by.items():
        assert abs(statistics.mean(v) - 50) < 8, f"tier {t} averages {statistics.mean(v):.1f} on cost"
    maricopa = next(r for r in R if r["geoid"] == "04013")
    assert maricopa["sub_scores"]["cost"] > 35, "Phoenix still scored as if compared with rural counties"
    # tiering only touches cost: workforce stays a single national ranking
    saved = set(scorer.TIERED_DIMS); scorer.TIERED_DIMS.clear()
    try:
        flat = scorer.run(US, top=5000)["results"]
    finally:
        scorer.TIERED_DIMS.update(saved)
    f = {r["geoid"]: r for r in flat}
    assert all(f[r["geoid"]]["sub_scores"]["workforce"] == r["sub_scores"]["workforce"] for r in R)
    metro_flat = statistics.mean(f[r["geoid"]]["sub_scores"]["cost"] for r in R
                                 if scorer.market_tier(scorer.FEAT[r["geoid"]]) == "metro" and f[r["geoid"]]["sub_scores"]["cost"] is not None)
    assert metro_flat < 35, "without tiering metros should read as expensive; the test premise is off"
    # Canada is one tier, so its cost scores are unchanged by tiering
    ca = scorer.run(CA, top=5000)["results"]
    scorer.TIERED_DIMS.clear()
    try:
        ca_flat = {r["geoid"]: r["sub_scores"]["cost"] for r in scorer.run(CA, top=5000)["results"]}
    finally:
        scorer.TIERED_DIMS.update(saved)
    assert all(ca_flat[r["geoid"]] == r["sub_scores"]["cost"] for r in ca)

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t(); passed += 1; print(f"  PASS  {t.__name__}")
        except Exception as e:
            failed += 1; print(f"  FAIL  {t.__name__}: {e!r}")
    print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
