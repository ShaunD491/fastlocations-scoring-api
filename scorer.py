#!/usr/bin/env python3
"""
scorer.py — FastLocations deterministic site-matching scorer  (ProjectCriteria v1).

Dual-spine: ranks US counties (FIPS) and Canadian Census Divisions (CDUID), then
rolls each up to the serving EDO customer from organizations.json.

Ten weighted dimensions (percentile-ranked within the candidate set, then weighted):
  workforce      US: unemployment, staffability, prime-age share, critical thinking ; CA: unemployment + staffability
  demographics   US: education (2x), growth, participation ; CA: bachelor share, growth, participation
  infrastructure US: ASCE state grade ; CA: CMA = high, mid elsewhere
  logistics      US: airports + ports + commute ; CA: airport/port/grid counts
  incentives     value tier (2x) + type diversity + priority match (state/province-keyed)
  real_estate    US: property-tax rate ; CA: median dwelling value
  cost           US: median income + wealth index ; CA: median income
  safety         US: crime rate ; CA: StatCan Crime Severity Index
  market_size    regional catchment population (US and CA)
  livability     US: County Health Rankings outcomes ; CA: Numbeo most-livable cities (ranked CDs only)

Rules: required/excluded = filters, preferred = bonus; coverage gaps -> null
(weights renormalise over non-null dims, never penalise). Results split into PRIMARY
(served by a customer EDO, distinct orgs) and OTHER NOTABLE (top non-customer counties).
Small-county reliability damping + local/regional coverage bonus applied to the final.
"""
import json,os,sys,math,urllib.request,urllib.parse
O=os.environ.get("FL_DATA_DIR") or os.path.dirname(os.path.abspath(__file__))
def _load(fn): return json.load(open(os.path.join(O,fn)))

FEAT=_load("county_features.json")                 # US, keyed by 5-digit FIPS
for v in FEAT.values(): v["geo_system"]="US"
try:                                               # regional market catchment (population reachable ~110km, decayed)
    _USC=_load("us_catchment.json")                # fips -> catchment_pop; lets fragmented multi-county MSAs (NYC, Boston, SF) aggregate
    for _k,_v in FEAT.items():
        if _k in _USC: _v["catchment_pop"]=_USC[_k]
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
try: CA_PLACES=_load("ca_places.json")             # CA "PROV|normname" and bare "normname" -> [lat,lon]
except FileNotFoundError: CA_PLACES={}
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

DIMS=["workforce","demographics","infrastructure","logistics","incentives","real_estate","cost","safety","market_size","livability"]
DEFAULT_WEIGHTS={"workforce":0.18,"infrastructure":0.08,"incentives":0.10,"real_estate":0.15,
                 "demographics":0.05,"logistics":0.08,"cost":0.20,"safety":0.05,"market_size":0.06,"livability":0.05}
# Small-county reliability damping. A county's score is shrunk toward the candidate-set mean
# by reliability = pop/(pop+SCALE_DAMP_K): big labor markets keep their score, thin ones (where
# percentile metrics are noisy and the market can't realistically host most projects) are pulled
# to the middle. Prevents tiny / college-town counties from topping generic searches. Tunable.
SCALE_DAMP_K=100000
CA_MARKET_WEIGHT=0.20   # Canada weights regional market access (catchment) up; see m_market_size (CA)
# Coverage bonus: over-index jurisdictions served by a LOCAL or REGIONAL EDO customer (a specific
# org to route the lead to) over those covered only by a broad State/Provincial agency. Added to
# the final score from the most-specific serving EDO's category. US + Canada (CA orgs are all
# provincial today, so they score 0 until local/regional Canadian EDOs are added).
COVERAGE_BONUS={"Local Development Agency":10,"Chamber of Commerce":10,"Megasite":10,
                "Industrial Park":10,"Port/Airport Authority":9,"Utility":9,
                "Regional Development Agency":7,"State Agency":0}

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
EDU={"none":["BACHDEG_CY","GRADDEG_CY","ASSCDEG_CY","SMCOLL_CY","HSGRAD_CY"],
     "hs":["HSGRAD_CY","SMCOLL_CY","ASSCDEG_CY","BACHDEG_CY","GRADDEG_CY"],
     "some_college":["SMCOLL_CY","ASSCDEG_CY","BACHDEG_CY","GRADDEG_CY"],
     "bachelors_plus":["BACHDEG_CY","GRADDEG_CY"]}
def edu_share(f,priority):
    base=f.get("EDUCBASECY") or 0
    if not base: return None
    s=sum((f.get(k) or 0) for k in EDU.get(priority,EDU["none"]))
    return s/base*100
def m_demographics(f,crit):
    # Composition / quality only. Raw population scale is carried by market_size, and absolute
    # labor force mirrors it (r~0.89), so demographics uses RATES/quality to de-correlate:
    # educational attainment, population growth, and labor-force participation.
    if gsys(f)=="CA":                              # StatCan: bachelor's share, 2016-21 growth, participation rate
        if f.get("bachelor_share") is None and f.get("pop_growth") is None: return None
        return {"education_attainment":f.get("bachelor_share"),
                "pop_growth":f.get("pop_growth"),
                "labor_force_participation":f.get("participation")}
    pr=(crit.get("demographics") or {}).get("education_priority","none")
    pop=f.get("TOTPOP_CY"); lf=f.get("CIVLBFR_CY")
    return {"education_attainment":edu_share(f,pr),   # weighted 2x via METRIC_WEIGHTS (lifts mature, highly-educated NE/east coast)
            "pop_growth":f.get("POPGRW20CY"),
            "labor_force_participation":(lf/pop if (lf is not None and pop) else None)}
def m_workforce(f,crit):
    need=((crit.get("workforce") or {}).get("headcount") or {}).get("initial")
    if gsys(f)=="CA":                              # StatCan: unemployment (availability) + staffability
        une=f.get("unemployment"); lf=f.get("labour_force"); pop=f.get("TOTPOP_CY")
        staff=None
        if need and need>0:
            parts=[]
            if lf  is not None: parts.append((lf /need)/50.0)
            if pop is not None: parts.append((pop/need)/100.0)
            if parts: staff=min(min(parts),1.0)
        return {"labor_availability":(-une if une is not None else None),"staffability":staff}
    une=f.get("UNEMPRT_CY"); lf=f.get("CIVLBFR_CY"); pop=f.get("TOTPOP_CY")
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
        if lf  is not None: parts.append((lf /need)/50.0)    # labor force ~50x need = ample supply
        if pop is not None: parts.append((pop/need)/100.0)   # population  ~100x need = ample draw
        if parts: staff=min(min(parts),1.0)                  # binding constraint, capped at "ample"
    return {"labor_availability":(-une if une is not None else None),
            "staffability":staff,
            "prime_workage_share":(f["WORKAGE_CY"]/f["TOTPOP_CY"] if (f.get("WORKAGE_CY") is not None and f.get("TOTPOP_CY")) else None),
            "critical_thinking":f.get("critical_thinking")}   # weighted 2x via METRIC_WEIGHTS
def m_logistics(f,crit):
    if gsys(f)=="CA":
        inf=f.get("infra_ca")
        if not inf: return None
        return {"ca_airports":(inf.get("airports") or None),
                "ca_ports":(inf.get("ports") or None),
                "ca_grid":(inf.get("grid_nodes") or None)}
    inf=f.get("infra")
    if not inf: return None
    ap=inf["airports"]; pt=inf["port"]
    cm=f.get("commute_min")
    return {"air_capacity":((ap["large"]*3+ap["medium"]*2+ap["small"]) or None),
            "air_enplanements":(ap["enplanements"] or None),
            "port_tonnage":(pt["max_tonnage"] or None),
            "short_commute":(-cm if cm is not None else None)}
def m_incentives(f,crit):
    rec=INC.get(f.get("ST_ABBREV"))
    if not rec: return None
    tc=rec.get("type_counts") or {}
    prio=(crit.get("incentives") or {}).get("priorities") or []
    pf=None
    if prio:
        # depth-weighted: each ranked priority scores by HOW MANY programs of that type exist
        # (saturating at 3), weighted by the user's ranking order -- not just present/absent.
        n=len(prio); tot=n*(n+1)/2.0
        got=sum((n-i)*min(tc.get(t,0),3)/3.0 for i,t in enumerate(prio))
        pf=got/tot*100
    # QUALITY, not quantity: raw program count is intentionally excluded. Score reflects the value
    # tier (largest program $ advertised, weighted double), the range of incentive types offered,
    # and how well those types match the project's ranked priorities.
    return {"incentive_value":rec.get("value_tier"),   # weighted 2x via METRIC_WEIGHTS
            "incentive_diversity":rec.get("type_diversity"),
            "priority_match":pf}

def m_livability(f,crit):
    if gsys(f)=="CA":                              # Numbeo 2026 most-livable Canadian cities (ranked CDs only)
        lv=f.get("ca_livability")
        return {"livability_rank":lv} if lv is not None else None
    pd=f.get("premature_death"); pf=f.get("poor_fair_health")
    pp=f.get("poor_phys_days"); pm=f.get("poor_mental_days"); le=f.get("life_expectancy")
    if all(x is None for x in (pd,pf,pp,pm,le)): return None
    return {"long_life":(-pd if pd is not None else None),
            "good_health":(-pf if pf is not None else None),
            "few_phys_unhealthy_days":(-pp if pp is not None else None),
            "few_mental_unhealthy_days":(-pm if pm is not None else None),
            "life_expectancy":le}

def m_market_size(f,crit):
    cp=f.get("catchment_pop")                       # regional catchment (metro access) for US and CA
    if cp is not None: return {"population_scale":cp}
    pop=f.get("TOTPOP_CY")
    if pop is None: return None
    return {"population_scale":pop}

def m_infrastructure(f,crit):
    if gsys(f)=="CA":                              # CMA = highest, mid everywhere else (per provided rule)
        p=f.get("ca_infra_pts")
        return {"infra_grade":p} if p is not None else None
    g=INFRA_GRADES.get(f.get("ST_ABBREV"))
    pts=GRADE_PTS.get(g) if g else None
    if pts is None: return None
    return {"infra_grade":pts}

def m_cost(f,crit):
    if gsys(f)=="CA":                              # StatCan: median household income as the labor-cost proxy
        inc=f.get("income")
        return {"low_labor_cost":-inc} if inc is not None else None
    inc=f.get("MEDHINC_CY"); wl=f.get("WLTHINDXCY")
    if inc is None and wl is None: return None
    return {"low_labor_cost":(-inc if inc is not None else None),
            "low_cost_of_living":(-wl if wl is not None else None)}

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
    pt=f.get("property_tax_rate")
    if pt is None: return None
    return {"low_property_tax":-pt}

# Per-metric weights WITHIN a dimension (default 1). Lets a metric count for more without the old
# duplicate-key hack: education dominates demographics; critical thinking and value tier count 2x.
METRIC_WEIGHTS={"education_attainment":2.0,"critical_thinking":2.0,"incentive_value":2.0}
def _wavg(pairs):   # pairs = list of (percentile_or_None, weight)
    num=den=0.0
    for v,wt in pairs:
        if v is not None: num+=v*wt; den+=wt
    return round(num/den,1) if den else None
def score_dimension(cands,extract,crit):
    raws={ff:extract(ALLFEAT[ff],crit) for ff in cands}
    keys=set()
    for r in raws.values():
        if r: keys|=set(r.keys())
    if not keys: return {ff:None for ff in cands}
    pcts={k:pct_rank({ff:(raws[ff].get(k) if raws[ff] else None) for ff in cands}) for k in keys}
    return {ff:_wavg([(pcts[k][ff],METRIC_WEIGHTS.get(k,1.0)) for k in keys]) for ff in cands}

def serving_edos(geoid,g):
    rows=[MASTER[i] for i in index_for(g).get(geoid,[]) if i in MASTER]
    rows.sort(key=lambda r:(r.get("territory_county_count") or 9999))
    return [{"objectid":r["objectid"],"organization":r["organization"],"category":r["category"],
             "territory_county_count":r.get("territory_county_count"),
             "resolution_status":r.get("resolution_status"),
             "embed_url":r.get("embed_url") or ""} for r in rows]

DIM_PHRASE={"workforce":"labor availability, the ability to staff the headcount, and workforce skills",
            "demographics":"educational attainment, growth, and labor-force participation",
            "logistics":"airport, port, and commute access",
            "incentives":"the breadth of incentive programs",
            "infrastructure":"infrastructure quality",
            "real_estate":"real-estate cost",
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
    return " ".join(out)

def run(criteria,top=10):
    w={**DEFAULT_WEIGHTS,**(criteria.get("weights") or {})}
    geo=criteria.get("geography") or {}; demo=criteria.get("demographics") or {}; infra=criteria.get("infrastructure") or {}
    countries=set(geo.get("countries") or ["US","CA"])
    refset=[f for f in ALLFEAT if (("US" in countries and gsys(ALLFEAT[f])=="US") or
                                   ("CA" in countries and gsys(ALLFEAT[f])=="CA"))]
    # NATIONAL ANCHOR: score every dimension against the WHOLE selected-country set, independent of
    # the query's region / proximity / threshold filters. So a county's sub-scores and FastLocations
    # Score mean the same thing in a nationwide search and in a "Texas only" search -- the number is
    # its standing against the country, not just against the other counties that passed the filter.
    sub={dim:score_dimension(refset,ex,criteria) for dim,ex in (
            ("workforce",m_workforce),("demographics",m_demographics),("logistics",m_logistics),
            ("incentives",m_incentives),("infrastructure",m_infrastructure),("real_estate",m_real_estate),
            ("cost",m_cost),("safety",m_safety),("market_size",m_market_size),("livability",m_livability))}
    cands=list(refset)
    trace={"candidates_start":len(cands)}
    if geo.get("required_regions"):
        rs=set(geo["required_regions"]); cands=[f for f in cands if ALLFEAT[f]["ST_ABBREV"] in rs]
    if geo.get("excluded_regions"):
        ex=set(geo["excluded_regions"]); cands=[f for f in cands if ALLFEAT[f]["ST_ABBREV"] not in ex]
    # market proximity: keep counties whose centroid is within max_miles of the place
    prox=[]
    for entry in (geo.get("market_proximity") or []):
        pt=geocode_place(entry.get("to")); mx=entry.get("max_miles")
        if pt and mx: prox.append((pt[0],pt[1],float(mx),entry.get("to")))
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
    def ok(f):
        d=ALLFEAT[f]
        # filters only apply where the datum exists -> absent data never excludes (no penalty)
        if demo.get("min_population") and d.get("TOTPOP_CY") is not None and d["TOTPOP_CY"]<demo["min_population"]: return False
        if demo.get("min_labor_force") and d.get("CIVLBFR_CY") is not None and d["CIVLBFR_CY"]<demo["min_labor_force"]: return False
        if infra.get("port_required") and not has_port(d): return False
        if infra.get("commercial_airport_max_miles") is not None and not has_airport(d): return False
        return True
    cands=[f for f in cands if ok(f)]
    trace["candidates_after_filters"]=len(cands)
    served=set(f for f in cands if index_for(gsys(ALLFEAT[f])).get(f))   # served by a customer EDO
    trace["candidates_with_customer_edo"]=len(served)
    if not cands: return {"trace":trace,"results":[],"other_notable":[],"note":"no candidates passed filters"}

    pref=set(geo.get("preferred_regions") or []); results=[]
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
        rel=None; final=None
        if total is not None:
            pop=d.get("TOTPOP_CY") or 0
            rel=pop/(pop+SCALE_DAMP_K)
            damped=base+(total-base)*rel
            # Apply preferred + coverage bonuses to the REMAINING headroom, not as flat points, so a
            # near-100 score can't clamp (which would tie the top matches at 100 and hide the real
            # ordering). At typical scores this still adds ~the same points; near the top it tapers.
            final=round(damped+(100-damped)*min((bonus+cov)/40.0,1.0),2)
        results.append({"geoid":f,"geo_system":g,"county":d["NAME"],"state":d["ST_ABBREV"],
                        "country":("Canada" if g=="CA" else "US"),
                        "lat":d.get("lat"),"lon":d.get("lon"),"msa":(MSA_MAP.get(f) if g=="US" else None),
                        "sub_scores":scores,"weighted_total":total,"preferred_bonus":bonus,"coverage_bonus":cov,
                        "reliability":(round(rel,3) if rel is not None else None),
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
    top_results=(primary+extra)[:top]
    for i,r in enumerate(top_results,1): r["rationale"]=build_rationale(i,r,None)
    other_notable=[{"county":r["county"],"state":r["state"],"country":r["country"],
                    "msa":r.get("msa"),"final_score":r["final_score"],"lat":r.get("lat"),"lon":r.get("lon")} for r in other[:top]]
    return {"schema_version":"1.0","trace":trace,"weights_used":w,
            "dimensions_live":["workforce","demographics","logistics","incentives","real_estate","cost","safety","market_size","infrastructure","livability"],
            "dimensions_pending_data":[],
            "results":top_results,"other_notable":other_notable}

if __name__=="__main__":
    crit=json.load(open(sys.argv[1])) if len(sys.argv)>1 else {}
    out=run(crit,top=int(os.environ.get("TOP","10")))
    print(json.dumps(out["trace"],indent=2))
    for r in out["results"]:
        e=r["serving_edos"][0]["organization"] if r["serving_edos"] else "— no EDO —"
        print(f"  {r['final_score']!s:>6}  {r['county']+', '+r['state']:<26}({r['country']:>6}) -> {e[:40]}")
