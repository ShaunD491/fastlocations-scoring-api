#!/usr/bin/env python3
r"""
build_labor_metrics.py  (Phase 2)
----------------------
Derived labor metrics for every geography x occupation, US and Canada, from the Phase 1 outputs.

OUTPUTS (out/), each as columnar JSON {"_meta", "fields", "rows":[[...]]} plus a CSV:
    labor_metrics_cbsa.json     US OEWS areas: 393 metropolitan areas (CBSA codes) + 137 nonmetro areas
    labor_metrics_county.json   US counties, MODELLED from the area figures (modelled=true)
    labor_metrics_ca_er.json    Canadian economic regions (76)
    labor_metrics_ca_cd.json    Canadian census divisions (293) - the scorer's Canadian geography
  (Columnar because the county table has ~1.5M rows; a list of dicts would be ~4x larger.)

METRICS
  emp               US: OEWS TOT_EMP (jobs located in the area). County: area emp split across the
                    area's counties in proportion to ACS RESIDENT workers in the occupation's ACS group
                    (modelled=true: it spreads jobs by where workers live). CA: 2021 Census employed
                    by NOC (residence basis, emp_source="census_2021"); ER and CD unit-group counts are
                    modelled from census-division minor-group counts x the province's unit/minor split.
  lq                location quotient = local share / national share (US: BLS LOC_QUOTIENT where
                    published, else computed; county and CA: computed).
  wage_p25/median/p75  annual, native currency (USD / CAD). wage_index = local median / the SAME
                    country's national median for the occupation (comparable without an exchange rate).
                    US counties inherit their area's wages. CA wages: Job Bank ER -> province -> Canada
                    fallback, recorded in wage_level. Top-coded OEWS wages are null with wage_topcoded.
  resident_workers  supply where people live. US: ACS C24010 residents in the occupation's group,
                    apportioned to the occupation by the area's workplace mix within that group
                    (resident_workers_group keeps the unapportioned ACS count). CA: = emp (census is
                    residence based).
  competition_ratio is local emp / the client's requested headcount, so it is computed at scoring time
                    (Phase 3); emp is the numerator stored here.
  growth_outlook    projected 10-year % change: US state (Projections Central) where available, else
                    BLS national; CA COPS national. growth_source says which.
  turnover_proxy    PROXY. QWI stable-job turnover (TurnOvrS) by NAICS sector, weighted by the
                    occupation's national industry mix (OEWS natsector). Sector rates come from the finest
                    level with data: county -> OEWS area -> state -> national (turnover_level).
                    OEWS sector 99 (government) uses QWI 92 (public administration). Alaska and
                    Michigan have no QWI and fall back to national rates. Canada: null (no source).

Every row carries country, occ_code_native, soc_code_harmonized, modelled, and the Canadian rows carry
comparability="comparable, not identical".

USAGE
  py -3 build_labor_metrics.py
"""
import collections
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import OUT, XW, load_json, out_path, today, write_json

FIELDS_US = ["country", "geo_level", "geo_code", "geo_name", "state", "area_code",
             "occ_code_native", "soc_code_harmonized", "occ_title",
             "emp", "emp_source", "modelled", "lq",
             "wage_p25", "wage_median", "wage_p75", "wage_median_hourly", "wage_topcoded", "wage_index", "currency",
             "resident_workers", "resident_workers_group", "acs_group",
             "growth_outlook", "growth_source",
             "turnover_proxy", "turnover_level", "turnover_coverage",
             "TOT_EMP", "LOC_QUOTIENT", "A_PCT25", "A_MEDIAN", "A_PCT75", "H_MEDIAN"]
FIELDS_CA = ["country", "geo_level", "geo_code", "geo_name", "prov",
             "occ_code_native", "soc_code_harmonized", "occ_title",
             "emp", "emp_source", "modelled", "lq",
             "wage_p25", "wage_median", "wage_p75", "wage_low", "wage_high", "wage_median_hourly",
             "wage_level", "wage_index", "currency",
             "resident_workers", "growth_outlook", "growth_source", "future_conditions",
             "turnover_proxy", "comparability"]
PROV = {"10": "NL", "11": "PE", "12": "NS", "13": "NB", "24": "QC", "35": "ON", "46": "MB", "47": "SK",
        "48": "AB", "59": "BC", "60": "YT", "61": "NT", "62": "NU"}
ST_ABBR = {"01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO", "09": "CT", "10": "DE",
           "11": "DC", "12": "FL", "13": "GA", "15": "HI", "16": "ID", "17": "IL", "18": "IN", "19": "IA",
           "20": "KS", "21": "KY", "22": "LA", "23": "ME", "24": "MD", "25": "MA", "26": "MI", "27": "MN",
           "28": "MS", "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH", "34": "NJ", "35": "NM",
           "36": "NY", "37": "NC", "38": "ND", "39": "OH", "40": "OK", "41": "OR", "42": "PA", "44": "RI",
           "45": "SC", "46": "SD", "47": "TN", "48": "TX", "49": "UT", "50": "VT", "51": "VA", "53": "WA",
           "54": "WV", "55": "WI", "56": "WY", "72": "PR", "78": "VI", "66": "GU"}
ABBR_ST = {v: k for k, v in ST_ABBR.items()}


def opt(name):
    p = os.path.join(OUT, name)
    return load_json(p) if os.path.exists(p) else None


def rnd(x, n=3):
    return None if x is None else round(x, n)


def write_table(name, fields, rows, meta):
    write_json(out_path(name), {"_meta": meta, "fields": fields, "rows": [[r.get(f) for f in fields] for r in rows]})
    path = out_path(name.replace(".json", ".csv"))
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(fields)
        for r in rows:
            w.writerow([r.get(f) for f in fields])
    print(f"  wrote {os.path.relpath(path, os.path.dirname(OUT))}  ({len(rows):,} rows)")


# =============================================================================== US
class Turnover:
    """Sector turnover by county / area / state / nation (EmpS-weighted), and the occupation proxy."""

    def __init__(self, qwi_rows, c2a):
        self.county = collections.defaultdict(dict)
        acc = {"area": collections.defaultdict(lambda: collections.defaultdict(lambda: [0.0, 0.0])),
               "state": collections.defaultdict(lambda: collections.defaultdict(lambda: [0.0, 0.0])),
               "nation": collections.defaultdict(lambda: collections.defaultdict(lambda: [0.0, 0.0]))}
        for r in qwi_rows:
            t, w = r.get("TurnOvrS_avg"), r.get("EmpS_avg")
            if t is None:
                continue
            self.county[r["GEOID"]][r["industry"]] = t
            if not w:
                continue
            area = (c2a.get(r["GEOID"]) or {}).get("oews_area")
            for lvl, key in (("area", area), ("state", r["GEOID"][:2]), ("nation", "US")):
                if key:
                    a = acc[lvl][key][r["industry"]]
                    a[0] += t * w
                    a[1] += w
        self.lvl = {lvl: {k: {s: v[0] / v[1] for s, v in d.items() if v[1]} for k, d in m.items()}
                    for lvl, m in acc.items()}

    def proxy(self, mix, chain):
        """mix {sector: emp}; chain [(level, rates)] finest first. Returns (proxy, level_of_most_weight, coverage)."""
        if not mix:
            return None, None, None
        tot = sum(mix.values())
        num = den = 0.0
        by_level = collections.Counter()
        for s, w in mix.items():
            s = "92" if s == "99" else s
            for lvl, rates in chain:
                if rates and s in rates:
                    num += w * rates[s]
                    den += w
                    by_level[lvl] += w
                    break
        if not den:
            return None, None, 0.0
        return round(num / den, 4), by_level.most_common(1)[0][0], round(den / tot, 3)


def us(xw_c2a, proj_nat, proj_st):
    print("US: loading OEWS, ACS, QWI")
    oews = load_json(out_path("oews_2025_area.json"))
    mix = load_json(out_path("oews_2025_industry_mix.json"))["mix"]
    acs_groups = load_json(out_path("acs_occ_groups.json"))["groups"]
    acs = load_json(out_path("acs_occ_county.json"))
    qwi = load_json(out_path("qwi_county_naics.json"))["rows"]
    turn = Turnover(qwi, xw_c2a)

    # SOC -> ACS group by longest prefix
    prefixes = sorted(((p, g) for g, v in acs_groups.items() for p in (v["soc_prefixes"] or [])),
                      key=lambda x: -len(x[0]))
    grp_cache = {}

    def group_of(soc):
        if soc not in grp_cache:
            grp_cache[soc] = next((g for p, g in prefixes if soc.startswith(p)), None)
        return grp_cache[soc]

    res = collections.defaultdict(dict)          # county -> group -> residents
    res_tot = {}
    for r in acs:
        res[r["GEOID"]][r["occ_group"]] = r["resident_workers"] or 0.0
        res_tot[r["GEOID"]] = r["total_employed"] or 0.0

    nat, areas, area_meta = {}, collections.defaultdict(dict), {}
    for r in oews:
        if r["O_GROUP"] not in ("detailed", "total"):
            continue
        if r["geo_level"] == "nation":
            nat[r["OCC_CODE"]] = r
        elif r["geo_level"] in ("msa", "nonmetro"):
            areas[r["AREA"]][r["OCC_CODE"]] = r
            area_meta[r["AREA"]] = (r["AREA_TITLE"], r["geo_level"], r["PRIM_STATE"])
    nat_total = nat["00-0000"]["TOT_EMP"]

    counties_of = collections.defaultdict(list)
    for fips, c in xw_c2a.items():
        if c.get("oews_area"):
            counties_of[c["oews_area"]].append(fips)

    def growth(soc, st_fips):
        g = ((proj_st or {}).get(st_fips) or {}).get(soc)
        if g and g.get("growth_pct") is not None:
            return g["growth_pct"], "state"
        g = (proj_nat or {}).get(soc)
        if g and g.get("growth_pct") is not None:
            return g["growth_pct"], "national"
        return None, None

    def wage_fields(r, natr):
        top = "A_MEDIAN" in (r.get("topcoded_fields") or []) or "H_MEDIAN" in (r.get("topcoded_fields") or [])
        med = r.get("A_MEDIAN")
        nmed = natr.get("A_MEDIAN") if natr else None
        idx = None
        if med and nmed:
            idx = med / nmed
        elif r.get("H_MEDIAN") and natr and natr.get("H_MEDIAN"):
            idx = r["H_MEDIAN"] / natr["H_MEDIAN"]
        return {"wage_p25": r.get("A_PCT25"), "wage_median": med, "wage_p75": r.get("A_PCT75"),
                "wage_median_hourly": r.get("H_MEDIAN"), "wage_topcoded": top, "wage_index": rnd(idx),
                "currency": "USD"}

    area_rows, county_rows = [], []
    print(f"US: {len(areas)} areas")
    for area, occs in areas.items():
        title, level, prim = area_meta[area]
        st_fips = ABBR_ST.get(prim, None)
        cfs = counties_of.get(area, [])
        # residents by ACS group across the area, and workplace emp by group in the area
        a_res = collections.Counter()
        for c in cfs:
            for g, v in res.get(c, {}).items():
                a_res[g] += v
        a_res_tot = sum(res_tot.get(c, 0) for c in cfs)
        a_grp_emp = collections.Counter()
        for soc, r in occs.items():
            if soc != "00-0000" and r.get("TOT_EMP"):
                a_grp_emp[group_of(soc)] += r["TOT_EMP"]
        a_total = (occs.get("00-0000") or {}).get("TOT_EMP")
        area_chain = [("area", turn.lvl["area"].get(area)), ("state", turn.lvl["state"].get(st_fips)),
                      ("nation", turn.lvl["nation"].get("US"))]

        for soc, r in occs.items():
            if soc == "00-0000":
                continue
            natr = nat.get(soc)
            emp = r.get("TOT_EMP")
            g = group_of(soc)
            in_grp = (emp / a_grp_emp[g]) if emp and a_grp_emp.get(g) else None
            lq = r.get("LOC_QUOTIENT")
            if lq is None and emp and a_total and natr and natr.get("TOT_EMP"):
                lq = (emp / a_total) / (natr["TOT_EMP"] / nat_total)
            gr, gsrc = growth(soc, st_fips)
            tp, tl, tc = turn.proxy(mix.get(soc), area_chain)
            base = {"country": "US", "occ_code_native": soc, "soc_code_harmonized": soc, "occ_title": r["OCC_TITLE"],
                    "area_code": area, "acs_group": g, "growth_outlook": gr, "growth_source": gsrc}
            wf = wage_fields(r, natr)
            area_rows.append({**base, **wf,
                              "geo_level": level, "geo_code": area, "geo_name": title, "state": prim,
                              "emp": emp, "emp_source": "oews_2025", "modelled": False, "lq": rnd(lq),
                              "resident_workers": round(a_res[g] * in_grp) if in_grp is not None and g else None,
                              "resident_workers_group": a_res.get(g),
                              "turnover_proxy": tp, "turnover_level": tl, "turnover_coverage": tc,
                              "TOT_EMP": emp, "LOC_QUOTIENT": r.get("LOC_QUOTIENT"), "A_PCT25": r.get("A_PCT25"),
                              "A_MEDIAN": r.get("A_MEDIAN"), "A_PCT75": r.get("A_PCT75"), "H_MEDIAN": r.get("H_MEDIAN")})

            # ---- counties (modelled)
            for c in cfs:
                c_res_g = res.get(c, {}).get(g, 0.0) if g else 0.0
                if g and a_res.get(g):
                    share = c_res_g / a_res[g]
                elif a_res_tot:
                    share = res_tot.get(c, 0) / a_res_tot
                else:
                    share = 1.0 / len(cfs)
                c_emp = emp * share if emp is not None else None
                if c_emp is not None and c_emp < 0.5:
                    continue                                   # no modelled jobs here; keeps the table lean
                c_total = a_total * (res_tot.get(c, 0) / a_res_tot) if a_total and a_res_tot else None
                c_lq = ((c_emp / c_total) / (natr["TOT_EMP"] / nat_total)
                        if c_emp and c_total and natr and natr.get("TOT_EMP") else None)
                c_chain = [("county", turn.county.get(c))] + [x for x in area_chain]
                tp, tl, tc = turn.proxy(mix.get(soc), c_chain)
                cg, cgsrc = growth(soc, c[:2])
                cname = xw_c2a[c].get("county_name")
                county_rows.append({**base, **wf, "growth_outlook": cg, "growth_source": cgsrc,
                                    "geo_level": "county", "geo_code": c,
                                    "geo_name": f"{cname}, {ST_ABBR.get(c[:2], '')}" if cname else c,
                                    "state": ST_ABBR.get(c[:2]),
                                    "emp": round(c_emp) if c_emp is not None else None,
                                    "emp_source": "oews_2025_x_acs_residents", "modelled": True,
                                    "lq": rnd(c_lq),
                                    "resident_workers": round(c_res_g * in_grp) if in_grp is not None and g else None,
                                    "resident_workers_group": c_res_g if g else None,
                                    "turnover_proxy": tp, "turnover_level": tl, "turnover_coverage": tc})
    return area_rows, county_rows


# =============================================================================== Canada
def canada(noc_soc, cd_er, er_names, cops):
    print("CA: loading StatCan, Job Bank")
    cma = load_json(out_path("statcan_occ_cma.json"))
    cd = load_json(out_path("statcan_occ_cd.json"))
    jb = load_json(out_path("jobbank_wages_er.json"))

    nat_unit, prov_unit, nat_total = {}, collections.defaultdict(dict), None
    titles = {}
    for r in cma:
        if r["geo_level"] == "nation":
            if r["noc_level"] == "unit":
                nat_unit[r["occ_code_native"]] = r["emp"] or 0
                titles[r["occ_code_native"]] = r["occ_title"]
            elif r["noc_level"] == "total":
                nat_total = r["emp"]
        elif r["geo_level"] == "province" and r["noc_level"] == "unit":
            prov_unit[r["geo_code"]][r["occ_code_native"]] = r["emp"] or 0
    prov_minor = {p: collections.Counter() for p in prov_unit}
    for p, d in prov_unit.items():
        for noc, v in d.items():
            prov_minor[p][noc[:4]] += v

    cd_minor, cd_total, cd_name = collections.defaultdict(dict), {}, {}
    for r in cd:
        if r["geo_level"] != "cd":
            continue
        cd_name[r["geo_code"]] = r["geo_name"]
        if r["noc_level"] == "minor":
            cd_minor[r["geo_code"]][r["occ_code_native"]] = r["emp"] or 0
        elif r["noc_level"] == "total":
            cd_total[r["geo_code"]] = r["emp"] or 0

    # unit-group emp per CD = CD minor-group emp x province unit/minor split
    cd_unit = {}
    for c, mins in cd_minor.items():
        p = c[:2]
        d = {}
        for noc, v in prov_unit.get(p, {}).items():
            m = prov_minor[p].get(noc[:4])
            mv = mins.get(noc[:4])
            if m and mv:
                d[noc] = mv * v / m
        cd_unit[c] = d

    wages = {(r["geo_code"], r["occ_code_native"]): r for r in jb}

    def wage(geo_codes, noc):
        """first level with a published median: [(level, code)]"""
        for lvl, code in geo_codes:
            r = wages.get((code, noc))
            if r and r.get("wage_median") is not None:
                return lvl, r
        return None, None

    def ca_row(level, code, name, prov, noc, emp, total, wage_chain):
        natv = nat_unit.get(noc)
        lq = (emp / total) / (natv / nat_total) if emp and total and natv and nat_total else None
        wl, w = wage(wage_chain, noc)
        _, wn = wage([("nation", "00")], noc)
        ann = lambda r, k: (None if r is None or r.get(k) is None else
                            (r[k] if r["wage_basis"] == "annual" else round(r[k] * 2080, 0)))
        idx = (w["wage_median_annual"] / wn["wage_median_annual"]) if w and wn and wn.get("wage_median_annual") else None
        cp = cops.get(noc) or {}
        return {"country": "CA", "geo_level": level, "geo_code": code, "geo_name": name, "prov": prov,
                "occ_code_native": noc, "soc_code_harmonized": (noc_soc.get(noc) or {}).get("soc_primary"),
                "occ_title": titles.get(noc), "emp": round(emp) if emp is not None else None,
                "emp_source": "census_2021", "modelled": True, "lq": rnd(lq),
                "wage_p25": ann(w, "wage_p25"), "wage_median": w["wage_median_annual"] if w else None,
                "wage_p75": ann(w, "wage_p75"), "wage_low": ann(w, "wage_low"), "wage_high": ann(w, "wage_high"),
                "wage_median_hourly": w["wage_median_hourly"] if w else None, "wage_level": wl,
                "wage_index": rnd(idx), "currency": "CAD",
                "resident_workers": round(emp) if emp is not None else None,
                "growth_outlook": cp.get("growth_pct"), "growth_source": "cops_national" if cp else None,
                "future_conditions": cp.get("future_conditions"),
                "turnover_proxy": None, "comparability": "comparable, not identical"}

    cd_rows, er_rows = [], []
    er_unit = collections.defaultdict(collections.Counter)
    er_total = collections.Counter()
    for c, d in cd_unit.items():
        er = (cd_er.get(c) or {}).get("er_code")
        prov = PROV.get(c[:2])
        chain = [("er", er), ("province", c[:2]), ("nation", "00")] if er else [("province", c[:2]), ("nation", "00")]
        for noc, v in d.items():
            if v < 0.5:
                continue
            cd_rows.append(ca_row("cd", c, cd_name.get(c), prov, noc, v, cd_total.get(c), chain))
        if er:
            er_total[er] += cd_total.get(c, 0)
            for noc, v in d.items():
                er_unit[er][noc] += v
    for er, d in er_unit.items():
        p = er[:2]
        name = (er_names.get(er) or {}).get("er_name")
        for noc, v in d.items():
            if v < 0.5:
                continue
            er_rows.append(ca_row("er", er, name, PROV.get(p), noc, v, er_total[er],
                                  [("er", er), ("province", p), ("nation", "00")]))
    return er_rows, cd_rows


def main():
    c2a = load_json(os.path.join(XW, "county_to_area.json"))
    noc_soc = load_json(os.path.join(XW, "noc_soc.json"))["noc_to_soc"]
    cd_er = load_json(os.path.join(XW, "cd_to_er.json"))
    er_names = load_json(os.path.join(XW, "er_names.json"))
    proj_nat, proj_st = opt("projections_us_national.json"), opt("projections_us_state.json")
    cops = opt("projections_ca_national.json") or {}
    if not proj_nat:
        print("  NOTE: no US projections yet (raw/bls/occupation.xlsx) - US growth_outlook will be null")

    area_rows, county_rows = us(c2a, proj_nat, proj_st)
    meta = {"built": today(), "sources": {"oews": "BLS OEWS May 2025", "acs": "Census ACS 2020-2024 5-year C24010",
                                          "qwi": "Census QWI, last 8 quarters (A00)",
                                          "growth": "Projections Central state" if proj_st else
                                          ("BLS EP national" if proj_nat else "none yet")},
            "notes": ["competition_ratio = emp / requested headcount, computed at scoring time",
                      "turnover_proxy is a proxy: industry turnover weighted by the occupation's national industry mix"]}
    write_table("labor_metrics_cbsa.json", FIELDS_US, area_rows, meta)
    write_table("labor_metrics_county.json", FIELDS_US, county_rows,
                {**meta, "notes": meta["notes"] + ["county emp is MODELLED: area jobs split by ACS resident workers "
                                                   "in the occupation's group; wages are the area's"]})
    er_rows, cd_rows = canada(noc_soc, cd_er, er_names, cops)
    cmeta = {"built": today(), "sources": {"emp": "StatCan 2021 Census 98-10-0593 + 98-10-0591",
                                           "wages": "ESDC Job Bank wages 2025", "growth": "ESDC COPS 2026-2035"},
             "notes": ["emp is census employed by place of residence; ER/CD unit-group counts modelled from CD minor "
                       "groups x the province's unit/minor split", "wages annualized at 2,080 h; CAD",
                       "comparable, not identical to US figures"]}
    write_table("labor_metrics_ca_er.json", FIELDS_CA, er_rows, cmeta)
    write_table("labor_metrics_ca_cd.json", FIELDS_CA, cd_rows, cmeta)


if __name__ == "__main__":
    main()
