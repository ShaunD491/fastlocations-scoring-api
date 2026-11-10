#!/usr/bin/env python3
"""
build_dash_labor.py - per-organization occupation profiles for the EDO dashboards and FastPitch briefings
(Phase 5 of Occupation_Labor_Data_Plan.md).

Reads the runtime labor data (Projects/labor_data, built by build_labor_runtime.py), the OEWS state rows
(out/oews_2025_area.json) and the dashboard geography of every organization (dash/data/trends.json), and
writes

    dash/data/labor/<OBJECTID>.json   {geo, level, country, currency, modelled, note, cols, rows}
    dash/data/labor/_titles.json      {"us": {soc: title}, "ca": {noc: title}}
    dash/data/labor/_sectors.json     {naics_sector: [[soc, share_of_sector_emp], ...]} (US national mix)
    dash/data/labor/_meta.json        build date, sources, counts

rows: [code, emp, lq, wage_median, growth_outlook] for every occupation with employment, largest first.

Geography, following the dashboard's own trend geography:
    msa        -> the OEWS metro area (published)
    city/county-> the OEWS metro area holding the county (the area people commute from); a county
                  outside any metro uses its own modelled county figures
    region     -> sum of its counties (modelled); location quotients recomputed for the whole region
    state      -> OEWS statewide (published)
    csd (= CD) -> the census division; cma -> the economic region of its core CSD; province -> sum of CDs
Public data only; deterministic. Run:  py -3 build_dash_labor.py
"""
import collections
import datetime
import gzip
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.abspath(os.path.join(HERE, "..", ".."))
ROOT = os.path.abspath(os.path.join(PROJ, ".."))
LABOR = os.path.join(PROJ, "labor_data")
OUT_DIR = os.path.join(ROOT, "dash", "data", "labor")
TRENDS = os.path.join(ROOT, "dash", "data", "trends.json")
OEWS_AREA = os.path.join(HERE, "out", "oews_2025_area.json")
MIX = os.path.join(HERE, "out", "oews_2025_industry_mix.json")
CMA = os.path.join(PROJ, "cma_to_csd_mapping.json")
CMA_CD = os.path.join(PROJ, "ca_cma.json")          # core census division(s) -> CMA name
STATE_ABBR = {"01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO", "09": "CT", "10": "DE", "11": "DC",
              "12": "FL", "13": "GA", "15": "HI", "16": "ID", "17": "IL", "18": "IN", "19": "IA", "20": "KS", "21": "KY",
              "22": "LA", "23": "ME", "24": "MD", "25": "MA", "26": "MI", "27": "MN", "28": "MS", "29": "MO", "30": "MT",
              "31": "NE", "32": "NV", "33": "NH", "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND", "39": "OH",
              "40": "OK", "41": "OR", "42": "PA", "44": "RI", "45": "SC", "46": "SD", "47": "TN", "48": "TX", "49": "UT",
              "50": "VT", "51": "VA", "53": "WA", "54": "WV", "55": "WI", "56": "WY", "72": "PR"}
PROV = {"10": "NL", "11": "PE", "12": "NS", "13": "NB", "24": "QC", "35": "ON", "46": "MB", "47": "SK", "48": "AB",
        "59": "BC", "60": "YT", "61": "NT", "62": "NU"}


def fold(s):
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return "".join(ch for ch in s if ch.isalnum())


def load_json(p):
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def load_occ(country):
    """{code: {"county"|"area" or "cd"|"er": {geo: row}}} for every occupation file."""
    d = os.path.join(LABOR, "us" if country == "US" else "ca")
    out = {}
    for f in sorted(os.listdir(d)):
        if f.endswith(".json.gz"):
            with gzip.open(os.path.join(d, f), "rt", encoding="utf-8") as fh:
                out[f[:-8]] = json.load(fh)
    return out


def r1(v, n=0):
    return None if v is None else (round(v, n) if n else int(round(v)))


def single(occ, level, geo):
    """Rows for one published (or modelled) geography: [code, emp, lq, wage, growth]."""
    rows = []
    for code, d in occ.items():
        r = d.get(level, {}).get(geo)
        if r and r[0]:
            rows.append([code, r[0], r[1], r[2], r[5]])
    return rows


def aggregate(occ, level, geos):
    """Sum several geographies. LQ for the whole: sum(emp) / sum(emp/lq) (each emp/lq is the area's total
    employment times the national share, so the sum is exact); wage and growth employment-weighted."""
    geos = set(geos)
    rows = []
    for code, d in occ.items():
        emp = base = wsum = wemp = gsum = gemp = 0.0
        for g, r in d.get(level, {}).items():
            if g not in geos or not r[0]:
                continue
            e = r[0]
            emp += e
            if r[1]:
                base += e / r[1]
            if r[2] is not None:
                wsum += r[2] * e
                wemp += e
            if r[5] is not None:
                gsum += r[5] * e
                gemp += e
        if emp:
            rows.append([code, r1(emp), round(emp / base, 2) if base else None,
                         r1(wsum / wemp, -1) if wemp else None, round(gsum / gemp, 1) if gemp else None])
    return rows


def state_rows(oews, fips):
    rows = []
    for r in oews:
        if r.get("AREA_TYPE") != "2" or str(r.get("AREA")).zfill(2) != fips or r.get("O_GROUP") != "detailed":
            continue
        if not r.get("TOT_EMP"):
            continue
        rows.append([r["OCC_CODE"], r["TOT_EMP"], r.get("LOC_QUOTIENT"), r.get("A_MEDIAN"), None])
    return rows


def main():
    idx = load_json(os.path.join(LABOR, "index.json"))
    trends = load_json(TRENDS)["orgs"]
    print("loading occupation files ...", flush=True)
    us, ca = load_occ("US"), load_occ("CA")
    c2a, cd2er = idx["county_to_area"], idx["cd_to_er"]
    # every county (metro AND nonmetro) -> its OEWS area, for the dashboard role search (labor/place)
    xw = load_json(os.path.join(HERE, "out", "crosswalks", "county_to_area.json"))
    county_oews = {f: c.get("oews_area") for f, c in xw.items() if c.get("oews_area")}
    areas, ers = idx["areas"], idx["ers"]

    oews = None
    if any(o.get("level") == "state" for o in trends.values()):
        print("loading OEWS state rows ...", flush=True)
        oews = [r for r in load_json(OEWS_AREA) if r.get("AREA_TYPE") == "2"]
        # state growth comes from the runtime rows of any county in the state (state projections)
    cma = {str(c["cma_code"]): c for c in load_json(CMA)} if os.path.exists(CMA) else {}
    with open(CMA_CD, encoding="utf-8", errors="replace") as fh:
        cma_cd = json.load(fh)

    os.makedirs(OUT_DIR, exist_ok=True)
    for f in os.listdir(OUT_DIR):
        if f.endswith(".json"):
            os.remove(os.path.join(OUT_DIR, f))

    counts = collections.Counter()
    skipped = []
    for oid, t in sorted(trends.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 0):
        lv, geo = t.get("level"), str(t.get("geoid") or "")
        country = "CA" if str(t.get("country", "")).lower() == "canada" else "US"
        rec = {"country": country, "currency": "CAD" if country == "CA" else "USD", "level": None, "geo": None,
               "modelled": False, "note": ""}
        rows = []
        if country == "US":
            if lv == "msa" and geo in areas:
                rows = single(us, "area", geo)
                rec.update(level="metro", geo=areas[geo][0])
                rec["note"] = "Published metro-area figures (BLS OEWS May 2025)."
            elif lv in ("city", "county"):
                fips = geo.zfill(5)
                a = c2a.get(fips)
                if a and a in areas:
                    rows = single(us, "area", a)
                    rec.update(level="metro", geo=areas[a][0],
                               note="Published figures for the metro area people commute from (BLS OEWS May 2025).")
                else:
                    rows = single(us, "county", fips)
                    rec.update(level="county", geo=t.get("region_name") or t.get("name"), modelled=True,
                               note="County figures modelled from the surrounding OEWS area and where residents work (ACS).")
            elif lv == "region":
                fl = [str(x).zfill(5) for x in (t.get("county_fips") or [])]
                rows = aggregate(us, "county", fl)
                rec.update(level="region", geo=t.get("name"), modelled=True,
                           note=f"Sum of the {len(fl)} count{'y' if len(fl) == 1 else 'ies'} served, modelled from OEWS areas and ACS.")
            elif lv == "state" and oews is not None:
                rows = state_rows(oews, geo.zfill(2))
                rec.update(level="state", geo=t.get("name"), note="Published statewide figures (BLS OEWS May 2025).")
                # growth: the state's projections, carried on every runtime row in that state
                st = STATE_ABBR.get(geo.zfill(2))
                g = {}
                for code, d in us.items():
                    for a, r in d.get("area", {}).items():
                        if areas.get(a, [None, None, None])[2] == st and r[5] is not None:
                            g[code] = r[5]
                            break
                for r in rows:
                    r[4] = g.get(r[0])
        else:
            if lv == "csd":
                rows = single(ca, "cd", geo)
                rec.update(level="census division", geo=t.get("region_name") or t.get("name"), modelled=True,
                           note="Census division figures (2021 Census, occupation counts modelled from minor groups).")
            elif lv == "cma":
                c = cma.get(geo)
                cds = sorted({s["csd_code"][:4] for s in (c or {}).get("census_subdivisions", [])})
                if not cds:      # not in the CSD mapping: the CMA's core division(s) by name
                    want = fold(str(t.get("name", "")).split(" metro area")[0])
                    pr = str(t.get("name", "")).rsplit(", ", 1)[-1]
                    cds = sorted(cd for cd, nm in cma_cd.items()
                                 if fold(nm) == want and PROV.get(cd[:2]) == pr)
                er = cd2er.get(cds[0]) if cds else None
                if c and len(cds) > 1:
                    rows = aggregate(ca, "cd", cds)
                    rec.update(level="metro", geo=t.get("name"), modelled=True,
                               note=f"Sum of the {len(cds)} census divisions in the metro area (2021 Census, modelled).")
                elif cds:
                    rows = single(ca, "cd", cds[0])
                    rec.update(level="metro", geo=t.get("name"), modelled=True,
                               note="Census division holding the metro area (2021 Census, modelled).")
                elif er:
                    rows = single(ca, "er", er)
                    rec.update(level="economic region", geo=ers[er][0], modelled=True, note="Economic region (2021 Census).")
            elif lv == "province":
                cds = [cd for cd in cd2er if cd[:2] == geo.zfill(2)]
                rows = aggregate(ca, "cd", cds)
                rec.update(level="province", geo=t.get("name"), modelled=True,
                           note="Sum of the province's census divisions (2021 Census, modelled).")
        # what the dashboard role search ranks: one OEWS area / economic region when the org has one,
        # and the state(s) or province(s) it sits in
        area, states = None, []
        if country == "US":
            if lv == "msa":
                area = geo if geo in areas else None
            elif lv in ("city", "county"):
                area = county_oews.get(geo.zfill(5))
            elif lv == "region":
                fl = [str(x).zfill(5) for x in (t.get("county_fips") or [])]
                al = {county_oews.get(f) for f in fl} - {None}
                area = al.pop() if len(al) == 1 else None
                states = sorted({STATE_ABBR.get(f[:2]) for f in fl} - {None})
            elif lv == "state":
                states = [STATE_ABBR.get(geo.zfill(2))] if STATE_ABBR.get(geo.zfill(2)) else []
            if area and not states and area in areas:
                states = [areas[area][2]]
        else:
            if lv == "csd":
                area = cd2er.get(geo)
            elif lv == "cma":
                c = cma.get(geo)
                cds = sorted({s_["csd_code"][:4] for s_ in (c or {}).get("census_subdivisions", [])}) or \
                    sorted(cd for cd, nm in cma_cd.items() if fold(nm) == fold(str(t.get("name", "")).split(" metro area")[0])
                           and PROV.get(cd[:2]) == str(t.get("name", "")).rsplit(", ", 1)[-1])
                area = cd2er.get(cds[0]) if cds else None
            if lv == "province":
                states = [PROV.get(geo.zfill(2))] if PROV.get(geo.zfill(2)) else []
            elif area and area in ers:
                states = [ers[area][2]]
        rec["search"] = {"area": area, "country": country, "states": states,
                         "area_name": (areas.get(area, [None])[0] if country == "US" else (ers.get(area, [None])[0] if area else None))}
        rows = [r for r in rows if r[1]]
        if len(rows) < 20:
            skipped.append((oid, lv, geo, len(rows)))
            continue
        rows.sort(key=lambda r: (-r[1], r[0]))
        for r in rows:
            r[1] = int(round(r[1]))
            if r[3] is not None:
                r[3] = int(round(r[3], -1))
        rec["total_emp"] = sum(r[1] for r in rows)
        rec["cols"] = ["code", "emp", "lq", "wage_median", "growth_pct"]
        rec["rows"] = rows
        with open(os.path.join(OUT_DIR, f"{oid}.json"), "w", encoding="utf-8") as fh:
            json.dump(rec, fh, separators=(",", ":"))
        counts[f"{country}:{rec['level']}"] += 1

    # titles and the sector occupation mix
    with open(os.path.join(OUT_DIR, "_titles.json"), "w", encoding="utf-8") as fh:
        json.dump({"us": idx["us_titles"], "ca": idx["ca_titles"], "soc_to_noc": idx["soc_to_noc"]}, fh,
                  separators=(",", ":"), ensure_ascii=False)
    mix = load_json(MIX)["mix"]
    tot = collections.Counter()
    for soc, by in mix.items():
        for s, e in by.items():
            tot[s] += e or 0
    sectors = {}
    for s in tot:
        lst = sorted(((soc, by.get(s) or 0) for soc, by in mix.items() if soc in idx["us_titles"]), key=lambda x: (-x[1], x[0]))
        sectors[s] = [[soc, round(e / tot[s], 4)] for soc, e in lst[:25] if e]
    with open(os.path.join(OUT_DIR, "_sectors.json"), "w", encoding="utf-8") as fh:
        json.dump(sectors, fh, separators=(",", ":"))
    meta = {"built": datetime.date.today().isoformat(), "orgs": sum(counts.values()), "by_level": dict(counts),
            "skipped": [list(s) for s in skipped],
            "sources": {"US": "BLS OEWS May 2025; county/region figures modelled with Census ACS 2020-2024; growth: "
                              "state and national employment projections",
                        "CA": "Statistics Canada 2021 Census (98-10-0593, 98-10-0591); ESDC Job Bank wages 2025; "
                              "ESDC COPS 2026-2035"}}
    with open(os.path.join(OUT_DIR, "_meta.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=1)
    print(json.dumps({k: v for k, v in meta.items() if k != "skipped"}, indent=1))
    print("skipped:", skipped[:20], "..." if len(skipped) > 20 else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
