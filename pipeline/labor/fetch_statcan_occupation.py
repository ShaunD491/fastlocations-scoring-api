#!/usr/bin/env python3
r"""
fetch_statcan_occupation.py  (Phase 1)
---------------------------
Canadian employment by occupation (NOC 2021) from the 2021 Census of Population, where workers live.
This is the Canadian employment count: Job Bank publishes wages only, so `emp` for Canadian rows
comes from here and is flagged emp_source="census_2021".

Two StatCan tables, streamed (the CSVs are 3-10 GB unzipped, never loaded into memory):
  98-10-0593  Class of worker by occupation UNIT GROUP (5-digit NOC), labour force status, age, gender:
              Canada, provinces/territories, CMAs and CAs (with parts)        -> statcan_occ_cma.json
  98-10-0591  Class of worker incl. job permanency by occupation MINOR GROUP (4-digit NOC) ...:
              Canada, provinces/territories and census divisions               -> statcan_occ_cd.json
  The CD table is what rolls up to economic regions (with cd_to_er.json) and lines up with the
  293 census divisions the scorer already uses.

Filters: Labour force status = Employed, Total - Age, Total - Gender, Statistics = Count, and the
"Total - Class of worker" column. Every NOC level present in the table is kept (broad 1-digit through
unit 5-digit), with noc_level so Phase 2 can pick the finest available.

Downloads (open data, ~1.1 GB and ~0.3 GB zipped) go to raw/statcan/ on first run:
    https://www150.statcan.gc.ca/n1/tbl/csv/98100593-eng.zip
    https://www150.statcan.gc.ca/n1/tbl/csv/98100591-eng.zip

OUTPUT rows: country="CA", geo_level (nation/province/cma/ca/cma_part/cd), geo_code, geo_name, DGUID,
  occ_code_native (NOC code, or "total"), occ_title, noc_level, soc_code_harmonized (5-digit only),
  emp, emp_source="census_2021", source.

USAGE
  py -3 fetch_statcan_occupation.py            (both tables)
  py -3 fetch_statcan_occupation.py --cd-only  /  --cma-only
"""
import csv
import io
import os
import re
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import XW, download, load_json, out_path, raw_path, write_csv, write_json

TABLES = {
    "cma": ("98100593", "statcan_occ_cma.json"),
    "cd": ("98100591", "statcan_occ_cd.json"),
}
# DGUID schema codes (2021 vintage): A0000 Canada, A0002 province, A0003 CD, S0503 CMA, S0504 CA.
# Province parts of cross-border CMAs (Ottawa-Gatineau etc.) use other schemas and are skipped; the
# whole CMA row is kept.
DGUID_LEVEL = {"A000011124": "nation", "A0002": "province", "A0003": "cd", "S0503": "cma", "S0504": "ca"}


def level_of(dguid):
    s = dguid[4:]
    if s.startswith("A000011124"):
        return "nation", "00"
    for pre, lvl in DGUID_LEVEL.items():
        if s.startswith(pre):
            return lvl, s[len(pre):]
    return None, None


def parse(key, xw):
    pid, outname = TABLES[key]
    zp = download(f"https://www150.statcan.gc.ca/n1/tbl/csv/{pid}-eng.zip", raw_path("statcan", f"{pid}-eng.zip"),
                  min_bytes=10_000_000)
    print(f"  streaming {pid}.csv")
    rows = []
    with zipfile.ZipFile(zp) as z:
        with z.open(f"{pid}.csv") as raw:
            fh = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
            r = csv.reader(fh)
            hdr = next(r)
            col = {h: i for i, h in enumerate(hdr)}

            def find(prefix):
                return next(i for h, i in col.items() if h.startswith(prefix))

            i_geo, i_dg = col["GEO"], col["DGUID"]
            i_lfs, i_age, i_gen = find("Labour force status"), find("Age"), find("Gender")
            i_stat = find("Statistics")
            i_occ = find("Occupation")
            # total class-of-worker column; StatCan puts a non-breaking space after "Total -"
            i_val = next(i for i, h in enumerate(hdr) if re.search(r":Total\s*-\s", h))
            print(f"    occupation column: {hdr[i_occ]}\n    value column: {hdr[i_val]}")
            seen_lfs = set()
            n = 0
            for row in r:
                n += 1
                if n % 2_000_000 == 0:
                    print(f"    {n:,} lines, {len(rows):,} kept")
                if len(row) <= i_val:
                    continue
                seen_lfs.add(row[i_lfs])
                if (row[i_lfs] != "Employed" or not row[i_age].startswith("Total") or
                        not row[i_gen].startswith("Total") or row[i_stat] != "Count"):
                    continue
                lvl, code = level_of(row[i_dg])
                if lvl is None:
                    continue
                occ = row[i_occ].strip()
                m = re.match(r"^(\d{1,5})\s+(.*)$", occ)
                if m:
                    noc, title = m.group(1), m.group(2)
                elif occ.startswith("Total"):
                    noc, title = "total", "All occupations"
                else:
                    continue                                    # "Occupation - not applicable"
                v = row[i_val].strip()
                try:
                    emp = int(float(v))
                except ValueError:
                    emp = None
                rows.append({
                    "country": "CA", "geo_level": lvl, "geo_code": code, "geo_name": row[i_geo], "DGUID": row[i_dg],
                    "occ_code_native": noc, "occ_title": title,
                    "noc_level": "total" if noc == "total" else
                                 {1: "broad", 2: "major", 3: "sub_major", 4: "minor", 5: "unit"}[len(noc)],
                    "soc_code_harmonized": (xw.get(noc) or {}).get("soc_primary") if len(noc) == 5 else None,
                    "emp": emp, "emp_source": "census_2021", "suppressed_flag": emp is None,
                    "source": f"StatCan 2021 Census, table {pid[:2]}-{pid[2:4]}-{pid[4:]}",
                })
    print(f"    {n:,} lines read; labour force status values seen: {sorted(seen_lfs)}")
    return rows, outname


def main():
    xw = load_json(os.path.join(XW, "noc_soc.json"))["noc_to_soc"]
    keys = ["cd"] if "--cd-only" in sys.argv else ["cma"] if "--cma-only" in sys.argv else ["cd", "cma"]
    for k in keys:
        print(f"fetch_statcan_occupation: {k}")
        rows, name = parse(k, xw)
        write_json(out_path(name), rows)
        write_csv(out_path(name.replace(".json", ".csv")), rows, list(rows[0].keys()))
        lv = {}
        for r in rows:
            lv[(r["geo_level"], r["noc_level"])] = lv.get((r["geo_level"], r["noc_level"]), 0) + 1
        for (g, nl), c in sorted(lv.items()):
            print(f"    {g:12s} {nl:10s} {c:8,}")


if __name__ == "__main__":
    main()
