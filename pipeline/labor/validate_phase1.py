#!/usr/bin/env python3
r"""
validate_phase1.py  (Phase 1 check)
------------------
For every Phase 1 output in out/: row count, null rate per field, and five sample rows.
Then spot-checks for known places, printed next to the public page to compare against:

  US  Syracuse, NY MSA (45060)      OEWS  https://www.bls.gov/oes/current/oes_45060.htm
  US  Columbus, OH MSA (18140)      OEWS  https://www.bls.gov/oes/current/oes_18140.htm
  US  Onondaga County, NY (36067)   ACS   https://data.census.gov/table/ACSDT5Y2024.C24010?g=050XX00US36067
  CA  Toronto CMA (535)             StatCan 98-10-0593 / Job Bank Toronto ER (3530)
  CA  Halifax ER (1250)             Job Bank  https://www.jobbank.gc.ca/wagereport/location/ns

Files that are not built yet are reported as MISSING with the script that makes them, so this
can be run at any point in Phase 1.

USAGE
  py -3 validate_phase1.py              (add --samples 0 to skip sample rows)
"""
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import OUT, XW

NS = int(sys.argv[sys.argv.index("--samples") + 1]) if "--samples" in sys.argv else 5

FILES = [
    ("crosswalks/county_to_area.json", "build_crosswalks.py"),
    ("crosswalks/noc_soc.json", "build_crosswalks.py"),
    ("crosswalks/onet_titles.json", "build_crosswalks.py"),
    ("crosswalks/noc_titles.json", "build_crosswalks.py"),
    ("crosswalks/er_names.json", "build_crosswalks.py"),
    ("crosswalks/cd_to_er.json", "build_crosswalks.py (needs raw/statcan/2021_98260004.csv)"),
    ("oews_2025_area.json", "fetch_oews.py (needs raw/bls/oesm25*.zip)"),
    ("oews_2025_industry_mix.json", "fetch_oews.py"),
    ("acs_occ_county.json", "fetch_acs_occupation.py"),
    ("acs_occ_tract.json", "fetch_acs_occupation.py"),
    ("jobbank_wages_er.json", "fetch_jobbank_wages.py"),
    ("statcan_occ_cma.json", "fetch_statcan_occupation.py"),
    ("statcan_occ_cd.json", "fetch_statcan_occupation.py"),
    ("qwi_county_naics.json", "fetch_qwi_turnover.py"),
]


def records(obj):
    """Turn each output shape into a list of flat-ish dicts."""
    if isinstance(obj, list):
        return obj
    for k in ("rows", "tracts"):
        if isinstance(obj.get(k), list):
            return obj[k]
    for k in ("noc_to_soc", "titles", "mix"):
        if isinstance(obj.get(k), dict):
            return [{"_key": kk, **(v if isinstance(v, dict) else {"value": v})} for kk, v in obj[k].items()]
    return [{"_key": k, **(v if isinstance(v, dict) else {"value": v})} for k, v in obj.items() if not k.startswith("_")]


def short(v, n=90):
    s = json.dumps(v, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + "..."


def summarize(name, recs):
    print(f"\n=== {name}: {len(recs):,} rows")
    if not recs:
        return
    fields = list(dict.fromkeys(k for r in recs[:5000] for k in r))
    nulls = collections.Counter()
    for r in recs:
        for k in fields:
            if r.get(k) in (None, "", [], {}):
                nulls[k] += 1
    print("  null rate by field:")
    line = []
    for k in fields:
        line.append(f"{k}={nulls[k] / len(recs):.0%}")
    for i in range(0, len(line), 6):
        print("    " + "  ".join(line[i:i + 6]))
    if NS:
        step = max(1, len(recs) // NS)
        print(f"  {NS} sample rows:")
        for r in recs[::step][:NS]:
            print("    " + short(r, 300))


def load(rel):
    return json.load(open(os.path.join(OUT, rel), encoding="utf-8"))


def spot_checks():
    print("\n\n################ SPOT CHECKS (compare with the linked public pages)")
    occs = {"00-0000": "All occupations", "15-1252": "Software developers", "51-4041": "Machinists",
            "53-7062": "Laborers & material movers, hand", "29-1141": "Registered nurses"}
    p = os.path.join(OUT, "oews_2025_area.json")
    if os.path.exists(p):
        rows = load("oews_2025_area.json")
        for area, label in (("45060", "Syracuse, NY"), ("18140", "Columbus, OH")):
            print(f"\n  OEWS {label} ({area})  -> https://www.bls.gov/oes/current/oes_{area}.htm")
            for r in rows:
                if r.get("AREA") == area and r.get("OCC_CODE") in occs:
                    print(f"    {r['OCC_CODE']} {occs[r['OCC_CODE']]:34s} emp {r.get('TOT_EMP')!s:>9}  "
                          f"median ${r.get('A_MEDIAN')!s:>9}  LQ {r.get('LOC_QUOTIENT')}  "
                          f"suppressed={r.get('suppressed_fields')}")
    else:
        print("\n  OEWS: not built yet (fetch_oews.py)")

    p = os.path.join(OUT, "acs_occ_county.json")
    if os.path.exists(p):
        print("\n  ACS Onondaga County, NY (36067), resident workers")
        print("    -> https://data.census.gov/table/ACSDT5Y2024.C24010?g=050XX00US36067")
        for r in load("acs_occ_county.json"):
            if r["GEOID"] == "36067" and r["occ_group"] in ("production occupations", "computer and mathematical occupations",
                                                            "material moving occupations", "management occupations"):
                print(f"    {r['occ_label']:45s} {r['resident_workers']!s:>8} +/- {r['resident_workers_moe']}  "
                      f"({r['share_pct']}% of {r['total_employed']:,.0f})")

    p = os.path.join(OUT, "jobbank_wages_er.json")
    if os.path.exists(p):
        nocs = {"21231": "Software engineers & designers", "72100": "Machinists", "75101": "Material handlers",
                "31301": "Registered nurses"}
        print("\n  Job Bank median wages, CAD (Toronto ER 3530, Halifax ER 1250, Ontario, Canada)")
        print("    -> https://www.jobbank.gc.ca/wagereport/occupation/<noc>  (search the NOC title)")
        idx = {(r["geo_code"], r["occ_code_native"]): r for r in load("jobbank_wages_er.json")}
        for noc, t in nocs.items():
            vals = []
            for g in ("3530", "1250", "35", "00"):
                r = idx.get((g, noc))
                vals.append(f"{g}: {r['wage_median'] if r else None} {('/yr' if r and r['wage_basis']=='annual' else '/h') if r else ''}")
            print(f"    {noc} {t:32s} " + " | ".join(vals))

    p = os.path.join(OUT, "statcan_occ_cma.json")
    if os.path.exists(p):
        print("\n  StatCan 2021 Census, Toronto CMA (535) and Halifax CMA (205), employed by NOC")
        print("    -> https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=9810059301")
        for r in load("statcan_occ_cma.json"):
            if r["geo_code"] in ("535", "205") and r["occ_code_native"] in ("21231", "72100", "75101", "31301", "total"):
                print(f"    {r['geo_name']:28s} {r['occ_code_native']} {r['occ_title'][:40]:40s} {r['emp']!s:>9}")

    p = os.path.join(OUT, "qwi_county_naics.json")
    if os.path.exists(p):
        print("\n  QWI turnover (stable), Onondaga 36067 and Franklin OH 39049, last quarters")
        for r in load("qwi_county_naics.json")["rows"]:
            if r["GEOID"] in ("36067", "39049") and r["industry"] in ("00", "31-33", "48-49", "62"):
                print(f"    {r['GEOID']} NAICS {r['industry']:6s} TurnOvrS {r['TurnOvrS_avg']}  sep {r['sep_rate_avg']}  "
                      f"hire {r['hire_rate_avg']}  {r['quarters'][0]}..{r['quarters'][-1]}")


def main():
    for rel, maker in FILES:
        p = os.path.join(OUT, rel)
        if not os.path.exists(p):
            print(f"\n=== {rel}: MISSING  (run {maker})")
            continue
        summarize(rel, records(load(rel)))
    spot_checks()


if __name__ == "__main__":
    main()
