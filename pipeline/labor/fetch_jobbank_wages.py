#!/usr/bin/env python3
r"""
fetch_jobbank_wages.py  (Phase 1)
----------------------
Canadian wages by occupation: ESDC Job Bank "Wages" open dataset (Open Government Licence - Canada).
Low / median / high (plus average and quartiles where published) by NOC 2021 unit group, for
Canada, each province/territory and each of the 76 economic regions (ERs).

The newest annual CSV in the dataset is found through the open.canada.ca CKAN API, so next
year's file is picked up without editing this script.

OUTPUT (out/):
    jobbank_wages_er.json / .csv   one row per geography x NOC (nation, province, ER)

FIELDS
  Source columns are kept under their Job Bank names (NOC_CNP, NOC_Title_eng, prov, ER_Code_Code_RE,
  ER_Name, Low_Wage_Salaire_Minium, Median_Wage_Salaire_Median, High_Wage_Salaire_Maximal,
  Average_Wage_Salaire_Moyen, Quartile1_Wage_Salaire_Quartile1, Quartile3_Wage_Salaire_Quartile3,
  Data_Source_E, Reference_Period, Annual_Wage_Flag_Salaire_annuel, Wage_Comment_E, ...),
  numbers as numbers, blanks as null. Added alongside:
    country="CA", currency="CAD", geo_level (nation/province/er), geo_code ("00", "35", "3530"),
    occ_code_native (5-digit NOC 2021), soc_code_harmonized (primary SOC from the crosswalk),
    wage_basis ("hourly"|"annual"), wage_low / wage_p25 / wage_median / wage_p75 / wage_high
    (as published), wage_median_annual + wage_median_hourly (converted at 2,080 h/yr, the BLS
    convention, so they line up with OEWS A_MEDIAN / H_MEDIAN), suppressed_flag (no median published).

USAGE
  py -3 fetch_jobbank_wages.py [--refresh]
"""
import csv
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import XW, download, http_get, load_json, out_path, raw_path, write_csv, write_json

DATASET = "adad580f-76b0-4502-bd05-20c125de9116"
HOURS = 2080
NUM = {"Low_Wage_Salaire_Minium": "wage_low", "Quartile1_Wage_Salaire_Quartile1": "wage_p25",
       "Median_Wage_Salaire_Median": "wage_median", "Quartile3_Wage_Salaire_Quartile3": "wage_p75",
       "High_Wage_Salaire_Maximal": "wage_high", "Average_Wage_Salaire_Moyen": "wage_avg"}


def latest_resource():
    meta = json.loads(http_get(f"https://open.canada.ca/data/en/api/3/action/package_show?id={DATASET}", timeout=60))
    res = [r for r in meta["result"]["resources"] if (r.get("format") or "").upper() == "CSV"]
    # file names look like ...-wage2025opendata-esdc-all-19nov2025-vf.csv ; take the highest wageYYYY
    import re
    def yr(r):
        m = re.search(r"wage(\d{4})", r["url"])
        return int(m.group(1)) if m else 0
    best = max(res, key=yr)
    return yr(best), best["url"]


def main():
    year, url = latest_resource()
    dest = raw_path("jobbank", f"wages{year}.csv")
    download(url, dest, force="--refresh" in sys.argv)
    print(f"fetch_jobbank_wages: Job Bank wages {year}")
    xw = load_json(os.path.join(XW, "noc_soc.json"))["noc_to_soc"]

    txt = open(dest, encoding="utf-8-sig").read()
    rows = []
    for r in csv.DictReader(io.StringIO(txt)):
        r = {k.strip(): (v or "").strip() for k, v in r.items() if k}
        noc = r["NOC_CNP"].replace("NOC_", "").zfill(5)
        er = r["ER_Code_Code_RE"].replace("ER", "")
        level = "nation" if er == "00" else ("province" if len(er) == 2 else "er")
        out = dict(r)
        for src, dst in NUM.items():
            v = r.get(src, "").replace(",", "")
            try:
                out[src] = float(v) if v else None
            except ValueError:
                out[src] = None
            out[dst] = out[src]
        annual = r.get("Annual_Wage_Flag_Salaire_annuel") == "1"
        med = out["wage_median"]
        out.update({
            "country": "CA", "currency": "CAD", "geo_level": level, "geo_code": er,
            "geo_name": r.get("ER_Name"),
            "occ_code_native": noc,
            "soc_code_harmonized": (xw.get(noc) or {}).get("soc_primary"),
            "wage_basis": "annual" if annual else "hourly",
            "wage_median_annual": None if med is None else round(med if annual else med * HOURS, 2),
            "wage_median_hourly": None if med is None else round(med / HOURS if annual else med, 2),
            "suppressed_flag": med is None,
            "source": f"ESDC Job Bank wages {year}",
        })
        rows.append(out)
    write_json(out_path("jobbank_wages_er.json"), rows)
    write_csv(out_path("jobbank_wages_er.csv"), rows, list(rows[0].keys()))
    by = {}
    for r in rows:
        by.setdefault(r["geo_level"], [0, 0])
        by[r["geo_level"]][0] += 1
        by[r["geo_level"]][1] += 0 if r["suppressed_flag"] else 1
    for k, (n, ok) in by.items():
        print(f"  {k:9s} rows {n:6,}  with median {ok:6,} ({ok / n:.0%})")
    miss = sorted({r["occ_code_native"] for r in rows if not r["soc_code_harmonized"]})
    if miss:
        print(f"  NOTE: {len(miss)} NOC codes have no SOC link: {miss[:10]}")


if __name__ == "__main__":
    main()
