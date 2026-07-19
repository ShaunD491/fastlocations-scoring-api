#!/usr/bin/env python3
r"""
build_regional_territories.py
-----------------------------
Resolves Regional Development Agency member-county lists to county FIPS and writes them into
edo_master_table_dual.json as `territory_geoids`.

WHY: 54 Regional Development Agencies were built with territory_basis='home_county_only_INCOMPLETE'
and a territory of exactly ONE county (their HQ) -- even though a regional partnership is multi-county
by definition. So leads anywhere in their real footprint routed to somebody else, or nobody.

SOURCE: regional_edo_members.json -- hand-curated from each agency's own site, with a `source` URL
per agency. There is no single national dataset for this (unlike EIA-861 for utilities), so it is
researched per agency and grows incrementally. Agencies absent from that file keep their existing
home-county territory and stay flagged INCOMPLETE, so the scorer keeps discounting them.

USAGE:
    python build_regional_territories.py
Then ALWAYS rerun:
    python build_edo_indexes.py
"""
import json, os, re, sys, unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
MASTER = os.path.join(HERE, "edo_master_table_dual.json")
FEATURES = os.path.join(HERE, "county_features.json")
MEMBERS = os.path.join(HERE, "regional_edo_members.json")


def cnorm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(county|parish|borough|census area|city and borough|municipality)\b", "", s)
    return re.sub(r"[^a-z0-9]", "", s)


def main():
    feat = json.load(open(FEATURES, encoding="utf-8"))
    # (state, normalized-name) -> fips. Independent cities keep their 'city' suffix in NAME, so a
    # bare "Charlottesville" won't collide with a same-named county; we try both spellings below.
    lookup = {}
    for f, d in feat.items():
        lookup[(d["ST_ABBREV"], cnorm(d["NAME"]))] = f
    members = json.load(open(MEMBERS, encoding="utf-8"))["members"]
    master = json.load(open(MASTER, encoding="utf-8"))
    by_id = {r["objectid"]: r for r in master}

    changed, missing = [], []
    for oid, rec in members.items():
        if oid not in by_id:
            missing.append((oid, rec.get("org", ""), "objectid not in master table")); continue
        fips = set()
        for st, names in (rec.get("counties") or {}).items():
            for nm in names:
                f = (lookup.get((st, cnorm(nm)))
                     or lookup.get((st, cnorm(nm + " city")))
                     or lookup.get((st, cnorm(nm + " County"))))
                if f:
                    fips.add(f)
                else:
                    missing.append((oid, rec.get("org", ""), f"county not matched: {nm}, {st}"))
        if not fips:
            continue
        r = by_id[oid]
        before = r.get("territory_county_count") or 0
        geo = sorted(fips)
        r["territory_geoids"] = geo
        r["territory_fips"] = geo
        r["territory_county_count"] = len(geo)
        r["territory_basis"] = "member_county_list"          # clears the _INCOMPLETE flag
        r["territory_source"] = rec.get("source", "")
        changed.append((oid, r["organization"], before, len(geo)))

    json.dump(master, open(MASTER, "w", encoding="utf-8"), indent=1, ensure_ascii=False)

    still = [r for r in master
             if r.get("category") == "Regional Development Agency"
             and str(r.get("territory_basis", "")).endswith("INCOMPLETE")]
    changed.sort(key=lambda t: -t[3])
    print(f"Resolved {len(changed)} regional agencies from regional_edo_members.json\n")
    for oid, org, b, a in changed:
        print(f"  #{oid:>4} {org[:44]:<44} {b:>2} -> {a:>3} counties")
    print(f"\n  regional agencies still unresolved: {len(still)}")
    if missing:
        print(f"\n  WARNING {len(missing)} problem(s):")
        for oid, org, why in missing[:20]:
            print(f"    #{oid} {org[:34]:<34} {why}")
    print(f"\nWrote {MASTER}\n>>> now run:  python build_edo_indexes.py")


if __name__ == "__main__":
    main()
