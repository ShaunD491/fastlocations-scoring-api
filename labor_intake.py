#!/usr/bin/env python3
"""
labor_intake.py - plain-English roles -> occupations (Phase 4 of Occupation_Labor_Data_Plan.md).

    parse("we need 120 forklift operators and 10 maintenance techs")
    -> [{"text": "forklift operators", "headcount": 120, "pick": "53-7051", "confidence": "exact",
         "candidates": [{"soc_code", "title", "noc_codes", "matched_title", "score"}, ...]}, ...]

Deterministic. Two steps:
 1. Split the text into role mentions and read each headcount ("120 forklift operators",
    "forklift operators x 120", "CNC machinists (200)", "a dozen welders"). A mention with no number
    keeps headcount null; the user fills it in.
 2. Match each role against ~72,000 job titles: O*NET 30.2 alternate / reported titles (-> SOC) and the
    Canadian NOC example titles (-> NOC, and its SOC). Exact title first, otherwise a token overlap
    score (rare words count more, and titles that are mostly the query beat long titles that merely
    contain it). Candidates are grouped by SOC, best first.

confidence: "exact"  - the title is a known job title with a single SOC
            "good"   - a clear best candidate: score >= GOOD and GAP ahead of the next, or tied on score but
                       backed by at least twice as many matching titles (support)
            "ambiguous" / "none" - the portal asks Claude to choose among the candidates (EDO/api.php
            labor_parse) and always shows the user the matched code and title to correct. Scoring
            itself never involves the model.
"""
import functools
import gzip
import json
import math
import os
import re

import labor

GOOD, GAP, MAX_CANDIDATES, MAX_ROLES = 0.75, 0.15, 6, 12

ABBREV = {"tech": "technician", "techs": "technician", "mech": "mechanic", "maint": "maintenance",
          "mfg": "manufacturing", "eng": "engineer", "engr": "engineer", "mgr": "manager", "mgmt": "management",
          "admin": "administrative", "admins": "administrative", "asst": "assistant", "assoc": "associate",
          "rep": "representative", "reps": "representative", "dev": "developer", "devs": "developer",
          "sw": "software", "qa": "quality", "qc": "quality", "hr": "human resources", "it": "information technology",
          "cdl": "heavy truck", "rn": "registered nurse", "rns": "registered nurse", "lpn": "licensed practical nurse",
          "csr": "customer service representative", "ops": "operations", "elec": "electrical", "supv": "supervisor",
          "acct": "accountant", "dept": "department", "temp": "", "temps": "", "sr": "", "jr": "", "senior": "",
          "junior": "", "lead": "", "entry": "", "level": ""}
STOP = {"a", "an", "the", "of", "and", "or", "for", "to", "in", "on", "with", "&", "staff", "person", "people",
        "employee", "position", "role", "job", "hire", "new", "additional", "experienced", "skilled", "qualified",
        "full", "part", "time", "fulltime", "parttime", "shift", "general"}
FILLER = re.compile(r"^(?:we(?:'ll| will)?\s+)?(?:(?:also|will|would|need(?:s)?|needing|want|plan|planning|expect(?:ing)?|"
                    r"to|hire|hiring|recruit(?:ing)?|looking|for|about|approximately|approx\.?|around|roughly|some|"
                    r"up|at least|min(?:imum)?|~|plus|another|an?|of)\s+)*", re.I)
NUMWORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
            "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
            "fifty": 50, "hundred": 100, "a dozen": 12, "dozen": 12, "a couple of": 2, "a couple": 2, "a few": 3}
_NUMALT = r"\d[\d,]*(?:\.\d+)?\s*k?|" + "|".join(sorted((re.escape(k) for k in NUMWORDS), key=len, reverse=True))
NUM = "(" + _NUMALT + ")"
NUM_NC = "(?:" + _NUMALT + ")"     # for re.split: a capturing group would come back as a part


def _num(s):
    s = s.strip().lower()
    if s in NUMWORDS:
        return NUMWORDS[s]
    k = s.endswith("k")
    try:
        v = float(s.rstrip("k").replace(",", "").strip())
    except ValueError:
        return None
    return int(round(v * (1000 if k else 1)))


def _sing(w):
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


def tokens(text):
    out = []
    for w in re.sub(r"[^a-z0-9+#]+", " ", (text or "").lower()).split():
        w = ABBREV.get(w, w)
        for x in w.split():
            x = _sing(x)
            if x and x not in STOP:
                out.append(x)
    return out


def key(text):
    return " ".join(tokens(text))


@functools.lru_cache(maxsize=1)
def _index():
    p = os.path.join(labor.LABOR_DIR, "titles.json.gz")
    if not os.path.exists(p):
        return None
    with gzip.open(p, "rt", encoding="utf-8") as fh:
        raw = json.load(fh)
    by_key, inv = {}, {}
    for _, (title, socs, nocs) in raw.items():
        k = key(title)
        if not k:
            continue
        e = by_key.setdefault(k, {"title": title, "socs": [], "nocs": []})
        e["socs"] = sorted(set(e["socs"]) | set(socs))
        e["nocs"] = sorted(set(e["nocs"]) | set(nocs))
    for k in by_key:
        for t in set(k.split()):
            inv.setdefault(t, []).append(k)
    n = len(by_key)
    idf = {t: math.log(1 + n / len(ks)) for t, ks in inv.items()}
    return {"by_key": by_key, "inv": inv, "idf": idf}


# ----------------------------------------------------------------------------- 1. split + headcount
def split_roles(text):
    text = re.sub(r"\s+", " ", (text or "").replace("\n", ";")).strip()
    parts = []
    for chunk in re.split(r"[;,/]|\.\s|\.$|\band then\b", text):
        # " and " separates roles only when a number follows it ("... and 10 maintenance techs"),
        # so titles such as "heating and air conditioning mechanics" stay whole
        parts.extend(re.split(r"\s+(?:and|plus|as well as|also)\s+(?=(?:about |around |approximately |another )?" + NUM_NC + r"\b)",
                              chunk, flags=re.I))
    out = []
    for p in parts:
        p = p.strip(" .:-")
        if not p or p.lower() in ("and", "plus"):
            continue
        hc, role = None, p
        m = re.match(r"^(.*?)\b" + NUM + r"\b\s*(?:x\s+|×\s*)?(.*)$", p, re.I)
        if m and m.group(3).strip():
            hc, role = _num(m.group(2)), (m.group(1) + " " + m.group(3))
        else:
            m = re.match(r"^(.*?)\s*(?:[x×:=\-–(]\s*)" + NUM + r"\s*\)?\s*(?:people|workers|staff|positions|hires|ftes?)?\s*$", p, re.I)
            if m:
                hc, role = _num(m.group(2)), m.group(1)
        role = FILLER.sub("", role.strip()).strip(" .:-()")
        role = re.sub(r"^(?:need|hire)\s+", "", role, flags=re.I)
        if not tokens(role):
            continue
        out.append({"text": role, "headcount": hc})
    return out[:MAX_ROLES]


# ----------------------------------------------------------------------------- 2. match
def match(role):
    ix = _index()
    idx = labor.index()
    if not ix or not idx:
        return {"candidates": [], "pick": None, "confidence": "none"}
    q = tokens(role)
    k = " ".join(q)
    scored = {}
    exact = ix["by_key"].get(k)
    if exact:
        for s in exact["socs"]:
            scored[s] = (1.0, exact["title"], exact["nocs"])
    qset = set(q)
    support = {}         # how many titles holding every query word point at each SOC (title evidence)
    wq = sum(ix["idf"].get(t, 0) for t in qset) or 1.0
    seen = set()
    for t in qset:
        for tk in ix["inv"].get(t, []):
            if tk in seen:
                continue
            seen.add(tk)
            tt = set(tk.split())
            hit = qset & tt
            cov_q = sum(ix["idf"][x] for x in hit) / wq
            cov_t = sum(ix["idf"][x] for x in hit) / (sum(ix["idf"][x] for x in tt) or 1.0)
            sc = round(cov_q * (0.6 + 0.4 * cov_t), 4)
            if sc < 0.3:
                continue
            e = ix["by_key"][tk]
            if hit == qset:
                for s in e["socs"]:
                    support[s] = support.get(s, 0.0) + 1.0 / len(e["socs"])
            for s in e["socs"]:
                if s not in scored or sc > scored[s][0] or (sc == scored[s][0] and e["title"] < scored[s][1]):
                    if not (s in scored and scored[s][0] == 1.0):
                        scored[s] = (min(sc, 0.99), e["title"], e["nocs"])
    # an occupation whose OFFICIAL title holds every query word ("welders" -> Welders, Cutters, ...) is the
    # natural reading, so it gets a strong evidence boost
    for s in scored:
        if qset <= set(tokens(idx["us_titles"].get(s, ""))):
            support[s] = support.get(s, 0.0) + 100.0
    # ties (common: O*NET lists "forklift operator" under several SOCs) go to the SOC that more of the
    # matching titles point at
    ranked = sorted(scored.items(), key=lambda x: (-x[1][0], -support.get(x[0], 0.0), x[0]))[:MAX_CANDIDATES]
    cands = [{"soc_code": s, "title": idx["us_titles"].get(s), "matched_title": t, "score": round(sc, 3),
              "support": round(support.get(s, 0.0), 2),
              "noc_codes": sorted(set(n) | set(idx["soc_to_noc"].get(s, [])))} for s, (sc, t, n) in ranked]
    if not cands:
        return {"candidates": [], "pick": None, "confidence": "none"}
    top = cands[0]["score"]
    nxt = cands[1]["score"] if len(cands) > 1 else 0.0
    sup = [c["support"] for c in cands]
    if exact and len(exact["socs"]) == 1:
        conf = "exact"
    elif top >= GOOD and len(cands) > 1 and cands[1]["score"] >= top - 1e-9 and sup[0] >= max(2.0, 2 * sup[1]):
        conf = "good"          # tied on score but clearly favoured by title evidence
    elif top >= GOOD and top - nxt >= GAP:
        conf = "good"
    else:
        conf = "ambiguous"
    return {"candidates": cands, "pick": cands[0]["soc_code"] if conf != "ambiguous" else None, "confidence": conf}


def parse(text):
    roles = []
    for r in split_roles(text):
        m = match(r["text"])
        roles.append({**r, **m})
    return {"roles": roles, "data_built": (labor.index() or {}).get("built")}
