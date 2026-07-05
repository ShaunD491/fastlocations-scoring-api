#!/usr/bin/env python3
"""
test_scorer.py — regression tests for the FastLocations scoring engine.

Run:  python test_scorer.py         (plain asserts, no pytest needed; exits non-zero on failure)

These are INVARIANT tests, not golden-value tests, so they keep passing when the underlying data
is refreshed. Each locks in a property we deliberately built and don't want to regress.
"""
import scorer

US = {"geography": {"countries": ["US"]}}
CA = {"geography": {"countries": ["CA"]}}

def _find(results, name, st):
    return next((r for r in results if r["county"].startswith(name) and r["state"] == st), None)

# ---- tests -----------------------------------------------------------------

def test_determinism():
    a = scorer.run(US, top=15)["results"]
    b = scorer.run(US, top=15)["results"]
    assert [r["geoid"] for r in a] == [r["geoid"] for r in b], "ranking not deterministic"
    assert [r["final_score"] for r in a] == [r["final_score"] for r in b], "scores not deterministic"

def test_scores_in_bounds():
    for r in scorer.run(US, top=4000)["results"]:
        if r["final_score"] is not None:
            assert 0.0 <= r["final_score"] <= 100.0, f"score out of range: {r['final_score']}"
            assert isinstance(r.get("msa"), (str, type(None)))

def test_tie_handling_state_broadcast():
    # every county in one state shares the state's infrastructure grade and incentive programs,
    # so those two sub-scores must be IDENTICAL across all of that state's counties.
    R = scorer.run(US, top=4000)["results"]
    for st in ("TX", "OH", "CA"):
        rows = [r for r in R if r["state"] == st]
        inf = {r["sub_scores"]["infrastructure"] for r in rows if r["sub_scores"]["infrastructure"] is not None}
        inc = {r["sub_scores"]["incentives"] for r in rows if r["sub_scores"]["incentives"] is not None}
        assert len(inf) == 1, f"{st}: infrastructure not tied ({len(inf)} distinct)"
        assert len(inc) == 1, f"{st}: incentives not tied ({len(inc)} distinct)"

def test_national_anchor():
    # a county's sub-scores must not change when the search is narrowed to its own state.
    nat = scorer.run(US, top=4000)["results"]
    il  = scorer.run({"geography": {"countries": ["US"], "required_regions": ["IL"]}}, top=4000)["results"]
    a = _find(nat, "Cook", "IL"); b = _find(il, "Cook", "IL")
    assert a and b, "Cook County not found"
    assert a["sub_scores"] == b["sub_scores"], "sub-scores changed under region filter (not national-anchored)"

def test_distinct_serving_edos():
    res = scorer.run(US, top=5)["results"]
    ids = [r["serving_edos"][0]["objectid"] for r in res if r["serving_edos"]]
    assert len(ids) == len(set(ids)), "primary results are not distinct EDO customers"

def test_other_notable_are_non_customers():
    out = scorer.run(US, top=5)
    served_ids = {r["county"] + r["state"] for r in out["results"]}
    for o in out["other_notable"]:
        assert (o["county"] + o["state"]) not in served_ids, "other_notable overlaps primary results"
        assert set(o.keys()) >= {"county", "state", "final_score"}, "other_notable missing fields"

def test_excluded_regions_filter():
    out = scorer.run({"geography": {"countries": ["US"], "excluded_regions": ["CA", "TX"]}}, top=4000)
    assert all(r["state"] not in ("CA", "TX") for r in out["results"]), "excluded regions leaked into results"

def test_required_regions_filter():
    out = scorer.run({"geography": {"countries": ["US"], "required_regions": ["OH"]}}, top=50)
    assert out["results"] and all(r["state"] == "OH" for r in out["results"]), "required region not enforced"

def test_staffability_monotonic():
    # a thin-labor county should score LOWER on workforce for a huge headcount than a tiny one.
    small = scorer.run({"geography": {"countries": ["US"], "required_regions": ["ND", "SD", "MT", "WY"]},
                        "workforce": {"headcount": {"initial": 50}}}, top=4000)["results"]
    big   = scorer.run({"geography": {"countries": ["US"], "required_regions": ["ND", "SD", "MT", "WY"]},
                        "workforce": {"headcount": {"initial": 20000}}}, top=4000)["results"]
    # pick the smallest-population county present in both and compare its workforce sub-score
    ref = min((r for r in small if r["sub_scores"]["workforce"] is not None),
              key=lambda r: scorer.FEAT.get(r["geoid"], {}).get("TOTPOP_CY") or 9e9)
    b = _find(big, ref["county"], ref["state"])
    assert b and b["sub_scores"]["workforce"] is not None
    assert b["sub_scores"]["workforce"] <= ref["sub_scores"]["workforce"] + 1e-6, \
        "large headcount did not reduce a thin county's workforce score"

def test_coverage_bonus_present():
    # at least one primary result is served by a local/regional org and carries a coverage bonus.
    res = scorer.run(US, top=10)["results"]
    assert any(r["coverage_bonus"] > 0 for r in res), "no coverage bonus applied to any local/regional match"

def test_null_coverage_no_crash_canada():
    # Canadian results must produce valid scores, and the null-renormalization path must work:
    # most Canadian CDs have no livability datum, so that dimension is null and drops out cleanly.
    out = scorer.run(CA, top=5)
    assert out["results"], "no Canadian results"
    assert all(r["final_score"] is not None for r in out["results"]), "CA produced a null final score"
    allca = scorer.run(CA, top=4000)["results"]
    assert any(r["sub_scores"]["livability"] is None for r in allca), "expected some CA CDs to lack livability"

def test_market_proximity():
    out = scorer.run({"geography": {"countries": ["US"],
                                    "market_proximity": [{"to": "Columbus, OH", "max_miles": 60}]}}, top=20)
    assert out["results"], "proximity search returned nothing"
    assert all(r["state"] in ("OH", "IN", "KY", "WV") for r in out["results"]), "proximity returned far-away counties"

# ---- runner ----------------------------------------------------------------

if __name__ == "__main__":
    import traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t(); passed += 1; print(f"  PASS  {t.__name__}")
        except Exception as e:
            failed += 1; print(f"  FAIL  {t.__name__}: {e}")
            traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
