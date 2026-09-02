#!/usr/bin/env python3
r"""
build_county_power.py
---------------------
Rebuilds county_power.json — generation capacity reachable from each county —
from the live EIA power-plant inventory.

WHAT THE SCORER DOES WITH IT (scorer)
    power_mw       infrastructure dimension: local generation capacity
    renew_share    the "Renewable / ESG power" preference, and the hard screen
                   when a project sets renewable="required"

WHY THIS ONE MATTERED
    The file it replaces was built from a static export whose US rows carry
    reference period 201705 — EIA-860, May 2017. The grid it describes is eight
    years gone: that snapshot has 1,887 US plants at 100 MW or more, against
    2,459 today, and essentially all of the growth is solar and wind. So
    `renew_share` — the number a project screens on when it asks for a greener
    grid — was answering a question about 2017.

SOURCE: the EIA power-plant layer published as an ArcGIS feature service.
    https://services2.arcgis.com/FiaPA4ga0iQKduv3/arcgis/rest/services
        /Power_Plants_in_the_US/FeatureServer/0
    Public, no key, paged 2000 at a time. Carries Latitude/Longitude, Total_MW
    and per-fuel MW columns, and a `Period` stamp (202502 at time of writing).

HOW THE NUMBER IS BUILT, and how that was recovered
    The original script was not kept, so the method was reverse-engineered from
    the file itself against the static export. For each county centroid
    (lat/lon from county_features.json), sum the capacity of every plant of
    100 MW or more whose coordinates fall within a radius:

        power_mw     sum of Total Capacity
        renew_mw     sum of Hydro + Pumped-Storage + Solar + Wind + Geothermal
                     + Biomass  (+ Tidal, which the US layer does not carry)
        renew_share  100 * renew_mw / power_mw, one decimal

    RADIUS: 95 km. Not 60 miles, which is what scorer.py's comment claims.
    Scanning radii against the stored file, 95.0 km reproduces it on 147 of 150
    sampled counties, against 68 of 80 for 60 miles — the original used a round
    metric number. `--radius-km` exposes it; the default preserves behaviour.

    PUMPED STORAGE counts as renewable here. That is arguable — it is storage,
    not generation — but it is what the stored file did (matching 2264 of 2266
    rows, against 2241 if excluded), so it is preserved. `--no-pumped-storage`
    turns it off.

    COUNTIES WITH NOTHING IN RANGE are OMITTED, not written as zero, which is
    again what the stored file did: 2,891 of 3,143 counties. This matters more
    than it looks. An absent key leaves the scorer's field null, and null
    weights renormalise instead of penalising; a zero would actively score the
    county at the bottom of the infrastructure dimension. `--emit-zero` changes
    it, and changes scores.

CANADA AND MEXICO — NOT USED, and that was already true
    The static export is titled "North American Power Plants" and carries 247
    Canadian and 132 Mexican plants alongside the US ones. The stored file
    ignored all of them. That is not a guess: US plants alone reproduce it on
    2,891 of 2,891 counties, while adding Canada drops the match to 98.4% and
    adding Mexico to 98.0% — the misses being exactly the border counties
    (Cochise AZ, Imperial CA, San Diego CA).

    So US-only is the default here, and it is also the right call going forward:
    the live EIA layer is US-only, and splicing a 2025 US inventory onto a 2017
    hand-assembled Canadian one would mix vintages inside a single number.
    `--include-foreign` adds them from the static CSV if you want the border
    counties covered, at the cost of that inconsistency. It changes scores.

USAGE
    python build_county_power.py
    python build_county_power.py --radius-km 95 --min-mw 100
    python build_county_power.py --include-foreign
Writes county_power.json:
    { "<fips5>": {"power_mw": <int>, "renew_mw": <int>, "renew_share": <float>}, ... }
"""
import argparse, csv, json, math, os, ssl, sys, time, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "county_power.json")
LEGACY_CSV = os.path.join(HERE, "North American Power Plants - 100 MW or more.csv")

SERVICE = ("https://services2.arcgis.com/FiaPA4ga0iQKduv3/arcgis/rest/services"
           "/Power_Plants_in_the_US/FeatureServer/0/query")

# per-fuel columns counted as renewable in the live layer
RENEW_COLS = ["Hydro_MW", "HydroPS_MW", "Solar_MW", "Wind_MW", "Geo_MW", "Bio_MW"]
OUT_FIELDS = ["Plant_Name", "State", "Total_MW", "Latitude", "Longitude",
              "Period"] + RENEW_COLS

# the same fuels in the static export's long column names
CSV_RENEW = ["Hydroelectric (MW)", "Pumped-Storage Hydroelectric (MW)", "Solar (MW)",
             "Wind (MW)", "Geothermal (MW)", "Biomass (MW)", "Tidal (MW)"]

MI_PER_KM = 0.621371


def ssl_context():
    """Honour a CA bundle from the environment - this machine runs TLS
    interception whose root is not in the default store."""
    for key in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        p = os.environ.get(key)
        if p and os.path.exists(p):
            try:
                return ssl.create_default_context(cafile=p)
            except Exception:
                pass
    return ssl.create_default_context()


def fnum(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def fetch_us(min_mw, pumped, ctx):
    """All US plants at or above the threshold, paged."""
    plants, offset, period = [], 0, None
    while True:
        q = {
            "where": "Total_MW>=%g" % min_mw,
            "outFields": ",".join(OUT_FIELDS),
            "returnGeometry": "false",
            "f": "json",
            "resultOffset": str(offset),
            "resultRecordCount": "2000",
        }
        url = SERVICE + "?" + urllib.parse.urlencode(q)
        req = urllib.request.Request(url, headers={"User-Agent": "FastLocations/1.0"})
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=120, context=ctx) as r:
                    data = json.loads(r.read().decode("utf-8", "replace"))
                break
            except Exception as e:
                if attempt == 3:
                    sys.exit("EIA plant service unreachable: %s" % e)
                time.sleep(2.0 * (attempt + 1))
        if data.get("error"):
            sys.exit("EIA plant service error: %s"
                     % (data["error"].get("message") or data["error"]))
        feats = data.get("features") or []
        for f in feats:
            a = f.get("attributes") or {}
            la, lo = a.get("Latitude"), a.get("Longitude")
            if la is None or lo is None:
                continue
            ren = sum(fnum(a.get(c)) for c in RENEW_COLS
                      if pumped or c != "HydroPS_MW")
            plants.append((float(la), float(lo), fnum(a.get("Total_MW")), ren))
            period = period or a.get("Period")
        print("  fetched %d plants" % len(plants))
        if not data.get("exceededTransferLimit") or not feats:
            break
        offset += len(feats)
    return plants, period


def load_legacy_foreign(min_mw, pumped):
    """Canadian and Mexican plants from the static export - no API covers them."""
    if not os.path.exists(LEGACY_CSV):
        print("  [!] %s not found - no Canadian or Mexican plants included"
              % os.path.basename(LEGACY_CSV))
        return []
    out = []
    with open(LEGACY_CSV, encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            if (r.get("Country") or "").strip() == "United States":
                continue
            try:
                la, lo = float(r["Latitude"]), float(r["Longitude"])
            except (TypeError, ValueError, KeyError):
                continue
            tot = fnum(r.get("Total Capacity (MW)"))
            if tot < min_mw:
                continue
            ren = sum(fnum(r.get(c)) for c in CSV_RENEW
                      if pumped or c != "Pumped-Storage Hydroelectric (MW)")
            out.append((la, lo, tot, ren))
    return out


def main():
    ap = argparse.ArgumentParser(description="Rebuild county_power.json")
    ap.add_argument("--radius-km", type=float, default=95.0,
                    help="capacity within this radius of the county centroid "
                         "(default 95, which reproduces the previous file)")
    ap.add_argument("--min-mw", type=float, default=100.0)
    ap.add_argument("--no-pumped-storage", action="store_true",
                    help="stop counting pumped-storage hydro as renewable")
    ap.add_argument("--include-foreign", action="store_true",
                    help="add the static export's 2017 Canadian and Mexican "
                         "plants, which the previous file did NOT use - covers "
                         "border counties but mixes data vintages")
    ap.add_argument("--emit-zero", action="store_true",
                    help="write 0 for counties with nothing in range instead of "
                         "omitting them (CHANGES SCORES - see the docstring)")
    a = ap.parse_args()
    pumped = not a.no_pumped_storage

    with open(os.path.join(HERE, "county_features.json"), encoding="utf-8") as fh:
        feat = json.load(fh)

    print("Fetching US plants >= %g MW from the EIA layer ..." % a.min_mw)
    plants, period = fetch_us(a.min_mw, pumped, ssl_context())
    n_us = len(plants)
    n_foreign = 0
    if a.include_foreign:
        foreign = load_legacy_foreign(a.min_mw, pumped)
        n_foreign = len(foreign)
        plants.extend(foreign)

    radius_mi = a.radius_km * MI_PER_KM
    dlat = radius_mi / 69.0 + 0.01          # cheap latitude prefilter

    # sort by latitude so the prefilter can bisect instead of scanning all plants
    plants.sort(key=lambda p: p[0])
    lats = [p[0] for p in plants]
    import bisect

    def dist_mi(la1, lo1, la2, lo2):
        p = math.pi / 180.0
        h = (0.5 - math.cos((la2 - la1) * p) / 2
             + math.cos(la1 * p) * math.cos(la2 * p) * (1 - math.cos((lo2 - lo1) * p)) / 2)
        return 7917.5 * math.asin(min(1.0, math.sqrt(max(0.0, h))))

    out, no_coords, empty = {}, 0, 0
    for fips, f in feat.items():
        la, lo = f.get("lat"), f.get("lon")
        if la is None or lo is None:
            no_coords += 1
            continue
        lo_i = bisect.bisect_left(lats, la - dlat)
        hi_i = bisect.bisect_right(lats, la + dlat)
        tot = ren = 0.0
        for pla, plo, pmw, prm in plants[lo_i:hi_i]:
            if dist_mi(la, lo, pla, plo) <= radius_mi:
                tot += pmw
                ren += prm
        if tot <= 0:
            empty += 1
            if not a.emit_zero:
                continue
        out[fips] = {
            "power_mw": int(round(tot)),
            "renew_mw": int(round(ren)),
            "renew_share": round(100.0 * ren / tot, 1) if tot > 0 else 0.0,
        }

    out["_meta"] = {
        "source": "EIA power plants feature service (Period %s)" % (period or "?"),
        "radius_km": a.radius_km,
        "min_mw": a.min_mw,
        "pumped_storage_counts_as_renewable": pumped,
        "us_plants": n_us,
        "foreign_plants_from_static_export": n_foreign,
        "counties": len(out),
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, separators=(",", ":"))

    n = len(out) - 1
    print("Wrote %s: %d counties within %g km of a plant >= %g MW"
          % (OUT, n, a.radius_km, a.min_mw))
    print("  %d US plants (EIA period %s)%s"
          % (n_us, period or "?",
             ", plus %d stale Canadian/Mexican from the static export" % n_foreign
             if n_foreign else ""))
    if no_coords:
        print("  %d counties skipped for missing centroid coordinates" % no_coords)
    print("  %d counties have nothing in range (%s)"
          % (empty, "written as zero" if a.emit_zero else "omitted, so the "
             "scorer treats them as no-data rather than worst-in-class"))
    shares = sorted(v["renew_share"] for k, v in out.items() if not k.startswith("_"))
    if shares:
        print("  renew_share  p10 %.1f  median %.1f  p90 %.1f"
              % (shares[len(shares) // 10], shares[len(shares) // 2],
                 shares[9 * len(shares) // 10]))


if __name__ == "__main__":
    main()
