#!/usr/bin/env python3
"""
check_regional_balance.py -- how evenly the Top 5 spreads across US regions.

Runs a nationwide US search under the default weights and every use-type preset in intake.js, and
reports each region's share of the Top-5 slots against its share of US population. Each share is
shown twice: as the form serves it (with the regional spread, scorer.MAX_PER_DIVISION) and on the
scores alone (spread off), so you can see how much of the balance the spread rule is supplying.
It also reports customer-EDO coverage per region, because only counties served by a customer EDO
can appear in Top Matches, so that coverage is the main driver of the balance.

Re-run it as the EDO list changes after launch:

    python check_regional_balance.py              # print the report
    python check_regional_balance.py --save       # also save a dated snapshot
    python check_regional_balance.py --compare    # show the change since the latest saved snapshot

Snapshots go to reports/regional_balance/YYYY-MM-DD.json. The regions are the ones the balance
question was asked in, not Census divisions (those are what the spread rule uses).
"""
import collections, datetime, glob, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import scorer

REGIONS = {"Northeast": "ME NH VT MA RI CT NY NJ PA", "Northwest": "WA OR ID MT",
           "Mid-Atlantic": "DE MD DC VA WV", "Southeast": "NC SC GA FL TN AL MS"}
REGION_OF = {st: rg for rg, sts in REGIONS.items() for st in sts.split()}
ORDER = list(REGIONS) + ["Rest of US"]
TOP = 5                                   # what the intake form requests
LOW_COVERAGE = 50                         # flag states where customer EDOs reach less than this % of people
SNAP_DIR = os.path.join(HERE, "reports", "regional_balance")

def region(st): return REGION_OF.get(st, "Rest of US")

def scenarios():
    """Default weights plus every use-type preset, parsed from intake.js (the same parse test_scorer.py uses)."""
    js = open(os.path.join(HERE, "intake.js"), encoding="utf-8").read()
    start = js.index("const USE_PRESETS")
    block = js[start:js.index("};", start)]
    out = {"default": None}
    for name, body in re.findall(r"^\s*(\w+):\s*\{([^}]*)\}", block, re.M):
        out[name] = {k: int(v) / 100 for k, v in re.findall(r"(\w+):\s*(\d+)", body)}
    return out

def top5(weights, spread):
    crit = {"geography": {"countries": ["US"]}}
    if weights: crit["weights"] = weights
    saved = scorer.MAX_PER_DIVISION
    scorer.MAX_PER_DIVISION = saved if spread else 0
    try:
        return scorer.run(crit, top=TOP)["results"]
    finally:
        scorer.MAX_PER_DIVISION = saved

def measure():
    us = {f: d for f, d in scorer.FEAT.items() if scorer.gsys(d) == "US"}
    idx = scorer.index_for("US")
    pop = collections.Counter(); served = collections.Counter()
    st_pop = collections.Counter(); st_served = collections.Counter()
    for f, d in us.items():
        p = d.get("TOTPOP_CY") or 0; st = d["ST_ABBREV"]
        pop[region(st)] += p; st_pop[st] += p
        if idx.get(f): served[region(st)] += p; st_served[st] += p
    total = sum(pop.values())
    slots = {"spread": collections.Counter(), "scores_only": collections.Counter()}
    lists = {}
    scen = scenarios()
    for name, w in scen.items():
        for mode in slots:
            R = top5(w, mode == "spread")
            slots[mode].update(region(r["state"]) for r in R)
            if mode == "spread":
                lists[name] = ["%s, %s" % (r["county"], r["state"]) for r in R]
    n = TOP * len(scen)
    return {
        "date": datetime.date.today().isoformat(),
        "scenarios": list(scen),
        "population_share": {rg: round(100 * pop[rg] / total, 1) for rg in ORDER},
        "top5_share": {m: {rg: round(100 * c[rg] / n, 1) for rg in ORDER} for m, c in slots.items()},
        "edo_coverage": {rg: round(100 * served[rg] / pop[rg], 1) if pop[rg] else None for rg in ORDER},
        "low_coverage_states": {st: round(100 * st_served[st] / st_pop[st]) for st in sorted(st_pop)
                                if st_pop[st] and 100 * st_served[st] / st_pop[st] < LOW_COVERAGE},
        "top5_lists": lists,
    }

def report(m, prev=None):
    def row(label, vals, fmt="%13.0f%%"):
        print("%-34s" % label + "".join(fmt % v if v is not None else "%14s" % "-" for v in vals))
    def delta(key, sub=None):
        if not prev: return
        cur = m[key][sub] if sub else m[key]; old = prev[key][sub] if sub else prev[key]
        row("  change since " + prev["date"], [(cur[rg] or 0) - (old.get(rg) or 0) for rg in ORDER], "%+10.0f pts")
    print("Regional balance of the Top %d  (%s; US-only searches, %d scenarios: %s)\n"
          % (TOP, m["date"], len(m["scenarios"]), ", ".join(m["scenarios"])))
    print("%-34s" % "" + "".join("%14s" % rg for rg in ORDER))
    row("Share of US population", [m["population_share"][rg] for rg in ORDER])
    row("Share of Top-5 slots (as served)", [m["top5_share"]["spread"][rg] for rg in ORDER]); delta("top5_share", "spread")
    row("Share of Top-5 slots (scores only)", [m["top5_share"]["scores_only"][rg] for rg in ORDER]); delta("top5_share", "scores_only")
    row("Population with a customer EDO", [m["edo_coverage"][rg] for rg in ORDER]); delta("edo_coverage")
    print("\nStates where customer EDOs reach under %d%% of people (their counties mostly can't appear):" % LOW_COVERAGE)
    print("  " + ("  ".join("%s %d%%" % kv for kv in m["low_coverage_states"].items()) or "none"))
    if prev:
        gained = sorted(set(prev["low_coverage_states"]) - set(m["low_coverage_states"]))
        if gained: print("  Now above %d%% since %s: %s" % (LOW_COVERAGE, prev["date"], ", ".join(gained)))
    print("\nTop 5 as served, per scenario:")
    for name, L in m["top5_lists"].items():
        print("  %-24s %s" % (name, "; ".join(L)))

def latest_snapshot(exclude_date):
    files = sorted(f for f in glob.glob(os.path.join(SNAP_DIR, "*.json"))
                   if os.path.basename(f) != exclude_date + ".json")
    return json.load(open(files[-1], encoding="utf-8")) if files else None

if __name__ == "__main__":
    args = set(sys.argv[1:])
    unknown = args - {"--save", "--compare"}
    if unknown: sys.exit("unknown option(s): %s\n%s" % (" ".join(sorted(unknown)), __doc__))
    m = measure()
    prev = None
    if "--compare" in args:
        prev = latest_snapshot(m["date"])
        if not prev: print("(no earlier snapshot in %s to compare with)\n" % SNAP_DIR)
    report(m, prev)
    if "--save" in args:
        os.makedirs(SNAP_DIR, exist_ok=True)
        path = os.path.join(SNAP_DIR, m["date"] + ".json")
        with open(path, "w", encoding="utf-8") as fh: json.dump(m, fh, indent=2)
        print("\nSaved %s" % os.path.relpath(path, HERE))
