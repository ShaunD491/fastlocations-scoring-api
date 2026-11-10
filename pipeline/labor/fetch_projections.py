#!/usr/bin/env python3
r"""
fetch_projections.py  (Phase 2 input: growth outlook)
--------------------
Ten-year occupational employment projections, normalized to one row per geography x occupation with
growth_pct (projected % change over the projection decade).

  CA  ESDC Canadian Occupational Projection System (COPS) 2026-2035, national, by NOC 2021.
      Downloaded automatically from open.canada.ca (Open Government Licence).
      growth_pct = Employment_Growth / Employment_2025 x 100 (2025 -> 2035).
      Also carries COPS' Future_Labour_Market_Conditions (shortage / balance / surplus).

  US  BLS Employment Projections 2024-2034, national, by SOC (Table 1.2 "Occupational projections").
      BLS refuses scripted downloads: save in a browser to raw/bls/occupation.xlsx from
          https://www.bls.gov/emp/ind-occ-matrix/occupation.xlsx
      (linked from https://www.bls.gov/emp/tables/occupational-projections-and-characteristics.htm)

  US  State long-term projections (Projections Central), OPTIONAL. Export the long-term occupational
      projections for all states from https://projectionscentral.org/Projections/LongTerm and save the
      CSV/XLSX in raw/projections/. Columns are found by name (state/area, occupation code, base and
      projected employment or percent change), so the export layout can drift a little.

OUTPUT (out/):
    projections_us_national.json   {soc: {...}}
    projections_us_state.json      {state_fips: {soc: {...}}}   (only if the state export is present)
    projections_ca_national.json   {noc: {...}}

USAGE
  py -3 fetch_projections.py
"""
import csv
import glob
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import (XW, download, http_get, load_json, out_path, raw_path, soc_fmt, write_json)

COPS_PKG = "e80851b8-de68-43bd-a85c-c72e1b3a3890"
BLS_EP_URL = "https://www.bls.gov/emp/ind-occ-matrix/occupation.xlsx"

STATE_FIPS = {"AL": "01", "AK": "02", "AZ": "04", "AR": "05", "CA": "06", "CO": "08", "CT": "09", "DE": "10",
              "DC": "11", "FL": "12", "GA": "13", "HI": "15", "ID": "16", "IL": "17", "IN": "18", "IA": "19",
              "KS": "20", "KY": "21", "LA": "22", "ME": "23", "MD": "24", "MA": "25", "MI": "26", "MN": "27",
              "MS": "28", "MO": "29", "MT": "30", "NE": "31", "NV": "32", "NH": "33", "NJ": "34", "NM": "35",
              "NY": "36", "NC": "37", "ND": "38", "OH": "39", "OK": "40", "OR": "41", "PA": "42", "PR": "72",
              "RI": "44", "SC": "45", "SD": "46", "TN": "47", "TX": "48", "UT": "49", "VT": "50", "VA": "51",
              "WA": "53", "WV": "54", "WI": "55", "WY": "56"}
STATE_NAME = {}


def num(v):
    try:
        return float(str(v).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------------------- Canada (COPS)
def cops():
    import json
    meta = json.loads(http_get(f"https://open.canada.ca/data/en/api/3/action/package_show?id={COPS_PKG}", timeout=60))
    res = meta["result"]
    url = next(r["url"] for r in res["resources"] if "summary_sommaire" in r["url"])
    period = re.search(r"(\d{4})_(\d{4})", url)
    p0, p1 = (int(period.group(1)), int(period.group(2))) if period else (None, None)
    dest = download(url, raw_path("cops", os.path.basename(url)))
    raw = open(dest, "rb").read()
    txt = raw.decode("utf-8-sig") if raw[:3] == b"\xef\xbb\xbf" else raw.decode("cp1252")
    out = {}
    base_col = None
    for r in csv.DictReader(io.StringIO(txt)):
        code = (r.get("NOC2021_CNP2021") or "").strip()
        if not (code.isdigit() and len(code) == 5):
            continue
        if base_col is None:
            base_col = next(k for k in r if k.lower().startswith("employment_emploi_"))
        base, growth = num(r.get(base_col)), num(r.get("Employment_Growth_Croissance_emploi"))
        out[code] = {
            "noc": code, "title": r.get("Occupation_Name"),
            "base_year": int(base_col[-4:]), "proj_year": p1,
            "emp_base": base, "emp_change": growth,
            "growth_pct": round(growth / base * 100, 2) if base and growth is not None else None,
            "job_openings": num(r.get("Total_Job_Openings_Ouvertures_emploi")),
            "job_seekers": num(r.get("Total_Job_Seekers_Chercheurs_emploi")),
            "future_conditions": (r.get("Future_Labour_Market_Conditions") or "").strip() or None,
            "recent_conditions": (r.get("Recent_Labour_Market_Conditions") or "").strip() or None,
            "source": f"ESDC COPS {p0}-{p1}",
        }
    return out


# ----------------------------------------------------------------------------- US national (BLS EP)
def bls_national():
    import pandas as pd
    p = raw_path("bls", "occupation.xlsx")
    if not os.path.exists(p):
        print(f"  NOTE: {os.path.relpath(p)} missing - US national growth not built.\n"
              f"        Save {BLS_EP_URL} there (browser download).")
        return None
    xl = pd.ExcelFile(p)
    sheet = next((s for s in xl.sheet_names if "1.2" in s), xl.sheet_names[0])
    raw = pd.read_excel(p, sheet_name=sheet, header=None, dtype=str)
    hdr_row = next(i for i in range(15) if any("matrix code" in str(v).lower() for v in raw.iloc[i]))
    df = raw.iloc[hdr_row + 1:].copy()
    df.columns = [str(c).strip() for c in raw.iloc[hdr_row]]
    cols = {c.lower(): c for c in df.columns}

    def col(*keys, exclude=()):
        for lc, c in cols.items():
            if all(k in lc for k in keys) and not any(x in lc for x in exclude):
                return c
        raise KeyError(f"occupation.xlsx: no column with {keys}: {list(df.columns)}")

    c_code, c_title = col("matrix code"), col("matrix title")
    c_type = next((c for lc, c in cols.items() if "occupation type" in lc), None)
    c_pct = col("percent", "change")
    c_edu = next((c for lc, c in cols.items() if "typical education" in lc), None)
    emp_cols = [c for lc, c in cols.items() if lc.startswith("employment,") and "change" not in lc]
    years = sorted(int(re.search(r"(\d{4})", c).group(1)) for c in emp_cols)
    c_base = next(c for c in emp_cols if str(years[0]) in c)
    c_proj = next(c for c in emp_cols if str(years[-1]) in c)
    out = {}
    for r in df.to_dict("records"):
        soc = soc_fmt(r.get(c_code))
        if not soc:
            continue
        if c_type and str(r.get(c_type)).strip().lower() not in ("line item", "nan", ""):
            # keep summary rows only if no line item exists for the code
            if soc in out:
                continue
        out[soc] = {"soc": soc, "title": str(r.get(c_title) or "").strip(), "base_year": years[0], "proj_year": years[-1],
                    "typical_education": (str(r.get(c_edu)).strip() if c_edu and r.get(c_edu) else None),
                    "emp_base_thousands": num(r.get(c_base)), "emp_proj_thousands": num(r.get(c_proj)),
                    "growth_pct": num(r.get(c_pct)),
                    "source": f"BLS Employment Projections {years[0]}-{years[-1]}"}
    return out


# ----------------------------------------------------------------------------- US states (Projections Central)
def state_projections():
    import pandas as pd
    files = [f for f in glob.glob(raw_path("projections", "*")) if f.lower().endswith((".csv", ".xlsx", ".xls"))]
    if not files:
        print("  NOTE: no Projections Central export in raw/projections/ - state growth not built (national is used).")
        return None
    out = {}
    for f in files:
        df = (pd.read_csv(f, dtype=str) if f.lower().endswith(".csv") else pd.read_excel(f, dtype=str))
        cols = {c.lower().strip(): c for c in df.columns}

        def find(*alts, exclude=()):
            for keys in alts:
                for lc, c in cols.items():
                    if all(k in lc for k in keys) and not any(x in lc for x in exclude):
                        return c
            return None

        c_st = find(("stfips",), ("state", "fips"), ("area",), ("state",), exclude=("name",))
        c_occ = find(("occ", "code"), ("soc",), ("code",))
        c_pct = find(("percent",), ("pct",), ("change", "%"))
        c_base = find(("base",), exclude=("year",))
        c_proj = find(("proj",), exclude=("year", "percent", "pct"))
        c_by, c_py = find(("baseyear",), ("base", "year")), find(("projyear",), ("proj", "year"))
        if not (c_st and c_occ and (c_pct or (c_base and c_proj))):
            print(f"  WARNING: could not read {os.path.basename(f)}; columns = {list(df.columns)}")
            continue
        for r in df.to_dict("records"):
            st = str(r.get(c_st) or "").strip()
            st = STATE_FIPS.get(st.upper(), st.zfill(2)[:2] if st.isdigit() else None)
            soc = soc_fmt(r.get(c_occ))
            if not st or not soc or st == "00":          # STFIPS 0 = the export's national rows
                continue
            base, proj = num(r.get(c_base)) if c_base else None, num(r.get(c_proj)) if c_proj else None
            pct = num(r.get(c_pct)) if c_pct else None
            if pct is None and base and proj is not None:
                pct = round((proj - base) / base * 100, 2)
            out.setdefault(st, {})[soc] = {"soc": soc, "emp_base": base, "emp_proj": proj, "growth_pct": pct,
                                           "base_year": r.get(c_by) if c_by else None,
                                           "proj_year": r.get(c_py) if c_py else None,
                                           "source": f"Projections Central long-term ({os.path.basename(f)})"}
        print(f"  read {os.path.basename(f)}: {sum(len(v) for v in out.values()):,} state x occupation rows so far")
    return out or None


def main():
    print("fetch_projections")
    ca = cops()
    write_json(out_path("projections_ca_national.json"), ca)
    print(f"  COPS: {len(ca)} NOCs")
    us = bls_national()
    if us:
        write_json(out_path("projections_us_national.json"), us)
        print(f"  BLS EP: {len(us)} SOCs")
    st = state_projections()
    if st:
        write_json(out_path("projections_us_state.json"), st)
        print(f"  states: {len(st)}")


if __name__ == "__main__":
    main()
