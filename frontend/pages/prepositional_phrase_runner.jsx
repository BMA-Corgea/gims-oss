// frontend/pages/prepositional_phrase_runner.jsx — Prepositional Phrase Runner (Phase 6; tool pages T8).
// React port of the 1062-line vanilla prepositional_phrase_runner.js (class PrephraseRunner): pick a
// project + a prepositional-phrase parser, configure it via a settings form expanded from the backend,
// run it, and browse/download the files it produces. Thin front end over /api/parser_test (all JSON, no
// multipart). Reuses prepositional_phrase_runner.css (the .runner-card/.status-dot/.result-area/.file-row
// + #id contract is reproduced — settings/outputs cards kept id-based + inline styles, byte-faithful).
//
// Byte-identical mutations (prepshot.py, real route, 0 console errors), under /api/parser_test:
//   POST /prephrase/expand/{project}            { pphrase_name, settings:[], user_values:{} }
//   POST /test_parser/{project}/{name}?parser_type=prep_phrase_parser&exec_mode=native   { params }
// `params` is collected from the live form (checkbox→bool, number→Number|null, multiselect→[], select/
// text→string), falling back to the confirmed JSON if empty — matching collectFormValues. A 202 with
// {trigger|handled} enters the e-signature wait (window 'prephrase:signature_resolved' or 30s timeout).
// Reads: GET /check_parser/projects · /list_prepositional_phrases?project= · /check_parser/{p}/{n}?type=
// prep_phrase_parser · /pphrase_outputs/{p}/tree. File downloads are <a download> (browser GET).
import { Fragment, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { Icon } from "../lib/ui.jsx";
import { enc, fetchJSON, mountOnAuth } from "../lib/api.js";

const API = "/api/parser_test";
const post = (url, body) => fetchJSON(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const KIND_TYPE = { bool: "checkbox", number: "number", text: "text", single: "select", multi: "multiselect" };

function buildUiSpec(payload) {
  const fields = (payload.expanded || []).map((f) => ({
    name: f.id, label: f.label || f.id, type: KIND_TYPE[f.kind] || "text", default: f.default, options: f.options || [],
  }));
  return { title: `Configure "${payload.pphrase_name || "pre-phrase"}"`, fields };
}
const optVals = (options) => (options || []).map((o, j) => String(o.value != null ? o.value : (o.label != null ? o.label : `opt_${j}`)));
function initValues(fields) {
  const v = {};
  for (const f of fields) {
    if (f.type === "checkbox") v[f.name] = !!f.default;
    else if (f.type === "multiselect") v[f.name] = Array.isArray(f.default) ? f.default.map(String) : [];
    else if (f.type === "number") v[f.name] = (f.default == null) ? "" : String(f.default);
    else if (f.type === "select") { const opts = optVals(f.options); const d = String(f.default); v[f.name] = opts.includes(d) ? d : (opts[0] || ""); }
    else v[f.name] = f.default != null ? String(f.default) : "";
  }
  return v;
}
function collectParams(fields, values) {
  const params = {};
  for (const f of fields) {
    const v = values[f.name];
    if (f.type === "checkbox") params[f.name] = !!v;
    else if (f.type === "number") { const n = (v === "" || v == null) ? null : Number(v); params[f.name] = Number.isNaN(n) ? null : n; }
    else if (f.type === "multiselect") params[f.name] = Array.isArray(v) ? v : [];
    else params[f.name] = v != null ? v : "";
  }
  return params;
}

// ── result text builders (plaintext into .result-area, white-space:pre-wrap) ──
function successText(result) {
  let s = "✅ Prepositional Phrase executed successfully!\n\n";
  s += `Status: ${result.status || (result.ok ? "Success" : "Failed")}\n`;
  if (result.produced && result.produced.length) { s += "\nProduced files:\n"; result.produced.forEach((f) => { s += `  • ${f}\n`; }); }
  if (result.output_files) {
    s += "\nOutput file contents:\n";
    for (const [fn, fd] of Object.entries(result.output_files)) { s += `\n--- ${fn} ---\n`; if (fd && fd.error) s += `Error: ${fd.error}\n`; else s += `${fd ? fd.content : ""}\n(${fd ? fd.size : 0} characters)\n`; }
  }
  if (result.post_doc) {
    const pd = result.post_doc;
    s += `\nPost-doc status: ${pd.status != null ? pd.status : ""}\n`;
    if (pd.error) s += `Post-doc error: ${pd.error}\n`;
    if (pd.return !== undefined) s += `Post-doc return: ${JSON.stringify(pd.return)}\n`;
  }
  if (result.logs && result.logs.length) { s += "\nLogs:\n"; result.logs.forEach((l) => { s += `${l}\n`; }); }
  return s;
}
const errorText = (r) => `❌ Prepositional Phrase execution failed!\n\nError: ${(r && r.error) || ""}\n` + ((r && r.traceback) ? `\nTraceback:\n${r.traceback}` : "");
function infoText(data) {
  let s = "Script Info:\n";
  s += `- Exists: ${data.exists}\n- Has TOOL spec: ${data.has_tool}\n- Has run(): ${data.has_run}\n- Valid: ${data.valid}\n`;
  if (data.tool_spec) s += `\nTOOL spec:\n${JSON.stringify(data.tool_spec, null, 2)}\n`;
  if (data.load_error) s += `\nLoad error: ${data.load_error}\n`;
  return s;
}

// ── outputs tree (recursive) ──
function OutputsNode({ node, project, root }) {
  if (!node) return null;
  if (node.type === "file") {
    return (
      <div className="file-row" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "8px" }}>
        <a href={`${API}/pphrase_outputs/${enc(project)}/download?path=${enc(node.path)}`} download={node.name}>{node.name}</a>
        <span className="muted">{(node.size != null ? node.size : 0).toLocaleString()} bytes · {node.mtime != null ? node.mtime : ""}</span>
      </div>
    );
  }
  const isRoot = node.path === "" || node.path === ".";
  const children = node.children || [];
  return (
    <details open={isRoot || root}>
      <summary style={{ cursor: "pointer", fontWeight: 600 }}>{node.name || "(root)"}</summary>
      <div style={{ paddingLeft: "12px" }}>
        {children.length ? children.map((c, i) => <OutputsNode key={i} node={c} project={project} />) : <div className="muted">— empty —</div>}
      </div>
    </details>
  );
}

function PrephraseRunner() {
  const [projects, setProjects] = useState(null);
  const [project, setProject] = useState("");
  const [pphrases, setPphrases] = useState([]);
  const [selected, setSelected] = useState("");
  const [status, setStatus] = useState({ state: "checking", text: "Initializing..." });
  const [expected, setExpected] = useState(null);
  const [uiSpec, setUiSpec] = useState(null);     // {title, fields, empty?} | null
  const [formValues, setFormValues] = useState({});
  const [settingsInfo, setSettingsInfo] = useState("");
  const [confirmedJson, setConfirmedJson] = useState("");
  const [runEnabled, setRunEnabled] = useState(false);
  const [result, setResult] = useState(null);     // {kind, text} | null
  const [running, setRunning] = useState(false);
  const [outputs, setOutputs] = useState({ status: "idle", tree: null });

  const updateStatus = (state, text) => setStatus({ state, text });
  const showResult = (kind, text) => setResult({ kind, text });
  const renderResult = (r) => { if (r && r.error) showResult("error", errorText(r)); else { showResult("success", successText(r)); loadOutputs(project); } };

  // ── signature listeners (faithful: orch:trigger debug + prephrase:signature_complete) ──
  useEffect(() => {
    const onTrigger = () => { /* debug-only in the vanilla */ };
    const onComplete = (e) => { const d = e.detail || {}; if (d.ok) renderResult(d.result || d); else showResult("error", errorText(d.result || d || { error: "Signature failed" })); };
    window.addEventListener("orch:trigger", onTrigger);
    window.addEventListener("prephrase:signature_complete", onComplete);
    return () => { window.removeEventListener("orch:trigger", onTrigger); window.removeEventListener("prephrase:signature_complete", onComplete); };
  }, [project]); // eslint-disable-line react-hooks/exhaustive-deps

  const waitForSignature = () => new Promise((resolve) => {
    const onResolved = (e) => { if (e.detail && e.detail.completed) { cleanup(); resolve(e.detail.result); } };
    const timer = setTimeout(() => { cleanup(); resolve({ error: "Signature process timeout" }); }, 30000);
    function cleanup() { window.removeEventListener("prephrase:signature_resolved", onResolved); clearTimeout(timer); }
    window.addEventListener("prephrase:signature_resolved", onResolved);
  });

  // ── loaders ──
  const loadOutputs = async (p) => {
    if (!p) { setOutputs({ status: "idle", tree: null }); return; }
    setOutputs({ status: "loading", tree: null });
    try { const data = await fetchJSON(`${API}/pphrase_outputs/${enc(p)}/tree`); setOutputs({ status: "ok", tree: data.tree || null }); }
    catch (e) { setOutputs({ status: "error", tree: null, error: String(e.message || e) }); }
  };
  const loadPphrases = async (p) => {
    updateStatus("checking", "Loading prepositional phrases...");
    try {
      const data = await fetchJSON(`${API}/list_prepositional_phrases?project=${enc(p)}`);
      const list = (data && data.pphrases || []).map((x) => (x && x.name) || x);
      setPphrases(list);
      updateStatus(list.length ? "ready" : "error", list.length ? "Select a prepositional phrase" : "No prepositional phrases found");
    } catch { setPphrases([]); updateStatus("error", "Failed to load prepositional phrases"); }
  };

  useEffect(() => {
    fetchJSON(`${API}/check_parser/projects`).then((ps) => {
      const list = Array.isArray(ps) ? ps : [];
      setProjects(list);
      if (list.length) { setProject(list[0]); loadPphrases(list[0]); loadOutputs(list[0]); }
      else updateStatus("error", "No projects");
    }).catch(() => { setProjects([]); updateStatus("error", "Failed to load projects"); });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const resetInterface = () => { setSelected(""); setUiSpec(null); setFormValues({}); setConfirmedJson(""); setSettingsInfo(""); setRunEnabled(false); setResult(null); setExpected(null); };
  const onProject = (p) => { setProject(p); resetInterface(); if (p) { loadPphrases(p); loadOutputs(p); } else { setPphrases([]); setOutputs({ status: "idle", tree: null }); } };

  // ── select a prepositional phrase → check + expand ──
  const onSelect = async (name) => {
    setSelected(name);
    setExpected(name ? `custom/prepositional phrases/${name}/${name}.py` : null);
    setResult(null);
    if (!name) { setUiSpec(null); setRunEnabled(false); updateStatus("error", "Select a prepositional phrase"); return; }
    setRunEnabled(false);
    updateStatus("checking", "Checking prepositional phrase...");
    let check = null;
    try { check = await fetchJSON(`${API}/check_parser/${enc(project)}/${enc(name)}?type=prep_phrase_parser`); } catch { check = { error: "Check failed" }; }
    if (check && !check.error && check.exists) { showResult("info", infoText(check)); }
    else { showResult("info", infoText(check || {})); }
    // expand settings
    let payload = null;
    try { payload = await post(`${API}/prephrase/expand/${enc(project)}`, { pphrase_name: name, settings: [], user_values: {} }); } catch { payload = null; }
    if (!payload || payload.ok !== true) { setUiSpec(null); setSettingsInfo("Settings not available for this pre-phrase."); setRunEnabled(true); updateStatus("ready", "Ready to run"); return; }
    const spec = buildUiSpec(payload);
    if (!spec.fields.length) { setUiSpec({ ...spec, empty: true }); setSettingsInfo("This pre-phrase has no settings; you can run it directly."); setRunEnabled(true); updateStatus("ready", "Ready (no settings)"); return; }
    setUiSpec(spec); setFormValues(initValues(spec.fields)); setConfirmedJson(""); setSettingsInfo(""); setRunEnabled(false); updateStatus("error", "Configure settings then click Confirm");
  };

  const confirmSettings = () => {
    const params = collectParams(uiSpec.fields, formValues);
    setConfirmedJson(JSON.stringify(params, null, 2));
    setRunEnabled(true); updateStatus("ready", "Ready to run"); setSettingsInfo("Settings confirmed. You can now run the pre-phrase.");
  };
  const resetSettings = () => { setFormValues(initValues(uiSpec.fields)); setConfirmedJson(""); setSettingsInfo("Settings cleared."); setRunEnabled(false); updateStatus("error", "Configure settings before running"); };

  const run = async () => {
    if (!selected || !runEnabled) return;
    let params = uiSpec && uiSpec.fields ? collectParams(uiSpec.fields, formValues) : {};
    if (!Object.keys(params).length) { try { params = JSON.parse(confirmedJson || "{}") || {}; } catch { params = {}; } }
    const endpoint = `${API}/test_parser/${enc(project)}/${enc(selected)}?parser_type=prep_phrase_parser&exec_mode=native`;
    setRunning(true); setResult(null);
    try {
      const res = await fetch(endpoint, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ params }) });
      const txt = await res.text();
      let r; try { r = JSON.parse(txt); } catch { r = { ok: false, error: "Non-JSON response from server", raw: txt }; }
      if (res.status === 202 && (r.trigger || r.handled)) {
        updateStatus("checking", "Waiting for e-signature...");
        showResult("info", "⏳ E-signature required. Please complete the signature dialog.\n\nThe operation will continue automatically after signing.");
        const finalResult = await waitForSignature();
        if (finalResult && !finalResult.error) renderResult(finalResult); else showResult("error", errorText(finalResult || { error: "Signature failed" }));
      } else if (!res.ok) {
        showResult("error", errorText({ error: (r && (r.detail || r.error)) || `${res.status} ${res.statusText}`, traceback: r && r.traceback }));
      } else { renderResult(r); }
    } catch (e) { showResult("error", errorText({ error: String(e.message || e) })); }
    finally { setRunning(false); }
  };

  // Ctrl+Enter to run
  useEffect(() => {
    const onKey = (e) => { if ((e.ctrlKey || e.metaKey) && e.key === "Enter" && runEnabled && !running) { e.preventDefault(); run(); } };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }); // eslint-disable-line react-hooks/exhaustive-deps

  const setField = (name, v) => setFormValues((s) => ({ ...s, [name]: v }));
  const toggleMulti = (name, val, on) => setFormValues((s) => { const cur = new Set(s[name] || []); if (on) cur.add(val); else cur.delete(val); return { ...s, [name]: [...cur] }; });

  return (
    <>
      <section className="panel pp-toolbar">
        <div className="panel-body">
          <label className="field"><span className="field-label">Project</span>
            <select id="project-select" className="input select" value={project} onChange={(e) => onProject(e.target.value)}>
              {projects == null ? <option value="">Loading projects…</option> : !projects.length ? <option value="">No projects</option> : projects.map((p) => <option key={p} value={p}>{p}</option>)}
            </select></label>
        </div>
      </section>

      <section className="panel runner-card">
        <div className="panel-head split">
          <span className="panel-title"><Icon name="terminal" /> Prepositional Phrase Configuration</span>
          <div className="status-indicator" id="prep-status">
            <span className={"status-dot " + status.state} id="prep-dot" /><span className="status-text" id="prep-text">{status.text}</span>
          </div>
        </div>

        <div className="panel-body card-body">
          <div className="info-section">
            <p className="description">Select and configure a prepositional phrase parser to process your data.</p>
            <p className="file-path"><span className="path-label">Expected Path:</span>
              <code id="prep-expected">{expected || "custom/prepositional phrases/{pphrase_name}/{pphrase_name}.py"}</code></p>
          </div>

          <div className="selection-section field">
            <label htmlFor="prep-select" className="field-label">Choose Prepositional Phrase</label>
            <select id="prep-select" className="input select phrase-select" value={selected} onChange={(e) => onSelect(e.target.value)}>
              <option value="">-- Select a prepositional phrase --</option>
              {pphrases.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </div>

          {uiSpec && !uiSpec.empty ? (
            <div id="prep-settings-wrap" style={{ marginTop: "12px" }}>
              <div className="card" id="prep-settings-card">
                <div className="card-header"><strong>{uiSpec.title}</strong></div>
                <div className="card-body" id="prep-settings-body">
                  <form id="prep-settings-form" onSubmit={(e) => e.preventDefault()}>
                    {uiSpec.fields.map((f) => (
                      <div className="form-row" key={f.name} style={{ marginBottom: "8px" }}>
                        <label style={{ display: "block", fontWeight: 600, marginBottom: "2px" }} htmlFor={`pphrase-${f.name}`}>{f.label}</label>
                        {f.type === "checkbox" ? (
                          <input type="checkbox" id={`pphrase-${f.name}`} checked={!!formValues[f.name]} onChange={(e) => setField(f.name, e.target.checked)} />
                        ) : f.type === "number" ? (
                          <input type="number" id={`pphrase-${f.name}`} value={formValues[f.name] != null ? formValues[f.name] : ""} onChange={(e) => setField(f.name, e.target.value)} />
                        ) : f.type === "select" ? (
                          <select id={`pphrase-${f.name}`} value={formValues[f.name] != null ? formValues[f.name] : ""} onChange={(e) => setField(f.name, e.target.value)}>
                            {(f.options || []).map((o, j) => { const val = String(o.value != null ? o.value : (o.label != null ? o.label : `opt_${j}`)); return <option key={j} value={val}>{o.label != null ? o.label : o.value}</option>; })}
                          </select>
                        ) : f.type === "multiselect" ? (
                          <div className="multiselect-checkboxes">
                            {(f.options || []).map((o, j) => { const val = String(o.value != null ? o.value : (o.label != null ? o.label : `opt_${j}`)); return (
                              <label className="multiselect-option" key={j} id={`pphrase-${f.name}-${j}`}>
                                <input type="checkbox" checked={(formValues[f.name] || []).includes(val)} onChange={(e) => toggleMulti(f.name, val, e.target.checked)} /> {o.label != null ? o.label : o.value}
                              </label>
                            ); })}
                          </div>
                        ) : (
                          <input type="text" id={`pphrase-${f.name}`} value={formValues[f.name] != null ? formValues[f.name] : ""} onChange={(e) => setField(f.name, e.target.value)} />
                        )}
                      </div>
                    ))}
                  </form>
                  <div style={{ marginTop: "10px", display: "flex", gap: "8px" }}>
                    <button type="button" className="btn-primary" id="prep-settings-confirm" onClick={confirmSettings}>Confirm</button>
                    <button type="button" className="btn" id="prep-settings-reset" onClick={resetSettings}>Reset</button>
                  </div>
                  <div className="muted" id="prep-settings-info">{settingsInfo}</div>
                </div>
              </div>
            </div>
          ) : null}
          {uiSpec && uiSpec.empty ? <div id="prep-settings-wrap" style={{ marginTop: "12px" }}><div className="muted" id="prep-settings-info">{settingsInfo}</div></div> : null}

          <button className="btn-primary run-button" id="prep-button" disabled={!runEnabled || running} onClick={run}>
            <span className="button-icon"><Icon name="play" /></span>
            <span className="button-text">Run Prepositional Phrase</span>
            <div className={"spinner" + (running ? " active" : "")} id="prep-spinner" />
          </button>

          <div className="result-section">
            <div className="result-header" id="result-header" style={{ display: result ? "" : "none" }}>
              <h3>Execution Results</h3>
              <button className="btn sm clear-button" id="clear-results" onClick={() => setResult(null)}><Icon name="close" />Clear</button>
            </div>
            <div className={"result-area" + (result ? " " + result.kind : "")} id="prep-result">{result ? result.text : ""}</div>
          </div>

          {project ? (
            <div id="pphrase-outputs-wrap" style={{ marginTop: "12px" }}>
              <div className="card">
                <div className="card-header" style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                  <strong>Outputs</strong>
                  <button type="button" className="btn sm" id="pphrase-outputs-refresh" style={{ marginLeft: "auto" }} onClick={() => loadOutputs(project)}><Icon name="refresh" />Refresh</button>
                </div>
                <div className="card-body" id="pphrase-outputs-body">
                  {outputs.status === "loading" ? <div className="muted">Loading…</div>
                    : outputs.status === "error" ? <div className="error">{outputs.error}</div>
                    : outputs.tree ? ((outputs.tree.children || []).length ? (outputs.tree.children || []).map((c, i) => <OutputsNode key={i} node={c} project={project} root />) : <div className="muted">— empty —</div>)
                    : <div className="muted">— empty —</div>}
                </div>
              </div>
            </div>
          ) : null}
        </div>
      </section>
    </>
  );
}

mountOnAuth("prep-runner-root", (host) => createRoot(host).render(<PrephraseRunner />));
