#!/usr/bin/env python3
"""
publish_arcgis.py - publish the labor GeoJSON layers (out/geo, from build_labor_geo.py) to ArcGIS Online as
hosted feature layers (Phase 5 of Occupation_Labor_Data_Plan.md).

Nothing is sent unless --publish is given; the default is a dry run that lists what would be published.

    py -3 publish_arcgis.py                      # dry run
    py -3 publish_arcgis.py --publish            # add or overwrite every layer, share with the organization
    py -3 publish_arcgis.py --publish --only labor_areas_us occ_51-4121_areas_us
    py -3 publish_arcgis.py --publish --group <groupId>   # also share with an AI+Plus group

Credentials come from the environment, never from this file:
    ARCGIS_PORTAL   default https://www.arcgis.com
    ARCGIS_TOKEN    an API key or token with content create/publish privileges, or
    ARCGIS_USER + ARCGIS_PASSWORD  (a built-in ArcGIS account; enterprise / SSO logins need ARCGIS_TOKEN)

Sharing: occupation layers are AI+Plus content, so layers are shared with the organization (and an optional
group) only, never with everyone. Each layer keeps one item per title in a "FastLocations Labor" folder; a second
run overwrites the hosted layer in place (same item ids, same URLs) instead of making copies.
Uses only the ArcGIS REST API (requests); the arcgis Python package is not needed.
"""
import argparse
import json
import os
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
GEO = os.path.join(HERE, "out", "geo")
FOLDER = "FastLocations Labor"
TAGS = "FastLocations,labor,occupations,OEWS,workforce"
DESCRIBE = {
    "labor_areas_us": ("FastLocations Labor - U.S. OEWS areas",
                       "Metro and nonmetro OEWS areas with employment and the most concentrated occupations. "
                       "Source: BLS Occupational Employment and Wage Statistics, May 2025."),
    "labor_counties_us": ("FastLocations Labor - U.S. counties",
                          "Counties with modelled occupation employment (OEWS area figures apportioned with Census ACS "
                          "2020-2024 resident workers). Modelled figures are flagged."),
    "labor_er_ca": ("FastLocations Labor - Canadian economic regions",
                    "Statistics Canada 2021 economic regions with occupation employment (2021 Census, NOC 2021). "
                    "Comparable to, not identical with, U.S. figures."),
}


def describe(stem):
    if stem in DESCRIBE:
        return DESCRIBE[stem]
    # occ_<code>_<level>  e.g. occ_51-4121_areas_us
    parts = stem.split("_")
    code, level = parts[1], "_".join(parts[2:])
    where = {"areas_us": "U.S. OEWS areas", "counties_us": "U.S. counties", "er_ca": "Canadian economic regions"}.get(level, level)
    return (f"FastLocations Labor - occupation {code} by {where}",
            f"Employment, location quotient, wages, growth and turnover for occupation {code} by {where}. "
            "Sources: BLS OEWS May 2025, Census ACS / QWI, state projections; Statistics Canada 2021 Census, "
            "ESDC Job Bank wages, COPS. County and census-division figures are modelled.")


class Portal:
    def __init__(self, portal):
        self.base = portal.rstrip("/") + "/sharing/rest"
        self.s = requests.Session()
        self.token = os.environ.get("ARCGIS_TOKEN")
        if not self.token:
            user, pw = os.environ.get("ARCGIS_USER"), os.environ.get("ARCGIS_PASSWORD")
            if not (user and pw):
                sys.exit("Set ARCGIS_TOKEN, or ARCGIS_USER and ARCGIS_PASSWORD.")
            r = self.call("generateToken", data={"username": user, "password": pw, "referer": portal,
                                                 "expiration": 120, "client": "referer"}, auth=False)
            self.token = r["token"]
        self.user = self.call("community/self")["username"]

    def call(self, path, data=None, files=None, auth=True, get=False):
        data = dict(data or {}, f="json")
        if auth:
            data["token"] = self.token
        url = f"{self.base}/{path}"
        hdr = {"Referer": self.base} if auth else {}
        r = (self.s.get(url, params=data, headers=hdr, timeout=120) if get
             else self.s.post(url, data=data, files=files, headers=hdr, timeout=600))
        r.raise_for_status()
        j = r.json()
        if isinstance(j, dict) and j.get("error"):
            raise RuntimeError(f"{path}: {j['error'].get('message')} {j['error'].get('details') or ''}")
        return j

    def folder_id(self):
        content = self.call(f"content/users/{self.user}", get=True)
        for f in content.get("folders", []):
            if f["title"] == FOLDER:
                return f["id"]
        return self.call(f"content/users/{self.user}/createFolder", {"title": FOLDER})["folder"]["id"]

    def find(self, title, typ):
        q = f'title:"{title}" AND owner:{self.user} AND type:"{typ}"'
        res = self.call("search", {"q": q, "num": 10}, get=True).get("results", [])
        return next((i for i in res if i["title"] == title), None)

    def wait(self, item_id, job_id=None):
        for _ in range(180):
            d = {"jobType": "publish", "jobId": job_id} if job_id else {}
            st = self.call(f"content/users/{self.user}/items/{item_id}/status", d, get=True)
            if st.get("status") in ("completed", "failed"):
                if st["status"] == "failed":
                    raise RuntimeError(f"publish failed: {st.get('statusMessage')}")
                return
            time.sleep(5)
        raise RuntimeError("publish did not finish in 15 minutes")


def service_name(stem):
    return "FL_Labor_" + stem.replace("-", "_")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--publish", action="store_true", help="actually publish (default: dry run)")
    ap.add_argument("--only", nargs="*", help="layer file stems to include (default: all in out/geo)")
    ap.add_argument("--group", help="also share with this group id (e.g. an AI+Plus group)")
    ap.add_argument("--portal", default=os.environ.get("ARCGIS_PORTAL", "https://www.arcgis.com"))
    a = ap.parse_args()

    stems = sorted(f[:-8] for f in os.listdir(GEO) if f.endswith(".geojson"))
    if a.only:
        missing = set(a.only) - set(stems)
        if missing:
            sys.exit(f"not in out/geo: {', '.join(sorted(missing))}")
        stems = [s for s in stems if s in a.only]
    if not stems:
        sys.exit("No GeoJSON in out/geo. Run build_labor_geo.py first.")

    print(f"{'PUBLISH' if a.publish else 'DRY RUN'} to {a.portal}, folder '{FOLDER}', shared with the organization"
          + (f" and group {a.group}" if a.group else "") + " (never public)")
    for s in stems:
        p = os.path.join(GEO, s + ".geojson")
        with open(p, encoding="utf-8") as fh:
            n = len(json.load(fh).get("features", []))
        print(f"  {s:34s} {n:6d} features  {os.path.getsize(p) / 1e6:5.1f} MB  -> {describe(s)[0]}")
    if not a.publish:
        print("Nothing sent. Add --publish to publish.")
        return 0

    P = Portal(a.portal)
    fid = P.folder_id()
    for s in stems:
        title, desc = describe(s)
        path = os.path.join(GEO, s + ".geojson")
        src = P.find(title, "GeoJson")
        with open(path, "rb") as fh:
            files = {"file": (s + ".geojson", fh, "application/geo+json")}
            meta = {"title": title, "type": "GeoJson", "tags": TAGS, "description": desc,
                    "snippet": desc[:250], "accessInformation": "BLS, U.S. Census Bureau, Statistics Canada, ESDC",
                    "licenseInfo": "Public statistical data. Modelled figures are estimates."}
            if src:
                P.call(f"content/users/{P.user}/items/{src['id']}/update", meta, files=files)
                src_id = src["id"]
            else:
                src_id = P.call(f"content/users/{P.user}/{fid}/addItem", meta, files=files)["id"]
        layer = P.find(title, "Feature Service")
        params = {"name": service_name(s), "maxRecordCount": 2000, "hasStaticData": True,
                  "layerInfo": {"capabilities": "Query"}}
        pub = P.call(f"content/users/{P.user}/publish", {
            "itemId": src_id, "filetype": "geojson", "publishParameters": json.dumps(params),
            "overwrite": "true" if layer else "false"})
        svc = pub["services"][0]
        if svc.get("error"):
            raise RuntimeError(f"{s}: {svc['error']}")
        P.wait(svc["serviceItemId"], svc.get("jobId"))
        share = {"everyone": "false", "org": "true"}
        if a.group:
            share["groups"] = a.group
        P.call(f"content/users/{P.user}/items/{svc['serviceItemId']}/share", share)
        print(f"  {'overwrote' if layer else 'published'} {s}: {svc.get('serviceurl')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
