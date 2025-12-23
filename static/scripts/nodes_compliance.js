// Debug control - set to false to disable all grid debug logging
const DEBUG_ENABLED = false;
const debug = DEBUG_ENABLED ? console.debug.bind(console) : () => {};

(() => {
  const API_BASE = "/api/nodes_compliance";

  // Helper to get auth token from localStorage
  function getAuthHeaders() {
    const token = localStorage.getItem("gims_token");
    if (token) {
      return { "Authorization": `Bearer ${token}` };
    }
    return {};
  }

  async function checkLogin() {
    const resp = await fetch("/login/auth/me", {
      headers: getAuthHeaders()
    });
    return resp.ok;
  }

  const el = (id) => document.getElementById(id);

  // ───────── Dual-panel loader controller
  const ready = { comp: false, audit: false };
  function $overlay() {
    return (
      document.getElementById("page-loading") ||
      document.getElementById("loadingOverlay") ||
      document.querySelector(".page-loading")
    );
  }
  function showLoading(show, label) {
    const ov = $overlay();
    if (!ov) return;
    if (show) {
      if (label) ov.setAttribute("data-label", label);
      ov.classList.add("show");
      ov.hidden = false;
      ov.style.display = "grid";
      ov.setAttribute("aria-hidden", "false");
      debug("show", { ov });
    } else {
      ov.classList.remove("show");
      ov.hidden = true;
      ov.style.display = "none";
      ov.setAttribute("aria-hidden", "true");
      debug("hide", { ov });
    }
  }
  function beginDualLoad(label = "Loading…") {
    ready.comp = false;
    ready.audit = false;
    showLoading(true, label);
  }
  function markReady(kind) {
    ready[kind] = true;
    if (ready.comp && ready.audit) {
      showLoading(false);
    }
  }

  // App state
  const state = {
    project: null,
    projects: [],
    comp: {
      limit: 100,
      offset: 0,
      total: 0,
      order_by: null,
      order_dir: "desc",
      columns: [],
    },
    audit: {
      limit: 100,
      offset: 0,
      total: 0,
      order_by: null,
      order_dir: "desc",
      columns: [],
    },
    filewatch: {
      folders: [],
      systemAvailable: false,
      hasAuth: false,
      healthCheckOk: false,
      refreshInterval: null,
    },
  };

  // ---------- Utilities ----------
  function showError(msg) {
    const box = el("errorBox");
    if (!box) return;
    box.textContent = msg || "";
    box.hidden = !msg;
  }

  function toDateTimeLocalValue(d) {
    // returns "YYYY-MM-DDTHH:mm"
    const pad = (n) => (n < 10 ? "0" + n : "" + n);
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(
      d.getHours()
    )}:${pad(d.getMinutes())}`;
  }

  function readDateTimeLocalValue(input) {
    // backend compares strings; we'll keep "YYYY-MM-DDTHH:mm:ss" for clarity
    if (!input || !input.value) return null;
    const v = input.value;
    return v.length === 16 ? v + ":00" : v; // normalize to seconds
  }

  function qs(params) {
    const u = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== null && v !== undefined && v !== "") u.set(k, v);
    });
    return u.toString();
  }

  function formatCell(val) {
    if (val === null || val === undefined) return "";
    if (typeof val === "object") return JSON.stringify(val);
    return String(val);
  }

  function setStatus(kind, text) {
    const s = el(`${kind}-status`);
    if (s) s.textContent = text;
  }

  function setPagerDisabled(kind, prevDisabled, nextDisabled) {
    const p = el(`${kind}-prev`);
    const n = el(`${kind}-next`);
    if (p) p.disabled = prevDisabled;
    if (n) n.disabled = nextDisabled;
  }

  function buildTable(kind, rows, columns) {
    const container = el(`${kind}-table`);
    if (!container) return;
    container.innerHTML = "";

    const table = document.createElement("table");
    table.className = "datagrid";

    const thead = document.createElement("thead");
    const trh = document.createElement("tr");

    columns.forEach((c) => {
      const th = document.createElement("th");
      th.textContent = c;
      th.dataset.col = c;
      th.title = "Click to sort by " + c;
      th.addEventListener("click", () => {
        const s = state[kind];
        if (s.order_by === c) {
          s.order_dir = s.order_dir === "desc" ? "asc" : "desc";
        } else {
          s.order_by = c;
          s.order_dir = "asc";
        }
        debug(`[${kind}] header sort`, { order_by: s.order_by, order_dir: s.order_dir });
        beginDualLoad("Sorting…");
        fetchTable("comp");
        fetchTable("audit");
      });
      trh.appendChild(th);
    });
    thead.appendChild(trh);

    const tbody = document.createElement("tbody");
    for (const r of rows) {
      const tr = document.createElement("tr");
      columns.forEach((c) => {
        const td = document.createElement("td");
        td.textContent = formatCell(r[c]);
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    }

    table.appendChild(thead);
    table.appendChild(tbody);
    container.appendChild(table);
  }

  // ---------- Projects ----------
  async function loadProjects() {
    debug("[projects] fetching...");
    showLoading(true, "Loading projects…");
    try {
      const res = await fetch(`${API_BASE}/projects`);
      if (!res.ok) throw new Error(`Failed to list projects: ${res.status}`);
      const arr = await res.json();
      state.projects = Array.isArray(arr) ? arr : [];
      debug("[projects] found:", state.projects);

      const sel = el("projectSelect");
      if (sel) {
        sel.innerHTML = "";
        state.projects.forEach((p) => {
          const opt = document.createElement("option");
          opt.value = p;
          opt.textContent = p;
          sel.appendChild(opt);
        });

        if (!state.project && state.projects.length > 0) {
          state.project = state.projects[0];
          sel.value = state.project;
        }
      }
    } catch (e) {
      showError(e.message);
      debug("[projects] error:", e);
    } finally {
      showLoading(false);
    }
  }

  // ---------- Schema ----------
  async function loadSchema(kind) {
    const project = state.project;
    const table = kind === "comp" ? "compliance_log" : "audit_log";
    const orderSel = el(`${kind}-order-by`);

    debug(`[${kind}] loading schema`, { project, table });
    showLoading(true, `Loading ${table} schema…`);
    try {
      const res = await fetch(
        `${API_BASE}/${encodeURIComponent(project)}/schema/${encodeURIComponent(table)}`
      );
      if (!res.ok) throw new Error(`Failed to load schema for ${table}: ${res.status}`);
      const data = await res.json();
      const cols = data?.columns?.map((c) => c.name) || [];

      state[kind].columns = cols;

      // Populate order_by dropdown
      if (orderSel) {
        orderSel.innerHTML = "";
        cols.forEach((c) => {
          const opt = document.createElement("option");
          opt.value = c;
          opt.textContent = c;
          orderSel.appendChild(opt);
        });
      }

      // Default ordering preference (include timestamp_utc/ts before id)
      const preferred = ["timestamp", "timestamp_utc", "ts", "created_at", "id"];
      state[kind].order_by = preferred.find((x) => cols.includes(x)) || cols[0] || null;
      if (orderSel && state[kind].order_by) orderSel.value = state[kind].order_by;

      debug(`[${kind}] schema loaded`, { columns: cols, order_by: state[kind].order_by });
    } catch (e) {
      showError(e.message);
      debug(`[${kind}] schema error:`, e);
    } finally {
      showLoading(false);
    }
  }

  // ---------- Fetch tables ----------
  function readCommonParams(kind) {
    const order_by = el(`${kind}-order-by`)?.value || state[kind].order_by;
    const order_dir_btn = el(`${kind}-order-dir`);
    const order_dir = (order_dir_btn?.textContent || state[kind].order_dir || "desc").toLowerCase();
    const limit = parseInt(el(`${kind}-limit`)?.value || state[kind].limit, 10);
    const start = readDateTimeLocalValue(el(`${kind}-start`));
    const end = readDateTimeLocalValue(el(`${kind}-end`));
    const search = (el(`${kind}-search`)?.value || "").trim();

    return { order_by, order_dir, limit, start, end, search };
  }

  async function fetchTable(kind) {
    const project = state.project;
    const s = state[kind];

    const isCompliance = kind === "comp";
    const path = isCompliance ? "compliance" : "audit";

    const common = readCommonParams(kind);
    s.limit = common.limit;
    s.order_by = common.order_by || s.order_by;
    s.order_dir = common.order_dir;

    let filters = {};
    if (isCompliance) {
      filters = {
        actor: el("comp-actor")?.value.trim(),
        action: el("comp-action")?.value.trim(),
        node: el("comp-node")?.value.trim(),
        module: el("comp-module")?.value.trim(),
      };
    } else {
      filters = {
        user_id: el("audit-user")?.value.trim(),
        run_id: el("audit-run")?.value.trim(),
        verb: el("audit-verb")?.value.trim(),
      };
    }

    const params = {
      limit: s.limit,
      offset: s.offset,
      order_by: s.order_by,
      order_dir: s.order_dir,
      search: common.search || undefined,
      start: common.start || undefined,
      end: common.end || undefined,
      ...filters,
    };

    const url = `${API_BASE}/${encodeURIComponent(project)}/${path}?${qs(params)}`;
    debug(`[${kind}] fetch`, { url, params });

    showError("");
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`Fetch failed: ${res.status} ${res.statusText}`);
      const data = await res.json();

      s.total = data.total || 0;
      // columns from backend for safety
      const cols = Array.isArray(data.columns) && data.columns.length ? data.columns : s.columns;
      buildTable(kind, data.rows || [], cols);

      const pageStart = Math.min(s.offset + 1, s.total);
      const pageEnd = Math.min(s.offset + s.limit, s.total);
      setStatus(
        kind,
        `${s.total} rows • showing ${pageStart || 0}-${pageEnd || 0} • sorted by ${s.order_by} ${s.order_dir.toUpperCase()}`
      );
      setPagerDisabled(kind, s.offset <= 0, s.offset + s.limit >= s.total);

      debug(`[${kind}] loaded`, { count: (data.rows || []).length, total: s.total });

      // mark panel as ready; hide overlay if both done
      markReady(kind);
    } catch (e) {
      showError(e.message);
      debug(`[${kind}] error`, e);
      // ensure overlay doesn't stick if one panel errors
      showLoading(false);
    }
  }

  function resetFilters(kind) {
    debug(`[${kind}] reset filters`);
    const set = (id, v = "") => {
      const x = el(id);
      if (x) x.value = v;
    };

    set(`${kind}-search`);
    set(`${kind}-start`);
    set(`${kind}-end`);

    if (kind === "comp") {
      set("comp-actor");
      set("comp-action");
      set("comp-node");
      set("comp-module");
    } else {
      set("audit-user");
      set("audit-run");
      set("audit-verb");
    }

    state[kind].offset = 0;
  }

  // ---------- Export ----------
  function exportFile(kind, type /* 'csv' | 'json' */) {
    const project = state.project;
    const s = state[kind];
    const isCompliance = kind === "comp";
    const path = isCompliance ? "compliance" : "audit";
    const common = readCommonParams(kind);

    let filters = {};
    if (isCompliance) {
      filters = {
        actor: el("comp-actor")?.value.trim(),
        action: el("comp-action")?.value.trim(),
        node: el("comp-node")?.value.trim(),
        module: el("comp-module")?.value.trim(),
      };
    } else {
      filters = {
        user_id: el("audit-user")?.value.trim(),
        run_id: el("audit-run")?.value.trim(),
        verb: el("audit-verb")?.value.trim(),
      };
    }

    const params = {
      order_by: s.order_by,
      order_dir: s.order_dir,
      search: common.search || undefined,
      start: common.start || undefined,
      end: common.end || undefined,
      ...filters,
    };

    const url = `${API_BASE}/${encodeURIComponent(project)}/${path}/export.${type}?${qs(params)}`;
    debug(`[${kind}] export`, { url, params });
    window.open(url, "_blank");
  }

  // ---------- Event wiring ----------
  function wireTabs() {
    document.querySelectorAll(".tab").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
        document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
        btn.classList.add("active");
        const tgt = el(btn.dataset.target);
        if (tgt) tgt.classList.add("active");
      });
    });
  }

  function wireCompliance() {
    el("comp-apply")?.addEventListener("click", () => {
      state.comp.offset = 0;
      beginDualLoad("Applying filters…");
      fetchTable("comp");
      fetchTable("audit");
    });
    el("comp-reset")?.addEventListener("click", () => {
      resetFilters("comp");
      beginDualLoad("Resetting…");
      fetchTable("comp");
      fetchTable("audit");
    });
    el("comp-prev")?.addEventListener("click", () => {
      state.comp.offset = Math.max(0, state.comp.offset - state.comp.limit);
      beginDualLoad("Loading…");
      fetchTable("comp");
      fetchTable("audit");
    });
    el("comp-next")?.addEventListener("click", () => {
      if (state.comp.offset + state.comp.limit < state.comp.total) {
        state.comp.offset += state.comp.limit;
        beginDualLoad("Loading…");
        fetchTable("comp");
        fetchTable("audit");
      }
    });
    el("comp-order-dir")?.addEventListener("click", () => {
      const b = el("comp-order-dir");
      if (!b) return;
      b.textContent = b.textContent.toUpperCase() === "DESC" ? "ASC" : "DESC";
    });
    el("comp-export-csv")?.addEventListener("click", () => exportFile("comp", "csv"));
    el("comp-export-json")?.addEventListener("click", () => exportFile("comp", "json"));
    el("comp-limit")?.addEventListener("change", () => {
      state.comp.offset = 0;
      beginDualLoad("Loading…");
      fetchTable("comp");
      fetchTable("audit");
    });
    el("comp-search")?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        state.comp.offset = 0;
        beginDualLoad("Searching…");
        fetchTable("comp");
        fetchTable("audit");
      }
    });
  }

  function wireAudit() {
    el("audit-apply")?.addEventListener("click", () => {
      state.audit.offset = 0;
      beginDualLoad("Applying filters…");
      fetchTable("audit");
      fetchTable("comp");
    });
    el("audit-reset")?.addEventListener("click", () => {
      resetFilters("audit");
      beginDualLoad("Resetting…");
      fetchTable("audit");
      fetchTable("comp");
    });
    el("audit-prev")?.addEventListener("click", () => {
      state.audit.offset = Math.max(0, state.audit.offset - state.audit.limit);
      beginDualLoad("Loading…");
      fetchTable("audit");
      fetchTable("comp");
    });
    el("audit-next")?.addEventListener("click", () => {
      if (state.audit.offset + state.audit.limit < state.audit.total) {
        state.audit.offset += state.audit.limit;
        beginDualLoad("Loading…");
        fetchTable("audit");
        fetchTable("comp");
      }
    });
    el("audit-order-dir")?.addEventListener("click", () => {
      const b = el("audit-order-dir");
      if (!b) return;
      b.textContent = b.textContent.toUpperCase() === "DESC" ? "ASC" : "DESC";
    });
    el("audit-export-csv")?.addEventListener("click", () => exportFile("audit", "csv"));
    el("audit-export-json")?.addEventListener("click", () => exportFile("audit", "json"));
    el("audit-limit")?.addEventListener("change", () => {
      state.audit.offset = 0;
      beginDualLoad("Loading…");
      fetchTable("audit");
      fetchTable("comp");
    });
    el("audit-search")?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        state.audit.offset = 0;
        beginDualLoad("Searching…");
        fetchTable("audit");
        fetchTable("comp");
      }
    });
  }

  function wireGlobal() {
    el("projectSelect")?.addEventListener("change", async () => {
      state.project = el("projectSelect").value;
      debug("[project] changed", state.project);
      beginDualLoad("Loading schema…");
      await loadSchema("comp");
      await loadSchema("audit");
      state.comp.offset = 0;
      state.audit.offset = 0;
      beginDualLoad("Loading logs…");
      fetchTable("comp");
      fetchTable("audit");
      
      // Refresh file watcher for new project
      await refreshFilewatchStatus();
    });

    el("refreshAll")?.addEventListener("click", async () => {
      debug("[refresh] manual");
      beginDualLoad("Refreshing…");
      fetchTable("comp");
      fetchTable("audit");
      await refreshFilewatchStatus();
    });
  }

  // ---------- File Watcher Functions ----------

  function updateStatusIndicator(elementId, isOk, label = "") {
    debug("[filewatch] updateStatusIndicator:start", { elementId, isOk, label });
    const el = document.getElementById(elementId);
    debug("[filewatch] updateStatusIndicator:element", { elementFound: !!el });

    if (!el) {
      debug("[filewatch] updateStatusIndicator:abort_no_element", { elementId });
      return;
    }
    
    el.className = "fw-indicator";

    if (isOk === null) {
      el.classList.add("unknown");
      el.title = label || "Unknown";
      debug("[filewatch] updateStatusIndicator:set_unknown", { title: el.title });
    } else if (isOk) {
      el.classList.add("green");
      el.title = label || "OK";
      debug("[filewatch] updateStatusIndicator:set_green", { title: el.title });
    } else {
      el.classList.add("red");
      el.title = label || "Error";
      debug("[filewatch] updateStatusIndicator:set_red", { title: el.title });
    }

    debug("[filewatch] updateStatusIndicator:done", {
      elementId,
      className: el.className,
      title: el.title,
    });
  }

  function updateWatcherBanner(hasWatchers, hasActiveWatchers) {
    debug("[filewatch] updateWatcherBanner:start", { hasWatchers, hasActiveWatchers });
    
    let banner = el("fw-status-banner");
    
    // Create banner if it doesn't exist
    if (!banner) {
      banner = document.createElement("div");
      banner.id = "fw-status-banner";
      banner.className = "fw-status-banner";
      
      // Insert after the fw-header, before fw-add-folder
      const filewatchPanel = document.getElementById("panel-filewatcher");
      const addFolderSection = document.querySelector(".fw-add-folder");
      if (filewatchPanel && addFolderSection) {
        filewatchPanel.insertBefore(banner, addFolderSection);
        debug("[filewatch] updateWatcherBanner:banner_created");
      } else {
        debug("[filewatch] updateWatcherBanner:no_panel_found");
        return;
      }
    }
    
    // Count active watchers
    const activeCount = state.filewatch.folders ? state.filewatch.folders.filter(f => f.active).length : 0;
    
    // Update banner content and visibility
    if (!hasWatchers) {
      banner.className = "fw-status-banner warning";
      banner.innerHTML = `
        <div class="fw-banner-icon">⚠️</div>
        <div class="fw-banner-content">
          <div class="fw-banner-title">No Folders Configured</div>
          <div class="fw-banner-message">Add a folder below to start watching for new files</div>
        </div>
      `;
      banner.style.display = "flex";
      debug("[filewatch] updateWatcherBanner:showing_no_folders");
    } else if (!hasActiveWatchers) {
      banner.className = "fw-status-banner warning";
      banner.innerHTML = `
        <div class="fw-banner-icon">⏸️</div>
        <div class="fw-banner-content">
          <div class="fw-banner-title">File Watchers Not Active</div>
          <div class="fw-banner-message">Click "Start All" or start individual watchers to begin monitoring for new files</div>
        </div>
      `;
      banner.style.display = "flex";
      debug("[filewatch] updateWatcherBanner:showing_not_active");
    } else {
      banner.className = "fw-status-banner success";
      banner.innerHTML = `
        <div class="fw-banner-icon">✓</div>
        <div class="fw-banner-content">
          <div class="fw-banner-title">File Watchers Active</div>
          <div class="fw-banner-message">Monitoring ${activeCount} folder(s) for new files</div>
        </div>
      `;
      banner.style.display = "flex";
      debug("[filewatch] updateWatcherBanner:showing_active");
    }
    
    debug("[filewatch] updateWatcherBanner:done");
  }

  function formatTimestamp(ts) {
    debug("[filewatch] formatTimestamp:start", { ts });
    if (!ts) {
      debug("[filewatch] formatTimestamp:no_timestamp");
      return "—";
    }
    const d = new Date(ts * 1000);
    const val = d.toLocaleString();
    debug("[filewatch] formatTimestamp:done", { ts, formatted: val });
    return val;
  }

  function formatBytes(bytes) {
    debug("[filewatch] formatBytes:start", { bytes });
    if (bytes < 1024) {
      const val = bytes + " B";
      debug("[filewatch] formatBytes:bytes", { result: val });
      return val;
    }
    if (bytes < 1024 * 1024) {
      const val = (bytes / 1024).toFixed(1) + " KB";
      debug("[filewatch] formatBytes:kb", { result: val });
      return val;
    }
    const val = (bytes / (1024 * 1024)).toFixed(1) + " MB";
    debug("[filewatch] formatBytes:mb", { result: val });
    return val;
  }

  async function filewatchConfigure(folders) {
    debug("[filewatch] filewatchConfigure:start", { folders });
    const url = `${API_BASE}/${state.project}/filewatch/configure`;
    debug("[filewatch] filewatchConfigure:url", { url });

    const resp = await fetch(url, {
      method: "POST",
      headers: { 
        "Content-Type": "application/json",
        ...getAuthHeaders()
      },
      body: JSON.stringify({ folders }),
    });

    debug("[filewatch] filewatchConfigure:response", {
      ok: resp.ok,
      status: resp.status,
    });

    if (!resp.ok) {
      debug("[filewatch] filewatchConfigure:error", { status: resp.status });
      throw new Error(`Configure failed: ${resp.status}`);
    }

    const data = await resp.json();
    debug("[filewatch] filewatchConfigure:done", { data });
    return data;
  }

  async function filewatchStart(folders = []) {
    debug("[filewatch] filewatchStart:start", { folders });
    const url = `${API_BASE}/${state.project}/filewatch/start`;
    debug("[filewatch] filewatchStart:url", { url });

    const resp = await fetch(url, {
      method: "POST",
      headers: { 
        "Content-Type": "application/json",
        ...getAuthHeaders()
      },
      body: JSON.stringify({ folders }),
    });

    debug("[filewatch] filewatchStart:response", {
      ok: resp.ok,
      status: resp.status,
    });

    if (!resp.ok) {
      debug("[filewatch] filewatchStart:error", { status: resp.status });
      throw new Error(`Start failed: ${resp.status}`);
    }

    const data = await resp.json();
    debug("[filewatch] filewatchStart:done", { data });
    return data;
  }

  async function filewatchStop(folders = []) {
    debug("[filewatch] filewatchStop:start", { folders });
    const url = `${API_BASE}/${state.project}/filewatch/stop`;
    debug("[filewatch] filewatchStop:url", { url });

    const resp = await fetch(url, {
      method: "POST",
      headers: { 
        "Content-Type": "application/json",
        ...getAuthHeaders()
      },
      body: JSON.stringify({ folders }),
    });

    debug("[filewatch] filewatchStop:response", {
      ok: resp.ok,
      status: resp.status,
    });

    if (!resp.ok) {
      debug("[filewatch] filewatchStop:error", { status: resp.status });
      throw new Error(`Stop failed: ${resp.status}`);
    }

    const data = await resp.json();
    debug("[filewatch] filewatchStop:done", { data });
    return data;
  }

  async function filewatchStatus() {
    debug("[filewatch] filewatchStatus:start", { project: state.project });
    const url = `${API_BASE}/${state.project}/filewatch/status`;
    debug("[filewatch] filewatchStatus:url", { url });

    const resp = await fetch(url, {
      headers: getAuthHeaders()
    });

    debug("[filewatch] filewatchStatus:response", {
      ok: resp.ok,
      status: resp.status,
    });

    if (!resp.ok) {
      debug("[filewatch] filewatchStatus:error", { status: resp.status });
      throw new Error(`Status failed: ${resp.status}`);
    }

    const data = await resp.json();
    debug("[filewatch] filewatchStatus:done", { data });
    return data;
  }

  async function filewatchRemoveFolder(folder) {
    debug("[filewatch] filewatchRemoveFolder:start", { folder });
    const url = `${API_BASE}/${state.project}/filewatch/folder`;
    debug("[filewatch] filewatchRemoveFolder:url", { url });

    const resp = await fetch(url, {
      method: "DELETE",
      headers: { 
        "Content-Type": "application/json",
        ...getAuthHeaders()
      },
      body: JSON.stringify({ folder }),
    });

    debug("[filewatch] filewatchRemoveFolder:response", {
      ok: resp.ok,
      status: resp.status,
    });

    if (!resp.ok) {
      debug("[filewatch] filewatchRemoveFolder:error", { status: resp.status });
      throw new Error(`Remove failed: ${resp.status}`);
    }

    const data = await resp.json();
    debug("[filewatch] filewatchRemoveFolder:done", { data });
    return data;
  }

  async function filewatchHealthCheck() {
    debug("[filewatch] filewatchHealthCheck:start");
    const url = `${API_BASE}/${state.project}/filewatch/health-check`;
    debug("[filewatch] filewatchHealthCheck:url", { url });

    const resp = await fetch(url, {
      method: "POST",
      headers: getAuthHeaders()
    });

    debug("[filewatch] filewatchHealthCheck:response", {
      ok: resp.ok,
      status: resp.status,
    });

    if (!resp.ok) {
      debug("[filewatch] filewatchHealthCheck:error", { status: resp.status });
      throw new Error(`Health check failed: ${resp.status}`);
    }

    const data = await resp.json();
    debug("[filewatch] filewatchHealthCheck:done", { data });
    return data;
  }

  function renderFolderItem(watcher) {
    debug("[filewatch] renderFolderItem:start", { watcher });

    const div = document.createElement("div");
    div.className = "fw-folder-item";
    div.dataset.folder = watcher.folder;

    const statusClass = watcher.active ? "green" : (watcher.exists ? "gray" : "red");
    const statusTitle = watcher.active ? "Active" : (watcher.exists ? "Stopped" : "Folder not found");

    debug("[filewatch] renderFolderItem:status", {
      folder: watcher.folder,
      statusClass,
      statusTitle,
    });

    const lastEventInfo = watcher.last_event 
      ? `${watcher.last_event.filename} (${formatBytes(watcher.last_event.size)}) at ${formatTimestamp(watcher.last_event.timestamp)}`
      : "No events yet";

    debug("[filewatch] renderFolderItem:lastEventInfo", { lastEventInfo });

    div.innerHTML = `
      <div class="fw-folder-header">
        <span class="fw-indicator ${statusClass}" title="${statusTitle}"></span>
        <span class="fw-folder-path" title="${watcher.folder}">${watcher.folder}</span>
        <div class="fw-folder-actions">
          ${watcher.active 
            ? `<button class="btn btn-sm fw-stop-btn" data-folder="${watcher.folder}">Stop</button>`
            : `<button class="btn btn-sm btn-primary fw-start-btn" data-folder="${watcher.folder}">Start</button>`
          }
          <button class="btn btn-sm fw-remove-btn" data-folder="${watcher.folder}">Remove</button>
        </div>
      </div>
      <div class="fw-folder-details">
        <div class="fw-stat">
          <span class="fw-stat-label">Detected:</span>
          <span class="fw-stat-value">${watcher.files_detected || 0}</span>
        </div>
        <div class="fw-stat">
          <span class="fw-stat-label">Logged:</span>
          <span class="fw-stat-value">${watcher.files_logged || 0}</span>
        </div>
        <div class="fw-stat">
          <span class="fw-stat-label">Last Event:</span>
          <span class="fw-stat-value">${lastEventInfo}</span>
        </div>
        ${watcher.last_error ? `<div class="fw-error">⚠ ${watcher.last_error}</div>` : ''}
      </div>
    `;

    debug("[filewatch] renderFolderItem:done", {
      folder: watcher.folder,
      htmlSnippet: div.innerHTML.slice(0, 200),
    });

    return div;
  }

  function renderRecentEvents(events) {
    debug("[filewatch] renderRecentEvents:start", {
      count: events ? events.length : 0,
    });

    const container = el("fw-events-list");
    debug("[filewatch] renderRecentEvents:container", { found: !!container });

    if (!container) {
      debug("[filewatch] renderRecentEvents:abort_no_container");
      return;
    }

    if (!events || events.length === 0) {
      container.innerHTML = '<p class="fw-no-events">No events yet</p>';
      debug("[filewatch] renderRecentEvents:empty");
      return;
    }

    container.innerHTML = events.map(event => {
      const statusIcon = event.logged ? "✓" : (event.error ? "✗" : "⋯");
      const statusClass = event.logged ? "success" : (event.error ? "error" : "pending");

      debug("[filewatch] renderRecentEvents:event", {
        filename: event.filename,
        size: event.size,
        timestamp: event.timestamp,
        logged: event.logged,
        error: !!event.error,
        statusIcon,
        statusClass,
      });

      return `
        <div class="fw-event-item ${statusClass}">
          <span class="fw-event-status">${statusIcon}</span>
          <span class="fw-event-filename">${event.filename}</span>
          <span class="fw-event-size">${formatBytes(event.size)}</span>
          <span class="fw-event-time">${formatTimestamp(event.timestamp)}</span>
          ${event.error ? `<span class="fw-event-error">${event.error}</span>` : ''}
        </div>
      `;
    }).join('');

    debug("[filewatch] renderRecentEvents:done");
  }

  async function refreshFilewatchStatus() {
    debug("[filewatch] refreshFilewatchStatus:start", {
      project: state.project,
    });

    if (!state.project) {
      debug("[filewatch] refreshFilewatchStatus:abort_no_project");
      return;
    }

    try {
      const status = await filewatchStatus();
      debug("[filewatch] refreshFilewatchStatus:status_received", { status });

      // Update global status indicators
      const watchfilesLabel = status.watchfiles_available
        ? "watchfiles library available"
        : "watchfiles not installed";
      updateStatusIndicator("fw-system-status", status.watchfiles_available, watchfilesLabel);

      const loggedIn = await checkLogin();
      const authLabel = loggedIn ? "Authenticated" : "No auth token";
      updateStatusIndicator("fw-auth-status", loggedIn, authLabel);

      const hasWatchers = status.watchers && status.watchers.length > 0;
      const healthCheckOk = status.health_check_ok;
      const apiLabel =
        healthCheckOk === true ? "API reachable" : 
        healthCheckOk === false ? "API unreachable" : "API check needed";
      updateStatusIndicator(
        "fw-api-status",
        healthCheckOk,
        apiLabel
      );

      debug("[filewatch] refreshFilewatchStatus:indicators_updated", {
        watchfiles_available: status.watchfiles_available,
        has_auth: status.has_auth,
        hasWatchers,
        healthCheckOk,
      });

      state.filewatch.systemAvailable = status.watchfiles_available;
      state.filewatch.hasAuth = loggedIn;
      state.filewatch.healthCheckOk = healthCheckOk;
      state.filewatch.folders = status.watchers; // Update folders FIRST

      // Check if any watchers are active
      const hasActiveWatchers = status.watchers && status.watchers.some(w => w.active);
      updateWatcherBanner(hasWatchers, hasActiveWatchers);

      // Render folder list
      const foldersContainer = el("fw-folders");
      debug("[filewatch] refreshFilewatchStatus:folders_container", {
        found: !!foldersContainer,
      });

      if (foldersContainer) {
        foldersContainer.innerHTML = "";
        const watchers = status.watchers || [];
        debug("[filewatch] refreshFilewatchStatus:watchers_count", {
          count: watchers.length,
        });

        if (watchers.length === 0) {
          foldersContainer.innerHTML =
            '<p class="fw-no-folders">No folders configured. Add a folder to start watching.</p>';
          debug("[filewatch] refreshFilewatchStatus:no_watchers_message_shown");
        } else {
          watchers.forEach(watcher => {
            const item = renderFolderItem(watcher);
            foldersContainer.appendChild(item);
          });
          debug("[filewatch] refreshFilewatchStatus:folders_rendered", {
            count: watchers.length,
          });
        }
      }

      // Render recent events
      renderRecentEvents(status.recent_events);

      debug("[filewatch] refreshFilewatchStatus:state_updated", {
        foldersCount: state.filewatch.folders.length,
      });

    } catch (err) {
      console.error("Failed to refresh file watcher status:", err);
      debug("[filewatch] refreshFilewatchStatus:catch_error", {
        message: err && err.message,
      });
      showError(`File watcher error: ${err.message}`);
    }
  }

  function wireFileWatcher() {
    debug("[filewatch] wireFileWatcher:start");

    // Add folder button
    const addFolderBtn = el("fw-add-folder-btn");
    debug("[filewatch] wireFileWatcher:addFolderBtn", { found: !!addFolderBtn });

    addFolderBtn?.addEventListener("click", async () => {
      debug("[filewatch] wireFileWatcher:addFolder:click");
      const input = el("fw-new-folder");
      const folder = input?.value?.trim();
      debug("[filewatch] wireFileWatcher:addFolder:input", {
        value: input?.value,
        folder,
      });

      if (!folder) {
        debug("[filewatch] wireFileWatcher:addFolder:no_folder");
        showError("Please enter a folder path");
        return;
      }

      try {
        showLoading(true, "Configuring folder...");
        debug("[filewatch] wireFileWatcher:addFolder:configure_start");

        // Get current folders and add the new one
        const currentFolders = state.filewatch.folders.map(f => f.folder);
        const allFolders = [...currentFolders, folder];

        debug("[filewatch] wireFileWatcher:addFolder:folders", {
          currentFolders,
          allFolders,
        });

        await filewatchConfigure(allFolders);
        debug("[filewatch] wireFileWatcher:addFolder:configure_done");

        await refreshFilewatchStatus();
        debug("[filewatch] wireFileWatcher:addFolder:refresh_done");

        if (input) input.value = "";
        showError("");
      } catch (err) {
        debug("[filewatch] wireFileWatcher:addFolder:error", {
          message: err && err.message,
        });
        showError(`Failed to add folder: ${err.message}`);
      } finally {
        showLoading(false);
        debug("[filewatch] wireFileWatcher:addFolder:finally");
      }
    });

    // Start all button
    const startAllBtn = el("fw-start-all");
    debug("[filewatch] wireFileWatcher:startAllBtn", { found: !!startAllBtn });

    startAllBtn?.addEventListener("click", async () => {
      debug("[filewatch] wireFileWatcher:startAll:click");
      try {
        showLoading(true, "Starting watchers...");
        debug("[filewatch] wireFileWatcher:startAll:start_call");
        await filewatchStart();
        debug("[filewatch] wireFileWatcher:startAll:start_done");
        await refreshFilewatchStatus();
        debug("[filewatch] wireFileWatcher:startAll:refresh_done");
        showError("");
      } catch (err) {
        debug("[filewatch] wireFileWatcher:startAll:error", {
          message: err && err.message,
        });
        showError(`Failed to start watchers: ${err.message}`);
      } finally {
        showLoading(false);
        debug("[filewatch] wireFileWatcher:startAll:finally");
      }
    });

    // Stop all button
    const stopAllBtn = el("fw-stop-all");
    debug("[filewatch] wireFileWatcher:stopAllBtn", { found: !!stopAllBtn });

    stopAllBtn?.addEventListener("click", async () => {
      debug("[filewatch] wireFileWatcher:stopAll:click");
      try {
        showLoading(true, "Stopping watchers...");
        debug("[filewatch] wireFileWatcher:stopAll:stop_call");
        await filewatchStop();
        debug("[filewatch] wireFileWatcher:stopAll:stop_done");
        await refreshFilewatchStatus();
        debug("[filewatch] wireFileWatcher:stopAll:refresh_done");
        showError("");
      } catch (err) {
        debug("[filewatch] wireFileWatcher:stopAll:error", {
          message: err && err.message,
        });
        showError(`Failed to stop watchers: ${err.message}`);
      } finally {
        showLoading(false);
        debug("[filewatch] wireFileWatcher:stopAll:finally");
      }
    });

    // Refresh button
    const refreshBtn = el("fw-refresh");
    debug("[filewatch] wireFileWatcher:refreshBtn", { found: !!refreshBtn });

    refreshBtn?.addEventListener("click", async () => {
      debug("[filewatch] wireFileWatcher:refresh:click");
      await refreshFilewatchStatus();
      debug("[filewatch] wireFileWatcher:refresh:done");
    });

    // Health check button
    const healthBtn = el("fw-health-check");
    debug("[filewatch] wireFileWatcher:healthBtn", { found: !!healthBtn });

    healthBtn?.addEventListener("click", async () => {
      debug("[filewatch] wireFileWatcher:health:click");
      try {
        showLoading(true, "Checking health...");
        debug("[filewatch] wireFileWatcher:health:call");
        await filewatchHealthCheck();
        debug("[filewatch] wireFileWatcher:health:call_done");
        await refreshFilewatchStatus();
        debug("[filewatch] wireFileWatcher:health:refresh_done");
        showError("");
      } catch (err) {
        debug("[filewatch] wireFileWatcher:health:error", {
          message: err && err.message,
        });
        showError(`Health check failed: ${err.message}`);
      } finally {
        showLoading(false);
        debug("[filewatch] wireFileWatcher:health:finally");
      }
    });

    // Delegate events for dynamically created buttons
    const foldersRoot = el("fw-folders");
    debug("[filewatch] wireFileWatcher:foldersRoot", { found: !!foldersRoot });

    foldersRoot?.addEventListener("click", async (e) => {
      const target = e.target;
      debug("[filewatch] wireFileWatcher:foldersRoot:click", {
        targetTag: target.tagName,
        classList: [...target.classList],
        dataset: target.dataset,
      });

      if (target.classList.contains("fw-start-btn")) {
        const folder = target.dataset.folder;
        debug("[filewatch] wireFileWatcher:folderStart:click", { folder });

        try {
          showLoading(true, "Starting watcher...");
          await filewatchStart([folder]);
          debug("[filewatch] wireFileWatcher:folderStart:start_done", { folder });
          await refreshFilewatchStatus();
          debug("[filewatch] wireFileWatcher:folderStart:refresh_done", { folder });
          showError("");
        } catch (err) {
          debug("[filewatch] wireFileWatcher:folderStart:error", {
            folder,
            message: err && err.message,
          });
          showError(`Failed to start watcher: ${err.message}`);
        } finally {
          showLoading(false);
          debug("[filewatch] wireFileWatcher:folderStart:finally", { folder });
        }
      }

      else if (target.classList.contains("fw-stop-btn")) {
        const folder = target.dataset.folder;
        debug("[filewatch] wireFileWatcher:folderStop:click", { folder });

        try {
          showLoading(true, "Stopping watcher...");
          await filewatchStop([folder]);
          debug("[filewatch] wireFileWatcher:folderStop:stop_done", { folder });
          await refreshFilewatchStatus();
          debug("[filewatch] wireFileWatcher:folderStop:refresh_done", { folder });
          showError("");
        } catch (err) {
          debug("[filewatch] wireFileWatcher:folderStop:error", {
            folder,
            message: err && err.message,
          });
          showError(`Failed to stop watcher: ${err.message}`);
        } finally {
          showLoading(false);
          debug("[filewatch] wireFileWatcher:folderStop:finally", { folder });
        }
      }

      else if (target.classList.contains("fw-remove-btn")) {
        const folder = target.dataset.folder;
        debug("[filewatch] wireFileWatcher:folderRemove:click", { folder });

        if (!confirm(`Remove folder from watch list?\n\n${folder}`)) {
          debug("[filewatch] wireFileWatcher:folderRemove:cancelled", { folder });
          return;
        }

        try {
          showLoading(true, "Removing folder...");
          await filewatchRemoveFolder(folder);
          debug("[filewatch] wireFileWatcher:folderRemove:remove_done", { folder });
          await refreshFilewatchStatus();
          debug("[filewatch] wireFileWatcher:folderRemove:refresh_done", { folder });
          showError("");
        } catch (err) {
          debug("[filewatch] wireFileWatcher:folderRemove:error", {
            folder,
            message: err && err.message,
          });
          showError(`Failed to remove folder: ${err.message}`);
        } finally {
          showLoading(false);
          debug("[filewatch] wireFileWatcher:folderRemove:finally", { folder });
        }
      }
    });

    // Auto-refresh status every 5 seconds if there are active watchers
    debug("[filewatch] wireFileWatcher:setInterval:register");
    state.filewatch.refreshInterval = setInterval(async () => {
      const hasProject = !!state.project;
      const hasFolders = state.filewatch.folders && state.filewatch.folders.length > 0;
      const hasActive = hasFolders && state.filewatch.folders.some(f => f.active);

      debug("[filewatch] autoRefresh:tick", {
        hasProject,
        folderCount: hasFolders ? state.filewatch.folders.length : 0,
        hasActive,
      });

      if (hasProject && hasActive) {
        await refreshFilewatchStatus();
        debug("[filewatch] autoRefresh:refreshed");
      }
    }, 5000);

    debug("[filewatch] wireFileWatcher:done");
  }

  // ---------- Boot ----------
  async function boot() {
    debug("[boot] starting UI");
    wireTabs();
    wireGlobal();
    wireCompliance();
    wireAudit();
    wireFileWatcher();
    await loadProjects();

    if (!state.project) {
      showError("No projects found under /api/nodes_compliance/projects");
      return;
    }

    // Date range inputs start empty (no default filter)
    // Uncomment lines below to seed with "last 24h" default:
    // const now = new Date();
    // const dayAgo = new Date(now.getTime() - 24 * 3600 * 1000);
    // if (el("comp-end")) el("comp-end").value = toDateTimeLocalValue(now);
    // if (el("comp-start")) el("comp-start").value = toDateTimeLocalValue(dayAgo);
    // if (el("audit-end")) el("audit-end").value = toDateTimeLocalValue(now);
    // if (el("audit-start")) el("audit-start").value = toDateTimeLocalValue(dayAgo);

    // Load schema and fetch
    beginDualLoad("Loading schema…");
    await loadSchema("comp");
    await loadSchema("audit");

    beginDualLoad("Loading logs…");
    fetchTable("comp");
    fetchTable("audit");
    
    // Load file watcher status
    await refreshFilewatchStatus();
  }

  document.addEventListener("DOMContentLoaded", boot);
})();