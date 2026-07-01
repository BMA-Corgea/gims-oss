// frontend/runlog/tabs/AdverbsTab.jsx — port of renderAdverbEditor. One control per adverb type,
// chosen by ui[key].kind: ref_list → inline checkbox grid (options ship in ui[key].options, so this
// uses an inline grid, not the fetch-based RefCheckboxGrid), ref/tag → select, picture → disabled
// "controlled by pipeline", scalar → boolean/number/date/text by field_type. Gated by computeAdverbsGate
// (locked → save + inputs disabled). Save → POST /adverb/update {adverbs}.
import { useEffect, useState } from "react";
import { Icon, StateBlock } from "../../lib/ui.jsx";
import { toast } from "../../lib/api.js";
import { getAdverbs, updateAdverbs } from "../api.js";
import { computeAdverbsGate } from "../gate.js";

function AdverbControl({ ui, fieldType, value, onChange, disabled }) {
  const kind = ui.kind || "scalar";

  if (kind === "ref_list") {
    const selected = new Set(Array.isArray(value) ? value.map(String) : []);
    const toggle = (val, on) => { const n = new Set(selected); if (on) n.add(val); else n.delete(val); onChange([...n]); };
    return (
      <div className="checkbox-grid">
        {(ui.options || []).map((opt) => (
          <label className="cb" key={opt.value}>
            <input type="checkbox" disabled={disabled} checked={selected.has(String(opt.value))}
                   onChange={(e) => toggle(String(opt.value), e.target.checked)} />
            <span> {opt.label || opt.value}</span>
          </label>
        ))}
      </div>
    );
  }

  if (kind === "ref" || kind === "tag") {
    return (
      <select className="input select" disabled={disabled} value={value == null ? "" : String(value)}
              onChange={(e) => onChange(e.target.value || undefined)}>
        <option value="">(select)</option>
        {(ui.options || []).map((opt) => <option key={opt.value} value={opt.value}>{opt.label || opt.value}</option>)}
      </select>
    );
  }

  if (kind === "picture") {
    return <input className="input" type="text" disabled readOnly placeholder="Controlled by pipeline" value={value == null ? "" : value} />;
  }

  const ft = String(ui.field_type || fieldType || "string").toLowerCase();
  if (ft === "boolean") {
    return <input type="checkbox" disabled={disabled} checked={!!value} onChange={(e) => onChange(e.target.checked)} />;
  }
  return (
    <input className="input" disabled={disabled}
           type={ft === "number" ? "number" : (ft === "date" ? "date" : "text")}
           value={value == null ? "" : value}
           onChange={(e) => {
             let v = e.target.value;
             if (ft === "number") v = (v !== "" && Number.isFinite(Number(v))) ? Number(v) : undefined;
             onChange(v === "" ? undefined : v);
           }} />
  );
}

export function AdverbsTab({ project, group, runID }) {
  const [state, setState] = useState({ status: "loading" });
  const [working, setWorking] = useState({});
  const [gate, setGate] = useState({ allowed: true });
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let live = true;
    setState({ status: "loading" });
    Promise.all([
      getAdverbs(project, group, runID),
      computeAdverbsGate(project, group, runID),
    ]).then(([payload, g]) => {
      if (!live) return;
      setGate(g);
      setWorking({ ...((payload && typeof payload.adverbs === "object") ? payload.adverbs : {}) });
      setState({ status: "ok", payload });
    }).catch((e) => { if (live) setState({ status: "error", message: String(e.message || e) }); });
    return () => { live = false; };
  }, [project, group, runID, tick]);

  if (state.status === "loading") return <StateBlock kind="loading" title="Loading adverbs…" />;
  if (state.status === "error") return <StateBlock kind="error" title="Failed to load adverbs" message={state.message} />;

  const payload = state.payload || {};
  const types = Array.isArray(payload.available_types) ? payload.available_types : [];
  if (!payload.verb || !types.length) {
    return <StateBlock kind="empty" icon="info" title="No adverbs" message="This run has no adverbs defined in its schema." />;
  }
  const ui = payload.ui || {};
  const locked = !gate.allowed;

  const setVal = (key, v) => setWorking((w) => { const n = { ...w }; if (v === undefined) delete n[key]; else n[key] = v; return n; });
  const save = async () => {
    try { await updateAdverbs(project, group, runID, working); toast("Adverbs saved.", "ok"); setTick((t) => t + 1); }
    catch (e) { toast(`Failed to save adverbs: ${String(e.message || e)}`, "err"); }
  };

  return (
    <div className="adverb-editor">
      <h3 className="rw-adverb-title">Adverbs for {runID}</h3>
      <div className={"raw-upload-gate " + (gate.allowed ? "ok" : "locked")}>
        <Icon name={gate.allowed ? "check" : "lock"} />{" "}
        {gate.allowed
          ? <span><strong>Adverbs unlocked</strong> — Adverbs step is current.</span>
          : <span><strong>Adverbs locked</strong> — current step: <em>{gate.reason}</em>.</span>}
      </div>
      <div className={"adverb-form card" + (locked ? " is-locked" : "")}>
        <div className="adverb-rows">
          {types.map((t) => {
            const key = t.adverb;
            return (
              <div className="form-row" key={key} data-adverb={key}>
                <label>{key}</label>
                <div className="adverb-control">
                  <AdverbControl ui={ui[key] || { kind: "scalar", field_type: t.field_type || "string" }}
                                 fieldType={t.field_type} value={working[key]} disabled={locked}
                                 onChange={(v) => setVal(key, v)} />
                </div>
              </div>
            );
          })}
        </div>
        <div className="form-actions">
          <button type="button" className="btn" id="adv-save" disabled={locked}
                  title={locked ? "Locked until the Adverbs step is current." : undefined} onClick={save}>
            <Icon name="save" /> Save
          </button>
        </div>
      </div>
    </div>
  );
}
