/* backup.js — UI for GIMS Backup System (local-only)
 * Integrates with global debug helper:
 *   const DEBUG_ENABLED = false;
 *   const debug = DEBUG_ENABLED ? console.debug.bind(console) : () => {};
 * We won't redefine `debug`; we wrap it so we can also write into the panel.
 */

(() => {
  // ---------------------------
  // Small DOM helpers
  // ---------------------------
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));
  const API_BASE = "/api/storage";

  // ---------------------------
  // Debug wrapper
  // ---------------------------
  const _hasGlobalDebug = typeof window !== "undefined" && typeof window.debug === "function";
  const _fallbackDebugEnabled = typeof window !== "undefined" && !!window.DEBUG_ENABLED;
  const _fallbackDebug = _fallbackDebugEnabled ? console.debug.bind(console) : () => {};
  const _stringify = (a) => (typeof a === "string" ? a : (() => { try { return JSON.stringify(a); } catch { return String(a); } })());
  const _panelEnabled = () => !!($("#debugToggle") && $("#debugToggle").checked);
  const _appendPanelLine = (text) => {
    const log = $("#debugLog");
    if (!log) return;
    log.textContent += (log.textContent ? "\n" : "") + text;
    log.scrollTop = log.scrollHeight;
  };
  function dbg(...args) {
    const prefix = "[backup-ui]";
    try { (_hasGlobalDebug ? window.debug : _fallbackDebug)(prefix, ...args); } catch {}
    if (_panelEnabled()) {
      const line = `${prefix} ${new Date().toLocaleTimeString()} ` + args.map(_stringify).join(" ");
      _appendPanelLine(line);
    }
  }

  // ---------------------------
  // UI feedback
  // ---------------------------
  function toast(msg, variant = "ok", timeout = 2400) {
    const box = $("#toastContainer"); if (!box) return;
    const el = document.createElement("div");
    el.className = `toast ${variant}`;
    el.textContent = msg;
    box.appendChild(el);
    setTimeout(() => { el.classList.add("gone"); }, Math.max(0, timeout - 280));
    setTimeout(() => { el.remove(); }, timeout);
  }

  // ---------------------------
  // Formatters
  // ---------------------------
  function fmtBytes(b) {
    if (b == null) return "—";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let i = 0; let n = Number(b);
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return `${n.toFixed(n < 10 && i > 0 ? 1 : 0)} ${units[i]}`;
  }
  function fmtWhen(s) {
    const base = `${s.frequency} @ ${String(s.hour).padStart(2, "0")}:${String(s.minute).padStart(2, "0")}`;
    if (s.frequency === "weekly") return `${base} (dow ${s.dow})`;
    if (s.frequency === "monthly") return `${base} (dom ${s.dom})`;
    return base;
  }

  // ---------------------------
  // Fetch helpers
  // ---------------------------
  async function fetchJSON(url, options = {}) {
    dbg("fetchJSON:start", url, options.method || "GET");
    const headers = options.headers || {};
    if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
    const res = await fetch(url, { ...options, headers });
    const txt = await res.text();
    const isJson = (res.headers.get("content-type") || "").includes("application/json");
    const data = isJson ? (txt ? JSON.parse(txt) : {}) : txt;
    if (!res.ok) {
      dbg("fetchJSON:error", res.status, data);
      const msg = typeof data === "string" ? data : (data?.detail || `HTTP ${res.status}`);
      throw new Error(msg);
    }
    dbg("fetchJSON:ok keys", isJson ? Object.keys(data || {}) : "(text)");
    return data;
  }

  // ---------------------------
  // State
  // ---------------------------
  let state = {
    project: null,
    backups: [],
    projects: [],
    schedules: [],
  };

  // ---------------------------
  // Project selection
  // ---------------------------
  async function loadProjectsList() {
    dbg("projects:list:fetch");
    try {
      const projects = await fetchJSON(`${API_BASE}/projects`);
      state.projects = Array.isArray(projects) ? projects : [];
      const sel = $("#projectSelect");
      sel.innerHTML = "";
      for (const p of state.projects) {
        const opt = document.createElement("option"); opt.value = p; opt.textContent = p; sel.appendChild(opt);
      }
      const last = localStorage.getItem("gims_last_project");
      if (last && state.projects.includes(last)) {
        sel.value = last; state.project = last;
        dbg("projects:list:preselect last ->", last);
        await Promise.all([loadBackups(), loadSchedules()]);
      } else if (state.projects.length) {
        sel.value = state.projects[0];
      }
      toast("Projects loaded", "info", 1000);
    } catch (err) {
      dbg("projects:list:error", String(err)); toast("Could not load project list", "error");
    }
  }
  function onLoadProject() {
    const sel = $("#projectSelect");
    if (!sel || !sel.value) return toast("Select a project", "warn");
    state.project = sel.value;
    try { localStorage.setItem("gims_last_project", state.project); } catch {}
    dbg("project set ->", state.project);
    Promise.all([loadBackups(), loadSchedules()]);
  }

  // ---------------------------
  // Backups
  // ---------------------------
  async function loadBackups() {
    $("#backupsEmpty").classList.add("hidden");
    $("#backupsTbody").innerHTML = "";
    $("#countLabel").textContent = "…";
    try {
      const data = await fetchJSON(`${API_BASE}/backups?project=${encodeURIComponent(state.project)}`);
      state.backups = Array.isArray(data?.backups) ? data.backups : [];
      renderBackups();
      toast("Backups loaded", "info", 1100);
    } catch (err) {
      dbg("loadBackups:error", String(err));
      toast(`Load failed: ${err.message || err}`, "error");
      $("#backupsEmpty").classList.remove("hidden");
      $("#countLabel").textContent = "0";
    }
  }

  function renderBackups() {
    const tb = $("#backupsTbody"); tb.innerHTML = "";
    $("#countLabel").textContent = String(state.backups.length || 0);
    if (!state.backups.length) { $("#backupsEmpty").classList.remove("hidden"); return; }
    $("#backupsEmpty").classList.add("hidden");

    for (const b of state.backups) {
      const tr = document.createElement("tr"); tr.id = `row-${b.backup_id}`;
      const created = document.createElement("td"); created.textContent = b.created_at || "—";
      const type = document.createElement("td");    type.textContent = b.type || "—";
      const size = document.createElement("td");    size.textContent = fmtBytes(b.size_bytes);
      const notes = document.createElement("td");   notes.textContent = b.notes || "";
      const actions = document.createElement("td"); actions.className = "actions-cell";
      actions.appendChild(btn("Validate", "small", () => onValidate(b.backup_id)));
      actions.appendChild(spacer());
      actions.appendChild(btn("Details+Download", "small", () => onDetails(b.backup_id)));
      actions.appendChild(spacer());
      actions.appendChild(btn("Clone Restore", "small", () => onCloneRestore(b.backup_id)));
      actions.appendChild(spacer());
      actions.appendChild(btn("Delete", "small danger", () => onDelete(b.backup_id)));
      tr.append(created, type, size, notes, actions); tb.appendChild(tr);
    }
  }

  async function onBackupNow() {
    if (!state.project) return toast("Select a project first", "warn");
    const type = $("#backupType").value;
    const notes = $("#backupNotes").value.trim() || undefined;
    const paranoid = $("#backupParanoid").checked;

    const payload = { project: state.project, type, paranoid, notes };
    dbg("backup-now:payload", payload); toast("Starting backup…", "info", 1500);

    try {
      const res = await fetchJSON(`${API_BASE}/backup-now`, { method: "POST", body: JSON.stringify(payload) });
      dbg("backup-now:ok", res); toast("Backup complete", "ok");
      loadBackups();
    } catch (err) { dbg("backup-now:error", String(err)); toast(`Backup failed: ${err.message || err}`, "error", 3800); }
  }

  async function onValidate(backup_id) {
    if (!state.project) return toast("Select a project first", "warn");
    dbg("validate:start", backup_id);
    try {
      const res = await fetchJSON(`${API_BASE}/validate/${encodeURIComponent(backup_id)}`, {
        method: "POST", body: JSON.stringify({ project: state.project }),
      });
      dbg("validate:ok", res);
      const ok = !!res.ok; toast(ok ? "Validation OK" : "Validation failed", ok ? "ok" : "error", 2500);
      const row = document.getElementById(`row-${backup_id}`); if (row) { row.classList.remove("row-ok", "row-bad"); row.classList.add(ok ? "row-ok" : "row-bad"); }
    } catch (err) { dbg("validate:error", String(err)); toast(`Validation error: ${err.message || err}`, "error", 3400); }
  }

  async function onDetails(backup_id) {
    if (!state.project) return toast("Select a project first", "warn");
    dbg("details:start", backup_id);
    try {
      const res = await fetchJSON(`${API_BASE}/backups/${encodeURIComponent(backup_id)}?project=${encodeURIComponent(state.project)}`);
      dbg("details:manifest keys", Object.keys(res || {}));
      $("#detailsContent").textContent = JSON.stringify(res, null, 2);
      const down = $("#downloadsBlock"); down.innerHTML = ""; const list = document.createElement("div"); list.className = "download-list";
      if (res?.artifacts?.project_zip?.path) {
        const a = document.createElement("a");
        a.href = `${API_BASE}/download/${encodeURIComponent(backup_id)}/project.zip?project=${encodeURIComponent(state.project)}`;
        a.textContent = "Download project.zip"; a.className = "btn small"; list.appendChild(a);
      }
      // Unified DB download (SQLite or Postgres)
      const dbMap = res?.artifacts?.db || {};
      for (const [key, meta] of Object.entries(dbMap)) {
        const backend = meta?.backend || "sqlite";
        const label = backend === "pg" ? `${key}.pg.zip` : `${key}.sqlite`;
        const a = document.createElement("a");
        a.href = `${API_BASE}/download/${encodeURIComponent(backup_id)}/db/${encodeURIComponent(key)}?project=${encodeURIComponent(state.project)}`;
        a.textContent = `Download ${label}`;
        a.className = "btn small";
        list.appendChild(a);
      }
      down.appendChild(list); openDetails();
    } catch (err) { dbg("details:error", String(err)); toast(`Load details failed: ${err.message || err}`, "error"); }
  }

  async function onCloneRestore(backup_id) {
    if (!state.project) return toast("Select a project first", "warn");

    // Respect CANCEL on either prompt (null means user hit Cancel; "" means OK with empty)
    const nameInput = prompt("New project name (leave blank for default suggestion):");
    if (nameInput === null) { toast("Clone cancelled", "info"); return; }

    const scopeInput = prompt('Scope: leave blank, or enter "db_only" or "files_only"');
    if (scopeInput === null) { toast("Clone cancelled", "info"); return; }

    const newName = nameInput.trim() || null; // empty string → default on server
    const scope = scopeInput.trim() || null;  // empty string → full scope

    // Optional safety confirmation
    const summary =
      `Clone ${state.project} from backup ${backup_id}\n` +
      `New project: ${newName || "(default)"}\n` +
      `Scope: ${scope || "(full)"}`;
    if (!confirm(summary + "\nProceed?")) { toast("Clone cancelled", "info"); return; }

    const payload = { project: state.project, mode: "clone", new_project: newName, scope };
    dbg("restore:payload", payload);
    try {
      const res = await fetchJSON(`${API_BASE}/restore/${encodeURIComponent(backup_id)}`, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      dbg("restore:ok", res);
      toast(`Cloned to: ${res?.new_project || "(unknown)"}`, "ok", 3000);
    } catch (err) {
      dbg("restore:error", String(err));
      toast(`Restore failed: ${err.message || err}`, "error", 3600);
    }
  }

  async function onDelete(backup_id) {
    if (!state.project) return toast("Select a project first", "warn");
    if (!confirm("Delete this backup? This cannot be undone.")) return;
    try {
      const res = await fetchJSON(`${API_BASE}/backups/${encodeURIComponent(backup_id)}`, { method: "DELETE", body: JSON.stringify({ project: state.project }) });
      dbg("delete:ok", res); toast("Backup deleted", "ok"); loadBackups();
    } catch (err) { dbg("delete:error", String(err)); toast(`Delete failed: ${err.message || err}`, "error"); }
  }

  // ---------------------------
  // Schedules
  // ---------------------------
  function applyFrequencyVisibility() {
    const f = $("#schFrequency").value;
    $$(".conditional.weekly").forEach(el => el.classList.toggle("hidden", f !== "weekly"));
    $$(".conditional.monthly").forEach(el => el.classList.toggle("hidden", f !== "monthly"));
    // Hour/minute always visible; for hourly: hour is ignored but we keep it visible to keep UI simple.
  }

  async function loadSchedules() {
    if (!state.project) return;
    try {
      const data = await fetchJSON(`${API_BASE}/schedules?project=${encodeURIComponent(state.project)}`);
      state.schedules = Array.isArray(data) ? data : [];
      renderSchedules();
    } catch (err) { dbg("schedules:load:error", String(err)); toast("Failed to load schedules", "error"); }
  }

  function renderSchedules() {
    const tb = $("#schedulesTbody"); tb.innerHTML = "";
    for (const s of state.schedules) {
      const tr = document.createElement("tr");
      const tType = document.createElement("td"); tType.textContent = s.type;
      const tWhen = document.createElement("td"); tWhen.textContent = fmtWhen(s);
      const tKeep = document.createElement("td"); tKeep.textContent = s.retention_keep ?? "—";
      const tEnabled = document.createElement("td");
      const chk = document.createElement("input"); chk.type = "checkbox"; chk.checked = !!s.enabled;
      chk.addEventListener("change", () => toggleSchedule(s.id, chk.checked));
      tEnabled.appendChild(chk);
      const tNextLast = document.createElement("td"); tNextLast.innerHTML = `<div class="small-muted">next</div>${s.next_run_at || "—"}<div class="small-muted mt4">last</div>${s.last_run_at || "—"}`;
      const tActions = document.createElement("td"); tActions.className = "actions-cell";
      tActions.appendChild(btn("Run now", "small", () => runScheduleNow(s)));
      tActions.appendChild(spacer());
      tActions.appendChild(btn("Delete", "small danger", () => deleteSchedule(s.id)));
      tr.append(tType, tWhen, tKeep, tEnabled, tNextLast, tActions); tb.appendChild(tr);
    }
  }

  async function createSchedule() {
    if (!state.project) return toast("Select a project first", "warn");
    const payload = {
      project: state.project,
      type: $("#schType").value,
      frequency: $("#schFrequency").value,
      hour: Number($("#schHour").value || 0),
      minute: Number($("#schMinute").value || 0),
      dow: $("#schFrequency").value === "weekly" ? Number($("#schDow").value || 0) : null,
      dom: $("#schFrequency").value === "monthly" ? Number($("#schDom").value || 1) : null,
      retention_keep: Number($("#schKeep").value || 10),
      enabled: true,
      notes: $("#schNotes").value.trim() || null
    };
    dbg("schedule:create", payload);
    try {
      await fetchJSON(`${API_BASE}/schedules`, { method: "POST", body: JSON.stringify(payload) });
      toast("Schedule created", "ok"); loadSchedules();
    } catch (err) { dbg("schedule:create:error", String(err)); toast(`Create failed: ${err.message || err}`, "error"); }
  }

  async function toggleSchedule(id, enabled) {
    const s = state.schedules.find(x => x.id === id);
    if (!s) return;
    const payload = { ...s, enabled };
    dbg("schedule:update:toggle", payload);
    try {
      await fetchJSON(`${API_BASE}/schedules`, { method: "POST", body: JSON.stringify(payload) });
      toast(enabled ? "Schedule enabled" : "Schedule disabled", "info");
      loadSchedules();
    } catch (err) { dbg("schedule:update:error", String(err)); toast(`Update failed: ${err.message || err}`, "error"); }
  }

  async function deleteSchedule(id) {
    if (!confirm("Delete this schedule?")) return;
    try { await fetchJSON(`${API_BASE}/schedules/${encodeURIComponent(id)}`, { method: "DELETE" }); toast("Schedule deleted", "ok"); loadSchedules(); }
    catch (err) { dbg("schedule:delete:error", String(err)); toast(`Delete failed: ${err.message || err}`, "error"); }
  }

  async function runScheduleNow(s) {
    // "Run now" = directly call backup-now with the schedule’s config (always to local backups folder)
    const payload = {
      project: s.project,
      type: s.type,
      paranoid: false,
      notes: `(manual run of ${s.frequency} schedule)`
    };
    dbg("schedule:run-now", payload);
    try { await fetchJSON(`${API_BASE}/backup-now`, { method: "POST", body: JSON.stringify(payload) }); toast("Backup started (run now)", "ok"); loadBackups(); }
    catch (err) { dbg("schedule:run-now:error", String(err)); toast(`Run now failed: ${err.message || err}`, "error"); }
  }

  async function tickSchedules() {
    try {
      const res = await fetchJSON(`${API_BASE}/schedule/tick`, { method: "POST", body: JSON.stringify({}) });
      dbg("schedule:tick:ok", res);
      if (res?.ran?.length) toast(`Ran ${res.ran.length} schedule(s)`, "ok");
      await Promise.all([loadBackups(), loadSchedules()]);
    } catch (err) { dbg("schedule:tick:error", String(err)); toast(`Tick failed: ${err.message || err}`, "error"); }
  }

  // ---------------------------
  // Modal & UI helpers
  // ---------------------------
  function btn(text, cls, onClick) { const b = document.createElement("button"); b.textContent = text; b.className = `btn ${cls || ""}`.trim(); b.addEventListener("click", onClick); return b; }
  function spacer() { const s = document.createElement("span"); s.className = "spacer"; return s; }
  function openDetails() { $("#detailsModal").classList.remove("hidden"); }
  function closeDetails() { $("#detailsModal").classList.add("hidden"); }

  // ---------------------------
  // Init / bindings
  // ---------------------------
  function bindUI() {
    $("#loadBtn").addEventListener("click", onLoadProject);
    $("#refreshBtn").addEventListener("click", () => {
      if (!state.project) return toast("Select a project first", "warn");
      loadBackups();
    });
    $("#backupNowBtn").addEventListener("click", onBackupNow);
    $("#detailsClose").addEventListener("click", closeDetails);
    $("#detailsClose2").addEventListener("click", closeDetails);

    // Schedules
    $("#createScheduleBtn").addEventListener("click", createSchedule);
    $("#refreshSchedulesBtn").addEventListener("click", loadSchedules);
    $("#tickBtn").addEventListener("click", tickSchedules);
    $("#schFrequency").addEventListener("change", applyFrequencyVisibility);
    applyFrequencyVisibility();

    // Debug panel controls
    $("#debugToggle").addEventListener("change", () => dbg("debug toggled"));
    $("#clearDebugBtn").addEventListener("click", () => { $("#debugLog").textContent = ""; });

    // Boot
    loadProjectsList();
  }

  window.addEventListener("DOMContentLoaded", () => {
    bindUI();
    dbg("ui-ready");
  });
})();
