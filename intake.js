(function () {
  "use strict";

  // ===== Scoring API endpoint =====
  // '' (empty) = same origin: use this when the form is served BY the API
  //   (local: open http://127.0.0.1:8000 ; Render: open the onrender.com URL).
  // If you host this form on a DIFFERENT domain than the API (e.g. embed it on
  //   fastlocations.ai), set this to your full API URL, e.g.
  //   'https://fastlocations-scoring-api.onrender.com'
  const API_BASE = 'https://fastlocations-scoring-api-production.up.railway.app';

  const form = document.getElementById('intakeForm');
  const $ = (n) => form.querySelector('[name="' + n + '"]');
  const num = (n) => { const v = $(n).value.trim(); return v === '' ? null : Number(v); };
  const str = (n) => { const v = $(n).value.trim(); return v === '' ? null : v; };
  const range = (a, b) => ({ min: num(a), max: num(b) });
  const regions = (n) => $(n).value.split(',').map(s => s.trim().toUpperCase()).filter(Boolean);
  const checkedVals = (group) =>
    [...form.querySelectorAll('[data-group="' + group + '"] input:checked')].map(i => i.value);
  const mselSelected = (name) => [...document.querySelectorAll('.msel[data-name="' + name + '"] .msel-pop input:checked')].map(i => i.value);
  function closeAllMsel() { document.querySelectorAll('.msel-pop').forEach(function (p) { p.hidden = true; }); }
  document.addEventListener('click', function (e) { if (!e.target.closest('.msel')) closeAllMsel(); });
  function buildMsel(name, list) {
    const root = document.querySelector('.msel[data-name="' + name + '"]');
    const box = root.querySelector('.msel-box'); const pop = root.querySelector('.msel-pop');
    pop.innerHTML = list.map(function (s) { return '<label><input type="checkbox" value="' + s[0] + '">' + s[1] + '</label>'; }).join('');
    function summarize() {
      const sel = [...pop.querySelectorAll('input:checked')];
      if (!sel.length) { box.textContent = 'Select states...'; box.classList.add('placeholder'); }
      else if (sel.length <= 2) { box.textContent = sel.map(function (i) { return i.parentElement.textContent; }).join(', '); box.classList.remove('placeholder'); }
      else { box.textContent = sel.length + ' selected'; box.classList.remove('placeholder'); }
    }
    pop.addEventListener('change', summarize);
    box.onclick = function (e) { e.stopPropagation(); const willOpen = pop.hidden; closeAllMsel(); pop.hidden = !willOpen; };
    summarize();
  }

  // ---- State / Province dropdowns, filtered by the selected country ----
  const US_STATES = [["AL","Alabama"],["AK","Alaska"],["AZ","Arizona"],["AR","Arkansas"],["CA","California"],["CO","Colorado"],["CT","Connecticut"],["DE","Delaware"],["DC","District of Columbia"],["FL","Florida"],["GA","Georgia"],["HI","Hawaii"],["ID","Idaho"],["IL","Illinois"],["IN","Indiana"],["IA","Iowa"],["KS","Kansas"],["KY","Kentucky"],["LA","Louisiana"],["ME","Maine"],["MD","Maryland"],["MA","Massachusetts"],["MI","Michigan"],["MN","Minnesota"],["MS","Mississippi"],["MO","Missouri"],["MT","Montana"],["NE","Nebraska"],["NV","Nevada"],["NH","New Hampshire"],["NJ","New Jersey"],["NM","New Mexico"],["NY","New York"],["NC","North Carolina"],["ND","North Dakota"],["OH","Ohio"],["OK","Oklahoma"],["OR","Oregon"],["PA","Pennsylvania"],["RI","Rhode Island"],["SC","South Carolina"],["SD","South Dakota"],["TN","Tennessee"],["TX","Texas"],["UT","Utah"],["VT","Vermont"],["VA","Virginia"],["WA","Washington"],["WV","West Virginia"],["WI","Wisconsin"],["WY","Wyoming"]];
  const CA_PROVINCES = [["AB","Alberta"],["BC","British Columbia"],["MB","Manitoba"],["NB","New Brunswick"],["NL","Newfoundland and Labrador"],["NS","Nova Scotia"],["NT","Northwest Territories"],["NU","Nunavut"],["ON","Ontario"],["PE","Prince Edward Island"],["QC","Quebec"],["SK","Saskatchewan"],["YT","Yukon"]];
  function currentCountry() {
    const r = form.querySelector('[name="country"]:checked');
    return r ? r.value : 'US';
  }
  function populateRegions() {
    const list = currentCountry() === 'CA' ? CA_PROVINCES : US_STATES;
    ['req_regions','pref_regions','excl_regions'].forEach(function (n) { buildMsel(n, list); });
  }
  function updateUnits() {
    const u = currentCountry() === 'CA' ? 'km' : 'miles';
    document.querySelectorAll('.unit').forEach(function (el) { el.textContent = u; });
    const prox = $('prox_miles'); if (prox) prox.placeholder = u;
  }
  form.querySelectorAll('[name="country"]').forEach(function (r) { r.addEventListener('change', function () { populateRegions(); updateUnits(); }); });
  populateRegions(); updateUnits();

  // ---- Incentive priority ordering: tag chips with click order ----
  const incOrder = [];
  document.querySelectorAll('#incChips input').forEach(inp => {
    inp.addEventListener('change', () => {
      if (inp.checked) incOrder.push(inp.value);
      else { const i = incOrder.indexOf(inp.value); if (i > -1) incOrder.splice(i, 1); }
      renumberIncentives();
    });
  });
  function renumberIncentives() {
    document.querySelectorAll('#incChips input').forEach(inp => {
      const ord = inp.parentElement.querySelector('.ord');
      const pos = incOrder.indexOf(inp.value);
      ord.textContent = pos > -1 ? (pos + 1) : '';
    });
  }

  // ---- Weights: live normalize to 100% ----
  const sliders = [...document.querySelectorAll('#weights input[type=range]')];
  function refreshWeights() {
    const raw = sliders.map(s => Number(s.value));
    const sum = raw.reduce((a, b) => a + b, 0) || 1;
    sliders.forEach((s, i) => {
      const pct = Math.round((raw[i] / sum) * 100);
      s.closest('.weight-row').querySelector('.wval').textContent = pct + '%';
    });
    document.getElementById('wtotal').textContent = '100%';
  }
  sliders.forEach(s => s.addEventListener('input', refreshWeights));
  refreshWeights();

  // ---- Use-type presets: picking a use type re-sets the weight sliders to a sensible default ----
  const USE_PRESETS = {
    manufacturing:          { workforce:22, cost:20, real_estate:12, incentives:12, infrastructure:8,  logistics:8,  market_size:5,  livability:4, safety:4, demographics:5 },
    warehouse_distribution: { workforce:14, cost:16, real_estate:14, incentives:8,  infrastructure:6,  logistics:24, market_size:8,  livability:3, safety:4, demographics:3 },
    data_center:            { workforce:8,  cost:16, real_estate:10, incentives:12, infrastructure:26, logistics:6,  market_size:8,  livability:3, safety:4, demographics:7 },
    office:                 { workforce:20, cost:14, real_estate:12, incentives:6,  infrastructure:6,  logistics:6,  market_size:12, livability:9, safety:7, demographics:8 },
    r_and_d:                { workforce:24, cost:12, real_estate:10, incentives:10, infrastructure:8,  logistics:5,  market_size:8,  livability:8, safety:5, demographics:10 },
    flex:                   { workforce:18, cost:18, real_estate:15, incentives:10, infrastructure:8,  logistics:10, market_size:6,  livability:5, safety:5, demographics:5 },
    mixed:                  { workforce:18, cost:20, real_estate:15, incentives:10, infrastructure:8,  logistics:8,  market_size:6,  livability:5, safety:5, demographics:5 }
  };
  const useSel = $('use_primary');
  if (useSel) useSel.addEventListener('change', function () {
    const p = USE_PRESETS[useSel.value]; if (!p) return;
    sliders.forEach(function (s) { if (p[s.dataset.w] != null) s.value = p[s.dataset.w]; });
    refreshWeights();
  });

  function normalizedWeights() {
    const raw = sliders.map(s => Number(s.value));
    const sum = raw.reduce((a, b) => a + b, 0) || 1;
    const out = {};
    sliders.forEach((s, i) => { out[s.dataset.w] = +(raw[i] / sum).toFixed(3); });
    return out;
  }

  // ---- Build the schema-compliant object ----
  function buildCriteria() {
    const prox_to = str('prox_to'), prox_miles = num('prox_miles');
    return {
      schema_version: "1.0",
      project: {
        project_id: "auto",
        project_name: str('project_name'),
        submitted_by: {
          name: str('submit_name'), firm: str('submit_firm'),
          email: str('submit_email'), phone: str('submit_phone') || ""
        },
        confidentiality: ($('submit_confidentiality') ? $('submit_confidentiality').value : 'blind'),
        timeline: { decision_by: str('decision_by'), operational_by: str('operational_by') },
        notes: str('notes') || ""
      },
      use_type: { primary: $('use_primary').value, naics: str('naics'), description: "" },
      facility: {
        type: $('facility_type').value,
        building_sqft: range('bldg_min', 'bldg_max'),
        site_acres: range('acre_min', 'acre_max'),
        ceiling_clear_height_ft: num('ceiling_ft'),
        expandability_required: $('expandability').checked
      },
      workforce: {
        headcount: { initial: num('hc_initial'), year_5: num('hc_year5') },
        skill_profile: checkedVals('skills'),
        shift_pattern: $('shift').value,
        right_to_work: str('right_to_work'),
        target_wage: {
          value: num('wage_value'), basis: $('wage_basis').value, relation: "at_or_below_market"
        }
      },
      infrastructure: {
        power_mw: num('power_mw'), power_reliability: $('power_reliability').value,
        natural_gas_required: $('gas').checked,
        water_gpd: num('water_gpd'), sewer_gpd: num('sewer_gpd'),
        rail: $('rail').value, broadband_min_gbps: num('broadband_gbps'),
        highway_access_max_miles: num('hwy_miles'),
        commercial_airport_max_miles: num('air_miles'),
        port_required: $('port').checked,
        renewable: str('renewable'),
        drought: str('drought')
      },
      demographics: {
        labor_draw_radius_miles: num('draw_radius'),
        min_population: num('min_pop'), min_labor_force: num('min_lf'),
        education_priority: $('education').value
      },
      incentives: {
        priorities: incOrder.slice(),
        min_value_target_usd: num('inc_target')
      },
      geography: {
        countries: checkedVals('countries'),
        required_regions: mselSelected('req_regions'),
        preferred_regions: mselSelected('pref_regions'),
        excluded_regions: mselSelected('excl_regions'),
        market_proximity: (prox_to && prox_miles != null) ? [{ to: prox_to, max_miles: (currentCountry() === 'CA' ? prox_miles / 1.609 : prox_miles) }] : []
      },
      budget: { capex_usd: num('capex'), annual_opex_target_usd: num('opex') },
      weights: normalizedWeights()
    };
  }

  // ---- Light validation (non-blocking guidance) ----
  function validate(c) {
    const errs = [];
    if (!c.project.project_name) errs.push("Add a project name.");
    const conflict = c.geography.required_regions.filter(r => c.geography.excluded_regions.includes(r));
    if (conflict.length) errs.push("A region is in both Required and Excluded: " + conflict.join(", "));
    return errs;
  }

  function showJSON(c) {
    document.getElementById('jsonOut').textContent = JSON.stringify(c, null, 2);
    document.getElementById('preview').classList.add('show');
  }

  // ---- Backend hand-off (local scoring API) ----
  async function submitToBackend(criteria) {
    const res = await fetch(API_BASE + '/match?top=5', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(criteria)
    });
    if (!res.ok) throw new Error('Scoring failed (HTTP ' + res.status + ')');
    return res.json();
  }

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const c = buildCriteria();
    const errs = validate(c);
    const box = document.getElementById('errbox');
    if (errs.length) {
      box.innerHTML = "Before generating matches:<ul>" + errs.map(x => "<li>" + x + "</li>").join('') + "</ul>";
      box.classList.add('show');
      box.scrollIntoView({ behavior: 'smooth', block: 'center' });
      return;
    }
    box.classList.remove('show');
    const results = document.getElementById('results');
    results.innerHTML = '<p class="cap">Scoring locations...</p>';
    results.classList.add('show');
    results.scrollIntoView({ behavior: 'smooth', block: 'start' });
    try {
      const data = await submitToBackend(c);
      renderResults(data);
      const panel = document.getElementById('flSubmitPanel');
      if (panel) {
        panel.style.display = '';
        const sp = $('submit_project'); if (sp) sp.value = c.project.project_name || '';
      }
    } catch (err) {
      results.innerHTML = '<h3>Could not score</h3><p class="cap" style="color:#9a2017">' +
        err.message + '. Please try again in a moment.</p>';
    }
  });

  // ---- Submit to FastLocations (optional lead capture) ----
  const flBtn = document.getElementById('flSubmitBtn');
  if (flBtn) flBtn.addEventListener('click', async function () {
    const c = buildCriteria();
    const msg = document.getElementById('flSubmitMsg');
    if (!c.project.submitted_by.email) { msg.style.color = '#9a2017'; msg.textContent = 'Please add your email so we can follow up.'; return; }
    msg.style.color = ''; msg.textContent = 'Submitting...';
    try {
      const res = await fetch(API_BASE + '/submit', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(c)
      });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      msg.style.color = '#1a7a3a';
      msg.textContent = 'Thank you - your project has been submitted to FastLocations. We will be in touch.';
      flBtn.disabled = true;
    } catch (err) {
      msg.style.color = '#9a2017'; msg.textContent = 'Could not submit: ' + err.message;
    }
  });

  // ---- Market proximity autocomplete ----
  const proxInput = form.querySelector('[name="prox_to"]');
  const placeList = document.getElementById('placeList');
  if (proxInput && placeList) {
    let proxTimer;
    proxInput.addEventListener('input', function () {
      const q = proxInput.value.trim();
      clearTimeout(proxTimer);
      if (q.length < 2) { placeList.innerHTML = ''; return; }
      proxTimer = setTimeout(async function () {
        try {
          const r = await fetch(API_BASE + '/places?q=' + encodeURIComponent(q));
          const list = await r.json();
          placeList.innerHTML = list.map(function (x) { return '<option value="' + x.replace(/"/g, '&quot;') + '"></option>'; }).join('');
        } catch (_) {}
      }, 220);
    });
  }

  function renderResults(data) {
    const wrap = document.getElementById('results');
    if (!data || !data.results || !data.results.length) {
      wrap.innerHTML = '<h3>No matches</h3><p class="cap">No counties passed the filters. Loosen the required regions or lower the population / labor-force thresholds.</p>';
      return;
    }
    const allDims = ['workforce','cost','real_estate','incentives','infrastructure','logistics','market_size','safety','demographics','livability'];
    // Only show dimensions that have data for at least one result. This hides dimensions with no
    // coverage for the selected region (e.g. infrastructure, safety, livability for Canada) instead
    // of implying data that isn't there.
    const dimOrder = allDims.filter(function (d) { return data.results.some(function (r) { return r.sub_scores[d] != null; }); });
    let html = '<h3>Your Top ' + data.results.length + ' Matches</h3>' +
      '<p class="cap">Ranked by <b>FastLocations Score</b>. ' + data.trace.candidates_after_filters + ' of ' + data.trace.candidates_start +
      ' candidates passed the filters. Scored on the factors with data for the selected region.</p>' +
      '<div id="flMap" class="flmap"></div>';
    data.results.forEach((r, i) => {
      const edo = r.serving_edos && r.serving_edos[0];
      const chips = dimOrder.map(function (d) {
        const v = r.sub_scores[d];
        return '<span class="sub' + (v == null ? ' na' : '') + '">' + d.replace(/_/g, ' ') +
               ' ' + (v == null ? '-' : v) + '</span>';
      }).join('');
      // Best serving EDO per tier (list is pre-sorted smallest-territory first, so the first match
      // in each tier is the most specific / best for that tier).
      const edos = r.serving_edos || [];
      const LOCAL = ['Local Development Agency', 'Chamber of Commerce', 'Port/Airport Authority', 'Megasite', 'Industrial Park'];
      const REGIONAL = ['Regional Development Agency'];
      const STATEU = ['State Agency', 'Utility'];
      const pick = function (cats) { for (var j = 0; j < edos.length; j++) { if (cats.indexOf(edos[j].category) >= 0) return edos[j]; } return null; };
      const edoLine = function (label, e) {
        if (!e) return '';
        var name = e.embed_url
          ? '<a href="' + e.embed_url + '" target="_blank" rel="noopener"><b>' + e.organization + '</b></a>'
          : '<b>' + e.organization + '</b>';
        var dash = e.objectid
          ? ' &middot; <a href="https://www.fastlocations.ai/dash/dashboard.html?id=' + encodeURIComponent(e.objectid) + '" target="_blank" rel="noopener">AI+Plus Dashboard &#8599;</a>'
          : '';
        return '<div class="edoline"><span class="edolabel">' + label + ':</span> ' + name + ' <span class="cat">(' + e.category + ')</span>' + dash + '</div>';
      };
      const local = pick(LOCAL), regional = pick(REGIONAL), stateu = pick(STATEU);
      let edoHtml;
      if (local || regional || stateu) {
        edoHtml = edoLine('Best Local EDO match', local) + edoLine('Best Regional EDO match', regional) + edoLine('Best State/Utility EDO match', stateu);
      } else if (edos[0]) {
        edoHtml = edoLine('Best EDO match', edos[0]);
      } else {
        edoHtml = '<span class="cat">No EDO customer currently serves this county</span>';
      }
      html += '<div class="result">' +
        '<div class="rhead"><span class="rank">' + (i + 1) + '</span>' +
        '<span class="place">' + r.county + ', ' + r.state + (r.msa ? ' <span class="msa">(' + r.msa + ' MSA)</span>' : '') + '</span>' +
        '<span class="score"><span class="flscore-cap">FastLocations Score</span><span class="flscore-val">' + r.final_score + '</span></span></div>' +
        '<div class="subs">' + chips + '</div>' +
        (r.rationale ? '<p class="rationale">' + r.rationale + '</p>' : '') +
        '<div class="edo">' + edoHtml + '</div></div>';
    });
    // Other notable matches: top-scoring counties NOT tied to an AI+Plus / EDO account.
    var other = data.other_notable || [];
    if (other.length) {
      html += '<div class="othersec"><h3>Other Notable Matches</h3>' +
        '<p class="cap">High-scoring locations not currently tied to an AI+Plus account.</p>';
      other.forEach(function (o) {
        html += '<div class="otherrow"><span class="place">' + o.county + ', ' + o.state + (o.msa ? ' <span class="msa">(' + o.msa + ' MSA)</span>' : '') + '</span>' +
          '<span class="score"><span class="flscore-cap">FastLocations Score</span>' +
          '<span class="flscore-val">' + o.final_score + '</span></span></div>';
      });
      html += '</div>';
    }
    wrap.innerHTML = html;
    initResultsMap(data.results);
  }

  function initResultsMap(results) {
    const el = document.getElementById('flMap');
    if (!el) return;
    const plottable = (results || []).filter(function (r) { return r.lat != null && r.lon != null; });
    if (typeof L === 'undefined' || !plottable.length) {
      // Nothing to plot (Leaflet missing, or Canadian Census Divisions which have no centroids).
      el.style.display = 'none';
      if (results && results.length) {
        const note = document.createElement('p');
        note.className = 'cap';
        note.style.margin = '0 0 14px';
        note.textContent = 'Map view is available for U.S. county results only; Canadian Census Divisions are not yet geocoded.';
        el.parentNode.insertBefore(note, el);
      }
      return;
    }
    el.style.display = '';
    if (el._map) { try { el._map.remove(); } catch (e) {} }
    const map = L.map(el, { scrollWheelZoom: false });
    el._map = map;
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
      { maxZoom: 18, attribution: '&copy; OpenStreetMap contributors' }).addTo(map);
    const markers = [];
    results.forEach(function (r, i) {
      if (r.lat == null || r.lon == null) return;
      const edo = r.serving_edos && r.serving_edos[0];
      const dash = (edo && edo.objectid)
        ? 'https://www.fastlocations.ai/dash/dashboard.html?id=' + encodeURIComponent(edo.objectid) : null;
      let pop = '<div style="font-size:13px;line-height:1.5">' +
        '<b>' + (i + 1) + '. ' + r.county + ', ' + r.state + '</b><br>' +
        'FastLocations Score: <b style="color:#cc2020">' + r.final_score + '</b>';
      if (edo) pop += '<br>' + edo.organization;
      if (dash) pop += '<br><a href="' + dash + '" target="_blank" rel="noopener">AI+Plus Dashboard &#8599;</a>';
      pop += '</div>';
      const icon = L.divIcon({ className: 'flpin', html: '<span>' + (i + 1) + '</span>', iconSize: [28, 28], iconAnchor: [14, 14] });
      markers.push(L.marker([r.lat, r.lon], { icon: icon }).addTo(map).bindPopup(pop));
    });
    if (markers.length) {
      const grp = L.featureGroup(markers);
      map.fitBounds(grp.getBounds().pad(0.35));
      if (markers.length === 1) map.setZoom(8);
    } else {
      map.setView([39.5, -98.35], 4);
    }
    setTimeout(function () { map.invalidateSize(); }, 250);
  }

  // ---- Print / Save as PDF ----
  document.getElementById('printBtn').addEventListener('click', function () { window.print(); });

  // ---- Export the completed form (+ matches) to Word ----
  document.getElementById('wordBtn').addEventListener('click', function () {
    const c = buildCriteria();
    const esc = function (v) { return (v == null ? '' : String(v)).replace(/&/g, '&amp;').replace(/</g, '&lt;'); };
    const row = function (k, v) {
      if (v == null || v === '' || (Array.isArray(v) && !v.length)) return '';
      return '<p><b>' + k + ':</b> ' + esc(Array.isArray(v) ? v.join(', ') : v) + '</p>';
    };
    const rng = function (o) { return [o.min, o.max].filter(function (x) { return x != null; }).join(' to '); };
    let b = '<h1>FastLocations &mdash; Site Selection Intake</h1>';
    b += '<h2>Project</h2>' + row('Project', c.project.project_name) + row('Submitted by', c.project.submitted_by.name) + row('Firm', c.project.submitted_by.firm) + row('Email', c.project.submitted_by.email) + row('Use type', c.use_type.primary) + row('NAICS', c.use_type.naics);
    b += '<h2>Real Estate</h2>' + row('Facility type', c.facility.type) + row('Building sqft', rng(c.facility.building_sqft)) + row('Site acres', rng(c.facility.site_acres));
    b += '<h2>Workforce</h2>' + row('Headcount at start', c.workforce.headcount.initial) + row('Headcount yr 5', c.workforce.headcount.year_5) + row('Skill profile', c.workforce.skill_profile);
    b += '<h2>Geography</h2>' + row('Country', c.geography.countries) + row('Required states', c.geography.required_regions) + row('Preferred states', c.geography.preferred_regions) + row('Excluded states', c.geography.excluded_regions);
    b += '<h2>Incentives</h2>' + row('Priorities', c.incentives.priorities);
    b += '<h2>Weights</h2>' + Object.keys(c.weights).map(function (k) { return row(k, Math.round(c.weights[k] * 100) + '%'); }).join('');
    const res = document.getElementById('results');
    if (res && res.classList.contains('show') && res.innerHTML.trim()) b += '<h2>Matches</h2>' + res.innerHTML;
    const doc = '<html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:w="urn:schemas-microsoft-com:office:word"><head><meta charset="utf-8"></head><body>' + b + '</body></html>';
    const blob = new Blob(['\uFEFF' + doc], { type: 'application/msword' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = ((c.project.project_name || 'site_selection').replace(/[^a-z0-9]+/gi, '_').toLowerCase()) + '.doc';
    a.click();
    URL.revokeObjectURL(a.href);
  });
})();
