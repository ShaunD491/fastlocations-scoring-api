#!/usr/bin/env python3
"""
test_labor.py - tests for occupation labor scoring (labor.py + its use in scorer.py).
Run:  py -3 test_labor.py   (plain asserts, no pytest; exits non-zero on failure)

Part 1 runs on a small FIXED fixture written to a temp folder, so the formulas are tested against known
numbers that never change. Part 2 runs on the real labor_data/ (skipped if it is not built) and checks
invariants plus the plan's three sample projects (200 CNC machinists, 50 software developers,
400 warehouse workers).
"""
import gzip
import json
import math
import os
import sys
import tempfile

import labor

FIELDS = ["emp", "lq", "wage_median", "wage_index", "resident_workers", "growth_outlook", "turnover_proxy"]
REAL_DIR = labor.LABOR_DIR


def _gz(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(obj, fh)


def use_fixture():
    """Four US counties (two in metro M1), one metro, two Canadian CDs in one ER."""
    d = tempfile.mkdtemp(prefix="labor_fixture_")
    _gz(os.path.join(d, "us", "51-4041.json.gz"), {
        "county": {
            "00001": [1000, 2.0, 50000, 1.00, 900, 5.0, 0.05],     # big, concentrated, average wage
            "00002": [100, 0.5, 40000, 0.80, 90, 5.0, 0.04],       # small, cheap; inside metro M1
            "00003": [None, None, 45000, 0.90, None, 5.0, 0.05],   # suppressed employment
            # 00004 has no row at all -> zero supply
        },
        "area": {"M1": [5000, 1.5, 48000, 0.96, 4800, 4.0, 0.05, "msa"]}})
    _gz(os.path.join(d, "ca", "72100.json.gz"), {
        "cd": {"C1": [300, 1.2, 60000, 1.10, 300, 3.0, None], "C2": [30, 0.4, 50000, 0.90, 30, 3.0, None]},
        "er": {"E1": [330, 1.0, 58000, 1.05, 330, 3.0, None]}})
    idx = {"built": "fixture", "fields": FIELDS, "fields_area": FIELDS + ["geo_level"], "fields_ca": FIELDS,
           "us_titles": {"51-4041": "Machinists"}, "ca_titles": {"72100": "Machinists (CA)"},
           "us_nat_median": {"51-4041": 50000}, "ca_nat_median": {"72100": 54500},
           "areas": {"M1": ["Metro One", "msa", "XX"]}, "ers": {"E1": ["Region One", "er", "ON"]},
           "noc_to_soc": {"72100": "51-4041"}, "soc_to_noc": {"51-4041": ["72100"]},
           "county_to_area": {"00002": "M1"}, "cd_to_er": {"C1": "E1", "C2": "E1"}}
    with open(os.path.join(d, "index.json"), "w", encoding="utf-8") as fh:
        json.dump(idx, fh)
    _switch(d)


def _switch(d):
    labor.LABOR_DIR = d
    labor.index.cache_clear()
    labor._occ_file.cache_clear()


def crit(*occs, **wf):
    return {"workforce": {"occupations": list(occs), **wf}}


US4 = ["00001", "00002", "00003", "00004"]


def score_us(c):
    return labor.score_geos(c, {"US": US4}, {"US": "county", "CA": "cd"})


# ----------------------------------------------------------------------------- part 1: fixture
def test_fixture_determinism():
    c = crit({"soc_code": "51-4041", "headcount": 100})
    a, b = score_us(c), score_us(c)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_competition_is_absolute_log_scale():
    r = score_us(crit({"soc_code": "51-4041", "headcount": 20}))
    # 00001: 1000 / 20 = 50 per hire = ample -> 100
    assert r["00001"]["occupations"][0]["competition_ratio"] == 50.0
    assert r["00001"]["sub_scores"]["competition"] == 100.0
    r = score_us(crit({"soc_code": "51-4041", "headcount": 500}))
    # 1000 / 500 = 2 per hire -> 100 * ln 3 / ln 51
    want = round(100 * math.log(3) / math.log(51), 1)
    assert r["00001"]["sub_scores"]["competition"] == want, r["00001"]["sub_scores"]


def test_tight_pool_damps_the_occupation_score():
    w = {"availability": 1, "competition": 0, "cost": 0, "growth": 0, "turnover": 0}
    ample = score_us(crit({"soc_code": "51-4041", "headcount": 100}, labor_weights=w))["00001"]
    tight = score_us(crit({"soc_code": "51-4041", "headcount": 400}, labor_weights=w))["00001"]   # 2.5 per hire
    assert ample["occupations"][0]["tight_pool"] is False and tight["occupations"][0]["tight_pool"] is True
    assert tight["labor_score"] == round(ample["labor_score"] * 0.75, 1)


def test_hiring_shed_used_for_competition():
    r = score_us(crit({"soc_code": "51-4041", "headcount": 100}))
    o = r["00002"]["occupations"][0]
    assert o["labor_shed"] == "M1" and o["labor_shed_emp"] == 5000 and o["competition_ratio"] == 50.0
    assert o["emp"] == 100                                    # local figure still reported
    o1 = r["00001"]["occupations"][0]
    assert o1["labor_shed"] is None and o1["labor_shed_emp"] == 1000


def test_missing_row_is_zero_supply_and_suppressed_is_unscored():
    r = score_us(crit({"soc_code": "51-4041", "headcount": 100}))
    assert r["00004"]["occupations"][0]["emp"] == 0
    assert r["00004"]["labor_score"] is not None
    assert r["00004"]["sub_scores"]["availability"] == 0.0    # lowest pool and LQ in the set
    assert r["00003"]["labor_score"] is None                  # suppressed count: no score at all
    assert r["00001"]["labor_score"] > r["00004"]["labor_score"]


def test_wage_ceiling_cuts_cost():
    base = score_us(crit({"soc_code": "51-4041", "headcount": 100}))
    # 00001 median 50,000; ceiling 50,000 -> not over; ceiling 35,714 -> 40% over -> cost 0
    ok = score_us(crit({"soc_code": "51-4041", "headcount": 100, "wage_ceiling": 50000}))
    assert ok["00001"]["occupations"][0]["over_wage_ceiling"] is False
    assert ok["00001"]["sub_scores"]["cost"] == base["00001"]["sub_scores"]["cost"]
    over = score_us(crit({"soc_code": "51-4041", "headcount": 100, "wage_ceiling": 50000 / 1.4}))
    assert over["00001"]["occupations"][0]["over_wage_ceiling"] is True
    assert over["00001"]["sub_scores"]["cost"] == 0.0


def test_canada_ceiling_uses_wage_index_and_turnover_is_a_gap():
    c = crit({"soc_code": "51-4041", "headcount": 10, "wage_ceiling": 50000})   # = US index 1.00
    r = labor.score_geos(c, {"CA": ["C1", "C2"]}, {"CA": "cd"})
    assert r["C1"]["occupations"][0]["over_wage_ceiling"] is True    # index 1.10 > 1.00
    assert r["C2"]["occupations"][0]["over_wage_ceiling"] is False   # index 0.90
    assert r["C1"]["sub_scores"]["turnover"] is None                 # no Canadian source
    assert r["C1"]["labor_score"] is not None                        # re-weighted, not penalised
    assert r["C1"]["occupations"][0]["labor_shed"] == "E1"
    assert r["C1"]["occupations"][0]["comparability"] == "comparable, not identical"


def test_codes_resolve_across_the_border():
    q = labor.resolve({"noc_code": "72100", "headcount": 5})
    assert q["soc"] == "51-4041" and q["nocs"] == ["72100"]
    q = labor.resolve({"soc_code": "514041", "headcount": 5})          # undashed SOC accepted
    assert q["soc"] == "51-4041" and q["nocs"] == ["72100"]
    assert labor.resolve({"soc_code": "99-9999"}) is None
    _, dropped = labor.requested(crit({"soc_code": "99-9999"}, {"soc_code": "51-4041"}))
    assert len(dropped) == 1


def test_headcount_weights_occupations():
    one = score_us(crit({"soc_code": "51-4041", "headcount": 100}))
    two = score_us(crit({"soc_code": "51-4041", "headcount": 100}, {"soc_code": "51-4041", "headcount": 900}))
    # same occupation twice: per-occupation scores differ only by headcount, so the blend lies between
    a = two["00001"]["occupations"][0]["score"]
    b = two["00001"]["occupations"][1]["score"]
    lo, hi = min(a, b), max(a, b)
    assert lo <= two["00001"]["labor_score"] <= hi
    assert abs(two["00001"]["labor_score"] - round((a * 100 + b * 900) / 1000, 1)) <= 0.1
    assert one["00001"]["occupations"][0]["score"] == a


def test_weight_override():
    c = crit({"soc_code": "51-4041", "headcount": 100},
             labor_weights={"availability": 1, "competition": 0, "cost": 0, "growth": 0, "turnover": 0})
    r = score_us(c)
    assert r["00001"]["labor_score"] == r["00001"]["sub_scores"]["availability"]


def test_no_occupations_is_a_no_op():
    assert score_us({}) == {}
    assert score_us(crit()) == {}


# ----------------------------------------------------------------------------- part 2: real data
def _real():
    _switch(REAL_DIR)
    return labor.available()


def _rank(occ, top=10, countries=("US", "CA")):
    return labor.rank_metros({"geography": {"countries": list(countries)}, "workforce": {"occupations": [occ]}}, top=top)


def test_real_metro_ranking_is_deterministic():
    occ = {"soc_code": "15-1252", "headcount": 50}
    assert json.dumps(_rank(occ), sort_keys=True) == json.dumps(_rank(occ), sort_keys=True)


def test_sample_200_cnc_machinists():
    r = _rank({"soc_code": "51-4041", "headcount": 200})["results"]["US"]
    names = " | ".join(x["name"] for x in r)
    # every top-10 metro must be able to staff 200 hires (>= 5 machinists per hire)...
    assert all(x["occupations"][0]["competition_ratio"] >= 5 for x in r), names
    # ...and the list should hold known machining centres
    assert any(k in names for k in ("Wichita", "Cleveland", "Houston", "Chicago", "Indianapolis", "Grand Rapids",
                                    "Rockford", "Detroit", "Milwaukee")), names


def test_sample_50_software_developers():
    out = _rank({"soc_code": "15-1252", "headcount": 50})["results"]
    assert all(x["occupations"][0]["emp"] >= 500 for x in out["US"])
    assert all(x["occupations"][0]["competition_ratio"] >= 10 for x in out["US"])
    assert out["CA"], "software developers map to NOC 21231/21232 - Canada must rank"


def test_sample_400_warehouse_workers():
    out = _rank({"soc_code": "53-7062", "headcount": 400})["results"]
    assert all(x["occupations"][0]["emp"] >= 10000 for x in out["US"][:5]), [x["name"] for x in out["US"][:5]]
    assert out["CA"][0]["name"] in ("Toronto", "Lower Mainland", "Montréal", "Calgary"), out["CA"][0]["name"]


def test_scorer_integration():
    import scorer
    base = scorer.run({"geography": {"countries": ["US"]}}, top=20)
    assert "labor_request" not in base and base["results"][0]["labor"] is None
    c = {"geography": {"countries": ["US"]},
         "workforce": {"occupations": [{"soc_code": "51-4041", "headcount": 200}]}}
    a, b = scorer.run(c, top=20), scorer.run(c, top=20)
    assert [r["geoid"] for r in a["results"]] == [r["geoid"] for r in b["results"]]
    assert [r["final_score"] for r in a["results"]] == [r["final_score"] for r in b["results"]]
    r0 = a["results"][0]
    assert r0["labor"]["occupations"][0]["modelled"] is True
    assert "For the roles requested" in r0["rationale"]
    # only the workforce dimension moves
    nat = {r["geoid"]: r for r in scorer.run({"geography": {"countries": ["US"]}}, top=4000)["results"]}
    for r in a["results"]:
        o = nat[r["geoid"]]
        for dim in scorer.DIMS:
            if dim != "workforce":
                assert r["sub_scores"][dim] == o["sub_scores"][dim], dim


def test_site_comparison():
    import scorer
    c = {"workforce": {"occupations": [{"soc_code": "51-4041", "headcount": 200}]},
         "sites": ["39035", "Wichita, KS", {"lat": 43.65, "lon": -79.38}]}
    out = scorer.compare_sites(c, c["sites"])
    got = {s["resolved"] for s in out["sites"]}
    assert {"39035", "20173", "3520"} <= got, out["sites"]
    assert out["best_by"]["labor_score"]
    assert json.dumps(out, sort_keys=True) == json.dumps(scorer.compare_sites(c, c["sites"]), sort_keys=True)


def test_intake_parses_roles_and_headcounts():
    import labor_intake as L
    r = L.parse("We need 120 forklift operators and 10 maintenance techs")["roles"]
    assert [(x["text"], x["headcount"]) for x in r] == [("forklift operators", 120), ("maintenance techs", 10)], r
    r = L.split_roles("Forklift operators x 120; CNC machinists (200); quality inspectors: 15; a dozen welders")
    assert [x["headcount"] for x in r] == [120, 200, 15, 12], r
    assert L.split_roles("heating and air conditioning mechanics")[0]["text"] == "heating and air conditioning mechanics"
    assert L.split_roles("2.5k call center reps")[0]["headcount"] == 2500


def test_intake_matches_common_roles():
    import labor_intake as L
    want = {"welders": "51-4121", "CNC machinists": "51-4041", "RNs": "29-1141", "electricians": "47-2111",
            "software developers": "15-1252", "industrial maintenance mechanics": "49-9041"}
    for text, soc in want.items():
        m = L.match(text)
        assert m["pick"] == soc and m["confidence"] in ("exact", "good"), (text, m["pick"], m["confidence"])
    for text, soc in {"forklift operators": "53-7051", "warehouse workers": "53-7062"}.items():
        m = L.match(text)
        assert m["candidates"][0]["soc_code"] == soc, (text, m["candidates"][:2])   # first candidate even when sent to the AI
    assert json.dumps(L.parse("40 welders, 6 assemblers"), sort_keys=True) == json.dumps(L.parse("40 welders, 6 assemblers"), sort_keys=True)


def test_place_summary():
    import labor
    crit = {"workforce": {"occupations": [{"soc_code": "51-4041", "headcount": 200}, {"soc_code": "51-4121", "headcount": 50}]}}
    full = labor.rank_metros(crit, top=10 ** 6)
    us = full["results"]["US"]
    pick = us[7]
    s = labor.place_summary(crit, area=pick["geo_code"], country="US", states=[pick["state_or_province"]])
    assert s["place"]["rank"] == 8 and s["peers"] == len(us) and s["place"]["labor_score"] == pick["labor_score"]
    assert s["in_states"] and all(r["rank"] >= 1 for r in s["in_states"]) and s["leaders"][0]["rank"] == 1
    nm = next(a for a, v in labor.index()["areas"].items() if v[1] == "nonmetro")
    s2 = labor.place_summary(crit, area=nm, country="US")
    assert s2["peer_set"].startswith("U.S. metro and nonmetro") and s2["peers"] > len(us)
    er = sorted(labor.index()["ers"])[0]
    s3 = labor.place_summary(crit, area=er, country="CA")
    assert s3["country"] == "CA" and "place" in s3
    s4 = labor.place_summary(crit, states=["OH"])
    assert "place" not in s4 and s4["in_states_count"] > 0
    assert labor.place_summary(crit, area="nope", country="US").get("error")


def test_api_gate_is_ai_plus_only():
    os.environ.pop("FL_LABOR_OPEN", None)
    os.environ["FL_LABOR_KEY"] = "k" * 24
    import app
    c = app.app.test_client()
    for path in ("/labor/rank", "/labor/compare", "/labor/parse", "/labor/place"):
        assert c.post(path, json={}).status_code == 403, path
    assert c.get("/labor/occupations?q=weld").status_code == 403
    occ = {"workforce": {"occupations": [{"soc_code": "51-4041", "headcount": 50}]}, "geography": {"countries": ["US"]}}
    pub = c.post("/match?top=2", json=occ).json
    assert pub["results"][0]["labor"] is None and "labor_access" in pub
    ok = c.post("/match?top=2", json=occ, headers={"X-FL-Labor-Key": "k" * 24}).json
    assert ok["results"][0]["labor"]["labor_score"] is not None
    assert c.post("/labor/parse", json={"text": "40 welders"}, headers={"X-FL-Labor-Key": "nope"}).status_code == 403
    assert c.post("/labor/parse", json={"text": "40 welders"}, headers={"X-FL-Labor-Key": "k" * 24}).status_code == 200
    os.environ.pop("FL_LABOR_KEY")
    assert c.post("/labor/parse", json={"text": "40 welders"}).status_code == 403     # no key configured: closed


if __name__ == "__main__":
    fails = 0
    use_fixture()
    tests = [(k, v) for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    real_ok = None
    for name, fn in tests:
        if name.startswith(("test_real", "test_sample", "test_scorer", "test_site", "test_intake", "test_api")):
            if real_ok is None:
                real_ok = _real()
            if not real_ok:
                print(f"SKIP {name} (labor_data/ not built)")
                continue
        try:
            fn()
            print(f"ok   {name}")
        except AssertionError as e:
            fails += 1
            print(f"FAIL {name}: {e}")
    print(f"\n{len(tests) - fails}/{len(tests)} passed")
    sys.exit(1 if fails else 0)
