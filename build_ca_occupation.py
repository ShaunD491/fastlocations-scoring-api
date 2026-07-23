#!/usr/bin/env python3
r"""
build_ca_occupation.py
----------------------
Builds Canadian census-division occupation shares from StatCan's 2021 Census occupation-by-CD table,
giving the Canadian skill_supply real occupational data (replacing the degree-share proxy).

WHY: US skill profiles use real occupation counts (Census S2401); Canada only had a bachelor's-share
proxy that went null for trades/labor. This provides NOC broad-category shares per CD so picking
"engineers", "skilled trades", "production" etc. actually differentiates Canadian locations.

SOURCE: StatCan table 98-10-0471 "Place of work status by occupation broad category ... census
divisions". Download the MACHINE-READABLE full table (Download options -> CSV -> the -eng.zip;
inside is 98100471.csv, ~3.3GB). Streamed, not loaded into memory.
    https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=9810047101

USAGE:
    python build_ca_occupation.py "98100471.csv"
Writes ca_occupation.json: { "<cduid>": {"noc0":<pct>,...,"noc9":<pct>, "_emp":<count>} }
Filtered to Total age / Total gender / Total place-of-work; value = Total work activity. Census
divisions only (DGUID starts 2021A0003; CDUID = last 4 digits). NOC broad codes 0-9.
"""
import csv, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "ca_occupation.json")


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "98100471.csv")
    if not os.path.exists(src):
        sys.exit(f"StatCan occupation CSV not found: {src}\nDownload 98-10-0471 (machine-readable CSV) "
                 f"from https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=9810047101")
    with open(src, encoding="utf-8-sig") as fh:
        r = csv.reader(fh)
        hdr = next(r)
        iDg, iOcc, iAge, iSex, iPow = 2, 3, 4, 5, 6
        iVal = next((i for i, h in enumerate(hdr) if "Total - Work activity" in h), 8)
        data = {}
        for row in r:
            if len(row) <= iVal:
                continue
            if (row[iAge] != "Total - Age" or row[iSex] != "Total - Gender"
                    or row[iPow] != "Total - Place of work status"):
                continue
            dg = row[iDg]
            if not dg.startswith("2021A0003"):        # census divisions only
                continue
            code = row[iOcc][:1]                        # '0'..'9', or 'T' for Total
            v = (row[iVal] or "").strip().replace(",", "")
            if not v:
                continue
            try:
                data.setdefault(dg[-4:], {})[code] = int(float(v))
            except ValueError:
                continue

    out = {}
    for cd, d in data.items():
        tot = d.get("T") or sum(d.get(str(i), 0) for i in range(10))
        if not tot:
            continue
        rec = {f"noc{i}": round(d.get(str(i), 0) / tot * 100, 2) for i in range(10)}
        rec["_emp"] = tot
        out[cd] = rec

    json.dump(out, open(OUT, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print(f"Wrote {OUT}: {len(out)} census divisions")
    for cd in ("3530", "3520"):
        if cd in out:
            o = out[cd]
            print(f"  {cd}: sciences(2)={o['noc2']}% trades(7)={o['noc7']}% mfg(9)={o['noc9']}% emp={o['_emp']}")


if __name__ == "__main__":
    main()
