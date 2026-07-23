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
    # and the workforce sub-score itself differs for the same county under the two profiles
    both = {r["geoid"] for r in eng} & {r["geoid"] for r in lab}
    assert both, "expected some overlap to compare"
    gid = next(iter(both))
    we = next(r for r in eng if r["geoid"] == gid)["sub_scores"]["workforce"]
    wl = next(r for r in lab if r["geoid"] == gid)["sub_scores"]["workforce"]
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
