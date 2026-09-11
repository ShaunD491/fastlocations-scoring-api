#!/usr/bin/env python3
r"""
check_edo_roster.py
-------------------
Compares the dashboard's EDO list with the scorer's EDO master table and reports drift:

    dash/data/organizations.json   (edited in the Organizations editor, port 5002)
    edo_master_table_dual.json     (what the scorer routes leads through)

The Organizations editor syncs the master on every save (edo_master_sync.py), so drift now means
an edit made outside it - by hand or by a script - that the master has not picked up. Until it
does, an EDO added on the dashboard gets no leads. The editor's "Sync all records now" fixes it.

    python check_edo_roster.py
    python check_edo_roster.py --orgs "C:\path\to\organizations.json" --json roster_report.json

Read-only: it never writes to either file. Exit code 0 = in sync (or cosmetic differences only),
1 = something affects lead routing or territories. Deterministic; no network.

Findings are grouped by what they break:
  ROUTING    leads go to the wrong EDO or to nobody - fix before deploying
  TERRITORY  the EDO's territory_geoids were derived from a value that has since changed
  DISPLAY    name / city / links differ; only what the result card shows
"""
import argparse, json, math, os, sys
from collections import Counter

from build_edo_indexes import build_indexes

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ORGS = os.path.normpath(os.path.join(HERE, "..", "dash", "data", "organizations.json"))
MASTER = os.path.join(HERE, "edo_master_table_dual.json")
US_INDEX = os.path.join(HERE, "edo_fips_index.json")
CA_INDEX = os.path.join(HERE, "edo_ca_cd_index.json")

MOVED_KM = 1.0   # a point moving further than this may put the EDO in a different county / CD

# dash field -> master field, and which group a mismatch belongs to
FIELDS = [
    ("Country",      "country",      "ROUTING"),     # decides geo_system (US_FIPS vs CA_CSD)
    ("Category",     "category",     "TERRITORY"),   # decides territory_basis (whole_state, eia861, ...)
    ("State",        "state",        "TERRITORY"),   # whole-state / province expansion keys off it
    ("Organization", "organization", "DISPLAY"),
    ("City",         "city",         "DISPLAY"),
    ("Embed",        "embed_url",    "DISPLAY"),
    ("AI Link",      "ai_link",      "DISPLAY"),
]
GROUPS = ("ROUTING", "TERRITORY", "DISPLAY")
FIX = {
    "ROUTING":   "Fix edo_master_table_dual.json, then run build_edo_indexes.py and redeploy.",
    "TERRITORY": "Re-check that EDO's territory_geoids (and territory_basis) in the master table, "
                 "then run build_edo_indexes.py.",
    "DISPLAY":   "Copy the dashboard value into the master table if the dashboard is right.",
}


def clean(v):
    return " ".join(str(v if v is not None else "").split())


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def km_between(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def keyed(rows, id_field, label, findings):
    """rows -> {id: row}, reporting blank and duplicate ids (a duplicate hides one of the rows)."""
    out = {}
    for i, r in enumerate(rows):
        oid = clean(r.get(id_field))
        if not oid:
            findings.append(("ROUTING", None, f"{label} row {i} has no {id_field}",
                             clean(r.get("Organization") or r.get("organization"))))
            continue
        if oid in out:
            findings.append(("ROUTING", oid, f"duplicate {id_field} in {label}",
                             f"{clean(out[oid].get('Organization') or out[oid].get('organization'))} / "
                             f"{clean(r.get('Organization') or r.get('organization'))}"))
            continue
        out[oid] = r
    return out


def index_drift(master):
    """Compare the shipped routing indexes with what build_edo_indexes.py would write now."""
    us, ca, _ = build_indexes(master)
    drift = []
    for path, want in ((US_INDEX, us), (CA_INDEX, ca)):
        if not os.path.exists(path):
            drift.append(f"{os.path.basename(path)} is missing")
            continue
        have = json.load(open(path, encoding="utf-8"))
        norm_have = {k: sorted(map(str, v), key=int) for k, v in have.items()}
        changed = sorted(k for k in set(want) | set(norm_have) if want.get(k) != norm_have.get(k))
        if changed:
            drift.append(f"{os.path.basename(path)} is stale: {len(changed)} key(s) differ "
                         f"from the master table (e.g. {', '.join(changed[:5])})")
    return drift


def check(orgs_path, master_path):
    orgs = json.load(open(orgs_path, encoding="utf-8"))
    master = json.load(open(master_path, encoding="utf-8"))
    findings = []   # (group, objectid, what, detail)

    D = keyed(orgs, "OBJECTID", "dashboard", findings)
    M = keyed(master, "objectid", "master", findings)

    for oid in sorted(set(D) - set(M), key=lambda x: int(x) if x.isdigit() else 0):
        r = D[oid]
        findings.append(("ROUTING", oid, "on the dashboard but NOT in the master table - gets no leads",
                         f"{clean(r.get('Organization'))} ({clean(r.get('Category'))}, "
                         f"{clean(r.get('City'))} {clean(r.get('State'))})"))
    for oid in sorted(set(M) - set(D), key=lambda x: int(x) if x.isdigit() else 0):
        r = M[oid]
        findings.append(("ROUTING", oid, "in the master table but NOT on the dashboard - still receives leads",
                         f"{clean(r.get('organization'))} ({clean(r.get('category'))}, {clean(r.get('state'))})"))

    for oid in sorted(set(D) & set(M), key=lambda x: int(x) if x.isdigit() else 0):
        d, m = D[oid], M[oid]
        name = clean(m.get("organization")) or clean(d.get("Organization"))
        for df, mf, group in FIELDS:
            a, b = clean(d.get(df)), clean(m.get(mf))
            if a != b:
                findings.append((group, oid, f"{df} differs - {name}", f"dashboard: {a!r}  |  master: {b!r}"))

        lat1, lon1, lat2, lon2 = (num(d.get("Latitude")), num(d.get("Longitude")),
                                  num(m.get("latitude")), num(m.get("longitude")))
        if None in (lat1, lon1, lat2, lon2):
            if (lat1, lon1) != (lat2, lon2):
                findings.append(("TERRITORY", oid, f"missing or unreadable coordinates - {name}",
                                 f"dashboard: {d.get('Latitude')},{d.get('Longitude')}  |  "
                                 f"master: {m.get('latitude')},{m.get('longitude')}"))
        else:
            dist = km_between(lat1, lon1, lat2, lon2)
            if dist > MOVED_KM:
                findings.append(("TERRITORY", oid, f"location moved {dist:.1f} km - {name}",
                                 f"home county/CD may have changed (master home: "
                                 f"{clean(m.get('home_county_name') or m.get('home_cd_name'))})"))

    for oid, r in M.items():
        if not r.get("territory_geoids"):
            findings.append(("ROUTING", oid, f"no territory_geoids - {clean(r.get('organization'))}",
                             "this EDO cannot receive any leads"))

    for msg in index_drift(master):
        findings.append(("ROUTING", None, msg, "run build_edo_indexes.py"))

    return findings, len(D), len(M)


def main():
    ap = argparse.ArgumentParser(description="Compare dashboard organizations.json with the EDO master table.")
    ap.add_argument("--orgs", default=DEFAULT_ORGS, help="dashboard organizations.json (default: %(default)s)")
    ap.add_argument("--master", default=MASTER, help="EDO master table (default: %(default)s)")
    ap.add_argument("--json", metavar="PATH", help="also write the findings to this JSON file")
    args = ap.parse_args()

    for p in (args.orgs, args.master):
        if not os.path.exists(p):
            sys.exit(f"Not found: {p}")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # accented EDO names on Windows

    findings, n_dash, n_master = check(args.orgs, args.master)
    counts = Counter(g for g, *_ in findings)

    print(f"Dashboard: {n_dash} EDOs  ({args.orgs})")
    print(f"Master:    {n_master} EDOs  ({args.master})\n")
    if not findings:
        print("In sync - no differences.")
    for group in GROUPS:
        rows = [f for f in findings if f[0] == group]
        if not rows:
            continue
        print(f"{group} ({len(rows)})  ->  {FIX[group]}")
        for _, oid, what, detail in rows:
            print(f"  {'#' + oid if oid else '':>6}  {what}")
            if detail:
                print(f"          {detail}")
        print()
    print("Summary: " + ", ".join(f"{g} {counts.get(g, 0)}" for g in GROUPS))

    if args.json:
        json.dump({"orgs": args.orgs, "master": args.master, "counts": {g: counts.get(g, 0) for g in GROUPS},
                   "findings": [{"group": g, "objectid": o, "issue": w, "detail": dt}
                                for g, o, w, dt in findings]},
                  open(args.json, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
        print(f"Wrote {args.json}")

    sys.exit(1 if counts.get("ROUTING") or counts.get("TERRITORY") else 0)


if __name__ == "__main__":
    main()
