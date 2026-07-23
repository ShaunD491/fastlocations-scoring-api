#!/usr/bin/env python3
r"""
build_ca_livability.py
----------------------
Builds a Canadian census-division livability signal from StatCan's Canadian Index of Multiple
Deprivation (CIMD), filling the near-total gap in the CA livability dimension (previously only ~9 CDs
had a value).

WHY: the CA livability dimension had almost no coverage. CIMD is an area-based deprivation index from
the 2021 Census at the Dissemination-Area (DA) level. We roll it up to census divisions (DAUID's
first 4 digits = CDUID) as a population-weighted mean, then invert it so HIGHER = MORE LIVABLE.

DIMENSIONS: CIMD has four - Residential instability, Ethno-cultural composition, Economic dependency,
Situational vulnerability. We use the THREE genuine-deprivation dimensions and DELIBERATELY EXCLUDE
Ethno-cultural composition, which StatCan documents as descriptive (immigrant/visible-minority
concentration), NOT a "more deprived = worse" measure.

SOURCE: CIMD 2021 dataset, "can_scores_quintiles_EN.csv" (StatCan 45-20-0001). Territories excluded
by StatCan. https://www150.statcan.gc.ca/n1/en/catalogue/452000012023001

USAGE:
    python build_ca_livability.py "can_scores_quintiles_EN.csv"
Writes ca_livability.json: { "<cduid>": {"livability": <float>, "das": <int>, "pop": <int>} }
livability = -(population-weighted mean of the 3 deprivation z-scores); higher = less deprived.
"""
import csv, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "ca_livability.json")
DIMS = ["Residential instability Scores", "Economic dependency Scores", "Situational vulnerability Scores"]


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "can_scores_quintiles_EN.csv")
    if not os.path.exists(src):
        sys.exit(f"CIMD CSV not found: {src}")
    with open(src, encoding="utf-8-sig") as f:
        rd = csv.DictReader(f)
        cols = rd.fieldnames
        da_col = next((c for c in cols if "Dissemination" in c), None)
        pop_col = next((c for c in cols if "Population" in c), None)
        if not da_col or not pop_col or any(d not in cols for d in DIMS):
            sys.exit(f"unexpected CIMD columns: {cols}")

        acc = {}   # cduid -> {"wsum":[..per dim..], "w":[..], "pop":int, "das":int}
        for row in rd:
            dauid = (row[da_col] or "").strip()
            if len(dauid) < 4 or not dauid[:4].isdigit():
                continue
            cd = dauid[:4]
            try:
                pop = float((row[pop_col] or "0").replace(",", ""))
            except ValueError:
                pop = 0.0
            a = acc.setdefault(cd, {"wsum": [0.0] * len(DIMS), "w": [0.0] * len(DIMS), "pop": 0, "das": 0})
            a["pop"] += int(pop); a["das"] += 1
            for i, d in enumerate(DIMS):
                v = (row[d] or "").strip()
                try:
                    s = float(v)
                except ValueError:
                    continue
                wt = pop if pop > 0 else 1.0
                a["wsum"][i] += s * wt; a["w"][i] += wt

    out = {}
    for cd, a in acc.items():
        dim_means = [a["wsum"][i] / a["w"][i] for i in range(len(DIMS)) if a["w"][i] > 0]
        if not dim_means:
            continue
        deprivation = sum(dim_means) / len(dim_means)
        out[cd] = {"livability": round(-deprivation, 4), "das": a["das"], "pop": a["pop"]}

    json.dump(out, open(OUT, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print(f"Wrote {OUT}: {len(out)} census divisions (from CIMD DAs)")
    ranked = sorted(out.items(), key=lambda kv: kv[1]["livability"], reverse=True)
    print("  most livable (least deprived):", [c for c, _ in ranked[:3]])
    print("  least livable (most deprived):", [c for c, _ in ranked[-3:]])
    for cd in ("3530", "3520", "5915"):
        if cd in out:
            print(f"  {cd}: livability {out[cd]['livability']}")


if __name__ == "__main__":
    main()
