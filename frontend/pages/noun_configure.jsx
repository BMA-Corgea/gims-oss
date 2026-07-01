// frontend/pages/noun_configure.jsx — Noun Configure (Phase 6 React; editors track E4).
// React port of the noun schema editor: a toolbar (project/noun/view-edit) + a Schema panel
// (view = pretty JSON; edit = primary-ID selector, autogen-ID segment editor with live preview,
// editable field table, 3-pass Save) + a Register-New-Noun form (fields + primary + autogen segments).
// Endpoints + payloads are byte-identical to the vanilla: /noun/{projects,types,describe,date_formats},
// POST /noun/register/{project} {noun_name,schema}, POST /noun/edit/{project}/{noun}
// {action: set_id|rename|delete|edit|add, ...}. number fields persist as "float" (as before).
import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { Icon, StateBlock } from "../lib/ui.jsx";
import { enc, fetchJSON, mountOnAuth, toast } from "../lib/api.js";

const TYPE_OPTIONS = ["string", "date", "datetime", "number"];
const SEGMENT_TYPES = ["static", "date", "number", "letter", "hex"];

const editUrl = (project, noun) => `/noun/edit/${enc(project)}/${enc(noun)}`;
const postEdit = (project, noun, body) => fetchJSON(editUrl(project, noun), {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
});

const newSeg = () => ({ type: "static", value: "", format: "", start: "", length: 4 });
function cleanSeg(s) {
  const o = { type: s.type };
  if (s.type === "static") o.value = s.value || "";
  else if (s.type === "date") o.format = s.format || "";
  else if (s.type === "number" || s.type === "hex") { o.start = s.start || ""; o.length = parseInt(s.length, 10); }
  else if (s.type === "letter") o.start = s.start || 0;
  return o;
}
function previewSeg(s) {
  switch (s.type) {
    case "static": return s.value;
    case "date": return `<${s.format}>`;
    case "number": return `[num:${s.start}→len${s.length}]`;
    case "hex": return `[hex:${s.start}→len${s.length}]`;
    case "letter": return `[let:${s.start}]`;
    default: return "";
  }
}
function segFrom(seg) { return { ...newSeg(), ...seg }; }

function SegmentEditor({ segments, onChange, dateFormats }) {
  const set = (i, patch) => onChange(segments.map((s, j) => (j === i ? { ...s, ...patch } : s)));
  return (
    <div className="nc-segment-rows">
      {segments.map((s, i) => (
        <div className="nc-flex" key={i}>
          <select className="input select nc-select" value={s.type} onChange={(e) => set(i, { type: e.target.value })}>
            {SEGMENT_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
          <div className="nc-flex-fill">
            {s.type === "static" ? <input className="input nc-input" placeholder="Static string" value={s.value} onChange={(e) => set(i, { value: e.target.value })} /> : null}
            {s.type === "date" ? (
              <select className="input select nc-select" value={s.format} onChange={(e) => set(i, { format: e.target.value })}>
                <option value="">(format)</option>
                {Object.entries(dateFormats || {}).map(([k, ex]) => <option key={k} value={k}>{k} → {ex}</option>)}
              </select>
            ) : null}
            {(s.type === "number" || s.type === "hex") ? (
              <>
                <input className="input nc-input" placeholder="Start" value={s.start} onChange={(e) => set(i, { start: e.target.value })} />
                <input className="input nc-input" type="number" placeholder="Length" value={s.length} onChange={(e) => set(i, { length: e.target.value })} />
              </>
            ) : null}
            {s.type === "letter" ? <input className="input nc-input" type="number" placeholder="Start index (0=A)" value={s.start} onChange={(e) => set(i, { start: e.target.value })} /> : null}
          </div>
          <button className="nc-btn nc-btn-icon" type="button" title="Remove segment" aria-label="Remove segment" onClick={() => onChange(segments.filter((_, j) => j !== i))}><Icon name="close" /></button>
        </div>
      ))}
      <button className="nc-btn" type="button" onClick={() => onChange([...segments, newSeg()])}><Icon name="plus" />Add Segment</button>
    </div>
  );
}

// ── edit mode: primary selector + autogen editor + field table + save ──────────────────────────
function EditView({ project, noun, data, dateFormats, onChanged }) {
  const [pid, setPid] = useState(data.primary_id_field || "");
  const [autogen, setAutogen] = useState(!!data.autogenerate_id);
  const [segs, setSegs] = useState((data.autogenerate_segments || []).map(segFrom));
  const [rows, setRows] = useState(Object.entries(data.fields || {}).map(([name, f]) => ({
    originalName: name, name, type: f.type === "float" ? "number" : f.type, required: !!f.required,
    isAdjective: f.type === "adjective", markedForDelete: false,
  })));

  const setRow = (i, patch) => setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));

  const updatePrimary = async () => {
    try { await postEdit(project, noun, { action: "set_id", field_name: pid, autogenerate: "keep" }); toast("Primary ID updated", "ok"); onChanged(); }
    catch (e) { toast(`Failed: ${String(e.message || e)}`, "err"); }
  };
  const saveAutogen = async () => {
    try {
      await postEdit(project, noun, { action: "set_id", field_name: pid, autogenerate: autogen ? "yes" : "no", segments: segs.map(cleanSeg) });
      toast("Autogen settings saved", "ok"); onChanged();
    } catch (e) { toast(`Failed: ${String(e.message || e)}`, "err"); }
  };

  const saveChanges = async () => {
    try {
      // Pass 1: renames
      for (const r of rows) {
        const cur = r.name.trim();
        if (cur && r.originalName && r.originalName !== cur && Object.prototype.hasOwnProperty.call(data.fields, r.originalName)) {
          await postEdit(project, noun, { action: "rename", old_name: r.originalName, new_name: cur });
        }
      }
      // Pass 2: deletes (shift primary to a fallback if deleting the primary)
      for (const r of rows) {
        if (!r.markedForDelete) continue;
        const name = r.name.trim();
        if (!name) continue;
        if (name === pid) {
          const fallback = rows.filter((x) => x !== r && !x.markedForDelete).map((x) => x.name.trim()).find(Boolean);
          if (fallback) await postEdit(project, noun, { action: "set_id", field_name: fallback, autogenerate: "keep" });
        }
        await postEdit(project, noun, { action: "delete", field_name: name });
      }
      // Pass 3: edits & adds
      for (const r of rows) {
        if (r.markedForDelete) continue;
        const cur = r.name.trim();
        if (!cur) continue;
        const isEdit = Object.prototype.hasOwnProperty.call(data.fields, r.originalName || cur);
        const body = isEdit
          ? { action: "edit", field_name: cur, required: r.required, new_type: r.type }
          : { action: "add", field_name: cur, required: r.required, field_type: r.type };
        await postEdit(project, noun, body);
      }
      toast("Saved changes", "ok");
      onChanged();
    } catch (e) { toast(`Save failed: ${String(e.message || e)}`, "err"); }
  };

  const addRow = () => setRows((rs) => [...rs, { originalName: "", name: "", type: "string", required: true, isAdjective: false, markedForDelete: false }]);

  return (
    <>
      <div className="nc-inline nc-primary-row">
        <label className="nc-field nc-grow"><span>Primary ID Field</span>
          <select className="input select nc-select" value={pid} onChange={(e) => setPid(e.target.value)}>
            {Object.keys(data.fields || {}).map((f) => <option key={f} value={f}>{f}</option>)}
          </select>
        </label>
        <button className="nc-btn" type="button" onClick={updatePrimary}><Icon name="check" />Update</button>
      </div>

      <div className="nc-section">
        <label className="nc-checkbox"><input type="checkbox" checked={autogen} onChange={(e) => setAutogen(e.target.checked)} /> Autogenerate ID</label>
        <SegmentEditor segments={segs} onChange={setSegs} dateFormats={dateFormats} />
        <button className="nc-btn nc-btn-primary" type="button" onClick={saveAutogen}><Icon name="save" />Save Autogen</button>
        <div className="nc-preview">Current format: {(data.autogenerate_segments || []).map(previewSeg).join("")}</div>
      </div>

      <div className="nc-table-wrap">
        <table className="nc-table">
          <thead><tr><th>Field</th><th>Type</th><th>Required</th><th>Actions</th></tr></thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className={r.markedForDelete ? "nc-row-deleting" : ""} data-field={r.originalName || r.name}>
                <td><input className="input nc-input" value={r.name} disabled={r.originalName === data.primary_id_field}
                           onChange={(e) => setRow(i, { name: e.target.value })} /></td>
                <td>
                  <select className="input select nc-select" value={r.type} disabled={r.isAdjective} onChange={(e) => setRow(i, { type: e.target.value })}>
                    {(r.isAdjective ? ["adjective", ...TYPE_OPTIONS] : TYPE_OPTIONS).map((t) => <option key={t} value={t}>{t}</option>)}
                  </select>
                </td>
                <td className="nc-center"><input type="checkbox" checked={r.required} disabled={r.isAdjective} onChange={(e) => setRow(i, { required: e.target.checked })} /></td>
                <td className="nc-center"><button className="nc-btn nc-btn-icon" type="button" title="Mark for delete" aria-label="Mark for delete" onClick={() => setRow(i, { markedForDelete: true })}><Icon name="trash" /></button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="nc-actions">
        <button className="nc-btn" type="button" onClick={addRow}><Icon name="plus" />New Field</button>
        <button className="nc-btn nc-btn-primary" type="button" onClick={saveChanges}><Icon name="save" />Save Changes</button>
      </div>
    </>
  );
}

// ── register a new noun ────────────────────────────────────────────────────────────────────────
function RegisterPanel({ project, dateFormats, onRegistered }) {
  const [name, setName] = useState("");
  const [fields, setFields] = useState([]); // [{name,type,required}]
  const [primaryId, setPrimaryId] = useState("");
  const [autogen, setAutogen] = useState(false);
  const [segs, setSegs] = useState([]);

  const setField = (i, patch) => setFields((fs) => fs.map((f, j) => (j === i ? { ...f, ...patch } : f)));
  const fieldNames = fields.map((f) => f.name.trim()).filter(Boolean);
  const pid = fieldNames.includes(primaryId) ? primaryId : (fieldNames[0] || "");

  const register = async () => {
    const nm = name.trim();
    if (!nm) { toast("Provide a noun name.", "warn"); return; }
    const f = {};
    for (const row of fields) {
      const fn = row.name.trim();
      if (!fn) { toast("Field name cannot be blank.", "warn"); return; }
      f[fn] = { type: row.type === "number" ? "float" : row.type, required: !!row.required || fn === pid };
    }
    if (!Object.keys(f).length) { toast("Define at least one field.", "warn"); return; }
    if (!f[pid]) { toast("Primary ID must be in the field list.", "warn"); return; }
    const clean = segs.map(cleanSeg);
    if (autogen && !clean.some((s) => s.type !== "static")) { toast("Autogen ID needs at least one non-static segment.", "warn"); return; }
    const payload = { noun_name: nm, schema: { fields: f, primary_id_field: pid, autogenerate_id: autogen, autogenerate_segments: autogen ? clean : [] } };
    try {
      await fetchJSON(`/noun/register/${enc(project)}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      toast(`Registered "${nm}"`, "ok");
      setName(""); setFields([]); setPrimaryId(""); setAutogen(false); setSegs([]);
      onRegistered(nm);
    } catch (e) { toast(`Registration failed: ${String(e.message || e)}`, "err"); }
  };

  return (
    <section className="panel nc-card">
      <div className="panel-head"><Icon name="plus" /><span className="panel-title">Register New Noun</span></div>
      <div className="panel-body">
        <label className="field nc-field"><span className="field-label">Noun Name</span>
          <input className="input nc-input" placeholder="e.g., Sample" value={name} onChange={(e) => setName(e.target.value)} /></label>
        <div className="nc-table-wrap">
          <table className="nc-table">
            <thead><tr><th>Field</th><th>Type</th><th>Required</th><th /></tr></thead>
            <tbody>
              {fields.map((f, i) => (
                <tr key={i}>
                  <td><input className="input nc-input" placeholder="Field name" value={f.name} onChange={(e) => setField(i, { name: e.target.value })} /></td>
                  <td><select className="input select nc-select" value={f.type} onChange={(e) => setField(i, { type: e.target.value })}>{TYPE_OPTIONS.map((t) => <option key={t} value={t}>{t}</option>)}</select></td>
                  <td className="nc-center"><input type="checkbox" checked={f.required} onChange={(e) => setField(i, { required: e.target.checked })} /></td>
                  <td className="nc-center"><button className="nc-btn nc-btn-icon" type="button" title="Remove field" aria-label="Remove field" onClick={() => setFields((fs) => fs.filter((_, j) => j !== i))}><Icon name="close" /></button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="nc-actions">
          <button className="nc-btn" type="button" onClick={() => setFields((fs) => [...fs, { name: "", type: "string", required: false }])}><Icon name="plus" />Add Field</button>
        </div>
        <label className="field nc-field"><span className="field-label">Primary ID Field</span>
          <select className="input select nc-select" value={pid} onChange={(e) => setPrimaryId(e.target.value)}>
            {fieldNames.map((n) => <option key={n} value={n}>{n}</option>)}
          </select></label>
        <div className="nc-section">
          <label className="nc-checkbox"><input type="checkbox" checked={autogen} onChange={(e) => setAutogen(e.target.checked)} /> Autogenerate ID</label>
          <SegmentEditor segments={segs} onChange={setSegs} dateFormats={dateFormats} />
        </div>
        <div className="nc-actions"><button className="nc-btn nc-btn-primary" type="button" onClick={register}><Icon name="save" />Register</button></div>
      </div>
    </section>
  );
}

function NounConfigure() {
  const [projects, setProjects] = useState(null);
  const [project, setProject] = useState("");
  const [nouns, setNouns] = useState([]);
  const [noun, setNoun] = useState("");
  const [action, setAction] = useState("describe"); // describe | edit
  const [dateFormats, setDateFormats] = useState({});
  const [schema, setSchema] = useState({ status: "empty" });
  const [loadKey, setLoadKey] = useState(0);

  useEffect(() => {
    fetchJSON("/noun/projects").then((ps) => { setProjects(ps || []); if (Array.isArray(ps) && ps.length) setProject(ps[0]); }).catch(() => { setProjects([]); toast("Could not load projects", "err"); });
    fetchJSON("/noun/date_formats").then(setDateFormats).catch(() => {});
  }, []);

  useEffect(() => { setNoun(""); setSchema({ status: "empty" }); }, [project]);
  useEffect(() => {
    if (!project) return;
    fetchJSON(`/noun/types/${enc(project)}`).then((ns) => setNouns(Array.isArray(ns) ? ns : [])).catch(() => { setNouns([]); toast("Could not load nouns", "err"); });
  }, [project]);

  useEffect(() => {
    if (!project || !noun) { setSchema({ status: "empty" }); return; }
    let live = true;
    setSchema({ status: "loading" });
    fetchJSON(`/noun/describe/${enc(project)}/${enc(noun)}`)
      .then((data) => { if (live) setSchema({ status: "ok", data }); })
      .catch((e) => { if (live) setSchema({ status: "error", message: String(e.message || e) }); });
    return () => { live = false; };
  }, [project, noun, loadKey]);

  const reDescribe = () => setLoadKey((k) => k + 1);

  return (
    <>
      <section className="panel nc-toolbar-panel">
        <div className="panel-head"><Icon name="noun" /><span className="panel-title">Configure noun schema</span></div>
        <div className="panel-body nc-toolbar">
          <label className="field nc-field"><span className="field-label">Project</span>
            <select className="input select nc-select" value={project} onChange={(e) => setProject(e.target.value)}>
              {projects == null ? <option>Loading…</option> : !projects.length ? <option value="">No projects</option> : projects.map((p) => <option key={p} value={p}>{p}</option>)}
            </select></label>
          <label className="field nc-field"><span className="field-label">Noun</span>
            <select className="input select nc-select" value={noun} onChange={(e) => setNoun(e.target.value)}>
              <option value="" disabled>Select a noun…</option>
              {nouns.map((n) => <option key={n} value={n}>{n}</option>)}
            </select></label>
          <fieldset className="nc-radio-group" role="radiogroup" aria-label="View mode">
            <label className="nc-radio"><input type="radio" name="action" value="describe" checked={action === "describe"} onChange={() => setAction("describe")} /> View</label>
            <label className="nc-radio"><input type="radio" name="action" value="edit" checked={action === "edit"} onChange={() => setAction("edit")} /> Edit</label>
          </fieldset>
          <button className="nc-btn nc-btn-primary" type="button" onClick={reDescribe}><Icon name="refresh" />Refresh</button>
        </div>
      </section>

      <section className="panel nc-card">
        <div className="panel-head"><Icon name="grid" /><span className="panel-title">Schema</span></div>
        <div className="panel-body">
          {schema.status === "empty" ? <StateBlock kind="empty" icon="noun" title="No schema loaded" message="Choose a project and noun to view or edit its field schema." /> : null}
          {schema.status === "loading" ? <StateBlock kind="loading" title="Describing noun…" /> : null}
          {schema.status === "error" ? <StateBlock kind="error" title="Could not describe this noun" message={schema.message} /> : null}
          {schema.status === "ok" ? (
            action === "edit"
              ? <EditView key={noun + ":" + loadKey} project={project} noun={noun} data={schema.data} dateFormats={dateFormats} onChanged={reDescribe} />
              : <pre className="nc-output">{JSON.stringify(schema.data, null, 2)}</pre>
          ) : null}
        </div>
      </section>

      {project ? <RegisterPanel project={project} dateFormats={dateFormats} onRegistered={(nm) => { fetchJSON(`/noun/types/${enc(project)}`).then((ns) => setNouns(Array.isArray(ns) ? ns : [])); setNoun(nm); }} /> : null}
    </>
  );
}

mountOnAuth("noun-configure-root", (host) => createRoot(host).render(<NounConfigure />));
