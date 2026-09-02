#!/usr/bin/env python3
r"""
build_us_catchment.py
---------------------
Rebuilds us_catchment.json — the regional market catchment population — from
county centroids and populations already on disk. No network, no key.

WHAT IT IS FOR
    The market_size dimension asks "how big a market does this location reach",
    and a county's own population is a bad answer to that. New York County is
    1.6 million people and Los Angeles County is 9.7 million, which would rank
    Manhattan as a fraction of LA — an artefact of county lines, not of markets.
    Fragmented metros (New York, Boston, the Bay Area) are split across many
    small counties; consolidated ones (Los Angeles, Maricopa) are not.

    Catchment fixes that by summing the population reachable from each county,
    decayed with distance, so a county is credited with the market around it.
    Manhattan comes out at 12.2 million against LA's 10.7 million — which is the
    honest ordering, and the reverse of what raw county population says.

THE FORMULA, recovered from the file it replaces
    The original script was not kept, so the functional form was fitted against
    the stored values. Exponential decay is unambiguous:

        catchment(i) = SUM over j within R of  pop(j) * exp(-d(i,j) / lambda)

        R      = 110 km   (cutoff)
        lambda =  55 km   (decay constant, exactly R/2)

    The form is unambiguous. Median relative error against the stored file is
    0.72%, where the alternatives are far worse — flat sum 62%, linear taper
    9.3%, Gaussian 8.5%, inverse-square 4.6% — and the minimum is sharp: R=105
    or R=115 triple the error, lambda=50 or 60 make it ten times worse. A county
    includes ITSELF at full weight (d=0).

    On the residual, honestly: it is NOT population drift. Rebuilding against
    the pre-refresh populations gives the same 0.71%, so that theory is dead.
    What it is: at lambda=55 the median SIGNED error is -0.19%, essentially
    unbiased, and both the bias and the absolute error are minimised there
    (lambda=54 gives -2.2%, lambda=56 gives +1.7%). So the parameters are right
    and what remains is per-county scatter of about 1%, from centroids or
    populations that differed slightly when the stored file was built. It is
    reproduction to within a percent, not to the digit, and it should not be
    described as more than that.

INPUTS
    county_features.json — lat, lon and TOTPOP_CY. That is the whole dependency,
    which is why this rebuilds automatically whenever the feature table changes.
    Rerun it after ANY population refresh or geography change, or the catchment
    describes a country that no longer exists.

    US ONLY, deliberately, matching the file it replaces. Counties on the
    northern border are therefore credited with no Canadian market — Detroit
    does not see Windsor. That is a real understatement for a handful of border
    counties and would need Canadian census-division populations to fix.

USAGE
    python build_us_catchment.py
    python build_us_catchment.py --radius-km 110 --decay-km 55
    python build_us_catchment.py --compare      # fit quality against the current file
"""
import argparse, bisect, json, math, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "us_catchment.json")
FEAT = os.path.join(HERE, "county_features.json")

EARTH_KM = 12742.0          # 2 * mean radius, for the haversine below


def dist_km(la1, lo1, la2, lo2):
    p = math.pi / 180.0
    h = (0.5 - math.cos((la2 - la1) * p) / 2
         + math.cos(la1 * p) * math.cos(la2 * p) * (1 - math.cos((lo2 - lo1) * p)) / 2)
    return EARTH_KM * math.asin(min(1.0, math.sqrt(max(0.0, h))))


def main():
    ap = argparse.ArgumentParser(description="Rebuild us_catchment.json")
    ap.add_argument("--radius-km", type=float, default=110.0)
    ap.add_argument("--decay-km", type=float, default=55.0)
    ap.add_argument("--compare", action="store_true",
                    help="report agreement with the current file and write nothing")
    a = ap.parse_args()

    with open(FEAT, encoding="utf-8") as fh:
        feat = json.load(fh)

    pts, skipped = [], 0
    for fips, v in feat.items():
        la, lo, pop = v.get("lat"), v.get("lon"), v.get("TOTPOP_CY")
        if la is None or lo is None or pop is None:
            skipped += 1
            continue
        pts.append((float(la), float(lo), float(pop), fips))
    pts.sort()                                   # by latitude, for the prefilter
    lats = [p[0] for p in pts]
    print("Read %d counties with centroid and population%s"
          % (len(pts), " (%d skipped)" % skipped if skipped else ""))

    R, lam = a.radius_km, a.decay_km
    dlat = R / 111.0 + 0.02                      # degrees of latitude covering R
    out = {}
    for la, lo, _pop, fips in pts:
        i = bisect.bisect_left(lats, la - dlat)
        j = bisect.bisect_right(lats, la + dlat)
        total = 0.0
        for pla, plo, ppop, _ in pts[i:j]:
            d = dist_km(la, lo, pla, plo)
            if d <= R:
                total += ppop * math.exp(-d / lam)
        out[fips] = int(round(total))

    if a.compare or os.path.exists(OUT):
        try:
            with open(OUT, encoding="utf-8") as fh:
                old = json.load(fh)
        except Exception:
            old = {}
        errs = [abs(out[k] - old[k]) / old[k] for k in out
                if k in old and old[k]]
        if errs:
            errs.sort()
            print("  agreement with the current file: median %.2f%%, p90 %.2f%% "
                  "(%d counties compared)"
                  % (100 * errs[len(errs) // 2], 100 * errs[9 * len(errs) // 10],
                     len(errs)))
            gained = sorted(set(out) - set(old))
            lostk = sorted(set(old) - set(out))
            if gained:
                print("  gained %d county key(s): %s" % (len(gained), ", ".join(gained[:9])))
            if lostk:
                print("  no longer present: %d key(s): %s" % (len(lostk), ", ".join(lostk[:9])))

    if a.compare:
        print("--compare: nothing written.")
        return

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, separators=(",", ":"))

    vals = sorted(out.values())
    print("Wrote %s: %d counties (R=%g km, decay=%g km)"
          % (OUT, len(out), R, lam))
    print("  catchment  min %d  median %d  max %d"
          % (vals[0], vals[len(vals) // 2], vals[-1]))
    top = sorted(out.items(), key=lambda x: -x[1])[:5]
    for fips, v in top:
        print("    %s %-26s %12d" % (fips, (feat[fips].get("NAME") or "")[:26], v))


if __name__ == "__main__":
    main()
