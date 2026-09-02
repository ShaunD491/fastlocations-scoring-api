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
import csv, io, json, os, re, sys, unicodedata, zipfile

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


REF_YEAR = [None]
PROV_CSI = {}          # 2-digit province code -> current CSI


def read_table_35100026(text):
    """StatCan table 35-10-0026, the durable source.

    Long format: one row per (REF_DATE, GEO, Statistics). GEO carries the
    geographic code in brackets - "Halifax, Nova Scotia [12205]" - and the CMA
    name is the part before the first comma, which is what CMA_TO_CDUID keys on.
    Only the newest REF_DATE is taken.

    This table has no crime RATE series (it publishes severity indexes and
    clearance rates), so `rate` comes back None. The scorer only reads `csi`;
    rate is carried in the file for continuity and nothing scores on it.
    """
    # Ottawa-Gatineau is Canada's only interprovincial CMA, and the table lists
    # it three ways: an Ontario part, a Quebec part, and a combined row. The
    # model keys Ottawa and Gatineau as separate census divisions, so the two
    # parts are mapped individually and the combined row is dropped - counting
    # it as well would double the capital region.
    SPLIT = {"ottawagatineauontariopart": "Ottawa",
             "ottawagatineauquebecpart": "Gatineau"}
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows or "Statistics" not in rows[0]:
        return None
    years = sorted({r["REF_DATE"] for r in rows if r.get("REF_DATE")})
    if not years:
        return None
    latest = years[-1]
    REF_YEAR[0] = latest
    out = []
    for r in rows:
        if r.get("REF_DATE") != latest or r.get("Statistics") != "Crime severity index":
            continue
        raw = (r.get("GEO") or "").strip()
        geo = re.sub(r"\[[^\]]*\]", "", raw).strip().rstrip(",")
        try:
            csi = float(r.get("VALUE"))
        except (TypeError, ValueError):
            continue
        # A bare province carries a 2-digit code and no comma: "Alberta [48]".
        # Capture it BEFORE the comma test, which exists to drop Canada and the
        # provinces from the metro list - they are wanted here, as the source of
        # the non-metro fill.
        prov = re.search(r"\[(\d{2})\]$", raw)
        if prov and "," not in geo:
            PROV_CSI[prov.group(1)] = csi
            continue
        if "," not in geo:                      # Canada, and anything unlabelled
            continue
        alias = SPLIT.get(norm(geo))
        if alias:
            out.append((alias, csi, None))
            continue
        if norm(geo).startswith("ottawagatineau"):
            continue                            # the combined row; parts handled above
        out.append((geo.split(",")[0].strip(), csi, None))
    print(f"  table 35-10-0026, reference year {latest}: {len(out)} metro rows")
    return out


def read_daily_t004a(text):
    """The Daily's annual release table. Kept because it carries a crime RATE
    column the statistical table does not.

    WARNING: the Daily names its files POSITIONALLY - t004a is simply "table 4
    of that day's release". The 2026-07-20 release's t004a is Consumer Price
    Index data, not crime. Never bump the date in a Daily URL without opening
    the file; that is why the stable table is the default source.
    """
    out = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 4:
            continue
        name = row[0].strip()
        csi = num(row[1], float)
        if name and csi is not None:
            out.append((name, csi, num(row[3], int)))
    print(f"  Daily t004a format: {len(out)} rows")
    return out


def read_source(path):
    """Accept the stable table (zip or csv) or a legacy Daily csv, autodetected."""
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            member = next(n for n in z.namelist()
                          if n.lower().endswith(".csv") and "metadata" not in n.lower())
            text = z.read(member).decode("utf-8-sig", "replace")
    else:
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
            text = fh.read()
    rows = read_table_35100026(text)
    if rows is None:
        rows = read_daily_t004a(text)
    if not rows:
        sys.exit("no usable crime rows found - is this the right file?")
    return rows


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


NO_FILL = [False]


def main():
    args = [a for a in sys.argv[1:] if a != "--no-provincial-fill"]
    NO_FILL[0] = "--no-provincial-fill" in sys.argv
    src = args[0] if args else os.path.join(HERE, "35100026-eng.zip")
    if not os.path.exists(src):
        sys.exit(f"crime source not found: {src}")
    acc = {}   # cduid -> list of (csi, rate, cma)
    for name, csi, rate in read_source(src):
        cd = CMA_TO_CDUID.get(norm(name))
        if cd:
            acc.setdefault(cd, []).append((csi, rate, name))

    out = {}
    for cd, vals in acc.items():
        csi = round(sum(v[0] for v in vals) / len(vals), 1)          # average if >1 CMA in the CD
        rates = [v[1] for v in vals if v[1] is not None]
        rate = round(sum(rates) / len(rates)) if rates else None
        cma = " + ".join(v[2] for v in vals)
        out[cd] = {"csi": csi, "rate": rate, "cma": cma}

    # Non-metro census divisions: fill from the CURRENT provincial CSI.
    #
    # This is not a loss of detail. The values these replace, carried in
    # ca_regional.json since 2021, are already one number per province repeated
    # across every non-metro CD in it - 253 divisions sharing 13 values. So the
    # granularity is identical and only the vintage changes. Marked
    # basis="provincial" so a provincial stand-in is never mistaken for a
    # measured metro figure.
    filled = 0
    if not NO_FILL[0] and PROV_CSI:
        try:
            with open(os.path.join(HERE, "ca_features.json"), encoding="utf-8") as fh:
                cds = [k for k in json.load(fh) if not str(k).startswith("_")]
        except Exception:
            cds = []
        for cd in cds:
            if cd in out:
                continue
            csi = PROV_CSI.get(str(cd)[:2])
            if csi is not None:
                out[cd] = {"csi": round(csi, 1), "rate": None,
                           "cma": None, "basis": "provincial"}
                filled += 1

    # vintage marker: the CRIME YEAR, which is what the data is about. Without
    # it the refresh tool falls back to the file's Last-Modified, which is when
    # StatCan published, not what the numbers describe.
    if REF_YEAR[0]:
        out["_meta"] = {"source": "StatCan table 35-10-0026, reference year %s"
                                  % REF_YEAR[0],
                        "provincial_fill": filled}
    json.dump(out, open(OUT, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    matched = sum(len(v) for v in acc.values())
    print(f"Mapped {matched} CMAs -> {len(out)} census divisions. Wrote {OUT}")
    ranked = [k for k in out if not str(k).startswith("_")]
    for cd in sorted(ranked, key=lambda x: out[x]["csi"], reverse=True)[:5]:
        print(f"  highest CSI: {cd} {out[cd]['cma'] or '(provincial)'}  CSI {out[cd]['csi']}")
    if filled:
        print(f"  filled {filled} non-metro CD(s) from the current provincial CSI "
              f"(was one 2021 value per province in ca_regional.json)")


if __name__ == "__main__":
    main()
