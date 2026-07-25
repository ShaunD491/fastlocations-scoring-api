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
        # EXPOSURE-NORMALIZED hazard columns. The NRI composite RISK_SCORE is loss-based, so it
        # scales with how much built value already exists -- a rural county scores "low risk" simply
        # because there's little to lose. For SITE SELECTION we want how exposed a NEW facility would
        # be, so we prefer the annualized loss RATIO percentile (loss per dollar exposed) and, failing
        # that, annualized hazard frequency.
        alr_cols = {c[:-10]: cols[c] for c in cols if c.endswith("_ALR_NPCTL")}
        afq_cols = {c[:-6]: cols[c] for c in cols if c.endswith("_AFREQ")}

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
            # exposure-normalized hazard exposure (what a NEW facility faces), 0-100 higher = worse.
            alr = [num(row.get(c)) for c in alr_cols.values()]
            alr = [v for v in alr if v is not None]
            if alr:
                alr.sort(reverse=True)
                top = alr[:3]                                    # worst perils drive siting decisions
                rec["exposure"] = round((sum(top) / len(top)) * 0.7 + (sum(alr) / len(alr)) * 0.3, 2)
                rec["exposure_src"] = "alr_npctl"
            else:
                afq = [num(row.get(c)) for c in afq_cols.values()]
                afq = [v for v in afq if v is not None]
                if afq:
                    rec["exposure_raw_freq"] = round(sum(afq), 3)  # ranked into a percentile below
                    rec["exposure_src"] = "afreq"
            if rec.get("risk") is not None or hz:
                out[fips] = rec

    # If no ALR percentiles existed, convert summed annualized frequency into a 0-100 percentile.
    if out and not any("exposure" in r for r in out.values()):
        freqs = sorted((r["exposure_raw_freq"], k) for k, r in out.items() if "exposure_raw_freq" in r)
        n = len(freqs)
        for i, (_, k) in enumerate(freqs):
            out[k]["exposure"] = round(i / max(n - 1, 1) * 100, 2)
    for r in out.values():
        r.pop("exposure_raw_freq", None)

    json.dump(out, open(OUT, "w", encoding="utf-8"), separators=(",", ":"))
    src = next((r.get("exposure_src") for r in out.values() if r.get("exposure_src")), "none")
    have = sum(1 for r in out.values() if r.get("exposure") is not None)
    print(f"Wrote {OUT}: {len(out)} counties | exposure signal: {src} ({have} counties)")
    print("  county    loss-based risk   exposure(new facility)")
    for fips, nm in (("22091", "St. Helena LA"), ("48201", "Harris TX"), ("12086", "Miami-Dade"),
                     ("06037", "Los Angeles"), ("39035", "Cuyahoga OH")):
        if fips in out:
            r = out[fips]
            print(f"  {fips} {nm:<16} risk={r.get('risk'):>6}   exposure={r.get('exposure')}")


if __name__ == "__main__":
    main()
