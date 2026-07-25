#!/usr/bin/env python3
r"""
build_us_nri.py
---------------
Builds a US county natural-hazard / resilience signal from FEMA's National Risk Index (NRI),
county table, for the scorer.

WHY: the model had drought + groundwater but no broad hazard/resilience signal (flood, wildfire,
hurricane, tornado, earthquake, etc.). FEMA NRI is the standard county-level composite.

SOURCE: FEMA National Risk Index, "NRI_Table_Counties.csv" (inside NRI_Table_Counties.zip).
    https://hazards.fema.gov/nri/  (Data Resources -> County table)

USAGE (runs locally with plain Python; unzip first or point at the CSV):
    python build_us_nri.py "NRI_Table_Counties.csv"

Writes county_nri.json:
  { "<fips5>": {"risk": <0-100 composite>, "eal_total": <$/yr>, "resilience": <score>,
                "social_vuln": <score>, "hazards": {"<HAZ>": <risk score>, ...}} }
Higher `risk` = more hazard-exposed (worse). Hazards keeps each peril's risk-index score.
"""
import csv, io, json, os, re, sys, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "county_nri.json")


def open_text(src):
    """Return a text stream for a .csv path, or the counties CSV inside a .zip."""
    if src.lower().endswith(".zip"):
        z = zipfile.ZipFile(src)
        member = next((n for n in z.namelist() if n.lower().endswith(".csv") and "count" in n.lower()),
                      None) or next((n for n in z.namelist() if n.lower().endswith(".csv")), None)
        if not member:
            sys.exit(f"no CSV found inside {src}")
        return io.TextIOWrapper(z.open(member), encoding="utf-8-sig", errors="replace")
    return open(src, encoding="utf-8-sig", errors="replace")


def num(x):
    x = (str(x) or "").strip().replace(",", "")
    if x in ("", "NA", "N/A", "nan"):
        return None
    try:
        return float(x)
    except ValueError:
        return None


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "NRI_Table_Counties.zip")
    if not os.path.exists(src):
        sys.exit(f"NRI file not found: {src}\nPass the path to NRI_Table_Counties.zip or its CSV.")
    with open_text(src) as f:
        rd = csv.DictReader(f)
        cols = {c.upper(): c for c in rd.fieldnames}

        def col(*names):
            for n in names:
                if n.upper() in cols:
                    return cols[n.upper()]
            return None

        c_fips = col("STCOFIPS", "STCO_FIPS", "FIPS")
        c_stfp = col("STATEFIPS"); c_cofp = col("COUNTYFIPS")
        c_risk = col("RISK_SCORE"); c_eal = col("EAL_VALT", "EAL_VALB", "EAL_SCORE")
        c_resl = col("RESL_SCORE"); c_sovi = col("SOVI_SCORE")
        # per-hazard risk-index score columns are named "<HAZ>_RISKS"
        haz_cols = {c[:-6]: cols[c] for c in cols if c.endswith("_RISKS")}

        out = {}
        for row in rd:
            fips = (row.get(c_fips) or "").strip() if c_fips else ""
            if not fips and c_stfp and c_cofp:
                fips = (row.get(c_stfp) or "").strip().zfill(2) + (row.get(c_cofp) or "").strip().zfill(3)
            fips = re.sub(r"\D", "", fips)
            if len(fips) == 4:            # some exports drop a leading zero
                fips = "0" + fips
            if len(fips) != 5:
                continue
            rec = {}
            if c_risk: rec["risk"] = num(row.get(c_risk))
            if c_eal:  rec["eal_total"] = num(row.get(c_eal))
            if c_resl: rec["resilience"] = num(row.get(c_resl))
            if c_sovi: rec["social_vuln"] = num(row.get(c_sovi))
            hz = {}
            for haz, c in haz_cols.items():
                v = num(row.get(c))
                if v is not None:
                    hz[haz] = round(v, 2)
            if hz:
                rec["hazards"] = hz
            if rec.get("risk") is not None or hz:
                out[fips] = rec

    json.dump(out, open(OUT, "w", encoding="utf-8"), separators=(",", ":"))
    print(f"Wrote {OUT}: {len(out)} counties")
    for fips in ("06037", "48201", "12086"):   # LA, Harris TX, Miami-Dade
        if fips in out:
            r = out[fips]
            top = sorted((r.get("hazards") or {}).items(), key=lambda kv: kv[1], reverse=True)[:3]
            print(f"  {fips}: risk={r.get('risk')} top hazards={top}")


if __name__ == "__main__":
    main()
