#!/usr/bin/env python3
r"""
build_ca_cma.py
---------------
Builds cduid -> Census Metropolitan Area (CMA) name so Canadian matches display a metro label beside
the census-division name, the same way US counties show their MSA.

WHY: results showed "Waterloo, ON" with no metro context, while US rows show "(Atlanta-Sandy
Springs-Roswell, GA MSA)". A site selector thinks in metros, not census divisions.

SOURCES (both already in the repo, no download needed):
  1. ca_csi_2025.json -- built from StatCan's Crime Severity Index BY CMA, so it already carries the
     CMA name for each metro's ANCHOR census division (~40 CMAs).
  2. CMA_EXTRA below -- curated CDs that belong to a multi-CD CMA but are not the anchor (e.g. Peel,
     York, Durham and Halton are all in the Toronto CMA). Without these they'd show no metro at all.

USAGE:
    python build_ca_cma.py
Writes ca_cma.json: { "<cduid>": "<CMA name>" }. Non-metro CDs are simply absent (they get no label).
"""
import json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "ca_cma.json")

# Census divisions inside a CMA but not that CMA's anchor CD. cduid -> CMA name.
CMA_EXTRA = {
    # Toronto CMA
    "3519": "Toronto", "3521": "Toronto", "3518": "Toronto", "3524": "Toronto",
    # Montréal CMA
    "2465": "Montréal", "2458": "Montréal", "2467": "Montréal", "2464": "Montréal",
    "2473": "Montréal", "2460": "Montréal", "2474": "Montréal", "2472": "Montréal",
    "2459": "Montréal", "2457": "Montréal", "2471": "Montréal",
    # Ottawa-Gatineau is one CMA split across two provinces
    "3506": "Ottawa-Gatineau", "2481": "Ottawa-Gatineau",
    # Victoria CMA sits inside BC's Capital regional district
    "5917": "Victoria",
}

# Anchor rows in ca_csi_2025 that merge two CMAs into one CD get a cleaner display name.
RENAME = {"Chilliwack + Abbotsford–Mission": "Abbotsford–Mission / Chilliwack",
          "Ottawa": "Ottawa-Gatineau", "Gatineau": "Ottawa-Gatineau"}


def clean(name):
    """Strip StatCan footnote markers ('Ottawa{5}' -> 'Ottawa') and tidy whitespace."""
    return re.sub(r"\{[^}]*\}", "", str(name or "")).strip()


def main():
    csi_path = os.path.join(HERE, "ca_csi_2025.json")
    out = {}
    if os.path.exists(csi_path):
        for cduid, rec in json.load(open(csi_path, encoding="utf-8")).items():
            name = clean((rec or {}).get("cma"))
            if name:
                out[cduid] = RENAME.get(name, name)
    else:
        print("WARNING: ca_csi_2025.json not found - anchors will be missing", file=sys.stderr)

    added = 0
    for cduid, name in CMA_EXTRA.items():
        if cduid not in out:                 # never override an anchor's own CMA name
            out[cduid] = name; added += 1

    json.dump(out, open(OUT, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print(f"Wrote {OUT}: {len(out)} census divisions carry a CMA label "
          f"({len(out)-added} anchors + {added} additional CDs)")
    for cd in ("3530", "3521", "2465", "3506"):
        if cd in out:
            print(f"  {cd} -> {out[cd]}")


if __name__ == "__main__":
    main()
