#!/usr/bin/env python3
r"""
build_county_workforce.py
-------------------------
Rebuilds county_workforce.json — the workforce availability signal — from the
Census ACS 5-year employment-status tables.

WHAT THE SCORER DOES WITH IT (scorer.m_workforce)
    recruitable / civ_lf    "employee availability": how much headroom there is
                            to hire without poaching the existing workforce
    recruitable             one of three capped sufficiency ratios in
                            "staffability" (available pool ~30x the start-up
                            headcount = ample)
    civ_lf                  another of those three
    mean_hours              "shift fit", and only when the project runs two,
                            three or continuous shifts - otherwise it drops out
    pct_nilf                carried for reference; nothing scores on it today

SOURCE: Census ACS 5-year detail tables, county level.
    B23025  Employment status for the population 16 years and over
              _001 total 16+   _003 civilian labor force
              _005 civilian unemployed   _007 not in labor force
    B23020  Mean usual hours worked in the past 12 months, workers 16 to 64
              _001 total
    https://api.census.gov/data/<year>/acs/acs5

    A key is REQUIRED. Unkeyed requests now return an HTML "Missing Key" page
    with HTTP 200, which parses as neither JSON nor an error unless you look.
    Free key: https://api.census.gov/data/key_signup.html

THE DERIVED FIELDS, and how they were recovered
    The script that first built this file was not kept, so the formulas were
    reverse-engineered from the file itself against County_1.csv and match to
    the unit across every county tested:

        civ_lf      = B23025_003
        unemp_n     = B23025_005
        pct_nilf    = 100 * B23025_007 / B23025_001        (1 dp)
        mean_hours  = B23020_001
        recruitable = B23025_005 + 0.25 * B23025_007

    That last one is the modelling assumption worth knowing about. "Recruitable"
    is everyone unemployed, plus a quarter of everyone not in the labour force
    at all - the latent pool a new plant might draw back in. The 0.25 is a
    judgement, not a measurement, and `--nilf-share` exposes it.

    KNOWN LIMITATION, measured rather than assumed: B23025 counts everyone 16
    and over, so "not in labor force" sweeps in retirees, students and the
    permanently disabled. The obvious worry is that retirement counties look
    recruitable when they are not. Checked against county_features across 3,135
    counties, that fear is mostly unfounded in aggregate - the correlation
    between non-working-age share and recruitable/civ_lf is 0.04, and the top
    and bottom deciles by retiree share have practically the same availability
    (0.249 vs 0.245).

    It does bite in the tail, exactly where you would guess. Sumter County FL
    (The Villages) is 60% non-working-age and scores 0.80 availability against a
    national median of 0.22. So this is an outlier problem, not a systematic
    bias, and it is not worth re-basing the whole measure over. If you ever do
    want it exact, take not-in-labour-force by age band from B23001 and keep only
    prime working age - that shifts scores, so it is deliberately NOT done here.

CONNECTICUT
    Connecticut replaced its counties with planning regions in 2022. The ACS -
    and County_1.csv before it - report the new geography (09110-09190), while
    county_features.json is still on the old county FIPS (09001-09015). The
    scorer looks its own FIPS up in this file, so the lookup missed every
    Connecticut county and the whole state has been carrying NO workforce data
    at all. Same root cause as the drought gap.

    These are counts, and the nine planning regions do not nest inside the eight
    counties, so they cannot simply be summed or averaged. Instead the bridge
    takes RATES from the regions and LEVELS from the county itself:

        civ_lf      county_features CIVLBFR_CY          (already county-level)
        unemp_n     civ_lf * county_features UNEMPRT_CY (already county-level)
        pct_nilf    mean of the overlapping regions
        mean_hours  mean of the overlapping regions
        recruitable unemp_n + share * NILF, with NILF implied from the county's
                    own labour force and the regional participation rate:
                    NILF ~= civ_lf * p / (1 - p),  p = pct_nilf / 100

    The NILF step is an approximation - it treats 16+ population as labour force
    plus not-in-labour-force, which ignores the armed forces and runs about 3.5%
    low on a test county. Bridged counties are named in _meta.ct_bridged so they
    are never mistaken for directly measured ones. The real fix is to rebuild
    county_features.json on 2022 geography; until then this beats a blank state.

USAGE
    python build_county_workforce.py YOUR_CENSUS_API_KEY
    python build_county_workforce.py YOUR_CENSUS_API_KEY --year 2023
    python build_county_workforce.py --csv County_1.csv     # rebuild from the
                                                            # original ESRI export
Writes county_workforce.json:
    { "<fips5>": {"mean_hours": <float>, "pct_nilf": <float>,
                  "recruitable": <int>, "civ_lf": <float>, "unemp_n": <float>}, ... }
"""
import argparse, csv, json, os, ssl, sys, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "county_workforce.json")

VARS = ["B23025_001E", "B23025_003E", "B23025_005E", "B23025_007E", "B23020_001E"]

# ACS uses large negative sentinels for suppressed / not-applicable estimates.
def num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f <= -999999 else f


def ssl_context():
    """Honour a CA bundle from the environment - this machine runs TLS
    interception whose root is not in the default store. The refresh tool
    exports SSL_CERT_FILE when it launches a builder."""
    for key in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        p = os.environ.get(key)
        if p and os.path.exists(p):
            try:
                return ssl.create_default_context(cafile=p)
            except Exception:
                pass
    return ssl.create_default_context()


def latest_acs_year(key, ctx, newest=None):
    """Newest published ACS 5-year vintage, probed downward.

    Pinning a year means the staleness check can flag the data as old while the
    builder keeps re-fetching the same old year - it rebuilds forever and never
    improves. Probing costs one or two requests and makes "stale" actionable.
    """
    import datetime as _dt
    top = newest or _dt.date.today().year
    for y in range(top, top - 5, -1):
        url = ("https://api.census.gov/data/%d/acs/acs5?get=NAME&for=state:01&key=%s"
               % (y, key))
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "FastLocations/1.0"})
            raw = urllib.request.urlopen(req, timeout=60, context=ctx).read().decode("utf-8")
            if raw.lstrip().startswith("["):
                return y
        except Exception:
            continue
    return None


def from_api(key, year):
    url = ("https://api.census.gov/data/%d/acs/acs5?get=%s&for=county:*&key=%s"
           % (year, ",".join(VARS), key))
    print("Fetching ACS %d 5-year employment status for all counties ..." % year)
    req = urllib.request.Request(url, headers={"User-Agent": "FastLocations/1.0"})
    with urllib.request.urlopen(req, timeout=180, context=ssl_context()) as r:
        raw = r.read().decode("utf-8", "replace")
    if not raw.lstrip().startswith("["):
        # the "Missing Key" page arrives as HTML with HTTP 200
        head = " ".join(raw.split())[:160]
        sys.exit("Census did not return JSON. This is almost always a missing or "
                 "rejected API key.\n  got: %s\n  free key: "
                 "https://api.census.gov/data/key_signup.html" % head)
    rows = json.loads(raw)
    hdr, data = rows[0], rows[1:]
    ix = {h: i for i, h in enumerate(hdr)}
    out = []
    for row in data:
        fips = row[ix["state"]].zfill(2) + row[ix["county"]].zfill(3)
        out.append((fips, {v: num(row[ix[v]]) for v in VARS}))
    return out


CSV_COLS = {
    "B23025_001E": "Total Population 16 Years and Over",
    "B23025_003E": "All Population in Civilian Labor Force",
    "B23025_005E": "Unemployed Population in Civilian Labor Force",
    "B23025_007E": "Population 16 Years and Over Not in Labor Force",
    "B23020_001E": ("Mean Usual Hours Worked of Population Age 16 to 64 who "
                    "Have Worked in Past 12 Months"),
}


def from_csv(path):
    """The original ESRI export of the same ACS tables, under long labels."""
    print("Reading %s ..." % os.path.basename(path))
    out = []
    with open(path, encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            fips = (r.get("Geographic Identifier - FIPS Code") or "").strip().zfill(5)
            if len(fips) != 5:
                continue
            out.append((fips, {v: num(r.get(c)) for v, c in CSV_COLS.items()}))
    return out


# Legacy Connecticut county FIPS -> the 2022 planning regions overlapping them.
# Approximate by design: the nine regions do not nest inside the eight counties.
CT_LEGACY = {
    "09001": ["09120", "09190"],            # Fairfield  <- Greater Bridgeport, Western CT
    "09003": ["09110"],                     # Hartford   <- Capitol
    "09005": ["09160", "09190"],            # Litchfield <- Northwest Hills, Western CT
    "09007": ["09130"],                     # Middlesex  <- Lower CT River Valley
    "09009": ["09170", "09140"],            # New Haven  <- South Central, Naugatuck Valley
    "09011": ["09180"],                     # New London <- Southeastern
    "09013": ["09110", "09150"],            # Tolland    <- Capitol, Northeastern
    "09015": ["09150"],                     # Windham    <- Northeastern
}


def bridge_connecticut(out, nilf_share):
    """Add legacy CT county keys, if the source used planning regions and the
    scorer's own feature table still has the counties. Returns what was added."""
    if not any(r in out for regions in CT_LEGACY.values() for r in regions):
        return []                                    # source is on old geography
    try:
        with open(os.path.join(HERE, "county_features.json"), encoding="utf-8") as fh:
            feat = json.load(fh)
    except Exception:
        print("  [!] county_features.json unreadable - cannot bridge Connecticut")
        return []

    added = []
    for legacy, regions in CT_LEGACY.items():
        if legacy in out:
            continue
        rs = [out[r] for r in regions if r in out]
        f = feat.get(legacy) or {}
        clf = f.get("CIVLBFR_CY")
        if not rs or not clf:
            continue
        pct = sum(r["pct_nilf"] for r in rs) / len(rs)
        hrs = [r["mean_hours"] for r in rs if r.get("mean_hours") is not None]
        unemp = clf * (f.get("UNEMPRT_CY") or 0.0) / 100.0
        p = min(max(pct / 100.0, 0.0), 0.95)
        nilf = clf * p / (1.0 - p) if p < 1.0 else 0.0
        out[legacy] = {
            "mean_hours": round(sum(hrs) / len(hrs), 1) if hrs else None,
            "pct_nilf": round(pct, 1),
            "recruitable": int(round(unemp + nilf_share * nilf)),
            "civ_lf": float(clf),
            "unemp_n": round(unemp, 1),
        }
        added.append(legacy)
    return added


def main():
    ap = argparse.ArgumentParser(description="Rebuild county_workforce.json")
    ap.add_argument("key", nargs="?", help="Census API key")
    ap.add_argument("--csv", help="build from the ESRI County_1.csv export instead")
    ap.add_argument("--year", default="latest",
                    help="ACS 5-year vintage, or 'latest' (default) to use the "
                         "newest published")
    ap.add_argument("--nilf-share", type=float, default=0.25,
                    help="share of the not-in-labor-force pool counted as "
                         "recruitable (default 0.25, matches the previous file)")
    a = ap.parse_args()

    if a.csv:
        src = from_csv(a.csv if os.path.isabs(a.csv) else os.path.join(HERE, a.csv))
        origin = "ESRI export %s" % os.path.basename(a.csv)
    else:
        if not a.key:
            sys.exit("usage: python build_county_workforce.py YOUR_CENSUS_API_KEY\n"
                     "   or: python build_county_workforce.py --csv County_1.csv\n"
                     "Free key: https://api.census.gov/data/key_signup.html")
        key = a.key.strip()
        if str(a.year).lower() == "latest":
            year = latest_acs_year(key, ssl_context())
            if not year:
                sys.exit("could not reach the Census API to find the newest ACS year")
            print("Newest published ACS 5-year vintage: %d" % year)
        else:
            year = int(a.year)
        src = from_api(key, year)
        origin = "Census ACS %d 5-year" % year

    out, skipped = {}, 0
    for fips, v in src:
        tot, clf = v["B23025_001E"], v["B23025_003E"]
        unemp, nilf = v["B23025_005E"], v["B23025_007E"]
        hours = v["B23020_001E"]
        if clf is None or unemp is None or nilf is None or not tot:
            skipped += 1
            continue
        out[fips] = {
            "mean_hours": round(hours, 1) if hours is not None else None,
            "pct_nilf": round(100.0 * nilf / tot, 1),
            "recruitable": int(round(unemp + a.nilf_share * nilf)),
            "civ_lf": clf,
            "unemp_n": unemp,
        }

    # Connecticut: rebuild legacy county keys from the planning regions the
    # source actually reports, so the state stops scoring blank (see docstring)
    bridged = bridge_connecticut(out, a.nilf_share)

    out["_meta"] = {
        "source": origin,
        "nilf_share": a.nilf_share,
        "recruitable": "unemployed + %.2f * not-in-labor-force (16+)" % a.nilf_share,
        "counties": len(out),
        "ct_bridged": sorted(bridged),
    }

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, separators=(",", ":"))

    n = len(out) - 1
    print("Wrote %s: %d counties from %s" % (OUT, n, origin))
    if skipped:
        print("  %d rows skipped for suppressed or missing estimates" % skipped)
    hrs = [v["mean_hours"] for k, v in out.items()
           if not k.startswith("_") and v.get("mean_hours") is not None]
    print("  mean_hours coverage %d/%d, range %.1f-%.1f"
          % (len(hrs), n, min(hrs), max(hrs)))
    avail = sorted(v["recruitable"] / v["civ_lf"] for k, v in out.items()
                   if not k.startswith("_") and v.get("civ_lf"))
    print("  recruitable/civ_lf  median %.3f  p10 %.3f  p90 %.3f"
          % (avail[len(avail) // 2], avail[len(avail) // 10], avail[9 * len(avail) // 10]))
    if bridged:
        print("  bridged %d legacy Connecticut county FIPS from planning regions "
              "(rates regional, levels county-level)" % len(bridged))


if __name__ == "__main__":
    main()
