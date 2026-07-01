// frontend/pages/noun_workbench.jsx — Noun Workbench (Phase 6 React; tool pages T7).
// React port of the 719-line vanilla noun_workbench.js: a noun-instance data-entry tool — a
// schema-driven manual form (Create or Edit, with autogen primary, adjective reference selects, date/
// text fields) + a bulk CSV/XLSX uploader (preview with insert/update diff, then commit valid rows).
// Reuses noun_workbench.css (the shared .controls/.segmented/.form-grid/.form-field/.messages/
// .upload-row/.diff/.chip + #id contract reproduced; the tour targets #project-select/#dynamicForm/
// #fileInput are preserved).
//
// Byte-identical mutations (nounworkshot.py, real route, 0 console errors), under /api/noun_workbench:
//   POST /{p}/{noun}/validate           { <field>: <trimmed> }     (autogen primary → "" in Create)
//   POST /{p}/{noun}/create             { ...same payload }
//   POST /{p}/{noun}/update/{id}        { ...same payload }
//   POST /{p}/{noun}/bulk_preview?mode= FormData { file }          (mode = create|update|upsert)
//   POST /{p}/{noun}/bulk_commit?mode=  { rows: [ ...valid payloads ] }
import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { Icon } from "../lib/ui.jsx";
import { enc, fetchJSON, mountOnAuth } from "../lib/api.js";

const API = "/api/noun_workbench";
const post = (url, body) => fetchJSON(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

function NounWorkbench() {
  const [projects, setProjects] = useState([]);
  const [project, setProject] = useState("");
  const [nounTypes, setNounTypes] = useState(null);
  const [nounType, setNounType] = useState("");
  const [schema, setSchema] = useState(null);    // {fields, primary_id_field, autogenerate_id, notes}
  const [refOpts, setRefOpts] = useState({});     // {field: [{value,label}]}
  const [form, setForm] = useState({});
  const [mode, setMode] = useState("create");
  const [bulkMode, setBulkMode] = useState("upsert");
  const [instances, setInstances] = useState([]); // [{value,label}]
  const [selInstance, setSelInstance] = useState("");
  const [currentId, setCurrentId] = useState(null);
  const [messages, setMessages] = useState([]);   // [{text,type}]
  const [fileName, setFileName] = useState("No file selected");
  const [preview, setPreview] = useState(null);
  const [bulkWarn, setBulkWarn] = useState([]);
  const fileRef = useRef(null);
  const existing = useRef({ ids: new Set(), byId: new Map() });
  const firstInit = useRef(true);

  const autogen = schema ? { field: schema.primary_id_field || null, enabled: !!schema.autogenerate_id } : { field: null, enabled: false };
  const fields = (schema && schema.fields) || {};
  const fieldNames = Object.keys(fields);
  const say = (text, type = "ok") => setMessages([{ text, type }]);
  const sayMany = (arr) => setMessages(arr);

  // ── loaders ──
  const loadNounTypes = async (p) => {
    setNounTypes(null);
    try { const nt = await fetchJSON(`${API}/${enc(p)}`); setNounTypes(Array.isArray(nt) ? nt : []); return Array.isArray(nt) ? nt : []; }
    catch { setNounTypes([]); return []; }
  };
  const loadInstances = async (p, nt) => {
    if (!p || !nt) { setInstances([]); return; }
    try {
      const items = await fetchJSON(`${API}/${enc(p)}/${enc(nt)}/items`);
      if (!Array.isArray(items) || !items.length) { setInstances([]); return; }
      setInstances(items.map((it) => { const keys = Object.keys(it); const pid = it[keys[0]] || "(unknown)"; return { value: pid, label: keys.includes("name") ? `${pid} — ${it.name}` : pid }; }));
    } catch { setInstances([]); }
  };
  const loadSchema = async (p, nt) => {
    setMessages([]);
    if (!nt) { setSchema(null); return; }
    const sch = await fetchJSON(`${API}/${enc(p)}/${enc(nt)}/schema`).catch(() => null);
    if (!sch) { say("Failed to load schema", "error"); return; }
    setSchema(sch); setCurrentId(null);
    // seed empty form + load adjective reference options
    setForm(Object.fromEntries(Object.keys(sch.fields || {}).map((f) => [f, ""])));
    const refs = {};
    for (const [fn, info] of Object.entries(sch.fields || {})) {
      if (info.type === "adjective") refs[fn] = await fetchJSON(`${API}/${enc(p)}/${enc(nt)}/references/${enc(fn)}`).catch(() => []);
    }
    setRefOpts(refs);
    if (sch.notes) say(sch.notes, "warn");
  };

  // ── init ──
  useEffect(() => {
    fetchJSON(`${API}/projects`).then(async (ps) => {
      const list = Array.isArray(ps) ? ps : [];
      setProjects(list);
      const url = new URLSearchParams(location.search);
      const urlP = url.get("project"), urlN = url.get("noun");
      const p = list.includes(urlP) ? urlP : (list[0] || "");
      setProject(p);
      if (p) { const nts = await loadNounTypes(p); const nt = (firstInit.current && urlN && nts.includes(urlN)) ? urlN : ""; firstInit.current = false; if (nt) { setNounType(nt); loadSchema(p, nt); loadInstances(p, nt); } }
    }).catch(() => {});
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const onProject = async (p) => { setProject(p); setNounType(""); setSchema(null); setForm({}); setMessages([]); setPreview(null); setBulkWarn([]); await loadNounTypes(p); };
  const onNoun = (nt) => { setNounType(nt); if (nt) { loadSchema(project, nt); loadInstances(project, nt); } else setSchema(null); };

  // ── form payload (collectFormData parity) ──
  const collect = () => {
    const payload = {};
    for (const fn of fieldNames) {
      const isAutogenDisabled = autogen.enabled && fn === autogen.field;
      payload[fn] = (isAutogenDisabled && mode === "create") ? "" : String(form[fn] != null ? form[fn] : "").trim();
    }
    return payload;
  };
  const guardAutogen = () => {
    if (autogen.enabled && mode === "create" && autogen.field) {
      const v = form[autogen.field];
      if (v && String(v).trim()) { say(`Field '${autogen.field}' must be blank (autogenerated).`, "error"); return false; }
    }
    return true;
  };

  const loadInstanceRecord = async () => {
    setMessages([]);
    if (!selInstance) { say("Select an instance to load.", "warn"); return; }
    try {
      const inst = await fetchJSON(`${API}/${enc(project)}/${enc(nounType)}/instance/${enc(selInstance)}`);
      if (!inst || !Object.keys(inst).length) { say(`No instance found for ID ${selInstance}`, "warn"); return; }
      setForm(Object.fromEntries(fieldNames.map((f) => [f, inst[f] != null ? String(inst[f]) : ""])));
      setCurrentId(selInstance); say(`Loaded ${selInstance}`, "ok");
    } catch { say("Failed to load instance", "error"); }
  };

  const validate = async () => {
    setMessages([]);
    if (!nounType) { say("Select a noun type first.", "warn"); return; }
    if (!guardAutogen()) return;
    try { const res = await post(`${API}/${enc(project)}/${enc(nounType)}/validate`, collect()); if (res.ok) say("Valid ✓", "ok"); else sayMany((res.errors || []).map((e) => ({ text: e, type: "error" }))); }
    catch { say("Validation failed (server error).", "error"); }
  };
  const save = async () => {
    setMessages([]);
    if (!nounType) { say("Select a noun type first.", "warn"); return; }
    if (!guardAutogen()) return;
    const payload = collect();
    try {
      if (mode === "edit" && currentId) { const r = await post(`${API}/${enc(project)}/${enc(nounType)}/update/${enc(currentId)}`, payload); if (r.ok) say(`Updated ${currentId} ✓`, "ok"); else sayMany((r.errors || []).map((e) => ({ text: e, type: "error" }))); }
      else { const r = await post(`${API}/${enc(project)}/${enc(nounType)}/create`, payload); if (r.ok) say(`Created ✓ ${r.id || ""}`, "ok"); else sayMany((r.errors || []).map((e) => ({ text: e, type: "error" }))); }
    } catch { say("Save failed (server error).", "error"); }
  };

  // ── bulk ──
  const doPreview = async () => {
    setBulkWarn([]); setPreview(null);
    if (!nounType) { setBulkWarn([{ text: "Select a noun type first.", type: "warn" }]); return; }
    const file = fileRef.current && fileRef.current.files[0];
    if (!file) { setBulkWarn([{ text: "Choose a CSV or XLSX file first.", type: "warn" }]); return; }
    try {
      const items = await fetchJSON(`${API}/${enc(project)}/${enc(nounType)}/items`).catch(() => []);
      const pidKey = schema && schema.primary_id_field;
      existing.current.ids = new Set((items || []).map((it) => String((it && it[pidKey]) != null ? it[pidKey] : "").trim().toLowerCase()));
      existing.current.byId = new Map((items || []).map((it) => [String((it && it[pidKey]) != null ? it[pidKey] : "").trim().toLowerCase(), it]));
      const fd = new FormData(); fd.append("file", file);
      const r = await fetch(`${API}/${enc(project)}/${enc(nounType)}/bulk_preview?mode=${enc(bulkMode)}`, { method: "POST", body: fd });
      const pv = await r.json();
      setPreview(pv);
      if (pv && pv.warnings && pv.warnings.length) setBulkWarn(pv.warnings.map((w) => ({ text: w, type: "warn" })));
    } catch { setBulkWarn([{ text: "Preview failed (server error).", type: "error" }]); }
  };
  const doCommit = async () => {
    setBulkWarn([]);
    if (!preview || !(preview.valid && preview.valid.length)) { setBulkWarn([{ text: "No valid rows to commit.", type: "warn" }]); return; }
    try {
      const rows = preview.valid.map((v) => v.payload);
      const res = await post(`${API}/${enc(project)}/${enc(nounType)}/bulk_commit?mode=${enc(bulkMode)}`, { rows });
      const warns = (res.errors || []).map((e) => ({ text: e, type: "error" }));
      warns.push({ text: `Inserted: ${res.inserted || 0}, Updated: ${res.updated || 0}, Skipped: ${res.skipped || 0}`, type: "ok" });
      setBulkWarn(warns); setPreview((p) => ({ ...p, _committed: true }));
    } catch { setBulkWarn([{ text: "Commit failed (server error).", type: "error" }]); }
  };

  // bulk preview table model
  const previewModel = (() => {
    if (!preview) return null;
    let columns = [];
    if (preview.valid && preview.valid.length) columns = Object.keys(preview.valid[0].payload || {});
    else if (preview.invalid && preview.invalid.length) columns = Array.from((preview.invalid).reduce((s, r) => { Object.keys(r.payload || {}).forEach((k) => s.add(k)); return s; }, new Set()));
    const pidKey = schema && schema.primary_id_field;
    const rowsMap = new Map();
    (preview.valid || []).forEach((r) => rowsMap.set(r.rowIndex, { ...r, ok: true }));
    (preview.invalid || []).forEach((r) => rowsMap.set(r.rowIndex, { ...(rowsMap.get(r.rowIndex) || {}), ...r, ok: false }));
    const rows = Array.from(rowsMap.values()).sort((a, b) => a.rowIndex - b.rowIndex);
    return { columns, pidKey, rows };
  })();

  const setField = (fn, v) => setForm((f) => ({ ...f, [fn]: v }));
  const committable = !!(preview && preview.valid && preview.valid.length && !preview._committed);

  return (
    <>
      <section className="panel">
        <div className="panel-body controls">
          <label className="field control-group"><span className="field-label">Project</span>
            <select id="project-select" className="input select" value={project} onChange={(e) => onProject(e.target.value)}>
              {!projects.length ? <option value="">No projects found</option> : projects.map((p) => <option key={p} value={p}>{p}</option>)}
            </select></label>
          <label className="field control-group"><span className="field-label">Noun Type</span>
            <select id="nounTypeSelect" className="input select" disabled={nounTypes == null || !nounTypes.length} value={nounType} onChange={(e) => onNoun(e.target.value)}>
              {nounTypes == null ? <option value="">Loading noun types...</option> : !nounTypes.length ? <option value="">No noun types</option> : [<option key="" value="">Select…</option>, ...nounTypes.map((n) => <option key={n} value={n}>{n}</option>)]}
            </select></label>
          <button id="reloadSchemaBtn" className="btn ghost" onClick={() => loadSchema(project, nounType)}><Icon name="refresh" />Reload Schema</button>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head"><Icon name="edit" /><span className="panel-title">Manual Form Mode</span></div>
        <div className="panel-body row gap">
          <div className="segmented" role="radiogroup" aria-label="Manual form mode">
            <label><input type="radio" name="form-mode" value="create" checked={mode === "create"} onChange={() => setMode("create")} /><span>Create</span></label>
            <label><input type="radio" name="form-mode" value="edit" checked={mode === "edit"} onChange={() => setMode("edit")} /><span>Edit</span></label>
          </div>
          {mode === "edit" ? (
            <div id="editLoadArea" className="inline">
              <select id="editInstanceSelect" className="input select" value={selInstance} onChange={(e) => setSelInstance(e.target.value)}>
                {!instances.length ? <option value="">No instances found</option> : [<option key="" value="">Select an instance</option>, ...instances.map((it) => <option key={it.value} value={it.value}>{it.label}</option>)]}
              </select>
              <button id="loadInstanceBtn" className="btn" type="button" onClick={loadInstanceRecord}>Load</button>
            </div>
          ) : null}
        </div>
      </section>

      <section className="panel">
        <div className="panel-head split"><span className="panel-title"><Icon name="noun" /> Manual Form</span>
          <div className="inline"><button id="validateBtn" className="btn ghost" type="button" onClick={validate}><Icon name="check" />Validate</button>
            <button id="saveBtn" className="btn-primary primary" type="button" onClick={save}><Icon name="save" />Save</button></div>
        </div>
        <div className="panel-body">
          <form id="dynamicForm" className="form-grid" noValidate onSubmit={(e) => e.preventDefault()}>
            {fieldNames.map((fn) => {
              const info = fields[fn] || {};
              const isAutogen = autogen.enabled && fn === autogen.field;
              return (
                <div className="form-field" key={fn}>
                  <label>{fn}{info.required ? <span className="required-badge">required</span> : null}</label>
                  {isAutogen ? (
                    <input className="input" data-name={fn} placeholder="Will be autogenerated" disabled value={form[fn] || ""} />
                  ) : info.type === "adjective" ? (
                    <select data-name={fn} value={form[fn] || ""} onChange={(e) => setField(fn, e.target.value)}>
                      <option value="">{info.required ? "Select…" : "— (optional) —"}</option>
                      {(refOpts[fn] || []).map((o) => <option key={o.value} value={o.value}>{o.label != null ? o.label : o.value}</option>)}
                    </select>
                  ) : (
                    <input type={info.type === "date" ? "date" : info.type === "datetime" ? "datetime-local" : "text"} className="input" data-name={fn} required={!!info.required} value={info.type === "datetime" ? String(form[fn] || "").replace("Z", "").slice(0, 16) : (form[fn] || "")} onChange={(e) => setField(fn, e.target.value)} />
                  )}
                </div>
              );
            })}
          </form>
          <div id="formMessages" className="messages">{messages.map((m, i) => <div className={`msg ${m.type}`} key={i}>{m.text}</div>)}</div>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head split"><span className="panel-title"><Icon name="upload" /> Bulk Upload</span>
          <div className="segmented" role="radiogroup" aria-label="Bulk mode">
            {[["create", "Create only"], ["update", "Update only"], ["upsert", "Upsert (default)"]].map(([v, lab]) => (
              <label key={v}><input type="radio" name="bulk-mode" value={v} checked={bulkMode === v} onChange={() => setBulkMode(v)} /><span>{lab}</span></label>
            ))}
          </div>
        </div>
        <div className="panel-body">
          <div className="upload-row">
            <label htmlFor="fileInput" className="file-label btn" role="button" tabIndex="0"><Icon name="folder" />Choose File</label>
            <span id="fileName" className="file-name">{fileName}</span>
            <input id="fileInput" ref={fileRef} type="file" accept=".csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                   onChange={(e) => setFileName(e.target.files.length ? e.target.files[0].name : "No file selected")} />
            <button id="previewBtn" className="btn" type="button" onClick={doPreview}><Icon name="filter" />Preview</button>
            <button id="commitBtn" className="btn success" type="button" disabled={!committable} onClick={doCommit}><Icon name="check" />Commit Valid Rows</button>
          </div>
          <div className="messages legend"><span className="msg"><strong>Legend:</strong> <span className="chip ins">Insert</span> <span className="chip upd">Update</span></span></div>
          <div id="bulkWarnings" className="messages">{bulkWarn.map((m, i) => <div className={`msg ${m.type}`} key={i}>{m.text}</div>)}</div>
          <div id="previewArea" className="table-wrap">
            {previewModel ? (
              <table>
                <thead><tr><th>#</th><th>Action</th>{previewModel.columns.map((c) => <th key={c}>{c}</th>)}<th>Status</th></tr></thead>
                <tbody>
                  {previewModel.rows.map((row, i) => {
                    const pidLc = String((row.payload && row.payload[previewModel.pidKey]) != null ? row.payload[previewModel.pidKey] : "").trim().toLowerCase();
                    const isUpdate = pidLc && existing.current.ids.has(pidLc);
                    const ex = isUpdate ? (existing.current.byId.get(pidLc) || {}) : null;
                    return (
                      <tr key={i} className={`${row.ok ? "valid" : "invalid"} ${isUpdate ? "action-update" : "action-insert"}`}>
                        <td>{row.rowIndex}</td><td>{isUpdate ? "Update" : "Insert"}</td>
                        {previewModel.columns.map((col) => {
                          const newVal = String((row.payload && row.payload[col]) != null ? row.payload[col] : "");
                          if (isUpdate && col !== previewModel.pidKey) {
                            const oldVal = String((ex && ex[col]) != null ? ex[col] : "");
                            if (oldVal !== newVal) return <td key={col} className="diff changed"><span className="old">{oldVal}</span> → <span className="new">{newVal}</span></td>;
                          }
                          return <td key={col}>{newVal}</td>;
                        })}
                        <td>{row.ok ? <span>✓ OK</span> : <pre>{(row.errors || []).map((e) => `• ${e}`).join("\n") || "Invalid"}</pre>}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            ) : null}
          </div>
        </div>
      </section>
    </>
  );
}

mountOnAuth("noun-workbench-root", (host) => createRoot(host).render(<NounWorkbench />));
