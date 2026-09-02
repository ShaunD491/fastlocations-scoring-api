# -*- coding: utf-8 -*-
"""
Build the standalone Data Refresh Runbook (HTML + PDF) from the artifact source.

  python build_runbook.py

Produces, next to this script:
    Data_Refresh_Runbook.html   self-contained page, opens in any browser
    Data_Refresh_Runbook.pdf    print-ready, via headless Chrome

The artifact source is body-only (the Artifact host supplies the document
shell), so this wraps it in a real HTML document and adds a print stylesheet.
"""

import base64
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "runbook_source.html")
OUT_HTML = os.path.join(HERE, "Data_Refresh_Runbook.html")
OUT_PDF = os.path.join(HERE, "Data_Refresh_Runbook.pdf")

PRINT_CSS = """
  /* ---------- print / PDF ---------- */
  @page{
    size:A4;
    margin:16mm 15mm 18mm;
  }
  @media print{
    /* paper is white: pin the light palette regardless of viewer theme */
    :root, :root[data-theme="dark"]{
      --ground:#ffffff;
      --panel:#ffffff;
      --panel-2:#f2f5f9;
      --ink:#101823;
      --ink-2:#2b3a4d;
      --muted:#55636f;
      --rule:#d4dce6;
      --rule-2:#b9c5d3;
      --accent:#0a4f8c;
      --accent-soft:#eaf1f8;
      --go:#166b43;
      --go-soft:#e6f3ec;
      --warn:#8a5209;
      --warn-soft:#fbf0dd;
      --stop:#98301f;
      --stop-soft:#fbe8e5;
      --shadow:none;
    }
    body{background:#fff;font-size:10.4pt;line-height:1.5}
    .shell{
      display:block;
      max-width:none;
      padding:0;
    }
    nav.rail{display:none}
    main{padding-top:0;max-width:none}

    .mast{padding:0 0 14pt;border-bottom-width:1.5pt}
    h1{font-size:26pt;margin-bottom:8pt}
    .standfirst{font-size:11pt;max-width:none}
    .facts{margin-top:14pt;padding-top:12pt;gap:0 22pt}
    .fact b{font-size:15pt}

    /* keep units of meaning on one page */
    section{margin-bottom:20pt;break-inside:auto}
    h2{font-size:16pt;margin-top:0;break-after:avoid}
    .sec-note{break-after:avoid;margin-bottom:12pt}
    h3{font-size:11.5pt;margin-top:14pt;break-after:avoid}
    p{margin-bottom:8pt;orphans:3;widows:3}

    .note,.tw,pre{break-inside:avoid;box-shadow:none}
    ol.steps > li{break-inside:avoid;padding-bottom:12pt}
    table{font-size:9.4pt}
    th,td{padding:6pt 8pt}
    thead{display:table-header-group}
    tr{break-inside:avoid}

    hr.div{margin-bottom:16pt}

    /* a new page for each major part keeps the runbook navigable on paper */
    #cadence,#running,#reading,#manual,#moved,#wrong,#adding,#never{
      break-before:page;
    }
    #cadence{break-before:auto}

    a{color:var(--accent);text-decoration:none}
    .btn,.chip{border:0.5pt solid var(--rule-2)}
  }
"""

FOOTER = """
  <footer style="grid-column:1/-1;margin-top:8px;padding-top:22px;
                 border-top:1px solid var(--rule);color:var(--muted);
                 font-size:13px;display:flex;flex-wrap:wrap;gap:6px 18px;
                 justify-content:space-between">
    <span>FastLocations &middot; Data Refresh Runbook</span>
    <span>Tool and reference: <code>Projects/tool/</code></span>
  </footer>
"""


def inline_fonts(head):
    """Replace the Google Fonts <link> with @font-face rules whose files are
    embedded as data URIs.

    Makes the page self-contained: it renders identically offline, in email,
    on a machine that blocks Google, and inside the PDF - none of which can be
    relied on when the faces are fetched at view time.
    """
    m = re.search(r'<link rel="stylesheet" href="(https://fonts\.googleapis\.com[^"]+)">', head)
    if not m:
        return head, 0

    css_url = m.group(1).replace("&amp;", "&")
    try:
        import refresh_engine as eng
        sess = eng.new_session(eng.load_config())
    except Exception:
        import requests
        sess = requests.Session()

    # a modern UA makes Google serve woff2 rather than legacy formats
    ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
    try:
        css = sess.get(css_url, headers={"User-Agent": ua}, timeout=60).text
    except Exception as e:
        print("[fonts] could not fetch font CSS (%s) - leaving the link in" % e)
        return head, 0

    # keep only the latin subsets; the doc has no other scripts
    blocks = re.findall(r"/\*\s*([a-z0-9\-]+)\s*\*/\s*(@font-face\s*\{.*?\})",
                        css, re.S)
    if not blocks:
        blocks = [("latin", b) for b in
                  re.findall(r"(@font-face\s*\{.*?\})", css, re.S)]

    cache = {}
    out = []
    kept = 0
    for subset, block in blocks:
        if subset not in ("latin", "latin-ext"):
            continue
        u = re.search(r"url\((https://fonts\.gstatic\.com[^)]+)\)", block)
        if not u:
            continue
        font_url = u.group(1)
        if font_url not in cache:
            try:
                raw = sess.get(font_url, headers={"User-Agent": ua},
                               timeout=60).content
            except Exception:
                continue
            cache[font_url] = base64.b64encode(raw).decode("ascii")
        data_uri = "data:font/woff2;base64," + cache[font_url]
        out.append(re.sub(r"url\(https://fonts\.gstatic\.com[^)]+\)",
                          "url(%s)" % data_uri, block))
        kept += 1

    if not out:
        print("[fonts] no font files embedded - leaving the link in")
        return head, 0

    total = sum(len(v) for v in cache.values()) * 3 // 4
    head = head.replace(m.group(0), "")
    head = re.sub(r'<link rel="preconnect"[^>]*>\s*', "", head)
    head = head.replace("<style>", "<style>\n" + "\n".join(out) + "\n", 1)
    print("[fonts] embedded %d faces from %d files (%.0f KB)"
          % (kept, len(cache), total / 1024.0))
    return head, kept


def build_html():
    if not os.path.exists(SRC):
        sys.exit("Missing %s - copy the artifact source next to this script."
                 % os.path.basename(SRC))
    src = io.open(SRC, encoding="utf-8").read()

    # The source is body-content that opens with <title>/<link>/<style>.
    # Split it at the end of the last <style> block: everything before belongs
    # in <head>, everything after is the body.
    end = src.rfind("</style>")
    if end == -1:
        sys.exit("No </style> found in the source - unexpected format.")
    head = src[:end]
    body = src[end + len("</style>"):]

    head, _ = inline_fonts(head)
    head = head + PRINT_CSS + "</style>"

    body = body.rstrip()
    if body.endswith("</div>"):
        body = body[: -len("</div>")] + FOOTER + "</div>"

    doc = (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="description" content="How to keep the FastLocations '
        'incentives search tool and publish its results.">\n'
        + head +
        "\n</head>\n<body>\n"
        + body +
        "\n</body>\n</html>\n"
    )
    io.open(OUT_HTML, "w", encoding="utf-8").write(doc)
    print("[html] %s  (%.1f KB)"
          % (os.path.basename(OUT_HTML), os.path.getsize(OUT_HTML) / 1024.0))
    return OUT_HTML


def build_pdf(html_path):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[pdf] playwright not installed - skipping PDF")
        return None

    url = "file:///" + html_path.replace("\\", "/")
    with sync_playwright() as pw:
        for launch in ({"channel": "chrome"}, {"channel": "msedge"}, {}):
            try:
                browser = pw.chromium.launch(**launch)
                break
            except Exception:
                browser = None
        if browser is None:
            print("[pdf] could not launch a browser - skipping PDF")
            return None
        page = browser.new_page()
        page.emulate_media(media="print", color_scheme="light")
        page.goto(url, wait_until="networkidle")
        # webfonts must finish loading or the PDF sets in fallback faces
        page.evaluate("() => document.fonts.ready")
        page.wait_for_timeout(1200)
        page.pdf(path=OUT_PDF, format="A4", print_background=True,
                 margin={"top": "16mm", "bottom": "18mm",
                         "left": "15mm", "right": "15mm"},
                 display_header_footer=True,
                 header_template="<div></div>",
                 footer_template=(
                     '<div style="width:100%;font:9px -apple-system,Segoe UI,'
                     'sans-serif;color:#7a8794;padding:0 15mm;display:flex;'
                     'justify-content:space-between">'
                     '<span>FastLocations &middot; Data Refresh Runbook</span>'
                     '<span class="pageNumber"></span></div>'))
        browser.close()
    print("[pdf]  %s  (%.1f KB)"
          % (os.path.basename(OUT_PDF), os.path.getsize(OUT_PDF) / 1024.0))
    return OUT_PDF


if __name__ == "__main__":
    h = build_html()
    build_pdf(h)
