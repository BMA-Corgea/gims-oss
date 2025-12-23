// static/scripts/adjective_editor.js

const $ = id => document.getElementById(id);

// Helper function to create valid CSS ID strings from adjective names
function sanitizeForId(str) {
  // Replaces spaces and any non-alphanumeric characters with a hyphen
  return str.replace(/[^a-zA-Z0-9]/g, '-');
}

// Add fetchJSON helper function
async function fetchJSON(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    throw new Error(`HTTP error ${response.status}: ${response.statusText}`);
  }
  return response.json();
}

window.onload = loadProjects;

async function loadProjects() {
  try {
    const projects = await fetchJSON('/adjective/projects');
    const sel = $("project");
    sel.innerHTML = '';

    if (!Array.isArray(projects) || projects.length === 0) {
      sel.innerHTML = '<option value="">No projects available</option>';
      return;
    }

    projects.forEach((p, i) => {
      const opt = document.createElement('option');
      opt.value = p;
      opt.textContent = p;
      if (i === 0) opt.selected = true;
      sel.appendChild(opt);
    });

    // Default to first project and load nouns
    sel.value = projects[0];
    await loadNouns();
  } catch (error) {
    console.error('Error loading projects:', error);
    showError('Failed to load projects');
    $("project").innerHTML = '<option value="">Error loading projects</option>';
  }
}

$("project").onchange = loadNouns;

async function loadNouns() {
  try {
    const project = $("project").value;
    
    if (!project) {
      $("noun").innerHTML = "<option value=''>Select a project first</option>";
      return;
    }
    
    const nouns = await fetchJSON(`/adjective/nouns/${project}`);
    
    $("noun").innerHTML = Object.keys(nouns).map(n => `<option>${n}</option>`).join("");
  } catch (error) {
    console.error('Error loading nouns:', error);
    showError('Failed to load nouns');
  }
}

$("load-noun").onclick = loadAdjectives;

async function loadAdjectives() {
  const project = $("project").value;
  const noun    = $("noun").value;
  const adjectives = await fetch(`/adjective/list/${project}/${noun}`).then(r => r.json());
  const container = $("editor-container");
  container.innerHTML = "";

  // render existing adjective cards
  for (const adj of adjectives) {
    // **FIX**: Create a sanitized version of the name for use in the HTML ID
    const safeAdjName = sanitizeForId(adj.adjective);
    
    const card = document.createElement("div");
    card.className = "adjective-card";
    card.innerHTML = `
      <h2>${adj.adjective} (${adj.adjective_class})</h2>
      <div id="fields-${safeAdjName}"></div>
      <div class="actions">
        <button onclick="saveAdjective('${adj.adjective}', this)">Save</button>
        <button onclick="cancelAdjectiveEdit('${adj.adjective}', this)">Cancel/Refresh</button>
        <button onclick="demoteAdjective('${adj.adjective}')">Demote</button>
      </div>
    `;
    container.appendChild(card);

    // fetch config and dispatch to the correct renderer
    const config = await fetch(`/adjective/options/${project}/${noun}/${adj.adjective}`).then(r => r.json());
    if (adj.adjective_class === "ActionRequirement") {
      await renderActionRequirementEditor(config, project, noun, adj.adjective);
    } else if (adj.adjective_class === "ReferenceList") {
      await renderReferenceListEditor(config, project, noun, adj.adjective);
    } else if (adj.adjective_class === "Reference") {
      await renderReferenceEditor(config, project, noun, adj.adjective);
    } else if (adj.adjective_class === "Picture") {
      await renderPictureEditor(config, project, noun, adj.adjective);
    } else if (adj.adjective_class === "Tag") {
      await renderTagEditor(config, project, noun, adj.adjective);
    } else {
      renderGenericEditor(config, adj.adjective);
    }
  }

  // ─── Register New Adjectives Section ─────────────────────
  const nounTypes   = await fetch(`/project/${project}/noun_types`).then(r => r.json());
  const nounSchema  = nounTypes[noun] || {};
  const existingSet = new Set(adjectives.map(a => a.adjective));

  if (nounSchema.fields) {
    const regDiv = document.createElement("div");
    regDiv.id = "register-adj-section";
    regDiv.innerHTML = `<h3>Register New Adjectives</h3>`;
    container.appendChild(regDiv);

    const classes = await fetch(`/adjective/classes`).then(r => r.json());

    Object.keys(nounSchema.fields)
      .filter(fieldName => !existingSet.has(fieldName))
      .forEach(fieldName => {
        const row = document.createElement("div");
        row.className = "register-row";
        
        const lbl = document.createElement("span");
        lbl.textContent = fieldName;
        row.appendChild(lbl);
        
        const sel = document.createElement("select");
        classes.forEach(c => {
          const opt = document.createElement("option");
          opt.value   = c;
          opt.textContent = c;
          sel.appendChild(opt);
        });
        row.appendChild(sel);
        
        const btn = document.createElement("button");
        btn.textContent = "Register";
        btn.onclick = () => registerAdjective(fieldName, sel.value);
        row.appendChild(btn);

        regDiv.appendChild(row);
      });
  }
}

async function registerAdjective(fieldName, adjectiveClass) {
  const project = $("project").value;
  const noun    = $("noun").value;

  await fetch(`/adjective/promote/${project}/${noun}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      adjective: fieldName,
      adjective_class: adjectiveClass,
      applies_to: [noun]
    })
  });
  
  loadAdjectives();
}

async function renderActionRequirementEditor(config, project, noun, adjName) {
  const verbs    = await fetch(`/project/${project}/verb_types`).then(r => r.json());
  const verbList = Object.keys(verbs);
  // **FIX**: Sanitize name to find the correct container element
  const safeAdjName = sanitizeForId(adjName);
  const container= $(`fields-${safeAdjName}`);
  container.innerHTML = "";
  
  function makeRow(label = "", selected = []) {
    const row = document.createElement("div");
    row.className = "editor-row filter-row";
    
    const lbl = document.createElement("input");
    lbl.className = "label";
    lbl.value = label;
    lbl.placeholder = "Request Label";
    row.appendChild(lbl);
    
    const boxContainer = document.createElement("div");
    boxContainer.className = "verbs-container";
    verbList.forEach(v => {
      const chk = document.createElement("input");
      chk.type  = "checkbox";
      chk.className = "verb-checkbox";
      chk.value = v;
      if (selected.includes(v)) chk.checked = true;
      const lab = document.createElement("label");
      lab.appendChild(chk);
      lab.append(` ${v}`);
      boxContainer.appendChild(lab);
    });
    row.appendChild(boxContainer);
    
    const delBtn = document.createElement("button");
    delBtn.className = "delete-row";
    delBtn.textContent = "✕";
    delBtn.title = "Remove this label";
    delBtn.onclick = () => {
      const rows = container.querySelectorAll(".editor-row");
      if (rows.length <= 1) {
        alert("At least one request label is required.");
        return;
      }
      row.remove();
    };
    row.appendChild(delBtn);

    return row;
  }
  
  Object.entries(config.request_options || {}).forEach(([label, verbs]) => {
    container.appendChild(makeRow(label, verbs));
  });
  
  const addRowBtn = document.createElement("button");
  addRowBtn.textContent = "+ Add Request Label";
  addRowBtn.onclick = () => container.insertBefore(makeRow(), addRowBtn);
  container.appendChild(addRowBtn);
}


async function renderReferenceListEditor(config, project, noun, adjName) {
  // **FIX**: Sanitize name to find the correct container element
  const safeAdjName = sanitizeForId(adjName);
  const container = $(`fields-${safeAdjName}`);
  container.innerHTML = "";

  const nounTypes = await fetch(`/project/${project}/noun_types`).then(r => r.json());
  const selectedNouns = config.reference_noun || [];

  const nounDiv = document.createElement("div");
  nounDiv.innerHTML = "<b>Reference Nouns:</b><br>";
  Object.keys(nounTypes).forEach(nType => {
    const chk = document.createElement("input");
    chk.type = "checkbox";
    chk.value = nType;
    if (selectedNouns.includes(nType)) chk.checked = true;
    nounDiv.appendChild(chk);
    nounDiv.append(` ${nType} `);
  });
  container.appendChild(nounDiv);

  const filterDiv = document.createElement("div");
  filterDiv.innerHTML = "<b>Filters:</b>";
  container.appendChild(filterDiv);
  
  let filtersArr = [];
  Object.entries(config.filters || {}).forEach(([noun, attrMap]) => {
    Object.entries(attrMap || {}).forEach(([attr, val]) => {
      filtersArr.push({ noun, attr, value: val });
    });
  });

  function renderFilters() {
    const checked = Array.from(
      nounDiv.querySelectorAll("input[type=checkbox]")
    )
      .filter(chk => chk.checked)
      .map(chk => chk.value);
    
    filterDiv.innerHTML = "<b>Filters:</b>";

    filtersArr.forEach((f, idx) => {
      const row = document.createElement("div");
      row.className = "editor-row filter-row";
      
      const nounSelect = document.createElement("select");
      nounSelect.className = "filter-noun";
      checked.forEach(nType => {
        const opt = document.createElement("option");
        opt.value = nType;
        opt.textContent = nType;
        if (nType === f.noun) opt.selected = true;
        nounSelect.appendChild(opt);
      });
      nounSelect.onchange = () => {
        f.noun = nounSelect.value;
        const fields = nounTypes[f.noun].fields || {};
        fieldSelect.innerHTML = "";
        Object.keys(fields).forEach(fieldName => {
          const o = document.createElement("option");
          o.value = fieldName;
          o.textContent = fieldName;
          fieldSelect.appendChild(o);
        });
        f.attr = fieldSelect.value;
      };
      row.appendChild(nounSelect);
      
      const fieldSelect = document.createElement("select");
      fieldSelect.className = "filter-attr";
      const fields = nounTypes[f.noun].fields || {};
      Object.keys(fields).forEach(fieldName => {
        const opt = document.createElement("option");
        opt.value = fieldName;
        opt.textContent = fieldName;
        if (fieldName === f.attr) opt.selected = true;
        fieldSelect.appendChild(opt);
      });
      fieldSelect.onchange = () => { f.attr = fieldSelect.value; };
      row.appendChild(fieldSelect);
      
      const valInput = document.createElement("input");
      valInput.className = "filter-val";
      let displayVal = f.value;
      if (typeof f.value === "object" && f.value !== null) {
        displayVal = f.value[f.attr] ?? "";
      }
      valInput.value = displayVal;
      valInput.oninput = () => {
        f.value = valInput.value;
      };
      row.appendChild(valInput);
      
      const delBtn = document.createElement("button");
      delBtn.textContent = "✕";
      delBtn.className = "delete-filter";
      delBtn.onclick = () => {
        filtersArr.splice(idx, 1);
        renderFilters();
      };
      row.appendChild(delBtn);

      filterDiv.appendChild(row);
    });
    
    const addBtn = document.createElement("button");
    addBtn.textContent = "+ Add Filter";
    addBtn.onclick = () => {
      const baseNoun = checked[0] || "";
      const baseAttr = baseNoun
        ? Object.keys(nounTypes[baseNoun].fields)[0]
        : "";
      filtersArr.push({ noun: baseNoun, attr: baseAttr, value: "" });
      renderFilters();
    };
    filterDiv.appendChild(addBtn);
  }
  
  nounDiv.querySelectorAll("input[type=checkbox]").forEach(chk => {
    chk.onchange = renderFilters;
  });
  
  renderFilters();
  
  container.dataset.type = "ReferenceList";
}


async function renderReferenceEditor(config, project, noun, adjName) {
  // **FIX**: Sanitize name to find the correct container element
  const safeAdjName = sanitizeForId(adjName);
  const container = $(`fields-${safeAdjName}`);
  container.innerHTML = "";
  
  const nounTypes = await fetch(`/project/${project}/noun_types`).then(r => r.json());
  const selectedNoun = config.reference_noun || "";

  const nounDiv = document.createElement("div");
  nounDiv.innerHTML = "<b>Reference Noun:</b><br>";
  const nounSelect = document.createElement("select");
  Object.keys(nounTypes).forEach(nType => {
    const opt = document.createElement("option");
    opt.value = nType;
    opt.textContent = nType;
    if (nType === selectedNoun) opt.selected = true;
    nounSelect.appendChild(opt);
  });
  nounDiv.appendChild(nounSelect);
  container.appendChild(nounDiv);
  
  const filterDiv = document.createElement("div");
  filterDiv.innerHTML = "<b>Filters:</b>";
  container.appendChild(filterDiv);
  
  let filtersArr = [];
  const attrMap = config.filters || {};
  Object.entries(attrMap || {}).forEach(([attr, val]) => {
    filtersArr.push({ attr, value: val });
  });

  function renderFilters() {
    filterDiv.innerHTML = "<b>Filters:</b>";

    filtersArr.forEach((f, idx) => {
      const row = document.createElement("div");
      row.className = "editor-row";
      
      const fieldSelect = document.createElement("select");
      fieldSelect.className = "filter-attr";
      const fields = nounTypes[nounSelect.value]?.fields || {};
      Object.keys(fields).forEach(fieldName => {
        const opt = document.createElement("option");
        opt.value = fieldName;
        opt.textContent = fieldName;
        if (fieldName === f.attr) opt.selected = true;
        fieldSelect.appendChild(opt);
      });
      fieldSelect.onchange = () => { f.attr = fieldSelect.value; };
      row.appendChild(fieldSelect);
      
      const valInput = document.createElement("input");
      valInput.className = "filter-val";
      valInput.value = f.value ?? "";
      valInput.oninput = () => { f.value = valInput.value; };
      row.appendChild(valInput);
      
      const delBtn = document.createElement("button");
      delBtn.textContent = "✕";
      delBtn.className = "delete-filter";
      delBtn.onclick = () => {
        filtersArr.splice(idx, 1);
        renderFilters();
      };
      row.appendChild(delBtn);

      filterDiv.appendChild(row);
    });
    
    const addBtn = document.createElement("button");
    addBtn.textContent = "+ Add Filter";
    addBtn.onclick = () => {
      const baseAttr = Object.keys(nounTypes[nounSelect.value]?.fields || {})[0] || "";
      filtersArr.push({ attr: baseAttr, value: "" });
      renderFilters();
    };
    filterDiv.appendChild(addBtn);
  }
  
  nounSelect.onchange = () => {
    filtersArr = [];
    renderFilters();
  };
  
  renderFilters();
  
  container.dataset.type = "Reference";
}


function renderPictureEditor(config, project, noun, adjName) {
  // **FIX**: Sanitize name to find the correct container element
  const safeAdjName = sanitizeForId(adjName);
  const container = $(`fields-${safeAdjName}`);
  container.innerHTML = "<i>No configuration required for Picture adjective.</i>";
  container.dataset.type = "Picture";
}


async function renderTagEditor(config, project, noun, adjName) {
  // **FIX**: Sanitize name to find the correct container element
  const safeAdjName = sanitizeForId(adjName);
  const container = $(`fields-${safeAdjName}`);
  container.innerHTML = "";
  
  const defLabel = document.createElement("label");
  defLabel.textContent = "Definition:";
  const defInput = document.createElement("input");
  defInput.type = "text";
  defInput.className = "tag-definition";
  defInput.value = config.definition || "";
  container.appendChild(defLabel);
  container.appendChild(defInput);
  
  const optDiv = document.createElement("div");
  optDiv.innerHTML = "<b>Valid Options:</b>";
  container.appendChild(optDiv);
  
  let optionsArr = config.valid_options ? [...config.valid_options] : [];

  function renderOptions() {
    optDiv.innerHTML = "<b>Valid Options:</b>";

    optionsArr.forEach((opt, idx) => {
      const row = document.createElement("div");
      row.className = "tag-option-row";
      
      const valInput = document.createElement("input");
      valInput.type = "text";
      valInput.className = "tag-value";
      valInput.value = opt.value || "";
      valInput.oninput = () => { opt.value = valInput.value; };
      row.appendChild(valInput);
      
      const expInput = document.createElement("input");
      expInput.type = "text";
      expInput.className = "tag-explanation";
      expInput.placeholder = "Explanation (tooltip)";
      expInput.value = opt.explanation || "";
      expInput.oninput = () => { opt.explanation = expInput.value; };
      row.appendChild(expInput);
      
      const dispChk = document.createElement("input");
      dispChk.type = "checkbox";
      dispChk.className = "tag-display";
      dispChk.checked = !!opt.display_in_id;
      dispChk.onchange = () => { opt.display_in_id = dispChk.checked; };
      row.appendChild(dispChk);
      row.append(" Show in ID");
      
      const delBtn = document.createElement("button");
      delBtn.textContent = "✕";
      delBtn.onclick = () => {
        optionsArr.splice(idx, 1);
        renderOptions();
      };
      row.appendChild(delBtn);

      optDiv.appendChild(row);
    });
    
    const addBtn = document.createElement("button");
    addBtn.textContent = "+ Add Option";
    addBtn.onclick = () => {
      optionsArr.push({ value: "", explanation: "", display_in_id: false });
      renderOptions();
    };
    optDiv.appendChild(addBtn);
  }

  renderOptions();
}


function renderGenericEditor(config, adjName) {
  // **FIX**: Sanitize name to find the correct container element
  const safeAdjName = sanitizeForId(adjName);
  const container = $(`fields-${safeAdjName}`);
  container.innerHTML = Object.entries(config).map(([key, value]) => `
    <div class="editor-row">
      <div class="key">${key}</div>
      <input class="value" value="${value}">
    </div>
  `).join('');
}

async function saveAdjective(adjName, buttonEl) {
  const project = $("project").value;
  const noun = $("noun").value;
  // **FIX**: Sanitize name to find the correct container element using querySelector
  const safeAdjName = sanitizeForId(adjName);

  const card = buttonEl.closest(".adjective-card");
  const adjClass = card.querySelector("h2").textContent.split("(")[1].replace(")", "").trim();
  const container = card.querySelector(`#fields-${safeAdjName}`);
  
  if (!container) {
    console.error(`No fields container found for ${adjName}`);
    $("status").textContent = `❌ Could not find editor fields for ${adjName}`;
    return;
  }

  let updatedData;

  if (adjClass === "ActionRequirement") {
    const original = await fetch(`/adjective/configure/${project}/${noun}/${adjName}`).then(r => r.json());
    updatedData = { ...original, request_options: {} };
    container.querySelectorAll(".editor-row").forEach(row => {
      const label = row.querySelector(".label").value.trim();
      if (!label) return;
      const verbs = Array.from(row.querySelectorAll(".verb-checkbox"))
        .filter(cb => cb.checked)
        .map(cb => cb.value);
      updatedData.request_options[label] = verbs;
    });

  } else if (adjClass === "ReferenceList") {
    const original = await fetch(`/adjective/configure/${project}/${noun}/${adjName}`).then(r => r.json());
    updatedData = {
      adjective: original.adjective,
      adjective_class: original.adjective_class,
      applies_to: original.applies_to
    };
    const selectedNouns = Array.from(container.querySelectorAll("input[type=checkbox]"))
      .filter(c => c.checked)
      .map(c => c.value);
    updatedData.reference_noun = selectedNouns;
    const newFilters = {};
    container.querySelectorAll(".editor-row").forEach(row => {
      const nounVal = row.querySelector(".filter-noun").value;
      const attr = row.querySelector(".filter-attr").value;
      let val = row.querySelector(".filter-val").value.trim();
      try { val = JSON.parse(val); } catch {}
      if (nounVal && attr) {
        if (!newFilters[nounVal]) newFilters[nounVal] = {};
        newFilters[nounVal][attr] = val;
      }
    });
    updatedData.filters = newFilters;

  } else if (adjClass === "Reference") {
    const original = await fetch(`/adjective/configure/${project}/${noun}/${adjName}`).then(r => r.json());
    updatedData = {
      adjective: original.adjective,
      adjective_class: original.adjective_class,
      applies_to: original.applies_to
    };
    const selectedNoun = container.querySelector("select").value;
    updatedData.reference_noun = selectedNoun;
    const newFilters = {};
    container.querySelectorAll(".editor-row").forEach(row => {
      const attr = row.querySelector(".filter-attr").value;
      let val = row.querySelector(".filter-val").value.trim();
      try { val = JSON.parse(val); } catch {}
      if (attr) {
        if (!newFilters[selectedNoun]) newFilters[selectedNoun] = {};
        newFilters[selectedNoun][attr] = val;
      }
    });
    updatedData.filters = newFilters;

  } else if (adjClass === "Picture") {
    const original = await fetch(`/adjective/configure/${project}/${noun}/${adjName}`).then(r => r.json());
    updatedData = {
      adjective: original.adjective,
      adjective_class: original.adjective_class,
      applies_to: original.applies_to
    };
    const val =
      container.querySelector("input[type=file]")?.value ||
      container.querySelector("input[type=text]")?.value ||
      "";
    updatedData[original.adjective] = val;

  } else if (adjClass === "Tag") {
    const original = await fetch(`/adjective/configure/${project}/${noun}/${adjName}`).then(r => r.json());
    updatedData = {
      adjective: original.adjective,
      adjective_class: original.adjective_class,
      applies_to: original.applies_to
    };
    updatedData.definition = container.querySelector(".tag-definition").value.trim();
    const options = [];
    container.querySelectorAll(".tag-option-row").forEach(row => {
      const val = row.querySelector(".tag-value").value.trim();
      if (!val) return;
      const explanation = row.querySelector(".tag-explanation").value.trim();
      const displayInId = row.querySelector(".tag-display").checked;
      options.push({ value: val, explanation, display_in_id: displayInId });
    });
    updatedData.valid_options = options;

  } else {
    updatedData = {};
    container.querySelectorAll(".editor-row").forEach(row => {
      const key = row.querySelector(".key").textContent;
      const value = row.querySelector(".value").value;
      updatedData[key] = value;
    });
  }

  await fetch(`/adjective/update/${project}/${noun}/${adjName}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(updatedData)
  });

  $("status").textContent = `✅ Saved ${adjName}`;
}

async function upgradeToAdjective(fieldName) {
  const project = $("project").value;
  const noun    = $("noun").value;

  try {
    const res = await fetch(`/adjective/promote/${project}/${noun}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        adjective: fieldName,
        adjective_class: "Tag",
        applies_to: [noun]
      })
    });

    if (!res.ok) {
      $("status").textContent = `❌ Failed to promote '${fieldName}': ${res.status} ${res.statusText}`;
      return;
    }
    
    $("editor-container").innerHTML = "";
    await loadAdjectives();

    $("status").textContent = `✅ Promoted '${fieldName}' to adjective`;
  } catch (err) {
    $("status").textContent = `❌ Network or server error while promoting '${fieldName}': ${err.message}`;
  }
}

async function demoteAdjective(adjName) {
  const project = $("project").value;
  const noun    = $("noun").value;
  
  const ok = window.confirm(`⚠️ Are you sure you want to demote '${adjName}' back to a plain attribute? This cannot be undone.`);
  if (!ok) {
    $("status").textContent = `❎ Demote cancelled for '${adjName}'`;
    return;
  }

  await fetch(`/adjective/demote/${project}/${noun}/${adjName}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" }
  });
  
  $("editor-container").innerHTML = "";
  await loadAdjectives();
  
  $("status").textContent = `❌ Demoted '${adjName}' back to attribute`;
}

async function cancelAdjectiveEdit(adjName, buttonEl) {
  const project = $("project").value;
  const noun    = $("noun").value;
  
  const card = buttonEl.closest(".adjective-card");
  const adjClass = card.querySelector("h2")
                       .textContent
                       .split("(")[1]
                       .replace(")", "")
                       .trim();
  
  const config = await fetch(
    `/adjective/options/${project}/${noun}/${adjName}`
  ).then(r => r.json());
  
  if (adjClass === "ActionRequirement") {
    await renderActionRequirementEditor(config, project, noun, adjName);
  } else if (adjClass === "ReferenceList") {
    await renderReferenceListEditor(config, project, noun, adjName);
  } else if (adjClass === "Reference") {
    await renderReferenceEditor(config, project, noun, adjName);
  } else if (adjClass === "Picture") {
    await renderPictureEditor(config, project, noun, adjName);
  } else if (adjClass === "Tag") {
    await renderTagEditor(config, project, noun, adjName);
  } else {
    renderGenericEditor(config, adjName);
  }

  $("status").textContent = `✖️ Changes to ${adjName} cancelled`;
}