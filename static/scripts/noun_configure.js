// Debug control - set to false to disable all grid debug logging
const DEBUG_ENABLED = false; // Change to true to enable debug logs
// Debug helper that respects the flag
const debug = DEBUG_ENABLED ? console.debug.bind(console, "[noun_ui]") : () => {};

// ====== Constants ======
const TYPE_OPTIONS = ["string", "date", "number"];
const SEGMENT_TYPES = ["static", "date", "number", "letter", "hex"];

// ====== Helpers ======
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));
const getProject = () => $("#project").value;
const getNoun = () => $("#noun").value;

// ====== Boot ======
window.addEventListener("load", async () => {
  debug("window.load fired");
  bindGlobalHandlers();
  await loadProjects();
  $("#project").addEventListener("change", () => {
    debug("project changed →", getProject());
    loadNouns();
  });
  $("#noun").addEventListener("change", () => {
    debug("noun changed →", getNoun());
    clearEditUI();
  });
});

function bindGlobalHandlers() {
  debug("binding global handlers");
  $("#configure-button").addEventListener("click", () => {
    debug("clicked: Run / configureNoun");
    configureNoun();
  });

  const registerBtn = $("#register-button");
  if (registerBtn) {
    registerBtn.addEventListener("click", () => {
      debug("clicked: Register");
      registerNoun();
    });
  } else {
    debug("register-button not present");
  }

  const addFieldBtn = $("#add-field-button");
  if (addFieldBtn) {
    addFieldBtn.addEventListener("click", () => {
      debug("clicked: Add Field (register form)");
      addFieldRow();
    });
  } else {
    debug("add-field-button not present");
  }
}

// ====== Data Loaders ======
async function loadProjects() {
  debug("loadProjects: fetching /noun/projects");
  const res = await fetch('/noun/projects');
  const projects = await res.json();
  debug("loadProjects: got", projects.length, "projects", projects);

  const select = $("#project");
  select.innerHTML = '';
  projects.forEach(p => {
    const opt = document.createElement('option');
    opt.value = p;
    opt.textContent = p;
    select.appendChild(opt);
  });
  if (projects.length > 0) {
    select.selectedIndex = 0;
    debug("loadProjects: default selected project →", select.value);
    await loadNouns();
  } else {
    debug("loadProjects: no projects found");
  }
}

async function loadNouns() {
  const project = getProject();
  debug("loadNouns: fetching nouns for project", project);
  const res = await fetch(`/noun/types/${encodeURIComponent(project)}`);
  const nouns = await res.json();
  debug("loadNouns: got", nouns.length, "nouns", nouns);

  const select = $("#noun");
  select.innerHTML = '';
  nouns.forEach(n => {
    const opt = document.createElement('option');
    opt.value = n;
    opt.textContent = n;
    select.appendChild(opt);
  });
  debug("loadNouns: noun select populated");
  clearEditUI();
}

// ====== UI Reset ======
function clearEditUI() {
  debug("clearEditUI");
  $("#field-list").innerHTML = "";
  $("#output").textContent = "";
  $("#current-primary-id").innerHTML = "";
  $("#autogen-settings").innerHTML = "";
  const existingSave = $("#save-changes-button");
  if (existingSave) {
    existingSave.remove();
    debug("clearEditUI: removed existing save button");
  }
}

// ====== Register New Noun ======
function clearRegisterForm() {
  debug("clearRegisterForm");
  $("#new-noun-name").value = "";
  $("#new-field-rows").innerHTML = "";
  $("#register-segment-rows").innerHTML = "";
  $("#primary-id-select").innerHTML = "";
  $("#autogen-toggle").checked = false;
}

function refreshPrimaryIDOptions() {
  debug("refreshPrimaryIDOptions");
  const select = $("#primary-id-select");
  const fieldRows = $("#new-field-rows").children;
  select.innerHTML = '';
  for (let i = 0; i < fieldRows.length; i++) {
    const input = fieldRows[i].querySelector("input");
    const name = input.value.trim();
    if (name) {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      select.appendChild(opt);
    }
  }
  debug("refreshPrimaryIDOptions: options →", [...select.options].map(o => o.value));
}

function addFieldRow() {
  debug("addFieldRow: creating row");
  const fieldRows = $("#new-field-rows");
  const row = document.createElement("tr");

  const nameInput = document.createElement("input");
  nameInput.placeholder = "Field name";
  nameInput.oninput = () => {
    debug("addFieldRow: name changed →", nameInput.value);
    refreshPrimaryIDOptions();
  };
  nameInput.className = "nc-input";

  const typeSelect = document.createElement("select");
  typeSelect.className = "nc-select";
  TYPE_OPTIONS.forEach(opt => {
    const o = document.createElement("option");
    o.value = opt;
    o.textContent = opt;
    typeSelect.appendChild(o);
  });
  typeSelect.addEventListener("change", () => debug("addFieldRow: type changed →", typeSelect.value));

  const reqCheckbox = document.createElement("input");
  reqCheckbox.type = "checkbox";
  reqCheckbox.addEventListener("change", () => debug("addFieldRow: required →", reqCheckbox.checked));

  const delBtn = document.createElement("button");
  delBtn.className = "nc-btn nc-btn-icon";
  delBtn.textContent = "✖";
  delBtn.onclick = () => {
    debug("addFieldRow: delete row");
    fieldRows.removeChild(row);
    refreshPrimaryIDOptions();
  };

  row.innerHTML = "<td></td><td></td><td class='nc-center'></td><td class='nc-center'></td>";
  row.children[0].appendChild(nameInput);
  row.children[1].appendChild(typeSelect);
  row.children[2].appendChild(reqCheckbox);
  row.children[3].appendChild(delBtn);

  fieldRows.appendChild(row);
  refreshPrimaryIDOptions();
  debug("addFieldRow: row appended");
}

async function addSegmentRow(targetId = "register-segment-rows", seg = null) {
  debug("addSegmentRow:", { targetId, seg });
  const segList = document.getElementById(targetId);
  if (!segList) {
    debug("addSegmentRow: container not found:", targetId);
    return;
  }

  const row = document.createElement("div");
  row.className = "nc-flex";

  const typeSelect = document.createElement("select");
  typeSelect.className = "nc-select";
  SEGMENT_TYPES.forEach(opt => {
    const o = document.createElement("option");
    o.value = opt;
    o.textContent = opt;
    if (seg && seg.type === opt) o.selected = true;
    typeSelect.appendChild(o);
  });
  typeSelect.addEventListener("change", () => debug("segment type changed →", typeSelect.value));

  const inputContainer = document.createElement("div");
  inputContainer.className = "nc-flex-fill";

  async function renderInputs() {
    inputContainer.innerHTML = "";
    const type = typeSelect.value;
    debug("renderInputs for type:", type);

    if (type === "static") {
      const val = document.createElement("input");
      val.placeholder = "Static string";
      val.name = "value";
      val.value = seg?.value || "";
      val.className = "nc-input";
      val.addEventListener("input", () => debug("static value →", val.value));
      inputContainer.appendChild(val);
    } else if (type === "date") {
      debug("fetching date formats");
      const res = await fetch("/noun/date_formats");
      const formats = await res.json();
      debug("date formats:", formats);
      const format = document.createElement("select");
      format.name = "format";
      format.className = "nc-select";
      for (const [key, example] of Object.entries(formats)) {
        const opt = document.createElement("option");
        opt.value = key;
        opt.textContent = `${key} → ${example}`;
        if (seg?.format === key) opt.selected = true;
        format.appendChild(opt);
      }
      format.addEventListener("change", () => debug("date format →", format.value));
      inputContainer.appendChild(format);
    } else if (type === "number" || type === "hex") {
      const start = document.createElement("input");
      start.type = "text";
      start.placeholder = "Start";
      start.name = "start";
      start.value = seg?.start || "";
      start.className = "nc-input";
      start.addEventListener("input", () => debug("start →", start.value));

      const length = document.createElement("input");
      length.type = "number";
      length.placeholder = "Length";
      length.name = "length";
      length.value = seg?.length || 4;
      length.className = "nc-input";
      length.addEventListener("input", () => debug("length →", length.value));

      inputContainer.appendChild(start);
      inputContainer.appendChild(length);
    } else if (type === "letter") {
      const start = document.createElement("input");
      start.type = "number";
      start.placeholder = "Start index (0=A)";
      start.name = "start";
      start.value = seg?.start || 0;
      start.className = "nc-input";
      start.addEventListener("input", () => debug("letter start →", start.value));
      inputContainer.appendChild(start);
    }
  }

  typeSelect.onchange = renderInputs;
  await renderInputs();

  const delBtn = document.createElement("button");
  delBtn.className = "nc-btn nc-btn-icon";
  delBtn.textContent = "✖";
  delBtn.onclick = () => {
    debug("remove segment row");
    segList.removeChild(row);
  };

  row.appendChild(typeSelect);
  row.appendChild(inputContainer);
  row.appendChild(delBtn);
  segList.appendChild(row);
  debug("addSegmentRow: row appended");
}

async function registerNoun() {
  const project = getProject();
  const name = $("#new-noun-name").value.trim();
  const rows = $("#new-field-rows").children;
  const autogen = $("#autogen-toggle").checked;
  const segRows = $("#register-segment-rows").children;
  const primaryId = $("#primary-id-select").value;

  debug("registerNoun: start", { project, name, autogen, primaryId });

  if (!name) return alert("Provide a noun name.");

  const fields = {};
  for (const row of rows) {
    const fname = row.querySelector("input").value.trim();
    const sel = row.querySelector("select").value;
    const required = row.querySelector("input[type=checkbox]").checked || (fname === primaryId);
    if (!fname) return alert("Field name cannot be blank.");
    fields[fname] = {
      type: sel === "number" ? "float" : sel,
      required
    };
  }
  debug("registerNoun: fields", fields);

  if (!Object.keys(fields).length) return alert("Define at least one field.");
  if (!fields[primaryId]) return alert("Primary ID must be in the field list.");

  const segments = [];
  let hasNonStatic = false;
  for (const row of segRows) {
    const type = row.querySelector("select").value;
    const inputs = row.querySelectorAll("input, select");
    const seg = { type };
    inputs.forEach(i => {
      if (i.name === "value") seg.value = i.value;
      if (i.name === "format") seg.format = i.value;
      if (i.name === "start") seg.start = i.value;
      if (i.name === "length") seg.length = parseInt(i.value, 10);
    });
    if (type !== "static") hasNonStatic = true;
    segments.push(seg);
  }
  debug("registerNoun: segments", segments);

  if (autogen && !hasNonStatic) return alert("Autogen ID needs at least one non-static segment.");

  const payload = {
    noun_name: name,
    schema: {
      fields,
      primary_id_field: primaryId,
      autogenerate_id: autogen,
      autogenerate_segments: autogen ? segments : []
    }
  };
  debug("registerNoun: POST payload", payload);

  const res = await fetch(`/noun/register/${encodeURIComponent(project)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  const result = await res.json();
  debug("registerNoun: response", res.status, result);

  if (!res.ok) return alert(result.detail || "Registration failed.");
  showToast(`Registered "${name}"`, "success");   // <— add this
  alert(result.message || "Registered.");
  await loadNouns();
  clearRegisterForm();
}

// ====== Describe / Edit ======
async function configureNoun() {
  const project = getProject();
  const noun = getNoun();
  const action = document.querySelector('input[name="action"]:checked')?.value || "describe";
  debug("configureNoun:", { project, noun, action });
  if (!project || !noun) return;

  // describe
  const url = `/noun/describe/${encodeURIComponent(project)}/${encodeURIComponent(noun)}`;
  debug("configureNoun: fetching", url);
  const res = await fetch(url);
  const data = await res.json();
  debug("configureNoun: describe result keys", Object.keys(data));
  $("#output").textContent = JSON.stringify(data, null, 2);

  if (action !== "edit") {
    debug("configureNoun: view-only mode");
    return;
  }

  clearEditUI();
  renderPrimarySelector(data);
  renderAutogenEditor(data);
  renderFieldTable(data);
  renderSaveChangesButton(data);
}

function renderPrimarySelector(data) {
  debug("renderPrimarySelector: current pid =", data.primary_id_field);
  const container = $("#current-primary-id");
  container.innerHTML = "";

  const wrap = document.createElement("div");
  wrap.className = "nc-inline";

  const label = document.createElement("label");
  label.className = "nc-field";
  label.innerHTML = "<span>Primary ID Field</span>";

  const pidSelect = document.createElement("select");
  pidSelect.className = "nc-select";
  for (const field in data.fields) {
    const opt = document.createElement("option");
    opt.value = field;
    opt.textContent = field;
    if (field === data.primary_id_field) opt.selected = true;
    pidSelect.appendChild(opt);
  }
  pidSelect.addEventListener("change", () => debug("primary selector changed →", pidSelect.value));
  label.appendChild(pidSelect);

  const updateBtn = document.createElement("button");
  updateBtn.className = "nc-btn";
  updateBtn.textContent = "Update";
  updateBtn.onclick = async () => {
    const project = getProject();
    const noun = getNoun();
    const body = {
      action: "set_id",
      field_name: pidSelect.value,
      autogenerate: "keep"
    };
    debug("renderPrimarySelector: POST set_id", body);
    await fetch(`/noun/edit/${encodeURIComponent(project)}/${encodeURIComponent(noun)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    showToast("Autogen settings saved", "success"); // <— add this
    configureNoun();
  };

  wrap.append(label, updateBtn);
  container.appendChild(wrap);
}

function renderAutogenEditor(data) {
  debug("renderAutogenEditor: autogen?", !!data.autogenerate_id, "segments:", data.autogenerate_segments?.length || 0);
  const container = $("#autogen-settings");
  container.innerHTML = "";

  const toggleWrap = document.createElement("label");
  toggleWrap.className = "nc-checkbox";
  const toggle = document.createElement("input");
  toggle.type = "checkbox";
  toggle.checked = !!data.autogenerate_id;
  toggle.addEventListener("change", () => debug("autogen toggle →", toggle.checked));
  toggleWrap.append(" Autogenerate ID");
  toggleWrap.prepend(toggle);

  const segDiv = document.createElement("div");
  segDiv.id = "segment-rows";
  segDiv.className = "nc-segment-rows";

  const addSeg = document.createElement("button");
  addSeg.className = "nc-btn";
  addSeg.textContent = "+ Add Segment";
  addSeg.onclick = () => {
    debug("clicked: add segment");
    addSegmentRow("segment-rows");
  };

  if (Array.isArray(data.autogenerate_segments)) {
    data.autogenerate_segments.forEach(seg => addSegmentRow("segment-rows", seg));
  }

  const save = document.createElement("button");
  save.className = "nc-btn nc-btn-primary";
  save.textContent = "Save Autogen";
  save.onclick = async () => {
    const rows = segDiv.children;
    const segs = [];
    for (const row of rows) {
      const type = row.querySelector("select").value;
      const s = { type };
      row.querySelectorAll("input, select").forEach(i => {
        if (i.name === "value") s.value = i.value;
        if (i.name === "format") s.format = i.value;
        if (i.name === "start") s.start = i.value;
        if (i.name === "length") s.length = parseInt(i.value, 10);
      });
      segs.push(s);
    }
    debug("renderAutogenEditor: save segs", segs);

    const project = getProject();
    const noun = getNoun();

    const body = {
      action: "set_id",
      field_name: document.querySelector("#current-primary-id select").value,
      autogenerate: toggle.checked ? "yes" : "no",
      segments: segs
    };
    debug("renderAutogenEditor: POST set_id", body);

    await fetch(`/noun/edit/${encodeURIComponent(project)}/${encodeURIComponent(noun)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    configureNoun();
  };

  const preview = document.createElement("div");
  preview.className = "nc-preview";
  preview.textContent = "Current format: " + formatAutogenPreview(data.autogenerate_segments || []);

  container.append(toggleWrap, segDiv, addSeg, save, preview);
}

function formatAutogenPreview(segs) {
  debug("formatAutogenPreview: seg count", segs.length);
  return segs.map(s => {
    switch (s.type) {
      case "static": return s.value;
      case "date": return `<${s.format}>`;
      case "number": return `[num:${s.start}→len${s.length}]`;
      case "hex": return `[hex:${s.start}→len${s.length}]`;
      case "letter": return `[let:${s.start}]`;
      default: return '';
    }
  }).join('');
}

function renderFieldTable(data) {
  debug("renderFieldTable: fields", Object.keys(data.fields).length);
  const table = $("#field-list");
  table.innerHTML = ""; // clear previous

  for (const [name, f] of Object.entries(data.fields)) {
    const row = document.createElement("tr");

    const nameTd = document.createElement("td");
    const nameInput = document.createElement("input");
    nameInput.className = "nc-input";
    nameInput.value = name;
    nameInput.dataset.originalName = name;
    if (name === data.primary_id_field) nameInput.disabled = true; // lock primary ID
    nameInput.addEventListener("input", () => debug("field name edit:", name, "→", nameInput.value));
    nameTd.append(nameInput);

    const typeTd = document.createElement("td");
    const typeSel = document.createElement("select");
    typeSel.className = "nc-select";
    TYPE_OPTIONS.forEach(opt => {
      const o = document.createElement("option");
      o.value = o.textContent = opt;
      if ((opt === "number" && f.type === "float") || opt === f.type) o.selected = true;
      typeSel.append(o);
    });
    if (f.type === "adjective") typeSel.disabled = true;
    typeSel.addEventListener("change", () => debug("type changed for", nameInput.value, "→", typeSel.value));
    typeTd.append(typeSel);

    const reqTd = document.createElement("td");
    reqTd.className = "nc-center";
    const reqCheck = document.createElement("input");
    reqCheck.type = "checkbox";
    reqCheck.checked = !!f.required;
    if (f.type === "adjective") reqCheck.disabled = true;
    reqCheck.addEventListener("change", () => debug("required changed for", nameInput.value, "→", reqCheck.checked));
    reqTd.append(reqCheck);

    const delTd = document.createElement("td");
    delTd.className = "nc-center";
    const delBtn = document.createElement("button");
    delBtn.className = "nc-btn nc-btn-icon";
    delBtn.textContent = "✖";
    delBtn.title = "Mark for delete";
    delBtn.onclick = () => {
      row.dataset.markedForDelete = "true";
      row.classList.add("nc-row-deleting");
      debug("marked for delete:", nameInput.value);
    };
    delTd.append(delBtn);

    row.append(nameTd, typeTd, reqTd, delTd);
    table.append(row);
  }
}

function renderSaveChangesButton(data) {
  debug("renderSaveChangesButton");
  const fieldList = $("#field-list");

  // Remove old if any
  $$("#save-changes-button, #add-new-field-button").forEach(btn => btn.remove());

  const saveBtn = document.createElement("button");
  saveBtn.id = "save-changes-button";
  saveBtn.className = "nc-btn nc-btn-primary";
  saveBtn.textContent = "Save Changes";
  saveBtn.onclick = async () => {
    const project = getProject();
    const noun = getNoun();
    const rows = Array.from(fieldList.children);
    const currentPrimary = $("#current-primary-id select")?.value;
    debug("saveChanges: begin", { project, noun, currentPrimary });

    // Pass 1: renames
    for (const row of rows) {
      const nameInput = row.querySelector("td:nth-child(1) input");
      if (!nameInput || !nameInput.value.trim()) continue;
      const current = nameInput.value.trim();
      const original = nameInput.dataset.originalName || "";
      if (original && original !== current && data.fields.hasOwnProperty(original)) {
        const body = { action: "rename", old_name: original, new_name: current };
        debug("saveChanges: RENAME", body);
        await fetch(`/noun/edit/${encodeURIComponent(project)}/${encodeURIComponent(noun)}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body)
        });
      }
    }

    // Pass 2: deletes
    for (const row of rows) {
      if (row.dataset.markedForDelete !== "true") continue;
      const name = row.querySelector("td:nth-child(1) input")?.value.trim();
      if (!name) continue;

      if (name === currentPrimary) {
        const fallback = rows
          .filter(r => r !== row && r.dataset.markedForDelete !== "true")
          .map(r => r.querySelector("td:nth-child(1) input")?.value.trim())
          .find(Boolean);
        if (fallback) {
          const body = { action: "set_id", field_name: fallback, autogenerate: "keep" };
          debug("saveChanges: SHIFT PRIMARY to fallback", body);
          await fetch(`/noun/edit/${encodeURIComponent(project)}/${encodeURIComponent(noun)}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body)
          });
        }
      }

      const delBody = { action: "delete", field_name: name };
      debug("saveChanges: DELETE", delBody);
      await fetch(`/noun/edit/${encodeURIComponent(project)}/${encodeURIComponent(noun)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(delBody)
      });
    }

    // Pass 3: edits & adds
    for (const row of rows) {
      if (row.dataset.markedForDelete === "true") continue;

      const nameInput = row.querySelector("td:nth-child(1) input");
      if (!nameInput || !nameInput.value.trim()) continue;

      const current = nameInput.value.trim();
      const original = nameInput.dataset.originalName || "";
      const type = row.querySelector("td:nth-child(2) select").value;
      const required = row.querySelector("td:nth-child(3) input").checked;

      const isEdit = data.fields.hasOwnProperty(original || current);
      const payload = {
        action: isEdit ? "edit" : "add",
        field_name: current,
        required,
        ...(isEdit ? { new_type: type } : { field_type: type })
      };
      debug("saveChanges:", isEdit ? "EDIT" : "ADD", payload);

      await fetch(`/noun/edit/${encodeURIComponent(project)}/${encodeURIComponent(noun)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
    }

    debug("saveChanges: done → reconfigure");
    showToast("Saved changes", "success");          // <— add this
    await configureNoun();
  };

  const addBtn = document.createElement("button");
  addBtn.id = "add-new-field-button";
  addBtn.className = "nc-btn";
  addBtn.textContent = "+ New Field";
  addBtn.onclick = () => {
    debug("clicked: add new field row (edit table)");
    fieldList.appendChild(createNewFieldRow());
  };

  $("#field-table").after(addBtn, saveBtn);
}

function createNewFieldRow() {
  debug("createNewFieldRow");
  const row = document.createElement("tr");

  const nameTd = document.createElement("td");
  const nameInput = document.createElement("input");
  nameInput.placeholder = "New field name";
  nameInput.dataset.originalName = ""; // new
  nameInput.className = "nc-input";
  nameInput.addEventListener("input", () => debug("new field name →", nameInput.value));
  nameTd.appendChild(nameInput);

  const typeTd = document.createElement("td");
  const typeSel = document.createElement("select");
  typeSel.className = "nc-select";
  ["string", "number", "date"].forEach(opt => {
    const o = document.createElement("option");
    o.value = o.textContent = opt;
    typeSel.append(o);
  });
  typeSel.addEventListener("change", () => debug("new field type →", typeSel.value));
  typeTd.appendChild(typeSel);

  const reqTd = document.createElement("td");
  reqTd.className = "nc-center";
  const reqCheck = document.createElement("input");
  reqCheck.type = "checkbox";
  reqCheck.checked = true;
  reqCheck.addEventListener("change", () => debug("new field required →", reqCheck.checked));
  reqTd.appendChild(reqCheck);

  const delTd = document.createElement("td");
  delTd.className = "nc-center";
  const delBtn = document.createElement("button");
  delBtn.className = "nc-btn nc-btn-icon";
  delBtn.textContent = "✖";
  delBtn.onclick = () => {
    debug("remove new field row");
    row.remove();
  };
  delTd.appendChild(delBtn);

  row.append(nameTd, typeTd, reqTd, delTd);
  return row;
}

function showToast(message, type = "success", duration = 2600) {
  const container = document.getElementById("nc-toasts") || (() => {
    const c = document.createElement("div");
    c.id = "nc-toasts";
    c.className = "nc-toast-container";
    document.body.appendChild(c);
    return c;
  })();

  const t = document.createElement("div");
  t.className = `nc-toast nc-toast-${type}`;
  t.textContent = message;
  container.appendChild(t);

  // animate in
  requestAnimationFrame(() => t.classList.add("nc-toast-show"));

  // auto-dismiss
  setTimeout(() => {
    t.classList.remove("nc-toast-show");
    t.classList.add("nc-toast-hide");
    t.addEventListener("transitionend", () => t.remove(), { once: true });
  }, duration);
}

// expose helpers for inline handlers
window.addSegmentRow = addSegmentRow;
