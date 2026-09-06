#!/usr/bin/env python3
r"""
build_county_dai.py
-------------------
Builds county_dai.json, the DAI score for every US county, from DAI.csv.

WHAT THIS IS
The DAI score (0-100, higher is better) was supplied as a hand-maintained county table
(NAME, STATE, FIPS, DAI Score). Until this builder existed the same numbers lived inside
county_features.json under the name `critical_thinking`, where the scorer used them as one
metric of the workforce dimension. As of this file DAI is a dimension of its own with a fixed
default weight (see scorer.DEFAULT_WEIGHTS["dai"]) and this is its only ingestion path, so a
new DAI.csv is a rebuild, not a hand-edit of the features file.

WHAT THE SOURCE LOOKS LIKE, AND WHAT IS DONE ABOUT IT
  * FIPS may lack the leading zero (1001 for Autauga). Zero-padded to 5 digits.
  * ~110 fully blank trailer rows, one row for FIPS 00000, and 17 rows for Puerto Rico and the
    US Virgin Islands (72xxx / 78xxx) that read "#DIV/0!". All skipped, all counted in _meta.
  * Connecticut is keyed by the eight pre-2022 counties (09001-09015). The model runs on the nine
    2022 planning regions (09110-09190, see migrate_ct_geography.py), so those eight rows cannot
    be joined. The planning-region values that the 2022 migration derived from this same data are
    CARRIED FORWARD from county_features.json (`critical_thinking`) and listed in _meta.carried.
    If a future DAI.csv is keyed by planning region the carry-forward simply stops being needed.
  * Alaska boroughs and census areas are not in the source at all (31 counties in the model have
    no value). They are written as absent, never as zero: the scorer treats a missing dimension as
    a data gap and renormalises the other weights rather than penalising the county.

USAGE
    python build_county_dai.py DAI.csv
    python build_county_dai.py            (defaults to DAI.csv next to this script)
Writes county_dai.json: { "<fips>": {"dai": n, "ref": "DAI.csv"} , "_meta": {...} }
"""
import csv, datetime, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "county_dai.json")
FEATURES = os.path.join(HERE, "county_features.json")


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "DAI.csv")
    if not os.path.exists(src):
        sys.exit(f"DAI source file not found: {src}")

    out, skipped, unjoined = {}, {"blank": 0, "non_numeric": [], "out_of_range": []}, []
    with open(src, encoding="utf-8-sig", errors="replace", newline="") as fh:
        rd = csv.DictReader(fh)
        cols = {c.strip().lower(): c for c in (rd.fieldnames or [])}
        fips_col = cols.get("fips")
        val_col = next((c for k, c in cols.items() if "dai" in k), None)
        if not fips_col or not val_col:
            sys.exit(f"unexpected columns: {rd.fieldnames}")
        for r in rd:
            fips = (r.get(fips_col) or "").strip()
            raw = (r.get(val_col) or "").strip()
            if not fips and not raw:
                skipped["blank"] += 1
                continue
            fips = fips.zfill(5)
            try:
                v = float(raw.replace(",", ""))
            except ValueError:
                skipped["non_numeric"].append(fips)   # "#DIV/0!" for PR / USVI
                continue
            if fips == "00000" or not (0 <= v <= 100):
                skipped["out_of_range"].append(fips)
                continue
            out[fips] = {"dai": round(v, 4), "ref": "DAI.csv"}

    try:
        feat = json.load(open(FEATURES, encoding="utf-8"))
    except FileNotFoundError:
        feat = None

    carried = []
    if feat:
        unjoined = sorted(k for k in out if k not in feat)
        for k in unjoined:
            del out[k]
        for k, f in feat.items():
            if k not in out and f.get("critical_thinking") is not None:
                out[k] = {"dai": f["critical_thinking"], "ref": "carried from county_features.json"}
                carried.append(k)

    vals = sorted(v["dai"] for v in out.values())
    if len(vals) < 3000:
        sys.exit(f"only {len(vals)} counties parsed from {src}; refusing to write a thin file")

    out["_meta"] = {
        "source": os.path.basename(src),
        "source_mtime": datetime.datetime.fromtimestamp(os.path.getmtime(src)).strftime("%Y-%m-%d"),
        "built": datetime.date.today().isoformat(),
        "counties": len(vals),
        "carried": carried,
        "unjoined_source_fips": unjoined,
        "skipped": {"blank_rows": skipped["blank"], "non_numeric": skipped["non_numeric"],
                    "out_of_range": skipped["out_of_range"]},
        "direction": "higher is better",
    }
    json.dump(out, open(OUT, "w", encoding="utf-8"), separators=(",", ":"))

    print(f"Wrote {OUT}: {len(vals)} counties")
    print(f"  DAI  min {vals[0]}   median {vals[len(vals)//2]}   max {vals[-1]}")
    print(f"  skipped: {skipped['blank']} blank rows, {len(skipped['non_numeric'])} non-numeric "
          f"({skipped['non_numeric'][:3]}...), {len(skipped['out_of_range'])} out of range/00000")
    if feat:
        print(f"  joins to county_features.json: {len(vals)}/{len(feat)}")
        print(f"  {len(unjoined)} source rows had no county in the model (old CT counties): {unjoined}")
        print(f"  {len(carried)} counties carried forward from county_features.json: {carried}")
        missing = sorted(k for k in feat if k not in out)
        print(f"  {len(missing)} counties have no DAI and stay null: {missing[:6]}...")
    for f, n in (("11001", "District of Columbia"), ("06085", "Santa Clara CA"),
                 ("48201", "Harris TX"), ("21051", "Clay KY"), ("48393", "Roberts TX")):
        if f in out:
            print(f"  {n:<22} {out[f]['dai']}")


if __name__ == "__main__":
    main()
