#!/usr/bin/env python3
r"""
build_land_cost.py
------------------
Adds LAND PURCHASE PRICE to the real-estate dimension, which until now measured property tax rate
and nothing else.

WHY THIS MATTERS: scoring real estate purely on property tax produced a real distortion. Upstate New
York scored near zero -- Rochester pays 2.41% and Syracuse 2.22%, among the highest rates in the
country -- while Santa Clara scored well on 0.68%, an artefact of California's Proposition 13. The
model was penalising Upstate NY for its taxes while giving it no credit at all for cheap land, which
is the region's single strongest selling point. Land at roughly $4,600/acre in Monroe County against
$25,000 in Fulton County GA is a bigger real number for most projects than the tax differential.

SOURCE: USDA NASS 2022 Census of Agriculture, average farm real estate value per acre by county.

READ THIS BEFORE TRUSTING IT -- what the measure is and is not:
This is FARM real estate. It is a proxy for raw land cost, not a price for a zoned, served industrial
site, and it carries no information about buildings. It tracks the land-price gradient well (farmland
is dear where development pressure is high and cheap where it is not) but a site consultant pricing an
improved parcel should not read these as asking prices.

Two consequences are handled deliberately:
  * A handful of dense urban counties report farm values orders of magnitude above the median --
    Richmond County NY (Staten Island) at $2.55M/acre from a tiny number of parcels. The scorer
    percentile-RANKS this metric rather than using its magnitude, so an unreliable extreme costs a
    county its rank position and nothing more. No winsorising is applied for that reason.
  * Counties with no farm acreage at all are absent from the source. They are written as null, never
    zero, so the scorer damps them instead of scoring them as free land.

USAGE:
    python build_land_cost.py "frontieracre-price-per-acre-by-county-2022.csv"
Writes county_land_cost.json:  { "<fips>": {"land_per_acre": n, "ref": "USDA NASS 2022"} }
"""
import csv, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "county_land_cost.json")


def main():
    src = (sys.argv[1] if len(sys.argv) > 1 else
           os.path.join(HERE, "frontieracre-price-per-acre-by-county-2022.csv"))
    if not os.path.exists(src):
        sys.exit(f"land cost file not found: {src}")

    out = {}
    with open(src, encoding="utf-8-sig", errors="replace") as fh:
        rd = csv.DictReader(fh)
        col = next((c for c in rd.fieldnames if "per acre" in c.lower()), None)
        fips_col = next((c for c in rd.fieldnames if c.strip().upper() == "FIPS"), None)
        if not col or not fips_col:
            sys.exit(f"unexpected columns: {rd.fieldnames}")
        for r in rd:
            fips = (r.get(fips_col) or "").strip().zfill(5)
            try:
                v = float(str(r[col]).replace(",", "").replace("$", ""))
            except (TypeError, ValueError, KeyError):
                continue
            if v > 0:
                out[fips] = {"land_per_acre": round(v, 2), "ref": "USDA NASS 2022"}

    json.dump(out, open(OUT, "w", encoding="utf-8"), separators=(",", ":"))
    vals = sorted(v["land_per_acre"] for v in out.values())
    print(f"Wrote {OUT}: {len(out)} counties")
    print(f"  $/acre  min {vals[0]:,.0f}   median {vals[len(vals)//2]:,.0f}   max {vals[-1]:,.0f}")
    try:
        feat = json.load(open(os.path.join(HERE, "county_features.json"), encoding="utf-8"))
        missing = sorted(set(feat) - set(out))
        print(f"  joins to county_features.json: {len(set(out) & set(feat))}/{len(feat)}")
        print(f"  {len(missing)} counties have no farm acreage and stay null: {missing[:8]}")
    except FileNotFoundError:
        pass
    for f, n in (("36055", "Monroe NY"), ("36067", "Onondaga NY"), ("06085", "Santa Clara CA"),
                 ("48453", "Travis TX"), ("13121", "Fulton GA")):
        if f in out:
            print(f"  {n:<16} ${out[f]['land_per_acre']:,.0f}/acre")


if __name__ == "__main__":
    main()
