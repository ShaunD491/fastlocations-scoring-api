# -*- coding: utf-8 -*-
"""
refresh_engine.py — rebuilds the data the My Project scorer reads.
==================================================================

The scorer (Projects/scorer.py) loads ~35 runtime JSON files. Most of them are
produced by a `build_*.py` script that takes one source file and writes one
JSON. This engine wraps those scripts so a refresh is a button rather than a
remembered command line:

    acquire  ->  build  ->  validate  ->  keep or roll back

ACQUIRE   download the source (or find it already sitting in staging/)
BUILD     run the registered build_*.py with the acquired file as argv[1]
VALIDATE  parse the JSON the builder wrote and count records
KEEP      if the count clears the registered floor, keep it
ROLLBACK  otherwise restore the backup taken before the builder ran

The rollback is the point of the whole wrapper. Running a builder by hand
against a source that has changed shape gives you a 12-record county_nri.json
and a model that silently stops screening on hazard. Here that outcome is
rejected and the previous file comes back.

Brave and Claude do one job: when a registered download URL stops working,
`research_source` searches for where the file moved to and Claude picks the
real download link out of the results. It proposes; it never writes.

Requires: pip install flask requests
"""

import fnmatch
import glob
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from datetime import date, datetime, timezone

import requests

import refresh_registry as reg

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(HERE, "state")
LAST_RUN = os.path.join(STATE_DIR, "last_run.json")
CONFIG = os.path.join(HERE, "refresh_config.json")


def resolve(path):
    if not path:
        return HERE
    if os.path.isabs(path):
        return path
    return os.path.normpath(os.path.join(HERE, path))


def log(msg):
    print(msg, flush=True)


def load_config():
    with open(CONFIG, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_config(cfg):
    with open(CONFIG, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def now_iso():
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.0f %s" % (n, unit) if unit == "B" else "%.1f %s" % (n, unit)
        n /= 1024.0


# ---------------------------------------------------------------------------
# TLS
#
# This machine runs Norton, which intercepts HTTPS and presents its own root
# CA. That root is not in certifi, so every python request fails verification
# until it is merged in. Same approach as the incentives tool.
# ---------------------------------------------------------------------------
_CA_BUNDLE = None

_EXTRA_CA_CANDIDATES = [
    r"C:\ProgramData\Norton\Antivirus\wscert.pem",
    r"C:\ProgramData\Norton\wscert.pem",
]


def build_ca_bundle(cfg=None, on_log=log):
    global _CA_BUNDLE
    if _CA_BUNDLE:
        return _CA_BUNDLE
    cfg = cfg or {}

    explicit = cfg.get("ca_bundle")
    if explicit and os.path.exists(resolve(explicit)):
        _CA_BUNDLE = resolve(explicit)
        return _CA_BUNDLE

    try:
        import certifi
        base = certifi.where()
    except ImportError:
        base = None

    extras = []
    for env_key in ("NODE_EXTRA_CA_CERTS", "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE"):
        p = os.environ.get(env_key)
        if p:
            p = p.replace("\\\\", "\\")
            if os.path.exists(p) and p != base:
                extras.append(p)
    # the incentives tool already merged one; reuse it rather than rebuild
    sibling = os.path.join(HERE, "..", "..", "incentives", "tool", "state", "ca_bundle.pem")
    sibling = os.path.normpath(sibling)
    if os.path.exists(sibling):
        _CA_BUNDLE = sibling
        on_log("[tls] using the incentives tool's merged CA bundle")
        return _CA_BUNDLE
    for p in _EXTRA_CA_CANDIDATES:
        if os.path.exists(p) and p not in extras:
            extras.append(p)

    if not base:
        _CA_BUNDLE = extras[0] if extras else True
        return _CA_BUNDLE
    if not extras:
        _CA_BUNDLE = base
        return _CA_BUNDLE

    if not os.path.isdir(STATE_DIR):
        os.makedirs(STATE_DIR)
    out = os.path.join(STATE_DIR, "ca_bundle.pem")
    try:
        with open(out, "wb") as fh:
            for p in [base] + extras:
                with open(p, "rb") as src:
                    fh.write(src.read())
                fh.write(b"\n")
        on_log("[tls] merged CA bundle: certifi + %s"
               % ", ".join(os.path.basename(e) for e in extras))
        _CA_BUNDLE = out
    except Exception as e:
        on_log("[tls] could not merge bundle (%s), using certifi" % e)
        _CA_BUNDLE = base
    return _CA_BUNDLE


def new_session(cfg=None, on_log=log):
    cfg = cfg or {}
    s = requests.Session()
    s.verify = build_ca_bundle(cfg, on_log)
    s.headers.update({"User-Agent": cfg.get("user_agent", "Mozilla/5.0")})
    return s


def load_claude_key(cfg):
    path = resolve(cfg.get("claude_key_file", ""))
    if not path or not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            lines = [l.strip() for l in fh if l.strip()]
    except Exception:
        return ""
    for l in lines:
        if l.startswith("sk-ant"):
            return l
    return lines[0] if lines else ""


# ---------------------------------------------------------------------------
# Job — mutable run state shared with the web UI
# ---------------------------------------------------------------------------
class Job(object):
    def __init__(self):
        self.running = False
        self.cancel = False
        self.title = ""
        self.log = []
        self.progress = {"done": 0, "total": 0, "current": ""}
        self.results = []          # one row per dataset attempted
        self.proposals = []        # research-mode findings awaiting approval
        self.stats = {"ok": 0, "failed": 0, "skipped": 0, "rolled_back": 0,
                      "bytes": 0, "brave_queries": 0, "claude_calls": 0}
        self.lock = threading.Lock()

    def emit(self, msg):
        with self.lock:
            self.log.append(str(msg))
            if len(self.log) > 4000:
                del self.log[:1000]
        # A Windows console is cp1252, and builders legitimately print
        # "Montreal" with an accent. Letting print() raise would abort a run
        # over a log line, which is an absurd way to lose a rebuild.
        try:
            print(msg, flush=True)
        except UnicodeEncodeError:
            enc = getattr(sys.stdout, "encoding", None) or "ascii"
            print(str(msg).encode(enc, "replace").decode(enc, "replace"),
                  flush=True)

    def record(self, ds_id, status, detail):
        with self.lock:
            self.results.append({"id": ds_id, "status": status,
                                 "detail": detail, "at": now_iso()})


# ---------------------------------------------------------------------------
# Inventory — what is on disk right now
# ---------------------------------------------------------------------------
def count_records(path):
    """Records in a runtime JSON. Dicts are keyed by FIPS/CDUID, lists are
    row tables; underscore keys are metadata, not data."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as e:
        return None, "unreadable: %s" % str(e)[:80]
    if isinstance(data, dict):
        keys = [k for k in data if not str(k).startswith("_")]
        # a few files wrap their records in a single container key
        # (ca_innovation.json is {_comment, _sources, provinces:{...}}), so
        # counting top level would report "1 record" for 13 provinces
        if len(keys) == 1 and isinstance(data[keys[0]], (dict, list)):
            return len(data[keys[0]]), ""
        return len(keys), ""
    if isinstance(data, list):
        return len(data), ""
    return None, "unexpected top-level %s" % type(data).__name__


def inventory(cfg=None):
    cfg = cfg or load_config()
    data_dir = resolve(cfg.get("data_dir", ".."))
    last = load_last_run()
    rows = []
    for d in reg.DATASETS:
        path = os.path.join(data_dir, d["output"])
        row = {
            "id": d["id"], "title": d["title"], "output": d["output"],
            "feeds": d["feeds"], "country": d["country"],
            "acquire": d["acquire"], "acquire_label": reg.ACQUIRE_LABEL[d["acquire"]],
            "builder": d["builder"], "source_page": d.get("source_page", ""),
            "url": d.get("url"), "notes": d.get("notes", ""),
            "cadence_days": d["cadence_days"],
            "automatable": d["acquire"] in reg.AUTOMATABLE,
            "exists": False, "size": 0, "size_h": "-", "age_days": None,
            "modified": "-", "modified_full": "-", "records": None,
            "ftype": (os.path.splitext(d["output"])[1].lstrip(".") or "?").upper(),
            "outputs": outputs_of(d), "status": "missing",
            "vintage": None, "vintage_src": None, "vintage_age_days": None,
            "vintage_known": False,
            "last_refresh": (last.get("datasets", {}).get(d["id"]) or {}),
        }
        if os.path.exists(path):
            st = os.stat(path)
            age = (time.time() - st.st_mtime) / 86400.0
            n, err = count_records(path)
            row.update({
                "exists": True, "size": st.st_size, "size_h": human_size(st.st_size),
                "age_days": round(age, 1),
                "modified": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d"),
                "modified_full": datetime.fromtimestamp(st.st_mtime)
                                         .strftime("%Y-%m-%d %H:%M"),
                "records": n,
            })
            # a builder that writes more than one file (the routing indexes)
            # should total its sizes, or the row understates what it produced
            if len(row["outputs"]) > 1:
                total = sum(os.path.getsize(os.path.join(data_dir, nm))
                            for nm in row["outputs"]
                            if os.path.exists(os.path.join(data_dir, nm)))
                row["size"] = total
                row["size_h"] = human_size(total)
            # Status is judged on the DATA's vintage, not the file's mtime.
            iso, prec, src = vintage_of(d, cfg, path)
            row["vintage"], row["vintage_src"] = iso, src
            row["vintage_known"] = bool(iso) and src != "file date only"
            v_age = age
            if iso:
                try:
                    v_age = (date.today() - date(*[int(x) for x in iso.split("-")])).days
                except Exception:
                    v_age = age
                row["vintage_age_days"] = v_age

            if err:
                row["status"] = "broken"
            elif n is not None and n < floor_for(d, d["output"]):
                row["status"] = "thin"
            elif v_age > d["cadence_days"]:
                # Old data and NOTHING NEWER PUBLISHED are different claims.
                # ACS 2024 5-year describes a period ending 609 days ago, but it
                # is the newest vintage in existence - calling that "stale"
                # invites a refresh that cannot improve it. When the last
                # successful rebuild already landed on this vintage, the source
                # is exhausted, and the honest label is "newest".
                prev = (last.get("datasets", {}).get(d["id"]) or {}).get("vintage")
                row["status"] = "newest" if (prev and prev == iso) else "stale"
            else:
                row["status"] = "ok"
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Data vintage
#
# A file's modification time says when it was WRITTEN, not how old the data
# inside it is. Those are different questions and only the second one matters.
# The Connecticut migration rewrote ten files in an afternoon and every one of
# them then reported "0 days old" while carrying data from 2022 and 2023 - a
# staleness column that says a 2017 power inventory is fresh is worse than no
# column at all.
#
# So vintage is read from the data itself, in this order:
#   1. a sidecar <stem>_meta.json          (county_features, which cannot hold
#                                           _meta inside it - see that builder)
#   2. a "_meta" block inside the file     (drought, power, workforce, ...)
#   3. a per-record field named by the registry's `vintage_field`
#      ("ref": "CBP2023", "USDA NASS 2022", "2026-07")
#   4. the source Last-Modified recorded in state/last_run.json
#   5. file mtime, LABELLED as a fallback so it is never mistaken for data age
# ---------------------------------------------------------------------------
# Anchored on DIGIT boundaries, not word boundaries. "CBP2023" has no word
# boundary between the P and the 2, so  would miss it; (?<!\d) only stops a
# year being torn out of the middle of a longer number.
_VINTAGE_PATTERNS = [
    (re.compile(r"(?<!\d)(?:19|20)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])(?!\d)"), "day"),
    (re.compile(r"(?<!\d)(?:19|20)\d{2}-(?:0[1-9]|1[0-2])(?!\d)"), "month"),
    (re.compile(r"(?<!\d)(?:20[0-4]\d)(?:0[1-9]|1[0-2])(?!\d)"), "yyyymm"),
    (re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)"), "year"),
]


def parse_vintage(text):
    """Best-effort (iso_date, precision) from any string carrying a date.

    Handles the shapes the builders actually emit: "2026-09-01",
    "Period 202502", "Census ACS 2023 5-year", "CBP2023", "2026-07".
    A bare year resolves to 31 December, because data labelled 2023 describes
    2023 and is published later - dating it to January would overstate how
    current it is.
    """
    if not text:
        return None, None
    t = str(text)
    for rx, kind in _VINTAGE_PATTERNS:
        m = rx.search(t)
        if not m:
            continue
        g = m.group(0)
        if kind == "day":
            return g, "day"
        if kind == "month":
            return g + "-01", "month"
        if kind == "yyyymm":
            return "%s-%s-01" % (g[:4], g[4:]), "month"
        return g + "-12-31", "year"
    return None, None


def _scan_for_vintage(obj, depth=0):
    """Pull the most specific date out of a _meta-ish mapping. Prefers keys that
    name the DATA (source, period, latest_map, window) over keys that name the
    RUN (built, migrated) - a run date is just mtime by another name."""
    if not isinstance(obj, dict) or depth > 2:
        return None, None, None
    prefer = ("latest_map", "period", "source", "sources", "window", "vintage",
              "reference", "ref")
    for key in prefer:
        for k, v in obj.items():
            if str(k).lower().lstrip("_") != key:
                continue
            text = " ".join(v) if isinstance(v, list) else str(v)
            iso, prec = parse_vintage(text)
            if iso:
                return iso, prec, str(k)
    for k in ("built", "migrated", "generated", "updated"):
        if k in obj:
            iso, prec = parse_vintage(str(obj[k]))
            if iso:
                return iso, prec, k
    return None, None, None


def vintage_of(d, cfg, path, _depth=0):
    """(iso_date, precision, source_label). source_label says WHERE it came
    from, so the UI can distinguish measured vintage from a mtime guess."""
    data_dir = resolve(cfg.get("data_dir", ".."))

    # 1. sidecar
    stem = os.path.splitext(d["output"])[0]
    side = os.path.join(data_dir, stem + "_meta.json")
    if os.path.exists(side):
        try:
            with open(side, encoding="utf-8") as fh:
                iso, prec, key = _scan_for_vintage(json.load(fh))
            if iso:
                return iso, prec, "sidecar %s" % os.path.basename(side)
        except Exception:
            pass

    body = None
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                body = json.load(fh)
        except Exception:
            body = None

    # 2. _meta inside the file
    if isinstance(body, dict) and isinstance(body.get("_meta"), dict):
        iso, prec, key = _scan_for_vintage(body["_meta"])
        if iso:
            return iso, prec, "_meta.%s" % key

    # 3. a per-record field the registry names
    field = d.get("vintage_field")
    if field and isinstance(body, dict):
        for k, v in body.items():
            if str(k).startswith("_") or not isinstance(v, dict):
                continue
            iso, prec = parse_vintage(v.get(field))
            if iso:
                return iso, prec, "record.%s" % field
            break

    # 4. what the server said when we last downloaded it
    entry = load_last_run().get("datasets", {}).get(d["id"]) or {}
    lm = (entry.get("signature") or {}).get("last_modified")
    if lm:
        try:
            from email.utils import parsedate_to_datetime
            return (parsedate_to_datetime(lm).date().isoformat(), "day",
                    "source Last-Modified")
        except Exception:
            pass

    # 5. a derived file is exactly as old as the oldest thing it is derived
    #    FROM. us_catchment rebuilt today from 2023 populations is 2023 data,
    #    and saying "0 days" would be the original bug in a new place.
    if d["acquire"] == "derive" and d.get("depends_on") and _depth < 3:
        best = None
        for dep in d["depends_on"]:
            dpath = os.path.normpath(os.path.join(data_dir, dep))
            owner = next((x for x in reg.DATASETS
                          if x["output"] == os.path.basename(dep)
                          and x["id"] != d["id"]), None)
            if owner:
                iso, prec, srcl = vintage_of(owner, cfg, dpath, _depth + 1)
            elif os.path.exists(dpath):
                iso, prec, srcl = (datetime.fromtimestamp(os.path.getmtime(dpath))
                                   .date().isoformat(), "file", "file date")
            else:
                continue
            if iso and (best is None or iso < best[0]):
                best = (iso, prec, os.path.basename(dep))
        if best:
            return best[0], best[1], "inherited from %s" % best[2]

    # 6. give up honestly
    if os.path.exists(path):
        return (datetime.fromtimestamp(os.path.getmtime(path)).date().isoformat(),
                "file", "file date only")
    return None, None, None


def load_last_run():
    try:
        with open(LAST_RUN, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {"datasets": {}}


def save_last_run(ds_id, entry):
    if not os.path.isdir(STATE_DIR):
        os.makedirs(STATE_DIR)
    data = load_last_run()
    data.setdefault("datasets", {})[ds_id] = entry
    data["updated"] = now_iso()
    tmp = LAST_RUN + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, ensure_ascii=False)
    os.replace(tmp, LAST_RUN)


# ---------------------------------------------------------------------------
# Backup / restore
# ---------------------------------------------------------------------------
def outputs_of(d):
    """Files a builder writes. build_edo_indexes writes two, so the backup has
    to cover both or a rollback restores half a routing table."""
    outs = [d["output"]] + list(d.get("also", []))
    if d["id"] == "edo_indexes":
        outs = ["edo_fips_index.json", "edo_ca_cd_index.json"]
    return outs


def backup_outputs(d, cfg, job):
    data_dir = resolve(cfg.get("data_dir", ".."))
    bdir = resolve(cfg.get("backup_dir", "../_data_backups"))
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    saved = {}
    for name in outputs_of(d):
        src = os.path.join(data_dir, name)
        if not os.path.exists(src):
            continue
        dest_dir = os.path.join(bdir, d["id"])
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, "%s.%s" % (stamp, name))
        shutil.copy2(src, dest)
        saved[name] = dest
    if saved:
        job.emit("    [backup] %d file(s) -> %s"
                 % (len(saved), os.path.relpath(bdir, HERE)))
        prune_backups(os.path.join(bdir, d["id"]),
                      int(cfg.get("backups_kept", 5)), len(saved))
    return saved


def prune_backups(folder, keep_sets, files_per_set):
    """Keep the newest `keep_sets` backups. Stamped names sort chronologically,
    so lexical order is chronological order."""
    try:
        files = sorted(os.listdir(folder))
    except Exception:
        return
    excess = len(files) - keep_sets * max(1, files_per_set)
    for f in files[:max(0, excess)]:
        try:
            os.remove(os.path.join(folder, f))
        except Exception:
            pass


def restore_outputs(saved, cfg, job):
    data_dir = resolve(cfg.get("data_dir", ".."))
    for name, backup in saved.items():
        try:
            shutil.copy2(backup, os.path.join(data_dir, name))
            job.emit("    [rollback] restored %s" % name)
        except Exception as e:
            job.emit("    [rollback] FAILED to restore %s: %s" % (name, e))


# ---------------------------------------------------------------------------
# Acquire
# ---------------------------------------------------------------------------
def staging_dir(cfg, ds_id=None):
    base = resolve(cfg.get("staging_dir", "staging"))
    path = os.path.join(base, ds_id) if ds_id else base
    os.makedirs(path, exist_ok=True)
    return path


def download(url, dest, cfg, job, session=None):
    """Stream a source file to disk. Written to .part and renamed on success so
    an interrupted download is never mistaken for a usable file."""
    session = session or new_session(cfg, job.emit)
    timeout = int(cfg.get("download_timeout", 60))
    chunk = int(cfg.get("download_chunk_bytes", 1048576))
    part = dest + ".part"
    job.emit("    [get] %s" % url)
    with session.get(url, stream=True, timeout=timeout) as r:
        if r.status_code != 200:
            raise IOError("HTTP %d fetching %s" % (r.status_code, url))
        total = int(r.headers.get("Content-Length") or 0)
        got, mark = 0, 0
        with open(part, "wb") as fh:
            for block in r.iter_content(chunk_size=chunk):
                if job.cancel:
                    raise KeyboardInterrupt("cancelled during download")
                if not block:
                    continue
                fh.write(block)
                got += len(block)
                if total and got - mark >= max(chunk * 8, total // 12):
                    mark = got
                    job.emit("      %s of %s (%d%%)"
                             % (human_size(got), human_size(total), 100 * got // total))
    os.replace(part, dest)
    job.emit("    [get] done, %s" % human_size(os.path.getsize(dest)))
    job.stats["bytes"] += os.path.getsize(dest)
    return dest


def extract_member(archive, pattern, dest_dir, job):
    """Pull one file out of a zip. Builders that unzip internally get the
    archive itself (pattern None) and never come through here."""
    if not zipfile.is_zipfile(archive):
        return archive
    with zipfile.ZipFile(archive) as z:
        names = z.namelist()
        hits = [n for n in names
                if fnmatch.fnmatch(os.path.basename(n).lower(), pattern.lower())]
        if not hits:
            hits = [n for n in names if fnmatch.fnmatch(n.lower(), pattern.lower())]
        if not hits:
            raise IOError("no member matching %r in %s (has: %s)"
                          % (pattern, os.path.basename(archive),
                             ", ".join(os.path.basename(n) for n in names[:8])))
        # largest match: these archives often carry a small metadata twin
        hits.sort(key=lambda n: z.getinfo(n).file_size, reverse=True)
        member = hits[0]
        out = os.path.join(dest_dir, os.path.basename(member))
        job.emit("    [unzip] %s (%s)"
                 % (os.path.basename(member), human_size(z.getinfo(member).file_size)))
        with z.open(member) as src, open(out, "wb") as fh:
            shutil.copyfileobj(src, fh, length=4 * 1024 * 1024)
    return out


def find_in_staging(d, cfg, job):
    """For manual-download datasets: whatever you dropped in staging/<id>/.
    Accepts the zip as well as the extracted file, so dragging the download in
    untouched works."""
    sdir = staging_dir(cfg, d["id"])
    pattern = d.get("member") or "*"
    files = [os.path.join(sdir, f) for f in os.listdir(sdir)]
    files = [f for f in files if os.path.isfile(f)]
    hits = [f for f in files
            if fnmatch.fnmatch(os.path.basename(f).lower(), pattern.lower())]
    if not hits:
        zips = [f for f in files if f.lower().endswith(".zip")]
        if zips:
            zips.sort(key=os.path.getmtime, reverse=True)
            return extract_member(zips[0], pattern, sdir, job)
    if not hits:
        return None
    hits.sort(key=os.path.getmtime, reverse=True)
    return hits[0]


def clean_staging(d, cfg, job, archive, extracted):
    """Tidy up after a successful build.

    Only ever touches what this tool downloaded or unpacked itself - a file you
    placed in staging/ by hand for a manual-download dataset is left alone,
    because re-fetching it is your work, not the tool's.

    The extracted member always goes: it is the big one (ca_occupation unpacks
    to 3.3 GB) and the archive can produce it again. The archive is kept only
    while it is small enough that keeping it beats re-downloading it.
    """
    if d["acquire"] != "download":
        return
    limit = int(cfg.get("staging_keep_archive_max_bytes", 100 * 1024 * 1024))
    freed = 0
    if extracted and archive and extracted != archive and os.path.exists(extracted):
        freed += os.path.getsize(extracted)
        try:
            os.remove(extracted)
        except Exception as e:
            job.emit("    [staging] could not remove %s: %s"
                     % (os.path.basename(extracted), e))
            freed = 0
    if archive and os.path.exists(archive) and os.path.getsize(archive) > limit:
        size = os.path.getsize(archive)
        try:
            os.remove(archive)
            freed += size
            job.emit("    [staging] archive over %s, not cached"
                     % human_size(limit))
        except Exception:
            pass
    if freed:
        job.emit("    [staging] freed %s" % human_size(freed))


def acquire(d, cfg, job, session=None):
    """Return (path_for_builder, archive, extracted). archive and extracted are
    what the tool itself created, so cleanup knows what it may delete."""
    kind = d["acquire"]

    if kind in ("derive", "census_api", "api"):
        return None, None, None

    if kind == "download":
        sdir = staging_dir(cfg, d["id"])
        name = d["url"].rsplit("/", 1)[-1].split("?")[0] or (d["id"] + ".bin")
        archive = os.path.join(sdir, name)
        download(d["url"], archive, cfg, job, session)
        if d.get("member"):
            extracted = extract_member(archive, d["member"], sdir, job)
            return extracted, archive, extracted
        return archive, archive, None

    if kind == "assisted":
        # ca_infrastructure wants the folder of GeoPackages, not one file
        if "{src_dir}" in " ".join(d.get("args", [])):
            sdir = staging_dir(cfg, d["id"])
            gpkg = glob.glob(os.path.join(sdir, "*.gpkg"))
            if not gpkg:
                raise IOError(
                    "no .gpkg files in %s - download them from %s and drop them there"
                    % (os.path.relpath(sdir, HERE), d.get("source_page", "the source")))
            job.emit("    [staging] %d GeoPackage(s) found" % len(gpkg))
            return sdir, None, None
        found = find_in_staging(d, cfg, job)
        if not found:
            raise IOError(
                "nothing matching %r in %s - download it from %s and drop it there"
                % (d.get("member") or "*",
                   os.path.relpath(staging_dir(cfg, d["id"]), HERE),
                   d.get("source_page", "the source")))
        job.emit("    [staging] using %s (%s)"
                 % (os.path.basename(found), human_size(os.path.getsize(found))))
        return found, None, None

    raise IOError("dataset %s has acquire=%r and cannot be refreshed here"
                  % (d["id"], kind))


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------
def run_builder(d, src, cfg, job):
    """Run the registered build_*.py in Projects/ with live output."""
    data_dir = resolve(cfg.get("data_dir", ".."))
    script = os.path.join(data_dir, d["builder"])
    if not os.path.exists(script):
        raise IOError("builder not found: %s" % script)

    args = []
    for a in d.get("args", []):
        if a == "{src}":
            args.append(src)
        elif a == "{src_dir}":
            args.append(src)
        elif a == "{census_key}":
            key = (cfg.get("census_api_key") or "").strip()
            if not key:
                raise IOError("census_api_key is empty in refresh_config.json - "
                              "get a free key at https://api.census.gov/data/key_signup.html")
            args.append(key)
        else:
            args.append(a)

    shown = [("<census key>" if a == "{census_key}" else
              os.path.basename(args[i]) if args[i] and os.path.sep in str(args[i]) else args[i])
             for i, a in enumerate(d.get("args", []))]
    job.emit("    [build] %s %s" % (d["builder"], " ".join(str(s) for s in shown)))

    # Builders that fetch over HTTPS themselves (drought, the two Census ones)
    # hit the same Norton-intercepted TLS this tool does. Hand them the merged
    # bundle so they do not each have to solve it.
    env = dict(os.environ)
    bundle = build_ca_bundle(cfg, job.emit)
    if isinstance(bundle, str) and os.path.exists(bundle):
        env["SSL_CERT_FILE"] = bundle
        env["REQUESTS_CA_BUNDLE"] = bundle

    proc = subprocess.Popen(
        [sys.executable, script] + [str(a) for a in args],
        cwd=data_dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        universal_newlines=True, bufsize=1, env=env,
        encoding="utf-8", errors="replace")

    deadline = time.time() + int(cfg.get("builder_timeout", 5400))
    tail = []
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            tail.append(line)
            if len(tail) > 60:
                del tail[0]
            job.emit("      | %s" % line[:300])
        if job.cancel:
            proc.terminate()
            raise KeyboardInterrupt("cancelled during build")
        if time.time() > deadline:
            proc.terminate()
            raise IOError("builder exceeded builder_timeout")
    proc.wait()
    if proc.returncode != 0:
        raise IOError("builder exited %d: %s"
                      % (proc.returncode, " / ".join(tail[-3:]) or "no output"))
    return "\n".join(tail)


_KEYSPACE_CACHE = {}


def key_space(d, cfg):
    """The set of keys the scorer will actually look this dataset up by."""
    name = d.get("key_space")
    if not name:
        return None
    if name not in _KEYSPACE_CACHE:
        path = os.path.join(resolve(cfg.get("data_dir", "..")), name)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                _KEYSPACE_CACHE[name] = set(json.load(fh))
        except Exception:
            _KEYSPACE_CACHE[name] = None
    return _KEYSPACE_CACHE[name]


def usable_keys(path, d, cfg):
    """How many of this file's keys the scorer can match. None when the dataset
    has no declared key space or the file is not a keyed object.

    Record count alone is not a safety check. ca_livability rebuilt from the
    2016 CIMD gained records (283 -> 699) while its usable coverage collapsed
    (283 -> 36) because the 2016 dissemination areas roll up to a different
    code space than the 2021 census divisions the model is keyed on. The count
    went UP, so a floor check waved it through.
    """
    ks = key_space(d, cfg)
    if ks is None:
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return len(set(data) & ks)


def floor_for(d, name):
    """The record floor for one output. A dict gives each output its own."""
    mr = d["min_records"]
    if isinstance(mr, dict):
        return mr.get(name, 0)
    return mr


def validate(d, cfg, job, before):
    """Post-build sanity check. Returns (ok, message)."""
    data_dir = resolve(cfg.get("data_dir", ".."))
    parts = []
    for name in outputs_of(d):
        path = os.path.join(data_dir, name)
        if not os.path.exists(path):
            return False, "%s was not written" % name
        n, err = count_records(path)
        if err:
            return False, "%s is %s" % (name, err)
        if n is None:
            return False, "%s has an unexpected shape" % name
        fl = floor_for(d, name)
        if n < fl:
            return False, ("%s has %d records, below the floor of %d - the source "
                           "has probably changed shape" % (name, n, fl))
        was = (before.get(name) or {}).get("records")
        if was and was > 0:
            delta = 100.0 * (n - was) / was
            # a builder that suddenly drops a third of its records is a source
            # problem, not a refresh, even when it clears the absolute floor
            if delta < -33.0:
                return False, ("%s fell from %d to %d records (%.0f%%) - refusing "
                               "a drop that large" % (name, was, n, delta))
            part = "%s %d (%+.1f%%)" % (name, n, delta)
        else:
            part = "%s %d" % (name, n)

        # The count is not the safety check - usable coverage is. A file can
        # gain records and still lose the scorer, by landing on a different
        # geography vintage whose codes look right and match nothing.
        use = usable_keys(path, d, cfg)
        if use is not None:
            ks = key_space(d, cfg)
            was_use = (before.get(name) or {}).get("usable")
            part += ", %d/%d keys usable" % (use, len(ks))
            if was_use and use < 0.75 * was_use:
                return False, ("%s matches only %d of the %d keys in %s, down "
                               "from %d - the rebuild landed on a different "
                               "geography vintage"
                               % (name, use, len(ks), d["key_space"], was_use))
            if not was_use and use < 0.25 * len(ks):
                return False, ("%s matches only %d of the %d keys in %s - the "
                               "source geography does not line up with the model"
                               % (name, use, len(ks), d["key_space"]))
        parts.append(part)
    return True, ", ".join(parts)


# ---------------------------------------------------------------------------
# "Only update newer" — decide whether there is anything new to build FROM
#
# Re-running every builder on every pass burns hours and bandwidth to rewrite
# byte-identical files. These checks answer one question per dataset: has the
# thing this file is built from changed since the file was built? If not, the
# dataset is skipped and left alone.
#
# The check is per acquire kind, because the evidence differs:
#   download    HTTP Last-Modified / ETag / Content-Length from the server
#   assisted    mtime of whatever you dropped in staging/
#   derive      mtime of the local files named in depends_on
#   census_api  no version to inspect; falls back to the publication cadence
# Any check that cannot answer confidently returns "refresh" rather than
# "skip", so an unknown never silently blocks an update.
# ---------------------------------------------------------------------------
def source_signature(d, cfg, session, job):
    """What the server says about the file, without downloading it."""
    try:
        r = session.get(d["url"], stream=True,
                        timeout=int(cfg.get("download_timeout", 60)),
                        headers={"Range": "bytes=0-0"})
        sig = {"last_modified": r.headers.get("Last-Modified", ""),
               "etag": r.headers.get("ETag", ""),
               "length": (r.headers.get("Content-Range") or "").rsplit("/", 1)[-1]
                         or r.headers.get("Content-Length", "")}
        r.close()
        if r.status_code not in (200, 206):
            return None
        return sig
    except Exception as e:
        job.emit("    [check] could not read source headers: %s" % str(e)[:90])
        return None


def newest_output_mtime(d, cfg):
    data_dir = resolve(cfg.get("data_dir", ".."))
    times = [os.path.getmtime(os.path.join(data_dir, n))
             for n in outputs_of(d)
             if os.path.exists(os.path.join(data_dir, n))]
    return max(times) if times else None


def is_current(d, cfg, job, session):
    """(True, why) when there is nothing newer to build from."""
    out_mtime = newest_output_mtime(d, cfg)
    if out_mtime is None:
        return False, "output missing"

    # a file that failed its own sanity check is not "current" whatever the
    # source says - it needs rebuilding from something
    data_dir = resolve(cfg.get("data_dir", ".."))
    for name in outputs_of(d):
        p = os.path.join(data_dir, name)
        if os.path.exists(p):
            n, err = count_records(p)
            if err or n is None or n < floor_for(d, name):
                return False, "%s is below its floor" % name

    kind = d["acquire"]

    if kind == "derive":
        deps = d.get("depends_on") or []
        if not deps:
            return False, "no dependencies recorded"
        newer = []
        for dep in deps:
            p = os.path.join(data_dir, dep)
            if not os.path.exists(p):
                return False, "input %s not found" % dep
            if os.path.getmtime(p) > out_mtime:
                newer.append(dep)
        if newer:
            return False, "input changed: %s" % ", ".join(newer)
        return True, "inputs unchanged since the last build"

    if kind == "assisted":
        try:
            staged = (find_in_staging(d, cfg, job)
                      if "{src_dir}" not in " ".join(d.get("args", []))
                      else (sorted(glob.glob(os.path.join(staging_dir(cfg, d["id"]),
                                                          "*.gpkg")),
                                   key=os.path.getmtime, reverse=True) or [None])[0])
        except Exception:
            staged = None
        if not staged:
            # An empty staging folder is not a problem to report, it is the
            # normal resting state: there is nothing newer to build from, so
            # there is nothing to do. The missing-file error belongs to a
            # deliberate refresh of this dataset, not to a sweep.
            return True, "nothing new staged"
        if os.path.getmtime(staged) > out_mtime:
            return False, "staged file is newer"
        return True, "staged file is older than the current output"

    if kind in ("census_api", "api"):
        # These APIs carry no version stamp to compare against, so the honest
        # proxy is how often the publisher actually releases: ACS and CBP once
        # a year, USDM weekly but read here as a five-year mean that barely
        # moves between refreshes.
        #
        # Age is measured on the DATA's vintage, not the file's mtime. Using
        # mtime here said "0 days old" about a file holding ACS 2023, which is
        # the whole reason the staleness measure was rewritten.
        iso, prec, vsrc = vintage_of(d, cfg, os.path.join(data_dir, d["output"]))
        age = (time.time() - out_mtime) / 86400.0
        basis = "file date"
        if iso and vsrc != "file date only":
            try:
                age = (date.today()
                       - date(*[int(x) for x in iso.split("-")])).days
                basis = "%s, via %s" % (iso, vsrc)
            except Exception:
                pass
        if age < d["cadence_days"]:
            return True, ("%d days old (%s), inside the %d-day publication cycle"
                          % (age, basis, d["cadence_days"]))
        # Past the cycle - but if the LAST rebuild already landed on exactly this
        # vintage, the publisher has nothing newer and rebuilding again would
        # just re-fetch the same year every run. Report it as old rather than
        # looping on it; "stale" and "fixable" are different claims.
        prev = (load_last_run().get("datasets", {}).get(d["id"]) or {}).get("vintage")
        if iso and prev == iso:
            return True, ("%d days old (%s) and past the %d-day cycle, but the "
                          "last rebuild produced this same vintage - the source "
                          "has nothing newer" % (age, basis, d["cadence_days"]))
        return False, ("%d days old (%s), past the %d-day publication cycle"
                       % (age, basis, d["cadence_days"]))

    if kind == "download":
        sig = source_signature(d, cfg, session, job)
        if not sig:
            return False, "could not read source headers"
        prev = (load_last_run().get("datasets", {}).get(d["id"]) or {}).get("signature")
        if prev and prev == sig:
            return True, "source unchanged since the last successful build"
        if sig.get("last_modified"):
            try:
                from email.utils import parsedate_to_datetime
                pub = parsedate_to_datetime(sig["last_modified"]).timestamp()
                if pub <= out_mtime:
                    return True, ("source published %s, older than the current file"
                                  % sig["last_modified"])
                return False, "source published %s" % sig["last_modified"]
            except Exception:
                pass
        if not prev:
            return False, "no signature recorded from a previous build"
        return False, "source signature changed"

    return False, "unknown acquire kind"


# ---------------------------------------------------------------------------
# Refresh one dataset
# ---------------------------------------------------------------------------
def refresh_one(ds_id, cfg, job, session=None, only_newer=True):
    d = reg.BY_ID[ds_id]
    job.progress["current"] = d["title"]
    job.emit("")
    job.emit("=== %s  (%s)" % (d["title"], reg.ACQUIRE_LABEL[d["acquire"]]))

    if d["acquire"] == "legacy":
        job.emit("    [skip] no builder exists for this file - see the notes column")
        job.stats["skipped"] += 1
        job.record(ds_id, "skipped", "no builder")
        return

    if d["acquire"] == "research":
        job.emit("    [skip] researched dataset - use 'Research sources' instead")
        job.stats["skipped"] += 1
        job.record(ds_id, "skipped", "research-only")
        return

    session = session or new_session(cfg, job.emit)

    if only_newer:
        try:
            current, why = is_current(d, cfg, job, session)
        except Exception as e:
            current, why = False, "freshness check failed (%s)" % str(e)[:80]
        if current:
            job.emit("    [skip] up to date - %s" % why)
            job.stats["skipped"] += 1
            job.record(ds_id, "up_to_date", why)
            return
        job.emit("    [newer] %s" % why)

    data_dir = resolve(cfg.get("data_dir", ".."))
    before = {}
    for name in outputs_of(d):
        p = os.path.join(data_dir, name)
        if os.path.exists(p):
            n, _ = count_records(p)
            before[name] = {"records": n, "usable": usable_keys(p, d, cfg)}

    saved = {}
    archive = extracted = None
    try:
        src, archive, extracted = acquire(d, cfg, job, session)
        saved = backup_outputs(d, cfg, job)
        run_builder(d, src, cfg, job)
        ok, msg = validate(d, cfg, job, before)
        if not ok:
            job.emit("    [!] REJECTED: %s" % msg)
            restore_outputs(saved, cfg, job)
            job.stats["rolled_back"] += 1
            job.record(ds_id, "rolled_back", msg)
            save_last_run(ds_id, {"at": now_iso(), "status": "rolled_back",
                                  "detail": msg})
            return
        job.emit("    [ok] %s" % msg)
        job.stats["ok"] += 1
        job.record(ds_id, "ok", msg)
        entry = {"at": now_iso(), "status": "ok", "detail": msg}
        # remember what the source looked like, so the next run can tell
        # "nothing new was published" from "we have never checked"
        if d["acquire"] == "download":
            sig = source_signature(d, cfg, session, job)
            if sig:
                entry["signature"] = sig
                # Persist BEFORE reading the vintage. vintage_of falls back to
                # the signature recorded in last_run.json, so reading it first
                # would report the PREVIOUS run's Last-Modified and leave the
                # vintage permanently one refresh behind.
                save_last_run(ds_id, entry)
        iso, prec, vsrc = vintage_of(d, cfg, os.path.join(data_dir, d["output"]))
        if iso and vsrc != "file date only":
            entry["vintage"] = iso
            job.emit("    [vintage] data is %s (via %s)" % (iso, vsrc))
        save_last_run(ds_id, entry)
        clean_staging(d, cfg, job, archive, extracted)

    except KeyboardInterrupt:
        if saved:
            restore_outputs(saved, cfg, job)
        job.emit("    [cancelled]")
        job.record(ds_id, "cancelled", "cancelled by user")
        raise
    except Exception as e:
        detail = str(e)[:300]
        job.emit("    [!] FAILED: %s" % detail)
        if saved:
            restore_outputs(saved, cfg, job)
        job.stats["failed"] += 1
        job.record(ds_id, "failed", detail)
        save_last_run(ds_id, {"at": now_iso(), "status": "failed", "detail": detail})


def run_refresh(ids, cfg, job, only_newer=True):
    """Refresh a set of datasets in dependency order.

    only_newer=True (the default) skips any dataset whose source has not moved
    since the file was last built. A dataset that IS rebuilt still drags its
    `then` dependants along, so a derived index is never left describing an
    input that changed underneath it.
    """
    order = reg.ordered(ids)
    pulled = [i for i in order if i not in ids]
    job.progress["total"] = len(order)
    job.progress["done"] = 0
    job.emit("Refreshing %d dataset(s)%s%s"
             % (len(order),
                (" (+%d pulled in as dependants: %s)"
                 % (len(pulled), ", ".join(pulled))) if pulled else "",
                "" if only_newer else "  [FORCED - freshness checks bypassed]"))
    if only_newer:
        job.emit("Only rebuilding what has something newer to build from.")
    session = new_session(cfg, job.emit)
    rebuilt = set()
    for ds_id in order:
        if job.cancel:
            job.emit("[cancelled] stopping before %s" % ds_id)
            break
        # a dependant is always rebuilt when its trigger was, whatever the
        # freshness check would say about it on its own
        forced = any(ds_id in reg.BY_ID[t].get("then", []) for t in rebuilt)
        # ...but one that was pulled in ONLY as a dependant, whose trigger did
        # not rebuild, has nothing to react to. Attempting it anyway is how a
        # dataset nobody asked for ends up reporting a failure.
        if only_newer and ds_id not in ids and not forced:
            job.emit("")
            job.emit("=== %s" % reg.BY_ID[ds_id]["title"])
            job.emit("    [skip] pulled in as a dependant, but its input did not change")
            job.stats["skipped"] += 1
            job.progress["done"] += 1
            continue
        try:
            before_ok = job.stats["ok"]
            refresh_one(ds_id, cfg, job, session,
                        only_newer=only_newer and not forced)
            if job.stats["ok"] > before_ok:
                rebuilt.add(ds_id)
        except KeyboardInterrupt:
            break
        job.progress["done"] += 1
    job.emit("")
    job.emit("--- %d rebuilt, %d failed, %d rolled back, %d skipped, %s downloaded"
             % (job.stats["ok"], job.stats["failed"], job.stats["rolled_back"],
                job.stats["skipped"], human_size(job.stats["bytes"])))


# ---------------------------------------------------------------------------
# Source check — a dry run that touches nothing
# ---------------------------------------------------------------------------
def check_sources(ids, cfg, job):
    session = new_session(cfg, job.emit)
    timeout = int(cfg.get("download_timeout", 60))
    job.progress["total"] = len(ids)
    job.progress["done"] = 0
    job.emit("Checking %d source(s). Nothing is downloaded or rebuilt." % len(ids))
    for ds_id in ids:
        if job.cancel:
            break
        d = reg.BY_ID[ds_id]
        job.progress["current"] = d["title"]
        job.progress["done"] += 1

        if d["acquire"] == "derive":
            job.emit("[--] %-28s derived locally, no source to check" % d["id"])
            continue
        if d["acquire"] == "api":
            try:
                r = session.get(d["url"], timeout=timeout,
                                params=d.get("probe_params") or {},
                                headers={"Accept": "application/json"})
                body = r.text.lstrip()
                # Not every API speaks JSON - NRCan's gazetteer serves CSV - so
                # the expected shape is declared per dataset rather than assumed.
                want = d.get("probe_expect", "json")
                if want == "any":
                    ok = r.status_code == 200 and bool(body)
                elif want == "csv":
                    first = body.splitlines()[0] if body.splitlines() else ""
                    ok = (r.status_code == 200 and "," in first
                          and not body.startswith("<"))
                else:
                    ok = r.status_code == 200 and body[:1] in ("[", "{")
                job.emit("[%s] %-28s live API, HTTP %d%s"
                         % ("ok" if ok else "!!", d["id"], r.status_code,
                            "" if ok else " - did not return %s: %s"
                            % (want, body[:60])))
            except Exception as e:
                job.emit("[!!] %-28s live API unreachable: %s"
                         % (d["id"], str(e)[:80]))
            continue
        if d["acquire"] == "census_api":
            key = (cfg.get("census_api_key") or "").strip()
            if not key:
                job.emit("[!!] %-28s Census API key MISSING in refresh_config.json"
                         % d["id"])
                continue
            # presence is not validity - Census serves an HTML "Invalid Key"
            # page under HTTP 200, which a status check reads as success
            try:
                r = session.get("https://api.census.gov/data/2023/acs/acs5",
                                params={"get": "NAME", "for": "state:01", "key": key},
                                timeout=timeout)
                body = r.text.lstrip()
                if body[:1] == "[":
                    job.emit("[ok] %-28s Census API key works" % d["id"])
                else:
                    title = re.search(r"<title>(.*?)</title>", body)
                    job.emit("[!!] %-28s Census rejected the key: %s"
                             % (d["id"], title.group(1) if title else body[:60]))
            except Exception as e:
                job.emit("[!!] %-28s could not reach the Census API: %s"
                         % (d["id"], str(e)[:70]))
            continue
        if d["acquire"] in ("legacy", "research"):
            job.emit("[--] %-28s %s" % (d["id"], reg.ACQUIRE_LABEL[d["acquire"]]))
            continue
        if d["acquire"] == "assisted":
            try:
                found = find_in_staging(d, cfg, job) if "{src_dir}" not in " ".join(d["args"]) \
                    else (glob.glob(os.path.join(staging_dir(cfg, d["id"]), "*.gpkg")) or [None])[0]
            except Exception:
                found = None
            job.emit("[%s] %-28s %s"
                     % ("ok" if found else "!!", d["id"],
                        ("staged: " + os.path.basename(found)) if found
                        else "waiting for a manual download into staging/%s/" % d["id"]))
            continue

        try:
            r = session.get(d["url"], stream=True, timeout=timeout,
                            headers={"Range": "bytes=0-1023"})
            size = r.headers.get("Content-Range") or r.headers.get("Content-Length") or "?"
            r.close()
            if r.status_code in (200, 206):
                job.emit("[ok] %-28s reachable (%s)" % (d["id"], size))
            else:
                job.emit("[!!] %-28s HTTP %d - use 'Research sources' to re-find it"
                         % (d["id"], r.status_code))
        except Exception as e:
            job.emit("[!!] %-28s unreachable: %s" % (d["id"], str(e)[:90]))
    job.emit("")
    job.emit("--- source check finished")


# ---------------------------------------------------------------------------
# Brave + Claude: re-find a download URL that has moved
# ---------------------------------------------------------------------------
class BraveClient(object):
    def __init__(self, cfg, on_log=log):
        self.key = cfg.get("brave_api_key", "")
        self.endpoint = cfg.get("brave_endpoint")
        self.count = int(cfg.get("brave_results_per_query", 20))
        self.gap = 1.0 / max(1, int(cfg.get("brave_qps", 8)))
        self.on_log = on_log
        self.session = new_session(cfg, on_log)
        self._last = 0.0

    def search(self, query, country="US"):
        wait = self.gap - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()
        try:
            r = self.session.get(
                self.endpoint,
                headers={"Accept": "application/json",
                         "X-Subscription-Token": self.key},
                params={"q": query, "count": self.count, "country": country,
                        "search_lang": "en", "safesearch": "off"},
                timeout=25)
        except Exception as e:
            self.on_log("    [brave] network error: %s" % e)
            return []
        if r.status_code in (401, 403):
            self.on_log("    [brave] AUTH FAILED (%d) - check brave_api_key" % r.status_code)
            return []
        if r.status_code != 200:
            self.on_log("    [brave] HTTP %d" % r.status_code)
            return []
        try:
            data = r.json()
        except ValueError:
            return []
        return [{"title": i.get("title", ""), "url": i.get("url", ""),
                 "snippet": i.get("description", "")}
                for i in (data.get("web") or {}).get("results", []) or []]


class ClaudeClient(object):
    def __init__(self, cfg, on_log=log):
        self.key = load_claude_key(cfg)
        self.endpoint = cfg.get("claude_endpoint")
        self.model = cfg.get("claude_model", "claude-sonnet-5")
        self.max_tokens = int(cfg.get("claude_max_tokens", 4000))
        self.on_log = on_log
        self.session = new_session(cfg, on_log)
        self.calls = 0

    @property
    def enabled(self):
        return bool(self.key)

    def ask(self, system, user):
        if not self.enabled:
            return ""
        body = {"model": self.model, "max_tokens": self.max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": user}]}
        headers = {"x-api-key": self.key, "anthropic-version": "2023-06-01",
                   "content-type": "application/json"}
        for attempt in range(3):
            try:
                r = self.session.post(self.endpoint, headers=headers, json=body,
                                      timeout=120)
            except Exception as e:
                self.on_log("    [claude] network error: %s" % e)
                time.sleep(2.0 * (attempt + 1))
                continue
            if r.status_code in (429, 529):
                time.sleep(4.0 * (attempt + 1))
                continue
            if r.status_code in (401, 403):
                self.on_log("    [claude] key rejected (HTTP %d)" % r.status_code)
                return ""
            if r.status_code != 200:
                self.on_log("    [claude] HTTP %d" % r.status_code)
                return ""
            self.calls += 1
            try:
                return "".join(b.get("text", "")
                               for b in r.json().get("content", []))
            except Exception:
                return ""
        return ""


PICK_SYSTEM = (
    "You identify the direct download URL for a public statistical dataset from "
    "search results. Answer with JSON only, no prose, no code fence:\n"
    '{"url": "<direct file URL or null>", "page": "<landing page URL or null>", '
    '"confidence": "high|medium|low", "why": "<one sentence>"}\n'
    "A direct URL ends in the file itself (.zip .csv .xlsx .gpkg). If no result "
    "is clearly the file, set url to null and give the best landing page instead. "
    "Never invent a URL that is not in the results."
)


def research_source(ds_id, cfg, job, brave=None, claude=None):
    """Find where a dataset's download moved to. Proposes only - the URL is
    written into refresh_config.json's url_overrides when you approve it."""
    d = reg.BY_ID[ds_id]
    brave = brave or BraveClient(cfg, job.emit)
    claude = claude or ClaudeClient(cfg, job.emit)

    hint = d.get("search_hint") or d["title"]
    job.emit("")
    job.emit("=== researching %s" % d["title"])
    job.emit("    [brave] %s" % hint)
    results = brave.search(hint, "CA" if d["country"] == "CA" else "US")
    job.stats["brave_queries"] += 1
    if not results:
        job.emit("    [!] no search results")
        return None

    for r in results[:6]:
        job.emit("      - %s" % r["url"][:110])

    if not claude.enabled:
        job.emit("    [!] no Claude key - showing raw results only")
        return {"id": ds_id, "url": None, "page": results[0]["url"],
                "confidence": "low", "why": "top Brave result (no Claude key)",
                "current": d.get("url"), "results": results[:8]}

    listing = "\n".join("%d. %s\n   %s\n   %s"
                        % (i + 1, r["title"][:120], r["url"], r["snippet"][:200])
                        for i, r in enumerate(results[:12]))
    user = ("Dataset: %s\nPublisher page we know of: %s\nURL we currently use "
            "(may be dead): %s\nThe builder expects a file matching: %s\n\n"
            "Search results:\n%s"
            % (d["title"], d.get("source_page") or "unknown",
               d.get("url") or "none", d.get("member") or "any", listing))
    raw = claude.ask(PICK_SYSTEM, user)
    job.stats["claude_calls"] += 1

    try:
        m = re.search(r"\{.*\}", raw, re.S)
        found = json.loads(m.group(0)) if m else {}
    except Exception:
        found = {}
    if not found:
        job.emit("    [!] could not parse a proposal from Claude")
        return None

    found.update({"id": ds_id, "title": d["title"], "current": d.get("url"),
                  "results": results[:8]})
    job.emit("    [claude] %s (%s) - %s"
             % (found.get("url") or found.get("page") or "nothing",
                found.get("confidence", "?"), found.get("why", "")))

    # verify the proposal actually serves bytes before offering it
    url = found.get("url")
    if url:
        try:
            s = new_session(cfg, job.emit)
            r = s.get(url, stream=True, timeout=int(cfg.get("download_timeout", 60)),
                      headers={"Range": "bytes=0-1023"})
            found["http_status"] = r.status_code
            found["reachable"] = r.status_code in (200, 206)
            r.close()
            job.emit("    [verify] HTTP %d%s" % (r.status_code,
                     "" if found["reachable"] else " - proposal not usable as-is"))
        except Exception as e:
            found["reachable"] = False
            found["http_status"] = 0
            job.emit("    [verify] unreachable: %s" % str(e)[:90])
    return found


def run_research(ids, cfg, job):
    brave = BraveClient(cfg, job.emit)
    claude = ClaudeClient(cfg, job.emit)
    if not claude.enabled:
        job.emit("[!] No Claude key found at %s - results will be unranked."
                 % cfg.get("claude_key_file"))
    job.progress["total"] = len(ids)
    job.progress["done"] = 0
    job.emit("Researching %d source(s). Nothing is downloaded, built or written."
             % len(ids))
    for ds_id in ids:
        if job.cancel:
            break
        job.progress["current"] = reg.BY_ID[ds_id]["title"]
        try:
            found = research_source(ds_id, cfg, job, brave, claude)
            if found:
                with job.lock:
                    job.proposals.append(found)
        except Exception as e:
            job.emit("    [!] %s" % str(e)[:200])
        job.progress["done"] += 1
    job.emit("")
    job.emit("--- research finished: %d proposal(s), %d Brave queries, %d Claude calls"
             % (len(job.proposals), job.stats["brave_queries"], job.stats["claude_calls"]))


def apply_url_override(ds_id, url, cfg):
    """Record an approved URL. Kept in config rather than edited into the
    registry so the registry stays a readable description of the data and the
    config holds the things that rot."""
    cfg = cfg or load_config()
    cfg.setdefault("url_overrides", {})[ds_id] = {"url": url, "set": now_iso()}
    save_config(cfg)
    reg.BY_ID[ds_id]["url"] = url
    return cfg


def apply_overrides(cfg):
    """Fold saved URL overrides into the in-memory registry at startup."""
    for ds_id, entry in (cfg.get("url_overrides") or {}).items():
        if ds_id in reg.BY_ID and entry.get("url"):
            reg.BY_ID[ds_id]["url"] = entry["url"]
            if reg.BY_ID[ds_id]["acquire"] == "assisted":
                reg.BY_ID[ds_id]["acquire"] = "download"


# ---------------------------------------------------------------------------
# CLI — the web UI is the front door, but every action works headless too
# ---------------------------------------------------------------------------
def main(argv):
    import argparse
    p = argparse.ArgumentParser(description="Refresh the My Project scorer's data.")
    p.add_argument("action", choices=["list", "refresh", "check", "research"])
    p.add_argument("ids", nargs="*", help="dataset ids, or 'all' / 'auto' / 'stale'")
    p.add_argument("--force", action="store_true",
                   help="rebuild even when the source has nothing newer")
    a = p.parse_args(argv)

    cfg = load_config()
    apply_overrides(cfg)
    job = Job()

    rows = inventory(cfg)
    by_id = {r["id"]: r for r in rows}

    def expand(ids):
        if not ids or ids == ["auto"]:
            return [r["id"] for r in rows if r["automatable"]]
        if ids == ["all"]:
            return [r["id"] for r in rows]
        if ids == ["stale"]:
            return [r["id"] for r in rows
                    if r["automatable"] and r["status"] in ("stale", "missing", "thin", "broken")]
        unknown = [i for i in ids if i not in reg.BY_ID]
        if unknown:
            sys.exit("unknown dataset id(s): %s" % ", ".join(unknown))
        return ids

    if a.action == "list":
        print("%-26s %-14s %-9s %8s  %s" % ("ID", "ACQUIRE", "STATUS", "RECORDS", "MODIFIED"))
        for r in rows:
            print("%-26s %-14s %-9s %8s  %s"
                  % (r["id"], r["acquire"], r["status"],
                     r["records"] if r["records"] is not None else "-", r["modified"]))
        return 0

    ids = expand(a.ids)
    if a.action == "refresh":
        run_refresh(ids, cfg, job, only_newer=not a.force)
    elif a.action == "check":
        check_sources(ids, cfg, job)
    else:
        run_research(ids, cfg, job)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
