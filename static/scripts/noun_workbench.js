// Debug control - set to false to disable all grid debug logging
const DEBUG_ENABLED = false; // Change to true to enable debug logs
// Debug helper that respects the flag
const debug = DEBUG_ENABLED ? console.debug.bind(console) : () => {};

// -----------------------------
// Utilities
// -----------------------------
const qs  = (s, el=document) => el.querySelector(s);
const qsa = (s, el=document) => Array.from(el.querySelectorAll(s));

function h(tag, attrs = {}, children = []) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") el.className = v;
    else if (k === "html") el.innerHTML = v;
    else if (v !== undefined && v !== null) el.setAttribute(k, v);
  }
  (Array.isArray(children) ? children : [children]).forEach(c => {
    if (c == null) return;
    if (typeof c === "string") el.appendChild(document.createTextNode(c));
    else el.appendChild(c);
  });
  return el;
}

function toast(container, text, type = "ok") {
  const node = h("div", { class: `msg ${type}` }, text);
  container.appendChild(node);
  return node;
}

function clear(container) { container.innerHTML = ""; }

function asJSON(resp) {
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

function getSelectedNounType() {
  return qs("#nounTypeSelect").value;
}

function getFormMode() {
  return qs('input[name="form-mode"]:checked').value;
}
function getBulkMode() {
  return qs('input[name="bulk-mode"]:checked').value;
}

// -----------------------------
// Global state (kept small on purpose)
// -----------------------------
const state = {
  schema: null,                 // current noun schema
  autogenPrimary: null,         // { fieldName, enabled }
  referenceCache: new Map(),    // key: `${project}:${nounType}:${fieldName}` -> options[]
  preview: null,                // last preview result
  currentInstanceId: null,      // when editing
  existingIds: new Set(),       // lowercase of existing primary IDs (for action detect)
  existingById: new Map()       // lowercase primary ID -> full existing record
};

// -----------------------------
// Project selection
// -----------------------------
const projectSelect = document.getElementById("project-select");
const nounTypeSelect = document.getElementById("nounTypeSelect");

async function fetchProjects() {
  debug("[fetchProjects] start");
  const res = await fetch("/api/noun_workbench/projects");
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

// Initialize project select with available projects (auto-select first & trigger normal change flow)
async function initProjectSelect() {
  try {
    const projects = await fetchProjects();
    projectSelect.innerHTML = "";

    if (!projects.length) {
      const none = document.createElement("option");
      none.value = "";
      none.textContent = "No projects found";
      none.selected = true;
      projectSelect.appendChild(none);
      nounTypeSelect.innerHTML = "<option value=''>No projects</option>";
      nounTypeSelect.disabled = true;
      return;
    }

    // Build options; default-select the first
    projects.forEach((p, i) => {
      const opt = document.createElement("option");
      opt.value = p;
      opt.textContent = p;
      if (i === 0) opt.selected = true;
      projectSelect.appendChild(opt);
    });

    // Respect URL ?project= & ?noun=
    const url = new URL(location.href);
    const presetProject = url.searchParams.get("project");
    const presetNoun    = url.searchParams.get("noun");

    if (presetProject && projects.includes(presetProject)) {
      projectSelect.value = presetProject;
      if (typeof projectSelect.onchange === "function") {
        await projectSelect.onchange();
      } else {
        await loadNounTypesForProject(presetProject);
      }

      if (presetNoun) {
        const values = Array.from(nounTypeSelect.options).map(o => o.value);
        if (values.includes(presetNoun)) {
          nounTypeSelect.value = presetNoun;
          await reloadSchemaAndForm();
        }
      }
      return;
    }

    // No preset → auto-select the first and trigger normal change flow
    projectSelect.value = projects[0];
    if (typeof projectSelect.onchange === "function") {
      await projectSelect.onchange();
    } else {
      await loadNounTypesForProject(projects[0]);
    }
  } catch (err) {
    console.error("Failed to load projects:", err);
    projectSelect.innerHTML = "<option value=''>Error loading projects</option>";
    nounTypeSelect.innerHTML = "<option value=''>Select a project first</option>";
    nounTypeSelect.disabled = true;
  }
}

async function loadNounTypesForProject(project) {
  if (!project) {
    nounTypeSelect.innerHTML = "<option value=''>Select a project first</option>";
    nounTypeSelect.disabled = true;
    return;
  }

  nounTypeSelect.disabled = false;
  nounTypeSelect.innerHTML = "<option value=''>Loading noun types...</option>";
  try {
    const nounTypes = await fetch(`/api/noun_workbench/${encodeURIComponent(project)}`).then(asJSON);
    nounTypeSelect.innerHTML = "";
    nounTypes.forEach(nt => {
      const opt = document.createElement("option");
      opt.value = nt;
      opt.textContent = nt;
      nounTypeSelect.appendChild(opt);
    });
    debug("[nounTypes] loaded", nounTypes);
  } catch (err) {
    console.error("Error loading noun types:", err);
    nounTypeSelect.innerHTML = "<option value=''>Error loading noun types</option>";
    nounTypeSelect.disabled = true;
  }
}

projectSelect.onchange = async () => {
  const project = projectSelect.value;
  await loadNounTypesForProject(project);
  // Clear state and form when project changes
  state.schema = null;
  state.referenceCache.clear();
  state.preview = null;
  state.currentInstanceId = null;
  clear(qs("#dynamicForm"));
  clear(qs("#formMessages"));
  clear(qs("#bulkWarnings"));
  clear(qs("#previewArea"));
};

// -----------------------------
// Backend fetchers (project in the path)
// -----------------------------
async function loadInstanceOptions() {
  const project = projectSelect.value;
  const nounType = nounTypeSelect.value;
  const select = document.getElementById("editInstanceSelect");

  if (!project || !nounType) {
    select.innerHTML = "<option value=''>Select a noun type first</option>";
    select.disabled = true;
    return;
  }

  try {
    const items = await fetch(
      `/api/noun_workbench/${encodeURIComponent(project)}/${encodeURIComponent(nounType)}/items`
    ).then(asJSON);

    select.innerHTML = "";

    if (items.length === 0) {
      select.innerHTML = "<option value=''>No instances found</option>";
      select.disabled = true;
      return;
    }

    const defaultOption = document.createElement("option");
    defaultOption.value = "";
    defaultOption.textContent = "Select an instance";
    select.appendChild(defaultOption);

    items.forEach(it => {
      const keys = Object.keys(it);
      const pid = it[keys[0]] || "(unknown)";
      const label = keys.includes("name") ? `${pid} — ${it["name"]}` : pid;

      const opt = document.createElement("option");
      opt.value = pid;
      opt.textContent = label;
      select.appendChild(opt);
    });

    select.disabled = false;
  } catch (err) {
    console.error("Failed to load instances:", err);
    select.innerHTML = "<option value=''>Error loading instances</option>";
    select.disabled = true;
  }
}

async function fetchNounTypes() {
  debug("[fetchNounTypes] start");
  const project = projectSelect.value;
  if (!project) throw new Error("No project selected");
  const data = await fetch(`/api/noun_workbench/${encodeURIComponent(project)}`).then(asJSON);
  debug("[fetchNounTypes] got", data);
  return data;
}

async function fetchSchema(nounType) {
  debug("[fetchSchema]", nounType);
  if (!nounType) {
    debug("[fetchSchema] no noun type provided");
    return null;
  }
  const project = projectSelect.value;
  if (!project) throw new Error("No project selected");
  const data = await fetch(`/api/noun_workbench/${encodeURIComponent(project)}/${encodeURIComponent(nounType)}/schema`).then(asJSON);
  debug("[fetchSchema] schema keys:", Object.keys(data || {}));
  return data;
}

async function fetchReferenceOptions(nounType, fieldName) {
  const project = projectSelect.value;
  if (!project) throw new Error("No project selected");

  const cacheKey = `${project}:${nounType}:${fieldName}`;
  if (state.referenceCache.has(cacheKey)) {
    debug("[fetchReferenceOptions] cache hit", cacheKey);
    return state.referenceCache.get(cacheKey);
  }
  debug("[fetchReferenceOptions] query", cacheKey);
  const data = await fetch(
    `/api/noun_workbench/${encodeURIComponent(project)}/${encodeURIComponent(nounType)}/references/${encodeURIComponent(fieldName)}`
  ).then(asJSON);
  state.referenceCache.set(cacheKey, data || []);
  return data || [];
}

async function validateSingle(nounType, payload) {
  const project = projectSelect.value;
  if (!project) throw new Error("No project selected");
  debug("[validateSingle] payload", payload);
  const resp = await fetch(`/api/noun_workbench/${encodeURIComponent(project)}/${encodeURIComponent(nounType)}/validate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  }).then(asJSON);
  debug("[validateSingle] resp", resp);
  return resp;
}

async function createSingle(nounType, payload) {
  const project = projectSelect.value;
  if (!project) throw new Error("No project selected");
  debug("[createSingle] payload", payload);
  return fetch(`/api/noun_workbench/${encodeURIComponent(project)}/${encodeURIComponent(nounType)}/create`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  }).then(asJSON);
}

async function loadInstance(nounType, id) {
  const project = projectSelect.value;
  if (!project) throw new Error("No project selected");
  debug("[loadInstance]", nounType, id);
  return fetch(`/api/noun_workbench/${encodeURIComponent(project)}/${encodeURIComponent(nounType)}/instance/${encodeURIComponent(id)}`).then(asJSON);
}

async function updateInstance(nounType, id, payload) {
  const project = projectSelect.value;
  if (!project) throw new Error("No project selected");
  debug("[updateInstance]", nounType, id, payload);
  return fetch(`/api/noun_workbench/${encodeURIComponent(project)}/${encodeURIComponent(nounType)}/update/${encodeURIComponent(id)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  }).then(asJSON);
}

async function bulkPreview(nounType, file) {
  const project = projectSelect.value;
  if (!project) throw new Error("No project selected");
  const mode = getBulkMode?.() || "upsert";
  const fd = new FormData();
  fd.append("file", file);
  debug("[bulkPreview] file", file?.name, file?.size, "mode", mode);
  return fetch(`/api/noun_workbench/${encodeURIComponent(project)}/${encodeURIComponent(nounType)}/bulk_preview?mode=${encodeURIComponent(mode)}`, {
    method: "POST",
    body: fd
  }).then(asJSON);
}

async function bulkCommit(nounType, rows) {
  const project = projectSelect.value;
  if (!project) throw new Error("No project selected");
  const mode = getBulkMode?.() || "upsert";
  debug("[bulkCommit] rows", rows?.length, "mode", mode);
  return fetch(`/api/noun_workbench/${encodeURIComponent(project)}/${encodeURIComponent(nounType)}/bulk_commit?mode=${encodeURIComponent(mode)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rows })
  }).then(asJSON);
}

// -----------------------------
// Form rendering (schema-driven)
// -----------------------------
function computeAutogen(schema) {
  const primary = schema?.primary_id_field;
  const enabled = !!schema?.autogenerate_id;
  return { fieldName: primary || null, enabled };
}

async function renderForm(schema, existing = null) {
  debug("[renderForm] existing?", !!existing);
  state.schema = schema;
  state.autogenPrimary = computeAutogen(schema);

  const form = qs("#dynamicForm");
  clear(form);

  const fields = schema?.fields || {};
  const nounType = getSelectedNounType();

  for (const [fieldName, info] of Object.entries(fields)) {
    const required = !!info.required;
    const type = info.type;

    const field = h("div", { class: "form-field" });
    const label = h("label", {}, [
      fieldName,
      required ? h("span", { class: "required-badge" }, "required") : null
    ]);
    field.appendChild(label);

    if (state.autogenPrimary.enabled && fieldName === state.autogenPrimary.fieldName) {
      const input = h("input", { class: "input", placeholder: "Will be autogenerated", disabled: "true" });
      input.dataset.name = fieldName;
      input.value = existing?.[fieldName] || "";
      field.appendChild(input);
      form.appendChild(field);
      continue;
    }

    if (type === "adjective") {
      const sel = h("select", {});
      sel.dataset.name = fieldName;
      sel.appendChild(h("option", { value: "" }, required ? "Select…" : "— (optional) —"));

      try {
        const opts = await fetchReferenceOptions(nounType, fieldName);
        for (const opt of opts) {
          const o = h("option", { value: opt.value }, opt.label ?? opt.value);
          if (existing && existing[fieldName] === opt.value) o.selected = true;
          sel.appendChild(o);
        }
      } catch (e) {
        debug("[renderForm] reference fetch failed", fieldName, e);
        toast(qs("#formMessages"), `Failed to load options for ${fieldName}`, "warn");
      }

      field.appendChild(sel);
      form.appendChild(field);
      continue;
    }

    let control;
    switch (type) {
      case "date":
        control = h("input", { type: "date", class: "input" });
        break;
      case "text":
      case "string":
      default:
        control = h("input", { type: "text", class: "input" });
    }
    control.dataset.name = fieldName;
    if (existing && existing[fieldName] != null) control.value = existing[fieldName];
    if (required) control.required = true;

    field.appendChild(control);
    form.appendChild(field);
  }

  if (schema?.notes) {
    toast(qs("#formMessages"), schema.notes, "warn");
  }
}

function collectFormData() {
  const form = qs("#dynamicForm");
  const payload = {};
  qsa("[data-name]", form).forEach(el => {
    const k = el.dataset.name;
    const v = el.value?.trim?.() ?? "";
    if (el.disabled && getFormMode() === "create") {
      payload[k] = "";
    } else {
      payload[k] = v;
    }
  });
  debug("[collectFormData]", payload);
  return payload;
}

// -----------------------------
// Bulk preview table
// -----------------------------
function renderPreviewTable(preview) {
  const wrap = qs("#previewArea");
  const warnings = qs("#bulkWarnings");
  clear(wrap);
  clear(warnings);

  state.preview = preview;

  if (preview?.warnings?.length) {
    preview.warnings.forEach(w => toast(warnings, w, "warn"));
  }

  const table = h("table");
  const thead = h("thead");
  const tbody = h("tbody");
  table.appendChild(thead);
  table.appendChild(tbody);

  let columns = [];
  if (preview.valid?.length) {
    columns = Object.keys(preview.valid[0].payload || {});
  } else if (preview.invalid?.length) {
    columns = Array.from(preview.invalid.reduce((set, r) => {
      Object.keys(r.payload || {}).forEach(k => set.add(k));
      return set;
    }, new Set()));
  }

  const pidKey = state.schema?.primary_id_field;

  const headerRow = h("tr");
  headerRow.appendChild(h("th", {}, "#"));
  headerRow.appendChild(h("th", {}, "Action"));
  columns.forEach(c => headerRow.appendChild(h("th", {}, c)));
  headerRow.appendChild(h("th", {}, "Status"));
  thead.appendChild(headerRow);

  const rows = new Map();
  (preview.valid || []).forEach(r => rows.set(r.rowIndex, { ...r, ok: true }));
  (preview.invalid || []).forEach(r => rows.set(r.rowIndex, { ...rows.get(r.rowIndex), ...r, ok: false }));

  const sorted = Array.from(rows.values()).sort((a, b) => a.rowIndex - b.rowIndex);

  for (const row of sorted) {
    const tr = h("tr", { class: row.ok ? "valid" : "invalid" });
    tr.appendChild(h("td", {}, String(row.rowIndex)));

    const pidValLc = String(row.payload?.[pidKey] ?? "").trim().toLowerCase();
    const isUpdate = pidValLc && state.existingIds?.has(pidValLc);
    tr.classList.add(isUpdate ? "action-update" : "action-insert");
    tr.appendChild(h("td", {}, isUpdate ? "Update" : "Insert"));

    const existing = isUpdate ? (state.existingById.get(pidValLc) || {}) : null;
    for (const col of columns) {
      const newVal = String(row.payload?.[col] ?? "");
      if (isUpdate && col !== pidKey) {
        const oldVal = String(existing?.[col] ?? "");
        if (oldVal !== newVal) {
          const cell = h("td", { class: "diff changed" }, [
            h("span", { class: "old" }, oldVal),
            " \u2192 ",
            h("span", { class: "new" }, newVal)
          ]);
          tr.appendChild(cell);
          continue;
        }
      }
      tr.appendChild(h("td", {}, newVal));
    }

    const statusCell = h("td");
    if (row.ok) {
      statusCell.appendChild(h("span", {}, "✓ OK"));
    } else {
      const errs = (row.errors || []).map(e => `• ${e}`).join("\n");
      statusCell.appendChild(h("pre", {}, errs || "Invalid"));
    }
    tr.appendChild(statusCell);
    tbody.appendChild(tr);
  }

  wrap.appendChild(table);
  qs("#commitBtn").disabled = !(preview.valid && preview.valid.length > 0);
}

// -----------------------------
// Page wiring
// -----------------------------
async function reloadSchemaAndForm() {
  clear(qs("#formMessages"));
  state.referenceCache.clear();
  const nt = getSelectedNounType();
  if (!nt) return;
  const schema = await fetchSchema(nt).catch(e => {
    console.error(e);
    toast(qs("#formMessages"), "Failed to load schema", "error");
    return null;
  });
  if (!schema) return;
  await renderForm(schema, null);
  state.currentInstanceId = null;
}

async function init() {
  debug("[init] start");

  // Prepare selects
  await initProjectSelect();

  // Manual form mode toggle (create/edit)
  qsa('input[name="form-mode"]').forEach(radio => {
    radio.addEventListener("change", () => {
      const isEdit = getFormMode() === "edit";
      qs("#editLoadArea").classList.toggle("hidden", !isEdit);
    });
  });

  // Noun change / reload
  nounTypeSelect.addEventListener("change", reloadSchemaAndForm);
  qs("#reloadSchemaBtn").addEventListener("click", reloadSchemaAndForm);

  // File input change
  document.getElementById("fileInput").addEventListener("change", (e) => {
    const fileName = e.target.files.length ? e.target.files[0].name : "No file selected";
    document.getElementById("fileName").textContent = fileName;
  });

  // Edit-load button
  qs("#loadInstanceBtn").addEventListener("click", async () => {
    clear(qs("#formMessages"));
    const nt = getSelectedNounType();
    const sel = qs("#editInstanceSelect");
    if (!sel) {
      console.error("editInstanceSelect not found in DOM");
      return;
    }
    const id = sel.value;
    if (!id) return toast(qs("#formMessages"), "Select an instance to load.", "warn");

    try {
      const inst = await loadInstance(nt, id);
      if (!inst || Object.keys(inst).length === 0) {
        toast(qs("#formMessages"), `No instance found for ID ${id}`, "warn");
        return;
      }
      await renderForm(state.schema || await fetchSchema(nt), inst);
      state.currentInstanceId = id;
      toast(qs("#formMessages"), `Loaded ${id}`, "ok");
    } catch (e) {
      console.error(e);
      toast(qs("#formMessages"), "Failed to load instance", "error");
    }
  });

  // Validate button
  qs("#validateBtn").addEventListener("click", async (e) => {
    e.preventDefault(); e.stopPropagation();
    clear(qs("#formMessages"));
    const nt = getSelectedNounType();
    if (!nt) return toast(qs("#formMessages"), "Select a noun type first.", "warn");
    const payload = collectFormData();

    if (state.autogenPrimary?.enabled && getFormMode() === "create") {
      const f = state.autogenPrimary.fieldName;
      if (f && payload[f] && payload[f].trim()) {
        return toast(qs("#formMessages"), `Field '${f}' must be blank (autogenerated).`, "error");
      }
    }

    try {
      const res = await validateSingle(nt, payload);
      if (res.ok) toast(qs("#formMessages"), "Valid ✓", "ok");
      else (res.errors || []).forEach(e => toast(qs("#formMessages"), e, "error"));
    } catch (e) {
      console.error(e);
      toast(qs("#formMessages"), "Validation failed (server error).", "error");
    }
  });

  // Save button (create or update)
  qs("#saveBtn").addEventListener("click", async () => {
    clear(qs("#formMessages"));
    const nt = getSelectedNounType();
    if (!nt) return toast(qs("#formMessages"), "Select a noun type first.", "warn");
    const payload = collectFormData();

    if (state.autogenPrimary?.enabled && getFormMode() === "create") {
      const f = state.autogenPrimary.fieldName;
      if (f && payload[f] && payload[f].trim()) {
        return toast(qs("#formMessages"), `Field '${f}' must be blank (autogenerated).`, "error");
      }
    }

    try {
      if (getFormMode() === "edit" && state.currentInstanceId) {
        const r = await updateInstance(nt, state.currentInstanceId, payload);
        if (r.ok) toast(qs("#formMessages"), `Updated ${state.currentInstanceId} ✓`, "ok");
        else (r.errors || []).forEach(e => toast(qs("#formMessages"), e, "error"));
      } else {
        const r = await createSingle(nt, payload);
        if (r.ok) toast(qs("#formMessages"), `Created ✓ ${r.id || ""}`, "ok");
        else (r.errors || []).forEach(e => toast(qs("#formMessages"), e, "error"));
      }
    } catch (e) {
      console.error(e);
      toast(qs("#formMessages"), "Save failed (server error).", "error");
    }
  });

  // When project or noun changes, reload instance options
  nounTypeSelect.addEventListener("change", () => {
    loadInstanceOptions();
  });
  projectSelect.addEventListener("change", () => {
    loadInstanceOptions();
  });

  // Bulk preview
  qs("#previewBtn").addEventListener("click", async () => {
    clear(qs("#bulkWarnings"));
    clear(qs("#previewArea"));
    qs("#commitBtn").disabled = true;

    const nt = getSelectedNounType();
    if (!nt) return toast(qs("#bulkWarnings"), "Select a noun type first.", "warn");

    const file = qs("#fileInput").files?.[0];
    if (!file) {
      toast(qs("#bulkWarnings"), "Choose a CSV or XLSX file first.", "warn");
      return;
    }

    try {
      // fetch existing items (for action + diffs)
      const items = await fetch(
        `/api/noun_workbench/${encodeURIComponent(projectSelect.value)}/${encodeURIComponent(nt)}/items`
      ).then(asJSON);

      const pidKey = state.schema?.primary_id_field;
      state.existingIds = new Set(
        items.map(it => String(it?.[pidKey] ?? "").trim().toLowerCase())
      );
      state.existingById = new Map(
        items.map(it => [String(it?.[pidKey] ?? "").trim().toLowerCase(), it])
      );

      const preview = await bulkPreview(nt, file);
      renderPreviewTable(preview);
    } catch (e) {
      console.error(e);
      toast(qs("#bulkWarnings"), "Preview failed (server error).", "error");
    }
  });

  // Bulk commit
  qs("#commitBtn").addEventListener("click", async () => {
    clear(qs("#bulkWarnings"));
    if (!state.preview || !(state.preview.valid?.length)) {
      toast(qs("#bulkWarnings"), "No valid rows to commit.", "warn");
      return;
    }
    try {
      const nt = getSelectedNounType();
      const rows = state.preview.valid.map(v => v.payload);
      const res = await bulkCommit(nt, rows);
      if (res.errors?.length) res.errors.forEach(e => toast(qs("#bulkWarnings"), e, "error"));
      toast(qs("#bulkWarnings"), `Inserted: ${res.inserted || 0}, Updated: ${res.updated || 0}, Skipped: ${res.skipped || 0}`, "ok");
      qs("#commitBtn").disabled = true;
    } catch (e) {
      console.error(e);
      toast(qs("#bulkWarnings"), "Commit failed (server error).", "error");
    }
  });
}

window.addEventListener("DOMContentLoaded", init);
