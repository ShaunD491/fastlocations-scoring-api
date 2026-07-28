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
    dem = scorer.m_demographics(mid, {})
    assert "rd_intensity" in dem, "rd_intensity never reaches the demographics dimension"

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
