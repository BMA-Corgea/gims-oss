// frontend/runlog/tabs/RawDataTab.jsx — per-pocket raw-data attachments (port of renderRawDataSection).
// Resolves pockets (dump.meta.raw_data_inputs → listing keys → verb-schema raw_data_inputs), shows a
// GateBanner (linear gate), and one PocketRow per pocket: file list (download/delete) + an Uploader
// (file + Allow-overwrite + Upload) enabled only when the gate allows this pocket.
import { useEffect, useRef, useState } from "react";
import { Icon, StateBlock } from "../../lib/ui.jsx";
import { toast } from "../../lib/api.js";
import { deleteRawFile, getRawList, getVerbSchema, rawDownloadUrl, uploadRawFile } from "../api.js";
import { computeRawUploadGate } from "../gate.js";

const ALLOWED_EXTS = ".csv,.xlsx,.jpeg,.png,.docx,.odt,.txt,.pdf,.html,.ods,.xcf";

async function resolvePockets(dump, listing, project) {
  const fromMeta = dump && dump.meta && dump.meta.raw_data_inputs;
  if (Array.isArray(fromMeta) && fromMeta.length) return [...fromMeta];
  const keys = Object.keys((listing && listing.pockets) || {});
  if (keys.length) return keys;
  const verbName = (dump && dump.run_entry && (dump.run_entry.test_type || dump.run_entry.verb))
    || (dump && (dump.verb || (dump.meta && dump.meta.verb))) || "";
  if (verbName) {
    try {
      const schema = await getVerbSchema(project, verbName);
      const fromSchema = schema && schema.data_entry_schema && schema.data_entry_schema.raw_data_inputs;
      if (Array.isArray(fromSchema) && fromSchema.length) return fromSchema;
    } catch { /* best-effort */ }
  }
  return [];
}

function GateBanner({ gate }) {
  return (
    <div className={"raw-upload-gate " + (gate.allowed ? "ok" : "locked")}>
      <Icon name={gate.allowed ? "check" : "lock"} />{" "}
      {gate.allowed
        ? <span><strong>Uploads unlocked</strong>{gate.pocket ? <> — allowed pocket: <em>{gate.pocket}</em></> : null}.</span>
        : <span><strong>Uploads locked</strong> — current step: <em>{gate.reason}</em>.</span>}
    </div>
  );
}

function PocketRow({ pocket, files, gate, project, group, runID, onChanged }) {
  const fileRef = useRef(null);
  const [file, setFile] = useState(null);
  const [overwrite, setOverwrite] = useState(false);
  const [busy, setBusy] = useState(false);
  const enableHere = !!(gate.allowed && (!gate.pocket || gate.pocket.toLowerCase() === pocket.toLowerCase()));

  const upload = async () => {
    if (!file) { toast("Choose a file first.", "warn"); return; }
    setBusy(true);
    try {
      await uploadRawFile(project, group, runID, pocket, file, overwrite);
      toast(`Uploaded ${file.name}`, "ok");
      setFile(null);
      if (fileRef.current) fileRef.current.value = "";
      onChanged();
    } catch (e) {
      toast(`Upload failed: ${String(e.message || e)}`, "err");
    } finally {
      setBusy(false);
    }
  };

  const remove = async (name) => {
    if (!window.confirm(`Delete ${name} from ${pocket}?`)) return;
    try { await deleteRawFile(project, group, runID, pocket, name); toast(`Deleted ${name}`, "ok"); onChanged(); }
    catch (e) { toast(`Delete failed: ${String(e.message || e)}`, "err"); }
  };

  const lockTitle = enableHere ? undefined
    : (gate.allowed ? "Not the active pocket for this step." : "Locked until the Raw Data step is current.");

  return (
    <tr data-pocket={pocket}>
      <td>{pocket}</td>
      <td data-cell="files">
        <div className="raw-files">
          {files.length ? (
            <ul className="raw-file-list">
              {files.map((f) => (
                <li key={f.name} className="raw-file">
                  <span className="raw-file-name">{f.name} ({f.bytes} B)</span>
                  <a className="btn btn-sm" href={rawDownloadUrl(project, group, runID, pocket, f.name)} download>Download</a>
                  <button type="button" className="btn btn-sm" onClick={() => remove(f.name)}>Delete</button>
                </li>
              ))}
            </ul>
          ) : <em className="muted">No files uploaded</em>}
        </div>
      </td>
      <td>
        <div className="raw-upload-form">
          <input ref={fileRef} type="file" accept={ALLOWED_EXTS} disabled={!enableHere}
                 onChange={(e) => setFile((e.target.files && e.target.files[0]) || null)} />
          <label className="raw-overwrite">
            <input type="checkbox" checked={overwrite} disabled={!enableHere}
                   onChange={(e) => setOverwrite(e.target.checked)} />
            <span>Allow overwrite</span>
          </label>
          <button type="button" className="btn btn-sm" disabled={!enableHere || busy} title={lockTitle} onClick={upload}>
            {busy ? "Uploading…" : "Upload"}
          </button>
        </div>
      </td>
    </tr>
  );
}

export function RawDataTab({ dump, project, group, runID }) {
  const [state, setState] = useState({ status: "loading" });
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let live = true;
    (async () => {
      let listing = { pockets: {} };
      try { listing = await getRawList(project, group, runID); } catch { /* best-effort */ }
      const pockets = await resolvePockets(dump, listing, project);
      if (!pockets.length) { if (live) setState({ status: "empty" }); return; }
      const gate = await computeRawUploadGate(project, group, runID, pockets);
      if (live) setState({ status: "ok", listing, pockets, gate });
    })();
    return () => { live = false; };
  }, [project, group, runID, tick]); // eslint-disable-line react-hooks/exhaustive-deps

  if (state.status === "loading") return <StateBlock kind="loading" title="Loading raw data…" />;
  if (state.status === "empty") {
    return <StateBlock kind="empty" icon="info" title="No raw data pockets" message="No raw data pockets are defined for this verb." />;
  }

  const { listing, pockets, gate } = state;
  const reload = () => setTick((t) => t + 1);

  return (
    <div className="rw-raw">
      <GateBanner gate={gate} />
      <table className="raw-table">
        <thead><tr><th>Pocket</th><th>Files</th><th>Upload</th></tr></thead>
        <tbody>
          {pockets.map((pocket) => (
            <PocketRow key={pocket} pocket={pocket}
                       files={(listing.pockets && listing.pockets[pocket]) || []}
                       gate={gate} project={project} group={group} runID={runID} onChanged={reload} />
          ))}
        </tbody>
      </table>
    </div>
  );
}
