#!/usr/bin/env python3
r"""
build_innovation_index.py
-------------------------
Ingests the StatsAmerica Innovation Intelligence (II3) county MEASURES file and writes the curated
subset the scorer uses.

SOURCE: Indiana Business Research Center, Kelley School of Business, Indiana University.
    "Innovation Intelligence" (II3)  -  https://www.statsamerica.org/innovation
    File: "Innovation Intelligence - Measures - States and Counties.csv"   (time_id 2023)

USE THE *MEASURES* FILE, NOT THE *INDEX VALUES* FILE. They are not interchangeable:
the Index Values export contains ready-made sub-indexes but silently omits the merged Virginia
areas -- 3,087 counties, with Fairfax, Prince William, Albemarle, Augusta, Montgomery and 46 other
Virginia county-equivalents absent entirely. The Measures export carries the full 3,110-county BEA
geography including all 25 merged codes. Since Virginia is a state this engine is already watched
closely for over-indexing, a silent 51-county hole there was not acceptable, so this script trades
the pre-built indexes for complete coverage and composes its own (see NORMALISATION below).

WHY ONLY A SUBSET IS INGESTED:
The file carries 56 measures, many of which this engine already sources directly and independently --
educational attainment (ACS), unemployment and poverty (LFS/ACS/BEA), STEM and high-tech employment
(ACS S2401, CBP NAICS 5417), population growth. Ingesting those would score the same underlying facts
twice, once from our own pull and once more inside a borrowed composite, silently doubling their
weight. What IS ingested is only what the engine is otherwise blind to.

NORMALISATION: the raw measures are in incompatible units -- percentages, ratios, dollars scaled by
GDP -- so they cannot simply be averaged. Each measure is percentile-ranked across all counties
first, then the ranks are averaged within a theme. This is the same operation the scorer applies to
every other metric, so the treatment is consistent end to end. It is a simplification of StatsAmerica's
own within-index weighting, which is documented in their Driving Regional Innovation report; equal
weighting is used here because the alternative is to bury undocumented judgement calls in our data.

BEA COUNTIES -- THE JOIN TRAP:
This file does NOT use standard Census FIPS. It uses "BEA counties", which merge 24 Virginia counties
with their independent cities plus Alaska and Hawaii areas. Codes like 51919 and 51942 do not exist in
Census geography, so a naive join drops every affected county without raising an error. This script
expands each merged code back to its constituent Census FIPS, assigning the merged value to each
component -- the honest treatment, since the measure cannot be disaggregated below the geography it
was computed for. Components are flagged with "shared_with" so the provenance stays visible.

USAGE:
    python build_innovation_index.py "Innovation Intelligence - Measures - States and Counties.csv"
Writes county_innovation_index.json:
    { "<census_fips>": {"innovation_output":0-100, "business_dynamism":0-100, "broadband":0-100,
                        "bea_county":"<code>", "shared_with":n|null}, "_meta": {...} }
"""
import csv, datetime, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "county_innovation_index.json")

# Theme -> measure codes. Sign is +1 where higher is better, -1 where higher is worse.
THEMES = {
    "innovation_output": {           # knowledge CREATION: measured output and institutional spillover
        "m10111": +1,                # Patent Technology Diffusion
        "m10112": +1,                # University-Based Knowledge Spillovers
        "m40221": +1,                # Change in Average Patenting Rate
        "m40222": +1,                # Patent Diversity
    },
    "business_dynamism": {           # entrepreneurial formation, capital, and industry structure
        "m20131": +1,                # Establishment Births to All Establishments Ratio
        "m20133": +1,                # Jobs Attributed to Establishment Births
        "m20134": +1,                # Change in Establishment Births Ratio
        "m20141": +1,                # Establishment Expansions to Contractions Ratio
        "m20142": +1,                # Establishment Births to Deaths Ratio
        "m20143": +1,                # Traded Sector Births+Expansions to Deaths+Contractions
        "m30151": +1, "m30152": +1, "m30153": +1, "m30154": +1,   # venture capital $
        "m30161": +1, "m30162": +1, "m30163": +1,                 # IPOs and VC deal counts
        "m30171": +1, "m30172": +1, "m30173": +1, "m30174": +1,   # FDI employment / investment
        "m40191": +1,                # Latent Innovation
        "m40192": +1,                # Industry Diversity
        "m40201": +1,                # Industry Cluster Growth Factor
        "m40202": +1,                # Industry Cluster Strength
    },
    # Genuinely absent from the model until now; feeds infrastructure, not the innovation composites.
    # BOTH measures are DEFICIT measures despite their names -- m50231 is the share of population
    # WITHOUT adequate broadband, not the share with it, so both are inverted. Verified against known
    # counties rather than inferred from the label: Todd County SD scores 89.7 and Gwinnett GA 6.2.
    # CAVEAT: built on FCC December 2019 availability and 2018 population. That is the oldest input
    # in this engine by some margin; the FCC's Broadband Data Collection has since superseded it.
    "broadband": {
        "m50231": -1,                # Broadband Infrastructure and Adoption (deficit)
        "m50232": -1,                # Broadband Adoption Barriers (deficit)
    },
}

# Deliberately NOT ingested, with the reason, so the decision survives future edits:
EXCLUDED_BECAUSE_ALREADY_MEASURED = {
    "m10102-m10106": "educational attainment - sourced directly from ACS",
    "m10121-m10123": "STEM degrees, tech occupations, high-tech employment - ACS S2401 and CBP 5417",
    "m10991": "prime working-age population growth - sourced from POPGRW20CY",
    "m40211-m40212": "GDP per worker - overlaps the BEA wage/cost signal",
    "m40991-m40992": "job growth and high-tech employment change - overlaps workforce and CBP",
    "m50247-m50996": "income, poverty, unemployment, net migration - LFS/ACS/BEA",
    "m20132": "marked (Removed) in the source file",
    "m20135/m20136/m30991/m30181-m30184": "establishment size and proprietorship - marginal, omitted",
}

# BEA/IBRC merged county code -> the Census FIPS codes it covers.
# Source: https://www.statsamerica.org/innovation/about.aspx (Geography section).
BEA_EXPAND = {
    "02232": ["02230", "02105"],
    "02280": ["02195", "02275"],
    "02901": ["02130", "02198"],       # 02201 Prince of Wales-Outer Ketchikan dissolved 2008
    "02261": ["02063", "02066"],       # Valdez-Cordova split into Chugach + Copper River, 2019
    "15901": ["15005", "15009"],
    # 51560 Clifton Forge (2001) and 51515 Bedford City (2013) reverted to towns and were absorbed
    # by their parent counties, so the parent alone carries the value.
    "51901": ["51003", "51540"], "51903": ["51005", "51580"],
    "51907": ["51015", "51790", "51820"], "51019": ["51019"],
    "51911": ["51031", "51680"], "51913": ["51035", "51640"],
    "51918": ["51053", "51570", "51730"], "51919": ["51059", "51600", "51610"],
    "51921": ["51069", "51840"], "51923": ["51081", "51595"],
    "51929": ["51089", "51690"], "51931": ["51095", "51830"],
    "51933": ["51121", "51750"], "51939": ["51143", "51590"],
    "51941": ["51149", "51670"], "51942": ["51153", "51683", "51685"],
    "51944": ["51161", "51775"], "51945": ["51163", "51530", "51678"],
    "51947": ["51165", "51660"], "51949": ["51175", "51620"],
    "51951": ["51177", "51630"], "51953": ["51191", "51520"],
    "51955": ["51195", "51720"], "51958": ["51199", "51735"],
}
RENAMED = {"46113": "46102",          # Shannon County SD -> Oglala Lakota County (2015)
           "02270": "02158"}          # Wade Hampton AK -> Kusilvak Census Area (2015)

WANTED = {c: (theme, sign) for theme, codes in THEMES.items() for c, sign in codes.items()}


def pct_rank(values):
    """Percentile-rank a {key: value} map to 0..100, averaging ties. Mirrors the scorer's own ranking
    so a measure means the same thing here as it does downstream."""
    items = sorted(values.items(), key=lambda kv: kv[1])
    n = len(items)
    if n < 2:
        return {k: 50.0 for k in values}
    out, i = {}, 0
    while i < n:
        j = i
        while j + 1 < n and items[j + 1][1] == items[i][1]:
            j += 1
        rank = (i + j) / 2.0
        for k, _ in items[i:j + 1]:
            out[k] = round(100.0 * rank / (n - 1), 3)
        i = j + 1
    return out


def main():
    src = (sys.argv[1] if len(sys.argv) > 1 else
           os.path.join(HERE, "Innovation Intelligence - Measures - States and Counties.csv"))
    if not os.path.exists(src):
        sys.exit(f"II3 measures file not found: {src}\n"
                 f"Download the county MEASURES file from https://www.statsamerica.org/innovation")

    raw, year = {}, None
    with open(src, encoding="utf-8-sig", errors="replace") as fh:
        rd = csv.reader(fh)
        head = [str(h).strip().lower() for h in next(rd)]
        try:
            i_geo, i_code = head.index("geo_id"), head.index("code_id")
            i_val = next(i for i, h in enumerate(head) if "value" in h)
            i_time = head.index("time_id")
        except (ValueError, StopIteration):
            sys.exit(f"unexpected column layout: {head}\n"
                     f"this script expects the MEASURES export, not the Index Values export")
        for r in rd:
            code = r[i_code].strip()
            if code not in WANTED:
                continue
            geo = r[i_geo].strip().zfill(5)
            if geo[2:] == "000":                 # state-level row, not a county
                continue
            try:
                v = float(r[i_val])
            except (TypeError, ValueError):
                continue
            year = year or r[i_time].strip()
            raw.setdefault(code, {})[geo] = v

    if not raw:
        sys.exit("no wanted measures found -- is this the Index Values file rather than Measures?")

    # Percentile-rank each measure across all BEA counties, applying sign, then average within theme.
    ranked = {}
    for code, vals in raw.items():
        theme, sign = WANTED[code]
        pr = pct_rank({k: sign * v for k, v in vals.items()})
        for geo, p in pr.items():
            ranked.setdefault(geo, {}).setdefault(theme, []).append(p)

    out, shared, unexpanded = {}, 0, []
    for geo, themes in ranked.items():
        rec = {t: round(sum(ps) / len(ps), 2) for t, ps in themes.items()}
        targets = BEA_EXPAND.get(geo, [RENAMED.get(geo, geo)])
        if geo not in BEA_EXPAND and geo[2:] >= "900":
            unexpanded.append(geo)
        for fips in targets:
            e = dict(rec)
            e["bea_county"] = geo
            e["shared_with"] = (len(targets) - 1) if len(targets) > 1 else None
            out[fips] = e
        if len(targets) > 1:
            shared += len(targets)

    counties = dict(out)
    out["_meta"] = {"source_file": os.path.basename(src), "time_id": year,
                    "built": datetime.date.today().isoformat(),
                    "bea_counties": len(ranked), "census_fips": len(counties),
                    "measures_used": {t: sorted(c) for t, c in
                                      ((t, list(cs)) for t, cs in THEMES.items())},
                    "excluded": EXCLUDED_BECAUSE_ALREADY_MEASURED}
    json.dump(out, open(OUT, "w", encoding="utf-8"), separators=(",", ":"))

    print(f"Wrote {OUT}   (source time_id {year})")
    print(f"  {len(ranked)} BEA counties -> {len(counties)} Census FIPS "
          f"({shared} carry a value shared across a merged BEA area)")
    for t, cs in THEMES.items():
        got = sum(1 for c in cs if c in raw)
        print(f"  {t:<18} {got}/{len(cs)} measures found")
    if unexpanded:
        print(f"  WARNING unexpanded merged codes (add to BEA_EXPAND): {sorted(unexpanded)}")
    try:
        feat = json.load(open(os.path.join(HERE, "county_features.json"), encoding="utf-8"))
        missing = sorted(set(feat) - set(counties))
        print(f"  joins to county_features.json: {len(set(counties) & set(feat))}/{len(feat)}")
        if missing:
            print(f"  counties with NO innovation data ({len(missing)}): {missing[:12]}")
    except FileNotFoundError:
        pass


if __name__ == "__main__":
    main()
