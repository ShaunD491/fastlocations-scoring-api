#!/usr/bin/env python3
r"""
build_ca_labour.py
------------------
Replaces the Canadian labour-market inputs with CURRENT Labour Force Survey data.

WHY: the model's Canadian unemployment and participation rates came from the 2021 Census — collected
during COVID restrictions, when the national unemployment rate was roughly 9.6% against a US
current-year estimate of about 4.0%. Even with per-country ranking, a five-year-old COVID-era
snapshot misrepresents which Canadian labour markets are actually tight today.

SOURCE: Statistics Canada table 14-10-0459-01, "Labour force characteristics by census metropolitan
area, three-month moving average, seasonally adjusted" (monthly, Labour Force Survey).
    https://www150.statcan.gc.ca/n1/tbl/csv/14100459-eng.zip

Coverage strategy — ratio benchmarking, NOT wholesale replacement. The LFS publishes census
metropolitan areas and provinces, not census divisions, so a straight substitution would give all 164
non-metro Ontario CDs one identical unemployment rate and destroy the within-province variation that
actually distinguishes candidate sites. Instead each CD keeps its 2021 Census *relative position* and
is rescaled to the current level of the smallest LFS geography containing it:

    current_cd = census_cd x (current_group / census_group)

where census_group is the labour-force-weighted mean of the Census rates across the CDs in that
group, and group = the CD's own CMA where the LFS reports one, otherwise its province. This is
standard structure-preserving small-area benchmarking: the level comes from current data, the
dispersion from the only source published at CD resolution.

USAGE:
    python build_ca_labour.py "14100459.csv"     # or pass the -eng.zip directly
Writes ca_labour.json: { "<cduid>": {"unemployment":r,"participation":r,"employment_rate":r,
                                     "ref":"YYYY-MM","basis":"cma"|"province"} }
"""
import collections, csv, io, json, os, re, sys, unicodedata, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "ca_labour.json")
WANT = {"Unemployment rate": "unemployment", "Participation rate": "participation",
        "Employment rate": "employment_rate"}
_PROV_NAMES = {"newfoundland and labrador": "NL", "prince edward island": "PE", "nova scotia": "NS",
               "new brunswick": "NB", "quebec": "QC", "ontario": "ON", "manitoba": "MB",
               "saskatchewan": "SK", "alberta": "AB", "british columbia": "BC"}


def norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", s)


# keys must be normalised the same way the GEO column is, or multi-word provinces never match
PROV = {norm(k): v for k, v in _PROV_NAMES.items()}


def open_csv(src):
    if src.lower().endswith(".zip"):
        z = zipfile.ZipFile(src)
        name = next(n for n in z.namelist() if n.lower().endswith(".csv") and "metadata" not in n.lower())
        return io.TextIOWrapper(z.open(name), encoding="utf-8-sig", errors="replace")
    return open(src, encoding="utf-8-sig", errors="replace")


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "14100459.csv")
    if not os.path.exists(src):
        sys.exit(f"LFS file not found: {src}\nDownload 14-10-0459 from "
                 f"https://www150.statcan.gc.ca/n1/tbl/csv/14100459-eng.zip")
    ca = json.load(open(os.path.join(HERE, "ca_features.json"), encoding="utf-8"))
    try:
        cma = json.load(open(os.path.join(HERE, "ca_cma.json"), encoding="utf-8"))
    except FileNotFoundError:
        cma = {}
    # CMA display name -> the census divisions that carry it (built by build_ca_cma.py)
    by_cma = {}
    for cduid, name in cma.items():
        by_cma.setdefault(norm(name.split("/")[0]), []).append(cduid)

    latest, rows = None, []
    with open_csv(src) as f:
        for row in csv.DictReader(f):
            if row.get("Statistics") != "Estimate":
                continue
            char = WANT.get(row.get("Labour force characteristics"))
            if not char:
                continue
            v = (row.get("VALUE") or "").strip()
            if not v:
                continue
            try:
                v = float(v)
            except ValueError:
                continue
            d = row.get("REF_DATE") or ""
            latest = d if latest is None or d > latest else latest
            rows.append((d, row.get("GEO") or "", char, v))

    prov_vals, cma_vals = {}, {}
    for d, geo, char, v in rows:
        if d != latest:
            continue
        g = geo.split(",")[0].strip()
        key = norm(g)
        if key in PROV:
            prov_vals.setdefault(PROV[key], {})[char] = v
        else:
            cma_vals.setdefault(key, {})[char] = v

    # --- assign every CD to the smallest LFS geography that contains it -------------------------
    socio = json.load(open(os.path.join(HERE, "ca_socio.json"), encoding="utf-8"))
    cma_of = {}
    for key, cduids in by_cma.items():
        if key in cma_vals:
            for c in cduids:
                cma_of[c] = key

    group_of, targets = {}, {}
    for cduid, feat in ca.items():
        if cduid in cma_of:
            g = ("cma", cma_of[cduid]); targets[g] = cma_vals[cma_of[cduid]]
        else:
            pv = feat.get("ST_ABBREV")
            if pv not in prov_vals:
                continue
            g = ("province", pv); targets[g] = prov_vals[pv]
        group_of[cduid] = g

    # --- census baseline for each group, weighted by labour force ------------------------------
    base = {}
    for cduid, g in group_of.items():
        s = socio.get(cduid) or {}
        w = s.get("labour_force") or 0
        if w <= 0:
            continue
        acc = base.setdefault(g, {})
        for char in WANT.values():
            v = s.get(char)
            if v is not None:
                a = acc.setdefault(char, [0.0, 0.0])
                a[0] += v * w; a[1] += w

    out = collections.Counter()
    res, capped = {}, 0
    for cduid, g in group_of.items():
        s = socio.get(cduid) or {}
        rec, e = targets[g], {}
        for char in WANT.values():
            cur = rec.get(char)
            if cur is None:
                continue
            cd_v, acc = s.get(char), (base.get(g) or {}).get(char)
            if cd_v is None or not acc or acc[1] <= 0 or acc[0] <= 0:
                e[char] = round(cur, 1)          # no census detail to preserve: take the group level
                continue
            ratio = cur / (acc[0] / acc[1])
            v = cd_v * ratio
            # Guard against a thin-sample CD with an extreme census rate being amplified.
            lo, hi = (cur * 0.35, cur * 2.5) if char == "unemployment" else (cur * 0.75, cur * 1.25)
            if not (lo <= v <= hi):
                v = min(max(v, lo), hi); capped += 1
            e[char] = round(v, 1)
        if not e:
            continue
        e["ref"] = latest; e["basis"] = g[0]; e["benchmark"] = g[1]
        res[cduid] = e
        out[g[0]] += 1

    json.dump(res, open(OUT, "w", encoding="utf-8"), separators=(",", ":"))
    print(f"Wrote {OUT}: {len(res)} census divisions  (reference period {latest})")
    print(f"  benchmarked to their own CMA: {out['cma']}   to their province: {out['province']}")
    print(f"  {capped} values clamped at the guard rails")
    for cd, nm in (("3530", "Waterloo"), ("5915", "Greater Vancouver"), ("2466", "Montreal"),
                   ("3554", "Kenora (rural ON)"), ("3560", "Cochrane (rural ON)")):
        if cd in res:
            r = res[cd]; c = (socio.get(cd) or {}).get("unemployment")
            print(f"  {nm:<20} unemployment {c}% (2021) -> {r.get('unemployment')}%   "
                  f"participation {r.get('participation')}%  [{r['basis']}]")


if __name__ == "__main__":
    main()
