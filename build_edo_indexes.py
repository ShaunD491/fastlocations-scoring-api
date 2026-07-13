#!/usr/bin/env python3
r"""
build_edo_indexes.py
--------------------
Rebuilds the scorer's lead-routing indexes from the EDO master table:

    edo_master_table_dual.json  (source of truth)
        -> edo_fips_index.json      (US:  county FIPS  -> [objectid])
        -> edo_ca_cd_index.json     (CA:  CD  CDUID    -> [objectid])

Run this whenever you ADD or REMOVE an EDO in edo_master_table_dual.json (or change any
EDO's territory_geoids). The scorer reads these two indexes to find which EDO customer(s)
serve each county / census division, so they must be regenerated after every roster change.

    python build_edo_indexes.py

Deterministic; no network. US objectids are routed by US_FIPS geo_system, Canadian by CA.
"""
import json, os
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
MASTER = os.path.join(HERE, "edo_master_table_dual.json")
US_OUT = os.path.join(HERE, "edo_fips_index.json")
CA_OUT = os.path.join(HERE, "edo_ca_cd_index.json")


def main():
    master = json.load(open(MASTER, encoding="utf-8"))
    us, ca = defaultdict(list), defaultdict(list)
    unresolved = []
    for r in master:
        oid = r.get("objectid")
        geoids = r.get("territory_geoids") or []
        if not geoids:
            unresolved.append((oid, r.get("organization", "")))
            continue
        bucket = ca if r.get("geo_system") == "CA_CSD" else us   # Canadian EDOs route by CD (CDUID)
        for g in geoids:
            if oid not in bucket[g]:
                bucket[g].append(oid)

    # stable ordering so diffs are clean in git
    us_sorted = {k: sorted(v, key=int) for k, v in sorted(us.items())}
    ca_sorted = {k: sorted(v, key=int) for k, v in sorted(ca.items())}
    json.dump(us_sorted, open(US_OUT, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    json.dump(ca_sorted, open(CA_OUT, "w", encoding="utf-8"), indent=1, ensure_ascii=False)

    print(f"{len(master)} EDOs -> US: {len(us_sorted)} county keys, CA: {len(ca_sorted)} CD keys")
    print(f"Wrote {os.path.basename(US_OUT)} and {os.path.basename(CA_OUT)}")
    if unresolved:
        print(f"\n{len(unresolved)} EDO(s) have NO territory_geoids (won't route any leads -- fix the source):")
        for oid, org in unresolved:
            print(f"  #{oid}  {org}")


if __name__ == "__main__":
    main()
