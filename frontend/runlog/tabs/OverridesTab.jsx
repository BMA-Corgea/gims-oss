// frontend/runlog/tabs/OverridesTab.jsx — port of renderOverrideEditor. Lists a run's overrides
// (resolve / delete) and an add-form whose dynamic fields come from the selected conjunction type:
// scalar text/date inputs + ReferenceList fields as a RefCheckboxGrid. All mutations go through
// updateOverrides (POST /override/update {overrides}).
import { useEffect, useMemo, useState } from "react";
import { Icon, StateBlock } from "../../lib/ui.jsx";
import { toast } from "../../lib/api.js";
import { getOverrides, updateOverrides } from "../api.js";
import { RefCheckboxGrid } from "../components/RefCheckboxGrid.jsx";

// Dedupe fields by label; ReferenceList → ref descriptor (Run nouns get verb_group/verb_name params).
function fieldDescriptors(t, group, verb) {
  const out = [];
  const seen = new Set();
  const fields = Array.isArray(t.fields) ? t.fields : [];
  for (const f of fields) {
    if (typeof f === "string") {
      if (seen.has(f)) continue; seen.add(f);
      out.push({ kind: "scalar", label: f, isDate: f.toLowerCase() === "date" });
      continue;
    }
    if (f && typeof f === "object") {
      const label = f.label || f.name || "field";
      if (f.type === "reference" && f.mode === "ReferenceList") {
        if (seen.has(label)) continue; seen.add(label);
        const params = { ...(f.filters || {}) };
        if (f.reference_noun === "Run") { params.verb_group = group; if (verb) params.verb_name = verb; }
        out.push({ kind: "ref", label, noun: f.reference_noun, params });
        continue;
      }
      if (seen.has(label)) continue; seen.add(label);
      out.push({ kind: "scalar", label, isDate: label.toLowerCase() === "date" });
    }
  }
  return out;
}

function overrideExtras(ovr) {
  const extras = [];
  if (ovr.note) extras.push(`note: ${ovr.note}`);
  if (ovr.initials) extras.push(`initials: ${ovr.initials}`);
  if (ovr.date) extras.push(`date: ${ovr.date}`);
  if (Array.isArray(ovr.linked_submission) && ovr.linked_submission.length) extras.push(`linked_submission: ${ovr.linked_submission.join(", ")}`);
  if (Array.isArray(ovr["previous runs"]) && ovr["previous runs"].length) extras.push(`previous runs: ${ovr["previous runs"].join(", ")}`);
  if (Array.isArray(ovr["retest of"]) && ovr["retest of"].length) extras.push(`retest of: ${ovr["retest of"].join(", ")}`);
  return extras;
}

function OverrideRow({ idx, ovr, onResolve, onDelete }) {
  const extras = overrideExtras(ovr);
  const isResolved = (ovr.resolution || []).map((r) => r && r.note).filter(Boolean).length > 0;
  const isNotification = String(ovr.status || "").toLowerCase() === "notification";
  const resolveDisabled = isResolved || isNotification;
  return (
    <div className="override-row">
      <div className="override-main">
        <span className="badge">{idx}</span>
        <strong>{ovr.type || "Unknown"}</strong> → <em>{ovr.status || "Status?"}</em>
        {extras.length ? <span className="muted"> ({extras.join("; ")})</span> : null}
      </div>
      <div className="override-actions">
        <button type="button" className={"btn btn-small" + (resolveDisabled ? " disabled" : "")} disabled={resolveDisabled}
                title={resolveDisabled ? "Notification — no override needed" : undefined} onClick={onResolve}>Resolve</button>
        <button type="button" className="btn btn-small btn-danger" onClick={onDelete}>Delete</button>
      </div>
    </div>
  );
}

function AddOverrideForm({ types, project, group, runID, verb, onAdd }) {
  const [typeIdx, setTypeIdx] = useState(0);
  const t = types[typeIdx] || { fields: [] };
  const descs = useMemo(() => fieldDescriptors(t, group, verb), [typeIdx, group, verb]); // eslint-disable-line react-hooks/exhaustive-deps
  const [scalars, setScalars] = useState({});
  const [refs, setRefs] = useState({});

  useEffect(() => {
    const init = {};
    descs.forEach((d) => { if (d.kind === "scalar" && d.isDate) init[d.label] = new Date().toISOString().slice(0, 10); });
    setScalars(init);
    setRefs({});
  }, [typeIdx]); // eslint-disable-line react-hooks/exhaustive-deps

  const add = async () => {
    const payload = { run: runID, type: t.type, status: t.status, resolution: [] };
    Object.entries(scalars).forEach(([k, v]) => { if (String(v).trim() !== "") payload[k] = String(v).trim(); });
    Object.entries(refs).forEach(([k, arr]) => { if (arr && arr.length) payload[k] = arr; });
    await onAdd(payload);
    setTypeIdx(0);
  };

  return (
    <div className="override-form card">
      <div className="form-row">
        <label>Conjunction</label>
        <select id="ovr-type" className="input select" value={typeIdx} onChange={(e) => setTypeIdx(parseInt(e.target.value, 10))}>
          {types.map((tt, i) => <option key={i} value={i}>{tt.type} ({tt.status})</option>)}
        </select>
      </div>
      <div id="ovr-dynamic-fields">
        {descs.map((d) => (
          <div className={"form-row" + (d.kind === "ref" ? " ref-field" : "")} key={d.label}>
            <label>{d.label}</label>
            {d.kind === "ref"
              ? <RefCheckboxGrid project={project} noun={d.noun} params={d.params} label={d.label} onChange={(label, arr) => setRefs((r) => ({ ...r, [label]: arr }))} />
              : <input type={d.isDate ? "date" : "text"} className="input" data-key={d.label}
                       value={scalars[d.label] || ""} onChange={(e) => setScalars((s) => ({ ...s, [d.label]: e.target.value }))} />}
          </div>
        ))}
      </div>
      <div className="form-actions">
        <button type="button" className="btn" id="ovr-add" onClick={add}><Icon name="plus" /> Add Override</button>
      </div>
    </div>
  );
}

export function OverridesTab({ project, group, runID, verbName }) {
  const [state, setState] = useState({ status: "loading" });
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let live = true;
    setState({ status: "loading" });
    getOverrides(project, group, runID)
      .then((d) => { if (live) setState({ status: "ok", data: d }); })
      .catch((e) => { if (live) setState({ status: "error", message: String(e.message || e) }); });
    return () => { live = false; };
  }, [project, group, runID, tick]);

  const reload = () => setTick((t) => t + 1);

  if (state.status === "loading") return <StateBlock kind="loading" title="Loading overrides…" />;
  if (state.status === "error") return <StateBlock kind="error" title="Failed to load overrides" message={state.message} />;

  const data = state.data || {};
  const overrides = Array.isArray(data.conjunctions) ? data.conjunctions : [];
  const types = Array.isArray(data.available_types) ? data.available_types : [];
  const verb = data.verb || verbName || "";

  const apply = async (next, okMsg) => {
    try { await updateOverrides(project, group, runID, next); toast(okMsg, "ok"); reload(); }
    catch (e) { toast("Failed to update overrides", "err"); }
  };
  const resolve = (idx) => {
    const note = window.prompt("Resolution note?");
    if (!note) return;
    apply(overrides.map((o, i) => i === idx
      ? { ...o, resolution: [...(Array.isArray(o.resolution) ? o.resolution : []), { note }] } : o), "Override resolved.");
  };
  const del = (idx) => {
    if (!window.confirm("Delete this override?")) return;
    apply(overrides.filter((_, i) => i !== idx), "Override deleted.");
  };
  const addOverride = (payload) => apply([...overrides, payload], "Override added.");

  return (
    <div className="override-editor">
      <h3 className="rw-override-title">Overrides for {runID}</h3>
      <div className="override-list">
        {overrides.length
          ? overrides.map((ovr, i) => <OverrideRow key={i} idx={i} ovr={ovr} onResolve={() => resolve(i)} onDelete={() => del(i)} />)
          : <div className="muted">No overrides have been added for this run.</div>}
      </div>
      {types.length ? <AddOverrideForm types={types} project={project} group={group} runID={runID} verb={verb} onAdd={addOverride} /> : null}
    </div>
  );
}
