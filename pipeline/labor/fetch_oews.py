#!/usr/bin/env python3
r"""
fetch_oews.py  (Phase 1)
-------------
Normalizes BLS Occupational Employment and Wage Statistics (OEWS), May 2025, into one row per
area x occupation, plus the national occupation-by-industry mix used for the turnover proxy.

INPUT - download these four files in a browser (BLS refuses scripted downloads) into raw/bls/:
    https://www.bls.gov/oes/special-requests/oesm25nat.zip   national
    https://www.bls.gov/oes/special-requests/oesm25st.zip    states
    https://www.bls.gov/oes/special-requests/oesm25ma.zip    metropolitan + nonmetropolitan areas
    https://www.bls.gov/oes/special-requests/oesm25in4.zip   national by industry (sector rows used)
  (all four are linked from https://www.bls.gov/oes/tables.htm -> "All data" / special requests)
  For another vintage pass --year, e.g. --year 2026 reads oesm26*.zip.

OUTPUT (out/):
    oews_2025_area.json / .csv      national + state + MSA/nonmetro rows
    oews_2025_industry_mix.json     { soc: { naics_sector: employment } }  national, private + public

FIELDS
  Every source column is kept under its BLS name (AREA, AREA_TITLE, AREA_TYPE, PRIM_STATE, OCC_CODE,
  OCC_TITLE, O_GROUP, TOT_EMP, EMP_PRSE, JOBS_1000, LOC_QUOTIENT, H_MEAN, A_MEAN, H_PCT10..H_PCT90,
  A_PCT10..A_PCT90, ANNUAL, HOURLY, ...). Numeric columns are numbers; footnote codes become null:
     *   wage estimate not available          -> null, listed in suppressed_fields
     **  employment estimate not available    -> null, listed in suppressed_fields
     #   wage at or above the top-code        -> null, listed in topcoded_fields (it is a floor, not missing)
     ~   under 0.5% of establishments report  -> null (PCT_RPT only)
  Added alongside: country="US", geo_level (nation/state/msa/nonmetro), occ_code_native,
  soc_code_harmonized, suppressed_flag (true if any employment or wage field was suppressed),
  topcoded_flag, source="BLS OEWS May 2025".

USAGE
  py -3 fetch_oews.py [--year 2025]
"""
import glob
import io
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import need_manual, out_path, raw_path, write_csv, write_json

YEAR = int(sys.argv[sys.argv.index("--year") + 1]) if "--year" in sys.argv else 2025
YY = str(YEAR)[2:]
SOURCE = f"BLS OEWS May {YEAR}"
FILES = {
    "nat": ("national", "national estimates"),
    "st": ("state", "state estimates"),
    "ma": ("metro/nonmetro", "metropolitan and nonmetropolitan area estimates"),
    "in4": ("industry", "national industry-specific estimates"),
}
NUMERIC = ["TOT_EMP", "EMP_PRSE", "JOBS_1000", "LOC_QUOTIENT", "PCT_TOTAL", "PCT_RPT",
           "H_MEAN", "A_MEAN", "MEAN_PRSE",
           "H_PCT10", "H_PCT25", "H_MEDIAN", "H_PCT75", "H_PCT90",
           "A_PCT10", "A_PCT25", "A_MEDIAN", "A_PCT75", "A_PCT90"]
EMP_FIELDS = {"TOT_EMP", "EMP_PRSE", "JOBS_1000", "LOC_QUOTIENT", "PCT_TOTAL"}
AREA_TYPE = {"1": "nation", "2": "state", "3": "territory", "4": "msa", "6": "nonmetro"}


def zip_path(key):
    p = raw_path("bls", f"oesm{YY}{key}.zip")
    if not os.path.exists(p):
        need_manual(p, f"https://www.bls.gov/oes/special-requests/oesm{YY}{key}.zip",
                    f"BLS OEWS May {YEAR} {FILES[key][1]}")
    return p


# Which data files to read inside each zip (file-name prefixes, case-insensitive). The metro zip holds
# MSA_ (metropolitan areas) AND BOS_ ("balance of state" = the nonmetropolitan areas); the industry
# zip holds nat3d/nat4d/... and natsector, of which only the sector file is needed.
MEMBERS = {"nat": ("national_",), "st": ("state_",), "ma": ("msa_", "bos_"), "in4": ("natsector_",)}


def read_zip_table(path, key):
    """DataFrame of the wanted data sheets inside a BLS OEWS zip (xlsx, or csv in some years), stacked."""
    import pandas as pd
    frames = []
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist()
                 if n.lower().endswith((".xlsx", ".xls", ".csv"))
                 and n.split("/")[-1].lower().startswith(MEMBERS[key])]
        if not names:
            raise RuntimeError(f"{path}: none of {MEMBERS[key]} inside ({z.namelist()})")
        for name in names:
            data = z.read(name)
            print(f"  reading {os.path.basename(path)} :: {name}")
            if name.lower().endswith(".csv"):
                df = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False)
            else:
                df = pd.read_excel(io.BytesIO(data), dtype=str, keep_default_na=False)
            df.columns = [str(c).strip().upper() for c in df.columns]
            frames.append(df)
    return pd.concat(frames, ignore_index=True)


def clean_row(r):
    out = {}
    supp, top = [], []
    for k, v in r.items():
        v = "" if v is None else str(v).strip()
        if k in NUMERIC:
            if v in ("*", "**"):
                out[k] = None
                supp.append(k)
            elif v == "#":
                out[k] = None
                top.append(k)
            elif v in ("~", ""):
                out[k] = None
            else:
                try:
                    f = float(v.replace(",", ""))
                    out[k] = int(f) if k == "TOT_EMP" else f
                except ValueError:
                    out[k] = None
                    supp.append(k)
        elif k in ("ANNUAL", "HOURLY"):
            out[k] = v.upper() == "TRUE"
        else:
            out[k] = v or None
    return out, supp, top


def normalize(df, level_hint):
    rows = []
    for rec in df.to_dict("records"):
        r, supp, top = clean_row(rec)
        at = AREA_TYPE.get(str(r.get("AREA_TYPE") or ""), level_hint)
        occ = r.get("OCC_CODE")
        r.update({
            "country": "US",
            "geo_level": at,
            "occ_code_native": occ,
            "soc_code_harmonized": occ,
            "suppressed_flag": bool(supp),
            "suppressed_fields": supp or None,
            "topcoded_flag": bool(top),
            "topcoded_fields": top or None,
            "emp_suppressed": any(f in EMP_FIELDS for f in supp),
            "source": SOURCE,
        })
        rows.append(r)
    return rows


def industry_mix(df):
    """National occupation employment by NAICS sector (I_GROUP == 'sector'), all ownerships.
    Feeds the QWI turnover proxy in Phase 2."""
    mix = {}
    df = df[(df["I_GROUP"].str.lower() == "sector")]
    if "OWN_CODE" in df.columns:
        # OWN_CODE 1235 = federal, state, local + private combined; prefer it, else sum what is there
        own = set(df["OWN_CODE"])
        if "1235" in own:
            df = df[df["OWN_CODE"] == "1235"]
    for rec in df.to_dict("records"):
        if (rec.get("O_GROUP") or "").lower() != "detailed":
            continue
        emp = (rec.get("TOT_EMP") or "").replace(",", "")
        if not emp.replace(".", "").isdigit():
            continue
        naics = rec["NAICS"].strip()
        mix.setdefault(rec["OCC_CODE"], {})
        mix[rec["OCC_CODE"]][naics] = mix[rec["OCC_CODE"]].get(naics, 0) + int(float(emp))
    return mix


def main():
    print(f"fetch_oews: May {YEAR}")
    rows = []
    for key, level in (("nat", "nation"), ("st", "state"), ("ma", "msa")):
        df = read_zip_table(zip_path(key), key)
        # cross-industry, all-ownership rows only (the area files are cross-industry already)
        if "I_GROUP" in df.columns:
            df = df[df["I_GROUP"].str.lower().isin(["cross-industry", ""])]
        if "OWN_CODE" in df.columns:
            df = df[df["OWN_CODE"].isin(["1235", ""])]
        part = normalize(df, level)
        print(f"    {key}: {len(part):,} rows")
        rows.extend(part)

    write_json(out_path(f"oews_{YEAR}_area.json"), rows)
    fields = list(dict.fromkeys(k for r in rows[:2000] for k in r))
    write_csv(out_path(f"oews_{YEAR}_area.csv"), rows, fields)

    mix = industry_mix(read_zip_table(zip_path("in4"), "in4"))
    write_json(out_path(f"oews_{YEAR}_industry_mix.json"),
               {"_meta": {"source": f"{SOURCE} national industry-specific, sector level, detailed occupations"},
                "mix": mix})
    n_supp = sum(r["suppressed_flag"] for r in rows)
    print(f"done: {len(rows):,} rows, {n_supp:,} with suppressed fields, {len(mix):,} occupations in industry mix")


if __name__ == "__main__":
    main()
