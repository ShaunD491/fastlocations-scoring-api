#!/usr/bin/env python3
r"""
build_occupation.py
-------------------
Pulls county OCCUPATION shares from the Census ACS 5-year subject table S2401 and writes
county_occupation.json for the scorer's skill_supply metric.

WHY: skill_profile was proxied by educational-attainment bands (no occupation data was loaded),
so choosing "engineers" vs "general labor" barely moved matches. This gives real occupation supply
per county -- "engineers" reads actual architecture-&-engineering employment share, etc.

SOURCE: Census ACS 2023 5-year, table S2401 (Occupation by Sex), column C01 = total estimate.
Line codes were validated to sum exactly to the civilian-employed total:
  001 total | 004 mgmt | 005 business/finance | 007 computer/math | 008 architecture/engineering |
  009 life/physical/social science | 010 education/legal/arts/media | 015 healthcare practitioners |
  017 health technologists | 019 healthcare support | 027 sales | 028 office/admin |
  031 construction/extraction | 032 install/maintenance/repair | 034 production |
  035 transportation | 036 material moving

USAGE (needs a free Census API key -> https://api.census.gov/data/key_signup.html):
    python build_occupation.py YOUR_CENSUS_KEY

Writes county_occupation.json: { "<fips5>": {"eng":<pct>, "comp":<pct>, ..., "_emp":<count>} }
Each value is that occupation group's share of the county's civilian employed pop 16+, in percent.
"""
import json, os, sys, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "county_occupation.json")

# S2401_C01 line code -> short group name used by the scorer
CODES = {
    "004": "mgmt", "005": "busfin", "007": "comp", "008": "eng", "009": "sci",
    "010": "edary", "015": "hlthpr", "017": "hlthtech", "019": "hlthsup",
    "027": "sales", "028": "office", "031": "construct", "032": "maint",
    "034": "prod", "035": "transport", "036": "material",
}
TOTAL = "001"


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: python build_occupation.py YOUR_CENSUS_API_KEY")
    key = sys.argv[1].strip()
    codes = [TOTAL] + list(CODES)
    getvars = ",".join(f"S2401_C01_{c}E" for c in codes)
    url = ("https://api.census.gov/data/2023/acs/acs5/subject"
           f"?get={getvars}&for=county:*&key={key}")
    print(f"Fetching S2401 occupation data for all counties ...")
    with urllib.request.urlopen(url, timeout=120) as r:
        rows = json.load(r)
    hdr, data = rows[0], rows[1:]
    idx = {h: i for i, h in enumerate(hdr)}
    si, ci = idx["state"], idx["county"]

    def col(code):
        return idx[f"S2401_C01_{code}E"]

    out = {}
    for row in data:
        fips = row[si] + row[ci]
        tot = row[col(TOTAL)]
        try:
            tot = int(tot)
        except (TypeError, ValueError):
            continue
        if tot <= 0:
            continue
        rec = {}
        for code, name in CODES.items():
            v = row[col(code)]
            try:
                v = int(v)
            except (TypeError, ValueError):
                continue
            if v >= 0:
                rec[name] = round(v / tot * 100.0, 2)
        if rec:
            rec["_emp"] = tot
            out[fips] = rec

    json.dump(out, open(OUT, "w", encoding="utf-8"), separators=(",", ":"))
    print(f"Wrote {OUT}: {len(out)} counties")
    a = out.get("06085", {})   # Santa Clara (tech) sanity check
    b = out.get("06107", {})   # Tulare (ag/production) sanity check
    print(f"  Santa Clara eng%={a.get('eng')} comp%={a.get('comp')} prod%={a.get('prod')}")
    print(f"  Tulare     eng%={b.get('eng')} comp%={b.get('comp')} prod%={b.get('prod')}")


if __name__ == "__main__":
    main()
