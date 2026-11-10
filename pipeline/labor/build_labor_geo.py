#!/usr/bin/env python3
r"""
build_labor_geo.py  (Phase 2 - spatial outputs for ArcGIS Online)
------------------
GeoJSON (WGS84) for the three labor geographies, ready to publish as hosted feature layers:

  out/geo/labor_areas_us.geojson      OEWS areas (393 metros + 137 nonmetro), dissolved from counties
  out/geo/labor_counties_us.geojson   US counties (2023 cartographic, Connecticut planning regions)
  out/geo/labor_er_ca.geojson         Canadian economic regions (76)

Each feature carries its key (geo_code - the join key into labor_metrics_*.csv), name, emp_detailed_sum (jobs summed over published occupations; suppressed ones are missing, so it runs a little under the all-occupations total),
and a "top 10 occupations by LQ" summary (top_lq_codes / top_lq_titles, at least --min-emp jobs) for
the EDO Labor panel.

Why geometry + table rather than one giant joined layer: there are ~800 occupations, so a fully
joined layer would repeat each polygon 800 times (several GB for counties). Publish the GeoJSON as the
layer and the labor_metrics_*.csv as a hosted table, then join on geo_code (an ArcGIS "join features"
view filtered by occupation drives the occupation picker). For quick maps, --occ writes a small
choropleth-ready layer for named occupations:
    py -3 build_labor_geo.py --occ 15-1252 51-4121 21231
  -> out/geo/occ_<code>_areas_us.geojson, occ_<code>_counties_us.geojson, occ_<code>_er_ca.geojson

SOURCES
  US counties: Census cartographic boundary file cb_2023_us_county_5m (downloaded automatically).
  CA ERs: StatCan 2021 cartographic boundary file for economic regions (ler_000b21s_e), fetched from
  StatCan's public ArcGIS map service (geo.statcan.gc.ca ... /2021/Cartographic_boundary_files/MapServer/2)
  as WGS84 GeoJSON and cached in raw/statcan/er_2021_cbf.geojson. If raw/statcan/ler_000b21a_e.zip (the
  shapefile from the 2021 boundary files page) is present it is used instead.

Needs: shapely, pyshp, pyproj  (py -3 -m pip install --user shapely pyshp pyproj)

USAGE
  py -3 build_labor_geo.py [--occ CODE ...] [--min-emp 50] [--tolerance 0.002]
"""
import collections
import io
import json
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import XW, download, load_json, out_path, raw_path

try:
    import shapefile
    from shapely.geometry import mapping, shape
    from shapely.ops import transform, unary_union
except ImportError:
    sys.exit("Needs shapely + pyshp: py -3 -m pip install --user shapely pyshp pyproj")

COUNTY_URL = "https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_us_county_5m.zip"
ER_ZIP = raw_path("statcan", "ler_000b21a_e.zip")          # optional local copy; otherwise the map service
ER_SERVICE = "https://geo.statcan.gc.ca/geo_wa/rest/services/2021/Cartographic_boundary_files/MapServer/2"


def arg(name, default, cast=str):
    return cast(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else default


MIN_EMP = arg("--min-emp", 50, int)
TOL = arg("--tolerance", 0.002, float)
OCCS = []
if "--occ" in sys.argv:
    for a in sys.argv[sys.argv.index("--occ") + 1:]:
        if a.startswith("--"):
            break
        OCCS.append(a)


def read_shp(zpath):
    z = zipfile.ZipFile(zpath)
    base = next(n[:-4] for n in z.namelist() if n.lower().endswith(".shp"))
    r = shapefile.Reader(shp=io.BytesIO(z.read(base + ".shp")), dbf=io.BytesIO(z.read(base + ".dbf")),
                         encoding="latin-1")
    prj = z.read(base + ".prj").decode("latin-1") if base + ".prj" in z.namelist() else ""
    return r, prj


def rounded(geom, nd=5):
    return json.loads(json.dumps(mapping(geom)), parse_float=lambda x: round(float(x), nd))


def write_fc(name, feats):
    p = out_path("geo", name)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump({"type": "FeatureCollection", "features": feats}, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"  wrote out/geo/{name}  ({len(feats):,} features, {os.path.getsize(p) / 1e6:.1f} MB)")


def table(name):
    t = load_json(out_path(name))
    return [dict(zip(t["fields"], r)) for r in t["rows"]]


def summaries(rows):
    """geo_code -> {total_emp, top_lq_codes, top_lq_titles}, plus geo_code -> occ -> row."""
    by = collections.defaultdict(list)
    for r in rows:
        by[r["geo_code"]].append(r)
    out, idx = {}, {}
    for g, rs in by.items():
        top = sorted((r for r in rs if r["lq"] is not None and (r["emp"] or 0) >= MIN_EMP), key=lambda r: -r["lq"])[:10]
        out[g] = {"emp_detailed_sum": sum(r["emp"] or 0 for r in rs),
                  "top_lq_codes": ", ".join(r["occ_code_native"] for r in top),
                  "top_lq_titles": " | ".join(f"{r['occ_title']} ({r['lq']:.1f})" for r in top)}
        idx[g] = {r["occ_code_native"]: r for r in rs}
    return out, idx


OCC_FIELDS = ["occ_code_native", "occ_title", "emp", "lq", "wage_median", "wage_index", "resident_workers",
              "growth_outlook", "turnover_proxy", "modelled", "currency"]


def occ_layers(tag, geoms, idx, names):
    for code in OCCS:
        feats = []
        for g, geom in geoms.items():
            r = idx.get(g, {}).get(code)
            if not r:
                continue
            feats.append({"type": "Feature", "geometry": geom,
                          "properties": {"geo_code": g, "geo_name": names.get(g), **{f: r.get(f) for f in OCC_FIELDS}}})
        if feats:
            write_fc(f"occ_{code}_{tag}.geojson", feats)


def us():
    c2a = load_json(os.path.join(XW, "county_to_area.json"))
    r, _ = read_shp(download(COUNTY_URL, raw_path("census", "cb_2023_us_county_5m.zip"), min_bytes=100_000))
    shapes = {}
    for sr in r.iterShapeRecords():
        g = shape(sr.shape.__geo_interface__)
        if not g.is_valid:
            g = g.buffer(0)
        shapes[sr.record["GEOID"]] = (g, sr.record["NAMELSAD"], sr.record["STUSPS"])
    print(f"US: {len(shapes)} county shapes")

    county_rows = table("labor_metrics_county.json")
    area_rows = table("labor_metrics_cbsa.json")
    c_sum, c_idx = summaries(county_rows)
    a_sum, a_idx = summaries(area_rows)
    a_name = {r["geo_code"]: (r["geo_name"], r["geo_level"]) for r in area_rows}

    feats, c_geoms, c_names = [], {}, {}
    for fips, (g, name, st) in shapes.items():
        x = c2a.get(fips, {})
        gs = g.simplify(TOL, preserve_topology=True)
        gj = rounded(gs)
        c_geoms[fips] = gj
        c_names[fips] = f"{name}, {st}"
        feats.append({"type": "Feature", "geometry": gj, "properties": {
            "geo_code": fips, "geo_name": f"{name}, {st}", "state": st, "area_code": x.get("oews_area"),
            "area_name": x.get("oews_area_name"), "modelled": True, **c_sum.get(fips, {})}})
    write_fc("labor_counties_us.geojson", feats)

    members = collections.defaultdict(list)
    for fips, (g, _, _) in shapes.items():
        a = (c2a.get(fips) or {}).get("oews_area")
        if a:
            members[a].append(g)
    feats, a_geoms, a_names = [], {}, {}
    for a, gs in members.items():
        u = unary_union(gs).simplify(TOL * 2, preserve_topology=True)
        gj = rounded(u)
        a_geoms[a] = gj
        nm, lvl = a_name.get(a, ((c2a.get(next(f for f in c2a if c2a[f].get("oews_area") == a)) or {}).get("oews_area_name"), None))
        a_names[a] = nm
        feats.append({"type": "Feature", "geometry": gj, "properties": {
            "geo_code": a, "geo_name": nm, "geo_level": lvl, "n_counties": len(gs), "modelled": False, **a_sum.get(a, {})}})
    write_fc("labor_areas_us.geojson", feats)
    occ_layers("counties_us", c_geoms, c_idx, c_names)
    occ_layers("areas_us", a_geoms, a_idx, a_names)


def er_shapes_from_zip():
    from pyproj import CRS, Transformer
    r, prj = read_shp(ER_ZIP)
    tf = Transformer.from_crs(CRS.from_wkt(prj) if prj else CRS.from_epsg(3347), CRS.from_epsg(4326), always_xy=True)
    fields = [f[0] for f in r.fields[1:]]
    k_code = next(f for f in fields if f.upper() in ("ERUID", "ERUID21"))
    k_name = next(f for f in fields if f.upper().startswith("ERNAME") or f.upper().startswith("ERENAME"))
    out = []
    for sr in r.iterShapeRecords():
        g = shape(sr.shape.__geo_interface__)
        out.append((str(sr.record[k_code]), sr.record[k_name], transform(tf.transform, g)))
    return out


def er_shapes_from_service():
    """StatCan's public ArcGIS service for the 2021 cartographic boundary files (layer 2 = ER,
    'ler_000b21s_e'). Asked for WGS84 GeoJSON, generalized server-side; cached in raw/statcan/."""
    cache = raw_path("statcan", "er_2021_cbf.geojson")
    if not os.path.exists(cache):
        from _common import http_get
        feats, off = [], 0
        while True:
            url = (f"{ER_SERVICE}/query?where=1%3D1&outFields=ERUID,ERNAME,PRUID&returnGeometry=true"
                   f"&outSR=4326&maxAllowableOffset=0.002&f=geojson&resultOffset={off}&resultRecordCount=50")
            page = json.loads(http_get(url, timeout=300))
            got = page.get("features") or []
            feats.extend(got)
            if len(got) < 50:
                break
            off += 50
        with open(cache, "w", encoding="utf-8") as fh:
            json.dump({"type": "FeatureCollection", "features": feats}, fh)
        print(f"  cached {len(feats)} ER shapes from the StatCan map service")
    fc = load_json(cache)
    return [(f["properties"]["ERUID"], f["properties"]["ERNAME"], shape(f["geometry"])) for f in fc["features"]]


def drop_specks(g, min_area=0.001):
    """Drop island parts under ~0.001 sq deg (a few km2); the Arctic and BC coasts have thousands and
    they, not vertex detail, make the ER layer heavy. The largest part is always kept."""
    from shapely.geometry import MultiPolygon
    if g.geom_type != "MultiPolygon":
        return g
    parts = sorted(g.geoms, key=lambda p: -p.area)
    keep = [parts[0]] + [p for p in parts[1:] if p.area >= min_area]
    return MultiPolygon(keep) if len(keep) > 1 else keep[0]


def canada():
    shapes_ = er_shapes_from_zip() if os.path.exists(ER_ZIP) else er_shapes_from_service()
    rows = table("labor_metrics_ca_er.json")
    s, idx = summaries(rows)
    feats, geoms, names = [], {}, {}
    for code, name, g in shapes_:
        if not g.is_valid:
            g = g.buffer(0)
        g = drop_specks(g).simplify(TOL * 5, preserve_topology=True)
        gj = rounded(g, 4)
        geoms[code] = gj
        names[code] = name
        feats.append({"type": "Feature", "geometry": gj, "properties": {
            "geo_code": code, "geo_name": name, "prov_code": code[:2], "modelled": True,
            "comparability": "comparable, not identical", **s.get(code, {})}})
    write_fc("labor_er_ca.geojson", feats)
    occ_layers("er_ca", geoms, idx, names)


def main():
    us()
    canada()


if __name__ == "__main__":
    main()
