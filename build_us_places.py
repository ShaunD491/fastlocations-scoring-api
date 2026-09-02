#!/usr/bin/env python3
r"""
build_us_places.py
------------------
Rebuilds us_places.json — the gazetteer behind "within X miles of Detroit, MI" —
from the Census Bureau's national Gazetteer files.

WHAT IT IS FOR
    The intake's market-proximity field takes a place name and a radius, and the
    scorer has to turn "Detroit, MI" into a coordinate before it can filter
    counties by distance. `scorer.PLACES` is that lookup, and `/places` in app.py
    serves the same table as the autocomplete. A name the gazetteer does not
    carry is silently unresolvable, so coverage here is directly a feature.

SOURCE: Census Gazetteer, national files, published annually.
    https://www2.census.gov/geo/docs/maps-data/data/gazetteer/<year>_Gazetteer/
        <year>_Gaz_place_national.zip      ~32,300 incorporated places and CDPs
        <year>_Gaz_cousubs_national.zip    ~36,400 county subdivisions
    Both are needed. Places alone miss the New England and Mid-Atlantic
    townships that people actually name - Pennsylvania contributes the most
    entries of any state precisely because so much of it is townships rather
    than incorporated places.

KEY FORMAT, which must match the scorer exactly
    "<USPS>|<normalised name>" -> [lat, lon, "Display Name"]

    The normalisation is scorer._norm: NFKD, strip to ASCII, lowercase, then
    drop everything that is not a letter or digit. It is reproduced here rather
    than imported so this script does not depend on the scorer loading (which
    reads ~40 files and takes seconds).

    The display name has its geographic-type suffix removed - "Abanda CDP"
    becomes "Abanda", "Autaugaville CCD" becomes "Autaugaville" - because that is
    what a person types and what the autocomplete should show. Coordinates are
    the Census internal points, rounded to 5 decimal places (about a metre,
    far finer than a county-radius filter needs).

COLLISIONS
    A name can exist as both a place and a township in the same state. Places win:
    they are what people mean by a city name. Within a file, the first row wins,
    which the Census orders by GEOID.

USAGE
    python build_us_places.py
    python build_us_places.py --year 2024
    python build_us_places.py --compare      # agreement with the current file
"""
import argparse, io, json, os, re, ssl, sys, unicodedata, urllib.request, zipfile
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "us_places.json")
META = os.path.join(HERE, "us_places_meta.json")

BASE = ("https://www2.census.gov/geo/docs/maps-data/data/gazetteer"
        "/%d_Gazetteer/%d_Gaz_%s_national.zip")

# Geographic-type words the Census appends to NAME. Stripped from the display
# name only; they are already absent from the normalised key.
SUFFIXES = re.compile(
    r"\s+(CDP|CCD|city|town|village|borough|township|municipality|"
    r"plantation|gore|grant|location|purchase|reservation|"
    r"\(balance\)|comunidad|zona urbana|UT)$", re.I)


def norm(x):
    """Must match scorer._norm exactly, or every lookup misses."""
    x = unicodedata.normalize("NFKD", str(x)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", x)


def clean_name(raw):
    """Strip the Census type suffix - ONCE.

    Stripping repeatedly eats real names: "Arctic Village CDP" loses CDP and
    then loses Village, leaving "Arctic"; "Mountain Village city" becomes
    "Mountain". The Census appends exactly one type word, so exactly one comes
    off. This cost 1,003 places before it was caught.
    """
    name = SUFFIXES.sub("", raw.strip()).strip()
    return name or raw.strip()


def ssl_context():
    for k in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        p = os.environ.get(k)
        if p and os.path.exists(p):
            try:
                return ssl.create_default_context(cafile=p)
            except Exception:
                pass
    return ssl.create_default_context()


def fetch_gazetteer(year, kind, ctx):
    url = BASE % (year, year, kind)
    req = urllib.request.Request(url, headers={"User-Agent": "FastLocations/1.0"})
    with urllib.request.urlopen(req, timeout=300, context=ctx) as r:
        blob = r.read()
    z = zipfile.ZipFile(io.BytesIO(blob))
    text = z.read(z.namelist()[0]).decode("utf-8-sig", "replace")
    lines = text.splitlines()
    # The Gazetteer changed delimiter between vintages: 2024 and earlier are
    # tab-separated, 2025 is pipe-separated and adds a GEOIDFQ column. Assuming
    # either one silently produces an empty file, so detect it from the header
    # and index columns by NAME rather than by position.
    delim = "|" if lines[0].count("|") > lines[0].count("\t") else "\t"
    hdr = [c.strip() for c in lines[0].split(delim)]
    ix = {h: i for i, h in enumerate(hdr)}
    missing = [c for c in ("USPS", "NAME", "INTPTLAT", "INTPTLONG") if c not in ix]
    if missing:
        raise SystemExit("Gazetteer %s file is missing columns %s; header was %s"
                         % (kind, missing, hdr))
    rows = []
    for line in lines[1:]:
        cells = [c.strip() for c in line.split(delim)]
        if len(cells) < len(hdr):
            continue
        try:
            lat = round(float(cells[ix["INTPTLAT"]]), 5)
            lon = round(float(cells[ix["INTPTLONG"]]), 5)
        except (ValueError, KeyError):
            continue
        rows.append((cells[ix["USPS"]].strip().upper(),
                     cells[ix["NAME"]], lat, lon))
    print("  %s: %d rows" % (kind, len(rows)))
    return rows


def latest_year(ctx, newest=None):
    """Newest published Gazetteer vintage, probed downward."""
    top = newest or date.today().year
    for y in range(top, top - 5, -1):
        try:
            req = urllib.request.Request(BASE % (y, y, "place"),
                                         headers={"User-Agent": "FastLocations/1.0"})
            req.get_method = lambda: "HEAD"
            urllib.request.urlopen(req, timeout=60, context=ctx)
            return y
        except Exception:
            continue
    return None


def main():
    ap = argparse.ArgumentParser(description="Rebuild us_places.json")
    ap.add_argument("--year", default="latest")
    ap.add_argument("--compare", action="store_true")
    a = ap.parse_args()
    ctx = ssl_context()

    year = latest_year(ctx) if str(a.year).lower() == "latest" else int(a.year)
    if not year:
        sys.exit("could not reach the Census Gazetteer to find the newest year")
    print("Census Gazetteer %d" % year)

    out = {}
    # cousubs first so that places, added second, overwrite them on a collision
    for kind in ("cousubs", "place"):
        for usps, raw, lat, lon in fetch_gazetteer(year, kind, ctx):
            # Key on the CLEANED name. A user types "Detroit", not
            # "Detroit city", and the scorer normalises what they typed - so
            # keying the raw Census name would make almost every lookup miss.
            # Emit BOTH spellings: the Census name as published and the name
            # with its type word removed. A user may type "Detroit" or
            # "Detroit city", and the file this replaces carried a mixture of
            # the two - 63,000 of its 64,003 keys are one form or the other.
            # Indexing both makes every lookup that used to work keep working.
            display = clean_name(raw)
            for key in {"%s|%s" % (usps, norm(raw)),
                        "%s|%s" % (usps, norm(display))}:
                if kind == "cousubs" and key in out:
                    continue                  # places already claimed it
                out[key] = [lat, lon, display]

    old = {}
    if os.path.exists(OUT):
        try:
            with open(OUT, encoding="utf-8") as fh:
                old = json.load(fh)
        except Exception:
            old = {}
    if old:
        shared = set(out) & set(old)
        moved = sum(1 for k in shared
                    if abs(out[k][0] - old[k][0]) > 0.01
                    or abs(out[k][1] - old[k][1]) > 0.01)
        print("  entries %d -> %d  (%d shared, %d gained, %d dropped, "
              "%d moved >0.01 deg)"
              % (len(old), len(out), len(shared), len(set(out) - set(old)),
                 len(set(old) - set(out)), moved))

    if a.compare:
        print("--compare: nothing written.")
        return

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, separators=(",", ":"), ensure_ascii=False)
    with open(META, "w", encoding="utf-8") as fh:
        json.dump({"built": date.today().isoformat(),
                   "source": "Census Gazetteer %d (place + cousubs national)" % year,
                   "entries": len(out)}, fh, indent=1)
    print("Wrote %s: %d places" % (OUT, len(out)))
    for probe in ("MI|detroit", "TX|austin", "NY|newyork", "PA|philadelphia"):
        print("    %-18s %s" % (probe, out.get(probe)))


if __name__ == "__main__":
    main()
