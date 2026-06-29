#!/usr/bin/env python3
"""
scorer.py — FastLocations deterministic site-matching scorer  (ProjectCriteria v1).

Dual-spine: ranks US counties (FIPS) and Canadian Census Divisions (CDUID), then
rolls each up to the serving EDO customer from organizations.json.

Weighted dimensions (per schema `weights`):
  workforce      US only (labor/wage/education metrics; CA has no such data -> null)
  demographics   US: pop/labor/education/growth ; CA: population
  logistics      US: airports+ports ; CA: airports+ports+grid counts
  incentives     US states + CA provinces (state/province-keyed programs)
  infrastructure PENDING (power) -> null
  real_estate    PENDING (sites)  -> null

Rules: required/excluded = filters, preferred = bonus; coverage gaps -> null
(weights renormalise over non-null dims, never penalise); only counties/CDs served
by a customer EDO are ranked; sub-scores surfaced individually.
"""
import json,os,sys,math,urllib.request,urllib.parse
O=os.environ.get("FL_DATA_DIR") or os.path.dirname(os.path.abspath(__file__))
def _load(fn): return json.load(open(os.path.join(O,fn)))

FEAT=_load("county_features.json")                 # US, keyed by 5-digit FIPS
for v in FEAT.values(): v["geo_system"]="US"
try:
    CA_FEAT=_load("ca_features.json")              # CA, keyed by 4-digit CDUID
except FileNotFoundError:
    CA_FEAT={}
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

DIMS=["workforce","demographics","infrastructure","logistics","incentives","real_estate","cost","safety","market_size","livability"]
DEFAULT_WEIGHTS={"workforce":0.18,"infrastructure":0.08,"incentives":0.10,"real_estate":0.13,
                 "demographics":0.06,"logistics":0.06,"cost":0.18,"safety":0.06,"market_size":0.07,"livability":0.08}

def gsys(f): return f.get("geo_system","US")
def index_for(g): return FIPS_INDEX if g=="US" else CA_INDEX

_GEO_CACHE={}
def geocode_place(place):
    place=(place or "").strip()
    if not place: return None
    if place in _GEO_CACHE: return _GEO_CACHE[place]
    res=None
    parts=[p.strip() for p in place.split(",")]
    if len(parts)>=2:
        city=_norm(parts[0]); stoken=parts[-1].strip()
        st=stoken.upper() if len(stoken)==2 else _STATE2AB.get(_norm(stoken))
        if st: res=PLACES.get(st+"|"+city)
    if not res:                                 # city only -> first state match
        nm="|"+_norm(parts[0])
        for k,v in PLACES.items():
            if k.endswith(nm): res=v; break
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

def pct_rank(values):
    pairs=[(k,v) for k,v in values.items() if v is not None]
    if len(pairs)<2: return {k:(50.0 if v is not None else None) for k,v in values.items()}
    s=sorted(pairs,key=lambda x:x[1]); n=len(s); rank={k:i/(n-1)*100 for i,(k,_) in enumerate(s)}
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
    pr=(crit.get("demographics") or {}).get("education_priority","none")
    return {"population":f.get("TOTPOP_CY"),"labor_force":f.get("CIVLBFR_CY"),
            "education_attainment":edu_share(f,pr),"pop_growth":f.get("POPGRW20CY")}
def m_workforce(f,crit):
    une=f.get("UNEMPRT_CY"); lf=f.get("CIVLBFR_CY"); inc=f.get("MEDHINC_CY")
    need=((crit.get("workforce") or {}).get("headcount") or {}).get("initial")
    adequacy=(lf/need if (lf is not None and need) else lf)
    ct=f.get("critical_thinking")
    return {"labor_availability":(-une if une is not None else None),
            "labor_pool_adequacy":adequacy,
            "prime_workage_share":(f["WORKAGE_CY"]/f["TOTPOP_CY"] if (f.get("WORKAGE_CY") is not None and f.get("TOTPOP_CY")) else None),
            "critical_thinking":ct}
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
    prio=(crit.get("incentives") or {}).get("priorities") or []
    pf=None
    if prio:
        n=len(prio); tot=n*(n+1)/2.0
        got=sum((n-i) for i,t in enumerate(prio) if rec["types_present"].get(t))
        pf=got/tot*100
    return {"incentive_breadth":rec["count"],"priority_match":pf}

def m_livability(f,crit):
    pd=f.get("premature_death"); pf=f.get("poor_fair_health")
    pp=f.get("poor_phys_days"); pm=f.get("poor_mental_days"); le=f.get("life_expectancy")
    if all(x is None for x in (pd,pf,pp,pm,le)): return None
    return {"long_life":(-pd if pd is not None else None),
            "good_health":(-pf if pf is not None else None),
            "few_phys_unhealthy_days":(-pp if pp is not None else None),
            "few_mental_unhealthy_days":(-pm if pm is not None else None),
            "life_expectancy":le}

def m_market_size(f,crit):
    pop=f.get("TOTPOP_CY")
    if pop is None: return None
    return {"population_scale":pop}

def m_infrastructure(f,crit):
    g=INFRA_GRADES.get(f.get("ST_ABBREV"))
    pts=GRADE_PTS.get(g) if g else None
    if pts is None: return None
    return {"infra_grade":pts}

def m_cost(f,crit):
    inc=f.get("MEDHINC_CY"); wl=f.get("WLTHINDXCY")
    if inc is None and wl is None: return None
    return {"low_labor_cost":(-inc if inc is not None else None),
            "low_cost_of_living":(-wl if wl is not None else None)}

def m_safety(f,crit):
    cr=f.get("crime_rate")
    if cr is None: return None
    return {"low_crime":-cr}

def m_real_estate(f,crit):
    pt=f.get("property_tax_rate")
    if pt is None: return None
    return {"low_property_tax":-pt}

def score_dimension(cands,extract,crit):
    raws={ff:extract(ALLFEAT[ff],crit) for ff in cands}
    keys=set()
    for r in raws.values():
        if r: keys|=set(r.keys())
    if not keys: return {ff:None for ff in cands}
    pcts={k:pct_rank({ff:(raws[ff].get(k) if raws[ff] else None) for ff in cands}) for k in keys}
    return {ff:avg([pcts[k][ff] for k in keys]) for ff in cands}

def serving_edos(geoid,g):
    rows=[MASTER[i] for i in index_for(g).get(geoid,[]) if i in MASTER]
    rows.sort(key=lambda r:(r.get("territory_county_count") or 9999))
    return [{"objectid":r["objectid"],"organization":r["organization"],"category":r["category"],
             "territory_county_count":r.get("territory_county_count"),
             "resolution_status":r.get("resolution_status"),
             "embed_url":r.get("embed_url") or ""} for r in rows]

DIM_PHRASE={"workforce":"labor availability and workforce skills (critical thinking)",
            "demographics":"population, labor force, and educational attainment",
            "logistics":"airport, port, and commute access",
            "incentives":"the breadth of incentive programs",
            "infrastructure":"infrastructure quality (ASCE state grade)",
            "real_estate":"real-estate cost (property taxes)",
            "cost":"operating cost (wages and cost of living)",
            "safety":"public safety (low crime)",
            "market_size":"market size (population scale)",
            "livability":"livability and community health (County Health Rankings)"}
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
    cands=[f for f in ALLFEAT if (("US" in countries and gsys(ALLFEAT[f])=="US") or
                                  ("CA" in countries and gsys(ALLFEAT[f])=="CA"))]
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
    cands=[f for f in cands if index_for(gsys(ALLFEAT[f])).get(f)]   # served by a customer EDO
    trace["candidates_with_customer_edo"]=len(cands)
    if not cands: return {"trace":trace,"results":[],"note":"no candidates with a customer EDO passed filters"}

    sub={"workforce":score_dimension(cands,m_workforce,criteria),
         "demographics":score_dimension(cands,m_demographics,criteria),
         "logistics":score_dimension(cands,m_logistics,criteria),
         "incentives":score_dimension(cands,m_incentives,criteria),
         "infrastructure":score_dimension(cands,m_infrastructure,criteria),
         "real_estate":score_dimension(cands,m_real_estate,criteria),
         "cost":score_dimension(cands,m_cost,criteria),
         "safety":score_dimension(cands,m_safety,criteria),
         "market_size":score_dimension(cands,m_market_size,criteria),
         "livability":score_dimension(cands,m_livability,criteria)}

    pref=set(geo.get("preferred_regions") or []); results=[]
    for f in cands:
        d=ALLFEAT[f]; g=gsys(d)
        scores={dim:sub[dim][f] for dim in DIMS}
        avail={dim:w[dim] for dim in DIMS if scores[dim] is not None and w.get(dim,0)>0}
        tw=sum(avail.values())
        total=round(sum(scores[dim]*w[dim] for dim in avail)/tw,2) if tw else None
        bonus=8 if d["ST_ABBREV"] in pref else 0
        final=min(round((total or 0)+bonus,2),100) if total is not None else None
        results.append({"geoid":f,"geo_system":g,"county":d["NAME"],"state":d["ST_ABBREV"],
                        "country":("Canada" if g=="CA" else "US"),
                        "sub_scores":scores,"weighted_total":total,"preferred_bonus":bonus,
                        "final_score":final,"serving_edos":serving_edos(f,g)})
    results.sort(key=lambda r:(r["final_score"] is not None,r["final_score"]),reverse=True)
    top_results=results[:top]
    for i,r in enumerate(top_results,1): r["rationale"]=build_rationale(i,r,None)
    return {"schema_version":"1.0","trace":trace,"weights_used":w,
            "dimensions_live":["workforce","demographics","logistics","incentives","real_estate","cost","safety","market_size","infrastructure","livability"],
            "dimensions_pending_data":[],
            "results":top_results}

if __name__=="__main__":
    crit=json.load(open(sys.argv[1])) if len(sys.argv)>1 else {}
    out=run(crit,top=int(os.environ.get("TOP","10")))
    print(json.dumps(out["trace"],indent=2))
    for r in out["results"]:
        e=r["serving_edos"][0]["organization"] if r["serving_edos"] else "— no EDO —"
        print(f"  {r['final_score']!s:>6}  {r['county']+', '+r['state']:<26}({r['country']:>6}) -> {e[:40]}")
