# -*- coding: utf-8 -*-
"""
Build the standalone "Add a New Client" runbook (HTML + PDF) from its source.

  python build_add_client_runbook.py

Produces, next to this script:
    Add_A_Client_Runbook.html   self-contained page, opens in any browser
    Add_A_Client_Runbook.pdf    print-ready, via headless Chrome

Same shape as build_runbook.py, which builds the Data Refresh Runbook, and it
reuses that script's font embedding and print stylesheet so the two documents
stay typographically identical. The difference is the screenshots: this one
carries eleven PNGs, which are embedded as data URIs so the HTML can be mailed
or copied around as a single file.
"""

import base64
import io
import mimetypes
import os
import re
import sys

import build_runbook as base

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "add_client_source.html")
MEDIA = os.path.join(HERE, "add_client_media")
OUT_HTML = os.path.join(HERE, "Add_A_Client_Runbook.html")
OUT_PDF = os.path.join(HERE, "Add_A_Client_Runbook.pdf")

# Reuse the Data Refresh Runbook's print stylesheet, repointing the page breaks
# at this document's sections and keeping a screenshot with its caption.
PRINT_CSS = base.PRINT_CSS.replace(
    "#cadence,#running,#reading,#live,#manual,#moved,#wrong,#adding,#never{",
    "#before,#chatbot,#map,#properties,#dashboard,#engine,#objectid,"
    "#checking,#wrong{",
).replace(
    "#cadence{break-before:auto}",
    "#before{break-before:auto}",
) + """
  @media print{
    figure.shot{break-inside:avoid;box-shadow:none}
    figure.shot img{filter:none}
    figure.shot figcaption{font-size:8.6pt;padding:5pt 9pt}
  }
"""

FOOTER = """
  <footer style="grid-column:1/-1;margin-top:8px;padding-top:22px;
                 border-top:1px solid var(--rule);color:var(--muted);
                 font-size:13px;display:flex;flex-wrap:wrap;gap:6px 18px;
                 justify-content:space-between">
    <span>FastLocations &middot; Add a New Client</span>
    <span>Companion runbooks: Property Scraper &middot; Organizations Data &middot; Data Refresh</span>
  </footer>
"""


def inline_images(body):
    """Embed every add_client_media/*.png referenced by the source as a data URI.

    The screenshots are the document. Leaving them as relative paths means the
    HTML only works from its own folder, which defeats the point of a runbook
    you can send to somebody.
    """
    missing = []
    total = [0]
    cache = {}

    def sub(m):
        rel = m.group(1)
        name = os.path.basename(rel)
        path = os.path.join(MEDIA, name)
        if not os.path.exists(path):
            missing.append(rel)
            return m.group(0)
        if name not in cache:
            raw = io.open(path, "rb").read()
            mime = mimetypes.guess_type(path)[0] or "image/png"
            cache[name] = "data:%s;base64,%s" % (
                mime, base64.b64encode(raw).decode("ascii"))
            total[0] += len(raw)
        return 'src="%s"' % cache[name]

    body = re.sub(r'src="(add_client_media/[^"]+)"', sub, body)

    if missing:
        sys.exit("Missing screenshots: %s" % ", ".join(sorted(set(missing))))
    print("[img]   embedded %d screenshots (%.0f KB)"
          % (len(cache), total[0] / 1024.0))
    return body


def build_html():
    if not os.path.exists(SRC):
        sys.exit("Missing %s" % os.path.basename(SRC))
    src = io.open(SRC, encoding="utf-8").read()

    end = src.rfind("</style>")
    if end == -1:
        sys.exit("No </style> found in the source - unexpected format.")
    head = src[:end]
    body = src[end + len("</style>"):]

    head, _ = base.inline_fonts(head)
    head = head + PRINT_CSS + "</style>"

    body = inline_images(body.rstrip())
    if body.endswith("</div>"):
        body = body[: -len("</div>")] + FOOTER + "</div>"

    doc = (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="description" content="How to add a new FastLocations '
        'client: the AI assistant, the map record, its properties, the '
        'dashboard record and the matching engine.">\n'
        + head +
        "\n</head>\n<body>\n"
        + body +
        "\n</body>\n</html>\n"
    )
    io.open(OUT_HTML, "w", encoding="utf-8").write(doc)
    print("[html]  %s  (%.1f KB)"
          % (os.path.basename(OUT_HTML), os.path.getsize(OUT_HTML) / 1024.0))
    return OUT_HTML


def build_pdf(html_path):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[pdf]   playwright not installed - skipping PDF")
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
            print("[pdf]   could not launch a browser - skipping PDF")
            return None
        page = browser.new_page()
        page.emulate_media(media="print", color_scheme="light")
        page.goto(url, wait_until="networkidle")
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
                     '<span>FastLocations &middot; Add a New Client</span>'
                     '<span class="pageNumber"></span></div>'))
        browser.close()
    print("[pdf]   %s  (%.1f KB)"
          % (os.path.basename(OUT_PDF), os.path.getsize(OUT_PDF) / 1024.0))
    return OUT_PDF


if __name__ == "__main__":
    h = build_html()
    build_pdf(h)
