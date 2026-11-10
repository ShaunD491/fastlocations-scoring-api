r"""
_common.py - shared helpers for the FastLocations labor pipeline (pipeline/labor/).

Layout (all relative to this folder, so the pipeline runs from any checkout):
    raw/<source>/     downloaded or hand-placed source files (git-ignored)
    out/              pipeline outputs (git-ignored); out/crosswalks/ holds the lookups
    config            Census API key is read from ..\..\tool\refresh_config.json (census_api_key)
                      or the CENSUS_API_KEY environment variable.

Downloads use a plain, honest client name. Sources that refuse scripted downloads (BLS, some
StatCan www12 pages) are NOT worked around: the fetch scripts tell you which file to download in a
browser and where to put it.
"""
import datetime
import json
import os
import re
import ssl
import sys
import unicodedata
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "raw")
OUT = os.path.join(HERE, "out")
XW = os.path.join(OUT, "crosswalks")
PROJECTS = os.path.normpath(os.path.join(HERE, "..", ".."))
UA = "FastLocations-labor-pipeline/1.0"


def raw_path(*parts):
    p = os.path.join(RAW, *parts)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


def out_path(*parts):
    p = os.path.join(OUT, *parts)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


def _ctx():
    ctx = ssl.create_default_context()
    for k in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        pth = os.environ.get(k)
        if pth and os.path.exists(pth):
            try:
                ctx = ssl.create_default_context(cafile=pth)
            except Exception:
                pass
            break
    return ctx


def http_get(url, timeout=300):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout, context=_ctx()) as r:
        return r.read()


def download(url, dest, force=False, min_bytes=200):
    """Download url -> dest unless dest already exists (and is not a tiny error page)."""
    if not force and os.path.exists(dest) and os.path.getsize(dest) >= min_bytes:
        return dest
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    print(f"  downloading {url}")
    data = http_get(url)
    if data[:200].lstrip().lower().startswith((b"<!doctype html", b"<html")):
        raise RuntimeError(f"{url} returned an HTML page, not a data file")
    with open(dest, "wb") as fh:
        fh.write(data)
    return dest


def need_manual(dest, url, what):
    """Exit with download instructions for a source that refuses scripted downloads."""
    sys.exit(
        f"\nMissing: {dest}\n"
        f"  {what}\n"
        f"  This source blocks scripted downloads. Open the link below in a browser, save the file,\n"
        f"  and put it at the path above, then run this script again.\n"
        f"  {url}\n")


def census_key():
    k = os.environ.get("CENSUS_API_KEY")
    if k:
        return k.strip()
    cfg = os.path.join(PROJECTS, "tool", "refresh_config.json")
    try:
        return json.load(open(cfg, encoding="utf-8"))["census_api_key"].strip()
    except Exception:
        sys.exit("No Census API key: set CENSUS_API_KEY or census_api_key in Projects/tool/refresh_config.json")


def write_json(path, obj, compact=True):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        if compact:
            json.dump(obj, fh, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(obj, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    print(f"  wrote {os.path.relpath(path, HERE)}  ({os.path.getsize(path)/1e6:.2f} MB)")


def write_csv(path, rows, fields):
    import csv
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"  wrote {os.path.relpath(path, HERE)}  ({len(rows)} rows)")


def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def norm_title(s):
    """Lower-case, accent-free, punctuation-free title for lookups ('Fork-lift Operator' -> 'fork lift operator')."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def soc_fmt(code):
    """'151252' / '15-1252' / '15-1252.00' -> '15-1252'."""
    c = re.sub(r"[^0-9]", "", str(code or ""))[:6]
    return f"{c[:2]}-{c[2:]}" if len(c) == 6 else None


def today():
    return datetime.date.today().isoformat()
