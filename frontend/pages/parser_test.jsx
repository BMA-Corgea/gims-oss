// frontend/pages/parser_test.jsx — Parser Test harness (Phase 6; tool pages T9).
// React port of the 1136-line vanilla parser_test.js (class ParserTestInterface): a developer harness
// to run a project's custom parsers and prepositional-phrase parsers and view the JSON result. Two
// side-by-side cards (Custom Parser, Prep Phrase Parser). Reuses parser_test.css (the .pt-*/.cards-
// container/.card/.status-dot/.result-area/.spinner/.script-select + #custom-*/#prep-* id contract).
//
// Byte-identical mutations (parsertestshot.py, real route, 0 console errors), under /api/parser_test:
//   POST /prephrase/expand/{project}    { pphrase_name, settings:[], user_values:{} }              (prep)
//   POST /test_parser/{project}/{name}?parser_type=custom_parser&exec_mode=native&verb_group=<g>&run_id=<r>  { params:{} }   (custom)
//   POST /test_parser/{project}/{name}?parser_type=prep_phrase_parser&exec_mode=native              { params:{...} }          (prep)
// custom run = JSON {verb_group,run_id} from the run select → query (not body); prep params from the
// settings form (checkbox→bool, number→Number|null, multiselect→[], select/text→string).
//
// FIX (the vanilla was broken): the page listed scripts via GET /custom/parser/list, which is NOT a
// real route (404 → dropdowns silently empty). Replaced with the working
// GET /api/parser_test/{list_custom_parsers,list_prepositional_phrases}?project= so the page functions.
import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { Icon } from "../lib/ui.jsx";
import { enc, fetchJSON, mountOnAuth, toast } from "../lib/api.js";

const API = "/api/parser_test";
const post = (url, body) => fetchJSON(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const KIND_TYPE = { bool: "checkbox", number: "number", text: "text", single: "select", multi: "multiselect" };

const optVals = (options) => (options || []).map((o, j) => String(o.value != null ? o.value : (o.label != null ? o.label : `opt_${j}`)));
function buildFields(payload) {
  return (payload.expanded || []).map((f) => ({ name: f.id, label: f.label || f.id, type: KIND_TYPE[f.kind] || "text", default: f.default, options: f.options || [] }));
}
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
function infoText(d) {
  let s = "Script Info:\n";
  s += `- Exists: ${d.exists}\n- Has TOOL spec: ${d.has_tool}\n- Has run(): ${d.has_run}\n- Valid: ${d.valid}\n`;
  if (d.tool_spec) s += `\nTOOL spec:\n${JSON.stringify(d.tool_spec, null, 2)}\n`;
  if (d.load_error) s += `\nLoad error: ${d.load_error}\n`;
  return s;
}
function successText(r) {
  let s = `Status: ${r.status || (r.ok ? "Success" : "Failed")}\n`;
  if (r.produced && r.produced.length) { s += "\nProduced files:\n"; r.produced.forEach((f) => { s += `  • ${f}\n`; }); }
  if (r.output_files) { s += "\nOutput file contents:\n"; for (const [fn, fd] of Object.entries(r.output_files)) { s += `\n--- ${fn} ---\n`; if (fd && fd.error) s += `Error: ${fd.error}\n`; else s += `${fd ? fd.content : ""}\n(${fd ? fd.size : 0} characters)\n`; } }
  if (r.post_doc) { const pd = r.post_doc; s += `\nPost-doc status: ${pd.status != null ? pd.status : ""}\n`; if (pd.error) s += `Post-doc error: ${pd.error}\n`; if (pd.return !== undefined) s += `Post-doc return: ${JSON.stringify(pd.return)}\n`; }
  if (r.logs && r.logs.length) { s += "\nLogs:\n"; r.logs.forEach((l) => { s += `${l}\n`; }); }
  return s;
}
const errBody = (r) => `Error: ${(r && r.error) || ""}\n` + ((r && r.traceback) ? `\nTraceback:\n${r.traceback}` : "");

function ResultArea({ id, result }) {
  if (!result) return <div className="result-area" id={id} style={{ display: "none" }} />;
  if (result.kind === "info") return <div className="result-area info" id={id}>{result.body}</div>;
  const icon = result.kind === "success" ? "check" : "warning";
  return (
    <div className={"result-area " + result.kind} id={id}>
      <div className="result-head"><Icon name={icon} /><span>{result.title}</span></div>
      <pre className="result-pre">{result.body}</pre>
    </div>
  );
}

function StatusDot({ idDot, idText, status }) {
  return <div className="status-indicator"><span className={"status-dot " + status.state} id={idDot} /><span className="status-text" id={idText}>{status.text}</span></div>;
}

// ── run the parser (shared) ──
async function runTest({ project, name, parserType, extraQuery, params }) {
  const qp = new URLSearchParams();
  qp.set("parser_type", parserType);
  qp.set("exec_mode", "native");
  for (const [k, v] of Object.entries(extraQuery || {})) qp.set(k, v);
  const endpoint = `${API}/test_parser/${enc(project)}/${enc(name)}?${qp.toString()}`;
  const res = await fetch(endpoint, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ params }) });
  const txt = await res.text();
  let r; try { r = JSON.parse(txt); } catch { r = { ok: false, error: "Non-JSON response from server", raw: txt }; }
  if (!res.ok && !(r && (r.error || r.ok === false))) r = { ok: false, error: `${res.status} ${res.statusText}` };
  return { res, r };
}

// ── Custom Parser card ──
function CustomCard({ project, parsers }) {
  const [name, setName] = useState("");
  const [status, setStatus] = useState({ state: "checking", text: "Checking…" });
  const [runs, setRuns] = useState([]);          // [{verb_group, run_id, verb, ...}]
  const [selRun, setSelRun] = useState("");      // JSON string {verb_group, run_id}
  const [result, setResult] = useState(null);
  const [running, setRunning] = useState(false);

  useEffect(() => { setName(""); setRuns([]); setSelRun(""); setResult(null); setStatus({ state: parsers.length ? "ready" : "error", text: parsers.length ? "Select a parser" : "No parsers found" }); }, [project, parsers]);

  const onSelect = async (n) => {
    setName(n); setRuns([]); setSelRun(""); setResult(null);
    if (!n) { setStatus({ state: "error", text: "Select a parser" }); return; }
    setStatus({ state: "checking", text: "Checking parser…" });
    let check = null;
    try { check = await fetchJSON(`${API}/check_parser/${enc(project)}/${enc(n)}?type=custom_parser`); } catch { check = { error: "Check failed" }; }
    setResult({ kind: "info", body: infoText(check || {}) });
    if (check && !check.error && check.exists && check.valid) {
      setStatus({ state: "ready", text: "Select a run" });
      try { const data = await fetchJSON(`${API}/check_parser/get_runs/${enc(project)}/${enc(n)}`); const list = Array.isArray(data) ? data : (data && data.runs) || []; setRuns(list.slice(0, 20)); }
      catch { setRuns([]); }
    } else setStatus({ state: "error", text: check && check.exists ? "Invalid parser" : "Parser not found" });
  };

  const test = async () => {
    if (!name || !selRun) { setStatus({ state: "error", text: "Select a run" }); return; }
    let ctx; try { ctx = JSON.parse(selRun); } catch { ctx = null; }
    if (!ctx || !ctx.verb_group || !ctx.run_id) { setStatus({ state: "error", text: "Invalid run selection" }); return; }
    setRunning(true); setResult(null);
    try {
      const { res, r } = await runTest({ project, name, parserType: "custom_parser", extraQuery: { verb_group: ctx.verb_group, run_id: ctx.run_id }, params: {} });
      if (res.ok && !(r && r.error)) setResult({ kind: "success", title: "Success", body: successText(r) });
      else setResult({ kind: "error", title: "Error", body: errBody({ error: (r && (r.detail || r.error)) || "Failed", traceback: r && r.traceback }) });
    } catch (e) { setResult({ kind: "error", title: "Error", body: errBody({ error: String(e.message || e) }) }); }
    finally { setRunning(false); }
  };

  return (
    <section className="panel card">
      <div className="panel-head card-header"><Icon name="parser" /><span className="panel-title">Custom Parser</span><StatusDot idDot="custom-dot" idText="custom-text" status={status} /></div>
      <div className="panel-body card-body">
        <p className="card-desc">Tests custom parser functionality.</p>
        <p className="file-path">Expected: <code id="custom-expected">{name ? `custom/parsers/${name}/${name}.py` : "custom/parsers/{parser_name}/{parser_name}.py"}</code></p>
        <label className="field-label" htmlFor="custom-select">Select Parser</label>
        <select id="custom-select" className="input select script-select" value={name} onChange={(e) => onSelect(e.target.value)}>
          <option value="">— Select a parser —</option>{parsers.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
        {runs.length ? (
          <div id="custom-run-wrap" style={{ marginTop: "12px" }}>
            <label className="field-label" htmlFor="custom-run-select">Select Run:</label>
            <select id="custom-run-select" className="input select script-select" value={selRun} onChange={(e) => setSelRun(e.target.value)}>
              <option value="">— Select a run —</option>
              {runs.map((rn, i) => { const val = JSON.stringify({ verb_group: rn.verb_group, run_id: rn.run_id }); return <option key={i} value={val}>{rn.run_id} ({rn.verb || rn.verb_group})</option>; })}
            </select>
          </div>
        ) : null}
        <button className="btn-primary test-button" id="custom-button" disabled={!selRun || running} onClick={test}>
          <span className="button-text">Test Custom Parser</span><div className={"spinner" + (running ? " active" : "")} id="custom-spinner" />
        </button>
        <ResultArea id="custom-result" result={result} />
      </div>
    </section>
  );
}

// ── Prep Phrase card ──
function PrepCard({ project, pphrases }) {
  const [name, setName] = useState("");
  const [status, setStatus] = useState({ state: "checking", text: "Checking…" });
  const [fields, setFields] = useState(null);    // null = no settings card; [] possible
  const [values, setValues] = useState({});
  const [info, setInfo] = useState("");
  const [confirmed, setConfirmed] = useState("");
  const [runEnabled, setRunEnabled] = useState(false);
  const [result, setResult] = useState(null);
  const [running, setRunning] = useState(false);

  useEffect(() => { setName(""); setFields(null); setValues({}); setInfo(""); setConfirmed(""); setRunEnabled(false); setResult(null); setStatus({ state: pphrases.length ? "ready" : "error", text: pphrases.length ? "Select a prepositional phrase" : "No prepositional phrases found" }); }, [project, pphrases]);

  const onSelect = async (n) => {
    setName(n); setFields(null); setValues({}); setInfo(""); setConfirmed(""); setRunEnabled(false); setResult(null);
    if (!n) { setStatus({ state: "error", text: "Select a prepositional phrase" }); return; }
    setStatus({ state: "checking", text: "Checking…" });
    let check = null;
    try { check = await fetchJSON(`${API}/check_parser/${enc(project)}/${enc(n)}?type=prep_phrase_parser`); } catch { check = { error: "Check failed" }; }
    setResult({ kind: "info", body: infoText(check || {}) });
    let payload = null;
    try { payload = await post(`${API}/prephrase/expand/${enc(project)}`, { pphrase_name: n, settings: [], user_values: {} }); } catch { payload = null; }
    if (!payload || payload.ok !== true) { setFields(null); setInfo("Settings not available for this pre-phrase."); setRunEnabled(true); setStatus({ state: "ready", text: "Ready to run" }); return; }
    const fl = buildFields(payload);
    if (!fl.length) { setFields([]); setInfo("This pre-phrase has no settings; you can run it directly."); setRunEnabled(true); setStatus({ state: "ready", text: "Ready (no settings)" }); return; }
    setFields(fl); setValues(initValues(fl)); setRunEnabled(false); setStatus({ state: "error", text: "Adjust settings then click Confirm" }); setInfo("");
  };

  const confirmSettings = () => { setConfirmed(JSON.stringify(collectParams(fields, values), null, 2)); setRunEnabled(true); setStatus({ state: "ready", text: "Ready with settings" }); setInfo("Settings confirmed."); };
  const resetSettings = () => { setValues(initValues(fields)); setConfirmed(""); setRunEnabled(false); setStatus({ state: "error", text: "Adjust settings then click Confirm" }); setInfo("Settings reset."); };

  const test = async () => {
    if (!name || !runEnabled) return;
    let params = (fields && fields.length) ? collectParams(fields, values) : {};
    if (!Object.keys(params).length) { try { params = JSON.parse(confirmed || "{}") || {}; } catch { params = {}; } }
    setRunning(true); setResult(null);
    try {
      const { res, r } = await runTest({ project, name, parserType: "prep_phrase_parser", params });
      if (res.ok && !(r && r.error)) setResult({ kind: "success", title: "Success", body: successText(r) });
      else setResult({ kind: "error", title: "Error", body: errBody({ error: (r && (r.detail || r.error)) || "Failed", traceback: r && r.traceback }) });
    } catch (e) { setResult({ kind: "error", title: "Error", body: errBody({ error: String(e.message || e) }) }); }
    finally { setRunning(false); }
  };

  const setField = (fn, v) => setValues((s) => ({ ...s, [fn]: v }));
  const toggleMulti = (fn, val, on) => setValues((s) => { const cur = new Set(s[fn] || []); if (on) cur.add(val); else cur.delete(val); return { ...s, [fn]: [...cur] }; });

  return (
    <section className="panel card">
      <div className="panel-head card-header"><Icon name="conjunction" /><span className="panel-title">Prepositional Phrase Parser</span><StatusDot idDot="prep-dot" idText="prep-text" status={status} /></div>
      <div className="panel-body card-body">
        <p className="card-desc">Tests prepositional phrase parsing functionality.</p>
        <p className="file-path">Expected: <code id="prep-expected">{name ? `custom/prepositional phrases/${name}/${name}.py` : "custom/prepositional phrases/{pphrase_name}/{pphrase_name}.py"}</code></p>
        <label className="field-label" htmlFor="prep-select">Select Prepositional Phrase</label>
        <select id="prep-select" className="input select script-select" value={name} onChange={(e) => onSelect(e.target.value)}>
          <option value="">— Select a prepositional phrase —</option>{pphrases.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
        {fields && fields.length ? (
          <div id="prep-settings-wrap" style={{ marginTop: "12px" }}>
            <div className="card" id="prep-settings-card">
              <div className="card-header"><strong>Pre-phrase settings</strong></div>
              <div className="card-body" id="prep-settings-body">
                <div className="muted" id="prep-settings-info">{info}</div>
                <form id="prep-settings-form" onSubmit={(e) => e.preventDefault()}>
                  {fields.map((f) => (
                    <div className="form-row" key={f.name} style={{ marginBottom: "8px" }}>
                      <label style={{ display: "block", fontWeight: 600 }} htmlFor={`pphrase-${f.name}`}>{f.label}</label>
                      {f.type === "checkbox" ? <input type="checkbox" id={`pphrase-${f.name}`} checked={!!values[f.name]} onChange={(e) => setField(f.name, e.target.checked)} />
                        : f.type === "number" ? <input type="number" id={`pphrase-${f.name}`} value={values[f.name] != null ? values[f.name] : ""} onChange={(e) => setField(f.name, e.target.value)} />
                        : f.type === "select" ? (
                          <select id={`pphrase-${f.name}`} value={values[f.name] != null ? values[f.name] : ""} onChange={(e) => setField(f.name, e.target.value)}>
                            {(f.options || []).map((o, j) => { const val = String(o.value != null ? o.value : (o.label != null ? o.label : `opt_${j}`)); return <option key={j} value={val}>{o.label != null ? o.label : o.value}</option>; })}
                          </select>
                        ) : f.type === "multiselect" ? (
                          <div className="multiselect-checkboxes" id={`pphrase-${f.name}`}>
                            {(f.options || []).map((o, j) => { const val = String(o.value != null ? o.value : (o.label != null ? o.label : `opt_${j}`)); return <label className="multiselect-option" key={j} id={`pphrase-${f.name}-${j}`}><input type="checkbox" checked={(values[f.name] || []).includes(val)} onChange={(e) => toggleMulti(f.name, val, e.target.checked)} /> {o.label != null ? o.label : o.value}</label>; })}
                          </div>
                        ) : <input type="text" id={`pphrase-${f.name}`} value={values[f.name] != null ? values[f.name] : ""} onChange={(e) => setField(f.name, e.target.value)} />}
                    </div>
                  ))}
                </form>
                <div style={{ display: "flex", gap: "8px", marginTop: "10px" }}>
                  <button type="button" className="btn btn-primary" id="prep-settings-confirm" onClick={confirmSettings}>Confirm settings</button>
                  <button type="button" className="btn" id="prep-settings-reset" onClick={resetSettings}>Reset</button>
                </div>
              </div>
            </div>
          </div>
        ) : (fields ? <div className="muted" id="prep-settings-info" style={{ marginTop: "12px" }}>{info}</div> : null)}
        <button className="btn-primary test-button" id="prep-button" disabled={!runEnabled || running} onClick={test}>
          <span className="button-text">Test Prep Phrase</span><div className={"spinner" + (running ? " active" : "")} id="prep-spinner" />
        </button>
        <ResultArea id="prep-result" result={result} />
      </div>
      <textarea id="prep-params-json" hidden readOnly value={confirmed} />
    </section>
  );
}

function ParserTest() {
  const [projects, setProjects] = useState(null);
  const [project, setProject] = useState("");
  const [parsers, setParsers] = useState([]);
  const [pphrases, setPphrases] = useState([]);

  const loadLists = async (p) => {
    try { const d = await fetchJSON(`${API}/list_custom_parsers?project=${enc(p)}`); setParsers((d.parsers || d.items || []).map((x) => (x && x.name) || x)); }
    catch { setParsers([]); toast("Failed to list parsers", "err"); }
    try { const d = await fetchJSON(`${API}/list_prepositional_phrases?project=${enc(p)}`); setPphrases((d.pphrases || []).map((x) => (x && x.name) || x)); }
    catch { setPphrases([]); toast("Failed to list prepositional phrases", "err"); }
  };

  useEffect(() => {
    fetchJSON(`${API}/check_parser/projects`).then((ps) => {
      const list = Array.isArray(ps) ? ps : [];
      setProjects(list);
      if (list.length) { setProject(list[0]); loadLists(list[0]); }
    }).catch(() => { setProjects([]); toast("Failed to load projects", "err"); });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const onProject = (p) => { setProject(p); setParsers([]); setPphrases([]); if (p) loadLists(p); };

  return (
    <>
      <section className="panel pt-toolbar">
        <div className="panel-head"><Icon name="folder" /><span className="panel-title">Project</span></div>
        <div className="panel-body pt-toolbar-row">
          <label className="field pt-project-field"><span className="field-label">Project</span>
            <select id="project-select" className="input select" value={project} onChange={(e) => onProject(e.target.value)}>
              {projects == null ? <option value="">Loading projects…</option> : !projects.length ? <option value="">No projects</option> : [<option key="" value="">Select a project…</option>, ...projects.map((p) => <option key={p} value={p}>{p}</option>)]}
            </select></label>
        </div>
      </section>

      <div className="cards-container">
        {project ? <CustomCard key={"c:" + project} project={project} parsers={parsers} /> : <section className="panel card" />}
        {project ? <PrepCard key={"p:" + project} project={project} pphrases={pphrases} /> : <section className="panel card" />}
      </div>
    </>
  );
}

mountOnAuth("parser-test-root", (host) => createRoot(host).render(<ParserTest />));
