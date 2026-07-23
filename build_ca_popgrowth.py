#!/usr/bin/env python3
r"""
build_ca_popgrowth.py
---------------------
Refreshes the Canadian demographics growth signal from StatCan's annual population estimates by
census division, replacing the stale 2016->2021 census growth with recent (e.g. 2021->latest) growth.

WHY: the CA demographics dimension uses `pop_growth` from the 2021 Census (2016-21) -- it misses the
2024 reality (e.g. Kitchener-Cambridge-Waterloo ~4.9% in 2024). This computes recent multi-year
growth per CD from the annual estimates.

SOURCE: StatCan table 17-10-0152-01 "Population estimates, July 1, by census division, 2021
boundaries". Download the CSV (Download options -> CSV) from
    https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=1710015201

USAGE:
    python build_ca_popgrowth.py "17100152.csv"
Writes ca_popgrowth.json: { "<cduid>": {"growth_pct": <float>, "from": <year>, "to": <year>, "pop": <int>} }
Filters to total population (all ages, both genders). CDUID is taken from DGUID (last 4 digits).
"""
import csv, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "ca_popgrowth.json")


def find_col(hdr, *needles):
    for i, h in enumerate(hdr):
        hl = h.strip().lower()
        if any(n in hl for n in needles):
            return i
    return None


def is_total(s):
    s = (s or "").strip().lower()
    return ("all ages" in s or s in ("total", "both sexes")
            or s.startswith("total") or "all persons" in s)


def cduid_from(dguid, geo):
    m = re.search(r"(\d{4})\s*$", str(dguid or ""))     # CD DGUID ends in the 4-digit CDUID
    if m:
        return m.group(1)
    m = re.match(r"\s*(\d{4})\b", str(geo or ""))         # some exports prefix "3530 Waterloo"
    return m.group(1) if m else None


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "17100152.csv")
    if not os.path.exists(src):
        sys.exit(f"StatCan CSV not found: {src}\nDownload table 17-10-0152 (CSV) from "
                 f"https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=1710015201")
    with open(src, encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    hdr = rows[0]
    iRef = find_col(hdr, "ref_date", "reference period")
    iDg = find_col(hdr, "dguid")
    iGeo = find_col(hdr, "geo")
    iVal = find_col(hdr, "value")
    iAge = find_col(hdr, "age")
    iSex = find_col(hdr, "gender", "sex")
    if None in (iRef, iVal) or (iDg is None and iGeo is None):
        sys.exit(f"unexpected columns: {hdr}")

    series = {}   # cduid -> {year: pop}
    for r in rows[1:]:
        if len(r) <= max(x for x in (iRef, iVal, iDg, iGeo, iAge, iSex) if x is not None):
            continue
        if iAge is not None and not is_total(r[iAge]):
            continue
        if iSex is not None and not is_total(r[iSex]):
            continue
        cd = cduid_from(r[iDg] if iDg is not None else "", r[iGeo] if iGeo is not None else "")
        if not cd:
            continue
        ym = re.search(r"(\d{4})", str(r[iRef]))
        v = (r[iVal] or "").strip().replace(",", "")
        if not ym or not v:
            continue
        try:
            series.setdefault(cd, {})[int(ym.group(1))] = int(float(v))
        except ValueError:
            continue

    out = {}
    for cd, yv in series.items():
        yrs = sorted(yv)
        if len(yrs) < 2:
            continue
        to = yrs[-1]
        frm = min((y for y in yrs if y >= to - 4), default=yrs[0])   # ~4-year window (e.g. 2021->latest)
        if frm == to:
            frm = yrs[0]
        if yv.get(frm):
            out[cd] = {"growth_pct": round((yv[to] / yv[frm] - 1) * 100, 2),
                       "from": frm, "to": to, "pop": yv[to]}

    json.dump(out, open(OUT, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print(f"Wrote {OUT}: {len(out)} census divisions")
    for cd in ("3530", "3520", "4811"):
        if cd in out:
            print(f"  {cd}: {out[cd]}")


if __name__ == "__main__":
    main()
