#!/usr/bin/env python3
r"""
build_us_innovation.py
----------------------
Builds a county-level R&D INDUSTRY signal for the US innovation term.

WHY NOT PATENTS: patent grants per capita would be the ideal measure, but there is no longer a usable
county-level source. USPTO's PTMT county tables stop at CY2015, and USPTO itself cautions that county
attribution is unreliable because many inventor city/state combinations map to more than one county.
PatentsView, which did publish disambiguated county FIPS, migrated to USPTO's Open Data Portal and its
bulk files now return HTTP 403 without a portal key. If you obtain a data.uspto.gov API key, patents
become available and are worth revisiting -- see the note at the bottom of this file.

WHAT THIS MEASURES INSTEAD: the share of a county's business establishments that are Scientific
Research and Development Services (NAICS 5417). This is deliberately distinct from the existing
occupation-share measure in scorer.knowledge_economy(). Occupation shares answer "do technical people
live here"; this answers "is R&D actually performed here as a line of business" -- a commuter suburb
full of engineers and a county with a corporate research campus look very different on this metric.

WHY ESTABLISHMENT SHARE AND NOT EMPLOYMENT: County Business Patterns suppresses employment counts for
small county-industry cells to protect respondent confidentiality, but establishment counts are never
suppressed. Using establishments avoids a systematic hole in exactly the small and mid-size counties
this engine is meant to surface. Employment is still recorded where published, for reference only.

TRUE ZERO vs MISSING -- these are different and are encoded differently:
  * A county that appears in the all-industry query but has no NAICS 5417 row has genuinely zero R&D
    establishments. That is a real measurement, recorded as 0.0, and it is the majority case
    (roughly 2,600 of 3,246 counties).
  * A county absent from the all-industry query has no CBP data at all. That is a coverage gap and is
    recorded as null, never 0, so the scorer damps rather than penalizes it.

SOURCE: US Census Bureau, County Business Patterns 2023 (latest vintage; 2024 not yet published).
    https://api.census.gov/data/2023/cbp

USAGE (needs the same free Census API key used by build_occupation.py):
    python build_us_innovation.py YOUR_CENSUS_KEY

Writes county_innovation.json:
    { "<fips>": {"rd_estab_share": pct, "rd_emp_share": pct|null,
                 "estab": n, "emp": n|null, "ref": "CBP2023"} }
"""
import json, os, sys, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "county_innovation.json")
YEAR = "2023"
RD_NAICS = "5417"          # Scientific Research and Development Services


def fetch(naics, key):
    url = (f"https://api.census.gov/data/{YEAR}/cbp"
           f"?get=ESTAB,EMP&for=county:*&NAICS2017={naics}&key={key}")
    with urllib.request.urlopen(url, timeout=180) as r:
        rows = json.load(r)
    head, out = rows[0], {}
    ie, im = head.index("ESTAB"), head.index("EMP")
    ist, ico = head.index("state"), head.index("county")
    for row in rows[1:]:
        fips = f"{row[ist]}{row[ico]}"
        def num(v):
            try:
                v = float(v)
            except (TypeError, ValueError):
                return None
            return v if v >= 0 else None          # CBP uses negative codes as suppression flags
        out[fips] = (num(row[ie]), num(row[im]))
    return out


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: python build_us_innovation.py YOUR_CENSUS_API_KEY\n"
                 "get a free key at https://api.census.gov/data/key_signup.html")
    key = sys.argv[1].strip()
    print(f"Fetching County Business Patterns {YEAR} ...")
    total = fetch("00", key)
    rd = fetch(RD_NAICS, key)
    print(f"  all industries: {len(total)} counties   NAICS {RD_NAICS}: {len(rd)} counties")

    out, zeros, gaps = {}, 0, 0
    for fips, (t_estab, t_emp) in total.items():
        if not t_estab:
            gaps += 1
            continue
        r_estab, r_emp = rd.get(fips, (0.0, None))
        rec = {"rd_estab_share": round(100.0 * (r_estab or 0.0) / t_estab, 4),
               "rd_emp_share": (round(100.0 * r_emp / t_emp, 4)
                                if (r_emp is not None and t_emp) else None),
               "estab": int(r_estab or 0),
               "emp": int(r_emp) if r_emp is not None else None,
               "ref": f"CBP{YEAR}"}
        out[fips] = rec
        if not r_estab:
            zeros += 1

    json.dump(out, open(OUT, "w", encoding="utf-8"), separators=(",", ":"))
    nz = [v["rd_estab_share"] for v in out.values() if v["rd_estab_share"] > 0]
    nz.sort()
    print(f"\nWrote {OUT}: {len(out)} counties")
    print(f"  {zeros} with a genuine zero (no R&D establishments)   {gaps} skipped as coverage gaps")
    if nz:
        print(f"  of the {len(nz)} with R&D presence: median {nz[len(nz)//2]:.2f}% of establishments, "
              f"max {nz[-1]:.2f}%")
    for f, nm in (("06085", "Santa Clara CA"), ("25017", "Middlesex MA"), ("51059", "Fairfax VA"),
                  ("48453", "Travis TX"), ("39035", "Cuyahoga OH")):
        if f in out:
            v = out[f]
            print(f"  {nm:<16} {v['rd_estab_share']:>5.2f}% of establishments  "
                  f"({v['estab']} R&D establishments, employment {v['emp']})")


# REVISIT IF YOU GET A USPTO OPEN DATA PORTAL KEY (https://data.uspto.gov):
# patent grants per capita by county measures innovation OUTPUT rather than industry presence, and
# would be a stronger signal than either this or occupation shares. The join key is the disambiguated
# inventor location county FIPS. Treat the first-named inventor's residence at grant as the location,
# which is the convention USPTO's own regional reports use.
if __name__ == "__main__":
    main()
