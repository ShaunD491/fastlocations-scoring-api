# -*- coding: utf-8 -*-
"""
FastLocations Data Refresh — local web interface
================================================
Runs on your machine only (http://localhost:5003). Front-end for
refresh_engine.py.

  * See every file the My Project scorer reads: age, record count, health.
  * Refresh one dataset, a selection, or everything that can run unattended.
  * Check sources without downloading anything.
  * When a download URL dies, find the new one with Brave + Claude and
    approve it before it is saved.

Port 5003 so it runs alongside the Incentives tool (5001) and the
Organizations editor (5002).

Requirements:
    pip install flask requests

Usage:
    python "...\\Projects\\tool\\refresh_web.py"
    -> open http://localhost:5003
"""

import json
import os
import threading

from flask import Flask, request, jsonify

import refresh_engine as eng
import refresh_registry as reg

app = Flask(__name__)

JOB = eng.Job()
LOCK = threading.Lock()


def start_job(title, fn):
    with LOCK:
        if JOB.running:
            return False
        JOB.running = True
        JOB.cancel = False
        JOB.title = title
        JOB.log = []
        JOB.results = []
        JOB.proposals = []
        JOB.progress = {"done": 0, "total": 0, "current": ""}
        for k in JOB.stats:
            JOB.stats[k] = 0

    def wrapper():
        try:
            fn()
        except Exception as e:
            JOB.emit("[!] ERROR: %s" % e)
        finally:
            JOB.emit("=== job finished ===")
            JOB.running = False

    threading.Thread(target=wrapper, daemon=True).start()
    return True


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FastLocations &mdash; Data Refresh</title>
<style>
  :root{
    --bg:#f5f7fa; --panel:#fff; --ink:#1a2330; --muted:#5d6b7f;
    --line:#dde3ec; --accent:#0b6bcb; --accent-dk:#08528f;
    --ok:#177245; --warn:#b26a00; --bad:#c0392b; --mute:#8b97a8;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
       font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif}
  header{background:#0f1b2d;color:#fff;padding:16px 22px;display:flex;
         align-items:center;gap:14px;flex-wrap:wrap}
  header h1{font-size:18px;margin:0;font-weight:600;letter-spacing:.2px}
  header .sub{color:#9fb2cc;font-size:13px}
  .wrap{max-width:1320px;margin:0 auto;padding:22px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:10px;
        padding:18px 20px;margin-bottom:18px;box-shadow:0 1px 2px rgba(16,30,54,.05)}
  .card h2{font-size:14px;text-transform:uppercase;letter-spacing:.6px;
           color:var(--muted);margin:0 0 14px;font-weight:600}
  button{border:0;border-radius:6px;padding:9px 16px;font:inherit;font-weight:600;
         cursor:pointer;background:var(--accent);color:#fff}
  button:hover{background:var(--accent-dk)}
  button.ghost{background:#eef2f8;color:var(--ink);border:1px solid var(--line)}
  button.ghost:hover{background:#e3e9f3}
  button.danger{background:var(--bad)}
  button:disabled{opacity:.45;cursor:not-allowed}
  .btns{display:flex;gap:10px;flex-wrap:wrap;margin-top:4px;align-items:center}
  .hint{color:var(--muted);font-size:13px;margin-top:10px}
  .chk{display:flex;gap:9px;align-items:flex-start;font-size:13px;
       color:var(--muted);line-height:1.45;max-width:860px}
  .chk input{margin-top:3px;flex:none}
  .chk b{color:var(--ink)}
  table{width:100%;border-collapse:collapse;font-size:13.5px}
  th,td{text-align:left;padding:7px 9px;border-bottom:1px solid var(--line);
        vertical-align:top}
  th{color:var(--muted);font-size:11.5px;text-transform:uppercase;
     letter-spacing:.4px;position:sticky;top:0;background:#fff;z-index:2}
  td.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
  tr.grp td{background:#eef2f8;font-weight:600;font-size:12px;
            text-transform:uppercase;letter-spacing:.5px;color:var(--muted)}
  tr:hover td{background:#fafcff}
  tr.grp:hover td{background:#eef2f8}
  .pill{display:inline-block;padding:1px 8px;border-radius:99px;font-size:11.5px;
        font-weight:600;white-space:nowrap}
  .s-ok{background:#e6f4ec;color:var(--ok)}
  .s-stale{background:#fdf1de;color:var(--warn)}
  .s-newest{background:#eef2f8;color:var(--muted)}
  .s-missing,.s-broken,.s-thin{background:#fdeaea;color:var(--bad)}
  .tag{display:inline-block;padding:1px 7px;border-radius:4px;font-size:11px;
       background:#eef2f8;color:var(--muted);white-space:nowrap}
  .tag.auto{background:#e8f0fb;color:var(--accent-dk)}
  .tag.manual{background:#fdf1de;color:var(--warn)}
  .tag.none{background:#f1f3f6;color:var(--mute)}
  .ttl{font-weight:600}
  .sub2{color:var(--muted);font-size:12px}
  .notes{color:var(--muted);font-size:12px;max-width:300px;line-height:1.4}
  .unk{color:var(--mute);font-style:italic;font-size:12px}
  .fname{font-family:Consolas,"Courier New",monospace;font-size:12.5px;
         color:var(--ink);word-break:break-all}
  #log{background:#0d1420;color:#cfe3ff;
       font:12.5px/1.55 Consolas,"Courier New",monospace;
       padding:14px;border-radius:8px;height:340px;overflow:auto;
       white-space:pre-wrap;word-break:break-word}
  .bar{height:9px;background:#e3e9f3;border-radius:99px;overflow:hidden;
       margin:10px 0 6px}
  .bar > i{display:block;height:100%;background:var(--accent);width:0;
           transition:width .3s}
  .stats{display:flex;gap:20px;flex-wrap:wrap;font-size:13px;color:var(--muted);
         margin-top:8px}
  .stats b{color:var(--ink);font-variant-numeric:tabular-nums}
  .prop{border:1px solid var(--line);border-radius:8px;padding:12px 14px;
        margin-bottom:10px;background:#fbfcfe}
  .prop code{font-size:12px;word-break:break-all;color:var(--accent-dk)}
  .prop .row2{display:flex;gap:10px;align-items:center;margin-top:9px;
              flex-wrap:wrap}
  a{color:var(--accent-dk)}
  .scroller{max-height:620px;overflow:auto;border:1px solid var(--line);
            border-radius:8px}
</style>
</head>
<body>
<header>
  <h1>FastLocations &mdash; Data Refresh</h1>
  <span class="sub">the data behind Where to Expand: My Project</span>
  <span class="sub" id="hdr" style="margin-left:auto"></span>
</header>

<div class="wrap">

  <div class="card">
    <h2>Run</h2>
    <div class="btns">
      <button id="bSel">Refresh selected</button>
      <button id="bAuto" class="ghost">Refresh everything automatable</button>
      <button id="bStale" class="ghost">Refresh what is stale</button>
      <button id="bCheck" class="ghost">Check sources only</button>
      <button id="bResearch" class="ghost">Research sources (Brave + Claude)</button>
      <button id="bCancel" class="danger" disabled>Cancel</button>
    </div>
    <label class="chk" style="margin-top:12px">
      <input type="checkbox" id="onlyNewer" checked>
      <span><b>Only update newer files</b> &mdash; skip anything whose source has not
      moved since the file was last built. Downloads are judged on the server's
      Last-Modified and ETag, staged files and derived builds on their inputs'
      timestamps. Untick to force a rebuild regardless.</span>
    </label>
    <p class="hint" id="hint">
      Every refresh backs up the current file first, then rebuilds it, then counts
      the records. A rebuild that lands under its floor &mdash; or loses more than a
      third of its records &mdash; is rejected and the backup is restored, so a source
      that has quietly changed shape cannot leave the scorer with a truncated file.
      <br><br><b>Data vintage, not file date.</b> Staleness is judged on how old the
      DATA is &mdash; read from the file's own provenance, a per-record reference, or
      the source's Last-Modified &mdash; because rewriting a file does not make its
      contents newer. Hover the vintage to see where it was read from. Where it
      genuinely cannot be determined the column says <i>unknown</i> rather than
      quoting the file date as if it meant something.
    </p>
    <div class="bar"><i id="bar"></i></div>
    <div class="stats">
      <span>Job: <b id="jtitle">idle</b></span>
      <span>Progress: <b id="jprog">0 / 0</b></span>
      <span>Now: <b id="jcur">-</b></span>
      <span>Rebuilt: <b id="sok">0</b></span>
      <span>Skipped as current: <b id="sskip">0</b></span>
      <span>Failed: <b id="sfail">0</b></span>
      <span>Rolled back: <b id="srb">0</b></span>
      <span>Downloaded: <b id="sbytes">0 B</b></span>
    </div>
  </div>

  <div class="card">
    <h2>Log</h2>
    <div id="log"></div>
  </div>

  <div class="card" id="propCard" style="display:none">
    <h2>Source proposals &mdash; nothing is saved until you approve it</h2>
    <div id="props"></div>
  </div>

  <div class="card">
    <h2>Datasets the scorer reads</h2>
    <div class="btns" style="margin-bottom:12px">
      <button class="ghost" id="selAuto">Select automatable</button>
      <button class="ghost" id="selStale">Select stale</button>
      <button class="ghost" id="selNone">Clear selection</button>
      <span class="sub2" id="selCount"></span>
    </div>
    <div class="scroller">
      <table>
        <thead><tr>
          <th style="width:28px"></th>
          <th>Dataset</th>
          <th>File</th>
          <th>Type</th>
          <th class="n">Size</th>
          <th class="n">Data vintage</th>
          <th class="n">File written</th>
          <th class="n">Records</th>
          <th>Status</th>
          <th>Refresh</th>
          <th>Notes</th>
        </tr></thead>
        <tbody id="tb"></tbody>
      </table>
    </div>
  </div>

</div>

<script>
let ROWS = [];
const sel = new Set();

function esc(s){ return String(s==null?'':s).replace(/[&<>"]/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

const GROUPS = [
  ['Automatable — download or API', r => r.acquire==='download'||r.acquire==='census_api'||r.acquire==='api'],
  ['Derived locally — no network',  r => r.acquire==='derive'],
  ['Manual download into staging/', r => r.acquire==='assisted'],
  ['Researched — Brave + Claude',   r => r.acquire==='research'],
  ['No builder — legacy files',     r => r.acquire==='legacy'],
];

function tagClass(a){
  if(a==='download'||a==='census_api'||a==='api'||a==='derive') return 'auto';
  if(a==='assisted'||a==='research') return 'manual';
  return 'none';
}

function render(){
  const tb = document.getElementById('tb');
  let html = '';
  for(const [label, test] of GROUPS){
    const rows = ROWS.filter(test);
    if(!rows.length) continue;
    html += '<tr class="grp"><td colspan="11">'+esc(label)+' &nbsp;('+rows.length+')</td></tr>';
    for(const r of rows){
      const canPick = r.acquire !== 'legacy';
      const lr = r.last_refresh && r.last_refresh.at
        ? '<div class="sub2">last run '+esc(r.last_refresh.at)+' &middot; '+esc(r.last_refresh.status)+'</div>' : '';
      // a builder with more than one output names them all, so the row is not
      // silently claiming to be one file when it writes two
      const extra = r.outputs && r.outputs.length > 1
        ? '<div class="sub2">+ '+esc(r.outputs.slice(1).join(', '))+'</div>' : '';
      html += '<tr>'
        + '<td>'+(canPick
            ? '<input type="checkbox" data-id="'+r.id+'"'+(sel.has(r.id)?' checked':'')+'>'
            : '')+'</td>'
        + '<td><span class="ttl">'+esc(r.title)+'</span>'
            + '<div class="sub2">'+esc(r.feeds)+' &middot; '+esc(r.country)+'</div>'+lr+'</td>'
        + '<td><span class="fname">'+esc(r.output)+'</span>'+extra
            + (r.builder ? '<div class="sub2">&larr; '+esc(r.builder)+'</div>' : '')+'</td>'
        + '<td><span class="tag">'+esc(r.ftype)+'</span></td>'
        + '<td class="n">'+esc(r.size_h)+'</td>'
        + '<td class="n" title="'+esc(r.vintage_src||'')+'">'
            + (r.vintage_known
                 ? esc(r.vintage)+'<div class="sub2">'+Math.round(r.vintage_age_days)+'d old</div>'
                 : '<span class="unk">unknown</span>')+'</td>'
        + '<td class="n" title="'+esc(r.modified_full)+'"><span class="sub2">'+esc(r.modified)
            + (r.age_days!=null ? ' &middot; '+Math.round(r.age_days)+'d' : '')+'</span></td>'
        + '<td class="n">'+(r.records==null?'-':r.records.toLocaleString())+'</td>'
        + '<td><span class="pill s-'+esc(r.status)+'">'+esc(r.status)+'</span></td>'
        + '<td><span class="tag '+tagClass(r.acquire)+'">'+esc(r.acquire_label)+'</span>'
            + (r.source_page ? '<div class="sub2"><a href="'+esc(r.source_page)
                +'" target="_blank" rel="noopener">source</a></div>' : '')+'</td>'
        + '<td class="notes">'+esc(r.notes)+'</td>'
        + '</tr>';
    }
  }
  // every row must land in exactly one group - a new acquire kind that no
  // filter matches would otherwise just vanish from the table
  const shown = new Set();
  for(const [, test] of GROUPS) ROWS.filter(test).forEach(r => shown.add(r.id));
  const missed = ROWS.filter(r => !shown.has(r.id));
  if(missed.length){
    html += '<tr class="grp"><td colspan="10">Ungrouped &mdash; unknown refresh kind ('
         + missed.length + ')</td></tr>';
    for(const r of missed){
      html += '<tr><td></td><td><span class="ttl">'+esc(r.title)+'</span></td>'
           + '<td><span class="fname">'+esc(r.output)+'</span></td>'
           + '<td colspan="8" class="sub2">acquire="'+esc(r.acquire)+'" matches no group filter</td></tr>';
    }
  }
  tb.innerHTML = html;
  tb.querySelectorAll('input[type=checkbox]').forEach(cb => {
    cb.onchange = () => { cb.checked ? sel.add(cb.dataset.id) : sel.delete(cb.dataset.id);
                          updateCount(); };
  });
  updateCount();
}

function updateCount(){
  document.getElementById('selCount').textContent =
    sel.size ? sel.size+' selected' : 'nothing selected';
}

function loadInventory(){
  fetch('/inventory').then(r=>r.json()).then(d => {
    ROWS = d.rows;
    const bad = ROWS.filter(r => r.status!=='ok').length;
    document.getElementById('hdr').textContent =
      ROWS.length+' datasets · '+bad+' need attention';
    render();
  });
}

function onlyNewer(){ return document.getElementById('onlyNewer').checked; }

function post(url, body){
  return fetch(url, {method:'POST', headers:{'Content-Type':'application/json'},
                     body: JSON.stringify(body||{})}).then(r=>r.json());
}

function ids(){ return Array.from(sel); }

document.getElementById('selAuto').onclick = () => {
  sel.clear(); ROWS.filter(r=>r.automatable).forEach(r=>sel.add(r.id)); render(); };
document.getElementById('selStale').onclick = () => {
  sel.clear(); ROWS.filter(r=>r.acquire!=='legacy' && r.status!=='ok')
                   .forEach(r=>sel.add(r.id)); render(); };
document.getElementById('selNone').onclick = () => { sel.clear(); render(); };

document.getElementById('bSel').onclick = () => {
  if(!sel.size){ alert('Tick at least one dataset first.'); return; }
  post('/run', {action:'refresh', ids:ids(), only_newer:onlyNewer()}); };
document.getElementById('bAuto').onclick = () =>
  post('/run', {action:'refresh', scope:'auto', only_newer:onlyNewer()});
document.getElementById('bStale').onclick = () =>
  post('/run', {action:'refresh', scope:'stale', only_newer:onlyNewer()});
document.getElementById('bCheck').onclick = () =>
  post('/run', {action:'check', ids: sel.size?ids():null, scope: sel.size?null:'all'});
document.getElementById('bResearch').onclick = () => {
  const picked = sel.size ? ids() : null;
  if(!picked && !confirm('Research the source URL for every dataset that has one? '
      +'This uses Brave and Claude credits.')) return;
  post('/run', {action:'research', ids:picked, scope: picked?null:'sourced'}); };
document.getElementById('bCancel').onclick = () => post('/cancel');

function renderProps(props){
  const card = document.getElementById('propCard');
  if(!props.length){ card.style.display='none'; return; }
  card.style.display='';
  document.getElementById('props').innerHTML = props.map(p => {
    const url = p.url || '';
    const ok  = p.reachable === true;
    return '<div class="prop"><b>'+esc(p.title||p.id)+'</b> '
      + '<span class="tag '+(ok?'auto':'manual')+'">'+esc(p.confidence||'?')
      + (p.http_status!=null ? ' · HTTP '+p.http_status : '')+'</span>'
      + '<div class="sub2" style="margin-top:6px">'+esc(p.why||'')+'</div>'
      + '<div style="margin-top:7px">now: <code>'+esc(p.current||'none')+'</code></div>'
      + '<div style="margin-top:3px">new: <code>'+esc(url||p.page||'nothing found')+'</code></div>'
      + '<div class="row2">'
      +   (url ? '<button data-id="'+esc(p.id)+'" data-url="'+esc(url)+'" class="useUrl"'
                 + (ok?'':' title="This URL did not serve bytes when tested"')
                 + '>Use this URL</button>' : '')
      +   (p.page ? '<a href="'+esc(p.page)+'" target="_blank" rel="noopener">open landing page</a>' : '')
      + '</div></div>';
  }).join('');
  document.querySelectorAll('.useUrl').forEach(b => {
    b.onclick = () => post('/approve-url', {id:b.dataset.id, url:b.dataset.url})
      .then(d => { alert(d.ok ? 'Saved. '+b.dataset.id+' will use the new URL.'
                              : 'Failed: '+d.error);
                   if(d.ok) loadInventory(); });
  });
}

let lastLen = 0;
function poll(){
  fetch('/status').then(r=>r.json()).then(s => {
    document.getElementById('jtitle').textContent = s.title || 'idle';
    document.getElementById('jprog').textContent  = s.progress.done+' / '+s.progress.total;
    document.getElementById('jcur').textContent   = s.progress.current || '-';
    document.getElementById('sok').textContent    = s.stats.ok;
    document.getElementById('sskip').textContent  = s.stats.skipped;
    document.getElementById('sfail').textContent  = s.stats.failed;
    document.getElementById('srb').textContent    = s.stats.rolled_back;
    document.getElementById('sbytes').textContent = s.bytes_h;
    const pct = s.progress.total ? 100*s.progress.done/s.progress.total : 0;
    document.getElementById('bar').style.width = pct+'%';
    document.getElementById('bCancel').disabled = !s.running;
    ['bSel','bAuto','bStale','bCheck','bResearch'].forEach(
      id => document.getElementById(id).disabled = s.running);

    const log = document.getElementById('log');
    const stick = log.scrollTop + log.clientHeight >= log.scrollHeight - 40;
    if(s.log.length !== lastLen){
      log.textContent = s.log.join('\n');
      lastLen = s.log.length;
      if(stick) log.scrollTop = log.scrollHeight;
    }
    renderProps(s.proposals || []);
    if(s.just_finished) loadInventory();
  }).catch(()=>{});
}

loadInventory();
setInterval(poll, 1200);
poll();
</script>
</body>
</html>
"""


@app.get("/")
def home():
    return PAGE


@app.get("/inventory")
def inventory():
    cfg = eng.load_config()
    eng.apply_overrides(cfg)
    return jsonify({"rows": eng.inventory(cfg)})


_WAS_RUNNING = {"v": False}


@app.get("/status")
def status():
    just = _WAS_RUNNING["v"] and not JOB.running
    _WAS_RUNNING["v"] = JOB.running
    with JOB.lock:
        log = list(JOB.log)
        props = list(JOB.proposals)
    return jsonify({
        "running": JOB.running, "title": JOB.title, "progress": JOB.progress,
        "stats": JOB.stats, "bytes_h": eng.human_size(JOB.stats.get("bytes", 0)),
        "log": log, "proposals": props, "results": JOB.results,
        "just_finished": just,
    })


def expand_scope(scope, cfg):
    rows = eng.inventory(cfg)
    if scope == "auto":
        return [r["id"] for r in rows if r["automatable"]]
    if scope == "stale":
        return [r["id"] for r in rows
                if r["automatable"] and r["status"] != "ok"]
    if scope == "sourced":
        return [d["id"] for d in reg.DATASETS
                if d.get("search_hint") and d["acquire"] != "legacy"]
    return [r["id"] for r in rows]


@app.post("/run")
def run():
    body = request.get_json(force=True, silent=True) or {}
    action = body.get("action", "refresh")
    cfg = eng.load_config()
    eng.apply_overrides(cfg)

    ids = body.get("ids") or expand_scope(body.get("scope") or "auto", cfg)
    ids = [i for i in ids if i in reg.BY_ID]
    if not ids:
        return jsonify({"ok": False, "error": "nothing to do"}), 400

    if action == "check":
        ok = start_job("Checking %d source(s)" % len(ids),
                       lambda: eng.check_sources(ids, cfg, JOB))
    elif action == "research":
        ok = start_job("Researching %d source(s)" % len(ids),
                       lambda: eng.run_research(ids, cfg, JOB))
    else:
        only_newer = bool(body.get("only_newer", True))
        ok = start_job("Refreshing %d dataset(s)%s"
                       % (len(ids), "" if only_newer else " (forced)"),
                       lambda: eng.run_refresh(ids, cfg, JOB, only_newer=only_newer))
    if not ok:
        return jsonify({"ok": False, "error": "a job is already running"}), 409
    return jsonify({"ok": True, "ids": ids})


@app.post("/cancel")
def cancel():
    JOB.cancel = True
    JOB.emit("[cancel requested - finishing the current step]")
    return jsonify({"ok": True})


@app.post("/approve-url")
def approve_url():
    body = request.get_json(force=True, silent=True) or {}
    ds_id, url = body.get("id"), (body.get("url") or "").strip()
    if ds_id not in reg.BY_ID:
        return jsonify({"ok": False, "error": "unknown dataset"}), 400
    if not url.startswith("http"):
        return jsonify({"ok": False, "error": "not a URL"}), 400
    eng.apply_url_override(ds_id, url, eng.load_config())
    JOB.emit("[config] %s now downloads from %s" % (ds_id, url))
    return jsonify({"ok": True})


if __name__ == "__main__":
    cfg = eng.load_config()
    eng.apply_overrides(cfg)
    port = int(cfg.get("port", 5003))
    os.makedirs(eng.resolve(cfg.get("staging_dir", "staging")), exist_ok=True)
    os.makedirs(eng.STATE_DIR, exist_ok=True)
    print("FastLocations Data Refresh -> http://localhost:%d  (Ctrl+C to stop)" % port)
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
