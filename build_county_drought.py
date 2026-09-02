#!/usr/bin/env python3
r"""
build_county_drought.py
-----------------------
Rebuilds county_drought.json — the US water-supply signal — from the U.S. Drought
Monitor's county statistics service.

SOURCE: USDM Data Services, county statistics by area percent.
    https://usdmdataservices.unl.edu/api/CountyStatistics/GetDroughtSeverityStatisticsByAreaPercent
    ?aoi=<STATE ABBREV>&startdate=m/d/yyyy&enddate=m/d/yyyy&statisticsType=1
Public, no key, weekly. `aoi` takes a STATE ABBREVIATION - a state FIPS or "us"
returns an empty list rather than an error, which is an easy hour to lose.

WHAT THE NUMBER IS
    The USDM `none` field: the percent of a county's area carrying NO drought
    designation at all (no D0-D4). d0..d4 are cumulative, so `none` = 100 - d0.
    This was verified against the file this script replaces: for map date
    2026-06-30, Barbour County AL came back 1.94 against a stored 1.9, and
    Bullock County 89.61 against a stored 89.6. So the existing semantics are
    reproduced exactly, and `--severity d1` is offered for the other reading.

    Note that D0 is "abnormally dry", which is formally a pre-drought watch
    rather than drought. Counting it, as the stored file does, is the cautious
    choice for a water-sensitive project. `--severity d1` scores on D1+ only.

WHY THE DEFAULT IS A FIVE-YEAR MEAN, NOT THIS WEEK'S MAP
    The file this replaces was a single week's snapshot, taken 2026-07-05 and
    never refreshed. A single USDM map is weather, not water security, and the
    scorer does not treat it gently: `not_in_drought >= 50` is a HARD EXCLUSION
    when a project sets drought="required", and 100 - value feeds the water
    penalty otherwise.

    Bullock County, Alabama, over three consecutive maps in mid-2026:

        2026-06-23   11.5      excluded
        2026-06-30   89.6      included
        2026-07-07  100.0      included

    Same county, same aquifer, three weeks apart, and the answer to "can I put
    a plant here" flips on which Thursday the file happened to be built. That
    is a build artefact, not a finding about Bullock County.

    So the default mode averages every weekly map over a trailing window
    (5 years, ~260 maps). That answers the question a site selector is actually
    asking - "how reliably wet is this county" - and it is stable enough that
    rebuilding it monthly barely moves a score. Use `--mode latest` to
    reproduce the old point-in-time behaviour.

CONNECTICUT
    Connecticut replaced its eight counties with nine planning regions in 2022
    and USDM reports on the new geography (09110-09190). county_features.json
    is still on the old county FIPS (09001-09015), so the scorer's lookup
    (`if fips in DROUGHT`) missed every Connecticut county and the whole state
    silently carried no drought data at all. This script emits BOTH: the new
    planning regions, and legacy county keys averaged from the regions that
    cover them (see CT_LEGACY). When county_features moves to the new FIPS the
    legacy keys simply stop being read - nothing needs changing here.

USAGE
    python build_county_drought.py                    # 5-year mean, D0+ (default)
    python build_county_drought.py --years 10
    python build_county_drought.py --mode latest      # single most recent map
    python build_county_drought.py --severity d1      # D1+ only, ignore "abnormally dry"

Writes county_drought.json: { "<fips5>": <percent of area not in drought>, ... }
plus a "_meta" key describing the run. The scorer looks its own FIPS up in this
dict, so the extra key is inert.
"""
import argparse, collections, json, os, ssl, sys, time, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "county_drought.json")

API = ("https://usdmdataservices.unl.edu/api/CountyStatistics"
       "/GetDroughtSeverityStatisticsByAreaPercent")

# 50 states + DC + Puerto Rico. USDM has no Virgin Islands county series.
AOIS = ("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI "
        "MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA PR RI SC SD TN TX UT "
        "VT VA WA WV WI WY").split()

# Legacy Connecticut county FIPS -> the 2022 planning regions covering them.
# Approximate by design: the nine regions do not nest inside the eight counties,
# so each legacy county takes the unweighted mean of the regions that overlap
# it. Connecticut is small and climatically uniform enough that this is a fair
# stand-in; it is not an areal-weighted crosswalk and does not pretend to be.
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


def ssl_context():
    """Honour a CA bundle from the environment.

    This machine runs TLS interception (Norton), whose root is not in the
    default trust store, so an unconfigured HTTPS call fails outright. The
    refresh tool exports SSL_CERT_FILE when it launches a builder; run standalone
    this falls back to the system default.
    """
    for key in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        path = os.environ.get(key)
        if path and os.path.exists(path):
            try:
                return ssl.create_default_context(cafile=path)
            except Exception:
                pass
    return ssl.create_default_context()


def fetch(aoi, start, end, ctx, tries=4):
    """One state, one date window. Returns the raw row list."""
    url = ("%s?aoi=%s&startdate=%s&enddate=%s&statisticsType=1"
           % (API, aoi, start.strftime("%m/%d/%Y"), end.strftime("%m/%d/%Y")))
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "FastLocations-data-refresh/1.0",
    })
    last = None
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=180, context=ctx) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            last = e
            time.sleep(2.0 * (attempt + 1))
    raise IOError("USDM fetch failed for %s %s..%s: %s"
                  % (aoi, start, end, last))


def windows(start, end, step_days=366):
    """Year-sized chunks. A single 5-year request for Texas is ~16 MB and times
    out often enough to be annoying; a year of Texas is ~3 MB and reliable."""
    cur = start
    while cur < end:
        nxt = min(end, cur + timedelta(days=step_days))
        yield cur, nxt
        cur = nxt + timedelta(days=1)


def value_of(row, severity):
    """Percent of the county NOT in drought, on the chosen threshold."""
    if severity == "d1":
        return 100.0 - float(row.get("d1") or 0.0)   # D0 "abnormally dry" not counted
    v = row.get("none")
    if v is None:                                    # none == 100 - d0 (cumulative)
        return 100.0 - float(row.get("d0") or 0.0)
    return float(v)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[2])
    ap.add_argument("--mode", choices=["mean", "latest"], default="mean")
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--severity", choices=["d0", "d1"], default="d0",
                    help="d0 = count 'abnormally dry' as drought (default, matches "
                         "the previous file); d1 = drought proper only")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()

    end = date.today()
    start = (end - timedelta(days=int(365.25 * a.years)) if a.mode == "mean"
             else end - timedelta(days=28))   # a month back is enough to catch one map
    ctx = ssl_context()

    print("USDM county statistics: %s .. %s  (%s, severity %s)"
          % (start, end, a.mode, a.severity))

    jobs = [(aoi, w0, w1) for aoi in AOIS for w0, w1 in windows(start, end)]
    rows_by_state = collections.defaultdict(list)
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, a.workers)) as ex:
        futs = {ex.submit(fetch, aoi, w0, w1, ctx): aoi for aoi, w0, w1 in jobs}
        for fut in as_completed(futs):
            aoi = futs[fut]
            done += 1
            try:
                rows = fut.result()
            except Exception as e:
                print("  [!] %s: %s" % (aoi, str(e)[:120]))
                continue
            rows_by_state[aoi].extend(rows)
            if done % 25 == 0 or done == len(jobs):
                print("  fetched %d/%d windows" % (done, len(jobs)))

    all_rows = [r for rows in rows_by_state.values() for r in rows]
    if not all_rows:
        sys.exit("no rows returned from USDM - check connectivity and the API shape")

    latest_map = max(r["mapDate"] for r in all_rows)
    # Only counties present on the most recent map are current geography.
    # Without this, Connecticut's retired county FIPS would linger with a mean
    # over whichever weeks preceded the 2022 change.
    current = {r["fips"] for r in all_rows if r["mapDate"] == latest_map}

    acc = collections.defaultdict(list)
    for r in all_rows:
        f = r.get("fips")
        if not f or f not in current:
            continue
        if a.mode == "latest" and r["mapDate"] != latest_map:
            continue
        acc[f].append(value_of(r, a.severity))

    out = {f: round(sum(v) / len(v), 2) for f, v in acc.items() if v}

    # Bridge the Connecticut geography change, but only while it still exists.
    # Once county_features.json moves to the 2022 planning regions (see
    # migrate_ct_geography.py) the scorer no longer looks up 09001-09015, and
    # writing them here would just leave stale keys behind. So the bridge is
    # conditional on the key space, and retires itself.
    ct_added = 0
    legacy_live = False
    try:
        with open(os.path.join(HERE, "county_features.json"), encoding="utf-8") as fh:
            legacy_live = any(k in json.load(fh) for k in CT_LEGACY)
    except Exception:
        legacy_live = True                      # cannot tell - keep the bridge
    if legacy_live:
        for legacy, regions in CT_LEGACY.items():
            vals = [out[r] for r in regions if r in out]
            if vals and legacy not in out:
                out[legacy] = round(sum(vals) / len(vals), 2)
                ct_added += 1

    weeks = len({r["mapDate"] for r in all_rows})
    out["_meta"] = {
        "built": date.today().isoformat(),
        "source": "US Drought Monitor county statistics (usdmdataservices.unl.edu)",
        "mode": a.mode,
        "severity": a.severity,
        "measure": ("percent of county area with no D0-D4 designation"
                    if a.severity == "d0"
                    else "percent of county area not in D1-D4 drought"),
        "window": [start.isoformat(), end.isoformat()],
        "maps_used": weeks if a.mode == "mean" else 1,
        "latest_map": latest_map[:10],
        "counties": len(out) - 1,
        "ct_legacy_keys": ct_added,
    }

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, separators=(",", ":"))

    vals = [v for k, v in out.items() if not k.startswith("_")]
    vals.sort()
    print("Wrote %s: %d counties from %d weekly maps (latest %s)"
          % (OUT, len(vals), out["_meta"]["maps_used"], latest_map[:10]))
    print("  not-in-drought  min %.1f  median %.1f  mean %.1f  max %.1f"
          % (vals[0], vals[len(vals) // 2], sum(vals) / len(vals), vals[-1]))
    print("  below the scorer's 50%% exclusion threshold: %d counties"
          % sum(1 for v in vals if v < 50))
    if ct_added:
        print("  bridged %d legacy Connecticut county FIPS from planning regions"
              % ct_added)
    elif not legacy_live:
        print("  Connecticut bridge not needed - the model is on 2022 planning regions")


if __name__ == "__main__":
    main()
