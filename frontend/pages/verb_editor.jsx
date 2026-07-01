// frontend/pages/verb_editor.jsx — Verb Editor (Phase 6 React; editors track E5 — the superset).
// React port of the 739-line vanilla verb_editor.js: a toolbar (project/verb picker + Load/New) +
// a VIEW panel (description/group/status-workflow/data-entry schema) + an EDIT stack
// (name/description/group + log-schema sub-editor, instructions / raw-inputs / interp-tabs / parsers
// list editors, and the status workflow — buckets vs a drag-reorderable linear step list).
//
// Endpoints + payloads are byte-identical to the vanilla:
//   GET  /verb/projects · /verb/{project} · /verb/{project}/{verb} · /noun/valid-refs/{project}
//   POST /verb/{project}/{verb}  (new) | PUT /verb/{project}/{verb}  (existing)
//        { description, verb_group, data_entry_schema:{instructions, raw_data_inputs,
//          set_up_inputs:{noun_type_ref}, interpretation:{tabs, parsers}},
//          linear_status:{enabled,steps}, status_values:[] }
//   GET/POST /verb/log-schema/{project}/{group}  { primary_id, fields:{name:{type,required}} }
// The page reuses verb_editor.css verbatim (its .ve-* / .kv / .form-grid / .list-editor / .step-row /
// .badge / #log-schema-* class+id contract is reproduced here).
import { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { Icon, StateBlock } from "../lib/ui.jsx";
import { enc, fetchJSON, mountOnAuth, toast } from "../lib/api.js";

const jsonHeaders = { "Content-Type": "application/json" };

// ── list editor (instructions / raw inputs / interp tabs / parsers): bare inputs, .row + trash ──
function ListEditor({ items, onChange, addLabel, onAdd }) {
  return (
    <div className="panel-body list-editor">
      <ul>
        {items.map((val, idx) => (
          <li key={idx}>
            <div className="row">
              <input type="text" value={val} onChange={(e) => onChange(items.map((v, j) => (j === idx ? e.target.value : v)))} />
            </div>
            <button type="button" aria-label="Remove" onClick={() => onChange(items.filter((_, j) => j !== idx))}><Icon name="trash" /></button>
          </li>
        ))}
      </ul>
      <div className="ve-add">
        <button type="button" className="btn blue sm" onClick={onAdd}><Icon name="plus" />{addLabel}</button>
      </div>
    </div>
  );
}

// ── linear step type/source <select> (mirrors buildStepSelect) ──
const STEP_OPT_VALUE = (step) => {
  if (step.type === "gate") return "type:gate";
  if (step.type === "raw_upload" && step.source) return "raw::" + step.source;
  if (step.type === "interpretation" && step.source) return "interp::" + step.source;
  if (step.type === "adverb" && step.source) return "adverb::" + step.source;
  return "type:data_entry";
};

function LinearSteps({ steps, onChange, rawInputs, interpTabs, adverbKeys }) {
  const dragFrom = useRef(null);

  const setStep = (idx, patch) => onChange(steps.map((s, j) => (j === idx ? { ...s, ...patch } : s)));

  const onSelect = (idx, value) => {
    if (value.startsWith("type:")) {
      const t = value.split(":")[1];
      const type = t === "data_entry" ? "data_entry" : "gate";
      onChange(steps.map((s, j) => (j === idx
        ? { ...s, type, source: null, ...(type === "gate" ? { manual_complete: false } : {}) }
        : s)));
    } else if (value.startsWith("raw::")) setStep(idx, { type: "raw_upload", source: value.slice(5) });
    else if (value.startsWith("interp::")) setStep(idx, { type: "interpretation", source: value.slice(8) });
    else if (value.startsWith("adverb::")) setStep(idx, { type: "adverb", source: value.slice(8) });
  };

  const reorder = (to) => {
    const from = dragFrom.current;
    dragFrom.current = null;
    if (from == null || to == null || from === to) return;
    const next = steps.slice();
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);
    onChange(next);
  };

  return (
    <ul id="linear-steps-list" className="ve-steps">
      {steps.map((step, idx) => {
        const isGate = step.type === "gate";
        return (
          <li key={idx} className="step-row" draggable
              onDragStart={(e) => { dragFrom.current = idx; e.dataTransfer.setData("text/plain", String(idx)); e.currentTarget.classList.add("dragging"); }}
              onDragEnd={(e) => e.currentTarget.classList.remove("dragging")}
              onDragOver={(e) => { e.preventDefault(); e.currentTarget.classList.add("drag-over"); }}
              onDragLeave={(e) => e.currentTarget.classList.remove("drag-over")}
              onDrop={(e) => { e.preventDefault(); e.currentTarget.classList.remove("drag-over"); reorder(idx); }}>
            <span className="drag-handle" aria-hidden="true"><Icon name="dots" /></span>
            <select value={STEP_OPT_VALUE(step)} onChange={(e) => onSelect(idx, e.target.value)}>
              <option value="type:data_entry">Data Entry</option>
              <option value="type:gate">Gate (approval)</option>
              {rawInputs.filter(Boolean).map((r) => <option key={"raw::" + r} value={"raw::" + r}>Raw – {r}</option>)}
              {interpTabs.filter(Boolean).map((t) => <option key={"interp::" + t} value={"interp::" + t}>Interpretation – {t}</option>)}
              {adverbKeys.filter(Boolean).map((a) => <option key={"adverb::" + a} value={"adverb::" + a}>Adverb – {a}</option>)}
            </select>
            <label className="small">
              <input type="checkbox" checked={!!step.required} onChange={(e) => setStep(idx, { required: e.target.checked })} /> Required
            </label>
            <label className="small" style={isGate ? { visibility: "hidden" } : undefined}>
              <input type="checkbox" checked={isGate ? false : !!step.manual_complete} onChange={(e) => setStep(idx, { manual_complete: e.target.checked })} /> Manual
            </label>
            <button type="button" aria-label="Remove step" onClick={() => onChange(steps.filter((_, j) => j !== idx))}><Icon name="trash" /></button>
          </li>
        );
      })}
    </ul>
  );
}

// ── Log-schema sub-editor card (Edit Verb Group Log Schema) ──
// Existing-field rows use the vanilla render option set; new rows use the vanilla add-field set.
const LOG_TYPES_EXISTING = ["string", "int", "float", "date"];
const LOG_TYPES_NEW = ["string", "number", "float", "boolean", "date"];
const effLogType = (t, opts) => (opts.includes(t) ? t : opts[0]); // mirror the DOM-select fallback on save

function LogSchemaCard({ project, group, onClose }) {
  // rows: [{name, type, required, opts}] — opts pins which option set a row renders (existing vs new)
  const [rows, setRows] = useState([]);
  const [primaryId, setPrimaryId] = useState("");

  useEffect(() => {
    let live = true;
    fetchJSON(`/verb/log-schema/${enc(project)}/${enc(group)}`)
      .then((schema) => {
        if (!live) return;
        const rs = Object.entries(schema.fields || {}).map(([name, cfg]) => ({
          name, type: effLogType(cfg.type, LOG_TYPES_EXISTING), required: !!cfg.required, opts: LOG_TYPES_EXISTING,
        }));
        setRows(rs);
        setPrimaryId(schema.primary_id || "");
      })
      .catch((e) => { toast("Failed to load log schema: " + (e && e.message ? e.message : e), "err"); });
    return () => { live = false; };
  }, [project, group]);

  const setRow = (i, patch) => setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const namedRows = rows.map((r) => r.name.trim()).filter(Boolean);

  const save = async () => {
    const fields = {};
    let error = null;
    for (const r of rows) {
      const name = r.name.trim();
      const type = effLogType(r.type, r.opts);
      let required = r.required;
      if (!name) continue;
      if (name === primaryId) {
        required = true;
        if (type === "boolean" || type === "date") error = `Primary ID '${name}' cannot be type '${type}'`;
      }
      fields[name] = { type, required };
    }
    if (error) { toast(error, "err"); return; }
    try {
      await fetchJSON(`/verb/log-schema/${enc(project)}/${enc(group)}`, {
        method: "POST", headers: jsonHeaders, body: JSON.stringify({ primary_id: primaryId, fields }),
      });
      toast("Log schema saved", "ok");
      onClose();
    } catch (e) { toast("Failed to save log schema: " + (e && e.message ? e.message : e), "err"); }
  };

  return (
    <section id="log-schema-card" className="panel">
      <div className="panel-head"><Icon name="template" /><span className="panel-title">Log Schema for Verb Group</span></div>
      <div className="panel-body">
        <div className="ve-table-wrap">
          <table id="log-schema-table">
            <thead><tr><th>Field Name</th><th>Type</th><th>Required</th><th>Action</th></tr></thead>
            <tbody id="log-schema-body">
              {rows.map((r, i) => (
                <tr key={i}>
                  <td><input type="text" value={r.name} onChange={(e) => setRow(i, { name: e.target.value })} /></td>
                  <td>
                    <select value={r.type} onChange={(e) => setRow(i, { type: e.target.value })}>
                      {r.opts.map((t) => <option key={t} value={t}>{t}</option>)}
                    </select>
                  </td>
                  <td><input type="checkbox" checked={r.required} onChange={(e) => setRow(i, { required: e.target.checked })} /></td>
                  <td><button type="button" aria-label="Remove field" onClick={() => setRows((rs) => rs.filter((_, j) => j !== i))}><Icon name="trash" /></button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="ve-add">
          <button type="button" className="btn blue sm" onClick={() => setRows((rs) => [...rs, { name: "", type: "string", required: false, opts: LOG_TYPES_NEW }])}><Icon name="plus" />Add Field</button>
        </div>
        <div className="kv">
          <label>Primary ID</label>
          <select className="input select" value={namedRows.includes(primaryId) ? primaryId : ""} onChange={(e) => setPrimaryId(e.target.value)}>
            {namedRows.map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </div>
        <div className="actions">
          <button type="button" className="btn-primary" onClick={save}><Icon name="save" />Save</button>
          <button type="button" className="btn" onClick={onClose}><Icon name="close" />Cancel</button>
        </div>
      </div>
    </section>
  );
}

// ── linear helpers (mirror ensureLinearStepIds / validateLinearWorkflow) ──
function withStepIds(steps) {
  return steps.map((step, idx) => {
    if (step.id && String(step.id).trim()) return step;
    const base = `${step.type}_${(step.source || "step" + (idx + 1)).toString().toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
    return { ...step, id: base };
  });
}
function validateLinearWorkflow(steps, rawInputs, interpTabs) {
  const errors = [];
  if (!steps.some((s) => s.type === "data_entry")) errors.push("Linear workflow must include a Data Entry step.");
  const seenRaw = new Set(steps.filter((s) => s.type === "raw_upload").map((s) => s.source));
  new Set(rawInputs.filter(Boolean)).forEach((r) => { if (!seenRaw.has(r)) errors.push(`Missing step for Raw Input: ${r}`); });
  const seenTabs = new Set(steps.filter((s) => s.type === "interpretation").map((s) => s.source));
  new Set(interpTabs.filter(Boolean)).forEach((t) => { if (!seenTabs.has(t)) errors.push(`Missing step for Interpretation Tab: ${t}`); });
  const withIds = withStepIds(steps);
  const ids = withIds.map((s) => String(s.id || "").trim());
  if (ids.some((id) => !id)) errors.push("One or more steps have blank ids.");
  const dupMap = ids.reduce((a, id) => ((a[id] = (a[id] || 0) + 1), a), {});
  const dups = Object.keys(dupMap).filter((k) => dupMap[k] > 1);
  if (dups.length) errors.push("Duplicate step id(s): " + dups.join(", "));
  return errors;
}

// ── EDIT form: seeds from the loaded verb (or blank for New); keyed per verb so it reseeds ──
function EditForm({ project, session, verbGroups, onSaved, onCancel }) {
  const data = session.data || {};
  const schema = data.data_entry_schema || {};
  const isNew = !!session.isNew;

  const [verbName, setVerbName] = useState(isNew ? "" : session.verb);
  const [description, setDescription] = useState(data.description || "");
  // The vanilla <select> has only the existing groups (no blank option): an empty model shows
  // (and saves) the first group. Seed to that so new verbs default to the first group like before.
  const [groupSelect, setGroupSelect] = useState(data.verb_group || (verbGroups[0] || ""));
  const [groupCustom, setGroupCustom] = useState("");
  const [nounRef, setNounRef] = useState((schema.set_up_inputs || {}).noun_type_ref || "");
  const [validRefs, setValidRefs] = useState([]);
  const [instructions, setInstructions] = useState([...(schema.instructions || [])]);
  const [rawInputs, setRawInputs] = useState([...(schema.raw_data_inputs || [])]);
  const [interpTabs, setInterpTabs] = useState([...((schema.interpretation || {}).tabs || [])]);
  const [parsers, setParsers] = useState([...((schema.interpretation || {}).parsers || [])]);
  const adverbKeys = useMemo(() => Object.keys(data.adverb_schema || {}), [data]);

  const loadedLinear = !!(data.linear_status && data.linear_status.steps && data.linear_status.steps.length);
  const [statusMode, setStatusMode] = useState(loadedLinear ? "linear" : "buckets");
  const [linearSteps, setLinearSteps] = useState(
    loadedLinear ? JSON.parse(JSON.stringify(data.linear_status.steps || [])).map((s) => ({ manual_complete: false, ...s })) : []
  );

  const [logSchemaOpen, setLogSchemaOpen] = useState(false);
  const groupForLog = groupSelect || groupCustom.trim();

  useEffect(() => {
    let live = true;
    fetch(`/noun/valid-refs/${enc(project)}`).then((r) => (r.ok ? r.json() : null)).then((d) => {
      if (live && d) setValidRefs(d.valid_noun_types || []);
    }).catch(() => {});
    return () => { live = false; };
  }, [project]);

  const save = async () => {
    const verb = (isNew ? verbName.trim() : session.verb) || verbName.trim();
    if (!verb) { toast("Please enter a verb name", "err"); return; }
    let group = groupSelect;
    const custom = groupCustom.trim();
    if (custom) group = custom;
    if (!group) { toast("Please select or enter a verb group", "err"); return; }
    if (!nounRef) { toast("Please select a Noun Type Ref (required)", "err"); return; }

    const payload = {
      description,
      verb_group: group,
      data_entry_schema: {
        instructions,
        raw_data_inputs: rawInputs,
        set_up_inputs: { noun_type_ref: nounRef },
        interpretation: { tabs: interpTabs, parsers },
      },
    };
    if (statusMode === "buckets") {
      payload.linear_status = { enabled: false, steps: [] };
      payload.status_values = [];
    } else {
      const errs = validateLinearWorkflow(linearSteps, rawInputs, interpTabs);
      if (errs.length) { toast("Linear workflow invalid: " + errs.join("; "), "err"); return; }
      payload.linear_status = { enabled: true, steps: withStepIds(linearSteps) };
      payload.status_values = [];
    }

    try {
      await fetchJSON(`/verb/${enc(project)}/${enc(verb)}`, {
        method: isNew ? "POST" : "PUT", headers: jsonHeaders, body: JSON.stringify(payload),
      });
      toast(`Verb '${verb}' ${isNew ? "created" : "updated"} successfully`, "ok");
      onSaved(verb);
    } catch (err) {
      toast("Failed to save verb: " + (err && err.message ? err.message : err), "err");
    }
  };

  const openLogSchema = () => {
    if (!project) { toast("Set Project first.", "err"); return; }
    if (!groupForLog) { toast("Set Verb Group first.", "err"); return; }
    setLogSchemaOpen(true);
  };

  return (
    <div id="verb-editor" className="ve-stack">
      <section className="panel">
        <div className="panel-head"><Icon name="edit" /><span className="panel-title">Edit Verb</span></div>
        <div className="panel-body form-grid">
          <div className="form-field">
            <span className="field-label">Verb Name</span>
            <input className="input" type="text" value={verbName} onChange={(e) => setVerbName(e.target.value)} />
          </div>
          <div className="form-field">
            <span className="field-label">Description</span>
            <input className="input" type="text" value={description} onChange={(e) => setDescription(e.target.value)} />
          </div>
          <div className="form-field">
            <span className="field-label">Verb Group</span>
            <select className="input select" value={groupSelect} onChange={(e) => setGroupSelect(e.target.value)}>
              {verbGroups.map((g) => <option key={g} value={g}>{g}</option>)}
            </select>
          </div>
          <div className="form-field">
            <span className="field-label">New Group (optional)</span>
            <input className="input" type="text" placeholder="Enter new group name" value={groupCustom} onChange={(e) => setGroupCustom(e.target.value)} />
          </div>
          <div className="form-field full">
            <button type="button" className="btn blue" onClick={openLogSchema}><Icon name="template" />Edit Verb Group Log Schema</button>
          </div>
        </div>
      </section>

      {logSchemaOpen && groupForLog
        ? <LogSchemaCard project={project} group={groupForLog} onClose={() => setLogSchemaOpen(false)} />
        : null}

      <section className="panel">
        <div className="panel-head"><Icon name="file" /><span className="panel-title">Instructions</span></div>
        <ListEditor items={instructions} onChange={setInstructions} addLabel="Add Instruction" onAdd={() => setInstructions((a) => [...a, ""])} />
      </section>

      <section className="panel">
        <div className="panel-head"><Icon name="upload" /><span className="panel-title">Raw Data Inputs</span></div>
        <div className="panel-body list-editor">
          <ul>
            {rawInputs.map((val, idx) => (
              <li key={idx}>
                <div className="row"><input type="text" value={val} onChange={(e) => setRawInputs(rawInputs.map((v, j) => (j === idx ? e.target.value : v)))} /></div>
                <button type="button" aria-label="Remove" onClick={() => setRawInputs(rawInputs.filter((_, j) => j !== idx))}><Icon name="trash" /></button>
              </li>
            ))}
          </ul>
          <div className="ve-add"><button type="button" className="btn blue sm" onClick={() => setRawInputs((a) => [...a, ""])}><Icon name="plus" />Add Raw Input</button></div>
          <div className="kv">
            <label>Noun Type Ref</label>
            <select id="noun-ref" className="input select" value={nounRef} onChange={(e) => setNounRef(e.target.value)}>
              <option value="">-- Select a Noun Type --</option>
              {validRefs.map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </div>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head"><Icon name="parser" /><span className="panel-title">Interpretation</span></div>
        <div className="panel-body list-editor">
          <div className="kv"><span className="badge">Tabs</span></div>
          <ul>
            {interpTabs.map((val, idx) => (
              <li key={idx}>
                <div className="row"><input type="text" value={val} onChange={(e) => setInterpTabs(interpTabs.map((v, j) => (j === idx ? e.target.value : v)))} /></div>
                <button type="button" aria-label="Remove" onClick={() => setInterpTabs(interpTabs.filter((_, j) => j !== idx))}><Icon name="trash" /></button>
              </li>
            ))}
          </ul>
          <div className="ve-add"><button type="button" className="btn blue sm" onClick={() => setInterpTabs((a) => [...a, ""])}><Icon name="plus" />Add Tab</button></div>
          <div className="kv ve-sub-note"><span className="badge">Parsers</span></div>
          <ul>
            {parsers.map((val, idx) => (
              <li key={idx}>
                <div className="row"><input type="text" value={val} onChange={(e) => setParsers(parsers.map((v, j) => (j === idx ? e.target.value : v)))} /></div>
                <button type="button" aria-label="Remove" onClick={() => setParsers(parsers.filter((_, j) => j !== idx))}><Icon name="trash" /></button>
              </li>
            ))}
          </ul>
          <div className="ve-add"><button type="button" className="btn blue sm" onClick={() => setParsers((a) => [...a, ""])}><Icon name="plus" />Add Parser</button></div>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head"><Icon name="runlog" /><span className="panel-title">Status Workflow</span></div>
        <div className="panel-body">
          <div className="kv">
            <label htmlFor="status-mode">Mode</label>
            <select id="status-mode" className="input select" value={statusMode} onChange={(e) => setStatusMode(e.target.value)}>
              <option value="buckets">Buckets</option>
              <option value="linear">Linear Workflow</option>
            </select>
          </div>
          {statusMode === "buckets"
            ? <div className="small">Buckets mode: statuses are managed elsewhere. No overrides here.</div>
            : (
              <div>
                <LinearSteps steps={linearSteps} onChange={setLinearSteps} rawInputs={rawInputs} interpTabs={interpTabs} adverbKeys={adverbKeys} />
                <div className="ve-add"><button type="button" className="btn blue sm" onClick={() => setLinearSteps((a) => [...a, { type: "data_entry", source: null, required: false, manual_complete: false }])}><Icon name="plus" />Add Step</button></div>
              </div>
            )}
        </div>
      </section>

      <div className="actions">
        <button type="button" className="btn-primary" onClick={save}><Icon name="save" />Save Verb</button>
        <button type="button" className="btn" onClick={onCancel}><Icon name="close" />Cancel</button>
      </div>
    </div>
  );
}

// ── VIEW panel ──
function VerbViewer({ verb, data, onEdit }) {
  const schema = data.data_entry_schema || {};
  const isLinear = !!(data.linear_status && data.linear_status.steps && data.linear_status.steps.length);
  const instructions = schema.instructions || [];
  return (
    <section id="verb-viewer" className="panel">
      <div className="panel-head"><Icon name="verb" /><span className="panel-title">Viewing Verb: <span className="ve-view-name">{verb}</span></span></div>
      <div className="panel-body">
        <div className="kv"><label>Description</label><div>{data.description || ""}</div></div>
        <div className="kv"><label>Group</label><div>{data.verb_group || ""}</div></div>

        <div className="ve-sub">
          <div className="ve-sub-title">Status Workflow</div>
          <div className="kv"><label>Mode</label><span className="badge">{isLinear ? "Linear" : "Buckets"}</span></div>
          {isLinear ? (
            <div>
              <div className="small ve-sub-note">Linear Steps</div>
              <ul className="ve-view-steps">
                {(data.linear_status.steps || []).map((s, i) => {
                  const src = (s.type === "raw_upload" || s.type === "interpretation") && s.source ? ` • ${s.source}` : "";
                  const manual = s.manual_complete ? " • manual" : "";
                  return <li key={i}>{`${i + 1}. ${s.type}${src}${s.required ? " (required)" : ""}${manual}`}</li>;
                })}
              </ul>
            </div>
          ) : <div className="small">Buckets mode enabled.</div>}
        </div>

        <div className="ve-sub">
          <div className="ve-sub-title">Data Entry Schema</div>
          <div id="view-instructions">
            <span className="badge">Instructions</span>
            {instructions.length ? instructions.map((txt, i) => <div className="child-line" key={i}>{txt}</div>) : <div>(none)</div>}
          </div>
          <div className="kv"><label>Raw Inputs</label><span>{(schema.raw_data_inputs || []).join(", ")}</span></div>
          <div className="kv"><label>Noun Ref</label><span>{(schema.set_up_inputs || {}).noun_type_ref || ""}</span></div>
          <div className="kv"><label>Interp Tabs</label><span>{(schema.interpretation && schema.interpretation.tabs) ? schema.interpretation.tabs.join(", ") : ""}</span></div>
        </div>

        <div className="actions"><button type="button" className="btn-primary" onClick={onEdit}><Icon name="edit" />Edit</button></div>
      </div>
    </section>
  );
}

// ── root ──
function VerbEditor() {
  const [projects, setProjects] = useState(null);
  const [project, setProject] = useState("");
  const [verbs, setVerbs] = useState({});       // {name: def}
  const [selectedVerb, setSelectedVerb] = useState("");
  // session: null (empty) | {verb, data} (view/edit existing) | {isNew:true} (edit new)
  const [session, setSession] = useState(null);
  const [mode, setMode] = useState("empty");    // empty | view | edit

  const verbNames = useMemo(() => Object.keys(verbs), [verbs]);
  const verbGroups = useMemo(() => {
    const s = new Set();
    Object.values(verbs).forEach((v) => { if (v.verb_group) s.add(v.verb_group); });
    return Array.from(s).sort();
  }, [verbs]);

  const refetchVerbs = async (proj) => {
    const v = await fetchJSON(`/verb/${enc(proj)}`);
    setVerbs(v || {});
    return v || {};
  };

  useEffect(() => {
    fetchJSON("/verb/projects").then((ps) => {
      setProjects(ps || []);
      if (Array.isArray(ps) && ps.length) setProject(ps[0]);
    }).catch((e) => { setProjects([]); toast("Failed to load projects: " + (e && e.message ? e.message : e), "err"); });
  }, []);

  // project change → reset + load its verbs
  useEffect(() => {
    if (!project) return;
    setSession(null); setMode("empty"); setSelectedVerb("");
    refetchVerbs(project).then((v) => {
      const names = Object.keys(v);
      setSelectedVerb(names.length ? names[0] : "");
    }).catch((e) => toast("Failed to load verbs: " + (e && e.message ? e.message : e), "err"));
  }, [project]);

  const loadVerb = async (verbName) => {
    const name = verbName != null ? verbName : selectedVerb;
    if (!name) return;
    try {
      const data = await fetchJSON(`/verb/${enc(project)}/${enc(name)}`);
      setSession({ verb: name, data });
      setMode("view");
    } catch (e) { toast("Failed to load verb: " + (e && e.message ? e.message : e), "err"); }
  };

  const newVerb = () => { setSession({ isNew: true }); setMode("edit"); };

  const onSaved = async (verb) => {
    await refetchVerbs(project);
    setSelectedVerb(verb);
    await loadVerb(verb);
  };

  return (
    <>
      <section className="panel ve-toolbar">
        <div className="panel-head"><Icon name="verb" /><span className="panel-title">Verb definitions</span></div>
        <div className="panel-body ve-toolbar-row">
          <label className="field ve-field"><span className="field-label">Project</span>
            <select id="project" className="input select" value={project} onChange={(e) => setProject(e.target.value)}>
              {projects == null ? <option>Loading…</option> : !projects.length ? <option value="">No projects</option> : projects.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </label>
          <label className="field ve-field"><span className="field-label">Verb</span>
            <select id="verb-select" className="input select" value={selectedVerb}
                    onChange={(e) => { setSelectedVerb(e.target.value); setSession(null); setMode("empty"); }}>
              {verbNames.map((v) => <option key={v} value={v}>{v}</option>)}
            </select>
          </label>
          <div className="ve-toolbar-actions">
            <button type="button" className="btn" onClick={() => loadVerb()}><Icon name="download" />Load</button>
            <button type="button" className="btn-primary" onClick={newVerb}><Icon name="plus" />New Verb</button>
          </div>
        </div>
      </section>

      {mode === "empty" ? (
        <section className="panel">
          <StateBlock kind="empty" icon="verb" title="No verb loaded">
            <p className="gims-state-msg">Pick a project and verb above, then <strong>Load</strong> to review it — or start a fresh <strong>New Verb</strong>.</p>
          </StateBlock>
        </section>
      ) : null}

      {mode === "view" && session && session.data
        ? <VerbViewer verb={session.verb} data={session.data} onEdit={() => setMode("edit")} />
        : null}

      {mode === "edit" && session ? (
        <EditForm key={session.isNew ? "__new" : session.verb}
                  project={project} session={session} verbGroups={verbGroups}
                  onSaved={onSaved}
                  onCancel={() => { if (session.isNew) { setSession(null); setMode("empty"); } else loadVerb(session.verb); }} />
      ) : null}
    </>
  );
}

mountOnAuth("verb-editor-root", (host) => createRoot(host).render(<VerbEditor />));
