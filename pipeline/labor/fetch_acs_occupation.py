#!/usr/bin/env python3
r"""
fetch_acs_occupation.py  (Phase 1)
-----------------------
Where workers LIVE, by occupation: Census ACS 5-year table C24010 (Sex by Occupation for the
Civilian Employed Population 16 Years and Over), by county and by census tract.

C24010 is the finest occupation table the ACS publishes below state level (B24114, the ~525-line
detailed table, is null for counties and tracts). Its ~30 leaf occupation groups are the residence-
side supply measure, and the key for splitting metro OEWS employment across counties in Phase 2.

Male and female lines are summed per leaf group; margins of error combine as sqrt(moe_m^2 + moe_f^2).
The newest published ACS 5-year vintage is probed (2020-2024 as of Nov 2026), never pinned.

OUTPUT (out/):
    acs_occ_county.json / .csv   one row per county x occupation group (long), with share of employed
    acs_occ_tract.json           one row per tract: {GEOID, total, groups:{key: est}} (wide; ~85k tracts)
    acs_occ_groups.json          the group keys, ACS labels and SOC major-group prefixes they cover

USAGE
  py -3 fetch_acs_occupation.py            (needs the Census key in Projects/tool/refresh_config.json
                                            or CENSUS_API_KEY)
  py -3 fetch_acs_occupation.py --no-tracts
"""
import json
import math
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import census_key, http_get, norm_title, out_path, write_csv, write_json

TABLE = "C24010"

# ACS C24010 leaf group -> SOC 2018 code prefixes it covers. Used in Phase 2 to pick the residence
# share for each SOC. Matching is by longest prefix, so "47-5" (extraction) beats "47" (construction).
# Built from the Census occupation code list (Census 2018 occupation codes <-> SOC 2018).
SOC_PREFIX = {
    "management occupations": ["11"],
    "business and financial operations occupations": ["13"],
    "computer and mathematical occupations": ["15"],
    "architecture and engineering occupations": ["17"],
    "life physical and social science occupations": ["19"],
    "community and social service occupations": ["21"],
    "legal occupations": ["23"],
    "educational instruction and library occupations": ["25"],
    "arts design entertainment sports and media occupations": ["27"],
    "health diagnosing and treating practitioners and other technical occupations": ["29-1", "29-9"],
    "health technologists and technicians": ["29-2"],
    "healthcare support occupations": ["31"],
    "firefighting and prevention and other protective service workers including supervisors": ["33-1021", "33-2", "33-9"],
    "law enforcement workers including supervisors": ["33-1011", "33-1012", "33-3"],
    "food preparation and serving related occupations": ["35"],
    "building and grounds cleaning and maintenance occupations": ["37"],
    "personal care and service occupations": ["39"],
    "sales and related occupations": ["41"],
    "office and administrative support occupations": ["43"],
    "farming fishing and forestry occupations": ["45"],
    "construction and extraction occupations": ["47"],
    "installation maintenance and repair occupations": ["49"],
    "production occupations": ["51"],
    "transportation occupations": ["53-1", "53-2", "53-3", "53-4", "53-5", "53-6"],
    "material moving occupations": ["53-7"],
}


def latest_acs_year(key):
    import datetime
    y0 = datetime.date.today().year
    for y in range(y0, y0 - 5, -1):
        try:
            b = http_get(f"https://api.census.gov/data/{y}/acs/acs5?get=NAME&for=state:01&key={key}", timeout=60)
            if b.lstrip().startswith(b"["):
                return y
        except Exception:
            continue
    sys.exit("Could not reach the Census ACS API")


def groups(year):
    """Leaf occupation lines of C24010, paired male/female by label path."""
    meta = json.loads(http_get(f"https://api.census.gov/data/{year}/acs/acs5/groups/{TABLE}.json"))["variables"]
    est = {k: v["label"] for k, v in meta.items() if re.fullmatch(rf"{TABLE}_\d{{3}}E", k)}
    paths = {k: [p.strip().rstrip(":") for p in lab.split("!!")[1:]] for k, lab in est.items()}  # drop "Estimate"
    out = {}
    for k, p in paths.items():
        if len(p) < 3 or p[1] not in ("Male", "Female"):
            continue
        is_parent = any(len(q) > len(p) and q[:len(p)] == p for q in paths.values())
        if is_parent:
            continue
        key = norm_title(p[-1])
        g = out.setdefault(key, {"key": key, "label": p[-1], "path": " > ".join(p[2:]), "vars": {}})
        g["vars"][p[1].lower()] = k[:-1]                     # 'C24010_005'
    for g in out.values():
        g["soc_prefixes"] = SOC_PREFIX.get(g["key"])
        if g["soc_prefixes"] is None:
            print(f"  WARNING: ACS group '{g['label']}' has no SOC prefix mapping - check SOC_PREFIX")
    return dict(sorted(out.items()))


def fetch(year, key, geo, within=None):
    gvars = ["NAME", f"{TABLE}_001E"]
    url = (f"https://api.census.gov/data/{year}/acs/acs5?get=group({TABLE})&for={geo}"
           + (f"&in={within}" if within else "") + f"&key={key}")
    for attempt in range(4):
        try:
            rows = json.loads(http_get(url, timeout=300))
            hdr = rows[0]
            return [dict(zip(hdr, r)) for r in rows[1:]]
        except Exception as e:
            if attempt == 3:
                raise
            print(f"    retry {geo} {within}: {e}")
            time.sleep(5 * (attempt + 1))


def num(v):
    try:
        f = float(v)
        return None if f < 0 else f        # Census uses large negatives (-666666666) for n/a
    except (TypeError, ValueError):
        return None


def summarize(rec, grp):
    total = num(rec.get(f"{TABLE}_001E"))
    vals = {}
    for k, g in grp.items():
        m, f = g["vars"].get("male"), g["vars"].get("female")
        em, ef = num(rec.get(f"{m}E")) if m else None, num(rec.get(f"{f}E")) if f else None
        mm, mf = num(rec.get(f"{m}M")) if m else None, num(rec.get(f"{f}M")) if f else None
        est = None if em is None and ef is None else (em or 0) + (ef or 0)
        moe = None if mm is None and mf is None else round(math.sqrt((mm or 0) ** 2 + (mf or 0) ** 2))
        vals[k] = (est, moe, em, ef)
    return total, vals


def main():
    key = census_key()
    year = latest_acs_year(key)
    vintage = f"ACS {year - 4}-{year} 5-year"
    print(f"fetch_acs_occupation: {vintage}, table {TABLE}")
    grp = groups(year)
    print(f"  {len(grp)} leaf occupation groups")
    write_json(out_path("acs_occ_groups.json"), {"_meta": {"source": vintage, "table": TABLE}, "groups": grp}, compact=False)

    counties = fetch(year, key, "county:*")
    rows = []
    for rec in counties:
        geoid = rec["state"] + rec["county"]
        total, vals = summarize(rec, grp)
        for k, (est, moe, em, ef) in vals.items():
            rows.append({"country": "US", "geo_level": "county", "GEOID": geoid, "NAME": rec.get("NAME"),
                         "occ_group": k, "occ_label": grp[k]["label"], "soc_prefixes": grp[k]["soc_prefixes"],
                         "resident_workers": est, "resident_workers_moe": moe,
                         "resident_workers_male": em, "resident_workers_female": ef,
                         "total_employed": total,
                         "share_pct": round(est / total * 100, 3) if est is not None and total else None,
                         "source": f"Census {vintage}, {TABLE}"})
    print(f"  counties: {len(counties):,}  rows: {len(rows):,}")
    write_json(out_path("acs_occ_county.json"), rows)
    write_csv(out_path("acs_occ_county.csv"), [{**r, "soc_prefixes": "|".join(r["soc_prefixes"] or [])} for r in rows],
              list(rows[0].keys()))

    if "--no-tracts" in sys.argv:
        return
    states = sorted({r["state"] for r in counties})
    tracts = []
    for st in states:
        recs = fetch(year, key, "tract:*", f"state:{st}")
        for rec in recs:
            total, vals = summarize(rec, grp)
            tracts.append({"GEOID": rec["state"] + rec["county"] + rec["tract"], "total_employed": total,
                           "groups": {k: v[0] for k, v in vals.items() if v[0]}})
        print(f"    state {st}: {len(recs):,} tracts")
    write_json(out_path("acs_occ_tract.json"), {"_meta": {"source": f"Census {vintage}, {TABLE}",
                                                          "groups": list(grp)}, "tracts": tracts})
    print(f"done: {len(tracts):,} tracts")


if __name__ == "__main__":
    main()
