#!/usr/bin/env python3
"""
FastLocations — local scoring API.

  pip install -r requirements.txt
  python app.py
  open http://127.0.0.1:8000

Routes:
  GET  /         -> the intake form
  GET  /health   -> data load status (open this in a browser to confirm the API is up)
  POST /match    -> body = ProjectCriteria JSON; returns ranked results + serving EDO (?top=N, default 5)
                    (workforce.occupations, if given, adds occupation labor scoring - see labor.py)
  POST /labor/rank     -> body = ProjectCriteria with workforce.occupations; every US metro and Canadian
                          economic region ranked for those roles (?top=N default 10, ?nonmetro=1)
  POST /labor/compare  -> body = ProjectCriteria + "sites": [2-5 FIPS/CDUIDs, "City, ST" or {lat,lon}]
  GET  /labor/occupations?q=weld  -> SOC/NOC title lookup for the occupation picker
  POST /labor/parse    -> {"text": "120 forklift operators and 10 maintenance techs"} -> roles, headcounts,
                          matched SOC/NOC candidates (labor_intake.py)

OCCUPATION LABOR IS AN AI+PLUS FEATURE. The /labor/* routes and workforce.occupations in /match and
/submit only work for callers that send the shared key (header X-FL-Labor-Key = env FL_LABOR_KEY). The
caller is EDO/api.php, which signs the EDO in with its portal access code first (labor_* actions) and
adds the key. Without the key /labor/* returns 403 and /match ignores workforce.occupations. Running
app.py directly (local dev) leaves it open unless FL_LABOR_KEY is set.

CORS is open so the form works whether it's opened via this server, a file://, or
another local dev server (e.g. VS Code Live Server).
"""
import os, json, urllib.request
HERE=os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("FL_DATA_DIR", HERE)

from flask import Flask, request, jsonify, send_file
import scorer
import hmac
import labor
import labor_intake

LABOR_DENIED={"ok":False,"error":"Occupation labor scoring is part of FastLocations AI+Plus.",
              "upgrade":"https://www.fastlocations.ai/subscribe.html"}

def labor_allowed():
    key=os.environ.get("FL_LABOR_KEY","")
    if not key: return os.environ.get("FL_LABOR_OPEN")=="1"
    got=request.headers.get("X-FL-Labor-Key","")
    return bool(got) and hmac.compare_digest(got,key)

def strip_labor(crit):
    """Public callers: drop the AI+Plus labor inputs so /match scores exactly as before."""
    wf=crit.get("workforce")
    if isinstance(wf,dict) and (wf.get("occupations") or wf.get("labor_weights")):
        crit["workforce"]={k:v for k,v in wf.items() if k not in ("occupations","labor_weights")}
        return True
    return False

app=Flask(__name__)

@app.after_request
def add_cors(resp):
    resp.headers["Access-Control-Allow-Origin"]="*"
    resp.headers["Access-Control-Allow-Headers"]="Content-Type, X-FL-Labor-Key"
    resp.headers["Access-Control-Allow-Methods"]="GET, POST, OPTIONS"
    return resp

@app.get("/")
def home():
    return send_file(os.path.join(HERE,"site_selection_intake.html"))

@app.get("/intake.js")
def intake_js():
    return send_file(os.path.join(HERE,"intake.js"), mimetype="application/javascript")

@app.get("/email-status")
def email_status():
    return {"resend_key_set": bool(os.environ.get("RESEND_API_KEY")),
            "notify_email_set": bool(os.environ.get("FL_NOTIFY_EMAIL")),
            "email_from": os.environ.get("EMAIL_FROM", "(default Resend test sender)")}

@app.get("/health")
def health():
    return {"status":"ok","counties":len(scorer.FEAT),"edos":len(scorer.MASTER),
            "states_with_incentives":len(scorer.INC),
            "labor_data":(labor.index() or {}).get("built")}


def send_match_email(criteria, out):
    """Email the ranked matches via Resend to the submitter + FastLocations inbox.
    Skips silently if RESEND_API_KEY is not configured."""
    key=os.environ.get("RESEND_API_KEY")
    if not key: return {"sent": False, "reason": "RESEND_API_KEY not set"}
    sb=(criteria.get("project") or {}).get("submitted_by") or {}
    submitter=(sb.get("email") or "").strip()
    notify=os.environ.get("FL_NOTIFY_EMAIL","").strip()
    to=list(dict.fromkeys([e for e in (submitter, notify) if e]))
    if not to: return {"sent": False, "reason": "no recipient email"}
    proj=(criteria.get("project") or {}).get("project_name") or "Your project"
    items=""
    for i, r in enumerate(out.get("results", []), 1):
        edo=(r.get("serving_edos") or [{}])[0]
        org=edo.get("organization","")
        items += ("<li style='margin-bottom:12px'><b>%d. %s, %s</b> &mdash; score %s"
                  "%s<br><span style='color:#555;font-size:13px'>%s</span></li>") % (
                  i, r.get("county",""), r.get("state",""), r.get("final_score",""),
                  ("<br>Route lead to: <b>%s</b>" % org) if org else "",
                  r.get("rationale",""))
    html=("<div style='font-family:Arial,sans-serif'>"
          "<h2 style='color:#1a54a0'>FastLocations matches &mdash; %s</h2>"
          "<p>Top %d locations for this project:</p><ol>%s</ol>"
          "<p style='color:#888;font-size:12px'>Generated by FastLocations.ai. "
          "Preliminary and informational only.</p></div>") % (proj, len(out.get("results",[])), items)
    payload={"from": os.environ.get("EMAIL_FROM", "FastLocations <onboarding@resend.dev>"),
             "to": to, "subject": "FastLocations matches - %s" % proj, "html": html}
    req=urllib.request.Request("https://api.resend.com/emails",
        data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer "+key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return {"sent": True, "status": resp.status, "to": to}
    except Exception as e:
        return {"sent": False, "reason": str(e)[:150]}

@app.route("/submit", methods=["POST","OPTIONS"])
def submit():
    if request.method=="OPTIONS": return ("",204)
    crit=request.get_json(force=True,silent=True) or {}
    if not labor_allowed(): strip_labor(crit)
    out=scorer.run(crit, top=5)
    try: res=send_match_email(crit, out)
    except Exception as e: res={"sent": False, "reason": str(e)[:150]}
    return jsonify({"email": res, "count": len(out.get("results", []))})

@app.get("/places")
def places():
    """Autocomplete for market-proximity. Covers BOTH countries: this used to iterate only the US
    table, so a Canada-mode search for 'Toronto, ON' was offered 'Toronto, OH'. Canadian labels come
    from scorer.CA_DISPLAY (ca_places.json stores coordinates only)."""
    q=(request.args.get("q") or "").strip()
    nq=scorer._norm(q.split(",")[0]) if q else ""
    if len(nq)<2: return jsonify([])
    # optional 2-letter region hint after the comma, e.g. "london, ON" -> prefer Ontario
    parts=[p.strip() for p in q.split(",")]
    want=parts[-1].upper() if len(parts)>=2 and len(parts[-1].strip())==2 else None
    hits=set()
    for k,v in scorer.PLACES.items():
        st,nm=k.split("|",1)
        if nm.startswith(nq) and len(v)>=3:
            hits.add(v[2]+", "+st)
    for k in scorer.CA_PLACES:
        if "|" not in k: continue                       # bare-name duplicates
        st,nm=k.split("|",1)
        if nm.startswith(nq):
            hits.add((scorer.CA_DISPLAY.get(nm) or nm.title())+", "+st)
    def rank(x):
        st=x.rsplit(", ",1)[-1]
        return (0 if (want and st==want) else 1, len(x), x)
    return jsonify(sorted(hits, key=rank)[:10])

@app.route("/match", methods=["POST","OPTIONS"])
def match():
    if request.method=="OPTIONS":
        return ("",204)
    crit=request.get_json(force=True,silent=True) or {}
    try: top=int(request.args.get("top",5))
    except ValueError: top=5
    stripped=strip_labor(crit) if not labor_allowed() else False
    out=scorer.run(crit,top=top)
    if stripped: out["labor_access"]="workforce.occupations ignored: occupation labor scoring is part of AI+Plus"
    return jsonify(out)

@app.route("/labor/rank", methods=["POST","OPTIONS"])
def labor_rank():
    if request.method=="OPTIONS": return ("",204)
    if not labor_allowed(): return jsonify(LABOR_DENIED),403
    crit=request.get_json(force=True,silent=True) or {}
    try: top=max(1,min(int(request.args.get("top",10)),100))
    except ValueError: top=10
    return jsonify(labor.rank_metros(crit,top=top,include_nonmetro=request.args.get("nonmetro") in ("1","true")))

@app.route("/labor/compare", methods=["POST","OPTIONS"])
def labor_compare():
    if request.method=="OPTIONS": return ("",204)
    if not labor_allowed(): return jsonify(LABOR_DENIED),403
    crit=request.get_json(force=True,silent=True) or {}
    return jsonify(scorer.compare_sites(crit,crit.get("sites") or []))

@app.get("/labor/occupations")
def labor_occupations():
    if not labor_allowed(): return jsonify(LABOR_DENIED),403
    q=(request.args.get("q") or "").strip().lower()
    idx=labor.index()
    if not idx or len(q)<2: return jsonify([])
    hits=[{"country":"US","code":c,"title":t} for c,t in idx["us_titles"].items() if q in t.lower() or c.startswith(q)]
    hits+=[{"country":"CA","code":c,"title":t,"soc_code":idx["noc_to_soc"].get(c)}
           for c,t in idx["ca_titles"].items() if q in t.lower() or c.startswith(q)]
    hits.sort(key=lambda h:(not h["title"].lower().startswith(q),h["country"]!="US",h["title"]))
    return jsonify(hits[:25])

@app.route("/labor/parse", methods=["POST","OPTIONS"])
def labor_parse():
    if request.method=="OPTIONS": return ("",204)
    if not labor_allowed(): return jsonify(LABOR_DENIED),403
    text=((request.get_json(force=True,silent=True) or {}).get("text") or "")[:4000]
    return jsonify(labor_intake.parse(text))

if __name__=="__main__":
    os.environ.setdefault("FL_LABOR_OPEN","1")      # local dev: labor routes open unless FL_LABOR_KEY is set
    print("FastLocations scoring API -> http://127.0.0.1:8000  (Ctrl+C to stop)")
    app.run(host="127.0.0.1",port=8000,debug=False)
