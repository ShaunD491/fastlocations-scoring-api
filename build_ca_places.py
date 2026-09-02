#!/usr/bin/env python3
r"""
build_ca_places.py
------------------
Rebuilds ca_places.json — the Canadian gazetteer behind "within X miles of
Kitchener, ON" — from Natural Resources Canada's official toponym service.

WHY THIS ONE MATTERS MORE THAN IT LOOKS
    scorer.geocode_place tries the bundled Canadian table first and, when a name
    is not in it, falls through to Nominatim. The code's own comment says that
    external call is blocked on the host. So in production, Canadian market
    proximity works for exactly the names in this file and silently fails for
    everything else - and the file it replaces held 590 entries, of which 280
    were not places at all but census-division centroids under the division's
    name ("Division No. 1"). Roughly 155 real Canadian towns were bundled.

    On a developer machine, where Nominatim is reachable, this gap is invisible:
    every test lookup succeeds. That is why it survived. The fix is to bundle
    enough of the country that the fallback is rarely needed.

SOURCE: NRCan Canadian Geographical Names, GeoGratis service.
    https://geogratis.gc.ca/services/geoname/en/geonames.csv?concise=<code>
        &province=<2-digit code>
    Public, no key. Queried per province because the service caps a result set
    around 1000 and silently ignores `start` - see fetch(). Concise codes are
    feature classes; the populated-place ones are taken:

        CITY  city        TOWN  town              VILG  village
        HAM   hamlet      UNP   unincorporated     MUN1/MUN2  municipality

    MUN1 is not optional and is easy to miss: British Columbia incorporates much
    of itself as district municipalities, so Squamish, Whistler and Langley are
    MUN1 rather than CITY or TOWN and were absent from the first build. Checking
    one known town against the result is what caught it.

    Indian Reserves (IR) are deliberately excluded: there are thousands, they
    are not what a site selector types into a market-proximity box, and
    including them would crowd the bare-name index with collisions.

KEY FORMAT, which must match the scorer
    "<PROV>|<normalised name>" -> [lat, lon, "Display Name"]
    plus a bare "<normalised name>" key, because scorer.geocode_place falls back
    to the unqualified name when no province is given. First writer wins on the
    bare key, and the categories are loaded largest-settlement-first so a city
    beats a hamlet of the same name.

    Normalisation is scorer._norm: NFKD, strip to ASCII, lowercase, drop
    non-alphanumerics. Reproduced here so this does not depend on the scorer
    loading. NOTE that stripping to ASCII is what makes "Montréal" findable as
    "montreal", which is what people type.

CENSUS DIVISION NAMES ARE KEPT
    The old file's division-name entries are preserved from ca_cd_centroids.json,
    so "Division No. 1" and the CD-name lookups keep resolving. They are added
    LAST so a real town of the same name wins.

USAGE
    python build_ca_places.py
    python build_ca_places.py --compare      # report the delta, write nothing
"""
import argparse, csv, io, json, os, re, ssl, sys, time, unicodedata, urllib.parse, urllib.request
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "ca_places.json")
CENTROIDS = os.path.join(HERE, "ca_cd_centroids.json")
FEATURES = os.path.join(HERE, "ca_features.json")
META = os.path.join(HERE, "ca_places_meta.json")

SERVICE = "https://geogratis.gc.ca/services/geoname/en/geonames.csv"
# largest settlement type first: it wins the bare-name key on a collision
CATEGORIES = ["CITY", "TOWN", "MUN1", "MUN2", "VILG", "HAM", "UNP"]
PAGE = 1000

# Names people type that the official gazetteer files under something else.
ALIASES = {
    "QC|quebeccity": "QC|quebec",
    "quebeccity": "quebec",
}

PROV = {"10": "NL", "11": "PE", "12": "NS", "13": "NB", "24": "QC", "35": "ON",
        "46": "MB", "47": "SK", "48": "AB", "59": "BC", "60": "YT", "61": "NT",
        "62": "NU"}


def norm(x):
    """Must match scorer._norm exactly, or every lookup misses."""
    x = unicodedata.normalize("NFKD", str(x)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", x)


def ssl_context():
    for k in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        p = os.environ.get(k)
        if p and os.path.exists(p):
            try:
                return ssl.create_default_context(cafile=p)
            except Exception:
                pass
    return ssl.create_default_context()


def fetch(code, prov_code, ctx):
    """One concise code for one province.

    Queried PER PROVINCE, not paged. The service accepts num/start but IGNORES
    start: requesting start=0, 1000 and 2000 for TOWN returns the same rows,
    960 of 1000 identical, and the result set is capped around 1000. Paging it
    would silently return the same first page over and over - it produced 61,000
    rows collapsing to 8,634 unique keys before that was spotted. A province
    filter keeps every result set well under the cap (Ontario has 147 towns), so
    each query returns a complete answer.
    """
    url = SERVICE + "?" + urllib.parse.urlencode(
        {"concise": code, "province": prov_code, "num": PAGE})
    req = urllib.request.Request(url, headers={"User-Agent": "FastLocations/1.0"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120, context=ctx) as r:
                rows = list(csv.DictReader(
                    io.StringIO(r.read().decode("utf-8-sig", "replace"))))
            if len(rows) >= PAGE:
                print("    [!] %s/%s returned %d rows, at the service cap - some "
                      "places may be missing" % (code, prov_code, len(rows)))
            return rows
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2.0 * (attempt + 1))
    return []


def fetch_category(code, ctx):
    rows = []
    for pc in PROV:
        rows.extend(fetch(code, pc, ctx))
    print("  %-5s %6d places" % (code, len(rows)))
    return rows


def main():
    ap = argparse.ArgumentParser(description="Rebuild ca_places.json")
    ap.add_argument("--compare", action="store_true")
    a = ap.parse_args()
    ctx = ssl_context()

    print("NRCan Canadian Geographical Names")
    out, skipped = {}, 0
    for code in CATEGORIES:
        for r in fetch_category(code, ctx):
            name = (r.get("name") or "").strip()
            pc = (r.get("province.code") or "").strip()
            prov = PROV.get(pc)
            try:
                lat = round(float(r.get("latitude")), 6)
                lon = round(float(r.get("longitude")), 6)
            except (TypeError, ValueError):
                skipped += 1
                continue
            if not name or not prov:
                skipped += 1
                continue
            key = norm(name)
            out.setdefault("%s|%s" % (prov, key), [lat, lon, name])
            out.setdefault(key, [lat, lon, name])   # bare fallback, first wins

    # Census-division names, kept so the old lookups keep working. Added last so
    # a real settlement of the same name takes precedence.
    cd_added = 0
    try:
        with open(CENTROIDS, encoding="utf-8") as fh:
            cent = json.load(fh)
        with open(FEATURES, encoding="utf-8") as fh:
            feat = json.load(fh)
        for cduid, pt in cent.items():
            f = feat.get(cduid) or {}
            nm, prov = f.get("NAME"), f.get("ST_ABBREV")
            if not nm or not prov or not isinstance(pt, list) or len(pt) < 2:
                continue
            key = norm(nm)
            for k in ("%s|%s" % (prov, key), key):
                if k not in out:
                    out[k] = [pt[0], pt[1], nm]
                    cd_added += 1
    except Exception as e:
        print("  [!] could not add census-division names: %s" % str(e)[:80])

    # Colloquial names the official gazetteer does not carry. An explicit list,
    # NOT a blanket copy of the previous file: copying forward whatever the last
    # build produced compounds on every run (30,202 became 31,295 on a rebuild
    # from the identical source) and makes the output depend on its own history
    # rather than only on the source.
    for key, official in ALIASES.items():
        if key in out:
            continue
        src_key = out.get(official)
        if src_key:
            out[key] = src_key

    old = {}
    if os.path.exists(OUT):
        try:
            with open(OUT, encoding="utf-8") as fh:
                old = json.load(fh)
        except Exception:
            old = {}
    if old:
        kept = len(set(old) & set(out))
        print("  entries %d -> %d  (%d of the old keys kept, %d dropped)"
              % (len(old), len(out), kept, len(set(old) - set(out))))
    print("  %d census-division name keys added, %d source rows skipped"
          % (cd_added, skipped))

    if a.compare:
        print("--compare: nothing written.")
        return

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, separators=(",", ":"), ensure_ascii=False)
    with open(META, "w", encoding="utf-8") as fh:
        json.dump({"built": date.today().isoformat(),
                   "source": "NRCan Canadian Geographical Names (GeoGratis), "
                             "concise codes %s" % "/".join(CATEGORIES),
                   "entries": len(out)}, fh, indent=1, ensure_ascii=False)
    print("Wrote %s: %d keys" % (OUT, len(out)))
    for probe in ("ON|woodstock", "ON|timmins", "BC|squamish", "QC|montreal"):
        v = out.get(probe)
        print("    %-16s %s" % (probe, v if v is None else
                                [v[0], v[1], v[2].encode("ascii", "replace").decode()]))


if __name__ == "__main__":
    main()
