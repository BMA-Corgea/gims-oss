// frontend/pages/verb_workbench.jsx — Verb Workbench (Phase 6 React; tool pages T3).
// React port of the 470-line vanilla verb_workbench.js: a run create/edit workbench — pick a project +
// verb, the log config drives a dynamic metadata form (Create or Edit-an-existing-run), with Validate /
// Save and an "Update Status" (Status.json refresh) control + an "Open in Runlog →" deep link. Reuses
// noun_workbench.css (the shared form-workbench stylesheet; .top-controls/.segmented/.form-grid/
// .form-field/.messages/.msg/.required-badge + #id contract reproduced).
//
// Byte-identical mutations (verbworkshot.py, real route, 0 console errors), under /api/verb_workbench:
//   POST /{p}/{verb}/validate            { <field>: <trimmed value>, ... }
//   POST /{p}/{verb}/create              { ...same form payload }
//   POST /{p}/{verb}/update/{runId}      { ...same form payload }
//   POST /{p}/{verb}/status/refresh/{id} (no body)
// The form payload is every log-config field except `test_type`, value-trimmed (collectForm parity).
import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { Icon } from "../lib/ui.jsx";
import { enc, fetchJSON, mountOnAuth } from "../lib/api.js";

const API = "/api/verb_workbench";
const post = (url, body) => fetchJSON(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const getRowTestType = (r) => (r.test_type != null ? r.test_type : (r.testType != null ? r.testType : (r.verb != null ? r.verb : (r.type != null ? r.type : null))));
function normalizeIdValue(row, pid) {
  if (Object.prototype.hasOwnProperty.call(row, pid)) return row[pid];
  const u = pid.replace(/ /g, "_"), s = pid.replace(/_/g, " ");
  if (Object.prototype.hasOwnProperty.call(row, u)) return row[u];
  if (Object.prototype.hasOwnProperty.call(row, s)) return row[s];
  return undefined;
}

function VerbWorkbench() {
  const [projects, setProjects] = useState([]);
  const [project, setProject] = useState("");
  const [verbs, setVerbs] = useState(null); // null=not loaded
  const [verb, setVerb] = useState("");
  const [logConfig, setLogConfig] = useState(null); // {group, primary_id, fields}
  const [verbGroup, setVerbGroup] = useState(null);
  const [mode, setMode] = useState("create");
  const [runs, setRuns] = useState([]);       // [{value, label}] for edit run select
  const [selRun, setSelRun] = useState("");
  const [currentRunId, setCurrentRunId] = useState(null);
  const [form, setForm] = useState({});       // {field: value}
  const [messages, setMessages] = useState([]); // [{text, type, link?}]
  const [refreshId, setRefreshId] = useState("");
  const [hint, setHint] = useState("");

  const renderedFields = logConfig ? Object.keys(logConfig.fields || {}).filter((n) => n !== "test_type") : [];
  const collect = () => Object.fromEntries(renderedFields.map((n) => [n, (form[n] != null ? String(form[n]) : "").trim()]));

  const runlogLink = (rid) => {
    const r = rid || currentRunId;
    if (!project || !verbGroup || !r) return null;
    return { href: `/runlog_workbench?project=${enc(project)}&group=${enc(verbGroup)}&run_id=${enc(r)}`, label: "Open in Runlog →" };
  };
  const say = (text, type = "ok", link = null) => setMessages([{ text, type, link }]);
  const sayMany = (msgs) => setMessages(msgs);

  // ── loaders ──
  const loadVerbs = async (p) => {
    setVerbs(null);
    try { const vs = await fetchJSON(`${API}/${enc(p)}`); setVerbs(Array.isArray(vs) ? vs : []); }
    catch { setVerbs([]); }
  };
  const fetchVerbSchema = (v) => fetchJSON(`/verb/${enc(project)}/${enc(v)}`).catch(() => null);
  const syncHint = async (v) => {
    if (!project || !v) { setHint(""); return; }
    const schema = await fetchVerbSchema(v);
    const isLinear = !!(schema && schema.linear_status && schema.linear_status.enabled && (schema.linear_status.steps || []).length);
    setHint(isLinear ? "" : "Verb is in Buckets mode; refresh writes default Status.json (no linear steps).");
  };

  const buildRunOptions = async (v, cfg) => {
    const pid = cfg && cfg.primary_id;
    if (!cfg) { setRuns([{ value: "", label: "(no log config)" }]); return; }
    if (!pid) { setRuns([{ value: "", label: "(missing primary_id in log config)" }]); return; }
    const allRows = await fetchJSON(`${API}/${enc(project)}/${enc(v)}/runs`).catch(() => []);
    const rows = (Array.isArray(allRows) ? allRows : []).filter((r) => String(getRowTestType(r)) === String(v));
    if (!rows.length) { setRuns([{ value: "", label: "No runs for this verb" }]); return; }
    const opts = [{ value: "", label: "Select a run" }];
    rows.forEach((r) => { const id = normalizeIdValue(r, pid); if (id == null) return; opts.push({ value: String(id), label: r.name ? `${id} — ${r.name}` : String(id) }); });
    setRuns(opts);
  };

  const reloadSchema = async (v, nextMode) => {
    setMessages([]);
    const theVerb = v != null ? v : verb;
    if (!theVerb) return;
    const cfg = await fetchJSON(`${API}/${enc(project)}/${enc(theVerb)}/log_config`).catch(() => null);
    setLogConfig(cfg);
    const grp = cfg ? (cfg.group != null ? cfg.group : (cfg.verb_group != null ? cfg.verb_group : (cfg.verbGroup != null ? cfg.verbGroup : null))) : null;
    setVerbGroup(grp);
    // seed an empty form for the new schema
    const fields = (cfg && cfg.fields) || {};
    setForm(Object.fromEntries(Object.keys(fields).filter((n) => n !== "test_type").map((n) => [n, ""])));
    setCurrentRunId(null);
    if ((nextMode || mode) === "edit") await buildRunOptions(theVerb, cfg);
    await syncHint(theVerb);
  };

  // ── init ──
  useEffect(() => {
    fetchJSON(`${API}/projects`).then((ps) => {
      const list = Array.isArray(ps) ? ps : [];
      setProjects(list);
      if (list.length) { setProject(list[0]); loadVerbs(list[0]); }
    }).catch(() => setProjects([]));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const onProject = (p) => { setProject(p); setVerb(""); setLogConfig(null); setForm({}); setMessages([]); setRuns([]); loadVerbs(p); };
  const onVerb = (v) => { setVerb(v); if (v) reloadSchema(v); else { setLogConfig(null); setForm({}); } };
  const onMode = async (m) => {
    setMode(m);
    if (m === "edit") { if (verb) { if (logConfig) await buildRunOptions(verb, logConfig); else await reloadSchema(verb, "edit"); } }
    else { setCurrentRunId(null); setForm((f) => Object.fromEntries(renderedFields.map((n) => [n, ""]))); setMessages([]); }
  };

  const loadRun = async () => {
    setMessages([]);
    if (!selRun) { say("Pick a run to load", "warn"); return; }
    const rec = await fetchJSON(`${API}/${enc(project)}/${enc(verb)}/run/${enc(selRun)}`).catch(() => null);
    if (!rec) { say("Failed to load run", "error"); return; }
    setForm(Object.fromEntries(renderedFields.map((n) => [n, rec[n] != null ? String(rec[n]) : ""])));
    setCurrentRunId(selRun); setRefreshId(selRun);
    say(`Loaded ${selRun}`, "ok", runlogLink(selRun));
  };

  const validate = async () => {
    setMessages([]);
    const v = await post(`${API}/${enc(project)}/${enc(verb)}/validate`, collect()).catch((e) => ({ ok: false, errors: [String(e.message || e)] }));
    if (v.ok) say("Valid ✓", "ok");
    else sayMany((v.errors || []).map((e) => ({ text: e, type: "error" })));
  };

  const save = async () => {
    setMessages([]);
    const payload = collect();
    try {
      if (mode === "edit" && currentRunId) {
        const r = await post(`${API}/${enc(project)}/${enc(verb)}/update/${enc(currentRunId)}`, payload);
        if (r.ok) say(`Updated ${r.id}`, "ok", runlogLink(r.id)); else sayMany((r.errors || []).map((e) => ({ text: e, type: "error" })));
      } else {
        const r = await post(`${API}/${enc(project)}/${enc(verb)}/create`, payload);
        if (r.ok) { setCurrentRunId(r.id); if (mode === "edit") await buildRunOptions(verb, logConfig); if (r.id) setRefreshId(r.id); say(`Created ${r.id}`, "ok", runlogLink(r.id)); }
        else sayMany((r.errors || []).map((e) => ({ text: e, type: "error" })));
      }
    } catch { say("Save failed", "error"); }
  };

  const refreshStatus = async () => {
    setMessages([]);
    const runId = (refreshId || "").trim() || currentRunId;
    if (!project || !verb) { say("Select a project and verb.", "warn"); return; }
    if (!runId) { say("Enter a Run ID or load a run first.", "warn"); return; }
    try {
      const res = await fetchJSON(`${API}/${enc(project)}/${enc(verb)}/status/refresh/${enc(runId)}`, { method: "POST" });
      say(`Status updated for '${runId}' • steps: ${res.steps != null ? res.steps : "0"}`, "ok", runlogLink(runId));
    } catch (e) { say(`Failed to update status: ${String(e.message || e)}`, "error"); }
  };

  const setField = (n, val) => setForm((f) => ({ ...f, [n]: val }));
  const pid = logConfig && logConfig.primary_id;

  return (
    <>
      <section className="panel">
        <div className="panel-body top-controls">
          <label className="field control-group"><span className="field-label">Project</span>
            <select id="project-select" className="input select" value={project} onChange={(e) => onProject(e.target.value)}>
              {!projects.length ? <option value="">—</option> : projects.map((p) => <option key={p} value={p}>{p}</option>)}
            </select></label>
          <label className="field control-group"><span className="field-label">Verb</span>
            <select id="verbSelect" className="input select" disabled={verbs == null || !verbs.length} value={verb} onChange={(e) => onVerb(e.target.value)}>
              {verbs == null ? <option value="">Loading verbs…</option> : !verbs.length ? <option value="">No verbs</option> : [<option key="" value="">Select a verb</option>, ...verbs.map((v) => <option key={v} value={v}>{v}</option>)]}
            </select></label>
          <button id="reloadSchemaBtn" className="btn ghost" onClick={() => reloadSchema()}><Icon name="refresh" />Reload</button>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head"><Icon name="edit" /><span className="panel-title">Mode</span></div>
        <div className="panel-body inline">
          <div className="segmented" role="radiogroup" aria-label="Form mode">
            <label><input type="radio" name="form-mode" value="create" checked={mode === "create"} onChange={() => onMode("create")} /><span>Create</span></label>
            <label><input type="radio" name="form-mode" value="edit" checked={mode === "edit"} onChange={() => onMode("edit")} /><span>Edit</span></label>
          </div>
          {mode === "edit" ? (
            <div id="editLoadArea" className="inline">
              <select id="runSelect" className="input select" value={selRun} onChange={(e) => { setSelRun(e.target.value); setCurrentRunId(e.target.value || null); }}>
                {runs.length ? runs.map((o, i) => <option key={i} value={o.value}>{o.label}</option>) : <option value="">Select a verb first</option>}
              </select>
              <button id="loadRunBtn" className="btn" onClick={loadRun}>Load</button>
            </div>
          ) : null}
        </div>
      </section>

      <section className="panel">
        <div className="panel-head split">
          <span className="panel-title"><Icon name="verb" /> Run Metadata</span>
          <div className="inline">
            <button id="validateBtn" className="btn ghost" onClick={validate}><Icon name="check" />Validate</button>
            <button id="saveBtn" className="btn-primary primary" onClick={save}><Icon name="save" />Save</button>
          </div>
        </div>
        <div className="panel-body">
          <form id="dynamicForm" className="form-grid" onSubmit={(e) => e.preventDefault()}>
            {renderedFields.map((name) => {
              const info = (logConfig.fields || {})[name] || {};
              return (
                <div className="form-field" key={name}>
                  <label>{name}{info.required ? <span className="required-badge">required</span> : null}</label>
                  <input type="text" className="input" data-name={name} placeholder={name === pid ? "Primary ID (required)" : undefined}
                         value={form[name] != null ? form[name] : ""} onChange={(e) => setField(name, e.target.value)} />
                </div>
              );
            })}
          </form>
          <div id="formMessages" className="messages">
            {messages.map((m, i) => (
              <div className={`msg ${m.type}`} key={i}>{m.text}{m.link ? <a href={m.link.href} target="_blank" rel="noreferrer" style={{ marginLeft: "12px", textDecoration: "underline", fontWeight: "bold", color: "inherit" }}>{m.link.label}</a> : null}</div>
            ))}
          </div>

          {project && verb ? (
            <div id="refresh-status-block" className="kv" style={{ marginTop: "16px" }}>
              <label htmlFor="refresh-run-id">Update Status for Run</label>
              <div className="inline" style={{ gap: "8px" }}>
                <input id="refresh-run-id" className="input" type="text" placeholder="Enter Run ID (primary id)" value={refreshId} onChange={(e) => setRefreshId(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") refreshStatus(); }} />
                <button id="refresh-status-btn" className="btn" onClick={refreshStatus}><Icon name="refresh" />Update Status</button>
                <span id="refresh-status-hint" className="small muted">{hint}</span>
              </div>
            </div>
          ) : null}
        </div>
      </section>
    </>
  );
}

mountOnAuth("verb-workbench-root", (host) => createRoot(host).render(<VerbWorkbench />));
