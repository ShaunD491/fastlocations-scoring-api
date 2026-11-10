#!/usr/bin/env python3
"""
build_property_labor.py - the labor market behind every Property Explorer listing (Phase 5 of
Occupation_Labor_Data_Plan.md).

Each listing in properties/properties_all.json is placed in the labor area people commute from:
    U.S.    -> its OEWS metro or nonmetro area (published BLS figures)
    Canada  -> its economic region (2021 Census, modelled)
Placement: the listing's county + state when given (exact), otherwise its city via the bundled place
gazetteers (Projects/us_places.json, ca_places.json) and a point-in-polygon test against the county /
economic-region boundaries from build_labor_geo.py. Listings that cannot be placed get no labor panel.

Writes properties/labor_areas.json:
    {built, groups: {US: [[prefix, label]], CA: [...]},
     areas:  {code: {n, c, lvl, mod, tot, g: [[emp, typical_pay] per group], top: [[title, emp, lq] x5]}},
     county: {"<state>|<county>": code}, place: {"<state>|<city>": code},
     source: {"<dataset label>": code}  (fallback: the listing organization's own area, single-place orgs only)}
Keys are normalised exactly as properties.js does (labKey). Run with  py -3 -P build_property_labor.py
(-P, not -I, so the user-installed shapely loads). Rerun after build_labor_runtime.py or a property refresh.
"""
import collections
import datetime
import json
import os
import re
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)      # -P leaves the script's folder off sys.path
from build_dash_labor import LABOR, PROJ, ROOT, load_json, load_occ  # noqa: E402

PROPS = os.path.join(ROOT, "properties", "properties_all.json")
OUT = os.path.join(ROOT, "properties", "labor_areas.json")
GEO = os.path.join(HERE, "out", "geo")
C2A = os.path.join(HERE, "out", "crosswalks", "county_to_area.json")

# occupation groups shown on a listing (industrial / site-selection focus)
GROUPS = {
    "US": [("51-", "Production"), ("53-", "Transportation & material moving"), ("49-", "Installation, maintenance & repair"),
           ("47-", "Construction trades"), ("17-", "Engineers & technicians"), ("15-", "Computer & math"),
           ("43-", "Office & administrative")],
    "CA": [("9", "Manufacturing & utilities"), ("7", "Trades, transport & equipment operators"),
           ("2", "Natural & applied sciences"), ("1", "Business, finance & administration")],
}
STATES = {"AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
          "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
          "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
          "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
          "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
          "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
          "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island",
          "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
          "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming", "PR": "Puerto Rico"}
PROVS = {"NL": "Newfoundland and Labrador", "PE": "Prince Edward Island", "NS": "Nova Scotia", "NB": "New Brunswick",
         "QC": "Quebec", "ON": "Ontario", "MB": "Manitoba", "SK": "Saskatchewan", "AB": "Alberta",
         "BC": "British Columbia", "YT": "Yukon", "NT": "Northwest Territories", "NU": "Nunavut"}
PROV_CODE = {"10": "NL", "11": "PE", "12": "NS", "13": "NB", "24": "QC", "35": "ON", "46": "MB", "47": "SK", "48": "AB",
             "59": "BC", "60": "YT", "61": "NT", "62": "NU"}
COUNTY_WORDS = re.compile(r"\b(city and borough|census area|county|parish|borough|municipality|municipio)\b")


def lab_key(s):
    """Mirror of labKey() in properties.js."""
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    s = COUNTY_WORDS.sub("", s)
    return re.sub(r"[^a-z0-9]", "", s)


def norm_place(x):
    """Mirror of scorer._norm (the gazetteer key)."""
    x = unicodedata.normalize("NFKD", str(x)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", x)


def area_summary(code, country, name, lvl, mod, occ, titles):
    level = "area" if country == "US" else "er"
    rows = []
    for c, d in occ.items():
        r = d.get(level, {}).get(code)
        if r and r[0]:
            rows.append((c, r[0], r[1], r[2]))
    if len(rows) < 20:
        return None
    tot = sum(r[1] for r in rows)
    g = []
    for pre, _ in GROUPS[country]:
        sel = [r for r in rows if r[0].startswith(pre)]
        emp = sum(r[1] for r in sel)
        w = [(r[3], r[1]) for r in sel if r[3] is not None]
        we = sum(e for _, e in w)
        g.append([int(round(emp)), int(round(sum(v * e for v, e in w) / we, -2)) if we else None])
    floor = max(100 if country == "CA" else 50, tot * 0.0008)
    sig = sorted((r for r in rows if r[2] is not None and r[1] >= floor), key=lambda r: (-r[2], -r[1]))[:5]
    return {"n": name, "c": country, "lvl": lvl, "mod": mod, "tot": int(round(tot)),
            "g": g, "top": [[titles.get(r[0], r[0]), int(round(r[1])), round(r[2], 2)] for r in sig]}


def main():
    try:
        from shapely.geometry import Point, shape
        from shapely.strtree import STRtree
    except ImportError:
        sys.exit("shapely is needed: run with py -3 -P (user site-packages)")
    idx = load_json(os.path.join(LABOR, "index.json"))
    c2a = load_json(C2A)
    props = load_json(PROPS)
    us_places = load_json(os.path.join(PROJ, "us_places.json"))
    ca_places = load_json(os.path.join(PROJ, "ca_places.json"))
    print("loading occupation files ...", flush=True)
    us, ca = load_occ("US"), load_occ("CA")

    # county lookup by "<state full name>|<county>"
    county_area = {}
    for fips, c in c2a.items():
        if c.get("oews_area"):
            county_area[lab_key(c["state_name"]) + "|" + lab_key(c["county_name"])] = (fips, c["oews_area"])
    # boundaries for city points
    cty = load_json(os.path.join(GEO, "labor_counties_us.geojson"))["features"]
    cty_geoms = [shape(f["geometry"]) for f in cty]
    cty_tree = STRtree(cty_geoms)
    ers = load_json(os.path.join(GEO, "labor_er_ca.geojson"))["features"]
    er_geoms = [shape(f["geometry"]) for f in ers]
    er_tree = STRtree(er_geoms)
    st_full = {lab_key(v): k for k, v in STATES.items()}
    pr_full = {lab_key(v): k for k, v in PROVS.items()}
    pr_full["quebec"] = "QC"

    def locate(tree, geoms, feats, lat, lon):
        pt = Point(lon, lat)
        for i in tree.query(pt):
            if geoms[i].contains(pt):
                return feats[i]["properties"]
        return None

    county_map, place_map, used = {}, {}, set()
    stats = collections.Counter()
    seen_city = set()
    for r in props["rows"]:
        city, county, state = r[3], r[4], r[5]
        sk = lab_key(state)
        if not sk:
            stats["no state"] += 1
            continue
        if county and sk in st_full:
            ck = sk + "|" + lab_key(county)
            hit = county_area.get(ck)
            if hit:
                county_map[ck] = hit[1]
                used.add(("US", hit[1]))
                stats["by county"] += 1
                continue
        if not city:
            stats["unplaced"] += 1
            continue
        pk = sk + "|" + lab_key(city)
        if pk in place_map:
            stats["by city"] += 1
            continue
        if pk in seen_city:
            stats["unplaced"] += 1
            continue
        seen_city.add(pk)
        code = None
        if sk in st_full:
            ll = us_places.get(st_full[sk] + "|" + norm_place(city))
            if ll:
                p = locate(cty_tree, cty_geoms, cty, ll[0], ll[1])
                if p and p.get("area_code"):
                    code = ("US", p["area_code"])
        elif sk in pr_full:
            ab = pr_full[sk]
            ll = ca_places.get(ab + "|" + norm_place(city))
            if ll:
                p = locate(er_tree, er_geoms, ers, ll[0], ll[1])
                if p:
                    code = ("CA", p["geo_code"])
        if code:
            place_map[pk] = code[1]
            used.add(code)
            stats["by city"] += 1
        else:
            stats["unplaced"] += 1

    # fallback for listings with no usable county or city: the listing organization's own area, when the
    # organization covers one place (city / county / metro / census division), not a state or multi-county region
    trends = load_json(os.path.join(ROOT, "dash", "data", "trends.json"))["orgs"]
    datasets = load_json(os.path.join(ROOT, "properties", "datasets.json"))["datasets"]
    cd2er = idx["cd_to_er"]
    source_map = {}
    for ds in datasets:
        t = trends.get(str(ds.get("dashId") or ""))
        if not t:
            continue
        lv, geo = t.get("level"), str(t.get("geoid") or "")
        code = None
        if lv == "msa" and geo in idx["areas"]:
            code = ("US", geo)
        elif lv in ("city", "county") and geo.zfill(5) in c2a and c2a[geo.zfill(5)].get("oews_area"):
            code = ("US", c2a[geo.zfill(5)]["oews_area"])
        elif lv == "csd" and geo in cd2er:
            code = ("CA", cd2er[geo])
        if code:
            source_map[ds["label"]] = code[1]
            used.add(code)
    for r in props["rows"]:
        if r[10] in source_map:
            stats["source fallback available"] += 1

    areas = {}
    for country, code in sorted(used):
        if country == "US":
            a = idx["areas"].get(code)
            if not a:
                continue
            nm = a[0]
            lvl = "nonmetro area" if a[1] == "nonmetro" else "metro area"
            s = area_summary(code, "US", nm, lvl, False, us, idx["us_titles"])
        else:
            e = idx["ers"].get(code)
            if not e:
                continue
            nm = e[0] + ", " + PROV_CODE.get(code[:2], e[2] if len(e) > 2 else "")
            s = area_summary(code, "CA", nm, "economic region", True, ca, idx["ca_titles"])
        if s:
            areas[code] = s
    county_map = {k: v for k, v in county_map.items() if v in areas}
    place_map = {k: v for k, v in place_map.items() if v in areas}
    out = {"built": datetime.date.today().isoformat(),
           "groups": {c: [list(g) for g in v] for c, v in GROUPS.items()},
           "sources": {"US": "BLS Occupational Employment and Wage Statistics, May 2025 (metro and nonmetro areas)",
                       "CA": "Statistics Canada 2021 Census (economic regions, modelled from census divisions); ESDC Job Bank wages 2025"},
           "areas": areas, "county": county_map, "place": place_map,
           "source": {k: v for k, v in source_map.items() if v in areas}}
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, separators=(",", ":"), ensure_ascii=False)
    print(f"areas {len(areas)}  county keys {len(county_map)}  place keys {len(place_map)}  sources {len(out['source'])}  "
          f"{os.path.getsize(OUT) / 1e3:.0f} KB")
    print(dict(stats))
    return 0


if __name__ == "__main__":
    sys.exit(main())
