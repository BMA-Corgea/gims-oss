// Debug control - set to false to disable all grid debug logging
const DEBUG_ENABLED = false;
const debug = DEBUG_ENABLED ? console.debug.bind(console) : () => {};

(function () {
  // ===== DOM =====
  const $project = document.getElementById('projectSelect');
  const $refreshProjectsBtn = document.getElementById('refreshProjectsBtn');

  // Load/Save
  const $loadPolicyBtn = document.getElementById('loadPolicyBtn');
  const $savePolicyBtn = document.getElementById('savePolicyBtn');
  const $policyStatus  = document.getElementById('policyStatus');

  // Defaults
  const $def_strategy  = document.getElementById('def_strategy');
  const $def_onref     = document.getElementById('def_onref');
  const $def_days_never= document.getElementById('def_days_never');
  const $def_days_range= document.getElementById('def_days_range');
  const $def_days_num  = document.getElementById('def_days_num');
  const $def_max_unlim = document.getElementById('def_max_unlim');
  const $def_max_range = document.getElementById('def_max_range');
  const $def_max_num   = document.getElementById('def_max_num');
  const $def_include_files = document.getElementById('def_include_files');
  const $def_schema_ver    = document.getElementById('def_schema_ver');

  // Override lists
  const $nounList = document.getElementById('nounOverrideList');
  const $verbList = document.getElementById('verbOverrideList');
  const $addNounOverrideBtn = document.getElementById('addNounOverrideBtn');
  const $addVerbOverrideBtn = document.getElementById('addVerbOverrideBtn');

  // Noun archive (legacy policy card IDs)
  const $previewNounPolicyBtn = document.getElementById('previewNounPolicyBtn');
  const $applyNounPolicyBtn   = document.getElementById('applyNounPolicyBtn');
  const $applyNounSelectedBtn = document.getElementById('applyNounSelectedBtn');
  const $nounPreviewBody      = document.getElementById('nounPreviewBody');
  const $nounActionStatus     = document.getElementById('nounActionStatus');

  // Manual noun selection
  const $manualNounType     = document.getElementById('manualNounType');
  const $manualIds          = document.getElementById('manualIds');
  const $applyManualBtn     = document.getElementById('applyManualBtn');
  const $manualActionStatus = document.getElementById('manualActionStatus') || $nounActionStatus;
  const $manualInstanceMount = document.getElementById('manualInstancePicker') || $manualIds;

  // ===== New: Noun Restore card (separate sibling card) =====
  let $restoreCard, $restoreNounType, $applyRestoreBtn, $restoreActionStatus, $restoreMount, $restoreCounts;
  let restorePicker = null;
  let RESTORE_ARCHIVED_MAP = new Map(); // id -> 'soft' | 'hard'

  // ===== New: Run Archive + Run Restore cards =====
  let $runArchiveCard, $runArchiveGroupSel, $runArchiveMount, $runArchiveBtn, $runArchiveStatus, runArchivePicker, $runArchiveCounts;
  let $runRestoreCard, $runRestoreGroupSel, $runRestoreMount, $runRestoreBtn, $runRestoreStatus, runRestorePicker, $runRestoreCounts;

  // API limits
  const ARCHIVE_FETCH_LIMIT = 5000; // matches FastAPI limit<=5000

  // ===== Tooltip manager (single node) =====
  const Tip = (() => {
    let el = null, currentTarget = null;
    const getEl = () => (el ||= document.getElementById('__tooltip'));
    function place(target) {
      const tooltip = getEl(); if (!tooltip || !target) return;
      const rect = target.getBoundingClientRect();
      const pad = 8, mw = Math.min(420, Math.max(220, rect.width));
      tooltip.style.maxWidth = mw + 'px'; tooltip.style.width = 'auto';
      let top = rect.bottom + pad, left = rect.left;
      const vw = window.innerWidth, elRect = tooltip.getBoundingClientRect();
      const expectedWidth = Math.min(mw, elRect.width || mw);
      if (left + expectedWidth > vw - 8) left = vw - expectedWidth - 8;
      if (top + (elRect.height || 120) > window.innerHeight - 8) top = rect.top - pad - (elRect.height || 120);
      tooltip.style.top  = `${Math.max(8, top)}px`;
      tooltip.style.left = `${Math.max(8, left)}px`;
    }
    function show(target, text) {
      const t = getEl(); if (!t || !text) return;
      currentTarget = target; t.textContent = text; t.hidden = false; place(target);
    }
    function hide() { const t = getEl(); if (!t) return; currentTarget = null; t.hidden = true; t.textContent = ''; }
    document.addEventListener('pointerenter', e => { const t = e.target.closest('[data-tip]'); if (t) show(t, t.getAttribute('data-tip')); }, true);
    document.addEventListener('pointerleave', e => { if (e.target.closest('[data-tip]')) hide(); }, true);
    document.addEventListener('focusin',  e => { const t = e.target.closest('[data-tip]'); if (t) show(t, t.getAttribute('data-tip')); });
    document.addEventListener('focusout', e => { if (e.target.closest('[data-tip]')) hide(); });
    window.addEventListener('scroll', () => currentTarget && place(currentTarget), true);
    window.addEventListener('resize', () => currentTarget && place(currentTarget));
    return { show, hide, place };
  })();

  // ===== Helpers =====
  function log(msg, type = 'info') {
    if (!$console) return;
    const line = document.createElement('div');
    line.className = `log ${type}`;
    line.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
    $console.prepend(line);
  }

  // Cache-busting + no-store on GETs so refreshes always reflect server state
  async function jsonFetch(url, options = {}) {
    const isGet = !options.method || String(options.method).toUpperCase() === 'GET';
    const finalUrl = isGet
      ? url + (url.includes('?') ? '&' : '?') + `_ts=${Date.now()}`
      : url;

    const base = isGet ? { cache: 'no-store' } : {};

    debug('jsonFetch', finalUrl, options);
    const res = await fetch(finalUrl, {
      ...base,
      ...options,
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) }
    });

    if (!res.ok) {
      const txt = await res.text().catch(() => `${res.status}`);
      throw new Error(`${res.status} ${res.statusText}: ${txt}`);
    }
    const ct = res.headers.get('content-type') || '';
    return ct.includes('application/json') ? res.json() : res.text();
  }

  function getProject() {
    const p = $project?.value;
    if (!p) throw new Error('Pick a project first.');
    return p;
  }

  function setStatus($el, msg, variant = 'info') {
    if (!$el) return;
    $el.textContent = msg;
    $el.className = `status ${variant}`;
  }

  // Segmented control enhancer (idempotent)
  function toTitle(s) { return String(s || '').replace(/[_\-]+/g, ' ').replace(/\b\w/g, m => m.toUpperCase()); }
  function enhanceSegmented(sel) {
    if (!sel) return null;
    let wrap = sel.nextElementSibling;
    if (!(wrap && wrap.classList.contains('seg'))) {
      wrap = document.createElement('div'); wrap.className = 'seg';
      Array.from(sel.options).forEach(o => {
        const btn = document.createElement('button');
        btn.type = 'button'; btn.className = 'seg-btn';
        btn.dataset.value = o.value; btn.textContent = toTitle(o.textContent || o.value);
        wrap.appendChild(btn);
      });
      wrap.addEventListener('click', (e) => {
        const btn = e.target.closest('.seg-btn'); if (!btn) return;
        const val = btn.dataset.value;
        if (sel.value !== val) {
          sel.value = val;
          sel.dispatchEvent(new Event('input'));
          sel.dispatchEvent(new Event('change'));
        }
        sync();
      });
      sel.style.display = 'none';
      sel.parentElement?.insertBefore(wrap, sel.nextSibling);
    }
    function sync() { Array.from(wrap.children).forEach(b => b.classList.toggle('active', b.dataset.value === sel.value)); }
    sync();
    return wrap;
  }

  // Range/number linking with an optional disabling toggle
  function linkRangeNumber($range, $num, $toggle) {
    if (!$range || !$num) return;
    const sync = (fromRange) => {
      if ($toggle?.checked) { $range.disabled = true; $num.disabled = true; return; }
      $range.disabled = false; $num.disabled = false;
      if (fromRange) $num.value = $range.value; else $range.value = $num.value;
    };
    $range.addEventListener('input', () => sync(true));
    $num.addEventListener('input', () => sync(false));
    $toggle?.addEventListener('change', () => sync(true));
    sync(true);
  }

  // Simple chips editor
  function chipsEditor(initial = []) {
    const wrap = document.createElement('div');
    const list = document.createElement('div'); list.className = 'chips';
    const input = document.createElement('input'); input.className = 'text'; input.placeholder = 'Type ID and press Enter';
    function add(v) {
      const val = (v || '').trim(); if (!val) return;
      const dup = Array.from(list.querySelectorAll('.chip span')).some(s => s.textContent === val);
      if (dup) return;
      const label = document.createElement('label'); label.className = 'chip';
      const span = document.createElement('span'); span.textContent = val;
      const x = document.createElement('button'); x.className='chip-x'; x.type='button'; x.textContent='×';
      x.addEventListener('click', () => label.remove());
      label.appendChild(span); label.appendChild(x); list.appendChild(label);
    }
    input.addEventListener('keydown', e => { if (e.key === 'Enter') { add(input.value); input.value=''; } });
    initial.forEach(add);
    wrap.getValues = () => Array.from(list.querySelectorAll('.chip span')).map(s => s.textContent);
    wrap.appendChild(list); wrap.appendChild(input);
    return wrap;
  }

  // Accordion
  function initAccordion() {
    const acc = document.querySelector('.accordion'); if (!acc) return;
    acc.addEventListener('click', e => {
      const head = e.target.closest('.acc-head'); if (head) head.parentElement.classList.toggle('open');
    });
    const first = acc.querySelector('.acc-item'); if (first) first.classList.add('open');
  }

  // ===== Searchable checkbox picker (used for instances/runs) =====
  function makeSearchableCheckboxPicker({ mountAfter, values = [], onChange, emptyText = 'No items' }) {
    const mount = mountAfter || document.body;

    const wrap = document.createElement('div');
    wrap.className = 'picker';
    wrap.style.display = 'grid';
    wrap.style.gridTemplateRows = 'auto auto 1fr';
    wrap.style.gap = '8px';
    wrap.style.border = '1px solid var(--panel-3, #333)';
    wrap.style.borderRadius = '10px';
    wrap.style.padding = '8px';
    wrap.style.maxHeight = '260px';

    const search = document.createElement('input');
    search.type = 'search'; search.placeholder = 'Search…';
    search.className = 'text'; search.autocomplete = 'off';

    const controls = document.createElement('div');
    controls.style.display = 'flex';
    controls.style.gap = '8px';
    const btnAll = document.createElement('button');
    btnAll.type = 'button'; btnAll.className = 'btn subtle'; btnAll.textContent = 'Select visible';
    const btnNone = document.createElement('button');
    btnNone.type = 'button'; btnNone.className = 'btn subtle'; btnNone.textContent = 'Clear';
    controls.appendChild(btnAll); controls.appendChild(btnNone);

    const list = document.createElement('div');
    list.style.overflow = 'auto';
    list.style.display = 'grid';
    list.style.gap = '4px';

    const state = new Set();

    function render(filter = '') {
      const f = filter.trim().toLowerCase();
      list.innerHTML = '';
      const filtered = values.filter(v => {
        const s = typeof v === 'string' ? v : v?.toString?.() || String(v);
        return !f || s.toLowerCase().includes(f);
      });
      if (filtered.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'muted'; empty.textContent = emptyText;
        list.appendChild(empty);
      } else {
        filtered.forEach(v => {
          const labelText = typeof v === 'string' ? v : (v?.label ?? v?.run_id ?? JSON.stringify(v));
          const value = typeof v === 'string' ? v : (v?.value ?? v?.run_id ?? labelText);

          const lab = document.createElement('label');
          lab.className = 'row';
          lab.style.display = 'flex'; lab.style.gap = '8px'; lab.style.alignItems = 'center';

          const cb = document.createElement('input');
          cb.type = 'checkbox'; cb.checked = state.has(value);
          cb.addEventListener('change', () => { if (cb.checked) state.add(value); else state.delete(value); onChange?.(Array.from(state)); });

          const span = document.createElement('span'); span.textContent = labelText;

          lab.appendChild(cb); lab.appendChild(span);
          list.appendChild(lab);
        });
      }
    }

    search.addEventListener('input', () => render(search.value));
    btnAll.addEventListener('click', () => {
      const f = search.value.trim().toLowerCase();
      values.forEach(v => {
        const s = typeof v === 'string' ? v : (v?.label ?? v?.run_id ?? JSON.stringify(v));
        if (!f || s.toLowerCase().includes(f)) state.add(typeof v === 'string' ? v : (v?.value ?? v?.run_id ?? s));
      });
      render(search.value); onChange?.(Array.from(state));
    });
    btnNone.addEventListener('click', () => { state.clear(); render(search.value); onChange?.(Array.from(state)); });

    wrap.appendChild(search); wrap.appendChild(controls); wrap.appendChild(list);
    render('');

    wrap.getSelected = () => Array.from(state);
    wrap.setValues = (arr = []) => { values = arr.slice(); state.clear(); render(search.value); onChange?.(Array.from(state)); };
    wrap.setSelected = (arr = []) => { state.clear(); arr.forEach(v => state.add(v)); render(search.value); onChange?.(Array.from(state)); };

    mount.parentElement?.insertBefore(wrap, mount.nextSibling);
    return wrap;
  }

  // ===== Type loading (with fallbacks) =====
  let NOUN_TYPES = [];
  let VERB_TYPES = [];
  let LAST_POLICY = { default: {}, nouns: {}, verbs: {} };
  let instancePicker = null;

  async function loadTypeLists(project) {
    debug('loadTypeLists', project);
    NOUN_TYPES = [];
    VERB_TYPES = [];

    try {
      const nt = await jsonFetch(`/api/archive_workbench/${encodeURIComponent(project)}/noun_types`);
      if (Array.isArray(nt)) NOUN_TYPES = nt;
    } catch (e) { debug('noun_types API failed:', e); }

    if (NOUN_TYPES.length === 0) {
      try {
        const prev = await jsonFetch(`/api/archive_workbench/${encodeURIComponent(project)}/nouns/preview`);
        if (prev && typeof prev === 'object') NOUN_TYPES = Object.keys(prev);
      } catch {}
    }
    if (NOUN_TYPES.length === 0) NOUN_TYPES = Object.keys(LAST_POLICY.nouns || {});

    try {
      const vt = await jsonFetch(`/api/archive_workbench/${encodeURIComponent(project)}/verb_types`);
      if (Array.isArray(vt)) VERB_TYPES = vt;
    } catch {}

    if (VERB_TYPES.length === 0) VERB_TYPES = Object.keys(LAST_POLICY.verbs || {});

    NOUN_TYPES = Array.from(new Set(NOUN_TYPES)).sort((a,b)=>a.localeCompare(b));
    VERB_TYPES = Array.from(new Set(VERB_TYPES)).sort((a,b)=>a.localeCompare(b));

    // Populate the manual noun select
    if ($manualNounType) {
      $manualNounType.innerHTML = '';
      NOUN_TYPES.forEach(t => {
        const opt = document.createElement('option');
        opt.value = t; opt.textContent = t;
        $manualNounType.appendChild(opt);
      });
    }

    // Build instance picker for manual nouns
    if ($manualInstanceMount) {
      if (instancePicker && instancePicker.parentElement) {
        instancePicker.parentElement.removeChild(instancePicker);
      }
      instancePicker = makeSearchableCheckboxPicker({
        mountAfter: $manualInstanceMount,
        values: [],
        onChange: (ids) => debug('manual instance selected ids:', ids),
        emptyText: 'No instances'
      });
      if ($manualIds) $manualIds.style.display = 'none';
    }

    // (Re)build restore nouns + run cards
    buildOrUpdateRestoreCard();
    buildOrUpdateRunCards();

    debug('Types:', { nouns: NOUN_TYPES.length, verbs:  VERB_TYPES.length });
  }

  // ===== Override card factory (dropdown for type; segmented only for strategy/onref) =====
  function overrideCard(kind, typeOptions, preset = {}) {
    const card = document.createElement('div');
    card.className = 'override-card full';

    const head = document.createElement('div');
    head.className = 'override-head';
    const title = document.createElement('h3');
    title.textContent = kind === 'noun' ? 'Noun Override' : 'Verb Override';
    const rm = document.createElement('button');
    rm.className = 'btn danger subtle';
    rm.textContent = 'Remove';
    rm.addEventListener('click', () => card.remove());
    head.appendChild(title); head.appendChild(rm);

    const body = document.createElement('div');
    body.className = 'override-body';

    // Type (PLAIN SELECT)
    const fType = document.createElement('div'); fType.className = 'field';
    const sel = document.createElement('select'); sel.className = 'ov_type';
    const ph = document.createElement('option'); ph.value = ''; ph.textContent = '— Pick one —';
    sel.appendChild(ph);
    (typeOptions || []).forEach(t => {
      const o = document.createElement('option'); o.value = t; o.textContent = t;
      sel.appendChild(o);
    });
    sel.value = preset.type || '';
    const labType = document.createElement('label'); labType.className='label';
    labType.textContent = kind==='noun' ? 'Noun Type' : 'Verb/Test Type';
    fType.appendChild(labType); fType.appendChild(sel);
    const hintType = document.createElement('div'); hintType.className='hint';
    hintType.textContent = kind==='noun' ? 'Pick the noun type this override applies to.' : 'Pick the verb/test type this override applies to.';
    fType.appendChild(hintType);

    // Strategy segmented
    const fStrat = document.createElement('div'); fStrat.className = 'field';
    fStrat.innerHTML = `<span class="label">Strategy</span>
      <select class="ov_strategy">
        <option value="soft">soft</option>
        <option value="hard">hard</option>
      </select>
      <div class="hint">Soft keeps rows in primary DB (marked); Hard moves rows to archive DB.</div>`;
    fStrat.querySelector('select').value = preset.strategy || 'soft';
    enhanceSegmented(fStrat.querySelector('select'));

    // Archive after days
    const fDays = document.createElement('div'); fDays.className = 'field';
    fDays.innerHTML = `
      <div class="slider-head">
        <span class="label">${kind==='verb'?'Archive runs after days':'Archive after days'}</span>
        <label class="toggle"><input class="ov_days_never" type="checkbox" ${preset.archive_after_days==null?'checked':''}> Never</label>
      </div>
      <input class="ov_days_range" type="range" min="0" max="3650" step="1" value="${Number(preset.archive_after_days ?? 0)}">
      <div class="slider-foot">
        <input class="ov_days_num" type="number" min="0" max="3650" step="1" value="${Number(preset.archive_after_days ?? 0)}">
        <span class="hint">0–3650</span>
      </div>
      <div class="hint">Delay before items/runs are eligible for archiving.</div>`;

    // Max items
    const fMax = document.createElement('div'); fMax.className = 'field';
    fMax.innerHTML = `
      <div class="slider-head">
        <span class="label">Max items</span>
        <label class="toggle"><input class="ov_max_unlim" type="checkbox" ${preset.max_items==null?'checked':''}> Unlimited</label>
      </div>
      <input class="ov_max_range" type="range" min="0" max="1000000" step="100" value="${Number(preset.max_items ?? 0)}">
      <div class="slider-foot">
        <input class="ov_max_num" type="number" min="0" max="1000000" step="100" value="${Number(preset.max_items ?? 0)}">
        <span class="hint">0–1,000,000</span>
      </div>
      <div class="hint">Cap hot items/runs before older ones are archived.</div>`;

    // Flags
    const fFlags = document.createElement('div'); fFlags.className = 'field';
    fFlags.innerHTML = `
      <span class="label">Flags</span>
      <label class="toggle"><input class="ov_include_files" type="checkbox" ${preset.include_files!==false?'checked':''}> include files</label>
      <label class="toggle"><input class="ov_schema_ver" type="checkbox" ${preset.schema_versioning!==false?'checked':''}> schema versioning</label>
      <div class="hint">Move/copy attachments and preserve the schema version.</div>`;

    // Noun-only fields: on_reference + exceptions
    let getOnRef = () => undefined;
    let getExceptions = () => [];
    if (kind === 'noun') {
      const fOnRef = document.createElement('div'); fOnRef.className = 'field';
      fOnRef.innerHTML = `
        <span class="label">On reference</span>
        <select class="ov_on">
          <option value="tombstone">tombstone</option>
          <option value="detach">detach</option>
          <option value="error">error</option>
        </select>
        <div class="hint">If archived item is still referenced by an active one.</div>`;
      fOnRef.querySelector('select').value = preset.on_reference || 'tombstone';
      enhanceSegmented(fOnRef.querySelector('select'));

      const fExc = document.createElement('div'); fExc.className = 'field';
      fExc.innerHTML = `<span class="label">Never archive these IDs</span>`;
      const excCtl = chipsEditor(Array.isArray(preset.exceptions) ? preset.exceptions : []);
      fExc.appendChild(excCtl);
      getOnRef = () => fOnRef.querySelector('select').value;
      getExceptions = () => excCtl.getValues();

      body.appendChild(fOnRef);
      body.appendChild(fType);
    } else {
      body.appendChild(fType);
    }

    // Link sliders
    const dn = fDays.querySelector('.ov_days_num');
    const dr = fDays.querySelector('.ov_days_range');
    const dnever = fDays.querySelector('.ov_days_never');
    linkRangeNumber(dr, dn, dnever);

    const mn = fMax.querySelector('.ov_max_num');
    const mr = fMax.querySelector('.ov_max_range');
    const munlim = fMax.querySelector('.ov_max_unlim');
    linkRangeNumber(mr, mn, munlim);

    // Assemble
    body.appendChild(fStrat);
    body.appendChild(fDays);
    body.appendChild(fMax);
    body.appendChild(fFlags);

    card.appendChild(head);
    card.appendChild(body);

    // Export
    card.getValue = () => {
      const type = sel.value;
      if (!type) return null;
      const out = {
        strategy: fStrat.querySelector('select').value,
        archive_after_days: dnever.checked ? null : Number(dn.value),
        max_items: munlim.checked ? null : Number(mn.value),
        include_files: fFlags.querySelector('.ov_include_files').checked,
        schema_versioning: fFlags.querySelector('.ov_schema_ver').checked
      };
      if (kind === 'noun') {
        out.on_reference = getOnRef();
        out.exceptions = getExceptions();
      }
      return [type, out];
    };

    return card;
  }

  // ===== Policy ⇆ UI =====
  function writeDefaultsUI(def) {
    $def_strategy.value = def.strategy ?? 'soft';
    $def_onref.value    = def.on_reference ?? 'tombstone';
    enhanceSegmented($def_strategy);
    enhanceSegmented($def_onref);

    $def_days_never.checked = def.archive_after_days == null;
    $def_days_num.value   = Number(def.archive_after_days ?? 0);
    $def_days_range.value = Number(def.archive_after_days ?? 0);
    linkRangeNumber($def_days_range, $def_days_num, $def_days_never);

    $def_max_unlim.checked = def.max_items == null;
    $def_max_num.value   = Number(def.max_items ?? 0);
    $def_max_range.value = Number(def.max_items ?? 0);
    linkRangeNumber($def_max_range, $def_max_num, $def_max_unlim);

    $def_include_files.checked = def.include_files !== false;
    $def_schema_ver.checked    = def.schema_versioning !== false;
  }

  function readDefaultsUI() {
    return {
      strategy: $def_strategy.value,
      on_reference: $def_onref.value,
      archive_after_days: $def_days_never.checked ? null : Number($def_days_num.value),
      max_items: $def_max_unlim.checked ? null : Number($def_max_num.value),
      include_files: $def_include_files.checked,
      schema_versioning: $def_schema_ver.checked
    };
  }

  function renderOverrideLists(policy) {
    // Nouns
    $nounList.innerHTML = '';
    const nounEntries = Object.entries(policy.nouns || {});
    if (nounEntries.length === 0 && NOUN_TYPES.length === 0) {
      const msg = document.createElement('div');
      msg.className = 'status info';
      msg.textContent = 'No noun types found. Load policy or refresh projects.';
      $nounList.appendChild(msg);
    } else {
      nounEntries.forEach(([type, cfg]) => {
        const card = overrideCard('noun', NOUN_TYPES, { type, ...cfg });
        $nounList.appendChild(card);
      });
    }

    // Verbs
    $verbList.innerHTML = '';
    const verbEntries = Object.entries(policy.verbs || {});
    if (verbEntries.length === 0 && VERB_TYPES.length === 0) {
      const msg = document.createElement('div');
      msg.className = 'status info';
      msg.textContent = 'No verb types found. Load policy or refresh projects.';
      $verbList.appendChild(msg);
    } else {
      verbEntries.forEach(([type, cfg]) => {
        const card = overrideCard('verb', VERB_TYPES, { type, ...cfg });
        $verbList.appendChild(card);
      });
    }
  }

  function writePolicyToUI(policy) {
    LAST_POLICY = policy || { default: {}, nouns: {}, verbs: {} };
    writeDefaultsUI(LAST_POLICY.default || {});
    renderOverrideLists(LAST_POLICY);
    setStatus($policyStatus, 'Policy loaded', 'ok');
    log('Policy loaded', 'ok');
  }

  function readPolicyFromUI() {
    const out = { default: readDefaultsUI(), nouns: {}, verbs: {} };

    $nounList.querySelectorAll('.override-card').forEach(c => {
      const v = c.getValue?.(); if (!v) return;
      const [type, cfg] = v; out.nouns[type] = cfg;
    });
    $verbList.querySelectorAll('.override-card').forEach(c => {
      const v = c.getValue?.(); if (!v) return;
      const [type, cfg] = v; out.verbs[type] = cfg;
    });

    return out;
  }

  // ===== API: load/save policy + projects =====
  async function loadProjects() {
    if (!$project) return;
    try {
      log('Loading projects…');
      const list = await jsonFetch('/api/archive_workbench/projects');
      $project.innerHTML = '';
      list.forEach(name => {
        const opt = document.createElement('option');
        opt.value = name; opt.textContent = name; $project.appendChild(opt);
      });
      const url = new URL(window.location.href);
      const q = url.searchParams.get('project');
      if (q && list.includes(q)) $project.value = q;
      if (!$project.value && list.length) $project.value = list[0];
      log(`Projects loaded (${list.length}). Using: ${$project.value}`, 'ok');
    } catch (e) { log(`Failed to load projects: ${e.message}`, 'err'); }
  }

  async function loadPolicy() {
    const project = getProject();
    try {
      log(`Loading policy for ${project}…`);
      const data = await jsonFetch(`/api/archive_workbench/${encodeURIComponent(project)}/policy`);
      LAST_POLICY = data || { default: {}, nouns: {}, verbs: {} };
      await loadTypeLists(project); // refresh types for selects
      writePolicyToUI(LAST_POLICY);
    } catch (e) {
      setStatus($policyStatus, `Load failed: ${e.message}`, 'err');
      log(`Policy load failed: ${e.message}`, 'err');
    }
  }

  async function savePolicy() {
    const project = getProject();
    try {
      const payload = readPolicyFromUI();
      log(`Saving policy for ${project}…`);
      await jsonFetch(`/api/archive_workbench/${encodeURIComponent(project)}/policy`, {
        method: 'POST',
        body: JSON.stringify(payload)
      });
      setStatus($policyStatus, 'Policy saved', 'ok');
      log('Policy saved', 'ok');
    } catch (e) {
      setStatus($policyStatus, `Save failed: ${e.message}`, 'err');
      log(`Policy save failed: ${e.message}`, 'err');
    }
  }

  // ===== Helpers specific to archived state =====
  async function fetchArchivedIds(noun, strategy /* 'soft' | 'hard' */) {
    const project = getProject();
    const url = `/api/archive_workbench/${encodeURIComponent(project)}/nouns/archived?noun=${encodeURIComponent(noun)}&strategy=${strategy}&limit=${ARCHIVE_FETCH_LIMIT}`;
    try {
      const data = await jsonFetch(url);
      return (data?.[noun]?.ids || []).map(String);
    } catch (e) {
      debug('fetchArchivedIds failed:', strategy, e?.message || e);
      return [];
    }
  }

  // ===== Noun instance fetching for manual picker (filters out archived soft & hard) =====
  async function fetchNounInstances(noun) {
    const project = getProject();
    let ids = [];

    // 1) Preferred: dedicated API
    const try1 = `/api/archive_workbench/${encodeURIComponent(project)}/nouns/ids?type=${encodeURIComponent(noun)}`;
    try {
      const data = await jsonFetch(try1);
      if (Array.isArray(data)) ids = data.map(String);
    } catch (e) { debug('fetch ids (query) failed:', e?.message || e); }

    // 2) Alt path
    if (ids.length === 0) {
      const try2 = `/api/archive_workbench/${encodeURIComponent(project)}/nouns/ids/${encodeURIComponent(noun)}`;
      try {
        const data = await jsonFetch(try2);
        if (Array.isArray(data)) ids = data.map(String);
      } catch (e) { debug('fetch ids (path) failed:', e?.message || e); }
    }

    // 3) Fallback
    if (ids.length === 0) {
      try {
        const prev = await jsonFetch(`/api/archive_workbench/${encodeURIComponent(project)}/nouns/preview`);
        const rec = prev?.[noun] || {};
        const best = rec.all_ids || rec.eligible_ids || rec.eligible || rec.ids || [];
        if (Array.isArray(best)) ids = best.map(String);
        else if (best && typeof best === 'object') ids = Object.values(best).map(String);
      } catch (e) { debug('preview fallback failed:', e?.message || e); }
    }

    // Filter out archived
    const [soft, hard] = await Promise.all([ fetchArchivedIds(noun,'soft'), fetchArchivedIds(noun,'hard') ]);
    const archived = new Set([...soft, ...hard].map(String));
    ids = ids.map(String).filter(id => !archived.has(id));

    return ids;
  }

  // ===== Noun archive flows =====

  function renderPolicyPreviewTable(map) {
    const $tbody = $policyPreviewBody || $nounPreviewBody;
    if (!$tbody) return;

    $tbody.innerHTML = '';
    const nounTypes = Object.keys(map || {}).sort((a,b)=>a.localeCompare(b));

    nounTypes.forEach(noun => {
      const row = document.createElement('tr');

      const cellN = document.createElement('td'); cellN.textContent = noun;

      const cellS = document.createElement('td');
      cellS.textContent =
        map[noun]?.strategy
        || (LAST_POLICY.nouns?.[noun]?.strategy)
        || (LAST_POLICY.default?.strategy)
        || 'soft';

      const eligRaw = map[noun]?.eligible_ids ?? map[noun]?.eligible ?? map[noun]?.ids ?? [];
      const elig = Array.isArray(eligRaw) ? eligRaw : Object.values(eligRaw || {});
      const cellC = document.createElement('td'); cellC.textContent = String(elig.length);

      const cellIDs = document.createElement('td');
      if (elig.length === 0) {
        cellIDs.innerHTML = '<span class="muted">None eligible</span>';
      } else {
        const wrap = document.createElement('div'); wrap.className = 'chips';
        elig.forEach(id => {
          const label = document.createElement('label'); label.className = 'chip';
          if ($tbody === $nounPreviewBody) {
            const cb = document.createElement('input');
            cb.type = 'checkbox'; cb.className='select-id'; cb.dataset.noun=noun; cb.dataset.id=id;
            const span = document.createElement('span'); span.textContent = id;
            label.appendChild(cb); label.appendChild(span);
          } else {
            const span = document.createElement('span'); span.textContent = id; label.appendChild(span);
          }
          wrap.appendChild(label);
        });
        cellIDs.appendChild(wrap);
      }

      row.appendChild(cellN); row.appendChild(cellS); row.appendChild(cellC); row.appendChild(cellIDs);
      $tbody.appendChild(row);
    });
  }

  async function previewPolicyArchive() {
    const project = getProject();
    try {
      setStatus($policyActionStatus,'Previewing…','info');

      const draftPolicy = readPolicyFromUI();
      await jsonFetch(`/api/archive_workbench/${encodeURIComponent(project)}/policy`, {
        method: 'POST',
        body: JSON.stringify(draftPolicy)
      });

      const data = await jsonFetch(`/api/archive_workbench/${encodeURIComponent(project)}/nouns/preview`);
      renderPolicyPreviewTable(data);
      setStatus($policyActionStatus,'Preview ready','ok');
    } catch (e) {
      setStatus($policyActionStatus,`Preview failed: ${e.message}`,'err');
    }
  }

  async function previewNounArchive() { return previewPolicyArchive(); }

  function collectSelectedIdsLegacy() {
    const out = {};
    document.querySelectorAll('.select-id:checked').forEach(cb => {
      const noun = cb.dataset.noun; const id = cb.dataset.id;
      if (!out[noun]) out[noun] = []; out[noun].push(id);
    });
    return out;
  }

  async function applyNounArchive(selectionOrNull) {
    const project = getProject();
    try {
      const isPolicy = !selectionOrNull;
      setStatus(isPolicy ? $policyActionStatus : $manualActionStatus,'Applying…','info');

      const res = await jsonFetch(`/api/archive_workbench/${encodeURIComponent(project)}/nouns/apply`, {
        method:'POST',
        body: JSON.stringify(selectionOrNull)
      });

      setStatus(isPolicy ? $policyActionStatus : $manualActionStatus,'Archive complete','ok');
      log(`Noun archive results: ${JSON.stringify(res)}`,'ok');
      await refreshAll();
    } catch (e) {
      setStatus($nounActionStatus,`Apply failed: ${e.message}`,'err');
    }
  }

  // Manual noun flow
  async function refreshManualInstances() {
    const noun = $manualNounType?.value || '';
    if (!noun) {
      if (instancePicker) instancePicker.setValues([]);
      return;
    }
    try {
      setStatus($manualActionStatus, 'Loading instances…', 'info');
      const ids = await fetchNounInstances(noun);
      if (instancePicker) instancePicker.setValues(ids);
      setStatus($manualActionStatus, `${ids.length} instances loaded`, 'ok');
    } catch (e) {
      setStatus($manualActionStatus, `Failed to load instances: ${e.message}`, 'err');
      if (instancePicker) instancePicker.setValues([]);
    }
  }

  async function applyManualArchive() {
    const noun = $manualNounType?.value || '';
    if (!noun) { setStatus($manualActionStatus,'Pick a noun type','err'); return; }
    const ids = instancePicker?.getSelected?.() || [];
    if (!ids.length) { setStatus($manualActionStatus,'Select at least one instance','err'); return; }
    await applyNounArchive({ [noun]: ids });
  }

  // ===== Restore Noun card UI & flows =====
  function buildOrUpdateRestoreCard() {
    if (!$restoreCard) {
      $restoreCard = document.createElement('div');
      $restoreCard.className = 'override-card full';
      const head = document.createElement('div');
      head.className = 'override-head';
      const title = document.createElement('h3');
      title.textContent = 'Restore by Selection';
      head.appendChild(title);

      const body = document.createElement('div');
      body.className = 'override-body';

      const fType = document.createElement('div'); fType.className = 'field';
      const labType = document.createElement('label'); labType.className = 'label'; labType.textContent = 'Noun Type';
      $restoreNounType = document.createElement('select'); $restoreNounType.className = 'text';
      fType.appendChild(labType); fType.appendChild($restoreNounType);
      const hintType = document.createElement('div'); hintType.className = 'hint'; hintType.textContent = 'Choose any noun (not limited by policy).';
      fType.appendChild(hintType);

      const fPick = document.createElement('div'); fPick.className = 'field';
      const labPick = document.createElement('label'); labPick.className = 'label'; labPick.textContent = 'IDs to restore';
      $restoreMount = document.createElement('div');
      fPick.appendChild(labPick); fPick.appendChild($restoreMount);
      restorePicker = makeSearchableCheckboxPicker({
        mountAfter: $restoreMount,
        values: [],
        onChange: (ids)=>debug('restore selected:', ids),
        emptyText: 'No archived IDs'
      });

      $restoreCounts = document.createElement('div');
      $restoreCounts.className = 'muted';
      $restoreCounts.style.marginTop = '4px';

      const fAct = document.createElement('div'); fAct.className = 'field';
      $applyRestoreBtn = document.createElement('button'); $applyRestoreBtn.className = 'btn primary'; $applyRestoreBtn.textContent = 'Restore Selected';
      $restoreActionStatus = document.createElement('div'); $restoreActionStatus.className = 'status info'; $restoreActionStatus.textContent = '';
      fAct.appendChild($applyRestoreBtn);
      fAct.appendChild($restoreActionStatus);

      body.appendChild(fType);
      body.appendChild(fPick);
      body.appendChild($restoreCounts);
      body.appendChild(fAct);

      $restoreCard.appendChild(head);
      $restoreCard.appendChild(body);

      // Insert as a separate section (not nested inside accordion items)
      const accordionContainer = document.querySelector('.accordion')?.parentElement;
      if (accordionContainer) {
        const policySection = document.querySelector('.accordion')?.closest('section, .panel, .card') || document.querySelector('.accordion');
        if (policySection && policySection.parentElement) {
          policySection.parentElement.insertBefore($restoreCard, policySection.nextSibling);
        } else {
          accordionContainer.appendChild($restoreCard);
        }
      } else {
        document.body.appendChild($restoreCard);
      }
    }

    $restoreNounType.innerHTML = '';
    NOUN_TYPES.forEach(t => {
      const opt = document.createElement('option');
      opt.value = t; opt.textContent = t;
      $restoreNounType.appendChild(opt);
    });

    $restoreNounType.onchange = refreshRestoreInstances;
    $applyRestoreBtn.onclick = applyNounRestore;
  }

  async function refreshRestoreInstances() {
    const noun = $restoreNounType?.value || '';
    RESTORE_ARCHIVED_MAP = new Map();
    if (!noun) { restorePicker?.setValues([]); $restoreCounts.textContent = ''; return; }
    try {
      setStatus($restoreActionStatus, 'Loading archived IDs…', 'info');
      const [soft, hard] = await Promise.all([ fetchArchivedIds(noun,'soft'), fetchArchivedIds(noun,'hard') ]);
      soft.forEach(id => RESTORE_ARCHIVED_MAP.set(String(id), 'soft'));
      hard.forEach(id => RESTORE_ARCHIVED_MAP.set(String(id), 'hard'));
      const union = Array.from(new Set([...soft, ...hard].map(String))).sort((a,b)=>String(a).localeCompare(String(b)));
      restorePicker?.setValues(union);
      $restoreCounts.textContent = `${union.length} archived IDs (soft: ${soft.length}, hard: ${hard.length})`;
      setStatus($restoreActionStatus, `${union.length ? 'Ready' : 'No archived IDs'}`, union.length ? 'ok' : 'info');
    } catch (e) {
      restorePicker?.setValues([]);
      RESTORE_ARCHIVED_MAP = new Map();
      $restoreCounts.textContent = '';
      setStatus($restoreActionStatus, `Failed to load: ${e.message}`, 'err');
    }
  }

  async function applyNounRestore() {
    const noun = $restoreNounType?.value || '';
    if (!noun) { setStatus($restoreActionStatus, 'Pick a noun type', 'err'); return; }
    const selected = restorePicker?.getSelected?.() || [];
    if (!selected.length) { setStatus($restoreActionStatus, 'Select at least one ID', 'err'); return; }

    const softIds = [], hardIds = [];
    selected.forEach(id => {
      const strat = RESTORE_ARCHIVED_MAP.get(String(id));
      if (strat === 'hard') hardIds.push(id);
      else softIds.push(id);
    });

    const project = getProject();
    try {
      setStatus($restoreActionStatus, 'Restoring…', 'info');
      const results = {};
      if (softIds.length) {
        const resSoft = await jsonFetch(`/api/archive_workbench/${encodeURIComponent(project)}/nouns/restore/apply?strategy=soft`, {
          method: 'POST',
          body: JSON.stringify({ [noun]: softIds })
        });
        results.soft = resSoft;
      }
      if (hardIds.length) {
        const resHard = await jsonFetch(`/api/archive_workbench/${encodeURIComponent(project)}/nouns/restore/apply?strategy=hard`, {
          method: 'POST',
          body: JSON.stringify({ [noun]: hardIds })
        });
        results.hard = resHard;
      }
      log(`Noun restore results: ${JSON.stringify(results)}`, 'ok');
      setStatus($restoreActionStatus, 'Restore complete', 'ok');
      await refreshAll();
    } catch (e) {
      setStatus($restoreActionStatus, `Restore failed: ${e.message}`, 'err');
    }
  }

  // ===== Runs: API helpers =====
  async function fetchVerbGroups() {
    const project = getProject();
    try {
      const list = await jsonFetch(`/api/archive_workbench/${encodeURIComponent(project)}/verb_groups`);
      return Array.isArray(list) ? list : [];
    } catch (e) {
      debug('fetchVerbGroups failed:', e?.message || e);
      return [];
    }
  }

  async function fetchRunsForGroup(group, where /* 'active' | 'archived' */) {
      const project = getProject();
      debug('[fetchRunsForGroup] project:', project, 'group:', group, 'where:', where);
      const candidates = [
        `/api/archive_workbench/${encodeURIComponent(project)}/runs/list?verb_group=${encodeURIComponent(group)}&where=${where}`,
        `/api/archive_workbench/${encodeURIComponent(project)}/runs/${where}?verb_group=${encodeURIComponent(group)}`,
        `/api/archive_workbench/${encodeURIComponent(project)}/runs/${where}/list?verb_group=${encodeURIComponent(group)}`
      ];
      for (const url of candidates) {
        try {
          debug('[fetchRunsForGroup] trying URL:', url);
          const data = await jsonFetch(url);
          debug('[fetchRunsForGroup] raw response:', data);
          // Accept either ["Run001", ...] or { runs: [...] } or [{run_id:..}, ...]
          let arr = [];
          if (Array.isArray(data)) {
              arr = data;
              debug('[fetchRunsForGroup] response is array directly');
          } else if (Array.isArray(data?.runs)) {
              arr = data.runs;
              debug('[fetchRunsForGroup] response has runs array:', arr);
          }
          const result = arr.map(v => (typeof v === 'string' ? v : (v?.run_id ?? v?.id ?? JSON.stringify(v)))).map(String);
          debug('[fetchRunsForGroup] processed result:', result);
          return result;
        } catch (e) {
          debug('[fetchRunsForGroup] failed for URL:', url, 'error:', e?.message || e);
        }
      }
      debug('[fetchRunsForGroup] all candidates failed, returning empty array');
      return [];
  }

  async function applyRunArchiveSelected(group, runIds) {
    const project = getProject();
    const url = `/api/archive_workbench/${encodeURIComponent(project)}/runs/archive/apply`;
    const body = runIds.map(rid => ({ verb_group: group, run_id: String(rid) }));
    return jsonFetch(url, { method: 'POST', body: JSON.stringify(body) });
  }

  async function applyRunRestoreSelected(group, runIds) {
    const project = getProject();
    const url = `/api/archive_workbench/${encodeURIComponent(project)}/runs/restore/apply`;
    const body = runIds.map(rid => ({ verb_group: group, run_id: String(rid) }));
    return jsonFetch(url, { method: 'POST', body: JSON.stringify(body) });
  }

  // ===== Runs: UI builders =====
  function buildOrUpdateRunCards() {
    // Proactively remove any legacy "Run Archive (Advanced)" card if present
    removeLegacyRunAdvancedCard();

    // --- Archive Runs card ---
    if (!$runArchiveCard) {
      $runArchiveCard = document.createElement('div');
      $runArchiveCard.className = 'override-card full';
      const head = document.createElement('div');
      head.className = 'override-head';
      const title = document.createElement('h3');
      title.textContent = 'Archive Runs by Selection';
      head.appendChild(title);

      const body = document.createElement('div'); body.className = 'override-body';

      // Verb group select
      const fGroup = document.createElement('div'); fGroup.className = 'field';
      const labGroup = document.createElement('label'); labGroup.className = 'label'; labGroup.textContent = 'Verb Group';
      $runArchiveGroupSel = document.createElement('select'); $runArchiveGroupSel.className = 'text';
      fGroup.appendChild(labGroup); fGroup.appendChild($runArchiveGroupSel);
      const hintG = document.createElement('div'); hintG.className = 'hint'; hintG.textContent = 'Pick which verb group’s runs to archive.';
      fGroup.appendChild(hintG);

      // Runs picker
      const fPick = document.createElement('div'); fPick.className = 'field';
      const labPick = document.createElement('label'); labPick.className = 'label'; labPick.textContent = 'Runs to archive';
      $runArchiveMount = document.createElement('div');
      fPick.appendChild(labPick); fPick.appendChild($runArchiveMount);
      runArchivePicker = makeSearchableCheckboxPicker({
        mountAfter: $runArchiveMount,
        values: [],
        onChange: (ids) => debug('runs selected to archive:', ids),
        emptyText: 'No runs found'
      });

      $runArchiveCounts = document.createElement('div'); $runArchiveCounts.className = 'muted'; $runArchiveCounts.style.marginTop='4px';

      // Action
      const fAct = document.createElement('div'); fAct.className = 'field';
      $runArchiveBtn = document.createElement('button'); $runArchiveBtn.className = 'btn primary'; $runArchiveBtn.textContent = 'Archive Selected';
      $runArchiveStatus = document.createElement('div'); $runArchiveStatus.className = 'status info'; $runArchiveStatus.textContent = '';
      fAct.appendChild($runArchiveBtn); fAct.appendChild($runArchiveStatus);

      body.appendChild(fGroup);
      body.appendChild(fPick);
      body.appendChild($runArchiveCounts);
      body.appendChild(fAct);

      $runArchiveCard.appendChild(head); $runArchiveCard.appendChild(body);

      // --- Restore Runs card ---
      $runRestoreCard = document.createElement('div');
      $runRestoreCard.className = 'override-card full';
      const rHead = document.createElement('div'); rHead.className = 'override-head';
      const rTitle = document.createElement('h3'); rTitle.textContent = 'Restore Runs';
      rHead.appendChild(rTitle);

      const rBody = document.createElement('div'); rBody.className = 'override-body';

      const rGroup = document.createElement('div'); rGroup.className = 'field';
      const rLabGroup = document.createElement('label'); rLabGroup.className = 'label'; rLabGroup.textContent = 'Verb Group';
      $runRestoreGroupSel = document.createElement('select'); $runRestoreGroupSel.className = 'text';
      rGroup.appendChild(rLabGroup); rGroup.appendChild($runRestoreGroupSel);
      const rHint = document.createElement('div'); rHint.className = 'hint'; rHint.textContent = 'Pick which verb group’s archived runs to restore.';
      rGroup.appendChild(rHint);

      const rPick = document.createElement('div'); rPick.className = 'field';
      const rLabPick = document.createElement('label'); rLabPick.className = 'label'; rLabPick.textContent = 'Runs to restore';
      $runRestoreMount = document.createElement('div');
      rPick.appendChild(rLabPick); rPick.appendChild($runRestoreMount);
      runRestorePicker = makeSearchableCheckboxPicker({
        mountAfter: $runRestoreMount,
        values: [],
        onChange: (ids) => debug('runs selected to restore:', ids),
        emptyText: 'No archived runs'
      });

      $runRestoreCounts = document.createElement('div'); $runRestoreCounts.className = 'muted'; $runRestoreCounts.style.marginTop='4px';

      const rAct = document.createElement('div'); rAct.className = 'field';
      $runRestoreBtn = document.createElement('button'); $runRestoreBtn.className = 'btn primary'; $runRestoreBtn.textContent = 'Restore Selected';
      $runRestoreStatus = document.createElement('div'); $runRestoreStatus.className = 'status info'; $runRestoreStatus.textContent = '';
      rAct.appendChild($runRestoreBtn); rAct.appendChild($runRestoreStatus);

      rBody.appendChild(rGroup);
      rBody.appendChild(rPick);
      rBody.appendChild($runRestoreCounts);
      rBody.appendChild(rAct);

      $runRestoreCard.appendChild(rHead); $runRestoreCard.appendChild(rBody);

      // Insert both cards after the noun restore card if present; otherwise after accordion parent
      const anchor = $restoreCard || document.querySelector('.accordion')?.closest('section, .panel, .card') || document.querySelector('.accordion');
      if (anchor && anchor.parentElement) {
        anchor.parentElement.insertBefore($runArchiveCard, anchor.nextSibling);
        $runArchiveCard.parentElement.insertBefore($runRestoreCard, $runArchiveCard.nextSibling);
      } else {
        document.body.appendChild($runArchiveCard);
        document.body.appendChild($runRestoreCard);
      }

      // Wire actions (handlers can be reassigned safely)
      $runArchiveGroupSel.onchange = refreshRunArchiveList;
      $runArchiveBtn.onclick = doRunArchive;

      $runRestoreGroupSel.onchange = refreshRunRestoreList;
      $runRestoreBtn.onclick = doRunRestore;
    }

    // Populate verb groups
    (async () => {
      const groups = await fetchVerbGroups();
      function fill(sel) {
        sel.innerHTML = '';
        groups.forEach(g => { const o = document.createElement('option'); o.value=g; o.textContent=g; sel.appendChild(o); });
      }
      fill($runArchiveGroupSel);
      fill($runRestoreGroupSel);
      await refreshRunArchiveList();
      await refreshRunRestoreList();
    })();
  }

  function removeLegacyRunAdvancedCard() {
    const advEl = document.getElementById('runsTextarea');
    if (advEl) {
      const card = advEl.closest('.override-card') || advEl.closest('.card') || advEl.closest('section');
      if (card) card.remove();
    }
    // Also nuke any stray controls if they exist
    ['runStrategy','previewRunsBtn','applyRunsBtn','runPlanPreview','runActionStatus'].forEach(id=>{
      const el = document.getElementById(id);
      if (el) {
        const card = el.closest('.override-card') || el.closest('.card') || el.closest('section');
        if (card) card.remove();
        else el.remove();
      }
    });
  }

  // ===== Unified refresh after any action =====
  async function refreshAll() {
    // Runs first (both panes), then nouns (manual + restore), then policy preview
    await refreshRunArchiveList();
    await refreshRunRestoreList();
    await refreshManualInstances();
    await refreshRestoreInstances();
    await previewPolicyArchive();
  }

  // ===== Runs: refresh lists & apply actions =====
  async function refreshRunArchiveList() {
      const group = $runArchiveGroupSel?.value || '';
      debug('[refreshRunArchiveList] group:', group);
      if (!group) {
          debug('[refreshRunArchiveList] no group selected, clearing');
          runArchivePicker?.setValues([]);
          $runArchiveCounts.textContent='';
          return;
      }
      try {
          setStatus($runArchiveStatus, 'Loading runs…', 'info');
          debug('[refreshRunArchiveList] fetching runs for group:', group, 'where: active');
          const runs = await fetchRunsForGroup(group, 'active');
          debug('[refreshRunArchiveList] received runs:', runs);
          debug('[refreshRunArchiveList] runs type:', typeof runs, 'isArray:', Array.isArray(runs));
          debug('[refreshRunArchiveList] runs length:', runs?.length);

          if (runArchivePicker) {
              debug('[refreshRunArchiveList] setting picker values');
              runArchivePicker.setValues(runs);
          } else {
              debug('[refreshRunArchiveList] ERROR: runArchivePicker is null/undefined');
          }

          $runArchiveCounts.textContent = `${runs.length} runs ready to archive`;
          setStatus($runArchiveStatus, runs.length ? 'Ready' : 'No runs found', runs.length ? 'ok' : 'info');
          debug('[refreshRunArchiveList] complete, status set');
      } catch (e) {
          debug('[refreshRunArchiveList] ERROR caught:', e);
          debug('[refreshRunArchiveList] ERROR stack:', e.stack);
          runArchivePicker?.setValues([]);
          $runArchiveCounts.textContent = '';
          setStatus($runArchiveStatus, `Failed to load: ${e.message}`, 'err');
      }
  }

  async function refreshRunRestoreList() {
    const group = $runRestoreGroupSel?.value || '';
    if (!group) { runRestorePicker?.setValues([]); $runRestoreCounts.textContent=''; return; }
    try {
      setStatus($runRestoreStatus, 'Loading archived runs…', 'info');
      const runs = await fetchRunsForGroup(group, 'archived');
      runRestorePicker?.setValues(runs);
      $runRestoreCounts.textContent = `${runs.length} archived runs`;
      setStatus($runRestoreStatus, runs.length ? 'Ready' : 'No archived runs', runs.length ? 'ok' : 'info');
    } catch (e) {
      runRestorePicker?.setValues([]);
      $runRestoreCounts.textContent = '';
      setStatus($runRestoreStatus, `Failed to load: ${e.message}`, 'err');
    }
  }

  async function doRunArchive() {
    const group = $runArchiveGroupSel?.value || '';
    const ids = runArchivePicker?.getSelected?.() || [];
    if (!group) { setStatus($runArchiveStatus,'Pick a verb group','err'); return; }
    if (!ids.length) { setStatus($runArchiveStatus,'Select at least one run','err'); return; }
    try {
      setStatus($runArchiveStatus, 'Archiving…', 'info');
      const res = await applyRunArchiveSelected(group, ids);
      log(`Run archive result: ${JSON.stringify(res)}`,'ok');
      setStatus($runArchiveStatus, 'Archive complete', 'ok');
      await refreshAll();
    } catch (e) {
      setStatus($runArchiveStatus, `Apply failed: ${e.message}`, 'err');
    }
  }

  async function doRunRestore() {
    const group = $runRestoreGroupSel?.value || '';
    const ids = runRestorePicker?.getSelected?.() || [];
    if (!group) { setStatus($runRestoreStatus,'Pick a verb group','err'); return; }
    if (!ids.length) { setStatus($runRestoreStatus,'Select at least one run','err'); return; }
    try {
      setStatus($runRestoreStatus, 'Restoring…', 'info');
      const res = await applyRunRestoreSelected(group, ids);
      log(`Run restore result: ${JSON.stringify(res)}`,'ok');
      setStatus($runRestoreStatus, 'Restore complete', 'ok');
      await refreshAll();
    } catch (e) {
      setStatus($runRestoreStatus, `Restore failed: ${e.message}`, 'err');
    }
  }

  // ===== Events =====
  const $console = document.getElementById('console');
  const $clearConsoleBtn = document.getElementById('clearConsoleBtn');

  $refreshProjectsBtn?.addEventListener('click', async () => {
    await loadProjects();
    await loadTypeLists(getProject());
    await loadPolicy();
    await previewPolicyArchive();
    await refreshManualInstances();
    await refreshRestoreInstances();
    await refreshRunArchiveList();
    await refreshRunRestoreList();
  });

  $project?.addEventListener('change', async () => {
    await loadTypeLists(getProject());
    await loadPolicy();
    await previewPolicyArchive();
    await refreshManualInstances();
    await refreshRestoreInstances();
    await refreshRunArchiveList();
    await refreshRunRestoreList();
  });

  $loadPolicyBtn?.addEventListener('click', loadPolicy);
  $savePolicyBtn?.addEventListener('click', savePolicy);

  $addNounOverrideBtn?.addEventListener('click', () => {
    $nounList.appendChild(overrideCard('noun', NOUN_TYPES, {}));
  });
  $addVerbOverrideBtn?.addEventListener('click', () => {
    $verbList.appendChild(overrideCard('verb', VERB_TYPES, {}));
  });

  // Policy card (new + legacy)
  const $policyPreviewBody = document.getElementById('policyPreviewBody') || $nounPreviewBody;
  const $policyActionStatus = document.getElementById('policyActionStatus') || $nounActionStatus;
  const $previewPolicyBtn   = document.getElementById('previewPolicyBtn') || $previewNounPolicyBtn;
  const $applyPolicyBtn     = document.getElementById('applyPolicyBtn') || $applyNounPolicyBtn;

  $previewNounPolicyBtn?.addEventListener('click', previewPolicyArchive);
  $previewPolicyBtn?.addEventListener('click', previewPolicyArchive);
  $applyNounPolicyBtn?.addEventListener('click', () => applyNounArchive(null));
  $applyPolicyBtn?.addEventListener('click', () => applyNounArchive(null));

  // Legacy “Archive Selected” (checkbox-based list in policy preview table)
  $applyNounSelectedBtn?.addEventListener('click', () => {
    const selection = collectSelectedIdsLegacy();
    if (!Object.keys(selection).length) { setStatus($nounActionStatus,'No IDs selected','err'); return; }
    applyNounArchive(selection);
  });

  // Manual selection card
  $manualNounType?.addEventListener('change', refreshManualInstances);
  $applyManualBtn?.addEventListener('click', applyManualArchive);

  $clearConsoleBtn?.addEventListener('click', () => { if ($console) $console.innerHTML=''; });

  // ===== Boot =====
  (async function init() {
    // NEW: Listen for generic action completions to trigger a refresh
    window.addEventListener('gims:action_completed', (e) => {
      debug('gims:action_completed event received', e.detail);
      const path = e?.detail?.path || '';
      // Only refresh if the action was one of the archive/restore endpoints
      const isArchiveEvent = /^\/api\/archive_workbench\/.+\/(apply|restore)/.test(path);
      if (isArchiveEvent) {
        log('Archive/restore action completed, refreshing workbench...', 'ok');
        refreshAll();
      }
    });

    initAccordion();
    // Keep defaults UI linked
    linkRangeNumber($def_days_range, $def_days_num, $def_days_never);
    linkRangeNumber($def_max_range,  $def_max_num,  $def_max_unlim);
    removeLegacyRunAdvancedCard();
    await loadProjects();
    await loadTypeLists(getProject());
    await loadPolicy();
    await previewPolicyArchive();
    await refreshManualInstances();
    await refreshRestoreInstances();
    await refreshRunArchiveList();
    await refreshRunRestoreList();
  })();
})();