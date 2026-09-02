#!/usr/bin/env python3
r"""
build_county_features.py
------------------------
Refreshes county_features.json — the largest single input to the US model —
from public sources, in blocks, carrying forward anything it cannot source.

READ THIS BEFORE RUNNING IT. This is not like the other builders.

WHAT MAKES THIS FILE DIFFERENT
    Every other build_*.py owns its output: it fetches a source and writes the
    whole file. This one cannot. county_features.json is a 41-field assembly and
    a large part of it is ESRI Business Analyst data — the `_CY` ("current year")
    fields — which is licensed, not public, and has no free equivalent. Two
    fields have no public equivalent at all:

        WLTHINDXCY   ESRI Wealth Index (indexed, 100 = US average)
        SEI_CY       ESRI Socioeconomic Status Index

    So this script UPDATES IN PLACE. It loads the existing file, refreshes the
    blocks it can source, leaves everything else exactly as it was, and reports
    which is which. It never drops a field, and it never invents one. If you
    delete county_features.json, this cannot recreate it.

THE BLOCKS

  chr     County Health Rankings, analytic_data<year>.csv. A clean swap: same
          measures, newer vintage. Verified by exact match against the stored
          file (premature_death 9938.2633823 to the digit).
              premature_death   <- v001_rawvalue
              poor_fair_health  <- v002_rawvalue
              poor_phys_days    <- v036_rawvalue
              poor_mental_days  <- v042_rawvalue
              life_expectancy   <- v147_rawvalue
              TOTPOP_CY         <- v051_rawvalue  (Census PEP based; matches the
                                   stored ESRI value to a median 1.2% across
                                   3,135 counties, so this is a like-for-like
                                   substitution rather than a methodology change)

  acs     Census ACS 5-year. This IS a methodology change and you should know it
          before you run it. The stored values are ESRI current-year PROJECTIONS;
          ACS is a five-year survey average. For Autauga County AL:

              field        stored (ESRI)   ACS 2023 5-yr
              TOTPOP_CY        60,428          59,285
              HSGRAD_CY        11,889          13,321     +12%
              SMCOLL_CY         7,193           8,719     +21%
              ASSCDEG_CY        4,032           3,234     -20%
              MEDHINC_CY       64,332          69,841
              CIVLBFR_CY       27,044          27,070     (0.1%)

          Labour force barely moves; the education SPLIT moves a lot, because
          ESRI apportions attainment differently than raw B15003. That matters:
          education_attainment carries a 2x metric weight. A uniform shift would
          be harmless — the scorer percentile-ranks within the candidate set —
          but a shift in composition is not uniform, so rankings will move. The
          script measures exactly how much and prints it.

  growth  POPGRW20CY, recomputed as annualised growth from the 2020 Decennial
          count to current population. (The stored 0.84 for Autauga is an annual
          rate, not a total: 60,428/58,805 over ~3.3 years compounds to ~0.84%.)

  Everything else is CARRIED FORWARD and listed in _meta.carried: WLTHINDXCY,
  SEI_CY, INCMORT_CY, POPDENS_CY, crime_rate, property_tax_rate,
  critical_thinking, infra, sdi, poverty_rate, hh_poverty, infant_mort_*, lat,
  lon and the name fields.

USAGE
    python build_county_features.py YOUR_CENSUS_API_KEY --dry-run
    python build_county_features.py YOUR_CENSUS_API_KEY
    python build_county_features.py YOUR_CENSUS_API_KEY --blocks chr
    python build_county_features.py --blocks chr --chr-csv analytic_data2025.csv

--dry-run reports every delta and writes nothing. Run it first.
--fill-only writes a block ONLY where a field is currently missing. Nothing
existing changes, so the acs block can close coverage gaps without imposing its
methodology on counties that already have a value. This is how Connecticut's
commute_min was filled after the 2022 geography migration.
"""
import argparse, csv, io, json, os, ssl, sys, urllib.parse, urllib.request, zipfile
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "county_features.json")
# Metadata goes in a SIDECAR, never inside the output. Unlike every other file
# here, the scorer loads county_features.json AS its county table and iterates
# it directly - `FEAT = _load("county_features.json")`. A "_meta" key inside it
# is not inert, it becomes a phantom 3,144th county.
META = os.path.join(HERE, "county_features_meta.json")

CHR_URL = ("https://www.countyhealthrankings.org/sites/default/files/media"
           "/document/analytic_data%d.csv")

# CHR short code -> field in county_features
CHR_MAP = {
    "v001_rawvalue": "premature_death",
    "v002_rawvalue": "poor_fair_health",
    "v036_rawvalue": "poor_phys_days",
    "v042_rawvalue": "poor_mental_days",
    "v147_rawvalue": "life_expectancy",
    "v051_rawvalue": "TOTPOP_CY",
}

ACS_VARS = [
    "B15003_001E", "B15003_017E", "B15003_018E", "B15003_019E", "B15003_020E",
    "B15003_021E", "B15003_022E", "B15003_023E", "B15003_024E", "B15003_025E",
    "B19013_001E", "B19025_001E", "B11001_001E",
    "B23025_003E", "B23025_004E", "B23025_005E",
    "B08013_001E", "B08012_001E",
]
DP_VARS = ["DP05_0021E", "DP05_0024E"]      # 18 and over, 65 and over


def ssl_context():
    for key in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        p = os.environ.get(key)
        if p and os.path.exists(p):
            try:
                return ssl.create_default_context(cafile=p)
            except Exception:
                pass
    return ssl.create_default_context()


def get(url, ctx, timeout=300):
    req = urllib.request.Request(url, headers={"User-Agent": "FastLocations/1.0"})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return r.read()


def num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f <= -999999 else f


# ---------------------------------------------------------------------------
# blocks
# ---------------------------------------------------------------------------
def block_chr(path_or_none, ctx):
    """County Health Rankings. Returns {fips: {field: value}} and the vintage."""
    if path_or_none:
        raw = open(path_or_none, "rb").read()
        year = "".join(c for c in os.path.basename(path_or_none) if c.isdigit())[:4]
    else:
        year, raw = None, None
        for y in range(date.today().year, date.today().year - 3, -1):
            try:
                raw = get(CHR_URL % y, ctx)
                year = str(y)
                print("  CHR %d: %.1f MB" % (y, len(raw) / 1e6))
                break
            except Exception:
                continue
        if raw is None:
            sys.exit("could not download County Health Rankings for any recent year")

    r = csv.reader(io.StringIO(raw.decode("utf-8-sig", "replace")))
    next(r)                                  # long labels
    codes = next(r)                          # short codes (v001_rawvalue, ...)
    idx = {c: i for i, c in enumerate(codes) if c in CHR_MAP}
    missing = set(CHR_MAP) - set(idx)
    if missing:
        print("  [!] CHR is missing expected columns, skipping them: %s"
              % ", ".join(sorted(missing)))
    out = {}
    for row in r:
        fips = row[2]
        if len(fips) != 5 or fips.endswith("000"):        # state / national rows
            continue
        vals = {}
        for code, i in idx.items():
            v = num(row[i])
            if v is not None:
                vals[CHR_MAP[code]] = v
        if vals:
            out[fips] = vals
    return out, ("County Health Rankings %s" % (year or "?"))


def census(path, varlist, key, ctx, year):
    url = ("https://api.census.gov/data/%d/acs/acs5%s?get=%s&for=county:*&key=%s"
           % (year, path, ",".join(varlist), key))
    raw = get(url, ctx).decode("utf-8", "replace")
    if not raw.lstrip().startswith("["):
        head = " ".join(raw.split())[:150]
        sys.exit("Census did not return JSON - the key is missing, unactivated or "
                 "rejected.\n  got: %s" % head)
    rows = json.loads(raw)
    hdr, ix = rows[0], {}
    for i, h in enumerate(hdr):
        ix[h] = i
    out = {}
    for row in rows[1:]:
        fips = row[ix["state"]].zfill(2) + row[ix["county"]].zfill(3)
        out[fips] = {v: num(row[ix[v]]) for v in varlist}
    return out


def block_acs(key, ctx, year):
    print("  ACS %d 5-year detail tables ..." % year)
    d = census("", ACS_VARS, key, ctx, year)
    print("  ACS %d data profile (age bands) ..." % year)
    p = census("/profile", DP_VARS, key, ctx, year)

    def s(row, *names):
        vals = [row.get(n) for n in names]
        return None if any(v is None for v in vals) else sum(vals)

    out = {}
    for fips, r in d.items():
        pr = p.get(fips, {})
        clf, emp, un = r.get("B23025_003E"), r.get("B23025_004E"), r.get("B23025_005E")
        agg, wk = r.get("B08013_001E"), r.get("B08012_001E")
        ai, hh = r.get("B19025_001E"), r.get("B11001_001E")
        a18, a65 = pr.get("DP05_0021E"), pr.get("DP05_0024E")
        vals = {
            "EDUCBASECY": r.get("B15003_001E"),
            "HSGRAD_CY": s(r, "B15003_017E", "B15003_018E"),
            "SMCOLL_CY": s(r, "B15003_019E", "B15003_020E"),
            "ASSCDEG_CY": r.get("B15003_021E"),
            "BACHDEG_CY": r.get("B15003_022E"),
            "GRADDEG_CY": s(r, "B15003_023E", "B15003_024E", "B15003_025E"),
            "MEDHINC_CY": r.get("B19013_001E"),
            "AVGHINC_CY": round(ai / hh) if (ai and hh) else None,
            "CIVLBFR_CY": clf,
            "EMP_CY": emp,
            "UNEMP_CY": un,
            "UNEMPRT_CY": round(100.0 * un / clf, 1) if (clf and un is not None) else None,
            "WORKAGE_CY": (a18 - a65) if (a18 is not None and a65 is not None) else None,
            "commute_min": round(agg / wk, 1) if (agg and wk) else None,
        }
        out[fips] = {k: v for k, v in vals.items() if v is not None}
    return out, "Census ACS %d 5-year" % year


def block_growth(key, ctx, pop_now):
    """Annualised growth from the 2020 Decennial count to current population."""
    print("  2020 Decennial counts ...")
    url = ("https://api.census.gov/data/2020/dec/pl?get=P1_001N&for=county:*&key=%s"
           % key)
    raw = get(url, ctx).decode("utf-8", "replace")
    if not raw.lstrip().startswith("["):
        print("  [!] 2020 Decennial call failed; skipping the growth block")
        return {}, ""
    rows = json.loads(raw)
    ix = {h: i for i, h in enumerate(rows[0])}
    years = (date.today() - date(2020, 4, 1)).days / 365.25
    out = {}
    for row in rows[1:]:
        fips = row[ix["state"]].zfill(2) + row[ix["county"]].zfill(3)
        base = num(row[ix["P1_001N"]])
        now = (pop_now.get(fips) or {}).get("TOTPOP_CY")
        if base and now and base > 0:
            out[fips] = {"POPGRW20CY": round(((now / base) ** (1.0 / years) - 1) * 100, 2)}
    return out, "Census 2020 Decennial + current population"


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------
def spearman(pairs):
    """Rank correlation, to answer 'did the ordering actually move'."""
    if len(pairs) < 3:
        return None
    def ranks(vals):
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        r = [0.0] * len(vals)
        for pos, i in enumerate(order):
            r[i] = float(pos)
        return r
    a = ranks([p[0] for p in pairs])
    b = ranks([p[1] for p in pairs])
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    num_ = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = (sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** 0.5
    return num_ / den if den else None


def report(base, updates, label):
    """Per-field impact of one block, measured only over counties the file
    actually has. Sources cover 3,222 counties (Puerto Rico, and Connecticut's
    2022 planning regions); the file has 3,143. Counting the surplus as "new"
    would advertise coverage this script never applies, since it only updates
    keys already present."""
    fields = sorted({f for v in updates.values() for f in v})
    print("")
    print("  %s" % label)
    print("  %-18s %6s %9s %9s %11s"
          % ("field", "n", "median %", ">10% moved", "median abs"))
    for f in fields:
        deltas, absd = [], []
        for fips, vals in updates.items():
            if f not in vals or fips not in base:
                continue
            old_v = (base.get(fips) or {}).get(f)
            if old_v is None:
                continue
            absd.append(abs(vals[f] - old_v))
            if old_v:
                deltas.append(abs(vals[f] - old_v) / abs(old_v) * 100.0)
        if not absd:
            continue
        deltas.sort(); absd.sort()
        med = deltas[len(deltas) // 2] if deltas else 0.0
        big = sum(1 for d in deltas if d > 10.0)
        # a percentage of a near-zero base is noise, so show the absolute move
        # too - POPGRW20CY shifting "62%" is 0.84 points becoming 1.36
        print("  %-18s %6d %8.1f%% %9d %11.4g"
              % (f, len(absd), med, big, absd[len(absd) // 2]))

    covered = {f for f in updates if f in base}
    gap = sorted(set(base) - covered)
    if gap:
        by_state = {}
        for f in gap:
            by_state[f[:2]] = by_state.get(f[:2], 0) + 1
        worst = sorted(by_state.items(), key=lambda x: -x[1])[:4]
        print("  NOT COVERED by this source: %d of %d counties%s"
              % (len(gap), len(base),
                 " - " + ", ".join("%s x%d" % (st, n) for st, n in worst)))


def main():
    ap = argparse.ArgumentParser(description="Refresh county_features.json in place")
    ap.add_argument("key", nargs="?", help="Census API key (needed for acs/growth)")
    ap.add_argument("--blocks", default="chr,acs,growth")
    ap.add_argument("--year", type=int, default=2023, help="ACS 5-year vintage")
    ap.add_argument("--chr-csv", help="local County Health Rankings csv")
    ap.add_argument("--dry-run", action="store_true",
                    help="report every delta and write nothing")
    ap.add_argument("--fill-only", action="store_true",
                    help="write a block's values ONLY where the field is currently "
                         "missing. Purely additive: no existing value changes, so "
                         "the acs block can close coverage gaps without imposing "
                         "its methodology on counties that already have a value")
    a = ap.parse_args()
    blocks = [b.strip() for b in a.blocks.split(",") if b.strip()]

    if not os.path.exists(OUT):
        sys.exit("%s not found. This script UPDATES that file in place and cannot "
                 "create it from scratch - a large part of it is licensed ESRI data "
                 "with no public equivalent. Restore it from a backup." % OUT)
    with open(OUT, encoding="utf-8") as fh:
        base = json.load(fh)
    base.pop("_meta", None)
    print("Loaded %d counties from the existing file" % len(base))

    if ("acs" in blocks or "growth" in blocks) and not a.key:
        sys.exit("the acs and growth blocks need a Census API key\n"
                 "  free key: https://api.census.gov/data/key_signup.html\n"
                 "  or run with --blocks chr")

    ctx = ssl_context()
    applied, sources = {}, []

    if "chr" in blocks:
        print("\nBlock: County Health Rankings")
        upd, src = block_chr(a.chr_csv, ctx)
        report(base, upd, src)
        applied["chr"] = upd
        sources.append(src)

    if "acs" in blocks:
        print("\nBlock: Census ACS")
        upd, src = block_acs(a.key.strip(), ctx, a.year)
        report(base, upd, src)
        applied["acs"] = upd
        sources.append(src)

    if "growth" in blocks:
        print("\nBlock: population growth")
        pop = applied.get("chr") or {f: {"TOTPOP_CY": v.get("TOTPOP_CY")}
                                     for f, v in base.items()}
        upd, src = block_growth(a.key.strip(), ctx, pop)
        if upd:
            report(base, upd, src)
            applied["growth"] = upd
            sources.append(src)

    # ---- does the ordering actually move? ---------------------------------
    merged = {}
    for upd in applied.values():
        for f, vals in upd.items():
            merged.setdefault(f, {}).update(vals)

    def bach_share(v):
        b = (v.get("BACHDEG_CY") or 0) + (v.get("GRADDEG_CY") or 0)
        e = v.get("EDUCBASECY")
        return (b / e) if e else None

    pairs = []
    for fips, old in base.items():
        new = dict(old)
        new.update(merged.get(fips, {}))
        a_, b_ = bach_share(old), bach_share(new)
        if a_ is not None and b_ is not None:
            pairs.append((a_, b_))
    rho = spearman(pairs)
    if rho is not None:
        print("\n  RANKING IMPACT — bachelor's-or-higher share, %d counties" % len(pairs))
        print("    Spearman rank correlation old vs new: %.4f" % rho)
        print("    %s" % ("ordering essentially preserved" if rho > 0.99 else
                          "ordering shifts materially - review before relying on it"
                          if rho < 0.97 else "ordering mostly preserved"))

    refreshed = sorted(merged and {f for v in merged.values() for f in v} or [])
    all_fields = sorted({f for v in base.values() for f in v})
    carried = [f for f in all_fields if f not in refreshed]

    if a.dry_run:
        print("\n--dry-run: nothing written.")
        print("  would refresh (%d): %s" % (len(refreshed), ", ".join(refreshed)))
        print("  would carry   (%d): %s" % (len(carried), ", ".join(carried)))
        return

    changed, filled = 0, 0
    for fips, vals in merged.items():
        if fips not in base:
            continue
        if a.fill_only:
            gaps = {f: v for f, v in vals.items() if base[fips].get(f) is None}
            if gaps:
                base[fips].update(gaps)
                filled += len(gaps)
                changed += 1
        else:
            base[fips].update(vals)
            changed += 1
    meta = {
        "built": date.today().isoformat(),
        "mode": "fill-only (gaps closed, nothing overwritten)" if a.fill_only
                else "full block refresh",
        "sources": sources,
        "refreshed": refreshed,
        "carried": carried,
        "carried_note": ("not refreshable from public data - WLTHINDXCY and SEI_CY "
                         "are proprietary ESRI indexes with no public equivalent; "
                         "the rest come from static repo extracts"),
        "counties": len(base),
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(base, fh, separators=(",", ":"))
    with open(META, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=1, ensure_ascii=False)
    print("")
    print("Wrote %s: %d counties, %d updated%s"
          % (OUT, len(base), changed,
             " (fill-only: %d empty values filled, none overwritten)" % filled
             if a.fill_only else ""))
    print("  provenance -> %s" % os.path.basename(META))
    print("  refreshed %d field(s): %s" % (len(refreshed), ", ".join(refreshed)))
    print("  carried   %d field(s) unchanged" % len(carried))


if __name__ == "__main__":
    main()
