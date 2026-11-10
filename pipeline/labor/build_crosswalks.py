#!/usr/bin/env python3
r"""
build_crosswalks.py  (Phase 1, step 0 - run this FIRST; every other labor script reads its output)
-------------------
Builds the lookups that join US and Canadian labor data:

  out/crosswalks/county_to_area.json   US county FIPS -> CBSA (OMB 2023) + OEWS area (metro or BLS nonmetro)
  out/crosswalks/noc_soc.json          NOC 2021 <-> SOC 2018 (chained NOC 2021 -> NOC 2016 v1.3 -> SOC 2018)
  out/crosswalks/onet_titles.json      plain-English job title -> SOC (O*NET titles, alternate + reported titles)
  out/crosswalks/noc_titles.json       plain-English job title -> NOC 2021 (NOC class titles + index examples)
  out/crosswalks/cd_to_er.json         Canadian census division -> economic region (needs the StatCan DGRF, see below)
  out/crosswalks/er_names.json         economic region code -> name (from the Job Bank wage file)
  out/crosswalks/manifest.json         sources, vintages and row counts

SOURCES
  Downloaded automatically (free, open licences):
    OMB CBSA delineation July 2023 (Census List 1)
    StatCan NOC 2016 v1.3 -> SOC 2018 concordance; NOC 2016 v1.3 -> NOC 2021 v1.0 concordance
    StatCan NOC 2021 v1.0 classification structure + elements (index of titles)
    O*NET database text files (newest release found, 30.2 as of Nov 2026)
    ESDC Job Bank wages 2025 (for ER names; fetch_jobbank_wages.py downloads it too)
  Download by hand (these sites refuse scripted downloads; the script tells you if they are missing):
    raw/bls/area_definitions_m2025.xlsx   BLS OEWS county-to-area list
        https://www.bls.gov/oes/area_definitions_m2025.xlsx  (listed on https://www.bls.gov/oes/2025/may/msa_def.htm)
        Without it, metro counties still map (OEWS metro area code = CBSA code) but nonmetro counties
        get oews_area = null.
    raw/statcan/2021_98260004.csv          StatCan Dissemination Geographies Relationship File (DGRF)
        https://www150.statcan.gc.ca/n1/pub/98-26-0003/982600032021001-eng.htm  (data download section)
        Only the CD and ER columns are used. Without it cd_to_er.json is not written.

CROSSWALK RULES
  NOC -> SOC is one-to-many and mostly partial (1,050 of 1,250 StatCan links are flagged partial).
  Every SOC a NOC reaches is kept, with its partial flag and the NOC 2016 code it came through.
  `soc_primary` is the single code used as `soc_code_harmonized`: the only full (non-partial) link
  if there is exactly one, otherwise the SOC reached through the most NOC 2016 paths, ties broken by
  the order StatCan lists them. Treat cross-border comparisons as "comparable, not identical".

USAGE
  py -3 build_crosswalks.py            (add --refresh to re-download the automatic sources)
"""
import collections
import csv
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import (XW, download, http_get, load_json, norm_title, raw_path, soc_fmt, today,
                     write_json)

REFRESH = "--refresh" in sys.argv

CBSA_URL = ("https://www2.census.gov/programs-surveys/metro-micro/geographies/reference-files/"
            "2023/delineation-files/list1_2023.xlsx")
NOC_SOC_URL = "https://www.statcan.gc.ca/en/statistical-programs/document/noc2016v1_3-soc2018us-eng.csv"
NOC16_21_URL = "https://www.statcan.gc.ca/en/statistical-programs/document/noc2016v1_3-noc2021v1_0-eng.csv"
NOC_STRUCT_URL = ("https://www.statcan.gc.ca/en/subjects/standard/noc/2021/indexV1/"
                  "noc-2021-v1.0-classification-structure.csv")
NOC_ELEM_URL = "https://www.statcan.gc.ca/en/subjects/standard/noc/2021/indexV1/noc-2021-v1.0-elements.csv"
ONET_BASE = "https://www.onetcenter.org/dl_files/database/db_{v}_text/{f}"
JOBBANK_URL = ("https://open.canada.ca/data/dataset/adad580f-76b0-4502-bd05-20c125de9116/resource/"
               "9da94d63-b178-4a64-aeb3-b6a3bd721ad2/download/2a71-das-wage2025opendata-esdc-all-19nov2025-vf.csv")
BLS_AREA_URL = "https://www.bls.gov/oes/area_definitions_m2025.xlsx"
DGRF_URL = "https://www150.statcan.gc.ca/n1/pub/98-26-0003/982600032021001-eng.htm"

STOP = {"and", "of", "the", "in", "all", "other", "except", "or", "n", "e", "c", "workers", "worker",
          "occupations", "related"}

PROV_ABBR = {"10": "NL", "11": "PE", "12": "NS", "13": "NB", "24": "QC", "35": "ON", "46": "MB",
             "47": "SK", "48": "AB", "59": "BC", "60": "YT", "61": "NT", "62": "NU"}


def read_csv_any(path):
    """StatCan files mix UTF-8 (with BOM) and cp1252; try both."""
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            txt = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    rows = list(csv.DictReader(io.StringIO(txt)))
    # strip stray spaces from header names (" GSIM Type of Change")
    return [{(k or "").strip(): (v or "").strip() for k, v in r.items()} for r in rows]


# --------------------------------------------------------------------------- US geography
def build_county_to_area():
    import pandas as pd
    src = download(CBSA_URL, raw_path("census", "list1_2023.xlsx"), force=REFRESH)
    d = pd.read_excel(src, header=2, dtype=str)
    d = d[d["FIPS State Code"].notna() & d["FIPS County Code"].notna()]
    out = {}
    for _, r in d.iterrows():
        fips = r["FIPS State Code"].zfill(2) + r["FIPS County Code"].zfill(3)
        typ = "metro" if str(r["Metropolitan/Micropolitan Statistical Area"]).startswith("Metro") else "micro"
        out[fips] = {
            "county_fips": fips,
            "county_name": r["County/County Equivalent"],
            "state_name": r["State Name"],
            "cbsa_code": r["CBSA Code"],
            "cbsa_title": r["CBSA Title"],
            "cbsa_type": typ,
            "metro_division_code": r.get("Metropolitan Division Code") if isinstance(r.get("Metropolitan Division Code"), str) else None,
            "csa_code": r["CSA Code"] if isinstance(r["CSA Code"], str) else None,
            "oews_area": r["CBSA Code"] if typ == "metro" else None,   # provisional; BLS file overrides
            "oews_area_name": r["CBSA Title"] if typ == "metro" else None,
            "oews_area_type": "msa" if typ == "metro" else None,
            "oews_area_source": "cbsa_2023" if typ == "metro" else None,
        }
    n_cbsa = len(out)

    bls = raw_path("bls", "area_definitions_m2025.xlsx")
    bls_used = False
    if os.path.exists(bls):
        bls_used = True
        a = pd.read_excel(bls, dtype=str)
        cols = {c: str(c).strip().lower() for c in a.columns}

        def col(*keys, exclude=()):
            for c, lc in cols.items():
                if all(k in lc for k in keys) and not any(x in lc for x in exclude):
                    return c
            raise KeyError(f"area_definitions file: no column containing {keys}; columns = {list(a.columns)}")

        c_st = col("fips")
        c_area = col("code", exclude=("county", "township", "fips", "state"))
        try:
            c_name = col("area title")                 # May 2025 layout: "May 2025 Area Title"
        except KeyError:
            c_name = col("name", exclude=("county", "township", "state"))
        c_cty = col("county code")
        c_ctyname = col("county name")
        votes = collections.defaultdict(collections.Counter)   # New England: townships can split a county
        names = {}
        for _, r in a.iterrows():
            st, cty, area = r.get(c_st), r.get(c_cty), r.get(c_area)
            if not isinstance(st, str) or not isinstance(cty, str) or not isinstance(area, str):
                continue
            fips = st.strip().zfill(2) + cty.strip().zfill(3)
            area = area.strip()
            votes[fips][area] += 1
            names[area] = str(r.get(c_name) or "").strip()
            if fips not in out:
                out[fips] = {"county_fips": fips, "county_name": str(r.get(c_ctyname) or "").strip(),
                             "state_name": None, "cbsa_code": None, "cbsa_title": None, "cbsa_type": None,
                             "metro_division_code": None, "csa_code": None}
        for fips, cnt in votes.items():
            (area, _), = cnt.most_common(1)
            rec = out[fips]
            rec["oews_area"] = area
            rec["oews_area_name"] = names.get(area)
            # BLS nonmetro area codes are 7 digits (state FIPS + 000 + n); metro areas are CBSA codes
            rec["oews_area_type"] = "nonmetro" if len(area) == 7 else "msa"
            rec["oews_area_source"] = "bls_area_definitions_m2025"
            if len(cnt) > 1:
                rec["oews_area_split"] = sorted(cnt)            # New England county spanning >1 area
    else:
        print(f"  NOTE: {os.path.relpath(bls)} not found - nonmetro counties get oews_area=null.\n"
              f"        Download it in a browser from {BLS_AREA_URL}")
    # counties outside any CBSA appear only in the BLS file; counties in a micro area are nonmetro for OEWS
    for rec in out.values():
        rec.setdefault("oews_area_split", None)
    return out, {"cbsa_counties": n_cbsa, "bls_area_definitions": bls_used}


# --------------------------------------------------------------------------- NOC <-> SOC
def build_noc_soc():
    s = read_csv_any(download(NOC_SOC_URL, raw_path("noc", "noc2016v1_3-soc2018us-eng.csv"), force=REFRESH))
    c = read_csv_any(download(NOC16_21_URL, raw_path("noc", "noc2016v1_3-noc2021v1_0-eng.csv"), force=REFRESH))
    st = read_csv_any(download(NOC_STRUCT_URL, raw_path("noc", "noc-2021-v1.0-classification-structure.csv"), force=REFRESH))

    titles21 = {r["Code - NOC 2021 V1.0"]: r["Class title"] for r in st if r["Level"] == "5"}
    soc_title = {}
    n16_soc = collections.defaultdict(list)          # NOC 2016 -> [(soc, partial)]
    for r in s:
        n16 = r["NOC 2016  Version 1.3 Code"] if "NOC 2016  Version 1.3 Code" in r else r["NOC 2016 Version 1.3 Code"]
        soc = soc_fmt(r["SOC 2018 (US) Code"])
        if not n16 or not soc:
            continue
        soc_title[soc] = r["SOC 2018 (US) Title"]
        n16_soc[n16.zfill(4)].append((soc, r["Partial"].strip() == "*"))

    # A NOC 2016 code that "continues as X and part transferred to Y" is a MAIN source of X but only
    # a minor transfer into Y. Chaining its SOCs into Y would, e.g., attach wholesale sales SOCs to
    # NOC 75101 Material handlers, so transfer-in links are recorded but not chained.
    targets16 = collections.defaultdict(set)
    for r in c:
        if r["NOC 2016 V1.3 Code"] and r["NOC 2021 V1.0 Code"]:
            targets16[r["NOC 2016 V1.3 Code"].zfill(4)].add(r["NOC 2021 V1.0 Code"].zfill(5))
    n21_from16 = collections.defaultdict(list)
    n21_transfer = collections.defaultdict(list)
    for r in c:
        n16, n21 = r["NOC 2016 V1.3 Code"], r["NOC 2021 V1.0 Code"]
        if not (n16 and n21):
            continue
        n16, n21 = n16.zfill(4), n21.zfill(5)
        note = r.get("Notes", "").lower()
        m = re.search(r"continues as (\d{5})", note)
        minor = len(targets16[n16]) > 1 and "part transferred" in note and m and m.group(1) != n21
        (n21_transfer if minor else n21_from16)[n21].append(n16)

    def stems(t):
        return {w[:5] for w in norm_title(t).split() if w not in STOP}

    noc_to_soc, soc_to_noc = {}, collections.defaultdict(set)
    unmapped = []
    for n21 in sorted(set(titles21) | set(n21_from16) | set(n21_transfer)):
        links, paths = {}, collections.Counter()
        for n16 in n21_from16.get(n21, []) or n21_transfer.get(n21, []):
            for soc, partial in n16_soc.get(n16, []):
                cur = links.get(soc)
                if cur is None:
                    links[soc] = {"soc": soc, "soc_title": soc_title.get(soc), "partial": partial, "via_noc2016": [n16]}
                else:
                    cur["partial"] = cur["partial"] and partial
                    if n16 not in cur["via_noc2016"]:
                        cur["via_noc2016"].append(n16)
                paths[soc] += 1
        lst = list(links.values())
        full = [x["soc"] for x in lst if not x["partial"]]
        if len(full) == 1:
            primary = full[0]
        elif lst:
            order = {x["soc"]: i for i, x in enumerate(lst)}
            nt = stems(titles21.get(n21, ""))
            primary = sorted(lst, key=lambda x: (-len(nt & stems(x["soc_title"] or "")), x["partial"],
                                                 -paths[x["soc"]], order[x["soc"]]))[0]["soc"]
        else:
            primary = None
            unmapped.append(n21)
        noc_to_soc[n21] = {"noc_title": titles21.get(n21), "soc_primary": primary, "soc": lst,
                           "transfer_from_noc2016": sorted(set(n21_transfer.get(n21, []))) or None,
                           "chained_via_transfer_only": not n21_from16.get(n21) and bool(n21_transfer.get(n21))}
        for x in lst:
            soc_to_noc[x["soc"]].add(n21)
    return ({"noc_to_soc": noc_to_soc,
             "soc_to_noc": {k: sorted(v) for k, v in sorted(soc_to_noc.items())},
             "soc_titles": dict(sorted(soc_title.items()))},
            {"noc2021_codes": len(noc_to_soc), "noc2021_unmapped": unmapped,
             "soc_codes_reached": len(soc_to_noc)})


# --------------------------------------------------------------------------- titles
def onet_version():
    """Newest release that has ALL three files (30.3 was seen with Occupation Data but no Alternate Titles)."""
    for v in ("31_2", "31_1", "31_0", "30_3", "30_2", "30_1", "30_0"):
        try:
            if all(http_get(ONET_BASE.format(v=v, f=f), timeout=60)[:20].startswith(b"O*NET-SOC Code")
                   for f in ("Occupation%20Data.txt", "Alternate%20Titles.txt",
                             "Sample%20of%20Reported%20Titles.txt")):
                return v
        except Exception:
            continue
    return "30_2"


def read_tsv(path):
    txt = open(path, encoding="utf-8", errors="replace").read()
    return list(csv.DictReader(io.StringIO(txt), delimiter="\t"))


def build_onet_titles():
    vfile = raw_path("onet", "VERSION.txt")
    have = all(os.path.exists(raw_path("onet", f)) for f in ("Occupation Data.txt", "Alternate Titles.txt",
                                                             "Sample of Reported Titles.txt"))
    if REFRESH or not have or not os.path.exists(vfile):
        v = onet_version()
        for f in ("Occupation Data.txt", "Alternate Titles.txt", "Sample of Reported Titles.txt"):
            download(ONET_BASE.format(v=v, f=f.replace(" ", "%20")), raw_path("onet", f), force=True)
        open(vfile, "w").write(v)
    v = open(vfile).read().strip().replace("_", ".")
    files = {f: read_tsv(raw_path("onet", f))
             for f in ("Occupation Data.txt", "Alternate Titles.txt", "Sample of Reported Titles.txt")}
    occ = {r["O*NET-SOC Code"]: r["Title"] for r in files["Occupation Data.txt"]}
    entries = {}

    def add(title, onet, source):
        soc = soc_fmt(onet)
        key = norm_title(title)
        if not key or not soc:
            return
        e = entries.setdefault(key, {"title": title.strip(), "soc": [], "onet": [], "sources": []})
        if soc not in e["soc"]:
            e["soc"].append(soc)
        if onet not in e["onet"]:
            e["onet"].append(onet)
        if source not in e["sources"]:
            e["sources"].append(source)

    for code, t in occ.items():
        add(t, code, "onet_title")
    for r in files["Sample of Reported Titles.txt"]:
        add(r["Reported Job Title"], r["O*NET-SOC Code"], "reported")
    for r in files["Alternate Titles.txt"]:
        add(r["Alternate Title"], r["O*NET-SOC Code"], "alternate")
        st = (r.get("Short Title") or "").strip()
        if st and st.lower() != "n/a":
            add(st, r["O*NET-SOC Code"], "alternate_short")
    soc_titles = {}
    for code, t in occ.items():
        s = soc_fmt(code)
        if code.endswith(".00") or s not in soc_titles:
            soc_titles[s] = t
    return ({"version": v, "titles": dict(sorted(entries.items())), "soc_titles": dict(sorted(soc_titles.items()))},
            {"onet_release": v, "title_keys": len(entries), "soc_codes": len(soc_titles)})


def build_noc_titles():
    el = read_csv_any(download(NOC_ELEM_URL, raw_path("noc", "noc-2021-v1.0-elements.csv"), force=REFRESH))
    st = read_csv_any(raw_path("noc", "noc-2021-v1.0-classification-structure.csv"))
    entries = {}

    def add(title, noc, source):
        key = norm_title(title)
        if not key:
            return
        e = entries.setdefault(key, {"title": title.strip(), "noc": [], "sources": []})
        if noc not in e["noc"]:
            e["noc"].append(noc)
        if source not in e["sources"]:
            e["sources"].append(source)

    for r in st:
        if r["Level"] == "5":
            add(r["Class title"], r["Code - NOC 2021 V1.0"], "noc_title")
    for r in el:
        if r["Level"] != "5":
            continue
        typ = r["Element Type Label English"]
        if typ in ("All examples", "Illustrative example(s)"):
            add(r["Element Description English"], r["Code - NOC 2021 V1.0"],
                "all_examples" if typ == "All examples" else "illustrative")
    return {"titles": dict(sorted(entries.items()))}, {"title_keys": len(entries)}


# --------------------------------------------------------------------------- Canada geography
def build_er():
    src = download(JOBBANK_URL, raw_path("jobbank", "wages2025.csv"), force=REFRESH)
    names = {}
    for r in read_csv_any(src):
        code = r["ER_Code_Code_RE"]
        if code.startswith("ER") and len(code) == 6:
            names[code[2:]] = {"er_code": code[2:], "er_name": r["ER_Name"], "prov": r["prov"]}
    names = dict(sorted(names.items()))

    dgrf = raw_path("statcan", "2021_98260004.csv")
    if not os.path.exists(dgrf):
        print(f"  NOTE: {os.path.relpath(dgrf)} not found - cd_to_er.json not written.\n"
              f"        Download the DGRF CSV from {DGRF_URL} and put it there.")
        return names, None, {"er_count": len(names), "cd_to_er": False}
    pairs = collections.defaultdict(collections.Counter)    # cd -> Counter(er) over dissemination blocks
    with open(dgrf, encoding="utf-8-sig", errors="replace", newline="") as fh:
        r = csv.reader(fh)
        hdr = [h.strip() for h in next(r)]
        icd = next(i for i, h in enumerate(hdr) if h.upper().startswith("CDDGUID"))
        ier = next(i for i, h in enumerate(hdr) if h.upper().startswith("ERDGUID"))
        for row in r:
            if len(row) <= max(icd, ier):
                continue
            cd, er = row[icd].strip()[-4:], row[ier].strip()[-4:]
            if cd.isdigit() and er.isdigit():
                pairs[cd][er] += 1
    cd_to_er = {}
    for cd, cnt in sorted(pairs.items()):
        (er, _), = cnt.most_common(1)
        cd_to_er[cd] = {"cd_code": cd, "er_code": er, "er_name": (names.get(er) or {}).get("er_name"),
                        "prov": PROV_ABBR.get(cd[:2]),
                        "er_split": sorted(cnt) if len(cnt) > 1 else None}   # Halton (3524) spans two ERs
    return names, cd_to_er, {"er_count": len(names), "cd_to_er": True, "cds": len(cd_to_er)}


def main():
    print("build_crosswalks: county -> area")
    c2a, m1 = build_county_to_area()
    write_json(os.path.join(XW, "county_to_area.json"), c2a)
    print("build_crosswalks: NOC <-> SOC")
    ns, m2 = build_noc_soc()
    write_json(os.path.join(XW, "noc_soc.json"), ns)
    print("build_crosswalks: O*NET titles")
    ot, m3 = build_onet_titles()
    write_json(os.path.join(XW, "onet_titles.json"), ot)
    print("build_crosswalks: NOC titles")
    nt, m4 = build_noc_titles()
    write_json(os.path.join(XW, "noc_titles.json"), nt)
    print("build_crosswalks: economic regions")
    er, cd_er, m5 = build_er()
    write_json(os.path.join(XW, "er_names.json"), er)
    if cd_er:
        write_json(os.path.join(XW, "cd_to_er.json"), cd_er)
    manifest = {"built": today(),
                "sources": {"cbsa": "OMB delineation July 2023 (Census List 1)",
                            "oews_areas": "BLS OEWS area definitions May 2025" if m1["bls_area_definitions"] else "CBSA codes only (BLS file missing)",
                            "noc_soc": "StatCan NOC 2016 v1.3 -> SOC 2018 + NOC 2016 v1.3 -> NOC 2021 v1.0",
                            "onet": f"O*NET {m3['onet_release']}",
                            "noc_titles": "NOC 2021 v1.0 classification structure + elements",
                            "er": "ESDC Job Bank wages 2025 (names); StatCan DGRF 2021 (CD->ER)"},
                "county_to_area": m1, "noc_soc": m2, "onet_titles": m3, "noc_titles": m4, "er": m5}
    write_json(os.path.join(XW, "manifest.json"), manifest, compact=False)
    print("done.")


if __name__ == "__main__":
    main()
