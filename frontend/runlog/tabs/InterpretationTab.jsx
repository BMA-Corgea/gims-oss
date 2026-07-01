// frontend/runlog/tabs/InterpretationTab.jsx — port of renderParser. Two cards:
//   • ParsersCard — pick a custom parser (/api/parser_test/list_custom_parsers) and Run it
//     (POST /api/parser_test/test_parser/{project}/{parser}?verb_group=&run_id=).
//   • InterpFilesCard — the interpretation gate banner + a per-tab file table (download / delete /
//     upload-replace), gated by computeInterpGate.
import { useEffect, useRef, useState } from "react";
import { Icon, StateBlock } from "../../lib/ui.jsx";
import { toast } from "../../lib/api.js";
import {
  deleteInterpFile, interpDownloadUrl, interpList, listCustomParsers, runParser, uploadInterpFile,
} from "../api.js";
import { computeInterpGate } from "../gate.js";

const ALLOWED_EXTS = ".csv,.xlsx,.jpeg,.png,.docx,.odt,.txt,.pdf,.html,.ods,.xcf";
const parserName = (p) => (typeof p === "string" ? p : (p.name || p.id || p.module || ""));

function ParsersCard({ project, group, runID, verbName, onRan }) {
  const [parsers, setParsers] = useState(null);
  const [sel, setSel] = useState("");
  const [busy, setBusy] = useState(false);
  const [hint, setHint] = useState("");

  useEffect(() => {
    let live = true;
    listCustomParsers(project)
      .then((ps) => { if (live) setParsers(ps.map(parserName).filter(Boolean)); })
      .catch(() => { if (live) setParsers([]); });
    return () => { live = false; };
  }, [project]);

  const run = async () => {
    if (!sel) { toast("Choose a parser first.", "warn"); return; }
    setBusy(true); setHint("");
    try {
      const body = { parser: sel, verb: verbName, run_id: runID, run: runID, project, group };
      await runParser(project, sel, group, runID, body);
      toast(`Parser "${sel}" started`, "ok");
      if (onRan) onRan();
    } catch (e) {
      toast(`Parser run failed: ${String(e.message || e)}`, "err");
      setHint("Failed to run parser.");
    } finally {
      setBusy(false);
    }
  };

  const none = parsers && !parsers.length;
  return (
    <div className="card">
      <div className="card-header"><strong>Parsers</strong> <span className="muted">(WorkBench)</span></div>
      <div className="card-body">
        <div className="form-row rw-interp-parsers">
          <label htmlFor="wb-parser-select">Select parser</label>
          <select id="wb-parser-select" className="input select" value={sel}
                  onChange={(e) => setSel(e.target.value)} disabled={!parsers || none}>
            {!parsers ? <option value="">Loading…</option>
              : none ? <option value="">(no parsers discovered)</option>
                : <>
                    <option value="">(select a parser)</option>
                    {parsers.map((p) => <option key={p} value={p}>{p}</option>)}
                  </>}
          </select>
          <button id="wb-run-parser" type="button" className="btn btn-primary btn-sm"
                  disabled={busy || !parsers || none} onClick={run}>
            {busy ? "Running…" : "Run Parser"}
          </button>
          <span className="muted">{none ? "No parsers available for this project/group." : hint}</span>
        </div>
      </div>
    </div>
  );
}

function TabFileRow({ tab, info, gate, project, group, runID, onChanged }) {
  const fileRef = useRef(null);
  const [overwrite, setOverwrite] = useState(false);
  const [busy, setBusy] = useState(false);
  const exists = !!(info && info.exists);
  const disabled = !gate.allowed;

  const upload = async () => {
    const f = fileRef.current && fileRef.current.files && fileRef.current.files[0];
    if (!f) { toast("Choose a file.", "warn"); return; }
    setBusy(true);
    try {
      await uploadInterpFile(project, group, runID, tab, f, overwrite);
      toast(`Uploaded ${f.name}`, "ok");
      if (fileRef.current) fileRef.current.value = "";
      onChanged();
    } catch (e) { toast(`Upload failed: ${String(e.message || e)}`, "err"); }
    finally { setBusy(false); }
  };

  const remove = async () => {
    if (!window.confirm(`Delete file for "${tab}"?`)) return;
    try { await deleteInterpFile(project, group, runID, tab); toast(`Deleted ${tab}`, "ok"); onChanged(); }
    catch (e) { toast(`Delete failed: ${String(e.message || e)}`, "err"); }
  };

  return (
    <tr data-tab={tab}>
      <td>{tab}</td>
      <td data-cell="file">
        {exists
          ? <span className="file-label">{info.name} ({info.bytes} B)</span>
          : <em className="muted">No file</em>}
        {exists ? <a className="btn btn-sm" href={interpDownloadUrl(project, group, runID, tab)} download>Download</a> : null}
        {exists ? <button type="button" className="btn btn-danger btn-sm" onClick={remove}>Delete</button> : null}
      </td>
      <td>
        <div className="raw-upload-form">
          <input ref={fileRef} type="file" accept={ALLOWED_EXTS} disabled={disabled} />
          <label className="checkbox-inline">
            <input type="checkbox" checked={overwrite} disabled={disabled} onChange={(e) => setOverwrite(e.target.checked)} /> Replace
          </label>
          <button type="button" className="btn btn-primary btn-sm" disabled={disabled || busy} onClick={upload}>
            {busy ? "Uploading…" : "Upload"}
          </button>
        </div>
      </td>
    </tr>
  );
}

function InterpFilesCard({ project, group, runID, verbName }) {
  const [state, setState] = useState({ status: "loading" });
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let live = true;
    setState({ status: "loading" });
    Promise.all([
      interpList(project, group, runID, verbName),
      computeInterpGate(project, group, runID),
    ]).then(([listing, gate]) => { if (live) setState({ status: "ok", listing, gate }); })
      .catch((e) => { if (live) setState({ status: "error", message: String(e.message || e) }); });
    return () => { live = false; };
  }, [project, group, runID, verbName, tick]);

  const reload = () => setTick((t) => t + 1);

  return (
    <div className="card">
      <div className="card-header">
        <strong>Interpretation Files</strong>
        <span className="muted">{project} • {group} • {verbName || "?"} • run: {runID}</span>
      </div>
      <div className="card-body">
        {state.status === "loading" ? <StateBlock kind="loading" title="Loading…" /> : null}
        {state.status === "error" ? <StateBlock kind="error" title="Failed to load interpretation files" message={state.message} /> : null}
        {state.status === "ok" ? <InterpTable listing={state.listing} gate={state.gate}
          project={project} group={group} runID={runID} onChanged={reload} /> : null}
      </div>
    </div>
  );
}

function InterpTable({ listing, gate, project, group, runID, onChanged }) {
  const tabs = Array.isArray(listing.tabs) ? listing.tabs : [];
  return (
    <>
      <div className={"raw-upload-gate " + (gate.allowed ? "ok" : "locked")}>
        <Icon name={gate.allowed ? "check" : "lock"} />{" "}
        {gate.allowed
          ? <span><strong>Uploads unlocked</strong> — Interpretation step is current.</span>
          : <span><strong>Uploads locked</strong> — current step: <em>{gate.reason}</em>.</span>}
      </div>
      {tabs.length ? (
        <table className="raw-table">
          <thead><tr><th>Tab</th><th>File</th><th>Upload / Replace</th></tr></thead>
          <tbody>
            {tabs.map((tab) => (
              <TabFileRow key={tab} tab={tab} info={(listing.files || {})[tab]} gate={gate}
                          project={project} group={group} runID={runID} onChanged={onChanged} />
            ))}
          </tbody>
        </table>
      ) : <div className="muted">No interpretation tabs are defined.</div>}
    </>
  );
}

export function InterpretationTab({ dump, project, group, runID }) {
  const verbName = (dump && dump.run_entry && (dump.run_entry.test_type || dump.run_entry.verb)) || (dump && dump.verb) || "";
  const [tick, setTick] = useState(0);
  return (
    <div className="rw-interp">
      <ParsersCard project={project} group={group} runID={runID} verbName={verbName} onRan={() => setTick((t) => t + 1)} />
      <InterpFilesCard key={tick} project={project} group={group} runID={runID} verbName={verbName} />
    </div>
  );
}
