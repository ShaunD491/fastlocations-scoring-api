#!/usr/bin/env python3
r"""
build_ca_features.py
--------------------
Refreshes the Canadian base tables — ca_features.json and ca_socio.json — in
place, from sources already on disk.

READ THIS FIRST, because it is a partial refresh by necessity.

WHAT IS AND IS NOT REFRESHABLE
    The Canadian side of the model rests on the 2021 Census. Most of it has
    since been overlaid by current sources — participation and unemployment from
    the Labour Force Survey, growth from the population estimates, livability
    from CIMD, crime from the Crime Severity Index, infrastructure from ODI. What
    was left running on raw 2021 Census values is five fields:

        TOTPOP_CY       refreshable  -> annual population estimates
        POPDENS_CY      refreshable  -> derived from the above
        labour_force    refreshable  -> rescaled with population (see below)
        income          NOT until the 2026 Census releases
        dwelling_value  NOT until the 2026 Census releases
        bachelor_share  NOT until the 2026 Census releases

    The last three were checked, not assumed. StatCan publishes annual income
    (tables 11-10-0005, 11-10-0012, 11-10-0135, 11-10-0190) but every one stops
    at the province or the CMA: their DGUIDs carry schema 0002 (province) and
    S0503 (CMA), never 0003 (census division). Median dwelling value and
    educational attainment by census division are Census products full stop.
    Nothing here can refresh them before the 2026 Census data lands in 2027-28.

WHY THIS MATTERED
    ca_features held the 2021 Census count. Canada's population has moved a long
    way since: the file totalled 36.99M against StatCan's ~41.5M for 2025, so
    the Canadian model was scoring on a country 4.7 million people short. It is
    not spread evenly either - Toronto CD was understated by 17%, Westmorland NB
    by 24%, and one division by 94%. TOTPOP_CY drives market_size and the
    staffability sufficiency ratios, so this was distorting rankings, not just
    labels.

SOURCE
    ca_popgrowth.json, already built by build_ca_popgrowth.py from StatCan table
    17-10-0152 (population estimates by census division). It carries a current
    `pop` per CD alongside the growth rate, so no new download is needed and the
    two files cannot disagree about the same population.

HOW THE DERIVED FIELDS MOVE
    POPDENS_CY   density is population over land area and the area does not
                 change, so it is scaled by the same ratio as population.
    labour_force the Census count scaled by population change. Participation and
                 unemployment RATES are already overlaid from the Labour Force
                 Survey (build_ca_labour.py); this keeps the LEVEL consistent
                 with them instead of leaving a 2021 count beside 2026 rates.
                 It is an estimate and is labelled as one.

PROVENANCE GOES IN A SIDECAR
    ca_features.json IS the scorer's Canadian feature table - `CA_FEAT =
    _load("ca_features.json")` - and is iterated directly. A "_meta" key inside
    it would become a phantom 294th census division, so provenance is written to
    ca_features_meta.json instead. Same reason as county_features.

USAGE
    python build_ca_features.py --dry-run
    python build_ca_features.py
"""
import argparse, json, os, sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
FEAT = os.path.join(HERE, "ca_features.json")
SOCIO = os.path.join(HERE, "ca_socio.json")
POPGROWTH = os.path.join(HERE, "ca_popgrowth.json")
META = os.path.join(HERE, "ca_features_meta.json")

CENSUS_ONLY = ["income", "dwelling_value", "bachelor_share"]


def load(path, label):
    if not os.path.exists(path):
        sys.exit("%s not found: %s" % (label, path))
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main():
    ap = argparse.ArgumentParser(description="Refresh the Canadian base tables")
    ap.add_argument("--dry-run", action="store_true",
                    help="report every change and write nothing")
    a = ap.parse_args()

    feat = load(FEAT, "ca_features.json")
    socio = load(SOCIO, "ca_socio.json")
    pg = load(POPGROWTH, "ca_popgrowth.json")
    feat.pop("_meta", None)
    socio.pop("_meta", None)

    ref_to = None
    changes, skipped = [], 0
    for cduid, row in feat.items():
        est = pg.get(cduid) or {}
        newpop = est.get("pop")
        oldpop = row.get("TOTPOP_CY")
        if not newpop or not oldpop:
            skipped += 1
            continue
        ref_to = ref_to or est.get("to")
        ratio = float(newpop) / float(oldpop)
        changes.append((cduid, row.get("NAME"), oldpop, newpop, ratio))

    if not changes:
        sys.exit("no census division matched between ca_features and ca_popgrowth")

    old_tot = sum(c[2] for c in changes)
    new_tot = sum(c[3] for c in changes)
    moves = sorted(changes, key=lambda c: c[4])
    print("Census divisions matched: %d of %d%s"
          % (len(changes), len(feat),
             "  (%d skipped for missing values)" % skipped if skipped else ""))
    print("  reference year: %s" % (ref_to or "?"))
    print("  national total: %.2fM -> %.2fM  (%+.1f%%)"
          % (old_tot / 1e6, new_tot / 1e6, (new_tot / old_tot - 1) * 100))
    print("  largest increases:")
    for c in moves[-3:]:
        print("    %-28s %+6.1f%%  %d -> %d"
              % ((c[1] or "")[:28], (c[4] - 1) * 100, c[2], c[3]))
    print("  largest decreases:")
    for c in moves[:2]:
        print("    %-28s %+6.1f%%  %d -> %d"
              % ((c[1] or "")[:28], (c[4] - 1) * 100, c[2], c[3]))

    lf_before = sum(v.get("labour_force") or 0 for v in socio.values())
    lf_after = sum((socio.get(c[0], {}).get("labour_force") or 0) * c[4]
                   for c in changes)
    print("  labour force (rescaled with population): %.2fM -> %.2fM"
          % (lf_before / 1e6, lf_after / 1e6))
    print("  NOT refreshed (Census-only until 2026 data lands): %s"
          % ", ".join(CENSUS_ONLY))

    if a.dry_run:
        print("")
        print("--dry-run: nothing written.")
        return

    for cduid, _name, _old, newpop, ratio in changes:
        row = feat[cduid]
        row["TOTPOP_CY"] = int(round(newpop))
        if row.get("POPDENS_CY"):
            # land area is unchanged, so density moves with population
            row["POPDENS_CY"] = round(row["POPDENS_CY"] * ratio, 4)
        s = socio.get(cduid)
        if s and s.get("labour_force"):
            s["labour_force"] = int(round(s["labour_force"] * ratio))

    with open(FEAT, "w", encoding="utf-8") as fh:
        json.dump(feat, fh, separators=(",", ":"), ensure_ascii=False)
    with open(SOCIO, "w", encoding="utf-8") as fh:
        json.dump(socio, fh, separators=(",", ":"), ensure_ascii=False)
    with open(META, "w", encoding="utf-8") as fh:
        json.dump({
            "built": date.today().isoformat(),
            "source": "StatCan population estimates by census division, "
                      "reference year %s (via ca_popgrowth.json)" % (ref_to or "?"),
            "refreshed": ["TOTPOP_CY", "POPDENS_CY", "labour_force"],
            "derived": {
                "POPDENS_CY": "scaled by the population ratio; land area constant",
                "labour_force": "2021 Census count scaled by the population ratio - "
                                "an estimate, kept consistent with the Labour Force "
                                "Survey rates overlaid by build_ca_labour.py",
            },
            "carried": CENSUS_ONLY,
            "carried_note": "no StatCan series publishes these below the province "
                            "or CMA; they are Census products and cannot move "
                            "until the 2026 Census releases",
            "census_divisions": len(feat),
        }, fh, indent=1, ensure_ascii=False)

    print("")
    print("Wrote ca_features.json and ca_socio.json (%d census divisions)" % len(feat))
    print("  provenance -> %s" % os.path.basename(META))


if __name__ == "__main__":
    main()
