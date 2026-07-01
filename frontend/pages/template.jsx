// frontend/pages/template.jsx — Project Template Manager (Phase 6 React; tool pages T1).
// React port of the 264-line vanilla template.js: an Export card (project picker → download a
// template), a Create-New-Project card, and an Import-Template card (file/paste JSON + new-project
// config + copy-custom toggle), with a shared Results area (dry-run plan table / completion summary).
// Reuses template.css (the .tm-*/.form-grid/.field/.kv-*/.results* + #id contract is reproduced).
//
// Byte-identical mutation (templateshot.py, real route, 0 console errors):
//   POST /api/project_template/import
//     { template: null|<parsed JSON>, config:{ name, project_code, description|null, extra:{k:v} },
//       copy_custom: bool, dry_run: bool }
//   Create → template:null, copy_custom:false; Import → template from file/text, copy_custom from toggle.
//   project_code validated /^[A-Za-z0-9.-]{2,64}$/. Export uses a temp <a> download (Content-Disposition).
import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { Icon } from "../lib/ui.jsx";
import { enc, fetchJSON, mountOnAuth, toast } from "../lib/api.js";

const API = "/api/project_template";
const PC_RE = /^[A-Za-z0-9.-]{2,64}$/;
const notify = (m, k = "ok") => toast(m, k === "err" || k === "info" ? k : "ok");

function ExtraFields({ rows, onChange }) {
  return (
    <div className="kv-list extra-fields">
      {rows.map((r, i) => (
        <div className="kv-row" key={i}>
          <input className="input kv-k" placeholder="key" value={r.k} onChange={(e) => onChange(rows.map((x, j) => (j === i ? { ...x, k: e.target.value } : x)))} />
          <input className="input kv-v" placeholder="value" value={r.v} onChange={(e) => onChange(rows.map((x, j) => (j === i ? { ...x, v: e.target.value } : x)))} />
          <button className="btn sm del" title="Remove field" aria-label="Remove field" type="button" onClick={() => onChange(rows.filter((_, j) => j !== i))}><Icon name="trash" /></button>
        </div>
      ))}
    </div>
  );
}

function gatherConfig(form) {
  const name = form.name.trim();
  const project_code = form.code.trim();
  const description = form.desc.trim() || null;
  if (!name) throw new Error("New project name is required");
  if (!project_code) throw new Error("Project code is required");
  if (!PC_RE.test(project_code)) throw new Error("Invalid project code. Use 2–64 characters: letters, numbers, dot, or hyphen (no underscores).");
  const extra = {};
  form.extra.forEach((row) => { const k = row.k.trim(); if (k) extra[k] = row.v.trim(); });
  return { name, project_code, description, extra };
}

function ConfigFields({ form, setForm, names }) {
  const set = (patch) => setForm({ ...form, ...patch });
  return (
    <>
      <label className="field"><span className="field-label">New Project Name</span>
        <input className="input project-name" placeholder={names.namePh} value={form.name} onChange={(e) => set({ name: e.target.value })} /></label>
      <label className="field"><span className="field-label">Project Code (required, unique)</span>
        <input className="input project-code" placeholder={names.codePh} value={form.code} onChange={(e) => set({ code: e.target.value })} /></label>
      <label className="field"><span className="field-label">Description (optional)</span>
        <input className="input project-desc" placeholder="Describe this project" value={form.desc} onChange={(e) => set({ desc: e.target.value })} /></label>
      <div className="kv-header"><span>Extra Fields (optional)</span>
        <button className="btn sm" type="button" onClick={() => set({ extra: [...form.extra, { k: "", v: "" }] })}><Icon name="plus" />Add field</button></div>
      <ExtraFields rows={form.extra} onChange={(extra) => set({ extra })} />
    </>
  );
}

function TemplateManager() {
  const [projects, setProjects] = useState([]);
  const [project, setProject] = useState("");
  const [createForm, setCreateForm] = useState({ name: "", code: "", desc: "", extra: [{ k: "", v: "" }] });
  const [importForm, setImportForm] = useState({ name: "", code: "", desc: "", extra: [{ k: "", v: "" }] });
  const [text, setText] = useState("");
  const [copyCustom, setCopyCustom] = useState(true);
  const [results, setResults] = useState(null); // {title, summary, plan:[]}
  const fileRef = useRef(null);

  const loadProjects = async () => {
    try {
      let list = await fetchJSON(`${API}/projects`);
      if (!Array.isArray(list)) list = Object.values(list || {});
      const names = list.map((p) => (typeof p === "string" ? p : (p.name || p.project || ""))).filter(Boolean);
      setProjects(names); if (names.length && !project) setProject(names[0]);
    } catch (e) { notify("Couldn't load projects list", "err"); }
  };
  useEffect(() => { loadProjects(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const doExport = () => {
    if (!project) { notify("Choose a project to export", "info"); return; }
    const a = document.createElement("a");
    a.href = `${API}/${enc(project)}/export?download=true`; a.rel = "noopener";
    document.body.appendChild(a); a.click(); a.remove();
  };

  const readTemplate = () => new Promise((resolve, reject) => {
    const file = fileRef.current && fileRef.current.files && fileRef.current.files[0];
    if (file) {
      const fr = new FileReader();
      fr.onload = () => { try { resolve(JSON.parse(fr.result)); } catch { reject(new Error("Selected file is not valid JSON")); } };
      fr.onerror = () => reject(new Error("Failed to read selected file"));
      fr.readAsText(file, "utf-8");
      return;
    }
    const t = text.trim();
    if (t) { try { resolve(JSON.parse(t)); } catch { reject(new Error("Pasted template JSON is invalid")); } return; }
    reject(new Error("Provide a template file or paste JSON"));
  });

  const submit = async (isDryRun, isCreate) => {
    try {
      const template = isCreate ? null : await readTemplate();
      const config = gatherConfig(isCreate ? createForm : importForm);
      const copy_custom = isCreate ? false : copyCustom;
      const res = await fetchJSON(`${API}/import`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ template, config, copy_custom, dry_run: isDryRun }) });
      if (isDryRun) {
        const plan = res.plan || [];
        const counts = plan.reduce((acc, s) => { acc[s.op] = (acc[s.op] || 0) + 1; return acc; }, {});
        const parts = Object.entries(counts).map(([k, v]) => `${k}: ${v}`).join(" • ");
        setResults({ title: "Dry Run Plan", summary: `Target: ${res.target || "(unknown)"}  •  Ops — ${parts || "none"}`, plan });
        notify("Dry run ready");
      } else if (res.ok) {
        setResults({ title: isCreate ? "Project Created" : "Import Complete", summary: `Created project at: ${res.created || "(unknown)"}`, plan: [] });
        notify("Project created successfully");
        loadProjects();
        if (isCreate) setCreateForm({ name: "", code: "", desc: "", extra: [{ k: "", v: "" }] });
      } else { notify(res.detail || "Operation did not succeed", "err"); }
    } catch (e) { notify(e.message || "An error occurred", "err"); }
  };

  return (
    <>
      <section className="panel tm-export">
        <div className="panel-head"><Icon name="download" /><span className="panel-title">Export Template</span></div>
        <div className="panel-body"><div className="form-grid">
          <div className="inline">
            <label className="field tm-grow"><span className="field-label">Project</span>
              <select id="projectSelect" className="input select" value={project} onChange={(e) => setProject(e.target.value)}>
                {!projects.length ? <option value="">— no projects found —</option> : projects.map((p) => <option key={p} value={p}>{p}</option>)}
              </select></label>
            <button className="btn" title="Reload project list" onClick={loadProjects}><Icon name="refresh" />Refresh</button>
          </div>
          <div className="actions"><button className="btn-primary" onClick={doExport}><Icon name="download" />Download Template</button></div>
        </div></div>
      </section>

      <section id="createCard" className="panel">
        <div className="panel-head"><Icon name="plus" /><span className="panel-title">Create New Project</span></div>
        <div className="panel-body"><div className="form-grid">
          <ConfigFields form={createForm} setForm={setCreateForm} names={{ namePh: "e.g., My-New-Project", codePh: "e.g., MY-PROJ (letters, numbers, dot, hyphen)" }} />
          <div className="actions">
            <button className="btn" onClick={() => submit(true, true)}><Icon name="play" />Preview (Dry Run)</button>
            <button className="btn-primary" onClick={() => submit(false, true)}><Icon name="plus" />Create Project</button>
          </div>
        </div></div>
      </section>

      <section id="importCard" className="panel">
        <div className="panel-head"><Icon name="upload" /><span className="panel-title">Import Template</span></div>
        <div className="panel-body"><div className="form-grid">
          <div className="field"><span className="field-label">Template JSON — choose a file or paste below</span>
            <input type="file" ref={fileRef} accept=".json,application/json" className="input tm-file" />
            <textarea className="input" rows="8" placeholder="{ ...template JSON... }" aria-label="Template JSON text" value={text} onChange={(e) => setText(e.target.value)} />
          </div>
          <hr className="rule" />
          <ConfigFields form={importForm} setForm={setImportForm} names={{ namePh: "e.g., LIMS-Clone", codePh: "e.g., LIMS-CLONE (letters, numbers, dot, hyphen)" }} />
          <div className="options"><label className="switch"><span className="toggle"><input type="checkbox" checked={copyCustom} onChange={(e) => setCopyCustom(e.target.checked)} /><span className="track" /></span><span>Copy <code>custom/</code> files</span></label></div>
          <div className="actions">
            <button className="btn" onClick={() => submit(true, false)}><Icon name="play" />Preview (Dry Run)</button>
            <button className="btn-primary" onClick={() => submit(false, false)}><Icon name="check" />Create From Template</button>
          </div>
        </div></div>
      </section>

      {results ? (
        <div id="results" className="results panel">
          <div className="results-header panel-head">
            <span className="results-head-title"><Icon name="file" /><span className="panel-title">{results.title}</span></span>
            <button className="btn sm" onClick={() => setResults(null)}><Icon name="close" />Clear</button>
          </div>
          <div className="panel-body">
            <div className="results-summary">{results.summary}</div>
            {results.plan.length ? (
              <div className="table-wrap"><table className="table"><thead><tr><th>Op</th><th>Path</th></tr></thead>
                <tbody>{results.plan.map((s, i) => <tr key={i}><td>{s.op}</td><td>{s.path}</td></tr>)}</tbody></table></div>
            ) : null}
          </div>
        </div>
      ) : null}
    </>
  );
}

mountOnAuth("template-root", (host) => createRoot(host).render(<TemplateManager />));
