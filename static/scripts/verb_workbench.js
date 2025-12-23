// Debug
const DEBUG = false;
const dlog = DEBUG ? console.debug.bind(console) : () => {};

const qs  = (s, el=document) => el.querySelector(s);
const qsa = (s, el=document) => Array.from(el.querySelectorAll(s));
const h = (tag, attrs={}, children=[]) => {
  const el = document.createElement(tag);
  for (const [k,v] of Object.entries(attrs)) {
    if (k === "class") el.className = v;
    else if (k === "html") el.innerHTML = v;
    else if (v !== undefined && v !== null) el.setAttribute(k, v);
  }
  (Array.isArray(children) ? children : [children]).forEach(c=>{
    if (c == null) return;
    if (typeof c === "string") el.appendChild(document.createTextNode(c));
    else el.appendChild(c);
  });
  return el;
};
const toast = (c, t, type="ok") => { const n = h("div",{class:`msg ${type}`},t); c.appendChild(n); return n; };
const clear = el => { el.innerHTML = ""; };

const projectSelect = qs("#project-select");
const verbSelect    = qs("#verbSelect");

const state = {
  logConfig: null,        // {group, primary_id, fields}
  currentRunId: null,
  currentVerbGroup: null, // ✅ REAL verb-group for runlog_workbench
  schemaLoadedFor: null,  // verb
};

const getMode = () => qs('input[name="form-mode"]:checked').value;
const getProject = () => projectSelect.value;
const getVerb    = () => verbSelect.value;

// Helper: Runlog Navigation Button (FIXED: uses verb GROUP, not verb)
const getRunlogLink = (runId) => {
  const p = getProject();
  const g = state.currentVerbGroup;           // ✅ verb group (what runlog_workbench expects)
  const r = runId || state.currentRunId;

  if (!p || !g || !r) return null;

  // Build URL with project, group, and run_id
  const url =
    `/runlog_workbench` +
    `?project=${encodeURIComponent(p)}` +
    `&group=${encodeURIComponent(g)}` +
    `&run_id=${encodeURIComponent(r)}`;

  return h("a", {
    href: url,
    target: "_blank",
    style: "margin-left:12px; text-decoration:underline; font-weight:bold; color:inherit; cursor:pointer;"
  }, "Open in Runlog \u2192");
};

async function asJSON(res){
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

// Project list (served by verb_workbench backend)
async function loadProjects(){
  return fetch("/api/verb_workbench/projects").then(asJSON);
}

// Verbs for project (project in path)
async function loadVerbs(project){
  return fetch(`/api/verb_workbench/${encodeURIComponent(project)}`).then(asJSON);
}

// Log config for selected verb (project in path)
async function fetchLogConfig(verb){
  const project = getProject();
  if (!project) throw new Error("No project selected");
  return fetch(`/api/verb_workbench/${encodeURIComponent(project)}/${encodeURIComponent(verb)}/log_config`).then(asJSON);
}

// Existing runs (JSONL list) — returns ALL runs in that verb group
async function fetchRuns(verb){
  const project = getProject();
  return fetch(`/api/verb_workbench/${encodeURIComponent(project)}/${encodeURIComponent(verb)}/runs`).then(asJSON);
}

// Load single run
async function fetchRun(verb, runId){
  const project = getProject();
  return fetch(`/api/verb_workbench/${encodeURIComponent(project)}/${encodeURIComponent(verb)}/run/${encodeURIComponent(runId)}`).then(asJSON);
}

// Validate
async function validatePayload(verb, payload){
  const project = getProject();
  return fetch(`/api/verb_workbench/${encodeURIComponent(project)}/${encodeURIComponent(verb)}/validate`, {
    method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify(payload)
  }).then(asJSON);
}

// Create / Update
async function createRun(verb, payload){
  const project = getProject();
  return fetch(`/api/verb_workbench/${encodeURIComponent(project)}/${encodeURIComponent(verb)}/create`, {
    method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify(payload)
  }).then(asJSON);
}
async function updateRun(verb, runId, payload){
  const project = getProject();
  return fetch(`/api/verb_workbench/${encodeURIComponent(project)}/${encodeURIComponent(verb)}/update/${encodeURIComponent(runId)}`, {
    method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify(payload)
  }).then(asJSON);
}

// --- Verb schema fetch (to show a hint about mode) ---
async function fetchVerbSchema(verb){
  const project = getProject();
  if (!project || !verb) return null;
  try {
    // This endpoint is already path-based in your app
    return await fetch(`/verb/${encodeURIComponent(project)}/${encodeURIComponent(verb)}`).then(asJSON);
  } catch {
    return null;
  }
}

// Render form from logConfig.fields
async function renderForm(existing=null){
  const form = qs("#dynamicForm");
  clear(form);
  const fields = state.logConfig?.fields || {};
  const pid = state.logConfig?.primary_id;

  for (const [name, info] of Object.entries(fields)){
    if (name === "test_type") continue; // auto
    const required = !!info.required;

    const slot = h("div",{class:"form-field"});
    slot.appendChild(h("label",{},[
      name,
      required ? h("span",{class:"required-badge"},"required") : null
    ]));

    const input = h("input",{type:"text", class:"input"});
    input.dataset.name = name;

    // Primary ID hint
    if (name === pid) input.placeholder = "Primary ID (required)";
    if (existing && existing[name] != null) input.value = String(existing[name]);

    slot.appendChild(input);
    form.appendChild(slot);
  }
}

function collectForm(){
  const data = {};
  qsa("[data-name]", qs("#dynamicForm")).forEach(el=>{
    data[el.dataset.name] = el.value?.trim?.() ?? "";
  });
  return data;
}

// ---------- ID + filter helpers ----------
function normalizeIdValue(row, primaryId){
  // Tolerant lookup for keys like "general ID" vs "general_ID"
  if (row.hasOwnProperty(primaryId)) return row[primaryId];
  const underscore = primaryId.replace(/ /g, "_");
  const spaced     = primaryId.replace(/_/g, " ");
  if (row.hasOwnProperty(underscore)) return row[underscore];
  if (row.hasOwnProperty(spaced))     return row[spaced];
  return undefined;
}
function getRowTestType(row){
  // Prefer canonical "test_type", fall back to common variants
  return row.test_type ?? row.testType ?? row.verb ?? row.type ?? null;
}

// Populate run select for Edit mode — ONLY runs whose test_type == selected verb
async function refreshRunSelect(){
  const runSelect = qs("#runSelect");
  runSelect.disabled = true;
  runSelect.innerHTML = "<option value=''>Loading…</option>";

  const verb = getVerb();
  if (!verb){
    runSelect.innerHTML = "<option value=''>Select a verb</option>";
    return;
  }

  // Ensure we have logConfig; if schema not yet loaded for this verb, load it now
  if (!state.logConfig || state.schemaLoadedFor !== verb){
    state.logConfig = await fetchLogConfig(verb).catch(()=>null);
    state.schemaLoadedFor = verb;

    // ✅ keep verb-group synced here too (in case refreshRunSelect loads schema)
    state.currentVerbGroup =
      state.logConfig?.group ??
      state.logConfig?.verb_group ??
      state.logConfig?.verbGroup ??
      null;
  }
  if (!state.logConfig){
    clear(runSelect);
    runSelect.appendChild(h("option",{value:""},"(no log config)"));
    return;
  }

  const pid = state.logConfig.primary_id;
  if (!pid){
    clear(runSelect);
    runSelect.appendChild(h("option",{value:""},"(missing primary_id in log config)"));
    return;
  }

  // Load group log then filter to rows where test_type === selected verb
  const allRows = await fetchRuns(verb).catch(()=>[]);
  const rows = allRows.filter(r => String(getRowTestType(r)) === String(verb));

  clear(runSelect);
  if (!rows.length){
    runSelect.appendChild(h("option",{value:""},"No runs for this verb"));
    return;
  }

  runSelect.appendChild(h("option",{value:""},"Select a run"));
  rows.forEach(r=>{
    const id = normalizeIdValue(r, pid);
    if (id == null) return; // skip malformed rows
    const pretty = r["name"] ? `${id} — ${r["name"]}` : String(id);
    runSelect.appendChild(h("option",{value:String(id)}, pretty));
  });
  runSelect.disabled = false;
}

// --- call backend to refresh Status.json for a run ---
async function refreshRunStatus(){
  clear(qs("#formMessages"));
  const project = getProject();
  const verb = getVerb();
  const input = qs("#refresh-run-id");
  const runId = (input?.value || "").trim() || state.currentRunId;

  if (!project || !verb) return toast(qs("#formMessages"), "Select a project and verb.", "warn");
  if (!runId) return toast(qs("#formMessages"), "Enter a Run ID or load a run first.", "warn");

  const url = `/api/verb_workbench/${encodeURIComponent(project)}/${encodeURIComponent(verb)}/status/refresh/${encodeURIComponent(runId)}`;
  try {
    const res = await fetch(url, { method: "POST" }).then(asJSON);
    const link = getRunlogLink(runId);
    toast(qs("#formMessages"), link
      ? [`Status updated for '${runId}' • steps: ${res.steps ?? "0"}`, link]
      : `Status updated for '${runId}' • steps: ${res.steps ?? "0"}`, "ok");
  } catch (err) {
    console.error(err);
    toast(qs("#formMessages"), `Failed to update status: ${String(err)}`, "error");
  }
}

// --- enable/disable the refresh UI based on verb mode ---
async function syncRefreshBlock() {
  const block = qs("#refresh-status-block");
  const hint  = qs("#refresh-status-hint");
  const btn   = qs("#refresh-status-btn");
  const input = qs("#refresh-run-id");
  if (!block || !btn || !input || !hint) return;

  const project = getProject();
  const verb = getVerb();
  if (!project || !verb) {
    block.classList.add("hidden");
    return;
  }
  block.classList.remove("hidden");

  const schema = await fetchVerbSchema(verb);
  const isLinear = !!(schema && schema.linear_status && schema.linear_status.enabled && (schema.linear_status.steps||[]).length);
  if (isLinear) {
    hint.textContent = "";
    btn.disabled = false;
    input.disabled = false;
  } else {
    hint.textContent = "Verb is in Buckets mode; refresh writes default Status.json (no linear steps).";
    btn.disabled = false; // still allowed; backend is safe
    input.disabled = false;
  }
}

// Wiring
async function reloadSchema(){
  clear(qs("#formMessages"));
  const v = getVerb();
  if (!v) return;

  // Load the log config for this verb (verb group) so we know the primary_id
  state.logConfig = await fetchLogConfig(v).catch(()=>null);
  state.schemaLoadedFor = v;

  // ✅ Resolve and store the REAL verb group for runlog_workbench routing
  state.currentVerbGroup =
    state.logConfig?.group ??
    state.logConfig?.verb_group ??
    state.logConfig?.verbGroup ??
    null;

  await renderForm(null);
  state.currentRunId = null;

  // If in edit mode, load run options — filtered by test_type
  if (getMode() === "edit"){
    await refreshRunSelect();
  }

  // Update the refresh block
  await syncRefreshBlock();
}

// Keep state.currentRunId synced with run selector
const runSelect = qs("#runSelect");
if (runSelect) {
  runSelect.addEventListener("change", e => {
    state.currentRunId = e.target.value || null;
    dlog("[runSelect] currentRunId updated", state.currentRunId);
  });
}

async function init(){
  // projects
  const projects = await loadProjects().catch(()=>[]);
  projectSelect.innerHTML = "";

  projects.forEach((p, i) => {
    const opt = h("option", { value: p }, p);
    if (i === 0) opt.setAttribute("selected","selected"); // default to first
    projectSelect.appendChild(opt);
  });

  // when project changes, load verbs
  projectSelect.addEventListener("change", async ()=>{
    verbSelect.disabled = true;
    verbSelect.innerHTML = "<option value=''>Loading verbs…</option>";
    const verbs = (projectSelect.value) ? await loadVerbs(projectSelect.value).catch(()=>[]) : [];
    clear(verbSelect);
    if (!verbs.length){
      verbSelect.appendChild(h("option",{value:""},"No verbs"));
      verbSelect.disabled = true;
      return;
    }
    verbSelect.appendChild(h("option",{value:""},"Select a verb"));
    verbs.forEach(v=>verbSelect.appendChild(h("option",{value:v}, v)));
    verbSelect.disabled = false;
    clear(qs("#dynamicForm")); clear(qs("#formMessages"));

    // refresh block visibility
    await syncRefreshBlock();
  });

  // trigger initial verbs load for first project
  if (projects.length) {
    projectSelect.dispatchEvent(new Event("change"));
  }

  // Changing the verb reloads schema AND the filtered run list
  verbSelect.addEventListener("change", async () => {
    await reloadSchema();
  });

  qs("#reloadSchemaBtn").addEventListener("click", reloadSchema);

  // Mode toggle
  qsa('input[name="form-mode"]').forEach(radio=>{
    radio.addEventListener("change", async ()=>{
      const isEdit = getMode()==="edit";
      qs("#editLoadArea").classList.toggle("hidden", !isEdit);

      if (isEdit) {
        if (state.schemaLoadedFor === getVerb()) {
          await refreshRunSelect();
        } else if (getVerb()){
          await reloadSchema();
        }
      } else {
        // switching to create: clear form + reset state
        state.currentRunId = null;
        await renderForm(null);
        clear(qs("#formMessages"));
      }
    });
  });

  // Load existing run into form
  qs("#loadRunBtn").addEventListener("click", async ()=>{
    clear(qs("#formMessages"));
    const rid = qs("#runSelect").value;
    if (!rid) return toast(qs("#formMessages"), "Pick a run to load", "warn");
    const rec = await fetchRun(getVerb(), rid).catch(e=>{console.error(e); return null;});
    if (!rec) return toast(qs("#formMessages"), "Failed to load run", "error");
    await renderForm(rec);
    state.currentRunId = rid;

    // auto-fill the refresh input with the loaded run id
    const refreshInput = qs("#refresh-run-id");
    if (refreshInput) refreshInput.value = rid;

    const link = getRunlogLink(rid);
    toast(qs("#formMessages"), link ? [`Loaded ${rid}`, link] : `Loaded ${rid}`, "ok");
  });

  // Validate
  qs("#validateBtn").addEventListener("click", async ()=>{
    clear(qs("#formMessages"));
    const payload = collectForm();
    const v = await validatePayload(getVerb(), payload).catch(e=>({ok:false, errors:[String(e)]}));
    if (v.ok) toast(qs("#formMessages"), "Valid ✓", "ok");
    else (v.errors||[]).forEach(e=>toast(qs("#formMessages"), e, "error"));
  });

  // Save (create/update)
  qs("#saveBtn").addEventListener("click", async ()=>{
    clear(qs("#formMessages"));
    const payload = collectForm();
    const mode = getMode();
    const runId = state.currentRunId;

    try {
      // Decide whether to update or create
      if (mode === "edit" && runId) {
        dlog("[saveBtn] edit mode detected", { runId });
        const r = await updateRun(getVerb(), runId, payload);
        if (r.ok) {
          const link = getRunlogLink(r.id);
          toast(qs("#formMessages"), link ? [`Updated ${r.id}`, link] : `Updated ${r.id}`, "ok");
        } else (r.errors || []).forEach(e => toast(qs("#formMessages"), e, "error"));
      } else {
        dlog("[saveBtn] create mode or no runId; performing create");
        const r = await createRun(getVerb(), payload);
        if (r.ok) {
          state.currentRunId = r.id; // remember newly created id
          if (mode === "edit") await refreshRunSelect();
          const refreshInput = qs("#refresh-run-id");
          if (refreshInput && r.id) refreshInput.value = r.id;

          const link = getRunlogLink(r.id);
          toast(qs("#formMessages"), link ? [`Created ${r.id}`, link] : `Created ${r.id}`, "ok");
        } else {
          (r.errors || []).forEach(e => toast(qs("#formMessages"), e, "error"));
        }
      }
    } catch (e) {
      console.error(e);
      toast(qs("#formMessages"), "Save failed", "error");
    }
  });

  // Wire the Update Status button + Enter key
  const refreshBtn = qs("#refresh-status-btn");
  const refreshInput = qs("#refresh-run-id");
  if (refreshBtn) refreshBtn.addEventListener("click", refreshRunStatus);
  if (refreshInput) refreshInput.addEventListener("keydown", (e)=>{
    if (e.key === "Enter") refreshRunStatus();
  });

  // initial refresh-block state
  await syncRefreshBlock();
}

window.addEventListener("DOMContentLoaded", init);
