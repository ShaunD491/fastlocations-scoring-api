#!/usr/bin/env python3
"""
scorer.py — FastLocations deterministic site-matching scorer  (ProjectCriteria v1).

Dual-spine: ranks US counties (FIPS) and Canadian Census Divisions (CDUID), then
rolls each up to the serving EDO customer from organizations.json.

Twelve weighted dimensions (percentile-ranked within the candidate set, then weighted):
  workforce      US: unemployment, staffability, prime-age share, skill supply ; CA: unemployment + staffability
  demographics   US: education (2x), growth, participation ; CA: bachelor share, growth, participation
  infrastructure US: ASCE state grade ; CA: CMA = high, mid elsewhere
  logistics      US: airports + ports + commute ; CA: airport/port/grid counts
  incentives     value tier (2x) + type diversity + priority match (state/province-keyed)
  real_estate    US: property-tax rate ; CA: median dwelling value
  cost           US: median income + wealth index ; CA: median income
  safety         US: crime rate ; CA: StatCan Crime Severity Index
  market_size    regional catchment population (US and CA)
  livability     US: County Health Rankings outcomes ; CA: Numbeo most-livable cities (ranked CDs only)
  innovation     US: technical employment, R&D presence, patenting, business dynamism ; CA: provincial index
  dai            US only: county DAI score (county_dai.json, built from DAI.csv); CA has no equivalent -> null

Rules: required/excluded = filters, preferred = bonus; coverage gaps -> null
(weights renormalise over non-null dims, never penalise). Every metric is percentile-ranked
within its country; the cost dimension is additionally ranked within market-size tiers
(TIERED_DIMS / COST_TIERS) so a metro's wages are compared with other metros. Results split into PRIMARY
(served by a customer EDO, distinct orgs) and OTHER NOTABLE (top non-customer counties).
Small-county reliability damping + local/regional coverage bonus applied to the final.
"""
import json,os,sys,math,collections,urllib.request,urllib.parse
O=os.environ.get("FL_DATA_DIR") or os.path.dirname(os.path.abspath(__file__))
# encoding="utf-8" is not optional. open() defaults to the platform encoding,
# which on Windows is cp1252, so every accented name in the data - Montreal,
# Memphremagog, La Canada Flintridge - was decoding to mojibake, and a byte
# cp1252 has no mapping for (0x8d) raised outright. That silent corruption is
# the likely origin of keys like "CA|lacaaadaflintridge" in the gazetteer this
# replaces: a name normalised AFTER being mis-decoded. Every file here is
# written as UTF-8, so it must be read as UTF-8.
def _load(fn):
    return json.load(open(os.path.join(O,fn), encoding="utf-8"))

FEAT=_load("county_features.json")                 # US, keyed by 5-digit FIPS
for v in FEAT.values(): v["geo_system"]="US"
try:                                               # regional market catchment (population reachable ~110km, decayed)
    _USC=_load("us_catchment.json")                # fips -> catchment_pop; lets fragmented multi-county MSAs (NYC, Boston, SF) aggregate
    for _k,_v in FEAT.items():
        if _k in _USC: _v["catchment_pop"]=_USC[_k]
except FileNotFoundError:
    pass
try:                                               # ACS workforce availability + hours (County_1.csv)
    _WKF=_load("county_workforce.json")            # fips -> {mean_hours, pct_nilf, recruitable, civ_lf, unemp_n}
    for _k,_v in FEAT.items():
        _wk=_WKF.get(_k)
        if _wk: _v["mean_hours"]=_wk.get("mean_hours"); _v["pct_nilf"]=_wk.get("pct_nilf")
        if _wk: _v["recruitable"]=_wk.get("recruitable"); _v["civ_lf"]=_wk.get("civ_lf")
except FileNotFoundError:
    pass
try:                                               # generation capacity within ~60mi (power plants >=100MW)
    _PWR=_load("county_power.json")                # fips -> {power_mw, renew_mw, renew_share}
    for _k,_v in FEAT.items():
        _pw=_PWR.get(_k)
        if _pw: _v["power_mw"]=_pw.get("power_mw"); _v["renew_share"]=_pw.get("renew_share")
except FileNotFoundError:
    pass
try:                                               # drought snapshot: percent of county area NOT in drought
    _DRT=_load("county_drought.json")              # fips -> pct_not_in_drought (point-in-time; refresh periodically)
    for _k,_v in FEAT.items():
        if _k in _DRT: _v["not_in_drought"]=_DRT[_k]
except FileNotFoundError:
    pass
try:                                               # chronic groundwater depletion flag (curated worst-case counties)
    _GW=_load("county_groundwater.json")           # fips -> 1 (aquifer overdraft / depletion risk)
    for _k in _GW:
        if _k in FEAT: FEAT[_k]["gw_depleted"]=True
except FileNotFoundError:
    pass
try:                                               # USGS well trends: share of monitored wells with declining groundwater (2000-2020)
    _GWT=_load("county_groundwater_trend.json")    # fips -> {wells, pct_declining}
    for _k,_v in FEAT.items():
        _t=_GWT.get(_k)
        if _t: _v["gw_pct_declining"]=_t.get("pct_declining")
except FileNotFoundError:
    pass
try:                                               # FEMA National Risk Index: composite natural-hazard risk by county (build_us_nri.py)
    _NRI=_load("county_nri.json")                  # fips -> {risk 0-100 (higher=worse), eal_total, resilience, social_vuln, hazards{}}
    for _k,_v in FEAT.items():
        _n=_NRI.get(_k)
        if _n:
            _v["nri_hazards"]=_n.get("hazards")
            # Prefer EXPOSURE (annualized loss RATIO percentile) over the NRI composite: the composite
            # is loss-based, so it scales with existing built value and rates rural hazard-prone areas
            # (e.g. St. Helena Parish LA) as "low risk". Exposure answers the siting question -- how
            # much would a NEW facility here be at risk -- independent of what's already built.
            _v["nri_risk"]=_n.get("exposure") if _n.get("exposure") is not None else _n.get("risk")
            _v["nri_loss_risk"]=_n.get("risk")
except FileNotFoundError:
    pass
try:
    CA_FEAT=_load("ca_features.json")              # CA, keyed by 4-digit CDUID
except FileNotFoundError:
    CA_FEAT={}
try:                                               # population-weighted CD centroids (from DA centroids)
    CA_CENT=_load("ca_cd_centroids.json")          # cduid -> [lat,lon]
    for _cid,_v in CA_FEAT.items():
        _c=CA_CENT.get(_cid)
        if _c: _v["lat"],_v["lon"]=_c[0],_c[1]
except FileNotFoundError:
    pass
try:                                               # StatCan Census Profile socio-economics by CD (98-401-X2021004)
    CA_SOCIO=_load("ca_socio.json")                # cduid -> {income,dwelling_value,bachelor_share,participation,unemployment,labour_force,pop_growth}
    for _cid,_v in CA_FEAT.items():
        _s=CA_SOCIO.get(_cid)
        if _s: _v.update(_s)
except FileNotFoundError:
    pass
try:                                               # regional layers: Safety (StatCan CSI), Infrastructure (CMA=high), Livability (Numbeo cities)
    CA_REG=_load("ca_regional.json")               # cduid -> {csi, infra_pts, livability?}
    for _cid,_v in CA_FEAT.items():
        _r=CA_REG.get(_cid)
        if _r:
            _v["ca_csi"]=_r.get("csi"); _v["ca_infra_pts"]=_r.get("infra_pts")
            if _r.get("livability") is not None: _v["ca_livability"]=_r["livability"]
except FileNotFoundError:
    pass
try:                                               # curated CA census-division water-supply stress (groundwater/capacity)
    _WS=_load("ca_water_stress.json")              # cduid -> {severity 1-3, region, note, source}
    for _cid,_w in _WS.items():
        if _cid in CA_FEAT and isinstance(_w,dict) and _w.get("severity") is not None:
            CA_FEAT[_cid]["water_stress"]=_w["severity"]
except FileNotFoundError:
    pass
try:                                               # StatCan latest Crime Severity Index by CMA -> anchor CD (build_ca_crime.py)
    _CSI25=_load("ca_csi_2025.json")               # cduid -> {csi, rate, cma}; overlays ca_regional's older CSI for metro CDs
    for _cid,_c in _CSI25.items():
        if _cid in CA_FEAT and isinstance(_c,dict) and _c.get("csi") is not None:
            CA_FEAT[_cid]["ca_csi"]=_c["csi"]
except FileNotFoundError:
    pass
try:                                               # StatCan recent annual population growth by CD (build_ca_popgrowth.py)
    _PG=_load("ca_popgrowth.json")                 # cduid -> {growth_pct, from, to}; replaces stale 2016-21 census growth
    for _cid,_g in _PG.items():
        if _cid in CA_FEAT and isinstance(_g,dict) and _g.get("growth_pct") is not None:
            CA_FEAT[_cid]["pop_growth"]=_g["growth_pct"]
except FileNotFoundError:
    pass
try:                                               # CIMD-derived livability by CD (build_ca_livability.py); one consistent scale, fills the coverage gap
    _LV=_load("ca_livability.json")                # cduid -> {livability (higher=less deprived), das, pop}
    for _cid,_l in _LV.items():
        if _cid in CA_FEAT and isinstance(_l,dict) and _l.get("livability") is not None:
            CA_FEAT[_cid]["ca_livability"]=_l["livability"]
except FileNotFoundError:
    pass
try:                                               # StatCan NOC broad occupation shares by CD (build_ca_occupation.py)
    _OC=_load("ca_occupation.json")                # cduid -> {noc0..noc9 (% of employed labour force), _emp}
    for _cid,_o in _OC.items():
        if _cid in CA_FEAT and isinstance(_o,dict):
            CA_FEAT[_cid]["ca_occ"]=_o
except FileNotFoundError:
    pass
try:                                               # CURRENT Labour Force Survey rates by CD (build_ca_labour.py)
    _LAB=_load("ca_labour.json")                   # cduid -> {unemployment, participation, employment_rate, ref, basis}
    for _cid,_l in _LAB.items():
        _v=CA_FEAT.get(_cid)
        if _v and isinstance(_l,dict):
            # Replaces the 2021 Census figures, which were collected under COVID restrictions and put
            # Canadian unemployment near 9.6% against a US current-year estimate of ~4.0%.
            if _l.get("unemployment") is not None: _v["unemployment"]=_l["unemployment"]
            if _l.get("participation") is not None: _v["participation"]=_l["participation"]
            _v["labour_ref"]=_l.get("ref")
except FileNotFoundError:
    pass
try:                                               # CA province-level innovation inputs (ISED SME survey + provincial R&D credit rates)
    _CI=(_load("ca_innovation.json") or {}).get("provinces") or {}
    for _cid,_v in CA_FEAT.items():
        _r=_CI.get(_v.get("ST_ABBREV"))
        if _r:
            _v["sme_innovation_rate"]=_r.get("sme_innovation_rate")
            _v["sred_rate_pct"]=_r.get("sred_rate_pct")
except FileNotFoundError:
    pass
try:                                               # StatCan Open Database of Infrastructure asset counts by CD (build_ca_infrastructure.py)
    _INF=_load("ca_infrastructure.json")           # cduid -> {electric_grid, potable_water, wastewater, telecom, ...}
    for _cid,_i in _INF.items():
        if _cid in CA_FEAT and isinstance(_i,dict):
            CA_FEAT[_cid]["ca_assets"]=_i
except FileNotFoundError:
    pass
ALLFEAT={**FEAT,**CA_FEAT}

MASTER={r["objectid"]:r for r in _load("edo_master_table_dual.json")}
FIPS_INDEX=_load("edo_fips_index.json")            # US: fips -> [objectid]
try:
    CA_INDEX=_load("edo_ca_cd_index.json")         # CA: cduid -> [objectid]
except FileNotFoundError:
    CA_INDEX={}
INC=_load("incentives_index.json")                 # state/province -> programs
try: INFRA_GRADES={k:v for k,v in _load("state_infrastructure_grades.json").items() if not k.startswith("_")}
except FileNotFoundError: INFRA_GRADES={}
GRADE_PTS={"A+":4.3,"A":4.0,"A-":3.7,"B+":3.3,"B":3.0,"B-":2.7,"C+":2.3,"C":2.0,"C-":1.7,"D+":1.3,"D":1.0,"D-":0.7,"F":0.0}
try: PLACES=_load("us_places.json")                # "ST|normname" -> [lat,lon]
except FileNotFoundError: PLACES={}
try: MSA_MAP=_load("fips_to_msa.json")             # US county FIPS -> Metropolitan Statistical Area name
except FileNotFoundError: MSA_MAP={}
try: CMA_MAP=_load("ca_cma.json")                  # CA CDUID -> Census Metropolitan Area name (build_ca_cma.py)
except FileNotFoundError: CMA_MAP={}
try: CA_PLACES=_load("ca_places.json")             # CA "PROV|normname" and bare "normname" -> [lat,lon]
except FileNotFoundError: CA_PLACES={}
# Property orgs come LIVE from the dashboard's properties.js (single source of truth): set
# PROPERTIES_JS_URL to its public URL and the scorer re-reads it every PROPERTY_TTL_SEC seconds,
# resolving dataset labels -> EDO objectids itself. So adding/removing a property org on the
# dashboard updates the badges automatically, with no rebuild, no orgs_with_properties.json, and
# no Railway push. If the URL is unset or a fetch fails, it falls back to the bundled JSON snapshot.
import time as _time
PROPERTIES_JS_URL=os.environ.get("PROPERTIES_JS_URL","").strip()
PROPERTY_TTL=int(os.environ.get("PROPERTY_TTL_SEC","900"))
try:
    _PJ=_load("orgs_with_properties.json")         # fallback snapshot (build_property_orgs.py output)
    _PROPERTY_FALLBACK=set(str(x) for x in (_PJ.get("objectids") if isinstance(_PJ,dict) else _PJ))
except FileNotFoundError: _PROPERTY_FALLBACK=set()
PROPERTY_ORGS=_PROPERTY_FALLBACK                   # back-compat name; run() uses get_property_orgs()
_prop_cache={"orgs":None,"ts":0.0}
def _fetch_property_orgs():
    if not PROPERTIES_JS_URL: return None
    try:
        import build_property_orgs as _bpo
        req=urllib.request.Request(PROPERTIES_JS_URL,headers={"User-Agent":"FastLocations-scorer/1.0"})
        with urllib.request.urlopen(req,timeout=8) as r:
            txt=r.read().decode("utf-8","replace")
        orgs=_bpo.resolve_objectids(txt,list(MASTER.values()))
        return orgs or None                        # empty result -> treat as failure, keep fallback
    except Exception:
        return None
def get_property_orgs():
    now=_time.time()
    if _prop_cache["orgs"] is not None and (now-_prop_cache["ts"])<PROPERTY_TTL:
        return _prop_cache["orgs"]
    orgs=_fetch_property_orgs()
    if orgs is None:                               # unset URL or fetch failed
        orgs=_prop_cache["orgs"] if _prop_cache["orgs"] is not None else _PROPERTY_FALLBACK
    _prop_cache["orgs"]=orgs; _prop_cache["ts"]=now
    return orgs
try: BEA_COST=_load("county_bea.json")             # fips -> {earn_pow_pc, pcpi, pcpi_growth10}; BEA CAINC30 via build_bea_cost.py
except FileNotFoundError: BEA_COST={}
try: OCC=_load("county_occupation.json")           # fips -> occupation-group employment shares (%); Census ACS S2401 via build_occupation.py
except FileNotFoundError: OCC={}
try: INNOV=_load("county_innovation.json")         # fips -> R&D industry presence (CBP NAICS 5417) via build_us_innovation.py
except FileNotFoundError: INNOV={}
try: LANDC=_load("county_land_cost.json")          # fips -> land $/acre (USDA NASS 2022) via build_land_cost.py
except FileNotFoundError: LANDC={}
try:                                               # DAI score (0-100, higher = better) via build_county_dai.py from DAI.csv
    _DAI=_load("county_dai.json")                  # fips -> {dai, ref}; feeds the `dai` dimension
    for _k,_v in FEAT.items():
        _d=_DAI.get(_k)
        if _d and _d.get("dai") is not None: _v["dai"]=_d["dai"]
except FileNotFoundError:
    # The same numbers were shipped inside county_features.json as `critical_thinking` before the
    # DAI dimension existed. Fall back to them so a deploy without county_dai.json still scores DAI.
    for _v in FEAT.values():
        if _v.get("critical_thinking") is not None: _v["dai"]=_v["critical_thinking"]
try:                                               # fips -> StatsAmerica Innovation Intelligence subset via build_innovation_index.py
    IIX=_load("county_innovation_index.json")
    IIX_META=IIX.pop("_meta",{})                   # provenance, not a county record
except FileNotFoundError:
    IIX,IIX_META={},{}
import re as _re, unicodedata as _ud
def _norm(x):
    x=_ud.normalize("NFKD",str(x)).encode("ascii","ignore").decode().lower()
    return _re.sub(r"[^a-z0-9]","",x)
_STATE2AB={"alabama":"AL","alaska":"AK","arizona":"AZ","arkansas":"AR","california":"CA","colorado":"CO",
"connecticut":"CT","delaware":"DE","districtofcolumbia":"DC","florida":"FL","georgia":"GA","hawaii":"HI",
"idaho":"ID","illinois":"IL","indiana":"IN","iowa":"IA","kansas":"KS","kentucky":"KY","louisiana":"LA",
"maine":"ME","maryland":"MD","massachusetts":"MA","michigan":"MI","minnesota":"MN","mississippi":"MS",
"missouri":"MO","montana":"MT","nebraska":"NE","nevada":"NV","newhampshire":"NH","newjersey":"NJ",
"newmexico":"NM","newyork":"NY","northcarolina":"NC","northdakota":"ND","ohio":"OH","oklahoma":"OK",
"oregon":"OR","pennsylvania":"PA","rhodeisland":"RI","southcarolina":"SC","southdakota":"SD","tennessee":"TN",
"texas":"TX","utah":"UT","vermont":"VT","virginia":"VA","washington":"WA","westvirginia":"WV","wisconsin":"WI","wyoming":"WY"}
_CA_PROV={"ON","QC","BC","AB","MB","SK","NS","NB","NL","PE","NT","YT","NU"}
# Display names for Canadian places. ca_places.json stores only [lat,lon] (no label), so the /places
# autocomplete had nothing to show and Canada was missing from it entirely -- "Toronto, ON" suggested
# Toronto, OH. Labels are recovered from the census-division names already loaded, plus the major
# cities whose accents/punctuation can't be reconstructed from a normalised key.
CA_DISPLAY={}
for _cid,_v in CA_FEAT.items():
    _nm=_v.get("NAME")
    if _nm: CA_DISPLAY.setdefault(_norm(_nm),_nm)
for _nm in ("Toronto","Montréal","Vancouver","Calgary","Edmonton","Ottawa","Winnipeg","Québec",
            "Hamilton","Kitchener","Waterloo","Cambridge","London","Victoria","Halifax","Saskatoon",
            "Regina","St. John's","Moncton","Fredericton","Saint John","Windsor","Oshawa","Barrie",
            "Guelph","Kingston","Sudbury","Thunder Bay","Kelowna","Abbotsford","Burnaby","Richmond",
            "Surrey","Laval","Longueuil","Gatineau","Sherbrooke","Trois-Rivières","Red Deer",
            "Lethbridge","Kamloops","Nanaimo","Brampton","Mississauga","Markham","Vaughan","Burlington",
            "Milton","Ajax","Whitby","Niagara Falls","St. Catharines","Cornwall","Belleville","Peterborough"):
    CA_DISPLAY[_norm(_nm)]=_nm
# Right-to-work states (26, current 2025 -- Michigan repealed its law Feb 2024 and is NOT included).
RTW_STATES={"AL","AZ","AR","FL","GA","ID","IN","IA","KS","KY","LA","MS","NE","NV","NC","ND",
            "OK","SC","SD","TN","TX","UT","VA","WV","WI","WY"}

DIMS=["workforce","demographics","infrastructure","logistics","incentives","real_estate","cost","safety","market_size","livability","innovation","dai"]
# Rebalanced (was cost .20 / real_estate .15 / market_size .06 / logistics .08 / infrastructure .08).
# The old default put 35% on cheapness and only 14% on market access, so an unweighted search ranked
# largely by low incomes -- 8,000-person rural counties beat every real market, and expensive but
# strong regions (US west coast, Vancouver) were buried. Cheapness is now 27% and market access 20%.
# Use-type presets in intake.js still override these per project type.
# innovation promoted to a dimension of its own (was four metrics buried inside demographics, where
# the arithmetic capped its influence near 1.5% of the final score even on an R&D search). demographics
# drops to .04 because it gave up knowledge_economy and rd_intensity; the remaining .04 comes off
# real_estate, logistics, cost and market_size. Default is a deliberately modest .05 -- most projects
# are not R&D, and the r_and_d use-type preset in intake.js raises it where it matters.
# dai is the county DAI score promoted to a dimension of its own at a FIXED ~7% under every scenario
# (the use-type presets in intake.js all carry dai:7 too). It used to be the `critical_thinking`
# metric inside workforce at 2x, where its effective share of the final score drifted with the
# workforce slider and the number of workforce metrics the query happened to populate (roughly 4-6%)
# and was invisible to the user. The 7 points came off workforce (-2, which is where the metric
# left), cost, real_estate, infrastructure, incentives and logistics (-1 each).
DEFAULT_WEIGHTS={"workforce":0.16,"infrastructure":0.09,"incentives":0.09,"real_estate":0.10,
                 "demographics":0.04,"logistics":0.08,"cost":0.13,"safety":0.05,"market_size":0.09,
                 "livability":0.05,"innovation":0.05,"dai":0.07}
# Small-county reliability damping. A county's score is shrunk toward the candidate-set mean
# by reliability = pop/(pop+SCALE_DAMP_K): big labor markets keep their score, thin ones (where
# percentile metrics are noisy and the market can't realistically host most projects) are pulled
# to the middle. Prevents tiny / college-town counties from topping generic searches. Tunable.
SCALE_DAMP_K=100000
CA_MARKET_WEIGHT=0.20   # Canada weights regional market access (catchment) up; see m_market_size (CA)
HAZARD_MAX_RISK=90.0    # infrastructure.hazard="required" excludes counties with FEMA NRI exposure >= this (top-decile hazard)
# Natural-hazard preference must actually bite. As one metric inside infrastructure (8% weight) it
# moved a county's score by ~0.2 pt -- invisible. When the project opts into hazard sensitivity we
# apply a DIRECT penalty of up to this many points, scaled by the county's national hazard-exposure
# percentile, so "prefer lower-hazard areas" visibly re-ranks (same reasoning as the property bonus).
HAZARD_PENALTY=15.0
WATER_PENALTY=12.0      # same reasoning for infrastructure.drought="preferred" (was a ~0.2pt nudge)
PRIORITY_DECAY=0.6      # incentive priority ranking: weight of each pick = 0.6^position (1, .6, .36, .22)
# Depth at which a jurisdiction counts as fully serving one incentive type. Was 3,
# calibrated when incentives_index.json held 1,557 programs and the median
# jurisdiction had 26. The index now carries the Incentives Search Tool's full
# master -- 6,930 programs, median 120 per jurisdiction -- and at 3 the common
# types saturated: cash_grant, tax_credit, job_training_grant and
# low_interest_loan all hit the cap in 65 of 65 jurisdictions, so picking any of
# them scored 100 everywhere and priority_match stopped discriminating. At 10 the
# median falls to 92.7 with a 35-point spread. Raise this if the index grows again.
# Caveat worth knowing: 60 of 65 jurisdictions sit exactly at the Incentives
# Search Tool's collection ceiling (120 programs, or 90 for the jurisdictions it
# treats as small, which includes most Canadian provinces), so type depth is a
# sample rather than a census. Checked rather than assumed: normalising the
# counts to a common basis closes only 1.4 points of the 11.3-point US/Canada
# gap, so the ceiling is a minor artefact and the rest is a real difference in
# programme mix. If that ceiling ever moves a lot, re-check this constant.
PRIORITY_DEPTH=10
MAX_PER_REGION=2        # max results one state/province may take in the Top-N (0 = uncapped). Counters
                        # the fact that county granularity varies ~5x by state; see run().
# COST is percentile-ranked within MARKET-SIZE TIERS (US only), not against all 3,144 counties.
# Ranked nationally, every large metro sat in the bottom decile on cost simply because wages rise
# with market size: Seattle 1, Santa Clara 0, Phoenix 10, San Diego 5, Las Vegas 25. Cost plus real
# estate plus safety carry 28% of the default weight, and that stack cost the big western markets
# 7-11 points of weighted total -- the gap between rank 200 and the top 10. Ranking cost within
# tiers of regional catchment (the same reach measure the reliability damping uses) makes the
# sub-score mean "how expensive is this market for one of its size": each tier averages ~50, so
# no size class is systematically cheap or dear. Measured before adopting: Phoenix 10 -> 49,
# Las Vegas 25 -> 72, Riverside 22 -> 63, San Diego 5 -> 31; the losers are small Midwest counties
# that had been credited for cheapness against the whole country. It does NOT rescue the most
# expensive coastal metros (Seattle 1 -> 12, Santa Clara 0 -> 3): they are the dearest markets even
# among big ones, and that is a weighting question (the R&D preset runs cost at 5), not a ranking
# one. Canada is a single group -- its catchments are an order of magnitude smaller and the tiers
# would leave two or three CDs ranking against each other. Only cost is tiered; real estate was
# measured too and moved the same counties further for no extra fairness, so it stays national.
COST_TIERS=((0,100_000,"rural"),(100_000,500_000,"small"),(500_000,2_000_000,"mid"),(2_000_000,float("inf"),"metro"))
TIERED_DIMS={"cost"}    # dimensions whose metrics are percentile-ranked within market_tier() (US only)
def market_tier(f):
    """Market-size tier of a US county by regional catchment (own population if no catchment).
    Canadian CDs all return one tier, so tiering is a no-op there."""
    if gsys(f)=="CA": return "CA"
    v=f.get("catchment_pop") or f.get("TOTPOP_CY") or 0
    return next(name for lo,hi,name in COST_TIERS if lo<=v<hi)
def water_risk(d):
    """0-100 water-supply risk (higher = worse), US and CA, for the drought preference penalty."""
    if gsys(d)=="CA":
        ws=d.get("water_stress")
        return None if ws is None else min(100.0,ws*33.3)
    parts=[]
    nid=d.get("not_in_drought")
    if nid is not None: parts.append(100.0-nid)          # share of county area in drought
    pd=d.get("gw_pct_declining")
    if pd is not None: parts.append(pd)                  # share of wells declining
    elif d.get("gw_depleted"): parts.append(100.0)
    return sum(parts)/len(parts) if parts else None
# Coverage bonus: over-index jurisdictions served by a LOCAL or REGIONAL EDO customer (a specific
# org to route the lead to) over those covered only by a broad State/Provincial agency. Added to
# the final score from the most-specific serving EDO's category. US + Canada (CA orgs are all
# provincial today, so they score 0 until local/regional Canadian EDOs are added).
COVERAGE_BONUS={"Local Development Agency":10,"Chamber of Commerce":10,"Megasite":10,
                "Industrial Park":10,"Port/Airport Authority":9,"Utility":9,
                "Regional Development Agency":7,"State Agency":0}
# Property-access bonus: a county whose serving EDO customer has properties listed on the
# FastLocations dashboard (see orgs_with_properties.json, regenerated from properties/properties.js)
# earns a lift for demonstrable, ready site availability. Applied to score HEADROOM like the other
# bonuses, so it only meaningfully elevates already-close matches -- "good access to available
# properties" -- and never rescues a poor fit.
PROPERTY_BONUS=5.0
# ...but SCALED BY SPECIFICITY. Listings held by a statewide agency blanket every county in that
# state, so they say nothing about which county is better -- without this, one statewide customer
# (e.g. VEDP, 133 VA county-equivalents) lifts an entire state and swamps the rankings. A single-
# county EDO's listings are a real, local signal; a 100-county agency's are nearly noise.
# factor = 1/(1 + territory_counties/K); the most specific property-holding EDO wins.
PROPERTY_SCOPE_K=25.0
# An EDO flagged '..._INCOMPLETE' carries territory_county_count=1 because its real territory was
# never resolved -- NOT because it serves one county. Treating that 1 as precision would hand a
# multi-state utility the maximum single-county specificity credit. So an unresolved territory is
# scored at a typical size for its KIND: utility service areas are large, regional partnerships are
# ~10 counties. (Utilities resolved from EIA-861 carry real counts and skip this entirely.)
INCOMPLETE_TERRITORY_ASSUMED=25.0
INCOMPLETE_ASSUMED_BY_CATEGORY={"Utility":40.0,"Regional Development Agency":10.0}
def property_scope_factor(edo):
    n=edo.get("territory_county_count") or 1
    if str(edo.get("territory_basis") or "").endswith("INCOMPLETE"):
        n=max(n,INCOMPLETE_ASSUMED_BY_CATEGORY.get(edo.get("category"),INCOMPLETE_TERRITORY_ASSUMED))
    return 1.0/(1.0+(n/PROPERTY_SCOPE_K))

def gsys(f): return f.get("geo_system","US")
def index_for(g): return FIPS_INDEX if g=="US" else CA_INDEX

_GEO_CACHE={}
def geocode_place(place):
    place=(place or "").strip()
    if not place: return None
    if place in _GEO_CACHE: return _GEO_CACHE[place]
    res=None
    parts=[p.strip() for p in place.split(",")]
    cityn=_norm(parts[0]); prov=parts[-1].strip().upper() if len(parts)>=2 else None
    if prov in _CA_PROV and CA_PLACES:          # Canadian "City, PROV" -> bundled CA geocoder (Nominatim blocked on host)
        res=CA_PLACES.get(prov+"|"+cityn) or CA_PLACES.get(cityn)
    if not res and len(parts)>=2:               # US "City, ST"
        st=prov if (prov and len(prov)==2 and prov not in _CA_PROV) else _STATE2AB.get(_norm(parts[-1]))
        if st: res=PLACES.get(st+"|"+cityn)
    if not res and prov not in _CA_PROV:        # US city-only loose match (skip for Canadian queries)
        nm="|"+cityn
        for k,v in PLACES.items():
            if k.endswith(nm): res=v; break
    if not res and CA_PLACES:                    # Canadian bare-name fallback
        res=CA_PLACES.get(cityn)
    if not res:                                 # last resort: external geocoder (may fail on cloud hosts)
        try:
            url="https://nominatim.openstreetmap.org/search?"+urllib.parse.urlencode({"q":place,"format":"json","limit":1})
            req=urllib.request.Request(url,headers={"User-Agent":"FastLocations/1.0 (site-selection)"})
            with urllib.request.urlopen(req,timeout=8) as r:
                data=json.load(r)
            if data: res=[float(data[0]["lat"]),float(data[0]["lon"])]
        except Exception: res=None
    res=tuple(res[:2]) if res else None
    _GEO_CACHE[place]=res; return res

def haversine_mi(lat1,lon1,lat2,lon2):
    R=3958.8
    p1=math.radians(lat1); p2=math.radians(lat2)
    dlat=math.radians(lat2-lat1); dlon=math.radians(lon2-lon1)
    h=math.sin(dlat/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dlon/2)**2
    return 2*R*math.asin(math.sqrt(h))

# Canadian regional market access: population reachable within ~250 km, distance-decayed
# (scale 75 km). Lets CDs in dense corridors (around Toronto, Vancouver, Montreal, Calgary)
# index higher on market size than their own population alone -- the metro's labor and customer
# base is within reach. US market_size stays own-county population (its county fabric is finer).
_CA_GEO=[v for v in CA_FEAT.values() if v.get("lat") is not None and v.get("TOTPOP_CY") is not None]
for _v in _CA_GEO:
    _la,_lo=_v["lat"],_v["lon"]; _acc=0.0
    for _v2 in _CA_GEO:
        _km=haversine_mi(_la,_lo,_v2["lat"],_v2["lon"])*1.60934
        if _km<=250: _acc+=_v2["TOTPOP_CY"]*math.exp(-_km/75.0)
    _v["catchment_pop"]=round(_acc)

def pct_rank(values):
    pairs=[(k,v) for k,v in values.items() if v is not None]
    if len(pairs)<2: return {k:(50.0 if v is not None else None) for k,v in values.items()}
    s=sorted(pairs,key=lambda x:x[1]); n=len(s)
    # AVERAGE-RANK ties: every candidate sharing a value gets the same percentile (the mean of the
    # positions it spans), so state-broadcast dimensions (infrastructure, incentives) and other
    # low-cardinality metrics don't get arbitrary within-tie spread from sort order.
    rank={}; i=0
    while i<n:
        j=i
        while j+1<n and s[j+1][1]==s[i][1]: j+=1
        p=((i+j)/2.0)/(n-1)*100
        for t in range(i,j+1): rank[s[t][0]]=p
        i=j+1
    return {k:(round(rank[k],1) if v is not None else None) for k,v in values.items()}
def avg(xs):
    xs=[x for x in xs if x is not None]; return round(sum(xs)/len(xs),1) if xs else None

# ---- raw metric extractors (higher = better) ----
# NOTE: "hs" previously listed the SAME five bands as "none" (just reordered), so edu_share summed to
# an identical value and the "High-school+" option was mathematically inert. It now means the
# non-degree workforce (high school + some college) -- a real, distinct preference for operations that
# want a trades/production labour pool rather than a degree-heavy one. UI label updated to match.
EDU={"none":["BACHDEG_CY","GRADDEG_CY","ASSCDEG_CY","SMCOLL_CY","HSGRAD_CY"],
     "hs":["HSGRAD_CY","SMCOLL_CY"],
     "some_college":["SMCOLL_CY","ASSCDEG_CY","BACHDEG_CY","GRADDEG_CY"],
     "bachelors_plus":["BACHDEG_CY","GRADDEG_CY"]}
def edu_share(f,priority):
    base=f.get("EDUCBASECY") or 0
    if not base: return None
    s=sum((f.get(k) or 0) for k in EDU.get(priority,EDU["none"]))
    return s/base*100

# Skill profile -> the county educational-attainment bands that actually supply that kind of worker.
# We DON'T have county occupation (BLS OES / SOC) counts in the loaded data, so skill availability is
# proxied honestly by attainment share, not invented. Each profile lists the ESRI attainment keys whose
# summed share (over EDUCBASECY) is that profile's local labor pool. Counties rank very differently on
# HS-share vs. bachelor-share, so selecting different profiles genuinely re-ranks the workforce dimension.
# `cog` (0..1) marks how degree/cognition-heavy the role is; used to give a coarse Canadian signal
# (bachelor_share) for high-skill profiles, where StatCan gives no trades-level breakdown.
SKILL_BANDS={
    "general_labor":      (["HSGRAD_CY","SMCOLL_CY"],                   0.0),
    "assembly_production":(["HSGRAD_CY","SMCOLL_CY"],                   0.0),
    "logistics_warehouse":(["HSGRAD_CY","SMCOLL_CY"],                   0.0),
    "machine_operators":  (["HSGRAD_CY","SMCOLL_CY","ASSCDEG_CY"],      0.2),
    "customer_service":   (["HSGRAD_CY","SMCOLL_CY","ASSCDEG_CY"],      0.2),
    "skilled_trades":     (["SMCOLL_CY","ASSCDEG_CY"],                  0.3),
    "technicians":        (["SMCOLL_CY","ASSCDEG_CY","BACHDEG_CY"],     0.5),
    "professional":       (["SMCOLL_CY","ASSCDEG_CY","BACHDEG_CY"],     0.5),
    "healthcare":         (["ASSCDEG_CY","BACHDEG_CY","GRADDEG_CY"],    0.7),
    "management":         (["BACHDEG_CY","GRADDEG_CY"],                 0.7),
    "finance_accounting": (["BACHDEG_CY","GRADDEG_CY"],                 0.8),
    "engineers":          (["BACHDEG_CY","GRADDEG_CY"],                 1.0),
    "it_software":        (["BACHDEG_CY","GRADDEG_CY"],                 1.0),
    "scientists_rd":      (["GRADDEG_CY","BACHDEG_CY"],                 1.0),
}
# Skill profile -> Census ACS S2401 occupation groups that actually DO that work (see build_occupation.py).
# Real occupational supply, not an attainment stand-in: selecting "engineers" reads the county's
# architecture/engineering employment share, "assembly_production" reads production, etc.
PROFILE_OCC={
    "general_labor":      ["prod","material"],
    "assembly_production":["prod"],
    "logistics_warehouse":["transport","material"],
    "machine_operators":  ["prod"],
    "customer_service":   ["sales","office"],
    "skilled_trades":     ["construct","maint"],
    "technicians":        ["maint","hlthtech"],
    "professional":       ["edary","busfin"],
    "healthcare":         ["hlthpr","hlthsup"],
    "management":         ["mgmt"],
    "finance_accounting": ["busfin"],
    "engineers":          ["eng"],
    "it_software":        ["comp"],
    "scientists_rd":      ["sci"],
}
def _attainment_supply(f,profiles):
    """Fallback: mean attainment-band share across selected profiles (used where occupation data
    is absent, e.g. Canada or an unmatched county)."""
    base=f.get("EDUCBASECY") or 0
    if not base or not profiles: return None
    shares=[]
    for p in profiles:
        spec=SKILL_BANDS.get(p)
        if not spec: continue
        s=sum((f.get(k) or 0) for k in spec[0])
        shares.append(s/base*100)
    return round(sum(shares)/len(shares),3) if shares else None
def skill_supply(f,profiles):
    """Occupation-group employment share of the selected profiles (Census S2401), averaged across
    profiles. Falls back to educational-attainment share when the county has no occupation record.
    None when no profile chosen -> dimension omits it (never penalized)."""
    if not profiles: return None
    occ=OCC.get(f.get("fips") or "")
    if occ:
        shares=[]
        for p in profiles:
            groups=PROFILE_OCC.get(p)
            if not groups: continue
            shares.append(sum((occ.get(g) or 0.0) for g in groups))   # % of employed in those occupations
        if shares: return round(sum(shares)/len(shares),3)
    return _attainment_supply(f,profiles)
def skill_cognition(profiles):
    """Mean cognition weight of the selected profiles (0..1); drives the coarse Canadian signal."""
    cs=[SKILL_BANDS[p][1] for p in (profiles or []) if p in SKILL_BANDS]
    return sum(cs)/len(cs) if cs else None
def knowledge_economy(f):
    """Innovation capacity of the local workforce, measured where the work actually happens.

    Built from the county/census-division OCCUPATION data already loaded, so it is fine-grained on
    both sides of the border (3,222 US counties; 293 Canadian CDs) rather than the state- or
    region-level innovation composites that are usually published. US: computer/mathematical,
    architecture/engineering and life/physical/social science employment shares. Canada: the natural
    and applied sciences NOC group, nudged by the province's SME innovation rate (ISED, region-level,
    so it is deliberately a minor term -- it cannot differentiate CDs within a province)."""
    if gsys(f)=="CA":
        occ=f.get("ca_occ") or {}
        stem=occ.get("noc2")
        if stem is None: return None
        rate=f.get("sme_innovation_rate")
        return stem if rate is None else stem*0.8+(rate/10.0)*0.2
    o=OCC.get(f.get("fips") or "")
    if not o: return None
    parts=[o.get(k) for k in ("comp","eng","sci") if o.get(k) is not None]
    return round(sum(parts),3) if parts else None
def rd_intensity(f):
    """Share of the county's establishments that are Scientific R&D Services (CBP NAICS 5417).

    Deliberately separate from knowledge_economy(): that one counts technical WORKERS who live in a
    county, this one counts whether R&D is performed there as a line of business. A commuter suburb
    of engineers and a county hosting a research campus diverge sharply on this. Kept as its own
    metric key rather than folded into knowledge_economy so each is percentile-ranked on its own
    distribution -- the two are on different scales and a weighted sum of the raw values would let
    occupation shares swamp this entirely.

    Roughly 2,600 of 3,246 counties record a genuine zero here, so the bottom of the distribution is
    one large tie block; that is a real finding about where US R&D is performed, not a coverage hole.
    Counties with no CBP data at all are absent from the file and return None."""
    r=INNOV.get(f.get("fips") or "")
    return r.get("rd_estab_share") if r else None
def _iix(f,key):
    """Read a pre-composed StatsAmerica theme. build_innovation_index.py percentile-ranks each raw
    measure and averages within theme, because the source measures are in incompatible units
    (percentages, ratios, dollars scaled by GDP) and cannot be averaged directly."""
    r=IIX.get(f.get("fips") or "")
    v=r.get(key) if r else None
    return v if isinstance(v,(int,float)) else None
def innovation_output(f):
    """Knowledge CREATION: patent technology diffusion, patenting rate change, patent diversity,
    university knowledge spillovers.

    Distinct from both knowledge_economy (technical workers resident) and rd_intensity (R&D firms
    present) -- this is measured output and institutional spillover, not headcount or firm counts.
    Sourced from StatsAmerica because there is no longer a reachable county-level patent feed:
    USPTO's own county tables stop at CY2015 and PatentsView's bulk files are now key-gated."""
    return _iix(f,"innovation_output")
def business_dynamism(f):
    """Entrepreneurial and capital conditions: establishment births, deaths, expansions and
    contractions; venture capital dollars and deal counts; IPOs; foreign and domestic direct
    investment; latent innovation, industry diversity and cluster performance."""
    return _iix(f,"business_dynamism")
def broadband(f):
    """Broadband infrastructure and adoption, net of adoption barriers. The engine had NO broadband
    signal before this -- it is a genuine gap being filled, not a refinement of something existing,
    which is why it sits in infrastructure rather than alongside the innovation composites."""
    return _iix(f,"broadband")
def m_demographics(f,crit):
    # Composition / quality only. Raw population scale is carried by market_size, and absolute
    # labor force mirrors it (r~0.89), so demographics uses RATES/quality to de-correlate:
    # educational attainment, population growth, and labor-force participation.
    if gsys(f)=="CA":                              # StatCan: bachelor's share, recent growth, participation rate
        if f.get("bachelor_share") is None and f.get("pop_growth") is None: return None
        return {"education_attainment":f.get("bachelor_share"),
                "pop_growth":f.get("pop_growth"),
                "labor_force_participation":f.get("participation")}
    pr=(crit.get("demographics") or {}).get("education_priority","none")
    pop=f.get("TOTPOP_CY"); lf=f.get("CIVLBFR_CY")
    out={"education_attainment":edu_share(f,pr),   # weighted 2x via METRIC_WEIGHTS (lifts mature, highly-educated NE/east coast)
         "pop_growth":f.get("POPGRW20CY"),
         "labor_force_participation":(lf/pop if (lf is not None and pop) else None)}
    return out

def m_innovation(f,crit):
    """Innovation capacity, as its own dimension rather than a corner of demographics.

    Four complementary views, each answering a different question, so a place that merely looks
    innovative on one cannot carry the dimension:
      knowledge_economy  - do technical people WORK here (occupation shares; both countries)
      rd_intensity       - are R&D firms PRESENT here (CBP NAICS 5417 establishment share; US)
      innovation_output  - is knowledge being CREATED here (patents, university spillovers; US)
      business_dynamism  - can a venture START and FUND itself here (establishment churn, VC, FDI; US)

    Canada currently has only the first of these: StatsAmerica and County Business Patterns are both
    US-only. Because every dimension is percentile-ranked within its own country, Canadian CDs are
    ranked against each other on the signal that does exist rather than being penalised for the three
    that do not -- but the Canadian innovation sub-score rests on one metric and should be read as the
    coarser number it is. A Canadian equivalent (StatCan business dynamics / CIPO patents by CD) is the
    obvious next build."""
    out={}
    ke=knowledge_economy(f)
    if ke is not None: out["knowledge_economy"]=ke
    rd=rd_intensity(f)
    if rd is not None: out["rd_intensity"]=rd
    io_=innovation_output(f)
    if io_ is not None: out["innovation_output"]=io_
    bd=business_dynamism(f)
    if bd is not None: out["business_dynamism"]=bd
    return out or None
# Skill profile -> NOC 2021 broad categories (Canada; from build_ca_occupation.py). Coarser than the
# US S2401 groups (10 vs 16) but REAL occupational supply, replacing the degree-share proxy for CA so
# "engineers" reads a CD's Natural-&-applied-sciences share, "skilled_trades" reads Trades, etc.
PROFILE_NOC={
    "engineers":["noc2"],"it_software":["noc2"],"scientists_rd":["noc2"],
    "healthcare":["noc3"],"management":["noc0"],"finance_accounting":["noc1"],
    "professional":["noc4"],"skilled_trades":["noc7"],"technicians":["noc7","noc2"],
    "machine_operators":["noc9"],"assembly_production":["noc9"],
    "general_labor":["noc9","noc8"],"logistics_warehouse":["noc7"],"customer_service":["noc6"],
}
def ca_occ_supply(occ,profiles):
    """Mean NOC-broad employment share of the selected profiles (Canada). None if no occ record."""
    if not occ or not profiles: return None
    shares=[]
    for p in profiles:
        groups=PROFILE_NOC.get(p)
        if not groups: continue
        shares.append(sum((occ.get(g) or 0.0) for g in groups))
    return round(sum(shares)/len(shares),3) if shares else None
def m_workforce(f,crit):
    wfc=(crit.get("workforce") or {})
    need=(wfc.get("headcount") or {}).get("initial")
    shift=wfc.get("shift_pattern")
    profiles=wfc.get("skill_profile") or []
    if gsys(f)=="CA":                              # StatCan: unemployment (availability) + staffability
        une=f.get("unemployment"); lf=f.get("labour_force"); pop=f.get("TOTPOP_CY")
        staff=None
        if need and need>0:
            parts=[]
            if lf  is not None: parts.append((lf /need)/50.0)
            if pop is not None: parts.append((pop/need)/100.0)
            if parts: staff=min(min(parts),1.0)
        # Real NOC-broad occupation supply (StatCan 98-10-0471) when available -- differentiates ALL
        # profiles incl. trades/labor. Falls back to the bachelor_share proxy (degree-heavy only) for
        # any CD without an occupation record.
        occ=f.get("ca_occ")
        if occ:
            ca_skill=ca_occ_supply(occ,profiles)
        else:
            cog=skill_cognition(profiles)
            ca_skill=f.get("bachelor_share") if (cog is not None and cog>=0.5) else None
        return {"labor_availability":(-une if une is not None else None),"staffability":staff,
                "skill_supply":ca_skill}
    une=f.get("UNEMPRT_CY"); lf=f.get("CIVLBFR_CY"); pop=f.get("TOTPOP_CY")
    recruit=f.get("recruitable"); civlf=f.get("civ_lf")
    # Staffability: can this market realistically supply the start-up headcount?
    # Factors BOTH total population depth and labor-force adequacy vs. the need,
    # each as a sufficiency ratio capped at 1.0 -- beyond a comfortable supply,
    # extra size stops adding score (that is what market_size measures). `need`
    # is constant across candidates, so an UNcapped ratio would just re-rank by raw
    # size; the cap is what makes this a feasibility test, not a size proxy. When no
    # headcount is given, this is null so raw size never leaks into workforce.
    staff=None
    if need and need>0:
        parts=[]
        if lf      is not None: parts.append((lf     /need)/50.0)   # labor force ~50x need = ample supply
        if pop     is not None: parts.append((pop    /need)/100.0)  # population  ~100x need = ample draw
        if recruit is not None: parts.append((recruit/need)/30.0)   # AVAILABLE pool (unemployed + latent) ~30x need = ample to hire
        if parts: staff=min(min(parts),1.0)                          # binding constraint, capped at "ample"
    # employee availability: recruitable slack (unemployed + a share of not-in-labor-force) relative
    # to the existing workforce -- how much headroom there is to hire without poaching.
    avail=(recruit/civlf if (recruit is not None and civlf) else None)
    # shift fit: mean usual hours worked -- only counts when the project runs multiple / continuous
    # shifts (a full-time, shift-accustomed workforce), otherwise it drops out.
    shift_fit=f.get("mean_hours") if shift in ("two","three","continuous") else None
    return {"labor_availability":(-une if une is not None else None),
            "staffability":staff,
            "employee_availability":avail,
            "shift_fit":shift_fit,
            "prime_workage_share":(f["WORKAGE_CY"]/f["TOTPOP_CY"] if (f.get("WORKAGE_CY") is not None and f.get("TOTPOP_CY")) else None),
            # critical_thinking (the DAI score) left this dimension: it is scored on its own as `dai`
            # (m_dai) so it cannot be double-counted here.
            # supply of the SELECTED skill profiles (attainment-band share). Null unless the intake
            # picks at least one profile, so blank searches score exactly as before.
            "skill_supply":skill_supply(f,profiles)}
def m_logistics(f,crit):
    if gsys(f)=="CA":
        inf=f.get("infra_ca")
        if not inf: return None
        return {"ca_airports":(inf.get("airports") or None),
                "ca_ports":(inf.get("ports") or None),
                "ca_grid":(inf.get("grid_nodes") or None)}
    # COVERAGE FIX: this used to `return None` whenever `infra` was absent, which dropped logistics
    # for 82% of US counties -- even though commute time is known for 3135/3143. Because a county only
    # HAS an `infra` record when an airport/port was actually found near it, absence is a genuine zero
    # (no air/port access), not a data gap. Scoring it as 0 stops data-poor counties from silently
    # skipping the single heaviest factor in a distribution search.
    inf=f.get("infra") or {}
    ap=inf.get("airports") or {}; pt=inf.get("port") or {}
    cm=f.get("commute_min")
    out={"air_capacity":float(ap.get("large",0)*3+ap.get("medium",0)*2+ap.get("small",0)),
         "air_enplanements":float(ap.get("enplanements") or 0),
         "port_tonnage":float(pt.get("max_tonnage") or 0)}
    if cm is not None: out["short_commute"]=-cm
    return out
def m_incentives(f,crit):
    rec=INC.get(f.get("ST_ABBREV"))
    if not rec: return None
    tc=rec.get("type_counts") or {}
    prio=(crit.get("incentives") or {}).get("priorities") or []
    pf=None
    if prio:
        # Depth-weighted by the user's RANKING. The old linear (n-i) weights made the top pick only
        # 3x the last on a 3-item list, and with priority_match at 1/4 of the dimension the ordering
        # was effectively cosmetic (reversing a list moved the total ~0.03). Geometric decay makes the
        # first pick decisively dominant; see METRIC_WEIGHTS for the matching weight increase.
        wts=[PRIORITY_DECAY**i for i in range(len(prio))]
        tot=sum(wts)
        got=sum(wts[i]*min(tc.get(t,0),PRIORITY_DEPTH)/float(PRIORITY_DEPTH)
                for i,t in enumerate(prio))
        pf=got/tot*100 if tot else None
    # QUALITY, not quantity: raw program count is intentionally excluded. Score reflects the value
    # tier (largest program $ advertised, weighted double), the range of incentive types offered,
    # and how well those types match the project's ranked priorities.
    out={"incentive_value":rec.get("value_tier"),   # weighted 2x via METRIC_WEIGHTS
         "incentive_diversity":rec.get("type_diversity"),
         "priority_match":pf}
    # TARGET INCENTIVE VALUE (previously ignored): score how well the largest available program covers
    # the project's stated target, capped at 1.0 so exceeding the ask isn't rewarded without limit.
    tv=(crit.get("incentives") or {}).get("min_value_target_usd")
    mx=rec.get("max_value_usd")
    try: tv=float(tv) if tv is not None else None
    except (TypeError,ValueError): tv=None
    if tv and tv>0 and mx is not None:
        out["value_target_fit"]=min(float(mx)/tv,1.0)*100.0
    # Canadian provincial R&D (SR&ED) credit rate -- only counts when the project actually ranks R&D
    # credits as a priority, since it is irrelevant to a project that isn't doing R&D. The 35% federal
    # credit applies nationwide and so cannot differentiate provinces; only the provincial top-up does.
    if "rd_credit" in prio:
        sr=f.get("sred_rate_pct")
        if sr is not None: out["rd_credit_rate"]=float(sr)
    return out

def m_livability(f,crit):
    if gsys(f)=="CA":                              # CIMD deprivation composite (higher = less deprived = more livable)
        lv=f.get("ca_livability")
        return {"low_deprivation":lv} if lv is not None else None
    pd=f.get("premature_death"); pf=f.get("poor_fair_health")
    pp=f.get("poor_phys_days"); pm=f.get("poor_mental_days"); le=f.get("life_expectancy")
    if all(x is None for x in (pd,pf,pp,pm,le)): return None
    return {"long_life":(-pd if pd is not None else None),
            "good_health":(-pf if pf is not None else None),
            "few_phys_unhealthy_days":(-pp if pp is not None else None),
            "few_mental_unhealthy_days":(-pm if pm is not None else None),
            "life_expectancy":le}

def m_dai(f,crit):
    """County DAI score (0-100, higher = better), US only. Single-metric dimension so its share of the
    final score is exactly its weight. Canada has no DAI, so it returns None there and the other
    weights renormalise over the dimensions that do have data (a gap, never a penalty)."""
    if gsys(f)=="CA": return None
    v=f.get("dai")
    return {"dai_score":v} if v is not None else None

def m_market_size(f,crit):
    cp=f.get("catchment_pop")                       # regional catchment (metro access) for US and CA
    if cp is not None: return {"population_scale":cp}
    pop=f.get("TOTPOP_CY")
    if pop is None: return None
    return {"population_scale":pop}

def m_infrastructure(f,crit):
    ci=(crit.get("infrastructure") or {})
    if gsys(f)=="CA":
        out={}
        p=f.get("ca_infra_pts")                    # legacy CMA proxy (CMA=high, else mid) -- kept as a coarse baseline
        if p is not None: out["infra_grade"]=p
        # Asset inventory from StatCan ODI (build_ca_infrastructure.py). ONLY the layers that are
        # genuine NATIONAL inventories are scored. ODI mixes federal datasets with voluntary municipal
        # submissions, so several layers measure "who uploaded their GIS" rather than what exists:
        #   electric_grid  -> 70.7% Alberta, 0.9% Ontario (Alberta open-data artifact)  EXCLUDED
        #   potable_water  -> present in only 43 of 293 CDs; Calgary alone = 408k assets EXCLUDED
        #   wastewater     -> 51% BC / 1.4% AB, municipal submission bias                EXCLUDED
        #   oil_gas        -> Alberta Energy Regulator wells; resource extraction, not infra EXCLUDED
        # airports/ports are excluded too -- m_logistics already scores them (no double-weighting).
        # Counts are log-damped: presence and depth matter, but 2,000 bridges isn't 1,000x better than 2.
        a=f.get("ca_assets")
        if a:
            def _lg(n): return math.log10(1.0+(n or 0))
            comm=_lg(a.get("telecom"))                 # NRCan national inventory (292/293 CDs)
            waste=_lg(a.get("solid_waste"))            # NRCan-led, provincially balanced
            road=_lg(a.get("bridges_tunnels"))         # all 293 CDs; mildly AB-skewed but broad
            if comm:  out["telecom_assets"]=comm
            if waste: out["waste_infrastructure"]=waste
            if road:  out["road_structures"]=road
        if ci.get("renewable"):                    # opt-in ESG: local low-carbon generation presence (CA analogue of US renew_share)
            lc=(a or {}).get("low_carbon")
            if lc is not None: out["renewable_share"]=math.log10(1.0+lc)
        if ci.get("drought"):                      # opt-in water-supply security (curated CD groundwater/capacity stress; higher=safer)
            ws=f.get("water_stress")
            out["water_security"]=100.0 if not ws else max(0.0,100.0-ws*33.3)
        return out or None
    # US: blend the ASCE STATE grade with LOCAL power-generation capacity (plants >=100MW within
    # ~60mi) so infrastructure is county-specific, not just a flat state value. If the project
    # flags renewable/ESG power as a priority, the local renewable share also counts.
    g=INFRA_GRADES.get(f.get("ST_ABBREV")); pts=GRADE_PTS.get(g) if g else None
    out={}
    if pts is not None: out["infra_grade"]=pts
    if f.get("power_mw") is not None: out["power_capacity"]=f["power_mw"]
    if ci.get("renewable") and f.get("renew_share") is not None:
        out["renewable_share"]=f["renew_share"]
    # water availability only counts when the project flags drought sensitivity (opt-in): the drought
    # feed is a point-in-time snapshot, so it never distorts a search that didn't ask for it. When
    # opted in, we score BOTH current surface drought and chronic groundwater (aquifer) depletion.
    if ci.get("drought"):
        if f.get("not_in_drought") is not None: out["water_resilience"]=f["not_in_drought"]
        pd=f.get("gw_pct_declining")               # graded: fewer wells declining = healthier aquifer
        if pd is not None: out["groundwater_health"]=100.0-pd
        elif f.get("gw_depleted"): out["groundwater_health"]=0.0   # curated hotspot fallback where no wells
    # natural-hazard resilience (FEMA NRI), opt-in via infrastructure.hazard: higher = safer (lower risk).
    if ci.get("hazard") and f.get("nri_risk") is not None:
        out["hazard_resilience"]=100.0-f["nri_risk"]
    bb=broadband(f)                                # US only; Canadian CDs simply omit the key
    if bb is not None: out["broadband"]=bb
    return out or None

def target_wage_annual(crit):
    """Project's target wage normalised to annual $ (form emits {amount, basis: hourly|annual})."""
    tw=(crit.get("workforce") or {}).get("target_wage") or {}
    if not isinstance(tw,dict): return None
    amt=tw.get("amount") if tw.get("amount") is not None else tw.get("value")
    try: amt=float(amt)
    except (TypeError,ValueError): return None
    if amt<=0: return None
    return amt*2080.0 if str(tw.get("basis","hourly")).lower().startswith("hour") else amt
def m_cost(f,crit):
    tgt=target_wage_annual(crit)
    if gsys(f)=="CA":                              # StatCan: median household income as the labor-cost proxy
        inc=f.get("income")
        if inc is None: return None
        out={"low_labor_cost":-inc}
        if tgt: out["wage_headroom"]=tgt-inc        # local pay below the budgeted wage = easier/cheaper to hire
        return out
    inc=f.get("MEDHINC_CY"); wl=f.get("WLTHINDXCY")
    bea=BEA_COST.get(f.get("fips") or "") or {}
    epw=bea.get("earn_pow_pc")                     # BEA earnings by place of work per capita = employer wage level
    if inc is None and wl is None and epw is None: return None
    out={"low_labor_cost":(-inc if inc is not None else None),        # household income (residence)
         "low_employer_wages":(-epw if epw is not None else None),    # BEA place-of-work earnings (~0.17 corr w/ MEDHINC)
         "low_cost_of_living":(-wl if wl is not None else None)}
    # TARGET WAGE (previously ignored): score the gap between the project's budgeted wage and the
    # local prevailing wage. Positive headroom = the market pays less than budget, so the project can
    # staff comfortably; negative = it is bidding under market and will struggle.
    if tgt:
        local=inc if inc is not None else (epw*2.0 if epw is not None else None)
        if local is not None: out["wage_headroom"]=tgt-local
    return out

def m_safety(f,crit):
    if gsys(f)=="CA":                              # StatCan Crime Severity Index (CMA-precise, else province); lower=safer
        cs=f.get("ca_csi")
        return {"low_crime":-cs} if cs is not None else None
    cr=f.get("crime_rate")
    if cr is None: return None
    return {"low_crime":-cr}

def m_real_estate(f,crit):
    if gsys(f)=="CA":                              # StatCan: median dwelling value as the real-estate-cost proxy
        dv=f.get("dwelling_value")
        return {"low_dwelling_cost":-dv} if dv is not None else None
    # Land purchase price carries 2x property tax via METRIC_WEIGHTS: the acquisition cost is a larger
    # real number for most projects than the annual rate, and scoring tax alone had been penalising
    # high-tax/cheap-land regions (Upstate NY) while flattering low-tax/dear-land ones (Prop 13
    # California). Either metric alone still scores if the other is missing.
    out={}
    lc=LANDC.get(f.get("fips") or "")
    if lc and lc.get("land_per_acre"): out["low_land_cost"]=-lc["land_per_acre"]
    pt=f.get("property_tax_rate")
    if pt is not None: out["low_property_tax"]=-pt
    return out or None

# Per-metric weights WITHIN a dimension (default 1). Lets a metric count for more without the old
# duplicate-key hack: education dominates demographics; value tier counts 2x.
# priority_match is the ONLY incentives metric that responds to what the user actually asked for, so
# it carries more weight than the generic value tier -- otherwise the ranked-priority UI is cosmetic.
METRIC_WEIGHTS={"education_attainment":2.0,"incentive_value":2.0,
                "skill_supply":1.5,"priority_match":3.0,
                # Within the innovation dimension. rd_intensity stays below its peers because roughly
                # 81% of US counties tie at zero R&D establishments, making it closer to a presence
                # flag than a gradient; the other three are continuous and carry equal weight.
                "rd_intensity":0.75,"innovation_output":1.0,"business_dynamism":1.0,
                # Acquisition cost outweighs the annual tax rate in the real-estate dimension.
                "low_land_cost":2.0}
def _wavg(pairs):   # pairs = list of (percentile_or_None, weight)
    num=den=0.0
    for v,wt in pairs:
        if v is not None: num+=v*wt; den+=wt
    return round(num/den,1) if den else None
def score_dimension(cands,extract,crit,tiered=False):
    raws={ff:extract(ALLFEAT[ff],crit) for ff in cands}
    keys=set()
    for r in raws.values():
        if r: keys|=set(r.keys())
    if not keys: return {ff:None for ff in cands}
    # Percentile-rank WITHIN EACH COUNTRY, never across both. Several metric keys are shared by the US
    # and Canadian extractors but come from sources that are not comparable on one scale:
    #   labor_availability -> US unemployment comes from the ACS/current-year model, Canada's from the
    #                         Labour Force Survey (3-month moving average, seasonally adjusted). Two
    #                         different instruments with different reference weeks and definitions of
    #                         "actively seeking"; their raw rates are not one comparable scale.
    #   low_labor_cost     -> US median household income is USD, Canada's is CAD (76,958 vs 60,420),
    #                         so Canadian markets read as systematically expensive.
    # Ranking each country against its own distribution makes a score mean "this place's standing in
    # its own country", which is comparable across the border; raw units no longer have to be.
    # tiered=True (the cost dimension, see COST_TIERS) further splits each country by market-size
    # tier, so the percentile means "standing among markets of this size".
    groups={}
    for ff in cands:
        d=ALLFEAT[ff]; groups.setdefault((gsys(d),market_tier(d) if tiered else None),[]).append(ff)
    pcts={k:{} for k in keys}
    for members in groups.values():
        for k in keys:
            pcts[k].update(pct_rank({ff:(raws[ff].get(k) if raws[ff] else None) for ff in members}))
    return {ff:_wavg([(pcts[k].get(ff),METRIC_WEIGHTS.get(k,1.0)) for k in keys]) for ff in cands}

def serving_edos(geoid,g):
    rows=[MASTER[i] for i in index_for(g).get(geoid,[]) if i in MASTER]
    rows.sort(key=lambda r:(r.get("territory_county_count") or 9999))
    return [{"objectid":r["objectid"],"organization":r["organization"],"category":r["category"],
             "territory_county_count":r.get("territory_county_count"),
             "territory_basis":r.get("territory_basis"),      # '..._INCOMPLETE' => territory unresolved
             "resolution_status":r.get("resolution_status"),
             "embed_url":r.get("embed_url") or ""} for r in rows]

DIM_PHRASE={"workforce":"labor availability, the ability to staff the headcount, and workforce skills",
            "demographics":"educational attainment, growth, and labor-force participation",
            "innovation":"technical employment, R&D presence, patenting and knowledge spillovers, and business dynamism",
            "dai":"its DAI score",
            "logistics":"airport, port, and commute access",
            "incentives":"the breadth of incentive programs",
            "infrastructure":"infrastructure quality",
            "real_estate":"land purchase price and property-tax burden",
            "cost":"labor and operating cost",
            "safety":"public safety (low crime)",
            "market_size":"market size (regional catchment)",
            "livability":"livability and community health"}
def build_rationale(rank,r,pending):
    s=r["sub_scores"]
    live=sorted([(d,s[d]) for d in DIMS if s[d] is not None],key=lambda x:x[1],reverse=True)
    place=f'{r["county"]}, {r["state"]}'
    out=[f'Ranked #{rank} with an overall score of {r["final_score"]} out of 100.']
    if live:
        strong=" and ".join(f'{DIM_PHRASE[d]} ({v})' for d,v in live[:2])
        out.append(f'{place} scores strongest on {strong}.')
        if len(live)>=3:
            d,v=live[-1]; out.append(f'Its lowest scored factor is {DIM_PHRASE[d]} ({v}).')
    if r["preferred_bonus"]:
        out.append(f'It also earns a +{r["preferred_bonus"]} bonus for being in a preferred region.')
    null_dims=[d for d in DIMS if s[d] is None]
    if null_dims:
        nice=", ".join(d.replace("_"," ") for d in null_dims)
        out.append(f'Data for {nice} is not in the model for this location, so the score reflects only the available factors rather than penalizing those gaps.')
    edo=r["serving_edos"][0] if r["serving_edos"] else None
    if edo: out.append(f'A lead here would route to {edo["organization"]} ({edo["category"]}).')
    if r.get("has_listed_properties"):
        who=r.get("property_edos") or []
        src=who[0] if who else "its serving EDO"
        out.append(f'It also has available properties currently listed on FastLocations through {src}, indicating ready site availability, which lifts its match.')
    # Disclose every adjustment applied AFTER the weighted factor average, so the headline number can
    # be reconciled from what the reader is shown rather than appearing to come from the factors alone.
    bd=r.get("score_breakdown") or {}
    adj=[]
    damp=bd.get("reliability_damping") or 0
    if abs(damp)>=0.5:
        adj.append(("pulled toward the national average" if damp<0 else "lifted toward the national average")+
                   f' by {abs(damp):.1f} points because its labour-market reach is '+("thin" if damp<0 else "modest"))
    b=bd.get("bonuses") or {}
    if b.get("preferred_region"): adj.append("a preferred-region bonus")
    if b.get("edo_coverage"): adj.append("a local-EDO coverage bonus")
    if b.get("property_access"): adj.append("a property-availability bonus")
    p=bd.get("penalties") or {}
    if p.get("hazard"): adj.append(f'a natural-hazard penalty of {p["hazard"]:.1f} points')
    if p.get("water"): adj.append(f'a water-risk penalty of {p["water"]:.1f} points')
    if adj:
        out.append("Beyond the factor scores, the total reflects "+", ".join(adj)+".")
    return " ".join(out)

def run(criteria,top=10):
    w={**DEFAULT_WEIGHTS,**(criteria.get("weights") or {})}
    # Guard: every slider can be dragged to 0. An all-zero vector made total weight 0, so the weighted
    # average was None -> final_score null, "score of None out of 100" in the rationale, and results
    # fell back to FIPS order (unranked). Fall back to the defaults instead of emitting a broken page.
    w={k:(float(v) if isinstance(v,(int,float)) and v>0 else 0.0) for k,v in w.items()}
    weights_fallback=sum(w.values())<=0
    if weights_fallback: w=dict(DEFAULT_WEIGHTS)
    prop_set=get_property_orgs()   # live property-org set (dashboard properties.js), cached w/ TTL
    geo=criteria.get("geography") or {}; demo=criteria.get("demographics") or {}; infra=criteria.get("infrastructure") or {}
    countries=set(geo.get("countries") or ["US","CA"])
    refset=[f for f in ALLFEAT if (("US" in countries and gsys(ALLFEAT[f])=="US") or
                                   ("CA" in countries and gsys(ALLFEAT[f])=="CA"))]
    # NATIONAL ANCHOR: score every dimension against the WHOLE selected-country set, independent of
    # the query's region / proximity / threshold filters. So a county's sub-scores and FastLocations
    # Score mean the same thing in a nationwide search and in a "Texas only" search -- the number is
    # its standing against the country, not just against the other counties that passed the filter.
    sub={dim:score_dimension(refset,ex,criteria,tiered=(dim in TIERED_DIMS)) for dim,ex in (
            ("workforce",m_workforce),("demographics",m_demographics),("logistics",m_logistics),
            ("incentives",m_incentives),("infrastructure",m_infrastructure),("real_estate",m_real_estate),
            ("cost",m_cost),("safety",m_safety),("market_size",m_market_size),("livability",m_livability),
            ("innovation",m_innovation),("dai",m_dai))}
    cands=list(refset)
    trace={"candidates_start":len(cands)}
    if weights_fallback:
        trace["weights_fallback"]="all supplied weights were zero; default weights used"
    if geo.get("required_regions"):
        rs=set(geo["required_regions"]); cands=[f for f in cands if ALLFEAT[f]["ST_ABBREV"] in rs]
    if geo.get("excluded_regions"):
        ex=set(geo["excluded_regions"]); cands=[f for f in cands if ALLFEAT[f]["ST_ABBREV"] not in ex]
    rtw=(criteria.get("workforce") or {}).get("right_to_work")   # None | "preferred" | "required"
    if rtw=="required":
        cands=[f for f in cands if ALLFEAT[f]["ST_ABBREV"] in RTW_STATES]
    if (criteria.get("infrastructure") or {}).get("drought")=="required":
        def _water_ok(f):
            d=ALLFEAT[f]
            if gsys(d)=="CA":                       # CA: exclude severe water-supply stress (e.g. Waterloo dev freeze);
                return (d.get("water_stress") or 0)<3   # CDs without a stress record are treated as OK (no US drought fields)
            # US: not in drought, not a depletion hotspot, and most local wells not declining
            return ((d.get("not_in_drought") or 0)>=50 and not d.get("gw_depleted")
                    and (d.get("gw_pct_declining") or 0)<=66)
        cands=[f for f in cands if _water_ok(f)]
    if (criteria.get("infrastructure") or {}).get("hazard")=="required":   # exclude high natural-hazard counties (FEMA NRI)
        cands=[f for f in cands if (ALLFEAT[f].get("nri_risk") or 0)<HAZARD_MAX_RISK]
    # market proximity: keep counties whose centroid is within max_miles of the place
    prox=[]; prox_unresolved=[]
    for entry in (geo.get("market_proximity") or []):
        nm=entry.get("to"); pt=geocode_place(nm); mx=entry.get("max_miles")
        if pt and mx: prox.append((pt[0],pt[1],float(mx),nm))
        elif nm:
            # A place we can't geocode used to be dropped silently -- the user got the FULL unfiltered
            # result set believing a proximity constraint had been applied. Report it instead.
            prox_unresolved.append(nm)
    if prox_unresolved: trace["market_proximity_unresolved"]=prox_unresolved
    if prox:
        def near(f):
            d=ALLFEAT[f]; lat=d.get("lat"); lon=d.get("lon")
            if lat is None or lon is None: return False  # no centroid -> cannot confirm within radius -> exclude
            return all(haversine_mi(lat,lon,a,b)<=m for a,b,m,_ in prox)
        cands=[f for f in cands if near(f)]
        trace["market_proximity"]=[{"to":t,"max_miles":m} for a,b,m,t in prox]
    def has_port(d):
        if gsys(d)=="CA": return bool((d.get("infra_ca") or {}).get("ports"))
        return bool(d.get("infra") and d["infra"]["port"]["present"])
    def has_airport(d):
        if gsys(d)=="CA": return bool((d.get("infra_ca") or {}).get("airports"))
        return bool(d.get("infra") and d["infra"]["airports"]["total"])
    # LABOR DRAW RADIUS: previously ignored entirely -- "500k people within 10 miles" returned the same
    # 1220 candidates as within 250 miles. The catchment aggregate is precomputed at a fixed ~65mi
    # radius, so we can't recompute it per request; instead we interpolate between the county's OWN
    # population (tight radius) and the full catchment (wide radius), which makes the input behave
    # directionally correctly: a tight radius stops crediting a county for a distant metro's people.
    draw=(demo.get("labor_draw_radius_miles") or None)
    def _reach(d):
        own=d.get("TOTPOP_CY"); cat=d.get("catchment_pop") or own
        if own is None: return cat
        if cat is None: return own
        if not draw: return cat                    # no radius given -> unchanged behaviour
        r=max(0.0,min(1.0,(float(draw)-15.0)/(100.0-15.0)))   # <=15mi = own county only, >=100mi = full catchment
        return own+(cat-own)*r
    def ok(f):
        d=ALLFEAT[f]
        # filters only apply where the datum exists -> absent data never excludes (no penalty).
        # min population / labor force are tested against the population reachable within the user's
        # stated labor-draw radius (see _reach), not just the county's own headcount.
        reg_pop=_reach(d)
        part=(d["CIVLBFR_CY"]/d["TOTPOP_CY"] if (d.get("CIVLBFR_CY") is not None and d.get("TOTPOP_CY")) else 0.48)
        reg_lf=(reg_pop*part if reg_pop is not None else None)
        if demo.get("min_population") and reg_pop is not None and reg_pop<demo["min_population"]: return False
        if demo.get("min_labor_force") and reg_lf is not None and reg_lf<demo["min_labor_force"]: return False
        if infra.get("port_required") and not has_port(d): return False
        if infra.get("commercial_airport_max_miles") is not None and not has_airport(d): return False
        return True
    cands=[f for f in cands if ok(f)]
    trace["candidates_after_filters"]=len(cands)
    served=set(f for f in cands if index_for(gsys(ALLFEAT[f])).get(f))   # served by a customer EDO
    trace["candidates_with_customer_edo"]=len(served)
    if not cands: return {"trace":trace,"results":[],"other_notable":[],"note":"no candidates passed filters"}

    # hazard preference: national percentile of hazard EXPOSURE across the reference set, so the
    # penalty is anchored the same way sub-scores are (a county's standing vs the country).
    hz_pref=(criteria.get("infrastructure") or {}).get("hazard")
    hz_pct={}
    if hz_pref:
        hz_pct=pct_rank({ff:ALLFEAT[ff].get("nri_risk") for ff in refset})   # 100 = most exposed
    wt_pref=(criteria.get("infrastructure") or {}).get("drought")
    wt_pct={}
    if wt_pref:
        wt_pct=pct_rank({ff:water_risk(ALLFEAT[ff]) for ff in refset})       # 100 = driest/most stressed
    pref=set(geo.get("preferred_regions") or [])
    if rtw=="preferred": pref|=RTW_STATES     # right-to-work states get the same +8 preference bonus
    results=[]
    # pass 1: raw weighted totals
    tmp=[]
    for f in cands:
        d=ALLFEAT[f]; g=gsys(d)
        scores={dim:sub[dim][f] for dim in DIMS}
        # Canada emphasizes regional market access (catchment population) so CDs in the Toronto /
        # Vancouver / Montreal orbits index higher; the user can still override the slider upward.
        wf=w if g!="CA" else {**w,"market_size":max(w.get("market_size",0),CA_MARKET_WEIGHT)}
        avail={dim:wf[dim] for dim in DIMS if scores[dim] is not None and wf.get(dim,0)>0}
        tw=sum(avail.values())
        total=round(sum(scores[dim]*wf[dim] for dim in avail)/tw,2) if tw else None
        tmp.append((f,d,g,scores,total))
    base=50.0   # damping shrink target = fixed national center (percentile scores average to ~50),
                # not the filtered-set mean, so scores stay absolute/comparable across queries.
    # pass 2: small-county reliability damping, then undamped preferred-region bonus
    for f,d,g,scores,total in tmp:
        bonus=8 if d["ST_ABBREV"] in pref else 0
        edos=serving_edos(f,g)
        cov=COVERAGE_BONUS.get(edos[0]["category"],0) if edos else 0   # local/regional coverage over-index
        prop_edos=[e for e in edos if e["objectid"] in prop_set]  # serving EDO(s) with live property listings
        # credit the most SPECIFIC property-holding EDO; statewide listings barely move the needle
        prop=round(PROPERTY_BONUS*max((property_scope_factor(e) for e in prop_edos),default=0.0),2)
        rel=None; final=None; damped=None
        if total is not None:
            # Reliability from the county's MARKET reach (regional catchment), not just its own
            # population. A small county inside a big metro (Arlington DC, Nassau NYC, a NJ suburb)
            # is a reliable, deep market and must not be damped like a thin rural county -- only
            # genuinely isolated small markets (catchment ~ own pop) get shrunk toward the mean.
            mkt=d.get("catchment_pop") or d.get("TOTPOP_CY") or 0
            rel=mkt/(mkt+SCALE_DAMP_K)
            damped=base+(total-base)*rel
            # Apply preferred + coverage bonuses to the REMAINING headroom, not as flat points, so a
            # near-100 score can't clamp (which would tie the top matches at 100 and hide the real
            # ordering). At typical scores this still adds ~the same points; near the top it tapers.
            final=round(damped+(100-damped)*min((bonus+cov+prop)/40.0,1.0),2)
        # hazard-sensitivity penalty: scaled by the county's national hazard-exposure percentile, so
        # opting in visibly demotes exposed locations instead of nudging them a fraction of a point.
        hz_p=hz_pct.get(f) if hz_pref else None
        hz_pen=round(HAZARD_PENALTY*(hz_p/100.0),2) if (hz_p is not None and final is not None) else 0.0
        wt_p=wt_pct.get(f) if wt_pref else None
        wt_pen=round(WATER_PENALTY*(wt_p/100.0),2) if (wt_p is not None and final is not None) else 0.0
        if (hz_pen or wt_pen) and final is not None:
            final=round(max(0.0,final-hz_pen-wt_pen),2)
        results.append({"geoid":f,"geo_system":g,"county":d["NAME"],"state":d["ST_ABBREV"],
                        "country":("Canada" if g=="CA" else "US"),
                        "lat":d.get("lat"),"lon":d.get("lon"),
                        "msa":(MSA_MAP.get(f) if g=="US" else CMA_MAP.get(f)),   # CA shows its CMA the same way US shows MSA
                        "sub_scores":scores,"weighted_total":total,"preferred_bonus":bonus,"coverage_bonus":cov,
                        "property_bonus":prop,"has_listed_properties":bool(prop_edos),
                        "property_edos":[e["organization"] for e in prop_edos],
                        "hazard_penalty":hz_pen,"hazard_exposure":(ALLFEAT[f].get("nri_risk") if hz_pref else None),
                        "water_penalty":wt_pen,
                        "reliability":(round(rel,3) if rel is not None else None),
                        # TRANSPARENCY: the score is weighted_total -> damped toward 50 by reliability
                        # -> headroom bonuses -> penalties. Every term is named here so the headline
                        # number reconciles exactly from the response (no hidden adjustment).
                        "score_breakdown":{
                            "weighted_total":total,
                            "reliability_damping":(round(damped-total,2) if (damped is not None and total is not None) else 0.0),
                            "bonuses":{"preferred_region":bonus,"edo_coverage":cov,"property_access":prop},
                            "penalties":{"hazard":hz_pen,"water":wt_pen},
                            "factors_scored":len([1 for dim in DIMS if scores[dim] is not None]),
                            "factors_total":len(DIMS)},
                        "final_score":final,"serving_edos":edos})
    results.sort(key=lambda r:(r["final_score"] is not None,r["final_score"]),reverse=True)
    # PRIMARY = counties served by an EDO customer, collapsed to distinct serving EDOs (roll past
    # repeats so the Top-N are different customers) with backfill; OTHER NOTABLE = the top-scoring
    # counties with NO EDO customer (unrelated to AI+Plus accounts) -- surfaced separately.
    seen=set(); primary=[]; extra=[]; other=[]
    for r in results:
        if r["geoid"] not in served:
            other.append(r); continue
        e=r["serving_edos"][0] if r["serving_edos"] else None
        key=e["objectid"] if e else ("_noedo_"+str(r["geoid"]))
        (extra if key in seen else primary).append(r); seen.add(key)
    # GEOGRAPHIC SPREAD: states differ enormously in how finely they're subdivided -- Georgia has 159
    # counties and Indiana 92, while all of British Columbia is 29 census divisions -- so granular
    # states get several times the chances of appearing. Cap how many slots one state/province can
    # take (same idea as the distinct-serving-EDO rule above). Scores are untouched; this only decides
    # which of the already-ranked results surface. Overflow is kept and used as backfill if the cap
    # would otherwise leave the list short.
    ordered=primary+extra
    if MAX_PER_REGION and len(ordered)>top:
        per=collections.Counter(); capped=[]; overflow=[]
        for r in ordered:
            st=r["state"]
            if per[st]<MAX_PER_REGION: capped.append(r); per[st]+=1
            else: overflow.append(r)
        ordered=capped+overflow
    top_results=ordered[:top]
    for i,r in enumerate(top_results,1): r["rationale"]=build_rationale(i,r,None)
    other_notable=[{"county":r["county"],"state":r["state"],"country":r["country"],
                    "msa":r.get("msa"),"final_score":r["final_score"],"lat":r.get("lat"),"lon":r.get("lon")} for r in other[:top]]
    return {"schema_version":"1.0","trace":trace,"weights_used":w,
            "dimensions_live":["workforce","demographics","logistics","incentives","real_estate","cost","safety","market_size","infrastructure","livability","innovation","dai"],
            "dimensions_pending_data":[],
            "results":top_results,"other_notable":other_notable}

if __name__=="__main__":
    crit=json.load(open(sys.argv[1])) if len(sys.argv)>1 else {}
    out=run(crit,top=int(os.environ.get("TOP","10")))
    print(json.dumps(out["trace"],indent=2))
    for r in out["results"]:
        e=r["serving_edos"][0]["organization"] if r["serving_edos"] else "— no EDO —"
        print(f"  {r['final_score']!s:>6}  {r['county']+', '+r['state']:<26}({r['country']:>6}) -> {e[:40]}")
