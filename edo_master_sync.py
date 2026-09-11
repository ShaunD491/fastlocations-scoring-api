#!/usr/bin/env python3
r"""
edo_master_sync.py
------------------
Keeps edo_master_table_dual.json in step with the dashboard's EDO list
(dash/data/organizations.json). The Organizations editor (dash/tool, port 5002) calls it after
every save, delete and batch run, and when you accept a researched territory.

What a record gets WITHOUT research - the territory rules, by Category:

    State Agency              every county in the state / every census division in the province
    Regional, Utility         the home county / division only, basis '..._only_INCOMPLETE',
                              until a researched member list is accepted
    everything else           the home county / division

The home county comes from the record's coordinates via a `locate(lat, lon, country)` callable the
caller supplies (the editor uses the Census geocoder and the StatCan DA lookup it already has).

Researched member lists are never written straight into the master: the editor queues them for
review, and accept_members() records the accepted list in regional_edo_members.json - the file
build_regional_territories.py reads - so a rerun of that script reproduces them.

Territories that came from research or EIA-861 are kept when only a record's name, links or
coordinates change. They are reset to the rule above only when Category, State or Country
changes, because those are what they were derived from.

Every write backs up the master, both indexes and the members file to
_data_backups/master_sync/<timestamp>/ first, then rebuilds the routing indexes.

commit_and_push() commits just those files and pushes them to main (which Railway deploys). It
refuses to push if that would also publish commits of your own that are not on main yet.
"""
import json, os, re, shutil, subprocess, threading, time
from collections import defaultdict

from build_edo_indexes import write_indexes
from build_regional_territories import county_lookup, resolve_counties, apply_members
from check_edo_roster import clean, num, km_between, MOVED_KM

HERE = os.path.dirname(os.path.abspath(__file__))
MASTER = os.path.join(HERE, "edo_master_table_dual.json")
US_INDEX = os.path.join(HERE, "edo_fips_index.json")
CA_INDEX = os.path.join(HERE, "edo_ca_cd_index.json")
MEMBERS = os.path.join(HERE, "regional_edo_members.json")
FEATURES = os.path.join(HERE, "county_features.json")
CA_FEATURES = os.path.join(HERE, "ca_features.json")
BACKUP_ROOT = os.path.join(HERE, "_data_backups", "master_sync")
KEEP_BACKUPS = 40

# The files a sync can touch - the only ones commit_and_push() ever commits.
SYNC_FILES = [os.path.basename(p) for p in (MASTER, US_INDEX, CA_INDEX, MEMBERS)]
AUTO_TRAILER = "Auto-sync: organizations-editor"

STATEWIDE = {"State Agency"}
NEEDS_RESEARCH = {"Regional Development Agency", "Utility"}
UNENUMERATED = "multi_county_territory_unenumerated"
UNENUMERATED_CA = "multi_cd_territory_unenumerated"     # the Canadian rows' spelling
# Bases that are just "the home county", so they follow the record when it moves.
HOME_BASES = {"home_county", "home_cd", "home_county_only_INCOMPLETE", "home_cd_only_INCOMPLETE"}
# Keys that describe where an old territory came from; dropped when the rule replaces it.
TERRITORY_PROVENANCE = ("territory_source", "territory_note", "member_counties_source")

# dashboard field -> master field, copied verbatim
COPY_FIELDS = [("Organization", "organization"), ("City", "city"), ("Embed", "embed_url"),
               ("AI Link", "ai_link"), ("Latitude", "latitude"), ("Longitude", "longitude")]

_LOCK = threading.RLock()    # the editor is threaded; one writer at a time


# --------------------------------------------------------------------------
# Files
# --------------------------------------------------------------------------

def load_master():
    return json.load(open(MASTER, encoding="utf-8"))


def _style(path):
    """(newline, trailing newline?) the file already uses, so a rewrite is a minimal diff."""
    try:
        b = open(path, "rb").read()
    except OSError:
        return "\n", False
    return ("\r\n" if b"\r\n" in b[:8192] else "\n"), b.endswith(b"\n")


def _write_json(path, payload, indent):
    nl, trailing = _style(path)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline=nl) as fh:
        json.dump(payload, fh, indent=indent, ensure_ascii=False)
        if trailing:
            fh.write("\n")
    for attempt in range(5):              # OneDrive briefly locks files it is syncing
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.5 * (attempt + 1))
    os.replace(tmp, path)


_STR_LIST = re.compile(r'\[\s*\n\s*("(?:[^"\\]|\\.)*"(?:,\s*\n\s*"(?:[^"\\]|\\.)*")*)\s*\n\s*\]')


def _write_members(members):
    """regional_edo_members.json keeps each county list on one line; json.dump alone would put
    every county on its own line and turn a one-agency edit into a whole-file diff."""
    text = json.dumps(members, indent=2, ensure_ascii=False)
    text = _STR_LIST.sub(lambda m: "[" + re.sub(r",\s*\n\s*", ", ", m.group(1)) + "]", text)
    nl, trailing = _style(MEMBERS)
    tmp = MEMBERS + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline=nl) as fh:
        fh.write(text + ("\n" if trailing else ""))
    os.replace(tmp, MEMBERS)


def backup():
    stamp = time.strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(BACKUP_ROOT, stamp)
    n = 1
    while os.path.exists(dest):
        n += 1
        dest = os.path.join(BACKUP_ROOT, "%s_%d" % (stamp, n))
    os.makedirs(dest)
    for p in (MASTER, US_INDEX, CA_INDEX, MEMBERS):
        if os.path.exists(p):
            shutil.copy2(p, dest)
    for old in sorted(os.listdir(BACKUP_ROOT))[:-KEEP_BACKUPS]:
        shutil.rmtree(os.path.join(BACKUP_ROOT, old), ignore_errors=True)
    return dest


def _save(master):
    _write_json(MASTER, master, 1)
    write_indexes(master)


# --------------------------------------------------------------------------
# Reference geography
# --------------------------------------------------------------------------

_REF = {}


def ref():
    """County / census-division names and per-state lists, reloaded if the files change."""
    key = tuple(os.path.getmtime(p) for p in (FEATURES, CA_FEATURES))
    if _REF.get("key") != key:
        us = json.load(open(FEATURES, encoding="utf-8"))
        ca = json.load(open(CA_FEATURES, encoding="utf-8"))
        by_state = defaultdict(list)
        for g, d in us.items():
            by_state[d["ST_ABBREV"]].append(g)
        for g, d in ca.items():
            by_state[d["ST_ABBREV"]].append(g)
        _REF.clear()
        _REF.update(key=key,
                    names=dict([(g, d["NAME"]) for g, d in us.items()]
                               + [(g, d["NAME"]) for g, d in ca.items()]),
                    by_state=dict((k, sorted(v)) for k, v in by_state.items()))
    return _REF


def names_in(state):
    """Sorted county / census-division names in one state or province (for research prompts)."""
    r = ref()
    return sorted(r["names"][g] for g in r["by_state"].get(state, []))


def geo_name(geoid):
    return ref()["names"].get(geoid)


# --------------------------------------------------------------------------
# Territory rules
# --------------------------------------------------------------------------

def is_ca(row):
    return row.get("geo_system") == "CA_CSD"


def home_of(row):
    home = row.get("home_cduid") if is_ca(row) else row.get("home_county_fips")
    # Nine Canadian EDOs were matched to their division by hand and carry it only as their
    # single territory entry, with no home_cduid.
    if not home and row.get("territory_basis") in HOME_BASES and len(row.get("territory_geoids") or []) == 1:
        home = row["territory_geoids"][0]
    return home


def needs_research(row):
    return (row.get("category") in NEEDS_RESEARCH
            and str(row.get("territory_basis", "")).endswith("INCOMPLETE"))


def _set_territory(row, geo, basis, status, flags):
    row["territory_geoids"] = geo
    if is_ca(row):
        row["territory_cd_uids"] = geo
        row["territory_fips"] = []
    else:
        row["territory_fips"] = geo
    row["territory_county_count"] = len(geo)
    row["territory_basis"] = basis
    row["resolution_status"] = status
    row["flags"] = flags
    for k in TERRITORY_PROVENANCE:
        row.pop(k, None)


def apply_rule(row, warn):
    """Set the territory from Category alone (see the module docstring)."""
    ca = is_ca(row)
    home = home_of(row)
    flags = [f for f in row.get("flags") or [] if f not in (UNENUMERATED, UNENUMERATED_CA)]
    unit = "home_cd" if ca else "home_county"
    if row.get("category") in STATEWIDE:
        geo = ref()["by_state"].get(row.get("state"), [])
        if geo:
            return _set_territory(row, list(geo), "province" if ca else "whole_state", "resolved", flags)
        warn("#%s %s: no counties known for state %r - using the home %s only"
             % (row["objectid"], row["organization"], row.get("state"), "division" if ca else "county"))
    if not home:
        warn("#%s %s: no home %s (coordinates missing or unresolved) - it will receive NO leads"
             % (row["objectid"], row["organization"], "division" if ca else "county"))
        return _set_territory(row, [], unit + "_unresolved", "unresolved", flags)
    if row.get("category") in NEEDS_RESEARCH:
        return _set_territory(row, [home], unit + "_only_INCOMPLETE", "partial",
                              flags + [UNENUMERATED_CA if ca else UNENUMERATED])
    return _set_territory(row, [home], unit, "resolved", flags)


def _set_home(row, loc, warn):
    """loc = {"geoid", "name"} from the caller's locate(); names are taken from our own reference
    files when the geoid is in them, so the master reads the same as every other row."""
    g = loc.get("geoid")
    name = geo_name(g)
    if not name:
        warn("#%s %s: located in %s (%s), which is not in %s - it can route leads but that area "
             "is not scored" % (row["objectid"], row["organization"], g, loc.get("name"),
                                os.path.basename(CA_FEATURES if is_ca(row) else FEATURES)))
        name = loc.get("name")
    if is_ca(row):
        row["home_csduid"] = loc.get("csduid")
        row["home_cduid"] = g
        row["home_cd_name"] = name
    else:
        row["home_county_fips"] = g
        row["home_county_name"] = name


def _locate(row, locate, warn):
    lat, lon = num(row.get("latitude")), num(row.get("longitude"))
    if lat is None or lon is None:
        return None
    try:
        return locate(lat, lon, row.get("country"))
    except Exception as e:                # network trouble must not lose the rest of the sync
        warn("#%s %s: could not locate %s,%s (%s)" % (row["objectid"], row["organization"], lat, lon, e))
        return None


def new_row(d, locate, warn):
    ca = clean(d.get("Country")) == "Canada"
    row = {"objectid": clean(d.get("OBJECTID")), "organization": d.get("Organization", ""),
           "category": d.get("Category", ""), "city": d.get("City", ""), "state": clean(d.get("State")),
           "country": "Canada" if ca else "US", "latitude": d.get("Latitude", ""),
           "longitude": d.get("Longitude", ""), "home_county_fips": None, "home_county_name": None,
           "territory_fips": [], "territory_county_count": 0, "territory_basis": "",
           "resolution_status": "", "flags": [],
           "geo_system": "CA_CSD" if ca else "US_FIPS",
           "geoid_type": "census_division_uid" if ca else "county_fips"}
    if ca:
        row.update(province=row["state"], territory_geoids=[], home_csduid=None, home_cduid=None,
                   home_cd_name=None)
    else:
        row["territory_geoids"] = []
    row["embed_url"] = d.get("Embed", "")
    row["ai_link"] = d.get("AI Link", "")
    if ca:
        row["territory_cd_uids"] = []
    loc = _locate(row, locate, warn)
    if loc:
        _set_home(row, loc, warn)
    apply_rule(row, warn)
    return row


def _describe(row):
    basis = row.get("territory_basis")
    if basis in HOME_BASES:
        return "%s (%s)" % (basis, row.get("home_cd_name") if is_ca(row) else row.get("home_county_name"))
    return "%s, %d %s" % (basis, row.get("territory_county_count") or 0,
                          "divisions" if is_ca(row) else "counties")


def update_row(row, d, locate, warn):
    """Bring one existing master row in line with its dashboard record.
    Returns (changed field names, territory change text or None)."""
    changed = []
    old_lat, old_lon = num(row.get("latitude")), num(row.get("longitude"))
    for df, mf in COPY_FIELDS:
        if clean(d.get(df)) != clean(row.get(mf)):
            row[mf] = d.get(df, "")
            changed.append(mf)
    state_changed = clean(d.get("State")) != clean(row.get("state"))
    cat_changed = clean(d.get("Category")) != clean(row.get("category"))
    if state_changed:
        row["state"] = clean(d.get("State"))
        if is_ca(row):
            row["province"] = row["state"]
        row["flags"] = [f for f in row.get("flags") or [] if not f.startswith("state_field_mismatch:")]
        changed.append("state")
    if cat_changed:
        row["category"] = d.get("Category", "")
        changed.append("category")

    new_lat, new_lon = num(row.get("latitude")), num(row.get("longitude"))
    moved = (None not in (old_lat, old_lon, new_lat, new_lon)
             and km_between(old_lat, old_lon, new_lat, new_lon) > MOVED_KM)
    # Provincial agencies were built without a home division and do not need one.
    tracks_home = (home_of(row) is not None or row.get("territory_basis") in HOME_BASES
                   or row.get("category") not in STATEWIDE)
    need_home = tracks_home and (moved or state_changed or not home_of(row))
    need_rule = cat_changed or state_changed or (need_home and row.get("territory_basis") in HOME_BASES)
    if not (need_home or need_rule):
        return changed, None

    before = _describe(row)
    old_home = home_of(row)
    if need_home:
        loc = _locate(row, locate, warn)
        if loc:
            _set_home(row, loc, warn)
        elif moved:
            warn("#%s %s: moved but could not be re-located - home kept at %s"
                 % (row["objectid"], row["organization"], old_home))
    if need_rule:
        apply_rule(row, warn)
    after = _describe(row)
    if home_of(row) != old_home:
        changed.append("home")
    return changed, (None if after == before else "%s -> %s" % (before, after))


def sync(orgs, locate, only=None, log=print):
    """Bring the master in line with `orgs` (dashboard records, as dicts).

    only: OBJECTIDs to reconcile (an id no longer in `orgs` means it was deleted), or None for
    every record. Writes the master and rebuilds the indexes only when something changed.
    Returns a summary: added / removed / updated / territory / research / warnings / lines.
    """
    warnings = []

    def warn(msg):
        warnings.append(msg)
        log("[master] ! " + msg)

    with _LOCK:
        master = load_master()
        by_id = dict((r["objectid"], r) for r in master)
        dash = dict((clean(r.get("OBJECTID")), r) for r in orgs if clean(r.get("OBJECTID")))
        ids = set(dash) | set(by_id) if only is None else set(clean(i) for i in only)

        added, removed, updated, territory = [], [], [], []
        for oid in sorted(ids, key=lambda x: int(x) if x.isdigit() else 0):
            d, row = dash.get(oid), by_id.get(oid)
            if d and not row:
                row = new_row(d, locate, warn)
                name = row["organization"].lower()
                at = next((k for k, r in enumerate(master) if r["organization"].lower() > name),
                          len(master))
                master.insert(at, row)
                by_id[oid] = row
                added.append((oid, row["organization"], _describe(row)))
            elif row and not d:
                master.remove(row)
                del by_id[oid]
                removed.append((oid, row["organization"]))
            elif row and d:
                if clean(d.get("Country")) != clean(row.get("country")):
                    # US <-> Canada changes every geography key; rebuild the row in place
                    fresh = new_row(d, locate, warn)
                    master[master.index(row)] = fresh
                    by_id[oid] = fresh
                    updated.append((oid, fresh["organization"], ["country"]))
                    territory.append((oid, fresh["organization"], "rebuilt for " + fresh["country"]))
                    continue
                fields, tchange = update_row(row, d, locate, warn)
                if fields:
                    updated.append((oid, row["organization"], fields))
                if tchange:
                    territory.append((oid, row["organization"], tchange))

        research = [oid for oid in ids if oid in by_id and needs_research(by_id[oid])
                    and (oid in [a[0] for a in added] or oid in [t[0] for t in territory])]

        lines = (["Added #%s %s (%s)" % a for a in added]
                 + ["Removed #%s %s" % r for r in removed]
                 + ["Updated #%s %s: %s" % (o, n, ", ".join(f)) for o, n, f in updated]
                 + ["Territory #%s %s: %s" % t for t in territory])
        summary = {"changed": bool(lines), "added": added, "removed": removed, "updated": updated,
                   "territory": territory, "research": research, "warnings": warnings,
                   "lines": lines, "backup": None}
        if lines:
            summary["backup"] = backup()
            _save(master)
            for ln in lines:
                log("[master] " + ln)
        return summary


def accept_members(oid, org, counties, source, log=print):
    """Record a reviewed member list in regional_edo_members.json and apply it to the master.
    counties = {"IN": ["Allen", ...]}. Returns a summary like sync()."""
    with _LOCK:
        geo, unmatched = resolve_counties(counties, county_lookup())
        if not geo:
            raise ValueError("none of the counties matched: %s" % ", ".join(unmatched))
        master = load_master()
        row = next((r for r in master if r["objectid"] == str(oid)), None)
        if row is None:
            raise ValueError("#%s is not in the master table - save the record first" % oid)
        before = _describe(row)
        path = backup()
        members = json.load(open(MEMBERS, encoding="utf-8"))
        members["members"][str(oid)] = {"org": org, "source": source, "counties": counties}
        _write_members(members)
        apply_members(row, geo, source)
        _save(master)
        line = "Territory #%s %s: %s -> %s (researched, %s)" % (oid, org, before, _describe(row), source)
        log("[master] " + line)
        return {"changed": True, "lines": [line], "unmatched": unmatched, "backup": path,
                "warnings": ["not matched: " + ", ".join(unmatched)] if unmatched else []}


def research_candidates(master=None):
    """Master rows whose territory is still a home-county placeholder."""
    return [r for r in (master or load_master()) if needs_research(r)]


# --------------------------------------------------------------------------
# Deploy: commit just the sync files and push them to main
# --------------------------------------------------------------------------

def _git(*args, timeout=120):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    kw = {"creationflags": 0x08000000} if os.name == "nt" else {}   # no console window flashes
    r = subprocess.run(["git", "-C", HERE] + list(args), capture_output=True, text=True,
                       timeout=timeout, env=env, **kw)
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()


def commit_and_push(lines, branch="main", log=print):
    """Commit the sync files (and nothing else) and push to `branch`.

    Refuses to push when HEAD carries commits that are not auto-sync commits and not on
    origin/<branch> yet - pushing would deploy your unfinished work along with the sync.
    Returns {"ok", "committed", "pushed", "message", "commit"}.
    """
    out = {"ok": False, "committed": False, "pushed": False, "message": "", "commit": ""}
    with _LOCK:
        code, dirty, err = _git("status", "--porcelain", "--", *SYNC_FILES)
        if code:
            out["message"] = "git status failed: %s" % err
            return out
        if dirty:
            msg = ("Sync EDO master from the Organizations editor\n\n"
                   + "\n".join("- " + ln for ln in lines[:60])
                   + ("\n- ... and %d more" % (len(lines) - 60) if len(lines) > 60 else "")
                   + "\n\n" + AUTO_TRAILER)
            code, _, err = _git("commit", "-m", msg, "--", *SYNC_FILES)
            if code:
                out["message"] = "commit failed: %s" % err
                return out
            # OneDrive can swallow git's object writes; make sure every blob really landed
            for f in SYNC_FILES:
                if _git("cat-file", "-e", "HEAD:" + f)[0]:
                    out["message"] = ("commit made but the object for %s is missing (OneDrive) - "
                                      "run git fsck in Projects before pushing" % f)
                    return out
            out["committed"] = True
            log("[deploy] committed %s" % _git("rev-parse", "--short", "HEAD")[1])

    code, _, err = _git("fetch", "origin", branch, timeout=180)
    if code:
        out["message"] = "fetch failed: %s" % err
        return out
    if _git("merge-base", "--is-ancestor", "origin/" + branch, "HEAD")[0]:
        out["message"] = ("origin/%s has commits this checkout does not - pull them into Projects, "
                          "then press Push now" % branch)
        return out
    ahead = _git("rev-list", "origin/%s..HEAD" % branch)[1].split()
    if not ahead:
        out.update(ok=True, message="nothing to push - main is up to date")
        return out
    foreign = [c for c in ahead if AUTO_TRAILER not in _git("log", "-1", "--format=%B", c)[1]]
    if foreign:
        out["message"] = ("not pushed: this branch has %d commit(s) of your own that are not on %s "
                          "yet, and pushing would deploy them too. Push or merge them yourself; the "
                          "sync commit goes with them." % (len(foreign), branch))
        return out
    code, _, err = _git("push", "origin", "HEAD:" + branch, timeout=180)
    if code:
        out["message"] = "push failed: %s" % err
        return out
    head = _git("rev-parse", "--short", "HEAD")[1]
    out.update(ok=True, pushed=True, commit=head,
               message="pushed %s to %s (%d commit%s)" % (head, branch, len(ahead),
                                                          "" if len(ahead) == 1 else "s"))
    # keep the working branch's remote copy level too, when that is a plain fast-forward
    cur = _git("rev-parse", "--abbrev-ref", "HEAD")[1]
    if cur not in ("HEAD", branch) and not _git("rev-parse", "--verify", "origin/" + cur)[0] \
            and not _git("merge-base", "--is-ancestor", "origin/" + cur, "HEAD")[0]:
        _git("push", "origin", "HEAD:" + cur, timeout=180)
    log("[deploy] " + out["message"])
    return out
