//template.js
// ---------- tiny helpers ----------
const DEBUG = false;
const dlog = DEBUG ? console.debug.bind(console) : () => {};

const qs  = (s, el=document) => el.querySelector(s);
const qsa = (s, el=document) => Array.from(el.querySelectorAll(s));
const h = (tag, attrs={}, children=[]) => {
  const el = document.createElement(tag);
  for (const [k,v] of Object.entries(attrs)) {
    if (k === "class") el.className = v;
    else if (k === "html") el.innerHTML = v;
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (v !== undefined && v !== null) el.setAttribute(k, v);
  }
  (Array.isArray(children) ? children : [children]).forEach(c=>{
    if (c == null) return;
    if (typeof c === "string") el.appendChild(document.createTextNode(c));
    else el.appendChild(c);
  });
  return el;
};

const toast = (msg, type="ok") => {
  const t = qs("#toast");
  t.textContent = msg;
  t.classList.add("show");
  setTimeout(()=>t.classList.remove("show"), 2200);
};

async function jsonFetch(url, opts={}) {
  dlog("jsonFetch", url, opts);
  const res = await fetch(url, { ...opts, headers: { "Content-Type":"application/json", ...(opts.headers||{}) } });
  if (!res.ok) {
    const text = await res.text().catch(()=> "");
    let detail = text;
    try { detail = JSON.parse(text).detail || text; } catch {}
    throw new Error(`HTTP ${res.status}: ${detail || res.statusText}`);
  }
  return res.json();
}

// ---------- projects list ----------
async function loadProjects() {
  const select = qs("#projectSelect");
  select.innerHTML = "";
  select.disabled = true;

  try {
    let list = await jsonFetch("/api/project_template/projects");
    if (!Array.isArray(list)) list = Object.values(list || {});
    if (list.length === 0) {
      select.appendChild(h("option", { value: "" }, "— no projects found —"));
      return;
    }
    for (const p of list) {
      const name = typeof p === "string" ? p : (p.name || p.project || "");
      if (!name) continue;
      select.appendChild(h("option", { value: name }, name));
    }
    select.disabled = false;
  } catch (e) {
    console.error(e);
    toast("Couldn't load projects list");
  }
}

// ---------- export ----------
function doExport() {
  const project = qs("#projectSelect").value;
  if (!project) {
    toast("Choose a project to export");
    return;
  }
  // Let the browser handle file download via Content-Disposition
  const url = `/api/project_template/${encodeURIComponent(project)}/export?download=true`;
  const a = document.createElement("a");
  a.href = url;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

// ---------- create / import ----------
function readTemplateFromInputs() {
  const file = qs("#templateFile").files?.[0] || null;
  const text = qs("#templateText").value.trim();

  if (file) {
    return new Promise((resolve, reject) => {
      const fr = new FileReader();
      fr.onload = () => {
        try { resolve(JSON.parse(fr.result)); }
        catch (e) { reject(new Error("Selected file is not valid JSON")); }
      };
      fr.onerror = () => reject(new Error("Failed to read selected file"));
      fr.readAsText(file, "utf-8");
    });
  }

  if (text) {
    try { return Promise.resolve(JSON.parse(text)); }
    catch { return Promise.reject(new Error("Pasted template JSON is invalid")); }
  }

  return Promise.reject(new Error("Provide a template file or paste JSON"));
}

const PC_RE = /^[A-Za-z0-9.-]{2,64}$/;

function gatherConfig(contextEl) {
  const name = qs(".project-name", contextEl).value.trim();
  const project_code = qs(".project-code", contextEl).value.trim();
  const description = qs(".project-desc", contextEl).value.trim() || null;

  if (!name) throw new Error("New project name is required");
  if (!project_code) throw new Error("Project code is required");
  if (!PC_RE.test(project_code)) throw new Error("Invalid project code. Use 2–64 characters: letters, numbers, dot, or hyphen (no underscores).");

  const extra = {};
  qsa(".kv-row", qs(".extra-fields", contextEl)).forEach(row => {
    const k = row.querySelector(".kv-k").value.trim();
    const v = row.querySelector(".kv-v").value.trim();
    if (k) extra[k] = v;
  });

  return { name, project_code, description, extra };
}

function renderPlan(plan, target, title="Dry Run Plan"){
  const results = qs("#results");
  const resultsTitle = qs("#resultsTitle");
  const tbody = qs("#resultsTbody");
  const summary = qs("#resultsSummary");
  const wrap = qs("#resultsTableWrap");

  results.classList.remove("hidden");
  resultsTitle.textContent = title;

  tbody.innerHTML = "";
  const counts = plan.reduce((acc, s)=>{ acc[s.op] = (acc[s.op]||0)+1; return acc; }, {});
  const parts = Object.entries(counts).map(([k,v])=>`${k}: ${v}`).join(" • ");
  summary.textContent = `Target: ${target}  •  Ops — ${parts || "none"}`;

  for (const step of plan) {
    const tr = document.createElement("tr");
    tr.appendChild(h("td", {}, step.op));
    tr.appendChild(h("td", {}, step.path));
    tbody.appendChild(tr);
  }
  if (plan.length === 0) {
    wrap.classList.add("hidden");
  } else {
    wrap.classList.remove("hidden");
  }
}

function renderResultOk(createdPath, title="Operation Complete"){
  const results = qs("#results");
  const resultsTitle = qs("#resultsTitle");
  const tbody = qs("#resultsTbody");
  const summary = qs("#resultsSummary");
  const wrap = qs("#resultsTableWrap");

  results.classList.remove("hidden");
  resultsTitle.textContent = title;
  summary.textContent = `Created project at: ${createdPath}`;
  tbody.innerHTML = "";
  wrap.classList.add("hidden");
}

async function handleProjectSubmission({ isDryRun, fromCardId }) {
    try {
        const isCreate = fromCardId === '#createCard';
        const cardEl = qs(fromCardId);
        
        const template = isCreate ? null : await readTemplateFromInputs();
        const config = gatherConfig(cardEl);
        const copy_custom = isCreate ? false : qs("#copyCustomChk").checked;

        const payload = { template, config, copy_custom, dry_run: isDryRun };
        const res = await jsonFetch("/api/project_template/import", { method: "POST", body: JSON.stringify(payload) });
        
        if (isDryRun) {
            renderPlan(res.plan || [], res.target || "(unknown)");
            toast("Dry run ready");
        } else if (res.ok) {
            renderResultOk(res.created || "(unknown)", isCreate ? "Project Created" : "Import Complete");
            toast("Project created successfully");
            loadProjects(); // Refresh project list after creation
            if (isCreate) {
                // Clear create form on success
                qs("#createName", cardEl).value = '';
                qs("#createCode", cardEl).value = '';
                qs("#createDesc", cardEl).value = '';
                const extraFieldsEl = qs("#extraFieldsCreate");
                extraFieldsEl.innerHTML = '';
                addExtraRow(extraFieldsEl);
            }
        } else {
            toast(res.detail || "Operation did not succeed");
        }
    } catch (e) {
        console.error(e);
        toast(e.message || "An error occurred");
    }
}

// ---------- dynamic fields ----------
function addExtraRow(containerEl, k="", v=""){
  const row = h("div",{class:"kv-row"},[
    h("input",{class:"input kv-k", placeholder:"key", value:k}),
    h("input",{class:"input kv-v", placeholder:"value", value:v}),
    h("button",{class:"btn small del", title:"Remove"}, "×")
  ]);
  row.querySelector(".del").addEventListener("click", ()=> row.remove());
  containerEl.appendChild(row);
}

// ---------- wire up ----------
window.addEventListener("DOMContentLoaded", async () => {
  // --- Export card
  qs("#refreshProjectsBtn").addEventListener("click", loadProjects);
  qs("#exportBtn").addEventListener("click", doExport);

  // --- Create card
  const extraFieldsCreate = qs("#extraFieldsCreate");
  qs("#addExtraBtnCreate").addEventListener("click", () => addExtraRow(extraFieldsCreate));
  qs("#createPreviewBtn").addEventListener("click", () => handleProjectSubmission({ isDryRun: true, fromCardId: "#createCard" }));
  qs("#createProjectBtn").addEventListener("click", () => handleProjectSubmission({ isDryRun: false, fromCardId: "#createCard" }));
  
  // --- Import card
  const extraFieldsImport = qs("#extraFields");
  qs("#addExtraBtn").addEventListener("click", () => addExtraRow(extraFieldsImport));
  qs("#previewBtn").addEventListener("click", () => handleProjectSubmission({ isDryRun: true, fromCardId: "#importCard" }));
  qs("#importBtn").addEventListener("click", () => handleProjectSubmission({ isDryRun: false, fromCardId: "#importCard" }));

  // --- Results area
  qs("#clearResultsBtn").addEventListener("click", ()=>{
    qs("#results").classList.add("hidden");
  });

  // boot
  await loadProjects();
  addExtraRow(extraFieldsImport); // one starter row for import
  addExtraRow(extraFieldsCreate); // one starter row for create
});

