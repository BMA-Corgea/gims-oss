// static/scripts/investigation.js

// Debug control - set to false to disable all UI debug logging
const DEBUG_ENABLED = false;
const debug = DEBUG_ENABLED ? console.debug.bind(console, "[investigation-ui]") : () => {};

const API = {
  projects: "/investigation/projects",
  nouns: (project) => `/investigation/nouns/${encodeURIComponent(project)}`,
  items: (project, noun) => `/investigation/items/${encodeURIComponent(project)}/${encodeURIComponent(noun)}`,
  table: (project, noun) => `/investigation/format_table/${encodeURIComponent(project)}/${encodeURIComponent(noun)}`,
  lineageUI: (project, noun) => `/investigation/lineage_ui/${encodeURIComponent(project)}/${encodeURIComponent(noun)}`
};

function el(id) { return document.getElementById(id); }

async function fetchJSON(url, options) {
  debug("fetchJSON:start", { url, hasOptions: !!options });
  const res = await fetch(url, options);
  if (!res.ok) {
    const t = await res.text().catch(() => "(no text)");
    debug("fetchJSON:error", { url, status: res.status, text: t.slice(0, 400) });
    throw new Error(t || `${res.status} ${res.statusText}`);
  }
  const json = await res.json();
  debug("fetchJSON:ok", { url, keys: Object.keys(json || {}) });
  return json;
}

async function init() {
  debug("Window loaded, initializing...");
  await loadProjects();
  el("project").addEventListener("change", onProjectChange);
  el("noun").addEventListener("change", () => setStatus("Select a filter or click Load Records."));
  setStatus("Select a project.");
}

function setStatus(msg) {
  const out = el("output");
  out.innerHTML = `<div class="placeholder-message">${msg}</div>`;
}

async function loadProjects() {
  const sel = el("project");
  try {
    const projects = await fetchJSON(API.projects);
    sel.innerHTML = "";

    if (!Array.isArray(projects) || projects.length === 0) {
      sel.innerHTML = `<option value="" disabled selected>No projects available</option>`;
      setStatus("No projects available.");
      return;
    }

    // Populate and default-select the first project
    projects.forEach((p, i) => {
      const opt = document.createElement("option");
      opt.value = p;
      opt.textContent = p;
      if (i === 0) opt.selected = true;
      sel.appendChild(opt);
    });

    // Set state and run normal selection logic
    sel.value = projects[0];
    await onProjectChange();
  } catch (e) {
    sel.innerHTML = `<option value="" disabled selected>Error loading projects</option>`;
    setStatus("Failed to load projects.");
  }
}

async function onProjectChange() {
  const project = el("project").value;
  if (!project) return;
  setStatus("Pick a noun type.");
  const nouns = await fetchJSON(API.nouns(project));
  const nsel = el("noun");
  nsel.innerHTML = `<option value="" disabled selected>Select a noun type</option>` +
                   nouns.map(n => `<option>${n}</option>`).join("");
}

async function loadRecords() {
  const project = el("project").value;
  const noun = el("noun").value;
  if (!project || !noun) return;

  setStatus("Loading items…");
  const items = await fetchJSON(API.items(project, noun));
  const filterVal = el("filter").value?.trim();
  const filtered = filterVal
    ? items.filter(i => JSON.stringify(i).toLowerCase().includes(filterVal.toLowerCase()))
    : items;

  debug("Loaded", filtered.length, "items");
  await renderTable(project, noun, filtered);
}

async function renderTable(project, noun, records) {
  const tableData = await fetchJSON(API.table(project, noun), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ records })
  });

  const { columns, rows, primary_id_field } = tableData;
  const thead = el("record-table").querySelector("thead");
  const tbody = el("record-table").querySelector("tbody");
  thead.innerHTML = "";
  tbody.innerHTML = "";

  // Build header
  const trh = document.createElement("tr");
  columns.forEach(h => {
    const th = document.createElement("th");
    th.textContent = h;
    trh.appendChild(th);
  });
  thead.appendChild(trh);

  // Build rows
  rows.forEach((row, idx) => {
    const tr = document.createElement("tr");
    row.forEach((cell, colIdx) => {
      const td = document.createElement("td");
      td.textContent = cell;
      tr.appendChild(td);
    });

    tr.addEventListener("click", () => {
      // Build a "record" object back from row
      const rec = {};
      columns.forEach((c, i) => { rec[c] = row[i]; });
      [...tbody.querySelectorAll("tr")].forEach(x => x.classList.remove("selected"));
      tr.classList.add("selected");
      loadLineageUI(project, noun, rec);
    });

    tbody.appendChild(tr);
  });

  setStatus("Select a record to view lineage.");
}

// ─────────────────────────────────────────────────────────────────────────────
// Lineage UI Rendering
// ─────────────────────────────────────────────────────────────────────────────

async function loadLineageUI(project, noun, record) {
  debug("Loading lineage for", project, noun, record);
  const data = await fetchJSON(API.lineageUI(project, noun), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ record })
  });
  debug("Lineage data:", data);
  renderLineagePanel(data);
}

function renderLineagePanel(data) {
  const out = el("output");
  out.innerHTML = "";

  const header = document.createElement("div");
  header.className = "lineage-display-header";
  header.innerHTML = `
    <div class="lineage-title">
      <span class="entity-icon">🧬</span>
      <h3>${escapeHtml(data.noun_type)}</h3>
    </div>
    <span class="entity-badge">${escapeHtml(data.display_id || "")}</span>
  `;

  const content = document.createElement("div");
  content.className = "lineage-display-content";

  // Summary + runs
  const runsSection = document.createElement("section");
  runsSection.className = "runs-section";
  runsSection.innerHTML = `<h4 class="section-title">Referenced Runs</h4>`;
  if (!data.runs || !data.runs.length) {
    runsSection.insertAdjacentHTML("beforeend", `<div class="no-dependencies">No runs reference this record.</div>`);
  } else {
    const list = document.createElement("div");
    list.className = "runs-list";
    data.runs.forEach(r => list.appendChild(renderRunCard(r)));
    runsSection.appendChild(list);
  }

  // Parents & Siblings
  const parentsSection = document.createElement("section");
  parentsSection.className = "parents-section";
  parentsSection.innerHTML = `<h4 class="section-title">Parents & Siblings</h4>`;
  if (!data.parents || !data.parents.length) {
    parentsSection.insertAdjacentHTML("beforeend", `<div class="no-dependencies">No referencing parents found.</div>`);
  } else {
    const cards = document.createElement("div");
    cards.className = "dependencies-list";
    data.parents.forEach(p => cards.appendChild(renderParentCard(p)));
    parentsSection.appendChild(cards);
  }

  // Retests
  const retestSection = document.createElement("section");
  retestSection.className = "retests-section";
  retestSection.innerHTML = `<h4 class="section-title">Retests via Overrides</h4>`;
  if (!data.retests || !data.retests.length) {
    retestSection.insertAdjacentHTML("beforeend", `<div class="no-dependencies">None</div>`);
  } else {
    const ul = document.createElement("ul");
    ul.className = "retest-list";
    data.retests.forEach(rt => {
      const li = document.createElement("li");
      const noun = rt.noun_instance || {};
      const nt = noun._noun_type || "Override";
      const pk = noun._primary_id_field || "run";
      const val = noun[pk] || "(no id)";
      const of = rt.retest_of || "(unknown)";
      li.innerHTML = `<span class="retest-chip">🔁</span> <b>${escapeHtml(nt)}</b> — ${escapeHtml(pk)} = <code>${escapeHtml(val)}</code> <span class="muted">(retest of ${escapeHtml(of)})</span>`;
      ul.appendChild(li);
    });
    retestSection.appendChild(ul);
  }

  content.appendChild(runsSection);
  content.appendChild(parentsSection);
  content.appendChild(retestSection);

  const footer = document.createElement("div");
  footer.className = "lineage-display-footer";
  footer.innerHTML = `
    <div>Investigation Module</div>
    <div class="muted">Rendered by investigation.js</div>
  `;

  const container = document.createElement("div");
  container.className = "lineage-display";
  container.appendChild(header);
  container.appendChild(content);
  container.appendChild(footer);

  out.appendChild(container);
}

function renderRunCard(run) {
  const card = document.createElement("div");
  card.className = "run-card";

  const top = document.createElement("div");
  top.className = "run-card-header";
  top.innerHTML = `
    <div class="run-title">
      <span class="run-icon">🧪</span>
      <div>
        <div class="verb">${escapeHtml(run.verb || "(unknown verb)")}</div>
        <div class="run-id">Run: <code>${escapeHtml(run.run_id)}</code></div>
      </div>
    </div>
    <div class="run-badges">
      <span class="badge group">${escapeHtml(run.verb_group || "Tests")}</span>
      <span class="badge mode ${run.mode}">${run.mode === "linear" ? "Linear" : "Classic"}</span>
    </div>
  `;

  const body = document.createElement("div");
  body.className = "run-card-body";

  if (run.mode === "classic") {
    body.appendChild(renderClassicProgress(run));
  } else {
    body.appendChild(renderLinearProgress(run));
  }

  if (run.override_status) {
    const ov = document.createElement("div");
    ov.className = "override-box";
    ov.innerHTML = `<div class="override-title">Overrides</div><pre>${escapeHtml(run.override_status)}</pre>`;
    body.appendChild(ov);
  }

  if (Array.isArray(run.referencing_nouns) && run.referencing_nouns.length) {
    const refs = document.createElement("div");
    refs.className = "referencing-box";
    refs.innerHTML = `<div class="referencing-title">Referencing Nouns</div>`;
    const ul = document.createElement("ul");
    run.referencing_nouns.forEach(ref => {
      const li = document.createElement("li");
      const nt = ref._noun_type || "(unknown)";
      const pk = ref._primary_id_field || "id";
      const val = ref[pk] || "(no id)";
      li.innerHTML = `<b>${escapeHtml(nt)}</b>: <code>${escapeHtml(pk)}</code> = <code>${escapeHtml(val)}</code>`;
      ul.appendChild(li);
    });
    refs.appendChild(ul);
    body.appendChild(refs);
  }

  card.appendChild(top);
  card.appendChild(body);
  return card;
}

function renderClassicProgress(run) {
  const wrap = document.createElement("div");
  wrap.className = "progress-wrap";

  const bar = document.createElement("div");
  bar.className = "progress-bar segmented";
  // 4 segments at 25% each
  (run.zones || []).forEach(z => {
    const seg = document.createElement("div");
    seg.className = "progress-segment" + (z.ok ? " ok" : " pending");
    seg.style.width = `${100 / (run.zones.length || 4)}%`;
    seg.title = `${z.label}: ${z.value}`;
    seg.innerHTML = `<span>${escapeHtml(z.label)}</span>`;
    bar.appendChild(seg);
  });

  const meta = document.createElement("div");
  meta.className = "progress-meta";
  meta.innerHTML = `
    <div class="percent">${run.percent}%</div>
    <div class="status-grid">
      ${(run.zones || []).map(z => `
        <div class="status-item">
          <span class="status-dot ${z.ok ? "ok" : "pending"}"></span>
          <span class="status-label">${escapeHtml(z.label)}</span>
          <code class="status-value">${escapeHtml(z.value)}</code>
        </div>
      `).join("")}
    </div>
  `;

  wrap.appendChild(bar);
  wrap.appendChild(meta);
  return wrap;
}

function renderLinearProgress(run) {
  const wrap = document.createElement("div");
  wrap.className = "progress-wrap";

  const bar = document.createElement("div");
  bar.className = "progress-bar";
  const fill = document.createElement("div");
  fill.className = "progress-fill";
  fill.style.width = `${run.percent}%`;
  bar.appendChild(fill);

  const meta = document.createElement("div");
  meta.className = "progress-meta";
  meta.innerHTML = `
    <div class="percent">${run.percent}%</div>
    <div class="progress-text">${escapeHtml(run.progress_text || "")}</div>
  `;

  const steps = document.createElement("div");
  steps.className = "linear-steps";
  const list = (run.linear && run.linear.breakdown) || [];
  list.forEach(st => {
    const chip = document.createElement("div");
    const isGate = st.type === "gate";
    const ok = !!st.completed;
    chip.className = "step-chip " + (ok ? "ok" : (isGate ? "gate" : "pending"));
    chip.title = `${st.label || st.id || st.type} — ${ok ? "Complete" : (st.reason || "Pending")}`;
    chip.innerHTML = `
      <span class="chip-icon">${ok ? "✔" : (isGate ? "🔒" : "…" )}</span>
      <span class="chip-label">${escapeHtml(st.label || st.id || st.type)}</span>
    `;
    steps.appendChild(chip);
  });

  wrap.appendChild(bar);
  wrap.appendChild(meta);
  wrap.appendChild(steps);
  return wrap;
}

function renderParentCard(p) {
  const card = document.createElement("div");
  card.className = "dependency-card";

  const head = document.createElement("div");
  head.className = "dependency-header";
  head.innerHTML = `
    <div class="dependency-type">${escapeHtml(p.noun_type)}</div>
    <div class="dependency-id"><code>${escapeHtml(p.noun_id)}</code></div>
  `;

  const rel = document.createElement("div");
  rel.className = "dependency-relation";
  rel.textContent = `Action Requirement: ${p.action_requirement || "(unknown)"}`;

  card.appendChild(head);
  card.appendChild(rel);

  const sibs = p.siblings || [];
  if (sibs.length) {
    const list = document.createElement("ul");
    list.className = "siblings-list";
    sibs.forEach(s => {
      const li = document.createElement("li");
      const nt = s._noun_type || "(unknown)";
      const pk = s._primary_id_field || "id";
      const val = s[pk] || "(no id)";
      const runid = s._runID || "(no run)";
      li.innerHTML = `<b>${escapeHtml(nt)}</b>: <code>${escapeHtml(pk)}</code> = <code>${escapeHtml(val)}</code> <span class="muted">run: ${escapeHtml(runid)}</span>`;
      list.appendChild(li);
    });
    const box = document.createElement("div");
    box.className = "process-info";
    box.innerHTML = `<div class="process-label">Siblings</div>`;
    box.appendChild(list);
    card.appendChild(box);
  } else {
    const none = document.createElement("div");
    none.className = "no-dependencies";
    none.textContent = "No siblings found";
    card.appendChild(none);
  }

  return card;
}

// ─────────────────────────────────────────────────────────────────────────────
// Utilities
// ─────────────────────────────────────────────────────────────────────────────
function escapeHtml(str) {
  return (str ?? "").toString()
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

// Kickoff
window.addEventListener("load", init);
