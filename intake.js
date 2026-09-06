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
  let lastResults = null;   // the most recent /match response, for the plan and for saved scenarios
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
  function weightSum() { return sliders.map(s => Number(s.value)).reduce((a, b) => a + b, 0); }
  function refreshWeights() {
    const raw = sliders.map(s => Number(s.value));
    const total = raw.reduce((a, b) => a + b, 0);
    const sum = total || 1;
    sliders.forEach((s, i) => {
      const pct = total ? Math.round((raw[i] / sum) * 100) : 0;
      s.closest('.weight-row').querySelector('.wval').textContent = pct + '%';
    });
    // Was hardcoded to '100%' -- it read "Normalized total: 100%" even with every slider at zero.
    const el = document.getElementById('wtotal');
    el.textContent = total ? '100%' : '0% — set at least one factor above zero';
    el.style.color = total ? '' : 'var(--fl-red)';
  }
  sliders.forEach(s => s.addEventListener('input', refreshWeights));
  refreshWeights();

  // ---- Use-type presets: picking a use type re-sets the weight sliders to a sensible default ----
  const USE_PRESETS = {
    // Innovation is its own dimension as of this version. Warehouse and flex barely move on it;
    // R&D leans on it hardest, second only to workforce -- an R&D site still has to be staffable.
    // DAI (the county DAI score) is a FIXED 7 in every preset by design: it is meant to carry ~7% of
    // the final score under all scenarios, so it does not flex with use type. Each preset still sums
    // to 100; the 7 points came off workforce (where the score used to live as a hidden metric) and
    // the preset's other large factors. test_scorer.py checks both invariants.
    manufacturing:          { workforce:19, cost:17, real_estate:10, incentives:11, infrastructure:8,  logistics:8,  market_size:5,  livability:4, safety:4, demographics:4,  innovation:3,  dai:7 },
    warehouse_distribution: { workforce:12, cost:14, real_estate:12, incentives:8,  infrastructure:6,  logistics:23, market_size:8,  livability:3, safety:4, demographics:2,  innovation:1,  dai:7 },
    data_center:            { workforce:7,  cost:14, real_estate:10, incentives:11, infrastructure:23, logistics:6,  market_size:8,  livability:3, safety:4, demographics:3,  innovation:4,  dai:7 },
    office:                 { workforce:17, cost:12, real_estate:11, incentives:6,  infrastructure:6,  logistics:6,  market_size:11, livability:9, safety:7, demographics:4,  innovation:4,  dai:7 },
    // R&D runs the lowest cost weight of any use type: these projects compete for scarce talent and
    // proximity to research, and rarely site on operating cost. What they do care about is land to
    // build a campus on, so the points come off cost and go to innovation and real estate.
    r_and_d:                { workforce:17, cost:5,  real_estate:9,  incentives:8,  infrastructure:7,  logistics:3,  market_size:6,  livability:7, safety:4, demographics:5,  innovation:22, dai:7 },
    flex:                   { workforce:16, cost:16, real_estate:13, incentives:10, infrastructure:8,  logistics:9,  market_size:6,  livability:5, safety:5, demographics:3,  innovation:2,  dai:7 },
    mixed:                  { workforce:16, cost:17, real_estate:13, incentives:9,  infrastructure:8,  logistics:8,  market_size:6,  livability:5, safety:5, demographics:3,  innovation:3,  dai:7 }
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
        drought: str('drought'),
        hazard: str('hazard')
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
    if (weightSum() <= 0) errs.push("Set at least one factor weight above zero before generating matches.");
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
      lastResults = data;
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

  function escHtml(v) {
    return (v == null ? '' : String(v)).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function renderResults(data) {
    const wrap = document.getElementById('results');
    if (!data || !data.results || !data.results.length) {
      wrap.innerHTML = '<h3>No matches</h3><p class="cap">No counties passed the filters. Loosen the required regions or lower the population / labor-force thresholds.</p>';
      return;
    }
    // Every dimension the scorer returns in sub_scores (scorer.DIMS). innovation was missing from this
    // list, so its chip never rendered even though it was scored; dai is the county DAI score.
    const allDims = ['workforce','cost','real_estate','incentives','infrastructure','logistics','market_size','safety','demographics','livability','innovation','dai'];
    // Chip label: the dimension key with underscores as spaces, except the acronym.
    const dimLabel = function (d) { return d === 'dai' ? 'DAI' : d.replace(/_/g, ' '); };
    // Only show dimensions that have data for at least one result, so we never imply data that isn't
    // there. (The old note here claimed infrastructure/safety/livability were missing for Canada --
    // that is stale: Canadian results return every factor except dai, which is US-only.)
    const dimOrder = allDims.filter(function (d) { return data.results.some(function (r) { return r.sub_scores[d] != null; }); });
    let html = '<h3>Your Top ' + data.results.length + ' Matches</h3>' +
      '<p class="cap">Ranked by <b>FastLocations Score</b>. ' + data.trace.candidates_after_filters + ' of ' + data.trace.candidates_start +
      ' candidates passed the filters. Scored on the factors with data for the selected region.</p>' +
      // Warn when a proximity place couldn't be geocoded -- otherwise the user sees an unfiltered
      // result set and believes the distance constraint was applied.
      ((data.trace.market_proximity_unresolved || []).length
        ? '<div class="warnbox">&#9888; ' +
          data.trace.market_proximity_unresolved.map(function (n) { return '"' + escHtml(n) + '"'; }).join(', ') +
          ' could not be located, so the proximity limit was <b>not applied</b>. Try "City, ST" (e.g. Columbus, OH) or "City, PROV" (e.g. Toronto, ON).</div>'
        : '') +
      '<div class="recalibrate">Not seeing the right fit? Change any input above - your factor weightings, filters, or priorities - then choose <b>Generate matches</b> again to refine these results.</div>' +
      '<div id="flMap" class="flmap"></div>';
    data.results.forEach((r, i) => {
      const edo = r.serving_edos && r.serving_edos[0];
      // Rank this location's factors by strength (strongest first) and show a relative rank number
      // instead of the absolute percentile. Factors with no data go last, unranked.
      const scored = dimOrder.filter(function (d) { return r.sub_scores[d] != null; })
                             .sort(function (a, b) { return r.sub_scores[b] - r.sub_scores[a]; });
      const unscored = dimOrder.filter(function (d) { return r.sub_scores[d] == null; });
      const chips = scored.map(function (d, k) {
        return '<span class="sub" title="Relative strength on this location (1 = strongest factor)">' +
               dimLabel(d) + ' <b class="rank">#' + (k + 1) + '</b></span>';
      }).concat(unscored.map(function (d) {
        return '<span class="sub na">' + dimLabel(d) + ' –</span>';
      })).join('');
      // Best serving EDO per tier (list is pre-sorted smallest-territory first, so the first match
      // in each tier is the most specific / best for that tier).
      const edos = r.serving_edos || [];
      const LOCAL = ['Local Development Agency', 'Chamber of Commerce', 'Port/Airport Authority', 'Megasite', 'Industrial Park'];
      const REGIONAL = ['Regional Development Agency'];
      const UTILITY = ['Utility'];
      const STATE = ['State Agency'];
      const pick = function (cats) { for (var j = 0; j < edos.length; j++) { if (cats.indexOf(edos[j].category) >= 0) return edos[j]; } return null; };
      const propEdos = r.property_edos || [];   // org names whose territory has listings on the dashboard
      const edoLine = function (label, e) {
        if (!e) return '';
        var name = e.embed_url
          ? '<a href="' + e.embed_url + '" target="_blank" rel="noopener"><b>' + e.organization + '</b></a>'
          : '<b>' + e.organization + '</b>';
        var dash = e.objectid
          ? ' &middot; <a href="https://www.fastlocations.ai/dash/dashboard.html?id=' + encodeURIComponent(e.objectid) + '" target="_blank" rel="noopener">AI+Plus Dashboard &#8599;</a>'
          : '';
        var propTag = propEdos.indexOf(e.organization) >= 0
          ? ' <span class="proptag">(&#9733; Properties Available)</span>'
          : '';
        return '<div class="edoline"><span class="edolabel">' + label + ':</span> ' + name + ' <span class="cat">(' + e.category + ')</span>' + dash + propTag + '</div>';
      };
      const local = pick(LOCAL), regional = pick(REGIONAL), utility = pick(UTILITY), state = pick(STATE);
      let edoHtml;
      if (local || regional || utility || state) {
        edoHtml = edoLine('Best Local EDO match', local) + edoLine('Best Regional EDO match', regional) +
                  edoLine('Best Utility EDO match', utility) + edoLine('Best State EDO match', state);
      } else if (edos[0]) {
        edoHtml = edoLine('Best EDO match', edos[0]);
      } else {
        edoHtml = '<span class="cat">No EDO customer currently serves this county</span>';
      }
      // Score transparency: the headline number is the weighted factor average PLUS damping, bonuses
      // and penalties. Show those terms so the score reconciles from what's on screen.
      var bd = r.score_breakdown || null;
      var bdHtml = '';
      if (bd) {
        var parts = [];
        parts.push('<span class="bdterm">Weighted factors <b>' + (bd.weighted_total != null ? bd.weighted_total.toFixed(1) : '—') + '</b></span>');
        if (Math.abs(bd.reliability_damping || 0) >= 0.05)
          parts.push('<span class="bdterm" title="Small, isolated labour markets are pulled toward the national average">Market-depth adj. <b>' + (bd.reliability_damping > 0 ? '+' : '') + bd.reliability_damping.toFixed(1) + '</b></span>');
        var bo = bd.bonuses || {};
        if (bo.preferred_region) parts.push('<span class="bdterm pos">Preferred region <b>+' + bo.preferred_region + '</b></span>');
        if (bo.edo_coverage) parts.push('<span class="bdterm pos">Local EDO coverage <b>+' + bo.edo_coverage + '</b></span>');
        if (bo.property_access) parts.push('<span class="bdterm pos">Property availability <b>+' + bo.property_access + '</b></span>');
        var pe = bd.penalties || {};
        if (pe.hazard) parts.push('<span class="bdterm neg">Hazard exposure <b>&minus;' + pe.hazard.toFixed(1) + '</b></span>');
        if (pe.water) parts.push('<span class="bdterm neg">Water risk <b>&minus;' + pe.water.toFixed(1) + '</b></span>');
        if (bd.factors_scored < bd.factors_total)
          parts.push('<span class="bdterm warn" title="Factors with no data for this location are omitted rather than penalised">Scored on ' + bd.factors_scored + ' of ' + bd.factors_total + ' factors</span>');
        bdHtml = '<div class="breakdown">' + parts.join('') + '</div>';
      }
      html += '<div class="result">' +
        '<div class="rhead"><span class="rank">' + (i + 1) + '</span>' +
        '<span class="place">' + r.county + ', ' + r.state + (r.msa ? ' <span class="msa">(' + r.msa + (r.country === 'Canada' ? ' CMA' : ' MSA') + ')</span>' : '') + '</span>' +
        '<span class="score"><span class="flscore-cap">FastLocations Score</span><span class="flscore-val">' + r.final_score + '</span></span></div>' +
        '<div class="subs">' + chips + '</div>' + bdHtml +
        (r.rationale ? '<p class="rationale">' + r.rationale + '</p>' : '') +
        '<div class="edo">' + edoHtml + '</div></div>';
    });
    // Other notable matches: top-scoring counties NOT tied to an AI+Plus / EDO account.
    var other = data.other_notable || [];
    if (other.length) {
      html += '<div class="othersec"><h3>Other Notable Matches</h3>' +
        '<p class="cap">High-scoring locations not currently tied to an AI+Plus account.</p>';
      other.forEach(function (o) {
        html += '<div class="otherrow"><span class="place">' + o.county + ', ' + o.state + (o.msa ? ' <span class="msa">(' + o.msa + (o.country === 'Canada' ? ' CMA' : ' MSA') + ')</span>' : '') + '</span>' +
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
        'FastLocations Score: <b style="color:#d32323">' + r.final_score + '</b>';
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

  // ---- Strategic plan (Word) ----
  // One generator for both tools, in /js/strategicPlan.js. Here it gets the
  // criteria as edited on the form, the last matches if any were generated,
  // and the Stay or Grow seed if the reader arrived from that report (or
  // opened a scenario file that carried one).
  const planMsg = document.getElementById('planMsg');
  const planMsgDefault = planMsg ? planMsg.textContent : '';
  function say(t, bad) {
    if (!planMsg) return;
    planMsg.textContent = t || planMsgDefault;
    planMsg.style.color = bad ? '#9a2017' : '';
  }
  const planBtn = document.getElementById('wordBtn');
  if (planBtn) planBtn.addEventListener('click', async function () {
    if (!window.FLStrategicPlan) { say('The plan builder did not load. Refresh the page and try again.', true); return; }
    planBtn.disabled = true; say('Building the Word document\u2026');
    try {
      const name = await FLStrategicPlan.download({ seed: window.__flProjectSeed || null, criteria: buildCriteria(), results: lastResults });
      say('Saved as ' + name + ' \u2014 look in your downloads folder. It is a draft: text in [brackets] is for you to complete.' +
        (lastResults ? '' : ' The candidate locations in it were scored from the form as it stands; generate matches here to refine them.'));
    } catch (err) {
      say('Could not build the document: ' + ((err && err.message) || 'unknown error'), true);
    }
    planBtn.disabled = false;
  });

  // ---- Apply a ProjectCriteria object to the form ----
  // The inverse of buildCriteria(). Used by scenario files, the unsaved-draft
  // restore, and the Stay or Grow prefill (which passes partial:true so the
  // untouched fields keep their defaults and the use-type preset still runs).
  function setText(name, v) {
    const el = $(name);
    if (!el || v == null || v === '') return false;
    el.value = String(v);
    return true;
  }
  function setSelect(name, v) {
    const el = $(name);
    if (!el || v == null) return false;
    const ok = [...el.options].some(function (o) { return o.value === v; });
    if (!ok) return false;
    el.value = v;
    return true;
  }
  function setCheck(name, v) {
    const el = $(name);
    if (!el || typeof v !== 'boolean') return false;
    el.checked = v;
    return true;
  }
  function setRadio(name, v) {
    const r = form.querySelector('[name="' + name + '"][value="' + v + '"]');
    if (!r) return false;
    const changed = !r.checked;
    r.checked = true;
    return changed;
  }
  function setGroup(group, values) {
    if (!Array.isArray(values)) return false;
    let hit = false;
    form.querySelectorAll('[data-group="' + group + '"] input').forEach(function (i) {
      i.checked = values.indexOf(i.value) >= 0;
      if (i.checked) hit = true;
    });
    return hit;
  }
  function setMsel(name, values) {
    const pop = document.querySelector('.msel[data-name="' + name + '"] .msel-pop');
    if (!pop) return;
    const want = Array.isArray(values) ? values : [];
    pop.querySelectorAll('input').forEach(function (i) { i.checked = want.indexOf(i.value) >= 0; });
    pop.dispatchEvent(new Event('change'));
  }
  function setIncentives(order) {
    incOrder.length = 0;
    const chips = [...document.querySelectorAll('#incChips input')];
    chips.forEach(function (i) { i.checked = false; });
    (Array.isArray(order) ? order : []).forEach(function (v) {
      const inp = chips.filter(function (i) { return i.value === v; })[0];
      if (inp) { inp.checked = true; incOrder.push(v); }
    });
    renumberIncentives();
  }
  function setWeights(w) {
    if (!w || typeof w !== 'object') return;
    let any = false;
    sliders.forEach(function (s) {
      const v = w[s.dataset.w];
      if (typeof v === 'number' && isFinite(v)) { s.value = Math.round(v <= 1 ? v * 100 : v); any = true; }
    });
    if (any) refreshWeights();
  }
  function resetForm() {
    form.reset();
    populateRegions(); updateUnits();
    incOrder.length = 0; renumberIncentives();
    refreshWeights();
  }

  function applyCriteria(c, opts) {
    opts = opts || {}; c = c || {};
    const p = c.project || {}, sb = p.submitted_by || {}, tl = p.timeline || {};
    const u = c.use_type || {}, f = c.facility || {}, w = c.workforce || {}, hc = w.headcount || {}, tw = w.target_wage || {};
    const inf = c.infrastructure || {}, dem = c.demographics || {}, inc = c.incentives || {}, geo = c.geography || {}, bud = c.budget || {};
    if (!opts.partial) resetForm();

    // Country first: changing it rebuilds the region lists the next lines fill.
    if (Array.isArray(geo.countries) && geo.countries.length === 1 && setRadio('country', geo.countries[0])) { populateRegions(); updateUnits(); }

    setText('project_name', p.project_name);
    setText('submit_name', sb.name); setText('submit_firm', sb.firm); setText('submit_email', sb.email); setText('submit_phone', sb.phone);
    setSelect('submit_confidentiality', p.confidentiality);
    setText('decision_by', tl.decision_by); setText('operational_by', tl.operational_by);
    setText('notes', p.notes);

    // A partial prefill lets the use-type change handler set the weight preset;
    // a full scenario carries its own weights, applied below, so no preset.
    if (setSelect('use_primary', u.primary) && useSel && opts.partial) useSel.dispatchEvent(new Event('change'));
    setText('naics', u.naics);

    setSelect('facility_type', f.type);
    if (f.building_sqft) { setText('bldg_min', f.building_sqft.min); setText('bldg_max', f.building_sqft.max); }
    if (f.site_acres) { setText('acre_min', f.site_acres.min); setText('acre_max', f.site_acres.max); }
    setText('ceiling_ft', f.ceiling_clear_height_ft);
    setCheck('expandability', f.expandability_required);

    setText('hc_initial', hc.initial); setText('hc_year5', hc.year_5);
    setGroup('skills', w.skill_profile);
    setSelect('shift', w.shift_pattern);
    setSelect('right_to_work', w.right_to_work);
    setText('wage_value', tw.value); setSelect('wage_basis', tw.basis);

    setText('power_mw', inf.power_mw); setSelect('power_reliability', inf.power_reliability);
    setCheck('gas', inf.natural_gas_required);
    setText('water_gpd', inf.water_gpd); setText('sewer_gpd', inf.sewer_gpd);
    setSelect('rail', inf.rail); setText('broadband_gbps', inf.broadband_min_gbps);
    setText('hwy_miles', inf.highway_access_max_miles); setText('air_miles', inf.commercial_airport_max_miles);
    setCheck('port', inf.port_required);
    setSelect('renewable', inf.renewable); setSelect('drought', inf.drought); setSelect('hazard', inf.hazard);

    setText('draw_radius', dem.labor_draw_radius_miles); setText('min_pop', dem.min_population); setText('min_lf', dem.min_labor_force);
    setSelect('education', dem.education_priority);

    if (Array.isArray(inc.priorities)) setIncentives(inc.priorities);
    setText('inc_target', inc.min_value_target_usd);

    if (!opts.partial || geo.required_regions) setMsel('req_regions', geo.required_regions);
    if (!opts.partial || geo.preferred_regions) setMsel('pref_regions', geo.preferred_regions);
    if (!opts.partial || geo.excluded_regions) setMsel('excl_regions', geo.excluded_regions);
    const mp = Array.isArray(geo.market_proximity) && geo.market_proximity[0];
    if (mp && mp.to) {
      setText('prox_to', mp.to);
      if (typeof mp.max_miles === 'number') setText('prox_miles', currentCountry() === 'CA' ? Math.round(mp.max_miles * 1.609) : mp.max_miles);
    }

    setText('capex', bud.capex_usd); setText('opex', bud.annual_opex_target_usd);
    setWeights(c.weights);
  }

  // ---- Scenarios: save to / open from a file ----
  // A scenario is the criteria plus the matches they produced, so a saved file
  // reopens with its results on screen and the reader can change one input,
  // generate again, and compare. The Stay or Grow seed travels too, so the
  // strategic plan built from a reopened scenario still has the report in it.
  const SCENARIO_FORMAT = 'fastlocations-project-scenario';
  function scenarioFilename(c) {
    return ((c.project.project_name || 'project').replace(/[^a-z0-9]+/gi, '_').replace(/^_+|_+$/g, '').toLowerCase() || 'project') +
      '_scenario_' + new Date().toISOString().slice(0, 10) + '.json';
  }
  function saveScenario() {
    const c = buildCriteria();
    const doc = {
      format: SCENARIO_FORMAT, version: 1, savedAt: new Date().toISOString(),
      project: c.project.project_name || null,
      criteria: c,
      results: lastResults,
      seed: window.__flProjectSeed || null
    };
    try {
      const blob = new Blob([JSON.stringify(doc, null, 2)], { type: 'application/json' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob); a.download = scenarioFilename(c);
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
      setTimeout(function () { URL.revokeObjectURL(a.href); }, 1500);
      say('Saved as ' + a.download + ' \u2014 look in your downloads folder.' + (lastResults ? ' The matches on screen are in the file.' : ''));
    } catch (err) {
      say('This browser would not let the page write a file.', true);
    }
  }
  function openScenario(file) {
    const reader = new FileReader();
    reader.onerror = function () { say('That file could not be read.', true); };
    reader.onload = function () {
      let doc;
      try { doc = JSON.parse(String(reader.result)); } catch (_) { say('That file is not a saved scenario. Nothing on screen has changed.', true); return; }
      if (!doc || doc.format !== SCENARIO_FORMAT || !doc.criteria || typeof doc.criteria !== 'object') {
        say('That is not a FastLocations scenario file. Nothing on screen has changed.', true); return;
      }
      try {
        applyCriteria(doc.criteria);
      } catch (err) {
        say('The scenario could not be applied: ' + ((err && err.message) || 'unknown error'), true); return;
      }
      window.__flProjectSeed = (doc.seed && doc.seed.source === 'stay_or_grow') ? doc.seed : null;
      const results = document.getElementById('results');
      if (doc.results && Array.isArray(doc.results.results)) {
        lastResults = doc.results;
        renderResults(doc.results);
        results.classList.add('show');
      } else {
        lastResults = null;
        results.classList.remove('show'); results.innerHTML = '';
      }
      document.getElementById('errbox').classList.remove('show');
      saveDraft();
      say('Opened ' + (doc.project || 'scenario') + (doc.savedAt ? ', saved ' + new Date(doc.savedAt).toLocaleString() : '') +
        '. Change any input and choose Generate matches to compare.');
      results.scrollIntoView({ behavior: 'smooth', block: 'start' });
    };
    reader.readAsText(file);
  }
  const saveBtn = document.getElementById('saveScenarioBtn');
  const openBtn = document.getElementById('openScenarioBtn');
  const fileInput = document.getElementById('scenarioFile');
  if (saveBtn) saveBtn.addEventListener('click', saveScenario);
  if (openBtn && fileInput) {
    openBtn.addEventListener('click', function () { fileInput.value = ''; fileInput.click(); });
    fileInput.addEventListener('change', function () { if (fileInput.files && fileInput.files[0]) openScenario(fileInput.files[0]); });
  }

  // ---- Unsaved draft ----
  // Kept in this browser so a closed tab does not cost the reader the form.
  // The Stay or Grow seed, when present, takes precedence over a draft.
  const DRAFT_KEY = 'fl_project_draft_v1';
  let draftTimer = null;
  function saveDraft() {
    try {
      const c = buildCriteria();
      localStorage.setItem(DRAFT_KEY, JSON.stringify({ savedAt: new Date().toISOString(), criteria: c }));
    } catch (_) {}
  }
  document.addEventListener('input', function () { clearTimeout(draftTimer); draftTimer = setTimeout(saveDraft, 700); });
  document.addEventListener('change', function () { clearTimeout(draftTimer); draftTimer = setTimeout(saveDraft, 700); });
  function dropDraft() { try { localStorage.removeItem(DRAFT_KEY); } catch (_) {} }
  function restoreDraft() {
    let doc = null;
    try { doc = JSON.parse(localStorage.getItem(DRAFT_KEY) || 'null'); } catch (_) { return false; }
    if (!doc || !doc.criteria) return false;
    const c = doc.criteria;
    // Only a draft with something in it is worth announcing.
    const touched = (c.project && c.project.project_name) || (c.use_type && c.use_type.primary) ||
      (c.workforce && c.workforce.headcount && c.workforce.headcount.initial != null) ||
      (c.geography && ((c.geography.preferred_regions || []).length || (c.geography.required_regions || []).length));
    if (!touched) return false;
    try { applyCriteria(c); } catch (_) { dropDraft(); return false; }
    const box = document.createElement('div');
    box.className = 'seedbox'; box.id = 'draftbox';
    box.innerHTML = '<div class="seed-head"><div><b>Unsaved draft restored</b>' +
      (doc.savedAt ? ' from ' + escHtml(new Date(doc.savedAt).toLocaleString()) : '') +
      '. Your answers were kept in this browser. Save a scenario to keep them as a file.</div>' +
      '<button type="button" class="seed-clear" id="draftClear">Start over</button></div>';
    form.insertBefore(box, form.firstElementChild);
    box.classList.add('show');
    document.getElementById('draftClear').addEventListener('click', function () { dropDraft(); location.replace(location.pathname); });
    return true;
  }

  // ---- Prefill from Stay or Grow ----
  // /StayOrGrow/intake.html writes a partial ProjectCriteria (plus a list of
  // which fields it filled and why) to localStorage, then opens this page.
  // Everything it touches is listed in a banner at the top of the form so the
  // reader knows what to check. Nothing here submits anything — Generate
  // matches is still the reader's own click.
  const SEED_KEY = 'fl_project_seed_v1';
  const SEED_SESSION_KEY = 'fl_project_seed_active';
  const SEED_MAX_AGE_MS = 7 * 24 * 3600 * 1000;

  function takeSeed() {
    // The localStorage copy is consumed on first read and parked in
    // sessionStorage, so a refresh of this tab re-applies it but a later,
    // unrelated visit to the form does not open prefilled with stale figures.
    let raw = null;
    try {
      raw = localStorage.getItem(SEED_KEY);
      if (raw) { localStorage.removeItem(SEED_KEY); sessionStorage.setItem(SEED_SESSION_KEY, raw); }
      else raw = sessionStorage.getItem(SEED_SESSION_KEY);
    } catch (_) { return null; }
    if (!raw) return null;
    let seed;
    try { seed = JSON.parse(raw); } catch (_) { return null; }
    if (!seed || seed.version !== 1 || seed.source !== 'stay_or_grow' || !seed.criteria) return null;
    const age = Date.now() - Date.parse(seed.createdAt || 0);
    if (!(age >= 0 && age < SEED_MAX_AGE_MS)) return null;
    return seed;
  }

  function dropSeed() {
    try { localStorage.removeItem(SEED_KEY); sessionStorage.removeItem(SEED_SESSION_KEY); } catch (_) {}
  }

  function applySeed(seed) {
    applyCriteria(seed.criteria, { partial: true });
    const nudges = seed.weightNudges || {};
    let nudged = false;
    sliders.forEach(function (s) {
      const d = nudges[s.dataset.w];
      if (typeof d === 'number' && d) { s.value = Math.max(0, Math.min(Number(s.max) || 100, Number(s.value) + d)); nudged = true; }
    });
    if (nudged) refreshWeights();
  }

  function showSeedBanner(seed) {
    const rep = seed.report || {};
    const box = document.createElement('div');
    box.className = 'seedbox';
    box.id = 'seedbox';
    const when = rep.reportDate ? ' run on ' + escHtml(rep.reportDate) : '';
    const verdict = rep.verdictLabel ? ' Its verdict was <b>' + escHtml(rep.verdictLabel) + '</b>' +
      (rep.endStateLabel ? ', with <b>' + escHtml(rep.endStateLabel.toLowerCase()) + '</b> as the long-term fix' : '') + '.' : '';
    const chips = (seed.seeded || []).map(function (s) {
      return '<span title="' + escHtml((s.value != null ? s.value + ' \u2014 ' : '') + (s.basis || '')) + '">' + escHtml(s.label) + '</span>';
    }).join('');
    box.innerHTML =
      '<div class="seed-head"><div><b>Prefilled from your Stay or Grow report</b>' + when + '.' + verdict +
      ' The fields below were filled from that report and are yours to change; everything about <b>where</b> is still blank, because that is the question this page asks.</div>' +
      '<button type="button" class="seed-clear" id="seedClear">Start blank instead</button></div>' +
      (chips ? '<div class="seed-fields">Filled: ' + chips + '</div>' : '');
    form.insertBefore(box, form.firstElementChild);
    box.classList.add('show');
    document.getElementById('seedClear').addEventListener('click', function () {
      dropSeed(); dropDraft();
      location.replace(location.pathname);
    });
  }

  (function initSeed() {
    const seed = takeSeed();
    if (!seed) { try { restoreDraft(); } catch (_) {} return; }
    try {
      applySeed(seed);
      showSeedBanner(seed);
      window.__flProjectSeed = seed;   // inspectable in the console while testing
      saveDraft();
    } catch (err) {
      // A bad seed must never take the form down with it: drop it and carry on blank.
      dropSeed();
      if (window.console) console.warn('Stay or Grow prefill skipped:', err);
    }
  })();
})();
