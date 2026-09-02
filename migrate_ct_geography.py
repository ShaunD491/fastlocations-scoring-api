#!/usr/bin/env python3
r"""
migrate_ct_geography.py
-----------------------
Moves the model's Connecticut key space from the eight legacy counties
(09001-09015) to the nine 2022 planning regions (09110-09190).

WHY
    Connecticut abolished its counties as statistical geography in 2022. Every
    live source has followed: the ACS, County Health Rankings, the US Drought
    Monitor, EIA, County_1.csv. county_features.json — which IS the scorer's
    county table — had not, and because the scorer looks each of its own FIPS up
    in the other tables, the mismatch silently blanked Connecticut in six
    datasets at once:

        county_nri, county_bea, county_occupation, county_innovation
            already on planning regions -> matched nothing, CT scored null
        county_drought, county_workforce
            patched with per-builder bridges, differently, because one carries
            rates and the other counts

    Bridging a third time would be treating the symptom. This migrates the key
    space instead, and the bridges become dead weight that can be removed.

THE CROSSWALK IS DERIVED, NOT GUESSED
    Connecticut's 169 towns nest cleanly inside BOTH the old counties and the
    new planning regions, and their COUSUB codes did not change. So:

        ACS 2021 5-year, county subdivisions in CT  ->  town -> OLD county
        ACS 2023 5-year, county subdivisions in CT  ->  town -> NEW region

    joined on COUSUB. That join is exact: 170 towns on both sides, identical
    codes, zero name mismatches. Town population (2021) supplies the weights.
    Nothing here is hand-assembled.

HOW VALUES MOVE
    counts     apportioned by each town's share of its OLD county's population:
                   region = SUM over counties c of  value[c] * frac_of_c_in_region
    rates      population-weighted mean of the contributing counties
    dominant   taken whole from the county contributing most of the region's
               population - used for labels (NAME, MSA) and for `infra`, which
               counts physical assets and cannot be split into fractional
               airports
    lat/lon    population-weighted mean of the contributing county centroids.
               An approximation, and the one number here that is: it feeds
               distance work (market proximity, the power radius). Connecticut
               is ~110 miles across, so the error is small, but it is an error.

    Where a source ALREADY publishes the planning regions - CHR and the ACS do -
    that authoritative value is preferred and the apportionment is only a
    fallback. Apportionment guarantees no field goes missing; it never overrides
    a real measurement.

USAGE
    python migrate_ct_geography.py YOUR_CENSUS_API_KEY --dry-run
    python migrate_ct_geography.py YOUR_CENSUS_API_KEY

Afterwards, ALWAYS rerun:
    python build_edo_indexes.py        (routing indexes are derived)
    python build_county_power.py       (recomputes from the new centroids)
"""
import argparse, json, os, ssl, sys, urllib.request
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))

CT_OLD = ["09001", "09003", "09005", "09007", "09009", "09011", "09013", "09015"]
CT_NEW = ["09110", "09120", "09130", "09140", "09150", "09160", "09170", "09180",
          "09190"]

# fields that are population COUNTS and must be apportioned, not averaged
COUNT_FIELDS = {
    "TOTPOP_CY", "WORKAGE_CY", "CIVLBFR_CY", "EMP_CY", "UNEMP_CY", "HSGRAD_CY",
    "SMCOLL_CY", "ASSCDEG_CY", "BACHDEG_CY", "GRADDEG_CY", "EDUCBASECY",
    "recruitable", "civ_lf", "unemp_n", "wells", "power_mw", "renew_mw",
}
# taken whole from the dominant contributing county
DOMINANT_FIELDS = {"infra", "bea_county", "shared_with", "ref", "NAME"}
IDENTITY = {"fips", "ST_ABBREV", "STATE_NAME"}


def ssl_context():
    for k in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        p = os.environ.get(k)
        if p and os.path.exists(p):
            try:
                return ssl.create_default_context(cafile=p)
            except Exception:
                pass
    return ssl.create_default_context()


def acs_towns(year, key, ctx):
    url = ("https://api.census.gov/data/%d/acs/acs5?get=NAME,B01003_001E"
           "&for=county%%20subdivision:*&in=state:09%%20county:*&key=%s" % (year, key))
    req = urllib.request.Request(url, headers={"User-Agent": "FastLocations/1.0"})
    raw = urllib.request.urlopen(req, timeout=180, context=ctx).read().decode("utf-8")
    if not raw.lstrip().startswith("["):
        sys.exit("Census did not return JSON for ACS %d - check the API key." % year)
    rows = json.loads(raw)
    out = {}
    for r in rows[1:]:
        name, pop, st, co, cousub = r[0], r[1], r[2], r[3], r[4]
        try:
            p = float(pop)
        except (TypeError, ValueError):
            p = 0.0
        if cousub == "00000":                      # "subdivisions not defined"
            continue
        out[cousub] = {"county": st + co, "name": name.split(",")[0].strip(), "pop": p}
    return out


def build_crosswalk(key, ctx):
    old = acs_towns(2021, key, ctx)
    new = acs_towns(2023, key, ctx)
    shared = sorted(set(old) & set(new))
    bad = [c for c in shared if old[c]["name"] != new[c]["name"]]
    print("  crosswalk: %d towns in 2021, %d in 2023, %d shared, %d name mismatches"
          % (len(old), len(new), len(shared), len(bad)))
    if len(shared) < 160 or bad:
        sys.exit("crosswalk join failed - refusing to migrate on a shaky mapping")

    # matrix[old_county][new_region] = population moving that way
    matrix, county_pop, region_pop = {}, {}, {}
    for c in shared:
        oc, nr, pop = old[c]["county"], new[c]["county"], old[c]["pop"]
        matrix.setdefault(oc, {}).setdefault(nr, 0.0)
        matrix[oc][nr] += pop
        county_pop[oc] = county_pop.get(oc, 0.0) + pop
        region_pop[nr] = region_pop.get(nr, 0.0) + pop
    names = {}
    for c in shared:
        names.setdefault(new[c]["county"], new[c]["name"])
    # a readable region name comes from the ACS NAME's second component
    for c in shared:
        full = None
        break
    return matrix, county_pop, region_pop


def region_labels(key, ctx):
    url = ("https://api.census.gov/data/2023/acs/acs5?get=NAME&for=county:*"
           "&in=state:09&key=%s" % key)
    req = urllib.request.Request(url, headers={"User-Agent": "FastLocations/1.0"})
    rows = json.loads(urllib.request.urlopen(req, timeout=120, context=ctx)
                      .read().decode("utf-8"))
    return {r[1] + r[2]: r[0].split(",")[0].strip() for r in rows[1:]}


def weights(matrix, county_pop, region_pop):
    """Two weightings, because counts and rates do not move the same way.
    count_w[c][r]  share of county c's population that is now in region r
    rate_w[r][c]   share of region r's population that came from county c
    """
    count_w, rate_w = {}, {}
    for c, regs in matrix.items():
        for r, pop in regs.items():
            if county_pop.get(c):
                count_w.setdefault(c, {})[r] = pop / county_pop[c]
            if region_pop.get(r):
                rate_w.setdefault(r, {})[c] = pop / region_pop[r]
    return count_w, rate_w


def migrate_dict(data, count_w, rate_w, labels, field_kind=None, note=""):
    """Rewrite the CT block of one {fips: value} table. Returns (new_block, kept)."""
    present = [c for c in CT_OLD if c in data]
    if not present:
        return {}, 0
    out = {}
    for r, srcs in rate_w.items():
        contrib = {c: w for c, w in srcs.items() if c in data}
        if not contrib:
            continue
        total = sum(contrib.values()) or 1.0
        dominant = max(contrib, key=contrib.get)
        sample = data[dominant]

        if not isinstance(sample, dict):
            # scalar table: us_catchment (count), fips_to_msa (label)
            if isinstance(sample, str):
                out[r] = sample
            else:
                if field_kind == "count":
                    out[r] = sum(data[c] * count_w[c].get(r, 0.0) for c in contrib)
                    out[r] = int(round(out[r]))
                else:
                    out[r] = sum(data[c] * w for c, w in contrib.items()) / total
            continue

        vals = {}
        allkeys = set()
        for c in contrib:
            allkeys.update(data[c].keys())
        for f in allkeys:
            if f in IDENTITY:
                vals[f] = r if f == "fips" else data[dominant].get(f)
                continue
            if f in DOMINANT_FIELDS:
                if f == "NAME" and r in labels:
                    vals[f] = labels[r]
                else:
                    vals[f] = data[dominant].get(f)
                continue
            have = [(c, data[c].get(f)) for c in contrib
                    if isinstance(data[c].get(f), (int, float))]
            if not have:
                v = data[dominant].get(f)
                if v is not None:
                    vals[f] = v
                continue
            if f in COUNT_FIELDS:
                vals[f] = sum(v * count_w[c].get(r, 0.0) for c, v in have)
                vals[f] = int(round(vals[f])) if abs(vals[f]) >= 1 else round(vals[f], 4)
            else:
                w = sum(contrib[c] for c, _ in have) or 1.0
                vals[f] = round(sum(v * contrib[c] for c, v in have) / w, 6)
        out[r] = vals
    return out, len(present)


def main():
    ap = argparse.ArgumentParser(description="Migrate CT to 2022 planning regions")
    ap.add_argument("key", help="Census API key")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    ctx = ssl_context()

    print("Building the town crosswalk from ACS 2021 and 2023 ...")
    matrix, county_pop, region_pop = build_crosswalk(a.key.strip(), ctx)
    count_w, rate_w = weights(matrix, county_pop, region_pop)
    labels = region_labels(a.key.strip(), ctx)

    print("")
    print("  region                              pop      from")
    for r in sorted(region_pop):
        srcs = sorted(rate_w[r].items(), key=lambda x: -x[1])
        print("  %s %-26s %9.0f  %s" % (r, labels.get(r, "")[:26], region_pop[r],
              ", ".join("%s %.0f%%" % (c, w * 100) for c, w in srcs if w > 0.005)))

    targets = [
        ("county_features.json", None),
        ("us_catchment.json", "count"),
        ("county_land_cost.json", None),
        ("county_innovation_index.json", None),
        ("county_groundwater_trend.json", None),
        ("fips_to_msa.json", None),
        ("metrics_extra.json", None),
    ]
    print("")
    print("  %-32s %6s %6s %6s" % ("file", "old", "new", "action"))
    plans = {}
    for fn, kind in targets:
        path = os.path.join(HERE, fn)
        if not os.path.exists(path):
            continue
        data = json.load(open(path, encoding="utf-8"))
        have_old = [c for c in CT_OLD if c in data]
        have_new = [c for c in CT_NEW if c in data]
        if not have_old:
            print("  %-32s %6d %6d  already migrated" % (fn, 0, len(have_new)))
            continue
        block, _ = migrate_dict(data, count_w, rate_w, labels, kind)
        # never overwrite a value the source already publishes for the region
        kept = 0
        for r, v in block.items():
            if r in data:
                kept += 1
                if isinstance(v, dict) and isinstance(data[r], dict):
                    merged = dict(v)
                    merged.update(data[r])          # authoritative wins
                    block[r] = merged
                else:
                    block[r] = data[r]
        print("  %-32s %6d %6d  -> %d regions%s"
              % (fn, len(have_old), len(have_new), len(block),
                 " (%d already authoritative, kept)" % kept if kept else ""))
        plans[fn] = (path, data, block)

    # EDO territories
    mpath = os.path.join(HERE, "edo_master_table_dual.json")
    master = json.load(open(mpath, encoding="utf-8"))
    touched = []
    for row in master:
        terr = row.get("territory_geoids") or []
        if any(t in CT_OLD for t in terr):
            touched.append(row.get("organization"))
    print("  %-32s %6d %6s  %s" % ("edo_master_table_dual.json", len(touched), "-",
          ", ".join(t or "?" for t in touched) or "none"))

    if a.dry_run:
        print("")
        print("--dry-run: nothing written.")
        return

    for fn, (path, data, block) in plans.items():
        for c in CT_OLD:
            data.pop(c, None)
        data.update(block)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, separators=(",", ":"))
        print("  wrote %s (%d keys)" % (fn, len(data)))

    for row in master:
        terr = row.get("territory_geoids") or []
        if any(t in CT_OLD for t in terr):
            row["territory_geoids"] = sorted(
                [t for t in terr if t not in CT_OLD] + CT_NEW)
    with open(mpath, "w", encoding="utf-8") as fh:
        json.dump(master, fh, indent=1, ensure_ascii=False)
    print("  wrote edo_master_table_dual.json (%d EDO territories updated)"
          % len(touched))

    meta = {
        "migrated": date.today().isoformat(),
        "from": CT_OLD, "to": CT_NEW,
        "crosswalk": "ACS 2021 + 2023 county subdivisions joined on COUSUB",
        "files": sorted(plans) + ["edo_master_table_dual.json"],
        "approximation": ("lat/lon are population-weighted means of the contributing "
                          "county centroids; infra and MSA labels are taken from the "
                          "dominant contributing county"),
    }
    with open(os.path.join(HERE, "ct_migration_meta.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=1, ensure_ascii=False)
    print("")
    print("Done. NOW RERUN:  python build_edo_indexes.py")
    print("                  python build_county_power.py")


if __name__ == "__main__":
    main()
