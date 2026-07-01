// frontend/pages/custom_upload.jsx — Custom Parser Manager (Phase 6 React; tool pages T6).
// React port of the 643-line vanilla custom_upload.js: upload custom Python parsers / prepositional
// phrases for a project, assign parsers to verbs, and unassign/unlink them. Reuses custom_upload.css
// (the .cu-*/.parser-*/.assign-form/.empty-state/.file-input-* + #id contract is reproduced).
//
// Byte-identical mutations (cuploadshot.py, real route, 0 console errors), under /custom_upload:
//   POST   /{p}/upload_parser   FormData { file, kind, overwrite, explicit_name, verb? }  (verb only for kind=parser)
//   POST   /{p}/assign?verb=<v>&parser_name=<n>                 (no body)
//   DELETE /{p}/unassign?parser_name=<n>&verb=<v>              (no body)
//   DELETE /{p}/{parserName}                                   (no body; backend only unlinks)
// Reads: GET /custom_upload/{projects, rds_mode, {p}/{verbs, assignments, list}}. RDS/hosted mode locks
// the upload card (server also 403s). RESTRUCTURE: the vanilla's vestigial static `disabled` attrs on the
// upload controls (never re-enabled → form was stuck) are not reproduced; the form is functional unless RDS.
import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { Icon } from "../lib/ui.jsx";
import { enc, fetchJSON, mountOnAuth, toast } from "../lib/api.js";

const API = "/custom_upload";
const notify = (m, t = "info") => toast(m, t === "success" ? "ok" : t === "error" || t === "warning" ? (t === "error" ? "err" : "err") : "info");
function fmtSize(bytes) {
  if (!bytes) return "0 B";
  const k = 1024, sizes = ["B", "KB", "MB", "GB"], i = Math.floor(Math.log(bytes) / Math.log(k));
  return Math.round((bytes / Math.pow(k, i)) * 100) / 100 + " " + sizes[i];
}

function CustomUpload() {
  const [projects, setProjects] = useState([]);
  const [project, setProject] = useState("");
  const [verbs, setVerbs] = useState([]);
  const [assignments, setAssignments] = useState({}); // {verb: [parserNames]}
  const [parsers, setParsers] = useState([]);
  const [combined, setCombined] = useState(null);     // null=loading, []=empty
  const [rds, setRds] = useState(false);

  // upload form
  const [fileName, setFileName] = useState("No file selected");
  const [parserName, setParserName] = useState("");
  const [kind, setKind] = useState("parser");
  const [verbValue, setVerbValue] = useState("");
  const [overwrite, setOverwrite] = useState(false);
  const fileRef = useRef(null);

  // assign form
  const [assignParserSel, setAssignParserSel] = useState("");
  const [assignVerbSel, setAssignVerbSel] = useState("");

  const [deleteTarget, setDeleteTarget] = useState(null);

  const loadVerbsAndAssignments = async (p) => {
    try {
      const vd = await fetchJSON(`${API}/${enc(p)}/verbs`).catch(() => ({ verbs: [] }));
      setVerbs(vd.verbs || []);
      const ad = await fetchJSON(`${API}/${enc(p)}/assignments`).catch(() => ({ assignments: {} }));
      setAssignments(ad.assignments || {});
    } catch { setVerbs([]); setAssignments({}); }
  };
  const loadParsers = async (p) => {
    setCombined(null);
    try {
      const data = await fetchJSON(`${API}/${enc(p)}/list`);
      const ps = data.parsers || [], pph = data.pphrases || [];
      setParsers(ps);
      setCombined([...ps.map((x) => ({ ...x, kind: "parser" })), ...pph.map((x) => ({ ...x, kind: "pphrase" }))]);
    } catch (e) { setCombined([]); notify(`Error loading custom scripts: ${e.message || e}`, "error"); }
  };
  const reload = async (p) => { await loadVerbsAndAssignments(p); await loadParsers(p); };

  useEffect(() => {
    fetchJSON(`${API}/projects`).then((ps) => {
      const list = Array.isArray(ps) ? ps : [];
      setProjects(list);
      const url = new URLSearchParams(location.search).get("project");
      const p = list.includes(url) ? url : (list[0] || "");
      setProject(p); if (p) reload(p);
    }).catch((e) => notify(`Error loading projects: ${e.message || e}`, "error"));
    fetchJSON(`${API}/rds_mode`).then((d) => setRds(!!(d && d.rds_enabled))).catch(() => {});
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const onProject = (p) => { setProject(p); if (p) reload(p); else { setVerbs([]); setAssignments({}); setCombined([]); } };

  const upload = async (e) => {
    e.preventDefault();
    if (!project) { notify("Please select a project", "warning"); return; }
    const file = fileRef.current && fileRef.current.files[0];
    if (!file) { notify("Please select a file", "warning"); return; }
    if (kind === "parser" && !verbValue) { notify("Please select a verb for parser type", "warning"); return; }
    if (!parserName.trim()) { notify("Please enter a parser name", "warning"); return; }
    const fd = new FormData();
    fd.append("file", file);
    fd.append("kind", kind);
    fd.append("overwrite", overwrite);
    fd.append("explicit_name", parserName.trim());
    if (verbValue && kind === "parser") fd.append("verb", verbValue);
    try {
      const r = await fetch(`${API}/${enc(project)}/upload_parser`, { method: "POST", body: fd });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.detail || "Upload failed");
      notify("Parser uploaded successfully!", "success");
      setFileName("No file selected"); setParserName(""); setOverwrite(false); if (fileRef.current) fileRef.current.value = "";
      reload(project);
    } catch (err) { notify(`Upload failed: ${err.message || err}`, "error"); }
  };

  const assign = async (e) => {
    e.preventDefault();
    if (!project) { notify("Please select a project", "warning"); return; }
    if (!assignParserSel || !assignVerbSel) { notify("Please select both parser and verb", "warning"); return; }
    const params = new URLSearchParams({ verb: assignVerbSel, parser_name: assignParserSel });
    try {
      const r = await fetch(`${API}/${enc(project)}/assign?${params.toString()}`, { method: "POST" });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.detail || "Assignment failed");
      notify(`Parser "${assignParserSel}" assigned to verb "${assignVerbSel}"`, "success");
      setAssignParserSel(""); setAssignVerbSel(""); reload(project);
    } catch (err) { notify(`Assignment failed: ${err.message || err}`, "error"); }
  };

  const unassign = async (parser, verb) => {
    if (!project) { notify("Please select a project", "warning"); return; }
    const params = new URLSearchParams({ parser_name: parser, verb });
    try {
      const r = await fetch(`${API}/${enc(project)}/unassign?${params.toString()}`, { method: "DELETE" });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.detail || "Unassignment failed");
      notify(`Parser "${parser}" unassigned from verb "${verb}"`, "success"); reload(project);
    } catch (err) { notify(`Unassignment failed: ${err.message || err}`, "error"); }
  };

  const del = async (parser) => {
    if (!project) { notify("Please select a project", "warning"); return; }
    try {
      const r = await fetch(`${API}/${enc(project)}/${enc(parser)}`, { method: "DELETE" });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.detail || "Delete failed");
      notify(`Parser "${parser}" unlinked successfully`, "success"); reload(project);
    } catch (err) { notify(`Delete failed: ${err.message || err}`, "error"); }
  };

  const assignedVerbsFor = (name) => Object.entries(assignments).filter(([, ps]) => ps.includes(name)).map(([v]) => v);

  return (
    <>
      <section className="panel cu-toolbar">
        <div className="panel-head"><Icon name="parser" /><span className="panel-title">Custom Parsers</span></div>
        <div className="panel-body cu-toolbar-row">
          <label className="field cu-field"><span className="field-label">Project</span>
            <select id="projectSelect" className="input select" value={project} onChange={(e) => onProject(e.target.value)}>
              {!projects.length ? <option value="">Loading…</option> : [<option key="" value="">Select project</option>, ...projects.map((p) => <option key={p} value={p}>{p}</option>)]}
            </select></label>
        </div>
      </section>

      <section className="panel cu-upload" id="uploadCard" style={rds ? { opacity: 0.6, pointerEvents: "none", position: "relative" } : { position: "relative" }}>
        {rds ? (
          <div className="cu-upload-disabled" id="uploadDisabledOverlay" style={{ display: "flex" }}>
            <span className="cu-upload-disabled-mark icon-chip round"><Icon name="lock" /></span>
            <h3 className="cu-upload-disabled-title">Uploads Disabled in Hosted Mode</h3>
            <p className="cu-upload-disabled-msg">Uploading executable Python code to a hosted server works only until the environment refreshes — the files live on an ephemeral filesystem that can be replaced at any time. This restriction prevents reliance on unstable, short-lived code deployments and protects the system from unverified execution.</p>
          </div>
        ) : null}
        <div className="cu-upload-inner">
          <div className="panel-head"><Icon name="upload" /><span className="panel-title">Upload New Parser</span></div>
          <form id="uploadForm" className="panel-body" onSubmit={upload}>
            <div className="form-row">
              <div className="form-group">
                <label className="field-label">Python File (.py)</label>
                <div className="file-input-wrapper">
                  <input type="file" id="fileInput" ref={fileRef} className="file-input" accept=".py" required
                         onChange={(e) => { const f = e.target.files[0]; setFileName(f ? f.name : "No file selected"); if (f && !parserName) { /* placeholder hint only */ } }} />
                  <label htmlFor="fileInput" className="file-input-label"><Icon name="file" />Choose Python file</label>
                  <div id="fileName" className={"file-name" + (fileName !== "No file selected" ? " has-file" : "")}>{fileName}</div>
                </div>
              </div>
              <div className="form-group">
                <label className="field-label">Parser Name</label>
                <input type="text" id="parserName" className="input" placeholder="Auto-detect from filename" value={parserName} onChange={(e) => setParserName(e.target.value)} />
              </div>
            </div>
            <div className="form-row">
              <div className="form-group">
                <label className="field-label">Type</label>
                <select id="parserKind" className="input select" value={kind} onChange={(e) => { setKind(e.target.value); if (e.target.value !== "parser") setVerbValue(""); }}>
                  <option value="parser">Parser (must be assigned to verbs)</option>
                  <option value="pphrase">Prepositional Phrase</option>
                </select>
              </div>
              {kind === "parser" ? (
                <div className="form-group" id="verbAssignmentGroup">
                  <label className="field-label">Assign to Verb <span id="verbRequired">*</span></label>
                  <select id="verbSelect" className="input select" value={verbValue} onChange={(e) => setVerbValue(e.target.value)}>
                    <option value="">Select a verb *</option>
                    {verbs.map((v) => <option key={v} value={v}>{v}</option>)}
                  </select>
                </div>
              ) : null}
            </div>
            <div className="form-group">
              <label className="checkbox-group"><input type="checkbox" id="overwrite" checked={overwrite} onChange={(e) => setOverwrite(e.target.checked)} /><span>Allow overwrite if exists</span></label>
            </div>
            <button type="submit" className="btn-primary cu-submit"><Icon name="upload" />Upload Parser</button>
          </form>
        </div>
      </section>

      <section className="panel cu-list-panel">
        <div className="panel-head cu-list-head"><Icon name="file" /><span className="panel-title">Existing Parsers</span>
          <button id="refreshBtn" className="btn sm cu-refresh" onClick={() => loadParsers(project)}><Icon name="refresh" />Refresh</button></div>
        <div id="parserList" className="parser-list">
          {combined == null ? <div className="loading">Loading parsers…</div>
            : !combined.length ? <div className="empty-state"><Icon name="file" /><h3>No parsers found</h3><p>Upload your first parser to get started</p></div>
            : combined.map((p, i) => {
              const isParser = p.kind === "parser";
              const files = (p.files && p.files.length) ? p.files.join(", ") : "No .py file found";
              const av = isParser ? assignedVerbsFor(p.name) : [];
              return (
                <div className="parser-item" key={i}>
                  <div className="parser-info">
                    <div className="parser-name">{p.name}{isParser ? null : <> <span className="verb-tag">pphrase</span></>}</div>
                    <div className="parser-details"><span className="parser-detail"><Icon name="file" /> {fmtSize(p.size)} — {files}</span></div>
                    {isParser ? (
                      av.length ? (
                        <div className="parser-assignments"><span className="assignment-label">Assigned to:</span>
                          {av.map((v) => <span className="verb-tag" key={v}>{v}<button className="unassign-btn" type="button" title={`Unassign from ${v}`} onClick={() => unassign(p.name, v)}><Icon name="close" /></button></span>)}
                        </div>
                      ) : <div className="parser-assignments"><span className="no-assignments">Not assigned to any verbs</span></div>
                    ) : <div className="parser-assignments"><span className="no-assignments">Prepositional phrase (no verb assignment)</span></div>}
                  </div>
                  {isParser ? (
                    <div className="parser-actions">
                      <button className="btn btn-sm btn-secondary" type="button" onClick={() => { setAssignParserSel(p.name); }}><Icon name="link" />Assign</button>
                      <button className="btn btn-sm btn-danger" type="button" onClick={() => setDeleteTarget(p.name)}><Icon name="trash" />Unassign All</button>
                    </div>
                  ) : null}
                </div>
              );
            })}
        </div>
      </section>

      <section className="panel cu-assign-panel">
        <div className="panel-head"><Icon name="link" /><span className="panel-title">Assign Parser to Verb</span></div>
        <div className="assign-form panel-body">
          <form id="assignForm" onSubmit={assign}>
            <div className="form-row">
              <div className="form-group"><label className="field-label">Parser Name</label>
                <select id="assignParser" className="input select" required value={assignParserSel} onChange={(e) => setAssignParserSel(e.target.value)}>
                  <option value="">Select a parser</option>{parsers.map((p) => <option key={p.name} value={p.name}>{p.name}</option>)}
                </select></div>
              <div className="form-group"><label className="field-label">Verb Name</label>
                <select id="assignVerb" className="input select" required value={assignVerbSel} onChange={(e) => setAssignVerbSel(e.target.value)}>
                  <option value="">Select a verb</option>{verbs.map((v) => <option key={v} value={v}>{v}</option>)}
                </select></div>
            </div>
            <button type="submit" className="btn-primary cu-submit"><Icon name="link" />Assign Parser</button>
          </form>
        </div>
      </section>

      <div id="deleteModal" className={"cu-modal-overlay" + (deleteTarget ? " active" : "")} onClick={(e) => { if (e.target === e.currentTarget) setDeleteTarget(null); }}>
        <div className="cu-modal panel">
          <div className="panel-head"><Icon name="trash" /><span className="panel-title">Delete Parser</span></div>
          <div className="cu-modal-body">
            <p>Are you sure you want to delete this parser? This action cannot be undone.</p>
            <div className="cu-modal-actions">
              <button id="cancelDelete" className="btn ghost" onClick={() => setDeleteTarget(null)}>Cancel</button>
              <button id="confirmDelete" className="btn cu-danger" onClick={() => { if (deleteTarget) { del(deleteTarget); setDeleteTarget(null); } }}>Delete</button>
            </div>
          </div>
        </div>
      </div>
      <div id="toastContainer" className="toast-container" />
    </>
  );
}

mountOnAuth("custom-upload-root", (host) => createRoot(host).render(<CustomUpload />));
