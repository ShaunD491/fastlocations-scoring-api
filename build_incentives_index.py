#!/usr/bin/env python3
r"""
build_incentives_index.py
-------------------------
Builds incentives_index.json — the scorer's summary of what each state and
province offers — from the master collected by the Incentives Search Tool.

WHAT THIS REPLACES
    The previous version of this file could not run at all. It carried
    hard-coded sandbox paths:

        P = "/sessions/modest-upbeat-mendel/mnt/Projects/incentives.json"

    so the index had been frozen since it was last built by hand, while the
    Incentives Search Tool on port 5001 kept collecting. The scorer was ranking
    the incentives dimension on 1,557 programs against the 6,930 the tool had
    already verified — 22% of the evidence.

SOURCE
    incentives/tool's master output, by default
        ../incentives/JSON_test/incentives_all_test.json
    which carries US states AND Canadian provinces (the published
    all_states_*.json is split by country and is US-only, so it cannot serve a
    scorer whose index is keyed AB, BC, ON as well as AL, AK, AZ).

WHAT THE SCORER READS (scorer.m_incentives)
    type_counts     per-type program counts; priority_match caps each at 3, so
                    "does this state do X at all, and more than token depth"
    type_diversity  how many of the 15 types are present at all
    value_tier      1-4 from the largest program advertised
    max_value_usd   scores value_target_fit against the project's stated target

TYPE DETECTION, and the bug worth knowing about
    The master's "Type of Incentive" is free text with 547 distinct values
    ("Grant", "Loan / Grant Guarantee", "Tax Credit/Tax Exemption"...), so types
    are matched by keyword across type + program name + description.

    SHORT ACRONYMS MUST MATCH ON WORD BOUNDARIES. Matching "tif" as a plain
    substring hits "cer-tif-ied", "cer-tif-ication" and "mul-tif-amily": of 396
    substring matches, 347 were false positives. The same applies to "ftz" and
    "r&d". Those patterns are anchored with \b; multi-word phrases are matched
    plainly.

WHAT IMPROVES, measured against the index this replaces
    jurisdictions offering each type, old -> new:

        property_tax_abatement  34 -> 61      payroll_rebate       21 -> 49
        fast_track_permitting    3 -> 32      enterprise_zone      30 -> 52
        foreign_trade_zone       3 -> 24      rd_credit            48 -> 65
        sales_tax_exemption     37 -> 47      tif                  42 -> 30

    tif is the one that goes DOWN, and that is the bug being fixed rather than a
    loss: the old 42 counted "cer-tif-ied" and "mul-tif-amily". The 30 that
    remain hold 80 real programs - Tax Increment Financing, Tax Allocation
    Districts, Alberta's Community Revitalization Levy. Note this counts states
    that list TIF as a discrete PROGRAM; nearly every state enables TIF by
    statute for local use, which is a different question and not what the
    scorer is ranking.

    The old near-zero counts for fast-track permitting and foreign trade zones
    were detection failures, not facts about the country - there are roughly 190
    FTZs in the United States.

    land_writedown stays genuinely rare (3 programs). That is not a detection
    gap: land discounts are local, site-specific deals, and this master covers
    the state/provincial and utility tiers. A project ranking it first should
    expect it to separate almost nothing.

    Coverage also goes from 61 jurisdictions to 65 - the old index had no
    District of Columbia and none of the three Canadian territories, so those
    scored null on the whole incentives dimension.

USAGE
    python build_incentives_index.py
    python build_incentives_index.py --master ../incentives/JSON_test/incentives_all_test.json
    python build_incentives_index.py --compare     # report the delta, write nothing
"""
import argparse, collections, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "incentives_index.json")
DEFAULT_MASTER = os.path.normpath(
    os.path.join(HERE, "..", "incentives", "JSON_test", "incentives_all_test.json"))

# Patterns starting with \b are regex; the rest are plain substrings. Short
# acronyms MUST be anchored - see the docstring.
KW = {
    "property_tax_abatement": ["tax abatement", "property tax", "abatement",
                               r"\bpilot\b", "payment in lieu"],
    "job_training_grant":     ["workforce training", "job training", "training",
                               "apprentice", "skills", "workforce development"],
    "cash_grant":             ["grant", "cash rebate", "matching fund", "forgivable"],
    "tax_credit":             ["tax credit", "investment credit", "jobs credit"],
    "tif":                    [r"\btif\b", "tax increment", "increment financing"],
    "utility_rate":           ["rate discount", "utility rate", "economic development rate",
                               "rate reduction", "energy rate", "rate rider", "electric rate"],
    "fast_track_permitting":  ["permit", "expedited", "fast-track", "fast track",
                               "streamlined review", "one-stop", "shovel-ready",
                               "shovel ready", "pre-certified", "certified site"],
    "sales_tax_exemption":    ["sales tax", "use tax", "sales/use", "sales and use"],
    "infrastructure_grant":   ["infrastructure", "rail spur", "water and sewer",
                               "site development"],
    "payroll_rebate":         ["payroll", "withholding", "wage rebate", "wage subsidy"],
    "rd_credit":              [r"\br&d\b", "research and development",
                               "research & development", "sr&ed", "scientific research"],
    "enterprise_zone":        ["enterprise zone", "opportunity zone", "empowerment zone",
                               "renaissance zone", "enterprise community"],
    "foreign_trade_zone":     ["foreign trade zone", "foreign-trade zone",
                               "free trade zone", r"\bftz\b"],
    "low_interest_loan":      ["loan", "bond", "revolving", "financing", "debenture"],
    "land_writedown":         ["land write", "write-down", "writedown", "land discount",
                               "land donation", "free land", "discounted land",
                               "site donation", "reduced land", "land price", "land grant"],
}
TYPES = list(KW)

JURISDICTIONS = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "District of Columbia": "DC", "Florida": "FL", "Georgia": "GA", "Hawaii": "HI",
    "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
    "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
    "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI",
    "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX",
    "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
    "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
    "Puerto Rico": "PR",
    "Alberta": "AB", "British Columbia": "BC", "Manitoba": "MB",
    "New Brunswick": "NB", "Newfoundland and Labrador": "NL",
    "Northwest Territories": "NT", "Nova Scotia": "NS", "Nunavut": "NU",
    "Ontario": "ON", "Prince Edward Island": "PE", "Quebec": "QC",
    "Saskatchewan": "SK", "Yukon": "YT",
}

# Largest single advertised incentive packages run into the low billions
# (Micron's New York award was about $5.5B). Anything above this is a parse
# artefact of the free-text amount field, not a program.
MAX_PLAUSIBLE_USD = 10_000_000_000

MULT = {"thousand": 1e3, "k": 1e3, "million": 1e6, "m": 1e6, "billion": 1e9,
        "b": 1e9, "trillion": 1e12}
AMOUNT = re.compile(
    r"\$\s*([\d,]+(?:\.\d+)?)\s*(thousand|million|billion|trillion|[kmb])?\b",
    re.I)


def parse_max_usd(text):
    """Largest dollar figure in a free-text amount range, or None."""
    if not text:
        return None
    best = None
    for num, unit in AMOUNT.findall(text):
        try:
            v = float(num.replace(",", ""))
        except ValueError:
            continue
        if unit:
            v *= MULT.get(unit.lower(), 1.0)
        if v > MAX_PLAUSIBLE_USD:
            continue                      # parse artefact, not a program
        if best is None or v > best:
            best = v
    return best


def value_tier(mx):
    """1-4, on the thresholds the previous index used."""
    if not mx:
        return 1
    if mx < 1e6:
        return 1
    if mx < 1e7:
        return 2
    if mx < 1e8:
        return 3
    return 4


def compile_kw():
    out = {}
    for t, pats in KW.items():
        out[t] = [(re.compile(p, re.I) if p.startswith("\\b") else p) for p in pats]
    return out


def types_of(rec, kw):
    blob = " ".join([rec.get("Type of Incentive") or "",
                     rec.get("Program/Incentive Name") or "",
                     rec.get("Brief Description") or ""]).lower()
    hits = []
    for t, pats in kw.items():
        for p in pats:
            if (p.search(blob) if hasattr(p, "search") else p in blob):
                hits.append(t)
                break
    return hits


def main():
    ap = argparse.ArgumentParser(description="Rebuild incentives_index.json")
    ap.add_argument("--master", default=DEFAULT_MASTER)
    ap.add_argument("--compare", action="store_true",
                    help="report the delta against the current index, write nothing")
    a = ap.parse_args()

    if not os.path.exists(a.master):
        sys.exit("incentives master not found: %s\n"
                 "It is produced by the Incentives Search Tool (port 5001)."
                 % a.master)
    with open(a.master, encoding="utf-8", errors="replace") as fh:
        master = json.load(fh)
    print("Read %d programs from %s" % (len(master), os.path.basename(a.master)))

    kw = compile_kw()
    per = collections.defaultdict(lambda: {"count": 0,
                                           "tc": collections.Counter(),
                                           "max": None})
    unknown = collections.Counter()
    for r in master:
        name = (r.get("State") or "").strip()
        code = JURISDICTIONS.get(name)
        if not code:
            unknown[name] += 1
            continue
        p = per[code]
        p["count"] += 1
        for t in types_of(r, kw):
            p["tc"][t] += 1
        mx = parse_max_usd(r.get("Amount Range"))
        if mx is not None and (p["max"] is None or mx > p["max"]):
            p["max"] = mx

    if unknown:
        print("  [!] %d program(s) in jurisdictions with no code mapping: %s"
              % (sum(unknown.values()),
                 ", ".join("%s x%d" % (k or "(blank)", v)
                           for k, v in unknown.most_common(6))))

    index = {}
    for code, p in per.items():
        tc = {t: p["tc"].get(t, 0) for t in TYPES}
        index[code] = {
            "count": p["count"],
            "types_present": {t: tc[t] > 0 for t in TYPES},
            "type_counts": tc,
            "type_diversity": sum(1 for t in TYPES if tc[t] > 0),
            "max_value_usd": int(p["max"]) if p["max"] else None,
            "value_tier": value_tier(p["max"]),
        }

    old = {}
    if os.path.exists(OUT):
        try:
            with open(OUT, encoding="utf-8") as fh:
                old = json.load(fh)
        except Exception:
            old = {}

    print("")
    print("  jurisdictions %d -> %d,  programs %d -> %d"
          % (len(old), len(index),
             sum(v.get("count", 0) for v in old.values()),
             sum(v["count"] for v in index.values())))
    gained = sorted(set(index) - set(old))
    if gained:
        print("  newly covered (previously scored null): %s" % ", ".join(gained))
    print("")
    print("  %-24s %12s %12s" % ("type", "jurisd old", "jurisd new"))
    for t in TYPES:
        o = sum(1 for v in old.values() if (v.get("type_counts") or {}).get(t))
        n = sum(1 for v in index.values() if v["type_counts"][t])
        # only call it under-detection when the old count was materially wrong,
        # not when both numbers are tiny (land_writedown going 0 -> 3 is a rare
        # program type, not a fixed bug)
        flag = ""
        if o and n >= o * 3:
            flag = "  <-- old index under-detected"
        elif n < o:
            flag = "  <-- old index OVER-counted (substring false positives)"
        print("  %-24s %12d %12d%s" % (t, o, n, flag))

    if a.compare:
        print("")
        print("--compare: nothing written.")
        return

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(index, fh, separators=(",", ":"), sort_keys=True)
    print("")
    print("Wrote %s: %d jurisdictions" % (OUT, len(index)))
    tiers = collections.Counter(v["value_tier"] for v in index.values())
    print("  value_tier spread: %s"
          % ", ".join("%d:%d" % (k, tiers[k]) for k in sorted(tiers)))
    div = sorted(v["type_diversity"] for v in index.values())
    print("  type_diversity  min %d  median %d  max %d"
          % (div[0], div[len(div) // 2], div[-1]))


if __name__ == "__main__":
    main()
