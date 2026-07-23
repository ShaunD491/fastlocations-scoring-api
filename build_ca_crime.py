#!/usr/bin/env python3
r"""
build_ca_crime.py
-----------------
Refreshes the Canadian safety signal from StatCan's Police-reported Crime Severity Index (CSI) by
Census Metropolitan Area, mapping each CMA to the census division (CDUID) that anchors it and writing
ca_csi_2025.json for the scorer to overlay onto ca_regional's CSI.

WHY: the CA safety dimension uses a CSI per census division (older vintage / provincial fallback).
This drops in the authoritative latest CMA CSI (incl. Kitchener-Cambridge-Waterloo) for the ~41
metro CDs that matter, leaving non-metro CDs on their existing value.

SOURCE: StatCan Table "Police-reported Crime Severity Index and crime rate, by CMA" (t004a-eng.csv).
    Columns: CMA name, CSI (index), CSI %chg, crime rate, rate %chg.

USAGE:
    python build_ca_crime.py "t004a-eng.csv"
Writes ca_csi_2025.json: { "<cduid>": {"csi": <float>, "rate": <int|null>, "cma": "<name>"} }
Where two CMAs share one CD (e.g. Abbotsford + Chilliwack in Fraser Valley 5909) the CSI is averaged.
"""
import csv, json, os, re, sys, unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "ca_csi_2025.json")

# normalized CMA name -> anchor CDUID (the census division containing the CMA's core)
CMA_TO_CDUID = {
    "stjohns": "1001", "halifax": "1209", "moncton": "1307", "saintjohn": "1301", "fredericton": "1310",
    "saguenay": "2494", "quebec": "2423", "sherbrooke": "2443", "troisrivieres": "2437",
    "drummondville": "2449", "montreal": "2466", "gatineau": "2481", "ottawa": "3506", "kingston": "3510",
    "bellevillequintewest": "3512", "peterborough": "3515", "toronto": "3520", "hamilton": "3525",
    "stcatharinesniagara": "3526", "kitchenercambridgewaterloo": "3530", "brantford": "3529",
    "guelph": "3523", "london": "3539", "windsor": "3537", "barrie": "3543", "greatersudbury": "3553",
    "thunderbay": "3558", "winnipeg": "4611", "regina": "4706", "saskatoon": "4711", "lethbridge": "4802",
    "calgary": "4806", "reddeer": "4808", "edmonton": "4811", "kelowna": "5935", "kamloops": "5933",
    "chilliwack": "5909", "abbotsfordmission": "5909", "vancouver": "5915", "victoria": "5917",
    "nanaimo": "5921",
}


def norm(s):
    s = re.sub(r"\{[^}]*\}", "", str(s))               # strip StatCan footnote markers {4}
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", s)


def num(x, cast=float):
    x = (x or "").strip().replace(",", "")
    try:
        return cast(x)
    except ValueError:
        return None


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "t004a-eng.csv")
    if not os.path.exists(src):
        sys.exit(f"crime CSV not found: {src}")
    acc = {}   # cduid -> list of (csi, rate, cma)
    with open(src, encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            if len(row) < 4:
                continue
            name = row[0].strip()
            csi = num(row[1], float)
            if not name or csi is None:
                continue
            cd = CMA_TO_CDUID.get(norm(name))
            if not cd:
                continue
            acc.setdefault(cd, []).append((csi, num(row[3], int), name))

    out = {}
    for cd, vals in acc.items():
        csi = round(sum(v[0] for v in vals) / len(vals), 1)          # average if >1 CMA in the CD
        rates = [v[1] for v in vals if v[1] is not None]
        rate = round(sum(rates) / len(rates)) if rates else None
        cma = " + ".join(v[2] for v in vals)
        out[cd] = {"csi": csi, "rate": rate, "cma": cma}

    json.dump(out, open(OUT, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    matched = sum(len(v) for v in acc.values())
    print(f"Mapped {matched} CMAs -> {len(out)} census divisions. Wrote {OUT}")
    for cd in sorted(out, key=lambda x: out[x]["csi"], reverse=True)[:5]:
        print(f"  highest CSI: {cd} {out[cd]['cma']}  CSI {out[cd]['csi']}")


if __name__ == "__main__":
    main()
