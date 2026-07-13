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
    out = scorer.run(CA, top=5)
    assert out["results"] and all(r["final_score"] is not None for r in out["results"])
    assert any(r["sub_scores"]["livability"] is None for r in scorer.run(CA, top=4000)["results"])

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

def test_property_access_bonus():
    # A county whose serving EDO has properties listed gets a property-access bonus that lifts (never
    # lowers) its score and flips the has_listed_properties flag. Inject an objectid to simulate the
    # dashboard list, since orgs_with_properties.json may be empty in a fresh checkout.
    saved = scorer.PROPERTY_ORGS
    try:
        scorer.PROPERTY_ORGS = set()               # clean baseline, independent of the shipped list
        base = scorer.run(US, top=30)["results"]
        served = next((r for r in base if r["serving_edos"]), None)
        assert served is not None
        before = _find(base, served["county"], served["state"])
        assert before["has_listed_properties"] is False and before["property_bonus"] == 0
        oid = served["serving_edos"][0]["objectid"]
        scorer.PROPERTY_ORGS = {oid}               # simulate exactly this org having listings
        after = _find(scorer.run(US, top=30)["results"], served["county"], served["state"])
    finally:
        scorer.PROPERTY_ORGS = saved
    assert after and after["has_listed_properties"] is True
    assert after["property_bonus"] == scorer.PROPERTY_BONUS
    assert after["property_edos"], "flagged county should name the property org"
    assert after["final_score"] >= before["final_score"] - 1e-6, "bonus must not lower the score"

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
