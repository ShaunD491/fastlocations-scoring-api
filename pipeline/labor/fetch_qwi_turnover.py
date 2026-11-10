#!/usr/bin/env python3
r"""
fetch_qwi_turnover.py  (Phase 1)
---------------------
Turnover, separations and hires by county x NAICS sector from the Census Quarterly Workforce
Indicators (QWI, LEHD), via the Census API (timeseries/qwi/sa, seasonally adjusted).

QWI is by INDUSTRY, not occupation. Phase 2 turns it into an occupation-level proxy by weighting
each sector's turnover with the occupation's national industry mix from OEWS.

What is fetched, per state, for every county and every NAICS sector (QWI's default sector level,
including "00" = all industries):
    TurnOvrS  turnover rate, stable jobs
    EmpS      stable (full-quarter) employment
    Emp       beginning-of-quarter employment
    HirA      all hires
    Sep       separations
Ownership: A00 (state & local government plus private); falls back to A05 (all private) where
a state publishes no A00 rows.

The newest quarters differ by state (states deliver UI wage records on different schedules), so
the script asks for a wide window and keeps each county's LAST 8 quarters that have data.

OUTPUT (out/):
    qwi_county_naics.json   one row per county x sector:
        {country, geo_level, GEOID, state, county, industry, ownercode,
         quarters:[...], TurnOvrS_avg, sep_rate_avg (Sep/Emp), hire_rate_avg (HirA/Emp),
         EmpS_avg, n_quarters, series:{quarter:{TurnOvrS, EmpS, Emp, HirA, Sep}}}
    qwi_county_naics.csv    the same without the quarterly series

USAGE
  py -3 fetch_qwi_turnover.py [--quarters 8]
"""
import datetime
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import census_key, http_get, out_path, write_csv, write_json

NQ = int(sys.argv[sys.argv.index("--quarters") + 1]) if "--quarters" in sys.argv else 8
VARS = ["TurnOvrS", "EmpS", "Emp", "HirA", "Sep"]
BASE = "https://api.census.gov/data/timeseries/qwi/sa"
SECTORS = ["00", "11", "21", "22", "23", "31-33", "42", "44-45", "48-49", "51", "52", "53", "54", "55",
           "56", "61", "62", "71", "72", "81", "92"]      # "00" = all industries; same codes as OEWS sectors
STATES = ["01", "02", "04", "05", "06", "08", "09", "10", "11", "12", "13", "15", "16", "17", "18", "19",
          "20", "21", "22", "23", "24", "25", "26", "27", "28", "29", "30", "31", "32", "33", "34", "35",
          "36", "37", "38", "39", "40", "41", "42", "44", "45", "46", "47", "48", "49", "50", "51", "53",
          "54", "55", "56"]


def window():
    today = datetime.date.today()
    q_now = (today.month - 1) // 3 + 1
    start_y = today.year - 3                 # QWI lags ~3 quarters; 3+ years back is ample for 8 quarters
    return f"{start_y}-Q1", f"{today.year}-Q{q_now}"


def get(st, owner, t0, t1, key):
    """All counties x sectors for one state over t0..t1, one calendar year per request: the API refuses
    queries estimated above 400,000 cells, which a multi-year window for a big state exceeds."""
    out = []
    for y in range(int(t0[:4]), int(t1[:4]) + 1):
        a = t0 if y == int(t0[:4]) else f"{y}-Q1"
        b = t1 if y == int(t1[:4]) else f"{y}-Q4"
        out.extend(_get(st, owner, a, b, key))
    return out


def _get(st, owner, t0, t1, key):
    # Name the sectors explicitly: with industry unfiltered the API sizes the query over every NAICS
    # level and refuses it (cell limit), even for one state and one year.
    ind = "".join(f"&industry={i}" for i in SECTORS)
    url = (f"{BASE}?get={','.join(VARS)}&for=county:*&in=state:{st}"
           f"&time=from%20{t0}%20to%20{t1}{ind}&ownercode={owner}&key={key}")
    for attempt in range(4):
        try:
            b = http_get(url, timeout=300)
            if not b.strip():
                return []                    # 204: no rows for this state/ownership
            rows = json.loads(b)
            return [dict(zip(rows[0], r)) for r in rows[1:]]
        except Exception as e:
            body = getattr(e, "read", lambda: b"")()[:200]
            if b"cell limit" in body or b"bounded" in body:
                print(f"    state {st} {t0}..{t1}: {body.decode(errors='replace')}")
                return []
            if attempt == 3:
                print(f"    FAILED state {st} {owner}: {e}")
                return []
            time.sleep(5 * (attempt + 1))


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main():
    key = census_key()
    t0, t1 = window()
    print(f"fetch_qwi_turnover: QWI {t0}..{t1}, last {NQ} quarters per county x sector")
    out, latest = [], {}
    for st in STATES:
        owner = "A00"
        recs = get(st, owner, t0, t1, key)
        if not recs:
            owner = "A05"
            recs = get(st, owner, t0, t1, key)
        series = {}
        for r in recs:
            k = (r["state"] + r["county"], r["industry"])
            series.setdefault(k, {})[r["time"]] = {v: f(r.get(v)) for v in VARS}
        for (geoid, ind), s in series.items():
            qs = sorted(q for q, v in s.items() if v["TurnOvrS"] is not None or v["Sep"] is not None)[-NQ:]
            if not qs:
                continue
            def avg(vals):
                vals = [x for x in vals if x is not None]
                return round(sum(vals) / len(vals), 4) if vals else None
            sep = avg([s[q]["Sep"] / s[q]["Emp"] for q in qs if s[q]["Sep"] is not None and s[q]["Emp"]])
            hire = avg([s[q]["HirA"] / s[q]["Emp"] for q in qs if s[q]["HirA"] is not None and s[q]["Emp"]])
            out.append({"country": "US", "geo_level": "county", "GEOID": geoid, "state": geoid[:2],
                        "county": geoid[2:], "industry": ind, "ownercode": owner,
                        "quarters": qs, "n_quarters": len(qs),
                        "TurnOvrS_avg": avg([s[q]["TurnOvrS"] for q in qs]),
                        "sep_rate_avg": sep, "hire_rate_avg": hire,
                        "EmpS_avg": avg([s[q]["EmpS"] for q in qs]),
                        "series": {q: s[q] for q in qs},
                        "source": f"Census QWI (LEHD), seasonally adjusted, ownership {owner}"})
        last = max((q for s in series.values() for q in s), default=None)
        latest[st] = last
        print(f"  state {st}: {len(recs):6,} records, owner {owner}, latest {last}")
    write_json(out_path("qwi_county_naics.json"),
               {"_meta": {"source": "Census QWI via api.census.gov timeseries/qwi/sa", "window": [t0, t1],
                          "quarters_kept": NQ, "latest_quarter_by_state": latest}, "rows": out})
    write_csv(out_path("qwi_county_naics.csv"), out,
              [k for k in out[0] if k not in ("series",)] if out else [])
    print(f"done: {len(out):,} county x sector rows")


if __name__ == "__main__":
    main()
