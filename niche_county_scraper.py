#!/usr/bin/env python3
r"""
niche_county_scraper.py  (v2 - network interception)
----------------------------------------------------
Scrape Niche's "Best Counties to Live" ranking across all pages -> NICHE_County_Liveability.json

    https://www.niche.com/places-to-live/search/best-counties/   (then ?page=2, ?page=3, ...)

WHY v2:
  Niche loads the ranked county cards from a background API call AFTER the page renders --
  the data is NOT in the initial HTML / __NEXT_DATA__ (v1 only saw nav links). This version
  drives a real Chromium browser (Playwright) and INTERCEPTS the JSON responses the page
  fetches, then harvests the county entities from them. A rendered-DOM reader is the fallback.

SETUP (one time):
    pip install playwright
    playwright install chromium

RUN:
    python niche_county_scraper.py
    # if results look thin, run once in debug to capture what the page fetched:
    set DEBUG=true && python niche_county_scraper.py      (Windows)
    DEBUG=true python niche_county_scraper.py             (Mac/Linux)

OPTIONS (environment variables):
    OUT_FILE (default NICHE_County_Liveability.json) · START_PAGE (1) · MAX_PAGES (300)
    DELAY_SECONDS (3.0) · HEADLESS (true; "false" to watch) · DEBUG (false)

RESPECTFUL USE:
  Check Niche's Terms of Service / robots.txt first. Keep DELAY_SECONDS high. If Niche shows a
  Cloudflare "verify you are human" challenge the script STOPS -- it does not bypass bot detection.
  (If challenged, try HEADLESS=false and solve it once, then let it continue.)
"""

import json, os, sys, time

OUT_FILE   = os.environ.get("OUT_FILE", "NICHE_County_Liveability.json")
START_PAGE = int(os.environ.get("START_PAGE", "1"))
MAX_PAGES  = int(os.environ.get("MAX_PAGES", "300"))
DELAY      = float(os.environ.get("DELAY_SECONDS", "6.0"))
HEADLESS   = os.environ.get("HEADLESS", "true").lower() != "false"
DEBUG      = os.environ.get("DEBUG", "false").lower() == "true"
URL        = "https://www.niche.com/places-to-live/search/best-counties/?page={page}"

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.exit("Playwright not installed.\n  pip install playwright\n  playwright install chromium")

GRADE_KEYS = ("grade", "overallGrade", "nicheGrade", "letterGrade")
SCORE_KEYS = ("score", "ratingScore", "overallScore", "nicheScore")
RANK_KEYS  = ("rank", "searchResultRank", "ordinal", "position")
STATE_KEYS = ("state", "region", "regionName", "stateAbbr", "stateName")
NAME_KEYS  = ("name", "entityName", "displayName", "title")


def first(d, keys):
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return None


def is_county_card(d):
    """A dict that represents a ranked place: has a name and either a places-to-live url or a grade."""
    if not isinstance(d, dict):
        return False
    name = first(d, NAME_KEYS)
    url = d.get("url") or d.get("permalink") or d.get("path") or ""
    has_place_url = isinstance(url, str) and "/places-to-live/" in url
    has_grade = first(d, GRADE_KEYS) is not None
    return bool(name) and (has_place_url or has_grade)


def harvest(node, found, seen):
    """Recursively collect county-like entities (dedup by url or name)."""
    if isinstance(node, dict):
        if is_county_card(node):
            url = node.get("url") or node.get("permalink") or node.get("path") or ""
            key = url or first(node, NAME_KEYS)
            if key and key not in seen:
                seen.add(key)
                full = (url if str(url).startswith("http")
                        else ("https://www.niche.com" + url if url else None))
                found.append({
                    "name":  first(node, NAME_KEYS),
                    "state": first(node, STATE_KEYS),
                    "url":   full,
                    "grade": first(node, GRADE_KEYS),
                    "score": first(node, SCORE_KEYS),
                    "rank":  first(node, RANK_KEYS),
                })
        for v in node.values():
            harvest(v, found, seen)
    elif isinstance(node, list):
        for v in node:
            harvest(v, found, seen)


def dom_fallback(page):
    """Read visible result cards straight from the rendered DOM."""
    return page.evaluate(
        r"""() => {
            const out = [], seen = new Set();
            document.querySelectorAll('a[href*="/places-to-live/"]').forEach(a => {
                const href = a.getAttribute('href') || '';
                if (!/\/places-to-live\/[a-z0-9-]+\/?(\?|$)/i.test(href)) return;
                if (/\/search\/|\/rankings\/|\/survey\//.test(href)) return;
                const card = a.closest('article,li,[class*="card"],div') || a;
                const lines = (card.innerText || a.innerText || '').split('\n').map(s=>s.trim()).filter(Boolean);
                const name = lines[0] || a.getAttribute('aria-label') || '';
                if (!name || seen.has(href)) return;
                seen.add(href);
                const grade = lines.find(t => /^[A-D][+-]?$/.test(t)) || null;
                out.push({ name, url: href.startsWith('http')?href:'https://www.niche.com'+href,
                           grade, state:null, score:null, rank:null });
            });
            return out;
        }"""
    )


def save(rows):
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)


def main():
    results, seen = [], set()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        ctx = browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
            viewport={"width": 1366, "height": 1000}, locale="en-US",
        )
        page = ctx.new_page()

        # --- capture every JSON response the page fetches ---
        captured = []   # list of (url, parsed_json) for the current page
        def on_response(resp):
            try:
                ct = (resp.headers or {}).get("content-type", "")
                if "application/json" in ct or resp.url.endswith(".json"):
                    captured.append((resp.url, resp.json()))
            except Exception:
                pass
        page.on("response", on_response)

        empty_streak = 0
        for n in range(START_PAGE, START_PAGE + MAX_PAGES):
            captured.clear()
            url = URL.format(page=n)
            print(f"[page {n}] {url}")
            try:
                page.goto(url, wait_until="networkidle", timeout=60000)
            except Exception as e:
                print(f"   load issue ({e}); continuing with whatever loaded")

            title = (page.title() or "").lower()
            body = (page.content() or "").lower()
            if "just a moment" in title or "verify you are human" in body or "challenge" in body[:4000]:
                print("   Niche presented a bot/CAPTCHA challenge. Stopping (not bypassing).")
                break

            # nudge any lazy loading, give the API calls time to land
            try:
                page.mouse.wheel(0, 4000); page.wait_for_timeout(1200)
                page.mouse.wheel(0, 8000); page.wait_for_timeout(1200)
            except Exception:
                pass

            if n <= START_PAGE + 1:   # save rendered HTML of the first two pages for inspection
                try:
                    with open(f"niche_page{n}.html", "w", encoding="utf-8") as f:
                        f.write(page.content())
                    print(f"   saved niche_page{n}.html")
                except Exception:
                    pass

            if n == START_PAGE:   # always save a diagnostic of what the page fetched
                ordered = sorted(captured, key=lambda t: len(json.dumps(t[1])), reverse=True)
                with open("niche_debug.json", "w", encoding="utf-8") as f:
                    json.dump({
                        "endpoints": [{"url": u, "bytes": len(json.dumps(j))} for u, j in ordered],
                        "largest_responses": [j for _u, j in ordered[:3]],
                    }, f, indent=2, ensure_ascii=False)
                print(f"   wrote niche_debug.json ({len(captured)} JSON responses captured)")

            before = len(results)
            # 1) harvest from intercepted API JSON
            for _u, j in captured:
                harvest(j, results, seen)
            # 2) fallback: rendered DOM
            if len(results) == before:
                for rec in dom_fallback(page):
                    if rec["url"] not in seen:
                        seen.add(rec["url"]); results.append(rec)

            added = len(results) - before
            print(f"   +{added} counties (total {len(results)})")

            for i, r in enumerate(results, start=1):
                if r.get("rank") in (None, ""):
                    r["rank"] = i
            save(results)

            if added == 0:
                empty_streak += 1
                if empty_streak >= 2:
                    print("   no new results on two pages; finished.")
                    break
            else:
                empty_streak = 0
            time.sleep(DELAY)

        browser.close()
    print(f"\nDone. {len(results)} counties written to {OUT_FILE}")


if __name__ == "__main__":
    main()
