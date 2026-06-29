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
        port_required: $('port').checked
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
        market_proximity: (prox_to && prox_miles != null) ? [{ to: prox_to, max_miles: prox_miles }] : []
      },
      budget: { capex_usd: num('capex'), annual_opex_target_usd: num('opex') },
      weights: normalizedWeights()
    };
  }

  // ---- Light validation (non-blocking guidance) ----
  function validate(c) {
    const errs = [];
    if (!c.project.project_name) errs.push("Add a project name.");
    if (c.workforce.headcount.initial == null) errs.push("Add a starting headcount - labor matching needs it.");
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
    const dimOrder = ['workforce','demographics','infrastructure','logistics','incentives','real_estate'];
    const live = (data.dimensions_live || []).join(', ');
    const pending = (data.dimensions_pending_data || []).join(', ') || 'none';
    let html = '<h3>Top ' + data.results.length + ' matches</h3>' +
      '<p class="cap">' + data.trace.candidates_after_filters + ' of ' + data.trace.candidates_start +
      ' counties passed the filters. Scored on: ' + live + '. Pending data shown as "-": ' + pending + '.</p>';
    data.results.forEach((r, i) => {
      const edo = r.serving_edos && r.serving_edos[0];
      const chips = dimOrder.map(function (d) {
        const v = r.sub_scores[d];
        return '<span class="sub' + (v == null ? ' na' : '') + '">' + d.replace(/_/g, ' ') +
               ' ' + (v == null ? '-' : v) + '</span>';
      }).join('');
      let edoHtml;
      if (edo) {
        const name = edo.embed_url
          ? '<a href="' + edo.embed_url + '" target="_blank" rel="noopener"><b>' + edo.organization + '</b></a>'
          : '<b>' + edo.organization + '</b>';
        const dash = edo.objectid
          ? ' &middot; <a href="https://www.fastlocations.ai/dash/dashboard.html?id=' + encodeURIComponent(edo.objectid) + '" target="_blank" rel="noopener">AI+Plus Dashboard &#8599;</a>'
          : '';
        edoHtml = 'Route lead to: ' + name + ' <span class="cat">(' + edo.category + ')</span>' + dash;
      } else {
        edoHtml = '<span class="cat">No EDO customer currently serves this county</span>';
      }
      html += '<div class="result">' +
        '<div class="rhead"><span class="rank">' + (i + 1) + '</span>' +
        '<span class="place">' + r.county + ', ' + r.state + '</span>' +
        '<span class="score">' + r.final_score + '</span></div>' +
        '<div class="subs">' + chips + '</div>' +
        (r.rationale ? '<p class="rationale">' + r.rationale + '</p>' : '') +
        '<div class="edo">' + edoHtml + '</div></div>';
    });
    wrap.innerHTML = html;
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
