#!/usr/bin/env python3
"""
labor.py - occupation-level labor scoring (Phase 3 of Occupation_Labor_Data_Plan.md).

A project lists the roles it must hire in criteria["workforce"]["occupations"]:

    [{"soc_code": "51-9161", "noc_code": "94107", "title_raw": "CNC machinists",
      "headcount": 200, "wage_ceiling": 60000}, ...]

soc_code or noc_code is enough: the other country is reached through the NOC <-> SOC crosswalk.
wage_ceiling is annual USD; Canada is tested against the same ceiling expressed as a wage index (the
ceiling / the US national median for the SOC), so no exchange rate is involved.

For every candidate geography and every requested occupation this scores five sub-scores, each a
0-100 percentile within the candidate's own country (the same convention as scorer.py):

  availability  how big and how concentrated the local pool is: the mean of the percentile of
                occupation employment (emp) and of the location quotient (lq). Size alone would just
                re-rank by population; LQ credits specialised places (aircraft mechanics in Wichita).
  competition   workers per hire in the HIRING SHED (shed emp / headcount, reported as competition_ratio)
                on an ABSOLUTE
                log scale: 100 * log(1 + ratio) / log(1 + AMPLE_RATIO), capped at 100 (50 workers per
                hire = ample; 2 per hire = 28; fewer than 1 per hire = under 18). The only sub-score
                that is not a percentile: it is a feasibility test, and "most places are short" must not
                make a short place look good (a percentile put 400 workers for 200 hires at 81).
                The shed is where the hires would commute from: a county inside a metro hires from the
                whole OEWS metro area, a Canadian census division from its economic region; a nonmetro
                county from itself (nonmetro OEWS areas are too large to commute across). Metros and
                ERs scored directly are their own shed. labor_shed / labor_shed_emp say which was used.
  cost          lower local wage relative to the country's national median for the occupation
                (wage_index). If wage_ceiling is set and the local median is above it, the sub-score
                is cut by CEILING_SLOPE per 1% over (40% over -> 0) and over_ceiling is flagged.
  growth        projected 10-year employment growth (US: state projections, else BLS national;
                Canada: COPS national, so it does not vary inside Canada).
  turnover      lower QWI stable-job turnover proxy (US only; Canada has no source -> gap).

A sub-score with no data drops out and the rest are re-weighted (a gap, never a penalty), exactly like
the scorer's dimensions. An occupation with NO row for a geography means fewer than one modelled job
there, so it is scored as zero supply - a real absence, not missing data. A row whose employment is
suppressed (null) gets no labor score at all, since cost and growth alone must not rank it.

TIGHT POOL: as one weighted sub-score, competition could not stop a place with 2.8 machinists per hire
(i.e. hiring a third of the local trade) from ranking in the top 5 on cheap wages and growth. Below
TIGHT_RATIO workers per hire the occupation score is multiplied by 0.5 + 0.5 * ratio / TIGHT_RATIO
(2.5 per hire -> x0.75, 1 per hire -> x0.6) and flagged tight_pool: true. The labor score for a place
is the weighted sub-score mean per occupation, then the headcount-weighted mean across occupations.

Default weights (LABOR_WEIGHTS) are overridable per request: criteria.workforce.labor_weights.

US county and Canadian CD figures are MODELLED (county jobs spread from the OEWS area by ACS resident
workers; CD unit groups from census minor groups) and every detail row says so (modelled: true).
Canadian figures are comparable to, not identical with, US figures.

Data: labor_data/ built by pipeline/labor/build_labor_runtime.py. Pure function of its inputs (no
randomness, sorted iteration): the same request always gives the same output.
"""
import functools
import gzip
import json
import math
import os
import re

O = os.environ.get("FL_DATA_DIR") or os.path.dirname(os.path.abspath(__file__))
LABOR_DIR = os.path.join(O, "labor_data")

LABOR_WEIGHTS = {"availability": 0.30, "competition": 0.25, "cost": 0.20, "growth": 0.15, "turnover": 0.10}
SUBS = ["availability", "competition", "cost", "growth", "turnover"]
AMPLE_RATIO = 50.0       # local workers per hire at which competition stops improving
TIGHT_RATIO = 5.0        # below this many workers per hire the occupation score is damped (tight_pool)
CEILING_SLOPE = 2.5      # cost sub-score multiplier falls 2.5% per 1% the median is over the ceiling
MAX_OCCUPATIONS = 12


# ----------------------------------------------------------------------------- data
@functools.lru_cache(maxsize=1)
def index():
    p = os.path.join(LABOR_DIR, "index.json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def available():
    return index() is not None


@functools.lru_cache(maxsize=256)
def _occ_file(country, code):
    p = os.path.join(LABOR_DIR, "us" if country == "US" else "ca", code + ".json.gz")
    if not os.path.exists(p):
        return None
    with gzip.open(p, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def _soc(v):
    s = re.sub(r"[^0-9]", "", str(v or ""))
    return f"{s[:2]}-{s[2:6]}" if len(s) == 6 else None


def _noc(v):
    s = re.sub(r"[^0-9]", "", str(v or ""))
    return s if len(s) == 5 else None


def resolve(req):
    """One requested role -> {soc, nocs, title, headcount, wage_ceiling, notes}. None if unusable."""
    idx = index()
    soc, noc = _soc(req.get("soc_code")), _noc(req.get("noc_code"))
    notes = []
    if soc and soc not in idx["us_titles"]:
        notes.append(f"SOC {soc} has no OEWS 2025 data")
        soc = None
    if noc and noc not in idx["ca_titles"]:
        notes.append(f"NOC {noc} has no Canadian census data")
        noc = None
    if not soc and noc:
        s = idx["noc_to_soc"].get(noc)
        soc = s if s in idx["us_titles"] else None
    nocs = [noc] if noc else list(idx["soc_to_noc"].get(soc, [])) if soc else []
    if not soc and not nocs:
        return None
    try:
        hc = float(req.get("headcount") or 0)
    except (TypeError, ValueError):
        hc = 0.0
    try:
        ceil = float(req["wage_ceiling"]) if req.get("wage_ceiling") not in (None, "") else None
    except (TypeError, ValueError):
        ceil = None
    if soc is None:
        notes.append("no US equivalent - scored in Canada only")
    if not nocs:
        notes.append("no Canadian equivalent - scored in the US only")
    elif not noc and soc in (idx.get("soc_to_noc_approx") or {}):
        notes.append("Canada: counted in the broader group " + ", ".join(idx["ca_titles"].get(n, n) for n in nocs))
    return {"soc": soc, "nocs": nocs, "headcount": hc if hc > 0 else None, "wage_ceiling": ceil,
            "title_raw": req.get("title_raw"),
            "title": idx["us_titles"].get(soc) if soc else idx["ca_titles"].get(nocs[0]),
            "notes": notes}


def requested(criteria):
    occs = ((criteria or {}).get("workforce") or {}).get("occupations") or []
    if not occs or not available():
        return [], []
    out, dropped = [], []
    for req in occs[:MAX_OCCUPATIONS]:
        r = resolve(req) if isinstance(req, dict) else None
        (out.append(r) if r else dropped.append(req))
    return out, dropped


def _rows(country, req, level):
    """{geo: {emp, lq, wage_median, wage_index, resident_workers, growth_outlook, turnover_proxy}} for
    one requested occupation at one level (US: county|area, CA: cd|er). Several NOCs for one SOC are
    combined: counts summed, rates employment-weighted."""
    idx = index()
    fields = idx["fields"]
    if country == "US":
        d = _occ_file("US", req["soc"]) if req["soc"] else None
        if not d:
            return {}
        return {g: dict(zip(fields + ["geo_level"], r)) for g, r in d[level].items()}
    files = [f for f in (_occ_file("CA", n) for n in req["nocs"]) if f]
    if len(files) == 1:
        return {g: dict(zip(fields, r)) for g, r in files[0][level].items()}
    acc = {}
    for f in files:
        for g, r in f[level].items():
            acc.setdefault(g, []).append(dict(zip(fields, r)))
    out = {}
    for g, rs in acc.items():
        emp = sum(r["emp"] or 0 for r in rs)

        def wmean(k):
            pairs = [(r[k], r["emp"] or 0) for r in rs if r[k] is not None]
            w = sum(e for _, e in pairs)
            return round(sum(v * e for v, e in pairs) / w, 4) if w else None
        out[g] = {"emp": emp, "lq": wmean("lq"), "wage_median": wmean("wage_median"),
                  "wage_index": wmean("wage_index"),
                  "resident_workers": sum(r["resident_workers"] or 0 for r in rs),
                  "growth_outlook": wmean("growth_outlook"), "turnover_proxy": wmean("turnover_proxy")}
    return out


# ----------------------------------------------------------------------------- scoring
def pct_rank(values):
    """Same average-rank percentile as scorer.pct_rank (kept here so labor.py has no import cycle)."""
    pairs = sorted(((k, v) for k, v in values.items() if v is not None), key=lambda x: (x[1], x[0]))
    n = len(pairs)
    if n < 2:
        return {k: (50.0 if v is not None else None) for k, v in values.items()}
    rank, i = {}, 0
    while i < n:
        j = i
        while j + 1 < n and pairs[j + 1][1] == pairs[i][1]:
            j += 1
        p = ((i + j) / 2.0) / (n - 1) * 100
        for t in range(i, j + 1):
            rank[pairs[t][0]] = p
        i = j + 1
    return {k: (round(rank[k], 1) if v is not None else None) for k, v in values.items()}


def _wavg(pairs):
    num = den = 0.0
    for v, w in pairs:
        if v is not None and w > 0:
            num += v * w
            den += w
    return round(num / den, 1) if den else None


def weights_for(criteria):
    w = dict(LABOR_WEIGHTS)
    for k, v in (((criteria or {}).get("workforce") or {}).get("labor_weights") or {}).items():
        if k in w and isinstance(v, (int, float)) and v >= 0:
            w[k] = float(v)
    if sum(w.values()) <= 0:
        w = dict(LABOR_WEIGHTS)
    return w


def score_occupation(country, req, geos, level, weights):
    """Score one requested occupation for `geos` (the whole reference set of one country, so each
    percentile means standing in that country). Returns {geo: detail}."""
    rows = _rows(country, req, level)
    idx = index()
    shed_map = idx.get("county_to_area" if level == "county" else "cd_to_er" if level == "cd" else "") or {}
    shed_rows = _rows(country, req, "area" if country == "US" else "er") if shed_map else {}
    hc = req["headcount"]
    raw = {k: {} for k in ("emp", "lq", "cost", "growth", "turnover")}
    for g in geos:
        r = rows.get(g)
        emp = 0 if r is None else r["emp"]
        lq = 0.0 if r is None else r["lq"]
        raw["emp"][g] = emp
        raw["lq"][g] = lq
        raw["cost"][g] = (-r["wage_index"] if (r and r["wage_index"] is not None) else None)
        raw["growth"][g] = (r["growth_outlook"] if r else None)
        raw["turnover"][g] = (-r["turnover_proxy"] if (r and r["turnover_proxy"] is not None) else None)
    pct = {k: pct_rank(v) for k, v in raw.items()}
    ceil = req["wage_ceiling"]
    nat = idx["us_nat_median"].get(req["soc"]) if req["soc"] else None
    out = {}
    for g in geos:
        r = rows.get(g)
        avail = _wavg([(pct["emp"][g], 1), (pct["lq"][g], 1)])
        cost = pct["cost"][g]
        over = None
        if ceil and r and r["wage_index"] is not None:
            if country == "US" and r["wage_median"]:
                ratio = r["wage_median"] / ceil
            elif country == "CA" and nat:
                ratio = r["wage_index"] / (ceil / nat)          # ceiling as a US wage index
            else:
                ratio = None
            if ratio is not None:
                over = ratio > 1.0
                if over and cost is not None:
                    cost = round(cost * max(0.0, 1.0 - (ratio - 1.0) * CEILING_SLOPE), 1)
        shed = shed_map.get(g)
        srow = shed_rows.get(shed) if shed else None
        if srow is not None and srow["emp"] is not None:
            pool = srow["emp"]
        else:
            shed, pool = None, raw["emp"][g]
        comp = (round(min(100.0, 100.0 * math.log1p(pool / hc) / math.log1p(AMPLE_RATIO)), 1)
                if (hc and pool is not None) else None)
        subs = {"availability": avail, "competition": comp, "cost": cost,
                "growth": pct["growth"][g], "turnover": pct["turnover"][g]}
        # No supply figure (OEWS suppressed the count) -> no labor score: cost and growth alone must not
        # rank a place whose pool size is unknown.
        score = _wavg([(subs[k], weights[k]) for k in SUBS]) if avail is not None else None
        ratio = (pool / hc) if (hc and pool is not None) else None
        tight = ratio is not None and ratio < TIGHT_RATIO
        if tight and score is not None:
            score = round(score * (0.5 + 0.5 * ratio / TIGHT_RATIO), 1)
        emp = 0 if r is None else r["emp"]
        out[g] = {"score": score, "sub_scores": subs,
                  "facts": {"emp": emp, "lq": (r["lq"] if r else 0.0),
                            "competition_ratio": (round(pool / hc, 1) if (hc and pool is not None) else None),
                            "labor_shed": shed, "labor_shed_emp": pool, "tight_pool": tight,
                            "wage_median": (r["wage_median"] if r else None),
                            "currency": "USD" if country == "US" else "CAD",
                            "wage_index": (r["wage_index"] if r else None),
                            "over_wage_ceiling": over,
                            "growth_outlook_pct": (r["growth_outlook"] if r else None),
                            "turnover_proxy": (r["turnover_proxy"] if r else None),
                            "resident_workers": (r["resident_workers"] if r else None)}}
    return out


def score_geos(criteria, geos_by_country, levels):
    """geos_by_country = {"US": [...], "CA": [...]} (each the full reference set for that country),
    levels = {"US": "county"|"area", "CA": "cd"|"er"}. Returns {geo: {labor_score, sub_scores,
    occupations: [...]}} or {} when the project lists no usable occupations."""
    reqs, _ = requested(criteria)
    if not reqs:
        return {}
    weights = weights_for(criteria)
    out = {}
    for country, geos in sorted(geos_by_country.items()):
        if not geos:
            continue
        usable = [q for q in reqs if (q["soc"] if country == "US" else q["nocs"])]
        per = [(q, score_occupation(country, q, geos, levels[country], weights)) for q in usable]
        modelled = levels[country] in ("county", "cd", "er")
        for g in geos:
            occs, pairs, sub_pairs = [], [], {k: [] for k in SUBS}
            for q, res in per:
                d = res[g]
                w = q["headcount"] or 1.0
                pairs.append((d["score"], w))
                for k in SUBS:
                    sub_pairs[k].append((d["sub_scores"][k], w))
                occs.append({"soc_code": q["soc"], "noc_codes": q["nocs"] if country == "CA" else None,
                             "title": q["title"], "title_raw": q["title_raw"], "headcount": q["headcount"],
                             "wage_ceiling": q["wage_ceiling"], "score": d["score"],
                             "sub_scores": d["sub_scores"], **d["facts"], "modelled": modelled,
                             "comparability": ("comparable, not identical" if country == "CA" else None)})
            out[g] = {"labor_score": _wavg(pairs),
                      "sub_scores": {k: _wavg(v) for k, v in sub_pairs.items()},
                      "occupations": occs}
    return out


def requested_summary(criteria):
    reqs, dropped = requested(criteria)
    return {"occupations": [{"soc_code": q["soc"], "noc_codes": q["nocs"], "title": q["title"],
                             "title_raw": q["title_raw"], "headcount": q["headcount"],
                             "wage_ceiling": q["wage_ceiling"], "notes": q["notes"]} for q in reqs],
            "unmatched": dropped, "weights": weights_for(criteria),
            "data_built": (index() or {}).get("built")}


# ----------------------------------------------------------------------------- metro ranking
def rank_metros(criteria, top=10, include_nonmetro=False):
    """Score every US metro (OEWS MSA; nonmetro areas too if asked) and every Canadian economic region
    against the project's occupations; return the top N per country with each sub-score."""
    idx = index()
    if not idx:
        return {"error": "labor data not built (labor_data/index.json missing)"}
    countries = set(((criteria or {}).get("geography") or {}).get("countries") or ["US", "CA"])
    us = sorted(a for a, (_, lvl, _s) in idx["areas"].items()
                if lvl == "msa" or (include_nonmetro and lvl == "nonmetro")) if "US" in countries else []
    ca = sorted(idx["ers"]) if "CA" in countries else []
    res = score_geos(criteria, {"US": us, "CA": ca}, {"US": "area", "CA": "er"})
    if not res:
        return {"error": "no usable occupations in workforce.occupations", **requested_summary(criteria)}
    out = {"schema_version": "1.0", "mode": "metro_ranking", "request": requested_summary(criteria), "results": {}}
    for country, geos, names in (("US", us, idx["areas"]), ("CA", ca, idx["ers"])):
        if not geos:
            continue
        ranked = sorted((g for g in geos if res[g]["labor_score"] is not None),
                        key=lambda g: (-res[g]["labor_score"], g))
        out["results"][country] = [{"rank": i, "geo_code": g, "name": names[g][0], "geo_level": names[g][1],
                                    "state_or_province": names[g][2], **res[g]}
                                   for i, g in enumerate(ranked[:top], 1)]
        out["results"][country + "_candidates"] = len(ranked)
    return out


def place_summary(criteria, area=None, country=None, states=None, top_in_state=5):
    """One community's standing for the requested roles (dashboard role search).

    area + country: an OEWS area code (US metro or nonmetro) or a Canadian economic-region code; the place
    is ranked against its peers in its own country (metros among metros; a nonmetro area among metros and
    nonmetro areas together). states: state / province abbreviations; the best-scoring places inside them
    are returned too (for state, province and multi-county organizations, which have no single area).
    """
    idx = index()
    if not idx:
        return {"error": "labor data not built (labor_data/index.json missing)"}
    country = "CA" if str(country or "").upper() in ("CA", "CAN", "CANADA") else "US"
    if area and country == "US" and area not in idx["areas"]:
        return {"error": f"unknown OEWS area {area}"}
    if area and country == "CA" and area not in idx["ers"]:
        return {"error": f"unknown economic region {area}"}
    nonmetro = bool(area and country == "US" and idx["areas"][area][1] == "nonmetro")
    crit = dict(criteria or {})
    crit["geography"] = dict(crit.get("geography") or {}, countries=[country])
    full = rank_metros(crit, top=10 ** 6, include_nonmetro=nonmetro)
    if full.get("error"):
        return full
    rows = full["results"].get(country) or []
    out = {"schema_version": "1.0", "mode": "place_summary", "request": full["request"], "country": country,
           "peers": len(rows), "peer_set": ("U.S. metro and nonmetro areas" if nonmetro else
                                            "U.S. metro areas" if country == "US" else "Canadian economic regions")}
    if area:
        hit = next((r for r in rows if r["geo_code"] == area), None)
        out["place"] = hit
        if hit is None:
            out["note"] = "this area has no published employment for the requested occupations"
    sts = {str(s).upper() for s in (states or []) if s}
    if sts:
        def in_states(r):
            if r["state_or_province"] in sts:
                return True
            tail = r["name"].rsplit(", ", 1)[-1] if ", " in r["name"] else ""
            return any(t in sts for t in tail.split("-"))
        inside = [r for r in rows if in_states(r)]
        out["in_states"] = inside[:top_in_state]
        out["in_states_count"] = len(inside)
    out["leaders"] = rows[:3]
    return out
