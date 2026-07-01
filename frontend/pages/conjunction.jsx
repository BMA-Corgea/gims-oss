// frontend/pages/conjunction.jsx — Conjunction Editor (Phase 6 React; editors track E6).
// React port of the 548-line vanilla conjunction.js: a project picker + a verb list-box (left) and,
// on the right, a conjunctions table (Add New / Edit / Delete) over a new/edit form (name,
// description, category, and a dynamic field editor where each field is text/number/boolean or a
// ReferenceList over a noun type). The dead apply/resolve stub (never reachable in the vanilla —
// showApplyConjunctionForm was never called) is dropped.
//
// Byte-identical payloads to the vanilla saveConjunction:
//   POST /conjunction/register/{project}/{verb}            (new)
//   POST /conjunction/update/{project}/{verb}/{originalName} (edit)
//   DELETE /conjunction/delete/{project}/{verb}/{name}
//   body { name, description, status, fields:[ "name" | {name,description,required}
//          | {type:'reference', mode:'ReferenceList', label, reference_noun, filters:{}, description?} ] }
// Reads: GET /conjunction/{projects, verbs/{p}, list/{p}/{v}, nouns/{p}}.
// Reuses conjunction.css (its .cm-*/.field-*/.actions/.edit-btn/.delete-btn contract is reproduced).
import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { Icon } from "../lib/ui.jsx";
import { enc, fetchJSON, mountOnAuth, toast } from "../lib/api.js";

const FIELD_TYPES = ["text", "number", "boolean", "reference"];
const CATEGORIES = ["Error", "Exception", "Cancelled", "Notification"];

function notify(message, kind = "info") {
  const k = kind === "error" ? "err" : kind === "success" ? "ok" : kind === "warning" ? "warn" : "info";
  toast(message, k);
}

// seed an editor row from a stored conjunction field (string or object), mirroring addFieldToForm
function rowFromField(field) {
  if (typeof field === "string") return { name: field, type: "text", required: true, description: "", reference_noun: "" };
  const type = FIELD_TYPES.includes(field.type) ? field.type : "text";
  const name = type === "reference" ? (field.label || "") : (field.name || "");
  return { name, type, required: !!field.required, description: field.description || "", reference_noun: field.reference_noun || "" };
}

// build the save payload field from an editor row, byte-identical to the vanilla collector
function fieldFromRow(row) {
  const name = row.name.trim();
  const description = (row.description || "").trim();
  if (row.type === "reference") {
    if (!row.reference_noun) return undefined; // dropped when no noun selected (as in vanilla)
    const f = { type: "reference", mode: "ReferenceList", label: name, reference_noun: row.reference_noun, filters: {} };
    if (description) f.description = description; // description: desc || undefined → key omitted when blank
    return f;
  }
  if (description) return { name, description, required: row.required };
  return name; // bare string when no description
}

// ── Delete-confirm modal on the Watery .overlay/.modal layer ──
function ConfirmModal({ message, onCancel, onConfirm }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onCancel(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onCancel]);
  return (
    <div className="overlay" onClick={(e) => { if (e.target === e.currentTarget) onCancel(); }}>
      <div className="modal">
        <div className="modal-head"><div className="modal-title"><Icon name="warning" />Confirm delete</div></div>
        <div className="modal-body"><p className="cm-confirm-msg">{message}</p></div>
        <div className="modal-foot">
          <button type="button" className="btn ghost" onClick={onCancel}>Cancel</button>
          <button type="button" className="btn cm-danger" onClick={onConfirm} autoFocus>Delete</button>
        </div>
      </div>
    </div>
  );
}

// ── one field editor row ──
function FieldRow({ row, nouns, onChange, onRemove, onWantNouns }) {
  const set = (patch) => onChange({ ...row, ...patch });
  return (
    <div className="field-item">
      <div className="field-row">
        <div className="field-group">
          <label>Name:</label>
          <input type="text" className="input field-name" value={row.name} required onChange={(e) => set({ name: e.target.value })} />
        </div>
        <div className="field-group">
          <label>Type:</label>
          <select className="input select field-type" value={row.type}
                  onChange={(e) => { const v = e.target.value; if (v === "reference") onWantNouns(); set({ type: v }); }}>
            {FIELD_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>
        <div className="field-group field-required">
          <label><input type="checkbox" className="field-required" checked={row.required} onChange={(e) => set({ required: e.target.checked })} /> Required</label>
        </div>
        <button type="button" className="remove-field-btn" onClick={onRemove}><Icon name="trash" />Remove</button>
      </div>
      <div className="field-row">
        <div className="field-group full-width">
          <label>Description:</label>
          <input type="text" className="input field-description" value={row.description} onChange={(e) => set({ description: e.target.value })} />
        </div>
      </div>
      {row.type === "reference" ? (
        <div className="field-row reference-config">
          <div className="field-group">
            <label>Reference Noun:</label>
            <select className="input select field-reference-noun" value={row.reference_noun} onChange={(e) => set({ reference_noun: e.target.value })}>
              <option value="">Select a noun type</option>
              {nouns.map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </div>
        </div>
      ) : null}
    </div>
  );
}

// ── new/edit form ──
function ConjunctionForm({ form, nouns, onWantNouns, onCancel, onSave }) {
  const [name, setName] = useState(form.name);
  const [description, setDescription] = useState(form.description);
  const [category, setCategory] = useState(CATEGORIES.includes(form.category) ? form.category : "Error");
  const [rows, setRows] = useState(form.fields);

  const setRow = (i, next) => setRows((rs) => rs.map((r, j) => (j === i ? next : r)));
  const addRow = () => setRows((rs) => [...rs, { name: "", type: "text", required: false, description: "", reference_noun: "" }]);

  const submit = (e) => {
    e.preventDefault();
    const fields = rows.map(fieldFromRow).filter((f) => f !== undefined);
    onSave({ name: name.trim(), description: description.trim(), status: category, fields });
  };

  return (
    <section id="conjunction-form-container" className="panel">
      <div className="panel-head cm-form-head">
        <Icon name="edit" />
        <span className="panel-title" id="form-title">{form.mode === "edit" ? `Edit Conjunction: ${form.originalName}` : "New Conjunction"}</span>
        <button type="button" id="close-form-btn" className="btn ghost sm action-button" onClick={onCancel}><Icon name="close" />Close</button>
      </div>
      <form id="conjunction-form" className="panel-body cm-form" onSubmit={submit}>
        <label className="field form-group"><span className="field-label">Name</span>
          <input type="text" id="conj-name" className="input" required value={name} onChange={(e) => setName(e.target.value)} /></label>
        <label className="field form-group"><span className="field-label">Description</span>
          <textarea id="conj-description" className="input" rows="2" value={description} onChange={(e) => setDescription(e.target.value)} /></label>
        <label className="field form-group"><span className="field-label">Category</span>
          <select id="conj-category" className="input select" value={category} onChange={(e) => setCategory(e.target.value)}>
            {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
          </select></label>
        <div className="form-group cm-fields">
          <div className="cm-fields-head">
            <h4 className="cm-subhead">Fields</h4>
            <button type="button" id="add-field-btn" className="btn sm secondary-button" onClick={addRow}><Icon name="plus" />Add Field</button>
          </div>
          <div id="fields-container">
            {rows.map((r, i) => (
              <FieldRow key={i} row={r} nouns={nouns} onWantNouns={onWantNouns}
                        onChange={(next) => setRow(i, next)} onRemove={() => setRows((rs) => rs.filter((_, j) => j !== i))} />
            ))}
          </div>
        </div>
        <div className="form-actions">
          <button type="button" id="cancel-btn" className="btn ghost secondary-button" onClick={onCancel}><Icon name="close" />Cancel</button>
          <button type="submit" id="save-conjunction-btn" className="btn-primary primary-button"><Icon name="save" />Save</button>
        </div>
      </form>
    </section>
  );
}

// ── root ──
function ConjunctionEditor() {
  const [projects, setProjects] = useState(null);
  const [project, setProject] = useState("");
  const [verbs, setVerbs] = useState(null);   // null=loading, []=loaded
  const [verb, setVerb] = useState("");
  const [conjunctions, setConjunctions] = useState({ status: "noverb" }); // noverb|loading|ok
  const [nouns, setNouns] = useState([]);      // ['Run', ...]
  const [form, setForm] = useState(null);      // null | {mode, originalName, name, description, category, fields}
  const [confirmName, setConfirmName] = useState(null);

  useEffect(() => {
    fetchJSON("/conjunction/projects").then((ps) => {
      if (!Array.isArray(ps) || !ps.length) { setProjects([]); notify("No projects available", "warning"); return; }
      setProjects(ps); setProject(ps[0]);
    }).catch(() => { setProjects([]); notify("Error loading projects", "error"); });
  }, []);

  // project change → load verbs, reset
  useEffect(() => {
    if (!project) { setVerbs([]); return; }
    setVerb(""); setConjunctions({ status: "noverb" }); setNouns([]); setForm(null); setVerbs(null);
    fetchJSON(`/conjunction/verbs/${enc(project)}`)
      .then((vs) => setVerbs(Array.isArray(vs) ? vs : []))
      .catch(() => { setVerbs([]); notify("Error loading verbs", "error"); });
  }, [project]);

  const loadConjunctions = (v) => {
    setForm(null);
    if (!v) { setConjunctions({ status: "noverb" }); return; }
    setConjunctions({ status: "loading" });
    fetchJSON(`/conjunction/list/${enc(project)}/${enc(v)}`)
      .then((data) => setConjunctions({ status: "ok", list: Array.isArray(data) ? data : [] }))
      .catch(() => setConjunctions({ status: "ok", list: [] }));
  };

  const onVerbChange = (v) => { setVerb(v); loadConjunctions(v); };

  // lazy noun fetch (guarded — once per project)
  const wantNouns = () => {
    if (!project || nouns.length) return;
    fetchJSON(`/conjunction/nouns/${enc(project)}`)
      .then((nt) => setNouns(["Run", ...Object.keys(nt || {})]))
      .catch(() => setNouns(["Run"]));
  };

  const openAdd = () => setForm({ mode: "new", originalName: "", name: "", description: "", category: "Error", fields: [] });
  const openEdit = (conj) => {
    wantNouns();
    setForm({
      mode: "edit", originalName: conj.name,
      name: conj.name, description: conj.description || "", category: conj.status || "failure",
      fields: (conj.fields || []).map(rowFromField),
    });
  };

  const save = async (payload) => {
    const url = form.mode === "edit"
      ? `/conjunction/update/${enc(project)}/${enc(verb)}/${enc(form.originalName)}`
      : `/conjunction/register/${enc(project)}/${enc(verb)}`;
    try {
      await fetchJSON(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      setForm(null);
      loadConjunctions(verb);
      notify(`Conjunction ${form.mode === "edit" ? "updated" : "created"} successfully`, "success");
    } catch (e) { notify(e && e.message ? e.message : "Failed to save conjunction", "error"); }
  };

  const doDelete = async (name) => {
    setConfirmName(null);
    try {
      await fetchJSON(`/conjunction/delete/${enc(project)}/${enc(verb)}/${enc(name)}`, { method: "DELETE" });
      loadConjunctions(verb);
      notify(`Conjunction "${name}" deleted successfully`, "success");
    } catch (e) { notify(e && e.message ? e.message : "Failed to delete conjunction", "error"); }
  };

  const list = conjunctions.status === "ok" ? conjunctions.list : [];

  return (
    <>
      <section className="panel cm-toolbar">
        <div className="panel-head"><Icon name="folder" /><span className="panel-title">Workspace</span></div>
        <div className="panel-body cm-toolbar-row">
          <label className="field cm-field"><span className="field-label">Project</span>
            <select id="project-select" className="input select" value={project} onChange={(e) => setProject(e.target.value)}>
              {projects == null ? <option value="">Loading…</option>
                : !projects.length ? <option value="">No projects available</option>
                : projects.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </label>
        </div>
      </section>

      <div className="cm-main">
        <section className="panel cm-verbs">
          <div className="panel-head"><Icon name="verb" /><span className="panel-title">Verbs</span></div>
          <div className="panel-body">
            <select id="verb-select" className="input select cm-verb-list" size="10" value={verb}
                    onChange={(e) => onVerbChange(e.target.value)}>
              {verbs == null ? <option value="">Loading verbs...</option>
                : !verbs.length ? <option value="">No verbs available</option>
                : verbs.map((v) => <option key={v} value={v}>{v}</option>)}
            </select>
          </div>
        </section>

        <div className="cm-right">
          <section className="panel cm-list">
            <div className="panel-head cm-list-head">
              <Icon name="conjunction" /><span className="panel-title">Conjunctions</span>
              <button id="add-conjunction-btn" className="btn blue sm action-button" disabled={!verb} onClick={openAdd}><Icon name="plus" />Add New</button>
            </div>
            <div className="panel-body cm-list-body">
              <div className="conjunction-list cm-table-wrap">
                <table id="conjunction-table" className="cm-table">
                  <thead><tr><th>Name</th><th>Description</th><th className="col-actions">Actions</th></tr></thead>
                  <tbody id="conjunction-list-body">
                    {conjunctions.status === "noverb" ? <tr><td colSpan="3" className="cm-state-cell">Select a verb to view conjunctions</td></tr> : null}
                    {conjunctions.status === "loading" ? <tr><td colSpan="3" className="cm-state-cell">Loading conjunctions...</td></tr> : null}
                    {conjunctions.status === "ok" && !list.length ? <tr><td colSpan="3" className="cm-state-cell">No conjunctions defined</td></tr> : null}
                    {conjunctions.status === "ok" && list.map((conj) => (
                      <tr key={conj.name}>
                        <td>{conj.name}</td>
                        <td>{conj.description || ""}</td>
                        <td className="actions">
                          <button className="edit-btn" onClick={() => openEdit(conj)}>Edit</button>
                          <button className="delete-btn" onClick={() => setConfirmName(conj.name)}>Delete</button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </section>

          {form ? (
            <ConjunctionForm key={form.mode + ":" + form.originalName} form={form} nouns={nouns}
                             onWantNouns={wantNouns} onCancel={() => setForm(null)} onSave={save} />
          ) : null}
        </div>
      </div>

      {confirmName != null ? (
        <ConfirmModal message={`Are you sure you want to delete conjunction "${confirmName}"? This cannot be undone.`}
                      onCancel={() => setConfirmName(null)} onConfirm={() => doDelete(confirmName)} />
      ) : null}
    </>
  );
}

mountOnAuth("conjunction-root", (host) => createRoot(host).render(<ConjunctionEditor />));
