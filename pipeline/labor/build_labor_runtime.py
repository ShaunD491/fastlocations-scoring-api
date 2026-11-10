#!/usr/bin/env python3
r"""
build_labor_runtime.py  (Phase 3 - the compact file the scoring API reads)
----------------------
The Phase 2 tables are far too large to ship with the API (the county table alone is ~390 MB of
JSON). The scorer only ever needs the handful of occupations a project asks for, so this splits the
tables into one small gzipped file per occupation, loaded on demand and cached by labor.py:

  Projects/labor_data/index.json          titles, field order, national medians, crosswalks, hiring sheds
                                          (county -> metro area, CD -> economic region), vintages
  Projects/labor_data/us/<SOC>.json.gz    {"county": {fips: row}, "area": {area_code: row}}
  Projects/labor_data/ca/<NOC>.json.gz    {"cd": {cduid: row}, "er": {eruid: row}}

Each row is a list in the order of index["fields"] (US) / index["fields_ca"] (CA):
  emp, lq, wage_median, wage_index, resident_workers, growth_outlook, turnover_proxy
Area rows add geo_level ("msa" | "nonmetro") at the end; names live in index["areas"] / ["ers"].

USAGE
  py -3 -I build_labor_runtime.py
"""
import collections
import gzip
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import XW, load_json, out_path, today

DEST = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "labor_data"))
ROW = ["emp", "lq", "wage_median", "wage_index", "resident_workers", "growth_outlook", "turnover_proxy"]


def rows(name):
    t = load_json(out_path(name))
    f = t["fields"]
    return t, [dict(zip(f, r)) for r in t["rows"]]


def pack(r):
    def rnd(v, n):
        return None if v is None else round(v, n)
    return [r["emp"], rnd(r["lq"], 3), rnd(r["wage_median"], 0), rnd(r["wage_index"], 3),
            r["resident_workers"], rnd(r["growth_outlook"], 2), rnd(r["turnover_proxy"], 4)]


def dump(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", compresslevel=9) as fh:
        json.dump(obj, fh, separators=(",", ":"))


def main():
    # Clear old occupation files one by one (removing the folders themselves fails while OneDrive holds them).
    for sub in ("us", "ca"):
        d = os.path.join(DEST, sub)
        for f in (os.listdir(d) if os.path.isdir(d) else []):
            if f.endswith(".json.gz"):
                os.remove(os.path.join(d, f))
    idx = {"built": today(), "fields": ROW, "fields_area": ROW + ["geo_level"], "fields_ca": ROW,
           "us_titles": {}, "ca_titles": {}, "us_nat_median": {}, "ca_nat_median": {},
           "areas": {}, "ers": {}, "noc_to_soc": {}, "soc_to_noc": {}, "sources": {}}

    # ---- US
    t_area, area = rows("labor_metrics_cbsa.json")
    idx["sources"]["us"] = t_area["_meta"].get("sources")
    by = collections.defaultdict(lambda: {"county": {}, "area": {}})
    for r in area:
        soc = r["occ_code_native"]
        by[soc]["area"][r["geo_code"]] = pack(r) + [r["geo_level"]]
        idx["us_titles"].setdefault(soc, r["occ_title"])
        idx["areas"].setdefault(r["geo_code"], [r["geo_name"], r["geo_level"], r["state"]])
    del area
    _, county = rows("labor_metrics_county.json")
    for r in county:
        by[r["occ_code_native"]]["county"][r["geo_code"]] = pack(r)
    del county
    # national median per SOC = wage_median / wage_index on any row that has both (the same for all rows)
    for soc, d in by.items():
        for row in d["area"].values():
            if row[2] and row[3]:
                idx["us_nat_median"][soc] = round(row[2] / row[3], -1)
                break
        dump(os.path.join(DEST, "us", soc + ".json.gz"), d)
    print(f"  US: {len(by)} occupation files")

    # ---- Canada
    t_er, er = rows("labor_metrics_ca_er.json")
    idx["sources"]["ca"] = t_er["_meta"].get("sources")
    cby = collections.defaultdict(lambda: {"cd": {}, "er": {}})
    for r in er:
        noc = r["occ_code_native"]
        cby[noc]["er"][r["geo_code"]] = pack(r)
        idx["ca_titles"].setdefault(noc, r["occ_title"])
        idx["ers"].setdefault(r["geo_code"], [r["geo_name"], "er", r["prov"]])
        if r["soc_code_harmonized"]:
            idx["noc_to_soc"][noc] = r["soc_code_harmonized"]
    _, cd = rows("labor_metrics_ca_cd.json")
    for r in cd:
        cby[r["occ_code_native"]]["cd"][r["geo_code"]] = pack(r)
    for noc, d in cby.items():
        for row in d["er"].values():
            if row[2] and row[3]:
                idx["ca_nat_median"][noc] = round(row[2] / row[3], -1)
                break
        dump(os.path.join(DEST, "ca", noc + ".json.gz"), d)
    for noc, soc in sorted(idx["noc_to_soc"].items()):
        idx["soc_to_noc"].setdefault(soc, []).append(noc)
    print(f"  CA: {len(cby)} occupation files")

    # Hiring sheds for the competition sub-score (labor.py): a county inside a metro hires from the
    # whole OEWS metro area, a census division from its economic region. Nonmetro OEWS areas are not
    # used as sheds - they are loose groupings of rural counties, often hundreds of miles across.
    c2a = load_json(os.path.join(XW, "county_to_area.json"))
    idx["county_to_area"] = {f: x["oews_area"] for f, x in sorted(c2a.items())
                             if x.get("oews_area") and x.get("oews_area_type") == "msa"}
    c2e = load_json(os.path.join(XW, "cd_to_er.json"))
    idx["cd_to_er"] = {c: x["er_code"] for c, x in sorted(c2e.items()) if x.get("er_code")}
    # Job-title lookup for the plain-English intake (labor_intake.py): O*NET 30.2 titles (alternate,
    # reported, short) -> SOC, and Job Bank / NOC example titles -> NOC (+ its SOC). Only codes that
    # have data are kept. [display title, [SOCs], [NOCs]] keyed by the normalised title.
    onet = load_json(os.path.join(XW, "onet_titles.json"))["titles"]
    nocs = load_json(os.path.join(XW, "noc_titles.json"))["titles"]
    titles = {}
    for k, v in onet.items():
        socs = [x for x in v.get("soc", []) if x in idx["us_titles"]]
        if socs:
            titles[k] = [v["title"], socs, []]
    for k, v in nocs.items():
        ns = [x for x in v.get("noc", []) if x in idx["ca_titles"]]
        if not ns:
            continue
        t = titles.setdefault(k, [v["title"], [], []])
        t[2] = sorted(set(t[2]) | set(ns))
        for n in ns:
            s_ = idx["noc_to_soc"].get(n)
            if s_ and s_ in idx["us_titles"] and s_ not in t[1] and not onet.get(k):
                t[1].append(s_)
    for soc, t in idx["us_titles"].items():                     # the official titles themselves
        k = " ".join("".join(c if c.isalnum() else " " for c in t.lower()).split())
        titles.setdefault(k, [t, [soc], list(idx["soc_to_noc"].get(soc, []))])
    for noc, t in idx["ca_titles"].items():
        k = " ".join("".join(c if c.isalnum() else " " for c in t.lower()).split())
        titles.setdefault(k, [t, [s for s in [idx["noc_to_soc"].get(noc)] if s and s in idx["us_titles"]], [noc]])
    dump(os.path.join(DEST, "titles.json.gz"), dict(sorted(titles.items())))
    print(f"  titles: {len(titles):,}")

    with open(os.path.join(DEST, "index.json"), "w", encoding="utf-8") as fh:
        json.dump(idx, fh, ensure_ascii=False, separators=(",", ":"))
    size = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(DEST) for f in fs)
    print(f"  wrote {DEST}  ({size / 1e6:.1f} MB total)")


if __name__ == "__main__":
    main()
