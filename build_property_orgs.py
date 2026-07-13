#!/usr/bin/env python3
r"""
build_property_orgs.py
----------------------
Regenerates orgs_with_properties.json from the dashboard's properties.js.

properties.js holds a `DATASETS = [ { label: 'Hoosier Energy, IN', ... }, ... ]` array
-- the live list of organizations that have properties listed on the FastLocations dashboard.
This script parses those labels, resolves each to its EDO `objectid` in
edo_master_table_dual.json, and writes the objectids the scorer reads to award the
property-access bonus (see PROPERTY_BONUS in scorer.py).

Run it whenever you add/remove a dataset in properties.js:

    python build_property_orgs.py "C:\path\to\properties.js"

If a label can't be resolved with confidence it is written to "unresolved" and printed,
so you can add an entry to OVERRIDES below (label -> objectid) or fix the source label.
Matching is deterministic; no network, no LLM.
"""
import json, os, re, sys, unicodedata, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
MASTER_PATH = os.path.join(HERE, "edo_master_table_dual.json")
OUT_PATH = os.path.join(HERE, "orgs_with_properties.json")

# Hard-coded resolutions for labels the fuzzy matcher can't nail (acronyms, brand names,
# or genuinely ambiguous cases). label (verbatim from properties.js) -> objectid or [objectids].
OVERRIDES = {
    "VEDP, VA": "5",           # Virginia Economic Development Partnership (acronym, no token overlap)
    "NE Indiana": "4379",      # Northeast Indiana Regional Partnership ("NE" abbreviation)
    "Bay EDA, CA": "53",       # Bay Economic Development Alliance (FL) -- data is Panama City Beach FL; label ", CA" appears to be a typo
    "REDI Blue Ash, OH": "4449",  # REDI Cincinnati manages the Blue Ash listings (regional EDO customer)
    "Alliant Energy": ["4429", "4538"],  # Alliant serves both IA (#4429) and WI (#4538); credit both territories
}

US_ST = {'AL','AK','AZ','AR','CA','CO','CT','DE','FL','GA','HI','ID','IL','IN','IA','KS','KY','LA',
'ME','MD','MA','MI','MN','MS','MO','MT','NE','NV','NH','NJ','NM','NY','NC','ND','OH','OK','OR','PA',
'RI','SC','SD','TN','TX','UT','VT','VA','WA','WV','WI','WY','DC',
'AB','BC','MB','NB','NL','NS','NT','NU','ON','PE','QC','SK','YT'}
STMAP = {'colorado':'CO','ontario':'ON','iowa':'IA','indiana':'IN','virginia':'VA','wyoming':'WY',
'kentucky':'KY','pennsylvania':'PA','maine':'ME','massachusetts':'MA','delaware':'DE',
'nova scotia':'NS','north dakota':'ND','seattle':'WA','florida':'FL','illinois':'IL'}
STOP = {"the","of","inc","llc","edc","eda","edo","development","economic","corporation","corp",
"county","authority","alliance","partnership","commission","group","agency","office","dept",
"department","doc","energy","power","company","co","and","for","greater","area","region",
"regional","board","chamber","commerce","city","project"}

def norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9 ]", " ", s)

def toks(s):
    return set(w for w in norm(s).split() if w and w not in STOP)

def label_state(lab):
    parts = [p.strip() for p in lab.split(",")]
    if len(parts) >= 2 and parts[-1].upper() in US_ST:
        return parts[-1].upper()
    low = norm(lab)
    for name, ab in STMAP.items():
        if name in low:
            return ab
    return None

def parse_labels(js_path):
    """Extract active DATASETS labels from properties.js (skips commented-out lines and NAV links)."""
    js = open(js_path, encoding="utf-8").read()
    i = js.index("DATASETS")
    block = js[i: js.index("];", i)]
    labels = []
    for m in re.finditer(r"label:\s*'([^']+)'|label:\s*\"([^\"]+)\"", block):
        line = block[block.rfind("\n", 0, m.start()) + 1: m.start()]
        if line.strip().startswith("//"):        # commented-out dataset
            continue
        lab = m.group(1) or m.group(2)
        if lab and lab != "All Properties":
            labels.append(lab)
    # de-dup, preserve order
    seen = set(); out = []
    for l in labels:
        if l not in seen:
            seen.add(l); out.append(l)
    return out

def best_match(lab, master):
    lst = label_state(lab)
    lt = toks(lab)
    best = None
    for r in master:
        ov = len(lt & toks(r["organization"]))
        if ov == 0:
            continue
        sc = ov * 2 - abs(len(toks(r["organization"])) - len(lt)) * 0.1
        if lst and r.get("state") == lst:
            sc += 1.5
        if best is None or sc > best[0]:
            best = (sc, ov, lst, r)
    return best

def main():
    js_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "properties.js")
    if not os.path.exists(js_path):
        sys.exit(f"properties.js not found at {js_path!r}. Pass its path as the first argument.")
    master = json.load(open(MASTER_PATH, encoding="utf-8"))
    by_id = {r["objectid"]: r for r in master}
    labels = parse_labels(js_path)

    resolved, unresolved = [], []
    for lab in labels:
        if lab in OVERRIDES:
            ov_val = OVERRIDES[lab]
            for oid in ([ov_val] if isinstance(ov_val, str) else ov_val):
                r = by_id.get(oid)
                resolved.append({"label": lab, "objectid": oid,
                                 "organization": r["organization"] if r else "(objectid not in master!)",
                                 "state": r["state"] if r else None, "confidence": "override"})
            continue
        b = best_match(lab, master)
        # accept when the state agrees (ov>=1) or token overlap is strong (ov>=2)
        if b and ((b[2] and b[3].get("state") == b[2] and b[1] >= 1) or b[1] >= 2):
            r = b[3]
            resolved.append({"label": lab, "objectid": r["objectid"], "organization": r["organization"],
                             "state": r["state"], "confidence": ("high" if b[1] >= 2 else "state+token")})
        else:
            cand = b[3] if b else None
            unresolved.append({"label": lab,
                               "best_guess": (f'#{cand["objectid"]} {cand["organization"]}' if cand else None)})

    objectids = sorted({d["objectid"] for d in resolved}, key=lambda x: int(x))
    out = {
        "_comment": "EDO objectids whose territory currently has properties listed on the FastLocations "
                    "dashboard. Regenerated from properties.js by build_property_orgs.py. The scorer gives "
                    "counties served by one of these EDOs a moderate property-access bonus.",
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "source": os.path.basename(js_path),
        "objectids": objectids,
        "resolved": resolved,
        "unresolved": unresolved,
    }
    json.dump(out, open(OUT_PATH, "w", encoding="utf-8"), indent=2, ensure_ascii=False)

    print(f"Parsed {len(labels)} datasets -> {len(objectids)} distinct EDO objectids "
          f"({len(resolved)} resolved, {len(unresolved)} unresolved)\n")
    for d in resolved:
        print(f"  OK   {d['label']:<26} -> #{d['objectid']:>4} {d['state']}  {d['organization']}  [{d['confidence']}]")
    if unresolved:
        print("\n  UNRESOLVED (add to OVERRIDES or fix the label in properties.js):")
        for d in unresolved:
            print(f"    ??  {d['label']}   best guess: {d['best_guess']}")
    print(f"\nWrote {OUT_PATH}")

if __name__ == "__main__":
    main()
