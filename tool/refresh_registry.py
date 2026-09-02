# -*- coding: utf-8 -*-
"""
refresh_registry.py — what the scorer reads, and how each file gets rebuilt.
==========================================================================

One entry per runtime JSON that `scorer.py` loads. This file is the contract
between the scorer's data layer and the refresh tool: if the scorer starts
reading a new file, add it here, otherwise the tool will not know it exists.

FIELDS
    id            stable key, used in the UI and in state/last_run.json
    title         human label
    output        the runtime file the scorer reads, relative to Projects/
    feeds         which scorer dimension(s) this data feeds
    country       US | CA | BOTH
    acquire       how a refresh is obtained:
                    download    - fetch `url`, then run `builder`
                    api         - builder fetches from a public API itself; no
                                  source file to stage and no key needed
                    census_api  - builder calls the Census API itself (needs a key)
                    derive      - no network; builder reads files already on disk
                    assisted    - source sits behind bot protection or a form.
                                  The tool cannot fetch it; you drop the file in
                                  staging/ and the tool builds from it.
                    legacy      - no builder exists. Built once by an ad-hoc
                                  script that was not kept. Shown for visibility
                                  and age, never auto-refreshed.
    builder       script in Projects/ that writes `output` (None for legacy)
    args          builder argv template. {src} = acquired source file,
                  {census_key} = key from config. Empty list = no arguments.
    url           direct download (acquire=download)
    member        glob for the file to pull out of a downloaded archive.
                  None means hand the archive to the builder as-is, which is
                  right for the builders that unzip internally (NRI, CA labour).
    source_page   human landing page — shown in the UI, and the starting point
                  for the Brave/Claude source healer when `url` breaks
    search_hint   Brave query used to re-find a moved download URL
    cadence_days  how often the source itself actually republishes; drives the
                  "stale" flag. Nothing is refreshed on a timer automatically.
    min_records   floor for the post-build sanity check. A rebuild that lands
                  under this is rejected and the previous file is restored.
                  An int applies to every output; a dict gives each output its
                  own floor, which the two routing indexes need - the US one
                  carries 1887 counties, the Canadian one 224 CDs.
    depends_on    local files a derived build reads. "Only update newer" skips
                  the rebuild when the output is already newer than all of them.
    vintage_field a per-record field carrying the data's vintage, used by the
                  staleness check when the file has no _meta block. The values
                  look like "CBP2023", "USDA NASS 2022", "2026-07".
    probe_expect  what a healthy probe response looks like: "json" (default) or
                  "csv". NRCan's gazetteer serves CSV, and assuming JSON reports
                  a working service as broken.
    probe_params  query string for "Check sources only" to prove a live API is
                  answering. Per dataset, because an API's idea of a harmless
                  request is its own - USDM wants a date window, ArcGIS wants a
                  where clause.
    key_space     the file whose keys the scorer actually looks this data up by.
                  Validation counts how many keys OVERLAP it, not how many
                  records exist - a rebuild can gain records and still lose
                  almost all its usable coverage by landing on the wrong
                  geography vintage. See the CIMD note on ca_livability.
    then          ids that MUST be rebuilt after this one (derived indexes)
    notes         why this exists / what to watch out for

WHY THE `legacy` ROWS ARE HERE AT ALL
    Fourteen runtime files have no surviving builder. Leaving them out would
    make the inventory look complete when it is not — county_features.json is
    the single biggest input to the model and nothing in the repo rebuilds it.
    They are listed, dated, and clearly marked so the gap is visible.
"""

# --------------------------------------------------------------------------
# US — scored dimensions
# --------------------------------------------------------------------------
DATASETS = [
    {
        "id": "county_nri",
        "title": "FEMA National Risk Index (hazards)",
        "output": "county_nri.json",
        "feeds": "hazard screen, infrastructure",
        "country": "US",
        "acquire": "assisted",
        "builder": "build_us_nri.py",
        "args": ["{src}"],
        "url": "https://hazards.fema.gov/nri/Content/StaticDocuments/DataDownload"
               "/NRI_Table_Counties/NRI_Table_Counties.zip",
        "member": None,
        "source_page": "https://hazards.fema.gov/nri/data-resources",
        "search_hint": "FEMA National Risk Index county table csv download NRI_Table_Counties",
        "cadence_days": 365,
        "min_records": 3000,
        "key_space": "county_features.json",
        "then": [],
        "notes": "hazards.fema.gov answers 403 to every non-browser request, so the "
                 "zip cannot be fetched programmatically. Download NRI_Table_Counties.zip "
                 "by hand into staging/ and refresh; the builder unzips it itself.",
    },
    {
        "id": "county_bea",
        "title": "BEA earnings by place of work (cost)",
        "output": "county_bea.json",
        "feeds": "cost",
        "country": "US",
        "acquire": "download",
        "builder": "build_bea_cost.py",
        "args": ["{src}"],
        "url": "https://apps.bea.gov/regional/zip/CAINC30.zip",
        "member": "CAINC30__ALL_AREAS_*.csv",
        "source_page": "https://apps.bea.gov/regional/downloadzip.cfm",
        "search_hint": "BEA regional CAINC30 all areas zip download economic profile",
        "cadence_days": 365,
        "min_records": 2800,
        "key_space": "county_features.json",
        "then": [],
        "notes": "Employer-side labour cost. Distinct from ESRI median household income, "
                 "which is residence-side and mixes in commuters and retirees.",
    },
    {
        "id": "county_occupation",
        "title": "Census ACS S2401 occupation shares",
        "output": "county_occupation.json",
        "feeds": "workforce (skill_supply)",
        "country": "US",
        "acquire": "census_api",
        "builder": "build_occupation.py",
        "args": ["{census_key}"],
        "url": None,
        "member": None,
        "source_page": "https://api.census.gov/data/key_signup.html",
        "search_hint": "",
        "cadence_days": 365,
        "min_records": 3000,
        "key_space": "county_features.json",
        "then": [],
        "notes": "Needs a free Census API key in refresh_config.json. Without real "
                 "occupation counts, picking 'engineers' vs 'general labor' barely "
                 "moves the match.",
    },
    {
        "id": "county_innovation",
        "title": "Census CBP R&D establishments (NAICS 5417)",
        "output": "county_innovation.json",
        "feeds": "innovation",
        "country": "US",
        "acquire": "census_api",
        "builder": "build_us_innovation.py",
        "args": ["{census_key}"],
        "url": None,
        "member": None,
        "source_page": "https://api.census.gov/data/key_signup.html",
        "search_hint": "",
        "cadence_days": 365,
        "min_records": 2000,
        "key_space": "county_features.json",
        "vintage_field": "ref",
        "then": [],
        "notes": "Answers 'is R&D performed here as a line of business', as opposed to "
                 "the occupation measure's 'do technical people live here'. NOT an "
                 "input to county_innovation_index - the two both feed the innovation "
                 "dimension but come from unrelated sources (CBP vs StatsAmerica).",
    },
    {
        "id": "county_innovation_index",
        "title": "StatsAmerica Innovation Intelligence (II3)",
        "output": "county_innovation_index.json",
        "feeds": "innovation",
        "country": "US",
        "acquire": "assisted",
        "builder": "build_innovation_index.py",
        "args": ["{src}"],
        "url": None,
        "member": "Innovation Intelligence - Measures*.csv",
        "source_page": "https://www.statsamerica.org/innovation",
        "search_hint": "StatsAmerica Innovation Intelligence measures states and counties csv download",
        "cadence_days": 365,
        "min_records": 3000,
        "key_space": "county_features.json",
        "then": [],
        "notes": "Use the MEASURES export, never the Index Values export - the latter "
                 "silently drops 51 Virginia county-equivalents including Fairfax.",
    },
    {
        "id": "county_land_cost",
        "title": "USDA farm real estate value per acre",
        "output": "county_land_cost.json",
        "feeds": "real_estate",
        "country": "US",
        "acquire": "assisted",
        "builder": "build_land_cost.py",
        "args": ["{src}"],
        "url": None,
        "member": "*price-per-acre*.csv",
        "source_page": "https://www.nass.usda.gov/Publications/AgCensus/2022/",
        "search_hint": "USDA NASS 2022 census of agriculture average farm real estate value per acre by county csv",
        "cadence_days": 1825,
        "min_records": 2000,
        "key_space": "county_features.json",
        "vintage_field": "ref",
        "then": [],
        "notes": "Census of Agriculture runs every five years, so this genuinely does "
                 "not need refreshing often. It is a raw-land proxy, not a price for "
                 "zoned served industrial land.",
    },
    {
        "id": "utility_territories",
        "title": "EIA-861 electric utility service territories",
        "output": "edo_master_table_dual.json",
        "feeds": "EDO lead routing",
        "country": "US",
        "acquire": "download",
        "builder": "build_utility_territories.py",
        "args": ["{src}"],
        "url": "https://www.eia.gov/electricity/data/eia861/zip/f8612024.zip",
        "member": "Service_Territory_*.xlsx",
        "source_page": "https://www.eia.gov/electricity/data/eia861/",
        "search_hint": "EIA form 861 service territory zip annual electric utility data",
        "cadence_days": 365,
        "min_records": 300,
        "then": ["edo_indexes"],
        "notes": "Writes territory_geoids INTO the EDO master table rather than a file "
                 "of its own, so the routing indexes must be rebuilt after it. The URL "
                 "carries a year - bump it when EIA publishes the next one.",
    },

    # ----------------------------------------------------------------------
    # Canada — scored dimensions
    # ----------------------------------------------------------------------
    {
        "id": "ca_labour",
        "title": "StatCan Labour Force Survey by CMA",
        "output": "ca_labour.json",
        "feeds": "workforce (CA)",
        "country": "CA",
        "acquire": "download",
        "builder": "build_ca_labour.py",
        "args": ["{src}"],
        "url": "https://www150.statcan.gc.ca/n1/tbl/csv/14100459-eng.zip",
        "member": None,
        "source_page": "https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=1410045901",
        "search_hint": "Statistics Canada table 14-10-0459 labour force characteristics census metropolitan area csv",
        "cadence_days": 90,
        "min_records": 200,
        "key_space": "ca_features.json",
        "vintage_field": "ref",
        "then": [],
        "notes": "The one that most needs refreshing. Without it the Canadian model "
                 "runs on 2021 Census rates collected under COVID restrictions - a "
                 "9.6% national unemployment rate against a current US ~4.0%.",
    },
    {
        "id": "ca_popgrowth",
        "title": "StatCan population estimates by census division",
        "output": "ca_popgrowth.json",
        "feeds": "demographics (CA)",
        "country": "CA",
        "acquire": "download",
        "builder": "build_ca_popgrowth.py",
        "args": ["{src}"],
        "url": "https://www150.statcan.gc.ca/n1/tbl/csv/17100152-eng.zip",
        "member": "17100152.csv",
        "source_page": "https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=1710015201",
        "search_hint": "Statistics Canada table 17-10-0152 population estimates census division csv",
        "cadence_days": 365,
        "min_records": 250,
        "key_space": "ca_features.json",
        "then": [],
        "notes": "Replaces the stale 2016-21 census growth figure.",
    },
    {
        "id": "ca_occupation",
        "title": "StatCan 2021 Census occupation by census division",
        "output": "ca_occupation.json",
        "feeds": "workforce (CA skill_supply)",
        "country": "CA",
        "acquire": "download",
        "builder": "build_ca_occupation.py",
        "args": ["{src}"],
        "url": "https://www150.statcan.gc.ca/n1/tbl/csv/98100471-eng.zip",
        "member": "98100471.csv",
        "source_page": "https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=9810047101",
        "search_hint": "Statistics Canada 98-10-0471 place of work status by occupation census divisions",
        "cadence_days": 1825,
        "min_records": 250,
        "key_space": "ca_features.json",
        "then": [],
        "notes": "BIG: ~150 MB zipped, ~3.3 GB extracted. The builder streams it rather "
                 "than loading it. Census-vintage, so it only changes every five years.",
    },
    {
        "id": "ca_livability",
        "title": "StatCan Index of Multiple Deprivation (CIMD)",
        "output": "ca_livability.json",
        "feeds": "livability (CA)",
        "country": "CA",
        "acquire": "assisted",
        "builder": "build_ca_livability.py",
        "args": ["{src}"],
        "url": None,
        "member": "can_scores_quintiles_EN.csv",
        "source_page": "https://www150.statcan.gc.ca/n1/en/catalogue/452000012023001",
        "search_hint": "Statistics Canada Canadian Index of Multiple Deprivation 2021 can_scores_quintiles_EN csv download",
        "cadence_days": 1825,
        "min_records": 200,
        "key_space": "ca_features.json",
        "then": [],
        "notes": "Deliberately excludes CIMD's Ethno-cultural composition dimension, "
                 "which StatCan documents as descriptive rather than a deprivation "
                 "measure. Distributed from a catalogue page, not a stable file URL.",
    },
    {
        "id": "ca_crime",
        "title": "StatCan Crime Severity Index by CMA",
        "output": "ca_csi_2025.json",
        "feeds": "safety (CA)",
        "country": "CA",
        "acquire": "download",
        "builder": "build_ca_crime.py",
        "args": ["{src}"],
        "url": "https://www150.statcan.gc.ca/n1/tbl/csv/35100026-eng.zip",
        "member": None,
        "source_page": "https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=3510002601",
        "search_hint": "Statistics Canada police reported crime severity index census metropolitan area t004a csv",
        "cadence_days": 365,
        "min_records": 30,
        "then": ["ca_cma"],
        "notes": "StatCan table 35-10-0026, a stable annual URL the builder unzips "
                 "itself. It used to point at a csv inside a Daily article, which is "
                 "the wrong kind of address: the Daily names its files POSITIONALLY, "
                 "so t004a means only table 4 of that day, and the 2026 one is "
                 "Consumer Price Index data. Bumping that date would have loaded "
                 "inflation figures into the crime file. Feeds ca_cma, so a refresh "
                 "here cascades.",
    },
    {
        "id": "ca_infrastructure",
        "title": "StatCan Open Database of Infrastructure (ODI)",
        "output": "ca_infrastructure.json",
        "feeds": "infrastructure (CA)",
        "country": "CA",
        "acquire": "assisted",
        "builder": "build_ca_infrastructure.py",
        "args": ["{src_dir}"],
        "url": None,
        "member": "*.gpkg",
        "source_page": "https://www.statcan.gc.ca/en/lode/databases/odi",
        "search_hint": "Statistics Canada Open Database of Infrastructure ODI geopackage download",
        "cadence_days": 730,
        "min_records": 200,
        "key_space": "ca_features.json",
        "then": [],
        "notes": "Ten separate GeoPackages, one per asset category, downloaded "
                 "individually from the ODI page. Drop them all in one staging folder; "
                 "the builder takes the FOLDER, not a file.",
    },
    {
        "id": "ca_cma",
        "title": "Canadian CD to metro (CMA) labels",
        "output": "ca_cma.json",
        "feeds": "result display (CA)",
        "country": "CA",
        "acquire": "derive",
        "builder": "build_ca_cma.py",
        "args": [],
        "url": None,
        "member": None,
        "source_page": "",
        "search_hint": "",
        "cadence_days": 365,
        "min_records": 30,
        "depends_on": ["ca_csi_2025.json"],
        "then": [],
        "notes": "Derived from ca_csi_2025.json plus a curated list of non-anchor CDs "
                 "(Peel, York, Durham, Halton all sit in the Toronto CMA). Rerun after "
                 "the crime refresh.",
    },

    # ----------------------------------------------------------------------
    # Derived — no network, but must be rerun when their inputs change
    # ----------------------------------------------------------------------
    {
        "id": "edo_indexes",
        "title": "EDO lead-routing indexes",
        "output": "edo_fips_index.json",
        "feeds": "EDO lead routing",
        "country": "BOTH",
        "acquire": "derive",
        "builder": "build_edo_indexes.py",
        "args": [],
        "url": None,
        "member": None,
        "source_page": "",
        "search_hint": "",
        "cadence_days": 180,
        "min_records": {"edo_fips_index.json": 1000,
                        "edo_ca_cd_index.json": 150},
        "depends_on": ["edo_master_table_dual.json"],
        "then": [],
        "notes": "Rebuilds edo_fips_index.json AND edo_ca_cd_index.json from the EDO "
                 "master table. Must run after ANY territory or roster change, or "
                 "leads route to the wrong EDO - or to nobody.",
    },
    {
        "id": "regional_territories",
        "title": "Regional EDO member counties",
        "output": "edo_master_table_dual.json",
        "feeds": "EDO lead routing",
        "country": "US",
        "acquire": "derive",
        "builder": "build_regional_territories.py",
        "args": [],
        "url": None,
        "member": None,
        "source_page": "",
        "search_hint": "",
        "cadence_days": 180,
        "min_records": 300,
        "depends_on": ["regional_edo_members.json"],
        "then": ["edo_indexes"],
        "notes": "Reads regional_edo_members.json, which is hand-researched per agency "
                 "(there is no national dataset the way EIA-861 covers utilities). "
                 "Agencies missing from it keep a single home county and stay flagged "
                 "INCOMPLETE. Use 'Research sources' to find members for the gaps.",
    },
    {
        "id": "orgs_with_properties",
        "title": "EDOs with listed properties",
        "output": "orgs_with_properties.json",
        "feeds": "property-access bonus",
        "country": "BOTH",
        "acquire": "derive",
        "builder": "build_property_orgs.py",
        "args": ["../properties/properties.js"],
        "url": None,
        "member": None,
        "source_page": "",
        "search_hint": "",
        "cadence_days": 90,
        "min_records": 3,
        "depends_on": ["../properties/properties.js"],
        "then": [],
        "notes": "Parses the dashboard's properties.js DATASETS array. Rerun whenever a "
                 "property dataset is added to or removed from the dashboard.",
    },

    # ----------------------------------------------------------------------
    # Researched / curated — Brave + Claude
    # ----------------------------------------------------------------------
    {
        "id": "state_infrastructure_grades",
        "title": "ASCE state infrastructure report-card grades",
        "output": "state_infrastructure_grades.json",
        "feeds": "infrastructure (US)",
        "country": "US",
        "acquire": "research",
        "builder": None,
        "args": [],
        "url": None,
        "member": None,
        "source_page": "https://infrastructurereportcard.org/state-item/",
        "search_hint": "ASCE infrastructure report card {state} overall grade state",
        "cadence_days": 1460,
        "min_records": 40,
        "then": [],
        "notes": "ASCE grades each state on its own four-year cycle, so this is never "
                 "uniformly current. Research mode reads each state's report-card page "
                 "and proposes a grade; nothing is written until you approve the diff.",
    },

    # ----------------------------------------------------------------------
    # Legacy — loaded by the scorer, but nothing in the repo rebuilds them
    # ----------------------------------------------------------------------
    {
        "id": "county_features",
        "title": "US county base features (health block)",
        "output": "county_features.json",
        "feeds": "every US dimension",
        "country": "US",
        "acquire": "download",
        "builder": "build_county_features.py",
        "args": ["--blocks", "chr"],
        "url": "https://www.countyhealthrankings.org/sites/default/files/media"
               "/document/analytic_data2025.csv",
        "member": None,
        "source_page": "https://www.countyhealthrankings.org/health-data",
        "search_hint": "County Health Rankings analytic data csv download national",
        "cadence_days": 365,
        "min_records": 3000,
        "key_space": None,
        "then": ["us_catchment"],
        "notes": "PARTIAL by necessity, and the only builder here that updates its "
                 "output in place rather than rewriting it. Much of this file is "
                 "licensed ESRI Business Analyst data; WLTHINDXCY and SEI_CY have no "
                 "public equivalent at all. The registered run refreshes only the "
                 "County Health Rankings block - 6 fields, verified same-methodology, "
                 "median change 0.0% on four of them. Two further blocks exist but "
                 "are NOT wired to the button because they change the model: run "
                 "'python build_county_features.py <census key> --dry-run' to see "
                 "them measured. Deleting county_features.json is unrecoverable - "
                 "nothing can rebuild it from scratch.",
    },
    {
        "id": "county_workforce",
        "title": "ACS workforce availability",
        "output": "county_workforce.json",
        "feeds": "workforce (staffability, hiring headroom, shift fit)",
        "country": "US",
        "acquire": "census_api",
        "builder": "build_county_workforce.py",
        "args": ["{census_key}"],
        "url": None,
        "member": None,
        "source_page": "https://api.census.gov/data/key_signup.html",
        "search_hint": "",
        "cadence_days": 365,
        "min_records": 3000,
        "key_space": "county_features.json",
        "then": [],
        "notes": "ACS B23025 + B23020. Needs a free Census API key. The builder was "
                 "reverse-engineered from the file it replaces and reproduces it to "
                 "the unit across all 3,222 counties, including recruitable = "
                 "unemployed + 0.25 x not-in-labor-force. That 0.25 is a judgement, "
                 "not a measurement, and 16+ NILF counts retirees - see the docstring. "
                 "Also rebuilds from the local County_1.csv with --csv.",
    },
    {
        "id": "county_power",
        "title": "EIA generation capacity within 95 km",
        "output": "county_power.json",
        "feeds": "infrastructure, renewable screen",
        "country": "US",
        "acquire": "api",
        "builder": "build_county_power.py",
        "args": [],
        "url": "https://services2.arcgis.com/FiaPA4ga0iQKduv3/arcgis/rest/services"
               "/Power_Plants_in_the_US/FeatureServer/0/query",
        "member": None,
        "source_page": "https://atlas.eia.gov/",
        "search_hint": "EIA power plants arcgis feature service latitude longitude nameplate capacity",
        "cadence_days": 180,
        "min_records": 2500,
        "key_space": "county_features.json",
        "probe_params": {"where": "1=1", "returnCountOnly": "true", "f": "json"},
        "then": [],
        "notes": "Live EIA plant layer, no key. The file it replaced came from a "
                 "static export stamped 201705 - eight years stale, 1,887 US plants "
                 "at 100+ MW against 2,459 today. Refreshing moved median county "
                 "capacity 2,168 -> 4,179 MW and median renewable share 13.2 -> 19.6, "
                 "which matters because renew_share is what a project screens on when "
                 "it asks for a greener grid. Radius is 95 km, not the 60 miles "
                 "scorer.py's comment claims - 95 km reproduced the old file exactly.",
    },
    {
        "id": "county_drought",
        "title": "US Drought Monitor water security",
        "output": "county_drought.json",
        "feeds": "water screen",
        "country": "US",
        "acquire": "api",
        "builder": "build_county_drought.py",
        "args": [],
        "url": "https://usdmdataservices.unl.edu/api/CountyStatistics"
               "/GetDroughtSeverityStatisticsByAreaPercent",
        "member": None,
        "source_page": "https://droughtmonitor.unl.edu/DmData/DataDownload.aspx",
        "search_hint": "US Drought Monitor county statistics API area percent data services",
        "cadence_days": 90,
        "min_records": 3000,
        "key_space": "county_features.json",
        "probe_params": {"aoi": "DC", "startdate": "6/24/2026",
                          "enddate": "7/1/2026", "statisticsType": "1"},
        "then": [],
        "notes": "Five-year mean of the weekly USDM maps, not a snapshot. The file "
                 "this replaced was one week (2026-07-05): Bullock County AL read "
                 "11.5 / 89.6 / 100.0 on three consecutive maps, and the scorer "
                 "excludes below 50, so the answer flipped on which Thursday the "
                 "file was built. Also bridges Connecticut, whose counties were "
                 "replaced by planning regions in 2022 - USDM moved, "
                 "county_features.json did not, and all 8 CT counties were "
                 "silently getting no drought data at all.",
    },
    {
        "id": "county_groundwater",
        "title": "Aquifer depletion flags",
        "output": "county_groundwater.json",
        "feeds": "water screen",
        "country": "US",
        "acquire": "legacy",
        "builder": None, "args": [], "url": None, "member": None,
        "source_page": "https://cida.usgs.gov/ngwmn/",
        "search_hint": "",
        "cadence_days": 1095, "min_records": 5, "then": [],
        "notes": "Hand-curated worst-case counties (Ogallala and similar). Small and "
                 "deliberate; changes rarely.",
    },
    {
        "id": "county_groundwater_trend",
        "title": "Groundwater well trends",
        "output": "county_groundwater_trend.json",
        "feeds": "water screen",
        "country": "US",
        "acquire": "legacy",
        "builder": None, "args": [], "url": None, "member": None,
        "source_page": "https://cida.usgs.gov/ngwmn/",
        "search_hint": "",
        "cadence_days": 730, "min_records": 200, "key_space": "county_features.json",
        "then": [],
        "notes": "Investigated and deliberately NOT built. The source is USGS data "
                 "release v4.0 - a STATIC versioned research product, not a feed, so "
                 "a builder could not keep it current, which was the point. Worse, "
                 "the stored file (21,105 wells / 685 counties) matches no natural "
                 "filter of the 834 MB long-format trends csv (nearest are 88,442 "
                 "and 12,620 sites) nor either well list (NGWMN 17,006, NWIS "
                 "914,280), and neither file carries a county code, so assignment "
                 "needs spatial work with no boundary file - nearest-centroid would "
                 "misassign exactly in the large western counties where groundwater "
                 "decline matters. A builder writing different numbers under the "
                 "same filename would look like a refresh while quietly changing a "
                 "screen input. Revisit if USGS publishes v5.",
    },
    {
        "id": "us_catchment",
        "title": "Regional market catchment population",
        "output": "us_catchment.json",
        "feeds": "market_size",
        "country": "US",
        "acquire": "derive",
        "builder": "build_us_catchment.py",
        "args": [],
        "url": None,
        "member": None,
        "source_page": "",
        "search_hint": "",
        "cadence_days": 365,
        "min_records": 3000,
        "key_space": "county_features.json",
        "depends_on": ["county_features.json"],
        "then": [],
        "notes": "Population within 110 km, decayed exp(-d/55km), county included at "
                 "full weight. No network - derived entirely from county_features "
                 "centroids and populations, so it MUST be rebuilt after any "
                 "population refresh or geography change. Formula recovered by "
                 "fitting the stored file: reproduces it to a median 0.72% with no "
                 "meaningful bias. It is what stops fragmented metros losing to "
                 "consolidated ones - Manhattan 12.2M against LA 10.7M, the reverse "
                 "of raw county population.",
    },
    {
        "id": "fips_to_msa",
        "title": "County to metro (MSA) label",
        "output": "fips_to_msa.json",
        "feeds": "result display",
        "country": "US",
        "acquire": "api",
        "builder": "build_fips_to_msa.py",
        "args": [],
        "url": "https://www2.census.gov/programs-surveys/metro-micro/geographies"
               "/reference-files/",
        "member": None,
        "probe_expect": "any",   # url is a directory listing, so HTML is correct
        "source_page": "https://www.census.gov/geographies/reference-files/time-series/demo/metro-micro/delineation-files.html",
        "search_hint": "Census CBSA delineation files list1 core based statistical areas",
        "cadence_days": 1095,
        "min_records": 1000,
        "key_space": "county_features.json",
        "then": [],
        "notes": "Census CBSA delineation, List 1, newest year probed. Metropolitan "
                 "only - micropolitan areas are 10,000-50,000 towns and calling one "
                 "a metro in a result would overstate the market (--include-micro "
                 "adds them). The 2023 revision relabelled 289 counties (Birmingham-"
                 "Hoover -> Birmingham) and moved 29 out of metro status entirely "
                 "(Pine Bluff AR), so the labels shown against results were several "
                 "years out of date. Presentation only; nothing scores on it.",
    },
    {
        "id": "us_places",
        "title": "US place gazetteer",
        "output": "us_places.json",
        "feeds": "market proximity",
        "country": "US",
        "acquire": "api",
        "builder": "build_us_places.py",
        "args": [],
        "url": "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/",
        "member": None,
        "probe_expect": "any",   # url is a directory listing, so HTML is correct
        "source_page": "https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.html",
        "search_hint": "Census gazetteer national place cousubs file download",
        "cadence_days": 365,
        "min_records": 50000,
        "key_space": None,
        "then": [],
        "notes": "Census Gazetteer place + cousubs files, newest vintage probed. "
                 "Backs the market-proximity lookup and the /places autocomplete, "
                 "so coverage here is a user-facing feature. 64,003 -> 102,242 keys: "
                 "both the published name and the name without its type word are "
                 "indexed, so 'Detroit' and 'Detroit city' both resolve. Watch the "
                 "format - the 2025 vintage switched from tab- to pipe-delimited and "
                 "added a column, which silently yields an empty file if assumed.",
    },
    {
        "id": "metrics_extra",
        "title": "Supplementary county metrics",
        "output": "metrics_extra.json",
        "feeds": "livability, demographics",
        "country": "US",
        "acquire": "legacy",
        "builder": None, "args": [], "url": None, "member": None,
        "source_page": "",
        "search_hint": "",
        "cadence_days": 730, "min_records": 2000, "key_space": "county_features.json",
        "then": [],
        "notes": "NOT READ BY THE SCORER - verified, zero references in scorer.py. It is "
                 "a build INPUT that county_features fields were merged from, not a "
                 "runtime file, and is listed here only so its 1.2 MB is not "
                 "mistaken for live data. Nothing breaks if it goes stale.",
    },
    {
        "id": "ca_features",
        "title": "Canadian CD base features (population block)",
        "output": "ca_features.json",
        "also": ["ca_socio.json"],
        "feeds": "every CA dimension",
        "country": "CA",
        "acquire": "derive",
        "builder": "build_ca_features.py",
        "args": [],
        "url": None,
        "member": None,
        "source_page": "https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=1710015201",
        "search_hint": "",
        "cadence_days": 365,
        "min_records": 250,
        "key_space": None,
        "depends_on": ["ca_popgrowth.json"],
        "then": [],
        "notes": "PARTIAL, like county_features, and for the same kind of reason. "
                 "Refreshes TOTPOP_CY, POPDENS_CY and labour_force from the annual "
                 "population estimates already downloaded for ca_popgrowth - the "
                 "file held the 2021 Census count, 36.99M against StatCan's 41.5M "
                 "for 2025, so the Canadian model was scoring a country 4.7 million "
                 "people short. income, dwelling_value and bachelor_share CANNOT be "
                 "refreshed: every StatCan income series stops at the province or "
                 "CMA (DGUID schema 0002 and S0503, never 0003), and the other two "
                 "are Census products. They move when the 2026 Census lands in "
                 "2027-28. Provenance is in ca_features_meta.json, not inside the "
                 "file - this IS the CA feature table and a _meta key would become "
                 "a phantom census division.",
    },
    {
        "id": "ca_socio",
        "title": "Canadian CD socioeconomics",
        "output": "ca_socio.json",
        "feeds": "cost, demographics (CA)",
        "country": "CA",
        "acquire": "legacy",
        "builder": None, "args": [], "url": None, "member": None,
        "source_page": "",
        "search_hint": "",
        "cadence_days": 1825, "min_records": 250, "key_space": "ca_features.json",
        "then": [],
        "notes": "PARTLY maintained now: labour_force is rescaled by build_ca_features.py "
                 "alongside ca_features (it is listed in that entry's `also`, so "
                 "backup and validation cover it). Still legacy for income, "
                 "dwelling_value and bachelor_share, which no StatCan series "
                 "publishes below the province or CMA - they are 2021 Census values "
                 "and cannot move until the 2026 Census releases in 2027-28.",
    },
    {
        "id": "ca_regional",
        "title": "Canadian regional CSI / infrastructure points",
        "output": "ca_regional.json",
        "feeds": "safety, infrastructure (CA)",
        "country": "CA",
        "acquire": "legacy",
        "builder": None, "args": [], "url": None, "member": None,
        "source_page": "",
        "search_hint": "",
        "cadence_days": 1095, "min_records": 250, "key_space": "ca_features.json",
        "then": [],
        "notes": "Older CSI vintage, overlaid by ca_csi_2025 for the ~41 metro CDs.",
    },
    {
        "id": "ca_water_stress",
        "title": "Canadian water stress regions",
        "output": "ca_water_stress.json",
        "feeds": "water screen (CA)",
        "country": "CA",
        "acquire": "legacy",
        "builder": None, "args": [], "url": None, "member": None,
        "source_page": "",
        "search_hint": "",
        "cadence_days": 1095, "min_records": 5, "then": [],
        "notes": "Curated, with a severity and a source note per region.",
    },
    {
        "id": "ca_innovation",
        "title": "Canadian provincial innovation",
        "output": "ca_innovation.json",
        "feeds": "innovation (CA)",
        "country": "CA",
        "acquire": "legacy",
        "builder": None, "args": [], "url": None, "member": None,
        "source_page": "",
        "search_hint": "",
        "cadence_days": 730, "min_records": 5, "then": [],
        "notes": "Province-level only - Canada has no county-equivalent R&D series to "
                 "match the US CBP measure.",
    },
    {
        "id": "ca_places",
        "title": "Canadian place gazetteer",
        "output": "ca_places.json",
        "feeds": "market proximity (CA)",
        "country": "CA",
        "acquire": "api",
        "builder": "build_ca_places.py",
        "args": [],
        "url": "https://geogratis.gc.ca/services/geoname/en/geonames.csv",
        "probe_expect": "csv",
        "probe_params": {"concise": "CITY", "province": "35", "num": "1"},
        "member": None,
        "source_page": "https://geogratis.gc.ca/",
        "search_hint": "NRCan Canadian Geographical Names geogratis geonames service",
        "cadence_days": 730,
        "min_records": 20000,
        "key_space": None,
        "then": [],
        "notes": "590 -> 30,202 keys. This one was quietly broken in PRODUCTION only: "
                 "scorer.geocode_place falls through to Nominatim for any Canadian "
                 "name not bundled, and the code notes that call is blocked on the "
                 "host - so every lookup succeeded in development and failed live. "
                 "The old file held 590 keys of which 280 were census-division "
                 "centroids, not places. Query PER PROVINCE: the service caps a "
                 "result set near 1000 and silently ignores `start`, which returns "
                 "the same page forever. All 590 original keys are carried forward.",
    },
    {
        "id": "ca_cd_centroids",
        "title": "Canadian CD centroids",
        "output": "ca_cd_centroids.json",
        "feeds": "distance calculations (CA)",
        "country": "CA",
        "acquire": "legacy",
        "builder": None, "args": [], "url": None, "member": None,
        "source_page": "",
        "search_hint": "",
        "cadence_days": 1825, "min_records": 250, "key_space": "ca_features.json",
        "then": [],
        "notes": "Static by nature, and verified rather than assumed: 293 of 293 census "
                 "divisions covered, every centroid inside Canada, all 13 provinces "
                 "represented. These are population-weighted centroids of the 2021 "
                 "census-division geography, which does not move until the "
                 "boundaries are redrawn - there is no live source that would "
                 "improve it, so a builder would add provenance and no currency. "
                 "It supplies every Canadian lat/lon the scorer uses.",
    },
    {
        "id": "incentives_index",
        "title": "Incentives index (per state/province)",
        "output": "incentives_index.json",
        "feeds": "incentives",
        "country": "BOTH",
        "acquire": "derive",
        "builder": "build_incentives_index.py",
        "args": [],
        "url": None,
        "member": None,
        "source_page": "",
        "search_hint": "",
        "cadence_days": 90,
        "min_records": 50,
        "key_space": None,
        "depends_on": ["../incentives/JSON_test/incentives_all_test.json"],
        "then": [],
        "notes": "Summarises the Incentives Search Tool's master (port 5001) into what "
                 "the scorer reads. The previous builder had hard-coded sandbox paths "
                 "and could not run, so the index was frozen at 1,557 programs while "
                 "the tool collected 6,930 - the scorer was ranking on 22% of the "
                 "evidence. Rebuilt it also fixed substring matching that counted "
                 "'cer-TIF-ied' as tax increment financing, and added DC plus the "
                 "three Canadian territories, which had scored null on incentives "
                 "entirely. Rerun after any incentives run.",
    },
    {
        "id": "organizations",
        "title": "EDO organizations",
        "output": "organizations.json",
        "feeds": "EDO lead routing",
        "country": "BOTH",
        "acquire": "legacy",
        "builder": None, "args": [], "url": None, "member": None,
        "source_page": "",
        "search_hint": "",
        "cadence_days": 90, "min_records": 300, "then": [],
        "notes": "Maintained by the Organizations editor on port 5002, not here. Listed "
                 "so its age is visible alongside everything else the scorer reads.",
    },
]

BY_ID = {d["id"]: d for d in DATASETS}

# Acquisition kinds the tool can actually run end to end without you fetching
# anything by hand.
AUTOMATABLE = ("download", "api", "census_api", "derive")

ACQUIRE_LABEL = {
    "download":   "Auto download",
    "api":        "Live API",
    "census_api": "Census API",
    "derive":     "Derived locally",
    "assisted":   "Manual download",
    "research":   "Brave + Claude",
    "legacy":     "No builder",
}


def ordered(ids):
    """Build order for a set of ids: every entry's `then` dependants are pulled
    in and placed AFTER it, with registry order as the tiebreak.

    Order is not cosmetic here. edo_fips_index.json is derived from the EDO
    master table, so rebuilding the index before the territory script has
    rewritten that table silently indexes the old territories.
    """
    rank = {d["id"]: i for i, d in enumerate(DATASETS)}

    # transitive closure: selecting a trigger selects its dependants too
    want = set()
    stack = [i for i in ids if i in BY_ID]
    while stack:
        cur = stack.pop()
        if cur in want:
            continue
        want.add(cur)
        stack.extend(t for t in BY_ID[cur].get("then", []) if t in BY_ID)

    # topological sort, lowest registry rank first among the ready set
    blockers = {i: set() for i in want}
    for i in want:
        for t in BY_ID[i].get("then", []):
            if t in want:
                blockers[t].add(i)

    out = []
    while blockers:
        ready = sorted((i for i, b in blockers.items() if not b), key=rank.get)
        if not ready:                       # a `then` cycle; fall back to rank
            ready = sorted(blockers, key=rank.get)[:1]
        for i in ready:
            out.append(i)
            del blockers[i]
            for b in blockers.values():
                b.discard(i)
    return out
