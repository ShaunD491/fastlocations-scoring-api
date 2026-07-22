#!/usr/bin/env python3
r"""
build_bea_cost.py
-----------------
Extracts county labor-cost signals from BEA Regional table CAINC30 ("Economic Profile")
and writes county_bea.json for the scorer's cost dimension.

WHY: the cost dimension's US labor-cost proxy was ESRI median HOUSEHOLD income (MEDHINC), which is
income at place of RESIDENCE -- it mixes in commuters, retirees, and investment income. BEA
"Earnings by place of work" per capita is what employers actually pay inside the county, and it
correlates only ~0.17 with MEDHINC, so it adds a genuinely new, employer-side labor-cost signal.
Per-capita personal income (residence, prosperity level) is also carried for reference.

SOURCE: BEA CAINC30, https://apps.bea.gov/regional/downloadzip.cfm (table CAINC30, all areas).
    LineCode 100 = Population; 110 = Per capita personal income; 180 = Earnings by place of work.

USAGE:
    python build_bea_cost.py "CAINC30__ALL_AREAS_1969_2024.csv"
Output county_bea.json: { "<fips5>": {"earn_pow_pc": <$>, "pcpi": <$>, "pcpi_growth10": <frac>} }
"""
import csv, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "county_bea.json")
LINES = {"100": "pop", "110": "pcpi", "180": "earn_pow"}   # BEA CAINC30 line codes


def num(x):
    x = (x or "").strip()
    if not x or x in ("(NA)", "(D)", "(L)", "(T)", "(NM)"):
        return None
    try:
        return float(x)
    except ValueError:
        return None


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "CAINC30__ALL_AREAS_1969_2024.csv")
    if not os.path.exists(src):
        sys.exit(f"CAINC30 file not found: {src}\nDownload table CAINC30 (all areas) from "
                 f"https://apps.bea.gov/regional/downloadzip.cfm")
    raw = {}
    with open(src, encoding="latin-1") as f:
        r = csv.reader(f)
        hdr = next(r)
        iF, iL = hdr.index("GeoFIPS"), hdr.index("LineCode")
        i_now, i_10y = hdr.index("2024"), hdr.index("2014")
        for row in r:
            if len(row) <= i_now:
                continue
            lc = row[iL].strip()
            if lc not in LINES:
                continue
            fips = row[iF].strip().strip('"').strip()
            if len(fips) != 5 or fips.endswith("000"):        # skip state/national aggregates
                continue
            raw.setdefault(fips, {})[LINES[lc]] = (num(row[i_now]), num(row[i_10y]))

    out = {}
    for fips, d in raw.items():
        pop = (d.get("pop") or (None, None))[0]
        pcpi, pcpi10 = d.get("pcpi") or (None, None)
        earn = (d.get("earn_pow") or (None, None))[0]           # thousands of dollars
        rec = {}
        if earn is not None and pop:
            rec["earn_pow_pc"] = round(earn * 1000.0 / pop, 1)   # employer earnings per resident
        if pcpi is not None:
            rec["pcpi"] = round(pcpi, 1)
        if pcpi and pcpi10:
            rec["pcpi_growth10"] = round(pcpi / pcpi10 - 1.0, 4)
        if rec:
            out[fips] = rec

    json.dump(out, open(OUT, "w", encoding="utf-8"), separators=(",", ":"))
    have_earn = sum(1 for v in out.values() if "earn_pow_pc" in v)
    print(f"Wrote {OUT}: {len(out)} counties ({have_earn} with earnings/place-of-work)")


if __name__ == "__main__":
    main()
