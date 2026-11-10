#!/usr/bin/env python3
r"""
check_phase2.py  (Phase 2 check)
---------------
1. Top-10 location-quotient metros for known clusters (they should "look right"):
     15-1252 Software developers, 49-3011 Aircraft mechanics, 51-4121 Welders,
     and in Canada NOC 21231 Software engineers (ERs).
   Metros only, at least --min-emp jobs (default 200) so tiny areas with a handful of workers
   don't top the list.
2. Consistency: modelled county jobs add back to each area's jobs; Canadian ER employment adds
   to the national census total; field coverage (null rates) for the key metrics.

USAGE
  py -3 check_phase2.py [--min-emp 200]
"""
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import load_json, out_path

MIN = int(sys.argv[sys.argv.index("--min-emp") + 1]) if "--min-emp" in sys.argv else 200


def table(name):
    t = load_json(out_path(name))
    f = t["fields"]
    return [dict(zip(f, r)) for r in t["rows"]]


def top_lq(rows, code, level, n=10, label=""):
    sel = [r for r in rows if r["occ_code_native"] == code and r["geo_level"] == level
           and r["lq"] is not None and (r["emp"] or 0) >= MIN]
    sel.sort(key=lambda r: -r["lq"])
    title = sel[0]["occ_title"] if sel else code
    print(f"\n  Top {n} by LQ - {code} {title} {label}")
    for r in sel[:n]:
        w = r["wage_median"]
        ws = "n/a" if w is None else f"{w:,.0f}"
        print(f"    {r['geo_name'][:44]:44s} LQ {r['lq']:6.2f}  emp {r['emp']:>8,}  median {ws:>9}"
              f"  idx {r['wage_index']}  turnover {r.get('turnover_proxy')}")


def coverage(rows, fields, label):
    print(f"\n  {label}: {len(rows):,} rows - null rate")
    print("    " + "  ".join(f"{f}={sum(r[f] is None for r in rows) / len(rows):.0%}" for f in fields))


def main():
    cbsa = table("labor_metrics_cbsa.json")
    print("=== 1. Top-LQ check (US metros)")
    for code in ("15-1252", "49-3011", "51-4121"):
        top_lq(cbsa, code, "msa")
    er = table("labor_metrics_ca_er.json")
    print("\n=== 1b. Top-LQ check (Canadian economic regions)")
    for code in ("21231", "72410", "72106"):
        top_lq(er, code, "er", label="(CAD)")

    print("\n=== 2. Consistency")
    county = table("labor_metrics_county.json")
    # Guam and the Virgin Islands are OEWS areas with no counties in the county list, so they have no
    # county rows by design; leave them out of the add-back test.
    a_emp = {(r["geo_code"], r["occ_code_native"]): r["emp"] for r in cbsa
             if r["emp"] and r["geo_code"] not in ("6600001", "7800001")}
    c_sum = collections.Counter()
    for r in county:
        if r["emp"]:
            c_sum[(r["area_code"], r["occ_code_native"])] += r["emp"]
    diffs = [abs(c_sum[k] - v) / v for k, v in a_emp.items() if v >= 100]
    print(f"  county jobs vs area jobs (areas x occs with >=100 jobs): {len(diffs):,} pairs, "
          f"median gap {sorted(diffs)[len(diffs) // 2]:.2%}, worst {max(diffs):.1%} "
          f"(gaps come from dropping counties with <0.5 modelled jobs and rounding)")
    tot = sum(r["emp"] or 0 for r in er)
    print(f"  Canada: ER unit-group employment sums to {tot:,} (census employed 17,321,700; the gap is occupations "
          f"'not elsewhere classified' and rounding in the minor -> unit split)")
    coverage(cbsa, ["emp", "lq", "wage_median", "wage_index", "resident_workers", "growth_outlook", "turnover_proxy"],
             "US areas")
    coverage(county, ["emp", "lq", "wage_median", "resident_workers", "turnover_proxy"], "US counties (modelled)")
    coverage(er, ["emp", "lq", "wage_median", "wage_index", "growth_outlook"], "CA ERs")
    lv = collections.Counter(r["turnover_level"] for r in county)
    print(f"\n  county turnover level used: {dict(lv)}")
    lv = collections.Counter(r["wage_level"] for r in er)
    print(f"  CA ER wage level used: {dict(lv)}")


if __name__ == "__main__":
    main()
