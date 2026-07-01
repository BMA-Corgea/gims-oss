// frontend/pages/backup.jsx — Storage / Backup (Phase 6 React; tool pages T2).
// React port of the 434-line vanilla backup.js: a project picker + a Backup-Now form, a backups table
// (Validate / Details+Download / Clone Restore / Delete), a Schedules form + table (enable toggle / Run
// now / Delete / "Run due now" tick), and a Details modal. Reuses backup.css (the .bk-*/.actions-cell/
// .row-ok|.row-bad/.download-list/.conditional.weekly|.monthly + #id contract is reproduced).
//
// Byte-identical mutations (backupshot.py, real route, 0 console errors), all under /api/storage:
//   POST   /backup-now      { project, type, paranoid, notes? }   (notes omitted when blank)
//   POST   /validate/{id}   { project }
//   POST   /restore/{id}    { project, mode:"clone", new_project, scope }   (prompt×2 + confirm)
//   DELETE /backups/{id}    { project }                            (DELETE carries a body)
//   POST   /schedules       { project, type, frequency, hour, minute, dow, dom, retention_keep, enabled, notes }
//   POST   /schedules       { ...schedule, enabled }              (enable/disable toggle = full obj + override)
//   DELETE /schedules/{id}                                        (no body)
//   POST   /schedule/tick   {}
// dow=null unless weekly; dom=null unless monthly. The legacy dev "Debug panel" is dropped (inert).
import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { Icon } from "../lib/ui.jsx";
import { enc, fetchJSON, mountOnAuth, toast } from "../lib/api.js";

const API = "/api/storage";
const notify = (m, v = "ok") => toast(m, v === "error" ? "err" : (v === "info" || v === "warn") ? "info" : "ok");
const j = (url, method, body) => fetchJSON(url, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

function fmtBytes(b) {
  if (b == null) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"]; let i = 0, n = Number(b);
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(n < 10 && i > 0 ? 1 : 0)} ${units[i]}`;
}
function fmtWhen(s) {
  const base = `${s.frequency} @ ${String(s.hour).padStart(2, "0")}:${String(s.minute).padStart(2, "0")}`;
  if (s.frequency === "weekly") return `${base} (dow ${s.dow})`;
  if (s.frequency === "monthly") return `${base} (dom ${s.dom})`;
  return base;
}

function Backup() {
  const [projects, setProjects] = useState([]);
  const [project, setProject] = useState("");       // committed (Load) project
  const [sel, setSel] = useState("");               // dropdown selection
  const [backups, setBackups] = useState([]);
  const [schedules, setSchedules] = useState([]);
  const [rowState, setRowState] = useState({});     // backup_id -> 'ok'|'bad'
  const [details, setDetails] = useState(null);     // {content, downloads:[{href,label}]}

  // Backup Now form
  const [bType, setBType] = useState("hybrid");
  const [bNotes, setBNotes] = useState("");
  const [bParanoid, setBParanoid] = useState(false);

  // Schedule form
  const [sType, setSType] = useState("hybrid");
  const [freq, setFreq] = useState("daily");
  const [hour, setHour] = useState(2);
  const [minute, setMinute] = useState(0);
  const [dow, setDow] = useState(0);
  const [dom, setDom] = useState(1);
  const [keep, setKeep] = useState(10);
  const [sNotes, setSNotes] = useState("");

  const loadBackups = async (p) => {
    try { const data = await fetchJSON(`${API}/backups?project=${enc(p)}`); setBackups(Array.isArray(data && data.backups) ? data.backups : []); }
    catch (e) { setBackups([]); notify(`Load failed: ${e.message || e}`, "error"); }
  };
  const loadSchedules = async (p) => {
    try { const data = await fetchJSON(`${API}/schedules?project=${enc(p)}`); setSchedules(Array.isArray(data) ? data : []); }
    catch (e) { notify("Failed to load schedules", "error"); }
  };
  const loadAll = (p) => Promise.all([loadBackups(p), loadSchedules(p)]);

  useEffect(() => {
    fetchJSON(`${API}/projects`).then((ps) => {
      const list = Array.isArray(ps) ? ps : [];
      setProjects(list);
      let last = null; try { last = localStorage.getItem("gims_last_project"); } catch { /* ignore */ }
      if (last && list.includes(last)) { setSel(last); setProject(last); loadAll(last); }
      else if (list.length) setSel(list[0]);
    }).catch(() => notify("Could not load project list", "error"));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const onLoad = () => { if (!sel) { notify("Select a project", "warn"); return; } setProject(sel); try { localStorage.setItem("gims_last_project", sel); } catch { /* ignore */ } loadAll(sel); };

  // ── backup actions ──
  const backupNow = async () => {
    if (!project) { notify("Select a project first", "warn"); return; }
    const notes = bNotes.trim();
    const payload = { project, type: bType, paranoid: bParanoid };
    if (notes) payload.notes = notes;
    notify("Starting backup…", "info");
    try { await j(`${API}/backup-now`, "POST", payload); notify("Backup complete", "ok"); loadBackups(project); }
    catch (e) { notify(`Backup failed: ${e.message || e}`, "error"); }
  };
  const validate = async (id) => {
    if (!project) { notify("Select a project first", "warn"); return; }
    try { const res = await j(`${API}/validate/${enc(id)}`, "POST", { project }); const ok = !!res.ok; notify(ok ? "Validation OK" : "Validation failed", ok ? "ok" : "error"); setRowState((s) => ({ ...s, [id]: ok ? "ok" : "bad" })); }
    catch (e) { notify(`Validation error: ${e.message || e}`, "error"); }
  };
  const showDetails = async (id) => {
    if (!project) { notify("Select a project first", "warn"); return; }
    try {
      const res = await fetchJSON(`${API}/backups/${enc(id)}?project=${enc(project)}`);
      const downloads = [];
      if (res && res.artifacts && res.artifacts.project_zip && res.artifacts.project_zip.path) downloads.push({ href: `${API}/download/${enc(id)}/project.zip?project=${enc(project)}`, label: "Download project.zip" });
      const dbMap = (res && res.artifacts && res.artifacts.db) || {};
      for (const [key, meta] of Object.entries(dbMap)) {
        const backend = (meta && meta.backend) || "sqlite";
        downloads.push({ href: `${API}/download/${enc(id)}/db/${enc(key)}?project=${enc(project)}`, label: `Download ${backend === "pg" ? `${key}.pg.zip` : `${key}.sqlite`}` });
      }
      setDetails({ content: JSON.stringify(res, null, 2), downloads });
    } catch (e) { notify(`Load details failed: ${e.message || e}`, "error"); }
  };
  const cloneRestore = async (id) => {
    if (!project) { notify("Select a project first", "warn"); return; }
    const nameInput = window.prompt("New project name (leave blank for default suggestion):");
    if (nameInput === null) { notify("Clone cancelled", "info"); return; }
    const scopeInput = window.prompt('Scope: leave blank, or enter "db_only" or "files_only"');
    if (scopeInput === null) { notify("Clone cancelled", "info"); return; }
    const newName = nameInput.trim() || null;
    const scope = scopeInput.trim() || null;
    const summary = `Clone ${project} from backup ${id}\nNew project: ${newName || "(default)"}\nScope: ${scope || "(full)"}`;
    if (!window.confirm(summary + "\nProceed?")) { notify("Clone cancelled", "info"); return; }
    try { const res = await j(`${API}/restore/${enc(id)}`, "POST", { project, mode: "clone", new_project: newName, scope }); notify(`Cloned to: ${(res && res.new_project) || "(unknown)"}`, "ok"); }
    catch (e) { notify(`Restore failed: ${e.message || e}`, "error"); }
  };
  const delBackup = async (id) => {
    if (!project) { notify("Select a project first", "warn"); return; }
    if (!window.confirm("Delete this backup? This cannot be undone.")) return;
    try { await fetchJSON(`${API}/backups/${enc(id)}`, { method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ project }) }); notify("Backup deleted", "ok"); loadBackups(project); }
    catch (e) { notify(`Delete failed: ${e.message || e}`, "error"); }
  };

  // ── schedule actions ──
  const createSchedule = async () => {
    if (!project) { notify("Select a project first", "warn"); return; }
    const payload = {
      project, type: sType, frequency: freq, hour: Number(hour || 0), minute: Number(minute || 0),
      dow: freq === "weekly" ? Number(dow || 0) : null, dom: freq === "monthly" ? Number(dom || 1) : null,
      retention_keep: Number(keep || 10), enabled: true, notes: sNotes.trim() || null,
    };
    try { await j(`${API}/schedules`, "POST", payload); notify("Schedule created", "ok"); loadSchedules(project); }
    catch (e) { notify(`Create failed: ${e.message || e}`, "error"); }
  };
  const toggleSchedule = async (s, enabled) => {
    try { await j(`${API}/schedules`, "POST", { ...s, enabled }); notify(enabled ? "Schedule enabled" : "Schedule disabled", "info"); loadSchedules(project); }
    catch (e) { notify(`Update failed: ${e.message || e}`, "error"); }
  };
  const delSchedule = async (id) => {
    if (!window.confirm("Delete this schedule?")) return;
    try { await fetchJSON(`${API}/schedules/${enc(id)}`, { method: "DELETE" }); notify("Schedule deleted", "ok"); loadSchedules(project); }
    catch (e) { notify(`Delete failed: ${e.message || e}`, "error"); }
  };
  const runScheduleNow = async (s) => {
    try { await j(`${API}/backup-now`, "POST", { project: s.project, type: s.type, paranoid: false, notes: `(manual run of ${s.frequency} schedule)` }); notify("Backup started (run now)", "ok"); loadBackups(project); }
    catch (e) { notify(`Run now failed: ${e.message || e}`, "error"); }
  };
  const tick = async () => {
    try { const res = await j(`${API}/schedule/tick`, "POST", {}); if (res && res.ran && res.ran.length) notify(`Ran ${res.ran.length} schedule(s)`, "ok"); loadAll(project); }
    catch (e) { notify(`Tick failed: ${e.message || e}`, "error"); }
  };

  return (
    <>
      <section className="panel bk-toolbar">
        <div className="panel-body bk-toolbar-row">
          <label className="field bk-field"><span className="field-label">Project</span>
            <select id="projectSelect" className="input select" value={sel} onChange={(e) => setSel(e.target.value)}>
              {!projects.length ? <option value="">—</option> : projects.map((p) => <option key={p} value={p}>{p}</option>)}
            </select></label>
          <div className="bk-toolbar-actions">
            <button id="loadBtn" className="btn blue" onClick={onLoad}><Icon name="folder" />Load</button>
            <button id="refreshBtn" className="btn" title="Refresh backups" onClick={() => project ? loadBackups(project) : notify("Select a project first", "warn")}><Icon name="refresh" />Refresh</button>
          </div>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head"><Icon name="backup" /><span className="panel-title">Backup Now</span></div>
        <div className="panel-body grid-3">
          <div className="field"><label className="field-label">Type</label>
            <select id="backupType" className="input select" value={bType} onChange={(e) => setBType(e.target.value)}>
              <option value="hybrid">Hybrid (ZIP + SQLite)</option><option value="zip">ZIP (files only)</option><option value="sqlite">SQLite (databases only)</option>
            </select></div>
          <div className="field"><label className="field-label">Notes (optional)</label><input id="backupNotes" className="input" type="text" maxLength="200" placeholder="Before schema migration…" value={bNotes} onChange={(e) => setBNotes(e.target.value)} /></div>
          <div className="field"><span className="field-label">Options</span><label className="checkbox"><input id="backupParanoid" type="checkbox" checked={bParanoid} onChange={(e) => setBParanoid(e.target.checked)} /><span>Write checksums.txt (paranoid mode)</span></label></div>
          <div className="field col-span-3"><button id="backupNowBtn" className="btn-primary bk-cta" onClick={backupNow}><Icon name="backup" />Backup Now</button></div>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head"><Icon name="archive" /><span className="panel-title">Backups</span><span id="countLabel" className="count-pill bk-count">{backups.length}</span></div>
        <div className="panel-body bk-list-body">
          {!backups.length ? (
            <div id="backupsEmpty" className="gims-state is-empty"><span className="gims-state-mark icon-chip round"><Icon name="archive" /></span><h3 className="gims-state-title">No backups yet</h3><p className="gims-state-msg">Create one using “Backup Now” above.</p></div>
          ) : (
            <div className="table-wrap"><table className="table" id="backupsTable">
              <thead><tr><th>Created</th><th>Type</th><th>Size</th><th>Notes</th><th>Actions</th></tr></thead>
              <tbody id="backupsTbody">
                {backups.map((b) => (
                  <tr key={b.backup_id} id={`row-${b.backup_id}`} className={rowState[b.backup_id] === "ok" ? "row-ok" : rowState[b.backup_id] === "bad" ? "row-bad" : ""}>
                    <td>{b.created_at || "—"}</td><td>{b.type || "—"}</td><td>{fmtBytes(b.size_bytes)}</td><td>{b.notes || ""}</td>
                    <td className="actions-cell">
                      <button className="btn small" onClick={() => validate(b.backup_id)}>Validate</button><span className="spacer" />
                      <button className="btn small" onClick={() => showDetails(b.backup_id)}>Details+Download</button><span className="spacer" />
                      <button className="btn small" onClick={() => cloneRestore(b.backup_id)}>Clone Restore</button><span className="spacer" />
                      <button className="btn small danger" onClick={() => delBackup(b.backup_id)}>Delete</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table></div>
          )}
        </div>
      </section>

      <section className="panel">
        <div className="panel-head"><Icon name="clock" /><span className="panel-title">Schedules</span>
          <div className="actions-right"><button className="btn sm ghost" title="Run due schedules now" onClick={tick}><Icon name="play" />Run due now</button>
            <button className="btn sm ghost" title="Refresh schedules" onClick={() => loadSchedules(project)}><Icon name="refresh" />Refresh</button></div>
        </div>
        <div className="panel-body">
          <div className="grid-4">
            <div className="field"><label className="field-label">Backup type</label><select id="schType" className="input select" value={sType} onChange={(e) => setSType(e.target.value)}><option value="hybrid">Hybrid</option><option value="zip">ZIP</option><option value="sqlite">SQLite</option></select></div>
            <div className="field"><label className="field-label">Frequency</label><select id="schFrequency" className="input select" value={freq} onChange={(e) => setFreq(e.target.value)}><option value="hourly">Hourly</option><option value="daily">Daily</option><option value="weekly">Weekly</option><option value="monthly">Monthly</option></select></div>
            <div className="field"><label className="field-label">Hour (0–23)</label><input id="schHour" className="input" type="number" min="0" max="23" value={hour} onChange={(e) => setHour(e.target.value)} /></div>
            <div className="field"><label className="field-label">Minute (0–59)</label><input id="schMinute" className="input" type="number" min="0" max="59" value={minute} onChange={(e) => setMinute(e.target.value)} /></div>
            {freq === "weekly" ? <div className="field conditional weekly"><label className="field-label">Day of week (0=Mon…6=Sun)</label><input id="schDow" className="input" type="number" min="0" max="6" value={dow} onChange={(e) => setDow(e.target.value)} /></div> : null}
            {freq === "monthly" ? <div className="field conditional monthly"><label className="field-label">Day of month (1–28)</label><input id="schDom" className="input" type="number" min="1" max="28" value={dom} onChange={(e) => setDom(e.target.value)} /></div> : null}
            <div className="field"><label className="field-label">Retention (keep last N)</label><input id="schKeep" className="input" type="number" min="1" value={keep} onChange={(e) => setKeep(e.target.value)} /></div>
            <div className="field col-span-2"><label className="field-label">Notes (optional)</label><input id="schNotes" className="input" type="text" maxLength="200" placeholder="Nightly save state…" value={sNotes} onChange={(e) => setSNotes(e.target.value)} /></div>
            <div className="field col-span-4"><button id="createScheduleBtn" className="btn blue" onClick={createSchedule}><Icon name="plus" />Create schedule</button></div>
          </div>
          <div className="table-wrap mtop"><table className="table" id="schedulesTable">
            <thead><tr><th>Type</th><th>When</th><th>Keep</th><th>Enabled</th><th>Next / Last</th><th>Actions</th></tr></thead>
            <tbody id="schedulesTbody">
              {schedules.map((s) => (
                <tr key={s.id}>
                  <td>{s.type}</td><td>{fmtWhen(s)}</td><td>{s.retention_keep ?? "—"}</td>
                  <td><input type="checkbox" checked={!!s.enabled} onChange={(e) => toggleSchedule(s, e.target.checked)} /></td>
                  <td><div className="small-muted">next</div><div>{s.next_run_at || "—"}</div><div className="small-muted mt4">last</div><div>{s.last_run_at || "—"}</div></td>
                  <td className="actions-cell"><button className="btn small" onClick={() => runScheduleNow(s)}>Run now</button><span className="spacer" /><button className="btn small danger" onClick={() => delSchedule(s.id)}>Delete</button></td>
                </tr>
              ))}
            </tbody>
          </table></div>
        </div>
      </section>

      {details ? (
        <div id="detailsModal" className="overlay" role="dialog" aria-modal="true" onClick={(e) => { if (e.target === e.currentTarget) setDetails(null); }}>
          <div className="modal bk-modal">
            <div className="modal-head bk-modal-head"><h3 className="modal-title">Backup Details</h3><button className="icon-btn" type="button" aria-label="Close" onClick={() => setDetails(null)}><Icon name="close" /></button></div>
            <div className="modal-body bk-modal-body">
              <div id="detailsContent" className="mono small">{details.content}</div>
              <div id="downloadsBlock" className="downloads"><div className="download-list">{details.downloads.map((d, i) => <a key={i} href={d.href} className="btn small">{d.label}</a>)}</div></div>
            </div>
            <div className="modal-foot"><button className="btn" type="button" onClick={() => setDetails(null)}>Close</button></div>
          </div>
        </div>
      ) : null}
    </>
  );
}

mountOnAuth("backup-root", (host) => createRoot(host).render(<Backup />));
