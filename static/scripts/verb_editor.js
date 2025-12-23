// ---------- Helpers ----------
async function fetchJSON(url, options = {}) {
  const res = await fetch(url, options);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

function makeListItem(value, arr, idx, listId) {
  const li = document.createElement("li");
  const row = document.createElement("div");
  row.className = "row";

  const input = document.createElement("input");
  input.type = "text";
  input.value = value;
  input.oninput = e => (arr[idx] = e.target.value);
  row.appendChild(input);

  const del = document.createElement("button");
  del.textContent = "🗑";
  del.onclick = () => { arr.splice(idx, 1); renderList(arr, listId); };
  li.appendChild(row);
  li.appendChild(del);
  return li;
}

function renderList(arr, listId) {
  const ul = document.getElementById(listId);
  ul.innerHTML = "";
  arr.forEach((val, idx) => ul.appendChild(makeListItem(val, arr, idx, listId)));
}

function unloadVerbUI() {
  document.getElementById("verb-viewer").classList.add("hidden");
  document.getElementById("verb-editor").classList.add("hidden");
  ["view-name","view-description","view-group","view-raw","view-noun","view-interpret"].forEach(id=>{
    const el=document.getElementById(id); if (el) el.textContent="";
  });
  document.getElementById("view-status-mode").textContent="";
  document.getElementById("view-linear-steps").innerHTML="";
  ["instructions-list","raw-inputs-list","interp-tabs-list","parsers-list","linear-steps-list"].forEach(id=>{
    const el=document.getElementById(id); if (el) el.innerHTML="";
  });
}

// ---------- loading ------------
async function loadValidNounRefs(project) {
  const res = await fetch(`/noun/valid-refs/${project}`);
  if (!res.ok) return;
  const data = await res.json();
  const select = document.getElementById("noun-ref");
  select.innerHTML = "";
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "-- Select a Noun Type --";
  select.appendChild(placeholder);
  data.valid_noun_types.forEach(name => {
    const opt = document.createElement("option");
    opt.value = name; opt.textContent = name;
    select.appendChild(opt);
  });
}

// ---------- Log Schema ----------
let logSchema = { primary_id: null, fields: {} };

async function loadLogSchema() {
  const project = document.getElementById("project").value;
  let group = document.getElementById("verb-group-select").value;
  if (!group) group = document.getElementById("verb-group-custom").value.trim();
  if (!project || !group) return;
  logSchema = await fetchJSON(`/verb/log-schema/${project}/${group}`);
  renderLogSchema();
}

function renderLogSchema() {
  const body = document.getElementById("log-schema-body");
  const primarySelect = document.getElementById("primary-id");
  body.innerHTML = "";
  primarySelect.innerHTML = "";

  Object.entries(logSchema.fields || {}).forEach(([field, cfg]) => {
    const tr = document.createElement("tr");

    const nameTd = document.createElement("td");
    const nameInput = document.createElement("input");
    nameInput.type = "text"; nameInput.value = field;
    nameInput.oninput = e => {
      const newName = e.target.value;
      if (newName && newName !== field) {
        logSchema.fields[newName] = logSchema.fields[field];
        delete logSchema.fields[field];
        renderLogSchema();
      }
    };
    nameTd.appendChild(nameInput); tr.appendChild(nameTd);

    const typeTd = document.createElement("td");
    const typeSelect = document.createElement("select");
    ["string","int","float","date"].forEach(opt=>{
      const o=document.createElement("option");
      o.value=opt; o.textContent=opt; if(cfg.type===opt) o.selected=true;
      typeSelect.appendChild(o);
    });
    typeSelect.onchange = e => (cfg.type = e.target.value);
    typeTd.appendChild(typeSelect); tr.appendChild(typeTd);

    const reqTd = document.createElement("td");
    const reqCb = document.createElement("input");
    reqCb.type = "checkbox"; reqCb.checked = cfg.required || false;
    reqCb.onchange = e => (cfg.required = e.target.checked);
    reqTd.appendChild(reqCb); tr.appendChild(reqTd);

    const delTd = document.createElement("td");
    const delBtn = document.createElement("button");
    delBtn.textContent = "🗑";
    delBtn.onclick = () => { delete logSchema.fields[field]; if (logSchema.primary_id === field) logSchema.primary_id = null; renderLogSchema(); };
    delTd.appendChild(delBtn); tr.appendChild(delTd);

    body.appendChild(tr);
  });

  Object.keys(logSchema.fields || {}).forEach(f=>{
    const opt=document.createElement("option"); opt.value=f; opt.textContent=f;
    if (f===logSchema.primary_id) opt.selected=true;
    primarySelect.appendChild(opt);
  });
  primarySelect.onchange = e => (logSchema.primary_id = e.target.value);
}

// ---------- State ----------
let instructions = [];
let rawInputs = [];
let interpTabs = [];
let parsers = [];
let verbGroups = [];
let statusMode = "buckets"; // "buckets" | "linear"
let linearSteps = [];       // [{type: 'data_entry'|'raw_upload'|'interpretation'|'gate', source?: string, required?: bool, manual_complete?: bool}]

// ---------- Project + Verbs ----------
async function loadProjects() {
  const projects = await fetchJSON("/verb/projects");
  const select = document.getElementById("project");
  select.innerHTML = "";
  projects.forEach(p=>{
    const opt=document.createElement("option"); opt.value=p; opt.textContent=p; select.appendChild(opt);
  });
  if (projects.length) {
    select.value = projects[0];
    await loadVerbs();
  }
}

async function loadVerbs() {
  unloadVerbUI();
  const project = document.getElementById("project").value;
  const verbs = await fetchJSON(`/verb/${project}`);
  const select = document.getElementById("verb-select");
  select.innerHTML = "";
  Object.keys(verbs).forEach(v=>{
    const opt=document.createElement("option"); opt.value=v; opt.textContent=v; select.appendChild(opt);
  });
  await loadVerbGroups();
}

async function loadVerbGroups() {
  const project = document.getElementById("project").value;
  if (!project) return;
  const verbs = await fetchJSON(`/verb/${project}`);
  const groups = new Set();
  Object.values(verbs).forEach(v=>{ if (v.verb_group) groups.add(v.verb_group); });
  verbGroups = Array.from(groups).sort();

  const select = document.getElementById("verb-group-select");
  select.innerHTML = "";
  verbGroups.forEach(g=>{
    const opt=document.createElement("option"); opt.value=g; opt.textContent=g; select.appendChild(opt);
  });
}

// ---------- Linear Status Helpers ----------
function ensureLinearStepIds() {
  linearSteps.forEach((step, idx) => {
    if (!step.id || !String(step.id).trim()) {
      const base = `${step.type}_${(step.source || "step"+(idx+1)).toString().toLowerCase().replace(/[^a-z0-9]+/g,"-")}`;
      step.id = base;
    }
  });
}

function buildStepSelect(step) {
  const sel = document.createElement("select");

  // Static options
  const optDE = document.createElement("option");
  optDE.value = "type:data_entry";
  optDE.textContent = "Data Entry";
  sel.appendChild(optDE);

  const optGate = document.createElement("option");
  optGate.value = "type:gate";
  optGate.textContent = "Gate (approval)";
  sel.appendChild(optGate);

  // Raw Inputs
  rawInputs.filter(Boolean).forEach(r => {
    const o = document.createElement("option");
    o.value = "raw::" + r;
    o.textContent = `Raw – ${r}`;
    sel.appendChild(o);
  });

  // Interpretation Tabs
  interpTabs.filter(Boolean).forEach(t => {
    const o = document.createElement("option");
    o.value = "interp::" + t;
    o.textContent = `Interpretation – ${t}`;
    sel.appendChild(o);
  });

  // Adverbs
  (window.adverbKeys || []).filter(Boolean).forEach(a => {
    const o = document.createElement("option");
    o.value = "adverb::" + a;
    o.textContent = `Adverb – ${a}`;
    sel.appendChild(o);
  });

  // Set currently selected value
  let v = "type:data_entry";
  if (step.type === "gate") v = "type:gate";
  if (step.type === "raw_upload" && step.source) v = "raw::" + step.source;
  if (step.type === "interpretation" && step.source) v = "interp::" + step.source;
  if (step.type === "adverb" && step.source) v = "adverb::" + step.source;
  sel.value = v;

  sel.onchange = e => {
    const value = e.target.value;
    if (value.startsWith("type:")) {
      const t = value.split(":")[1];
      step.type = t === "data_entry" ? "data_entry" : "gate";
      step.source = null;
      // Gate cannot be manually completed
      if (step.type === "gate") step.manual_complete = false;
      renderLinearSteps(); // refresh to hide/show manual toggle
    } else if (value.startsWith("raw::")) {
      step.type = "raw_upload";
      step.source = value.slice(5);
    } else if (value.startsWith("interp::")) {
      step.type = "interpretation";
      step.source = value.slice(8);
    } else if (value.startsWith("adverb::")) {
      step.type = "adverb";
      step.source = value.slice(8);
    }   
  };
  return sel;
}

function renderLinearSteps() {
  const ul = document.getElementById("linear-steps-list");
  ul.innerHTML = "";

  linearSteps.forEach((step, idx) => {
    const li = document.createElement("li");
    li.className = "step-row";
    li.draggable = true;

    // add a little drag handle bar
    const handle = document.createElement("span");
    handle.textContent = "⋮⋮"; // vertical dots as a grip
    handle.className = "drag-handle";
    li.appendChild(handle);

    // drag & drop events
    li.addEventListener("dragstart", e => {
      e.dataTransfer.setData("text/plain", idx.toString());
      li.classList.add("dragging");
    });
    li.addEventListener("dragend", () => {
      li.classList.remove("dragging");
    });
    li.addEventListener("dragover", e => {
      e.preventDefault();
      li.classList.add("drag-over");
    });

    li.addEventListener("dragleave", () => {
      li.classList.remove("drag-over");
    });

    li.addEventListener("drop", e => {
      e.preventDefault();
      li.classList.remove("drag-over");

      const from = parseInt(e.dataTransfer.getData("text/plain"), 10);
      const ul = document.getElementById("linear-steps-list");
      const to = Array.from(ul.children).indexOf(li);

      if (from >= 0 && to >= 0 && from !== to) {
        const moved = linearSteps.splice(from, 1)[0];
        linearSteps.splice(to, 0, moved);
        renderLinearSteps();
      }
    });

    // type/source selector
    const typeSel = buildStepSelect(step);
    li.appendChild(typeSel);

    // required
    const reqWrap = document.createElement("label");
    reqWrap.className = "small";
    const req = document.createElement("input");
    req.type = "checkbox"; req.checked = !!step.required;
    req.onchange = e => (step.required = e.target.checked);
    reqWrap.appendChild(req);
    reqWrap.appendChild(document.createTextNode(" Required"));
    li.appendChild(reqWrap);

    // manual completion (hidden for gates)
    const manWrap = document.createElement("label");
    manWrap.className = "small";
    const man = document.createElement("input");
    man.type = "checkbox";
    man.checked = !!step.manual_complete;
    man.onchange = e => (step.manual_complete = e.target.checked);
    manWrap.appendChild(man);
    manWrap.appendChild(document.createTextNode(" Manual"));
    if (step.type === "gate") {
      manWrap.style.visibility = "hidden"; // gate is auto-completed; no manual toggle
      man.checked = false;
      step.manual_complete = false;
    }
    li.appendChild(manWrap);

    // delete
    const del = document.createElement("button");
    del.textContent = "🗑";
    del.onclick = () => { linearSteps.splice(idx,1); renderLinearSteps(); };
    li.appendChild(del);

    ul.appendChild(li);
  });
}

// ---------- View + Edit ----------
async function loadVerb() {
  const project = document.getElementById("project").value;
  const verb = document.getElementById("verb-select").value;
  if (!verb) return;
  const data = await fetchJSON(`/verb/${project}/${encodeURIComponent(verb)}`);

  // VIEW
  document.getElementById("view-name").textContent = verb;
  document.getElementById("view-description").textContent = data.description || "";
  document.getElementById("view-group").textContent = data.verb_group || "";

  // status view
  const isLinear = !!(data.linear_status && data.linear_status.steps && data.linear_status.steps.length);
  document.getElementById("view-status-mode").textContent = isLinear ? "Linear" : "Buckets";
  document.getElementById("view-status-buckets").classList.toggle("hidden", isLinear);
  const linearView = document.getElementById("view-status-linear");
  linearView.classList.toggle("hidden", !isLinear);
  const stepsUl = document.getElementById("view-linear-steps");
  stepsUl.innerHTML = "";
  if (isLinear) {
    (data.linear_status.steps || []).forEach((s,i)=>{
      const li=document.createElement("li");
      const src = (s.type==="raw_upload"||s.type==="interpretation") && s.source ? ` • ${s.source}` : "";
      const manual = s.manual_complete ? " • manual" : "";
      li.textContent = `${i+1}. ${s.type}${src}${s.required ? " (required)" : ""}${manual}`;
      stepsUl.appendChild(li);
    });
  }

  // schema view
  const schema = data.data_entry_schema || {};
  const instrEl = document.getElementById("view-instructions");
  instrEl.innerHTML = "<span class='badge'>Instructions</span>";
  if (schema.instructions && schema.instructions.length) {
    schema.instructions.forEach(txt=>{
      const line=document.createElement("div"); line.textContent=txt; line.classList.add("child-line"); instrEl.appendChild(line);
    });
  } else {
    const none=document.createElement("div"); none.textContent="(none)"; instrEl.appendChild(none);
  }
  document.getElementById("view-raw").textContent = (schema.raw_data_inputs || []).join(", ");
  document.getElementById("view-noun").textContent = (schema.set_up_inputs || {}).noun_type_ref || "";
  document.getElementById("view-interpret").textContent = (schema.interpretation && schema.interpretation.tabs) ? schema.interpretation.tabs.join(", ") : "";

  // EDIT defaults
  document.getElementById("verb-name").value = verb;
  document.getElementById("description").value = data.description || "";
  document.getElementById("verb-group-select").value = data.verb_group || "";

  instructions = [...(schema.instructions || [])];
  rawInputs = [...(schema.raw_data_inputs || [])];
  interpTabs = [...((schema.interpretation || {}).tabs || [])];
  parsers = [...((schema.interpretation || {}).parsers || [])];
  window.adverbKeys = Object.keys(data.adverb_schema || {});

  await loadValidNounRefs(project);
  document.getElementById("noun-ref").value = (schema.set_up_inputs || {}).noun_type_ref || "";

  // status editor defaults
  if (isLinear) {
    statusMode = "linear";
    document.getElementById("status-mode").value = "linear";
    document.getElementById("buckets-section").classList.add("hidden");
    document.getElementById("linear-section").classList.remove("hidden");
    linearSteps = JSON.parse(JSON.stringify(data.linear_status.steps || [])).map(s => ({ manual_complete:false, ...s }));
  } else {
    statusMode = "buckets";
    document.getElementById("status-mode").value = "buckets";
    document.getElementById("buckets-section").classList.remove("hidden");
    document.getElementById("linear-section").classList.add("hidden");
    linearSteps = [];
  }

  // render editors
  renderList(instructions, "instructions-list");
  renderList(rawInputs, "raw-inputs-list");
  renderList(interpTabs, "interp-tabs-list");
  renderList(parsers, "parsers-list");
  renderLinearSteps();

  // Show view mode by default
  document.getElementById("verb-viewer").classList.remove("hidden");
  document.getElementById("verb-editor").classList.add("hidden");
}

function enterEditMode() {
  document.getElementById("verb-viewer").classList.add("hidden");
  document.getElementById("verb-editor").classList.remove("hidden");
}

// ---------- Linear validation ----------
function validateLinearWorkflow() {
  const errors = [];
  if (!linearSteps.some(s => s.type === "data_entry"))
    errors.push("Linear workflow must include a Data Entry step.");

  const needRaw = new Set(rawInputs.filter(Boolean));
  const seenRaw = new Set(linearSteps.filter(s=>s.type==="raw_upload").map(s=>s.source));
  needRaw.forEach(r => { if (!seenRaw.has(r)) errors.push(`Missing step for Raw Input: ${r}`); });

  const needTabs = new Set(interpTabs.filter(Boolean));
  const seenTabs = new Set(linearSteps.filter(s=>s.type==="interpretation").map(s=>s.source));
  needTabs.forEach(t => { if (!seenTabs.has(t)) errors.push(`Missing step for Interpretation Tab: ${t}`); });

  ensureLinearStepIds();
  const ids = linearSteps.map(s=>String(s.id||"").trim());
  if (ids.some(id=>!id)) errors.push("One or more steps have blank ids.");
  const dupMap = ids.reduce((a,id)=>(a[id]=(a[id]||0)+1,a),{});
  const dups = Object.keys(dupMap).filter(k=>dupMap[k]>1);
  if (dups.length) errors.push("Duplicate step id(s): " + dups.join(", "));

  return errors;
}

// ---------- Save ----------
async function saveVerb() {
  const project = document.getElementById("project").value;

  let verb = document.getElementById("verb-select").value || document.getElementById("verb-name").value.trim();
  if (!verb) { alert("⚠️ Please enter a verb name"); return; }

  let group = document.getElementById("verb-group-select").value;
  const custom = document.getElementById("verb-group-custom").value.trim();
  if (custom) group = custom;
  if (!group) { alert("⚠️ Please select or enter a verb group"); return; }

  const nounRef = document.getElementById("noun-ref").value;
  if (!nounRef) { alert("⚠️ Please select a Noun Type Ref (required)"); return; }

  const payload = {
    description: document.getElementById("description").value,
    verb_group: group,
    data_entry_schema: {
      instructions,
      raw_data_inputs: rawInputs,
      set_up_inputs: { noun_type_ref: nounRef },
      interpretation: {
        tabs: interpTabs,
        parsers
      }
    }
  };

  if (statusMode === "buckets") {
    // Buckets mode: clear out any old linear workflow on the server
    payload.linear_status = { enabled: false, steps: [] };
    payload.status_values = [];
  } else {
    const errs = validateLinearWorkflow();
    if (errs.length) { alert("❌ Linear workflow invalid:\n- " + errs.join("\n- ")); return; }
    ensureLinearStepIds();
    payload.linear_status = {
      enabled: true,
      steps: linearSteps
    };
    payload.status_values = [];
  }

  const isNew = !document.getElementById("verb-select").value;
  await fetchJSON(`/verb/${project}/${encodeURIComponent(verb)}`, {
    method: isNew ? "POST" : "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });

  alert(`✅ Verb '${verb}' ${isNew ? "created" : "updated"} successfully`);
  await loadVerbs();
  document.getElementById("verb-select").value = verb;
  await loadVerb();
}

// ---------- New verb ----------
async function newVerb() {
  unloadVerbUI();
  const project = document.getElementById("project").value;

  document.getElementById("verb-viewer").classList.add("hidden");
  document.getElementById("verb-editor").classList.remove("hidden");

  document.getElementById("verb-name").value = "";
  document.getElementById("description").value = "";
  document.getElementById("verb-group-select").value = "";
  document.getElementById("verb-group-custom").value = "";

  instructions = [];
  rawInputs = [];
  interpTabs = [];
  parsers = [];
  statusMode = "buckets";
  linearSteps = [];

  await loadValidNounRefs(project);
  document.getElementById("noun-ref").value = "";

  renderList(instructions, "instructions-list");
  renderList(rawInputs, "raw-inputs-list");
  renderList(interpTabs, "interp-tabs-list");
  renderList(parsers, "parsers-list");
  renderLinearSteps();

  document.getElementById("verb-name").focus();
}

// ---------- Events ----------
document.getElementById("load-verb").addEventListener("click", loadVerb);
document.getElementById("new-verb").addEventListener("click", newVerb);
document.getElementById("edit-btn").addEventListener("click", enterEditMode);
document.getElementById("save-btn").addEventListener("click", saveVerb);
document.getElementById("cancel-btn").addEventListener("click", loadVerb);

// switching clears UI for clarity
document.getElementById("project").addEventListener("change", loadVerbs);
document.getElementById("verb-select").addEventListener("change", unloadVerbUI);

// Add buttons
document.getElementById("add-instruction").addEventListener("click", () => {
  instructions.push(""); renderList(instructions, "instructions-list");
});
document.getElementById("add-raw").addEventListener("click", () => {
  rawInputs.push(""); renderList(rawInputs, "raw-inputs-list");
});
document.getElementById("add-tab").addEventListener("click", () => {
  interpTabs.push(""); renderList(interpTabs, "interp-tabs-list");
});
document.getElementById("add-parser").addEventListener("click", () => {
  parsers.push(""); renderList(parsers, "parsers-list");
});

// Status mode switch
document.getElementById("status-mode").addEventListener("change", e => {
  statusMode = e.target.value;
  document.getElementById("buckets-section").classList.toggle("hidden", statusMode !== "buckets");
  document.getElementById("linear-section").classList.toggle("hidden", statusMode !== "linear");
  renderLinearSteps();
});

// Linear helpers
document.getElementById("add-step").addEventListener("click", () => {
  linearSteps.push({ type: "data_entry", source: null, required: false, manual_complete: false });
  renderLinearSteps();
});

// Log schema UI
function refreshPrimaryIdOptions() {
  const select = document.getElementById("primary-id");
  const body = document.getElementById("log-schema-body");
  select.innerHTML = "";
  const rows = body.querySelectorAll("tr");
  rows.forEach(row => {
    const nameInput = row.querySelector("td:first-child input");
    if (nameInput && nameInput.value.trim() !== "") {
      const opt = document.createElement("option");
      opt.value = nameInput.value.trim();
      opt.textContent = nameInput.value.trim();
      select.appendChild(opt);
    }
  });
}

document.getElementById("add-log-field").addEventListener("click", () => {
  const body = document.getElementById("log-schema-body");
  const row = document.createElement("tr");

  const nameTd = document.createElement("td");
  const nameInput = document.createElement("input");
  nameInput.type = "text";
  nameInput.addEventListener("input", refreshPrimaryIdOptions);
  nameTd.appendChild(nameInput);

  const typeTd = document.createElement("td");
  const typeSelect = document.createElement("select");
  ["string","number","float","boolean","date"].forEach(t=>{
    const opt=document.createElement("option"); opt.value=t; opt.textContent=t; typeSelect.appendChild(opt);
  });
  typeTd.appendChild(typeSelect);

  const reqTd = document.createElement("td");
  const reqCheck = document.createElement("input");
  reqCheck.type = "checkbox";
  reqTd.appendChild(reqCheck);

  const actionTd = document.createElement("td");
  const delBtn = document.createElement("button");
  delBtn.textContent = "🗑";
  delBtn.addEventListener("click", () => { row.remove(); refreshPrimaryIdOptions(); });
  actionTd.appendChild(delBtn);

  row.appendChild(nameTd); row.appendChild(typeTd); row.appendChild(reqTd); row.appendChild(actionTd);
  body.appendChild(row);
  refreshPrimaryIdOptions();
});

document.getElementById("edit-log-schema").addEventListener("click", async () => {
  const project = document.getElementById("project").value;
  let group = document.getElementById("verb-group-select").value || document.getElementById("verb-group-custom").value.trim();
  if (!project) return alert("Set Project first.");
  if (!group) return alert("Set Verb Group first.");
  try {
    logSchema = await fetchJSON(`/verb/log-schema/${project}/${group}`);
    renderLogSchema();
    document.getElementById("log-schema-card").classList.remove("hidden");
  } catch (err) {
    alert("❌ Failed to load log schema: " + err);
  }
});
document.getElementById("cancel-log-schema").addEventListener("click", () => {
  document.getElementById("log-schema-card").classList.add("hidden");
});
document.getElementById("save-log-schema").addEventListener("click", async () => {
  const project = document.getElementById("project").value;
  let group = document.getElementById("verb-group-select").value || document.getElementById("verb-group-custom").value.trim();
  if (!project || !group) return;

  const body = document.getElementById("log-schema-body");
  const rows = body.querySelectorAll("tr");

  const primaryId = document.getElementById("primary-id").value;
  const fields = {};
  let error = null;

  rows.forEach(row=>{
    const name = row.querySelector("td:first-child input").value.trim();
    const type = row.querySelector("td:nth-child(2) select").value;
    let required = row.querySelector("td:nth-child(3) input").checked;
    if (!name) return;
    if (name === primaryId) {
      required = true;
      if (type === "boolean" || type === "date") error = `❌ Primary ID '${name}' cannot be type '${type}'`;
    }
    fields[name] = { type, required };
  });

  if (error) { alert(error); throw new Error(error); }

  await fetchJSON(`/verb/log-schema/${project}/${group}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ primary_id: primaryId, fields })
  });

  alert("✅ Log schema saved");
  document.getElementById("log-schema-card").classList.add("hidden");
});

// Auto init
window.addEventListener("DOMContentLoaded", loadProjects);
