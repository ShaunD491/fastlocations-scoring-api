#!/usr/bin/env python3
r"""
build_ca_infrastructure.py
--------------------------
Builds a Canadian census-division infrastructure asset inventory from StatCan's Open Database of
Infrastructure (ODI v2) GeoPackages, replacing the crude "CMA = high / else = mid" infrastructure
score with a real, CD-specific asset count.

WHY: the CA infrastructure dimension was a binary (ca_infra_pts). The US side scores local power
generation + state grade. ODI gives point-level Canadian assets (grid, water, wastewater, ports,
airports, telecom, bridges/tunnels, oil & gas, low-carbon, solid waste) that roll up per CD.

SOURCE: StatCan Open Database of Infrastructure v2 (ODI), one GeoPackage per category, e.g.
odi_airports.gpkg. Each layer carries `csduid` (census subdivision); CDUID = first 4 digits.
    https://www.statcan.gc.ca/en/lode/databases/odi

USAGE (local, stdlib only - GeoPackage is SQLite):
    python build_ca_infrastructure.py "C:\path\to\folder_with_gpkg_files"

Writes ca_infrastructure.json:
  { "<cduid>": {"airports": n, "electric_grid": n, ..., "_total": n} }
Counts are raw asset counts per category; the scorer applies the weighting so it can be tuned
without rebuilding. Files whose layer lacks `csduid` are reported and skipped (nothing invented).
"""
import glob, json, os, sqlite3, sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "ca_infrastructure.json")

# filename fragment -> output key
CATEGORIES = {
    "airports": "airports",
    "bridges_tunnels": "bridges_tunnels",
    "electric_grid": "electric_grid",
    "low_carbon": "low_carbon",
    "oil_gas": "oil_gas",
    "ports_marinas": "ports_marinas",
    "potable_water": "potable_water",
    "solid_waste": "solid_waste",
    "telecom": "telecom",
    "wastewater_stormwater": "wastewater",
}


def layer_tables(con):
    try:
        return [r[0] for r in con.execute("SELECT table_name FROM gpkg_contents")]
    except sqlite3.Error:
        return [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'gpkg_%' "
            "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'rtree_%'")]


def main():
    folder = sys.argv[1] if len(sys.argv) > 1 else HERE
    files = sorted(glob.glob(os.path.join(folder, "*.gpkg")))
    if not files:
        sys.exit(f"no .gpkg files found in {folder}")

    out, skipped = {}, []
    for path in files:
        base = os.path.basename(path).lower()
        key = next((v for frag, v in CATEGORIES.items() if frag in base), None)
        if not key:
            key = os.path.splitext(base)[0].replace("odi_", "")
        con = sqlite3.connect(path)
        try:
            tables = layer_tables(con)
            if not tables:
                skipped.append((base, "no layer table")); continue
            tbl = tables[0]
            cols = [d[1].lower() for d in con.execute('PRAGMA table_info("%s")' % tbl)]
            if "csduid" not in cols:
                skipped.append((base, f"no csduid column (has: {cols[:8]})")); continue
            n = 0
            for (csduid,) in con.execute('SELECT csduid FROM "%s"' % tbl):
                s = str(csduid or "").strip()
                if len(s) < 4 or not s[:4].isdigit():
                    continue
                cd = s[:4]                                   # CDUID = first 4 of CSDUID
                rec = out.setdefault(cd, {})
                rec[key] = rec.get(key, 0) + 1
                n += 1
            print(f"  {base:<34} {key:<20} {n:>7} assets")
        finally:
            con.close()

    for cd, rec in out.items():
        rec["_total"] = sum(v for k, v in rec.items() if not k.startswith("_"))

    json.dump(out, open(OUT, "w", encoding="utf-8"), separators=(",", ":"))
    print(f"\nWrote {OUT}: {len(out)} census divisions")
    if skipped:
        print("SKIPPED:")
        for b, why in skipped:
            print(f"  {b}: {why}")
    for cd, nm in (("3530", "Waterloo"), ("3520", "Toronto"), ("5915", "Vancouver")):
        if cd in out:
            r = dict(out[cd])
            print(f"  {cd} {nm}: total={r.pop('_total')} {r}")


if __name__ == "__main__":
    main()
