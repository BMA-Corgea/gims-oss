// Debug control - set to false to disable all grid debug logging
const DEBUG_ENABLED = false; // Change to true to enable debug logs
// Debug helper that respects the flag
const debug = DEBUG_ENABLED ? console.debug.bind(console, "[runlog-ui]") : () => {};

/* ------------------------------------------------------------------------------------------------
 * Small utilities
 * ------------------------------------------------------------------------------------------------ */
async function fetchJSON(url, options) {
  debug("fetchJSON:start", { url, hasOptions: !!options });
  const res = await fetch(url, options);
  if (!res.ok) {
    const t = await res.text().catch(() => "(no text)");
    debug("fetchJSON:error", { url, status: res.status, text: t.slice(0, 400) });
    throw new Error(t || `${res.status} ${res.statusText}`);
  }
  const json = await res.json();
  debug("fetchJSON:ok", { url, keys: Object.keys(json || {}) });
  return json;
}

async function tryFetchAny(urls, { expectArray = false } = {}) {
  for (const url of urls) {
    try {
      const j = await fetchJSON(url);
      if (expectArray) {
        if (Array.isArray(j)) return j;
        if (Array.isArray(j?.parsers)) return j.parsers;
        if (Array.isArray(j?.pphrases)) return j.pphrases;
        if (Array.isArray(j?.items)) return j.items;
      } else {
        return j;
      }
    } catch (e) {
      debug("tryFetchAny:fail", { url, err: String(e) });
    }
  }
  return expectArray ? [] : null;
}

async function tryPostAny(candidates, bodyObj) {
  const bodyJson = JSON.stringify(bodyObj || {});
  const asJson = {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: bodyJson
  };
  const asForm = (() => {
    const fd = new FormData();
    Object.entries(bodyObj || {}).forEach(([k, v]) => fd.set(k, v));
    return { method: "POST", body: fd };
  })();

  for (const { url, mode = "json" } of candidates) {
    try {
      const res = await fetch(url, mode === "json" ? asJson : asForm);
      if (!res.ok) throw new Error(await res.text());
      const maybe = await res.json().catch(() => ({}));
      debug("tryPostAny:ok", { url, mode });
      return { ok: true, url, mode, data: maybe };
    } catch (e) {
      debug("tryPostAny:fail", { url, mode, err: String(e) });
    }
  }
  return { ok: false };
}

/* ----------------------------------------------------------------------------
 * Linear-gate helpers (only enforce when the verb is linear-enabled)
 * ---------------------------------------------------------------------------- */
async function computeLinearGate(project, group, run_id, { keywords = [], pockets = [] } = {}) {
  try {
    const res = await fetch(
      `/runlog/${encodeURIComponent(project)}/${encodeURIComponent(group)}/${encodeURIComponent(run_id)}/status.json`
    );
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();

    // Normalize steps and detect "linear-enabled"
    const steps =
      Array.isArray(data.steps)
        ? data.steps
        : (data.linear_status && Array.isArray(data.linear_status.steps) ? data.linear_status.steps : []);
    const linearEnabled = Boolean(data?.linear_status?.enabled) || steps.length > 0;

    // If not linear-enabled (e.g., buckets verbs) → never gate
    if (!linearEnabled) {
      return { ok: true, allowed: true, pocket: null, reason: "not linear-enabled", currentIndex: -1 };
    }

    // Work out the "current" (first incomplete) index
    const idx =
      (data.first_incomplete && typeof data.first_incomplete.index === "number")
        ? data.first_incomplete.index
        : (typeof data.steps_completed === "number" ? data.steps_completed : -1);

    // If we can't resolve a current step, or everything's complete, don't gate
    if (idx < 0 || idx >= steps.length) {
      return { ok: true, allowed: true, pocket: null, reason: "all steps completed", currentIndex: idx };
    }

    const current = steps[idx] || null;
    if (!current) {
        return { ok: true, allowed: false, pocket: null, reason: "an unknown step", currentIndex: idx };
    }
    
    const hay = ([current.id, current.label, current.type, current.source])
      .map(x => String(x || "").toLowerCase())
      .join(" ");

    // Only enforce when the CURRENT step matches the feature being gated
    const matchesKind = keywords.length ? keywords.some(k => hay.includes(k)) : true;
    if (!matchesKind) {
      return {
        ok: true,
        allowed: false,
        pocket: null,
        reason: (current.label || current.id || "the current step"),
        currentIndex: idx
      };
    }

    // Optional: pocket-specific gating (if step text mentions a pocket)
    let matchedPocket = null;
    if (pockets && pockets.length && current.source) {
        const sourcePocket = String(current.source).toLowerCase().trim();
        matchedPocket = pockets.find(p => String(p).toLowerCase().trim() === sourcePocket) || null;
    }

    return {
      ok: true,
      allowed: true,
      pocket: matchedPocket,
      reason: current.label || current.id || "current step",
      currentIndex: idx
    };
  } catch (e) {
    // On any error, fail open (do not block uploads for non-linear/buckets runs)
    return { ok: false, allowed: true, pocket: null, reason: "status unavailable", currentIndex: -1 };
  }
}

// Specific gate checkers
const computeRawUploadGate = (p, g, r, pockets = []) =>
  computeLinearGate(p, g, r, {
    keywords: ["raw data", "raw_data", "raw upload", "upload raw", "raw files", "raw_files", "raw"],
    pockets
  });

const computeInterpGate  = (p, g, r) =>
  computeLinearGate(p, g, r, { keywords: ["interpret", "interpretation", "parse", "parsing"] });

const computeAdverbsGate = (p, g, r) =>
  computeLinearGate(p, g, r, { keywords: ["adverb", "adverbs"] });


// Toast / Snackbar helper
function showToast(message, variant = "success", opts = {}) {
  debug("toast", { message, variant, opts });
  const { timeout = 2500 } = opts;
  let tc = document.getElementById("toast-container");
  if (!tc) {
    tc = document.createElement("div");
    tc.id = "toast-container";
    document.body.appendChild(tc);
  }

  const el = document.createElement("div");
  el.className = `toast ${variant}`;
  el.setAttribute("role", "status");
  el.setAttribute("aria-live", "polite");

  const text = document.createElement("div");
  text.textContent = message;

  const close = document.createElement("button");
  close.className = "toast-close";
  close.innerHTML = "&times;";
  close.onclick = () => dismiss();

  el.appendChild(text);
  el.appendChild(close);
  tc.appendChild(el);

  let timer = setTimeout(() => dismiss(), timeout);

  function dismiss() {
    clearTimeout(timer);
    el.style.animation = "toast-out 160ms ease-in forwards";
    setTimeout(() => el.remove(), 180);
  }

  return { dismiss };
}

// Convenience: also mirror to footer status bar
function setStatus(msg) {
  debug("status", msg);
  const sb = document.getElementById("status-bar");
  if (sb) sb.textContent = msg;
}

/* ------------------------------------------------------------------------------------------------
 * DOM handles
 * ------------------------------------------------------------------------------------------------ */
const projectSelect = document.getElementById("project-select");
const verbGroupSelect = document.getElementById("verbgroup-select");
const runlogTable = document.getElementById("runlog-table");
// Sort state for renderRunlog (column index in *data.headers* space + dir)
let __runlogSort = { idx: null, dir: "asc" };
const statusBar = document.getElementById("status-bar");
const dumpSection = document.getElementById("data-dump-section");
const dumpTabs = document.getElementById("data-dump-tabs");
const dumpContents = document.getElementById("data-dump-contents");

// "+ Create New" button (hidden until a verb group runlog is loaded)
const createNewVerbBtn = document.createElement("a");
createNewVerbBtn.id = "create-verb-instance-btn";
createNewVerbBtn.className = "btn btn-primary btn-sm";
createNewVerbBtn.textContent = "+ Create New";
createNewVerbBtn.href = "/verb_workbench"; // same as http://127.0.0.1:8000/verb_workbench
createNewVerbBtn.style.display = "none";
createNewVerbBtn.style.marginLeft = ".5rem";

if (verbGroupSelect) {
  verbGroupSelect.insertAdjacentElement("afterend", createNewVerbBtn);
}

/* ------------------------------------------------------------------------------------------------
 * Primary-ID helpers (dynamic per-verb-group)
 * ------------------------------------------------------------------------------------------------ */
function resolvePidFieldFromRunlog(runlogPayload) {
  const pid = runlogPayload?.meta?.primary_id_field || "run_ID";
  debug("resolvePidFieldFromRunlog", pid);
  return pid;
}
function resolvePidFieldFromDump(dumpPayload) {
  const pid = dumpPayload?.meta?.primary_id_field || "run_ID";
  debug("resolvePidFieldFromDump", pid);
  return pid;
}

/* ------------------------------------------------------------------------------------------------
 * Initialize verbGroupSelect with a prompt to select a project first
 * ------------------------------------------------------------------------------------------------ */
function initializeVerbGroupSelect() {
  debug("initializeVerbGroupSelect");
  verbGroupSelect.innerHTML = "<option value=''>Select a project first</option>";
  verbGroupSelect.disabled = true;
}

/* ------------------------------------------------------------------------------------------------
 * Close dump panel handler
 * ------------------------------------------------------------------------------------------------ */
const closeDump = document.getElementById("close-dump");
if (closeDump) {
  closeDump.onclick = () => {
    debug("closeDump:click");
    dumpSection.classList.add("hidden");
    dumpTabs.innerHTML = "";
    dumpContents.innerHTML = "";
  };
}

/* ------------------------------------------------------------------------------------------------
 * Project selection -> load verb groups
 * ------------------------------------------------------------------------------------------------ */
projectSelect.onchange = async () => {
  const project = projectSelect.value;
  debug("projectSelect:onchange", { project });

  if (!project) {
    initializeVerbGroupSelect();
    return;
  }

  verbGroupSelect.disabled = true;
  verbGroupSelect.innerHTML = "<option value=''>Loading verb groups...</option>";

  try {
    const groups = await fetchJSON(`/runlog_data_dump/verb_groups/${project}`);
    debug("verbGroups:loaded", groups);

    verbGroupSelect.innerHTML = "";
    if (groups && groups.length > 0) {
      const defaultOption = document.createElement("option");
      defaultOption.value = "";
      defaultOption.textContent = "Select a verb group";
      verbGroupSelect.appendChild(defaultOption);

      groups.forEach(group => {
        const opt = document.createElement("option");
        opt.value = group;
        opt.textContent = group;
        verbGroupSelect.appendChild(opt);
      });
      verbGroupSelect.disabled = false;
      statusBar.textContent = "Select a verb group.";
    } else {
      verbGroupSelect.innerHTML = "<option value=''>No verb groups found</option>";
      verbGroupSelect.disabled = true;
      statusBar.textContent = "No verb groups found for this project.";
    }
  } catch (err) {
    debug("verbGroups:error", err);
    verbGroupSelect.innerHTML = "<option value=''>Error loading verb groups</option>";
    verbGroupSelect.disabled = true;
    statusBar.textContent = "Failed to load verb groups.";
  }
};

/* ------------------------------------------------------------------------------------------------
 * Verb group selection -> load runlog
 * ------------------------------------------------------------------------------------------------ */
verbGroupSelect.onchange = async () => {
  const project = projectSelect.value;
  const group = verbGroupSelect.value;
  debug("verbGroupSelect:onchange", { project, group });
  if (!project || !group) return;

  try {
    const data = await fetchJSON(`/runlog/${project}/${group}`);
    debug("runlog:loaded", { headers: data.headers, rows: data.rows?.length, meta: data.meta });
    renderRunlog(data, project, group);
    statusBar.textContent = `Loaded ${data.rows.length} runs.`;
  } catch (err) {
    debug("runlog:error", err);
    runlogTable.innerHTML = "<tr><td>Failed to load run log.</td></tr>";
    statusBar.textContent = "Error loading run log.";
  }
};

/* ------------------------------------------------------------------------------------------------
 * Render runlog table (primary-id aware)
 * ------------------------------------------------------------------------------------------------ */
function renderRunlog(data, project, group) {
  debug("renderRunlog:start", { project, group, headerCount: data.headers?.length, rowCount: data.rows?.length });

  // Clear table + any previous header controls
  runlogTable.innerHTML = "";

  // ─────────────────────────────────────────────
  // Runlog header actions (Create New)
  // ─────────────────────────────────────────────

  // Reuse or create header container
  let headerRow = runlogTable.parentElement.querySelector(".runlog-header-actions");

  if (!headerRow) {
    headerRow = document.createElement("div");
    headerRow.className = "runlog-header-actions";
    headerRow.style.display = "flex";
    headerRow.style.justifyContent = "flex-end";
    headerRow.style.marginBottom = "0.5rem";

    // Insert ABOVE the runlog table
    runlogTable.parentElement.insertBefore(headerRow, runlogTable);
  }

  // Clear previous contents (important!)
  headerRow.innerHTML = "";

  const createBtn = document.createElement("a");
  createBtn.textContent = "+ Create New";
  createBtn.href = "/verb_workbench";
  createBtn.className = "btn btn-primary";
  createBtn.style.padding = "0.4rem 0.75rem";
  createBtn.style.fontSize = "0.9rem";

  headerRow.appendChild(createBtn);

  // Compute pid before using it anywhere
  const pidField = resolvePidFieldFromRunlog(data);
  const pidIndex = data.headers.indexOf(pidField);
  debug("renderRunlog:pid", { pidField, pidIndex });

  // Always put display_ID in the first column
  const hidden = new Set([
    "run_ID",    // legacy raw id
    "_run_id",   // internal raw id (if present)
    pidField     // group-specific primary id (e.g., "general ID")
  ]);

  // Split out display_ID if it exists
  const displayIdx = data.headers.indexOf("display_ID");
  const displayCol = displayIdx >= 0 ? [{ h: "display_ID", i: displayIdx }] : [];

  // Then append all other non-hidden columns (excluding display_ID to avoid dupes)
  const columns = [
    ...displayCol,
    ...data.headers
      .map((h, i) => ({ h, i }))
      .filter(({ h }) => !hidden.has(String(h)) && h !== "display_ID")
  ];

  // Table header (click to sort)
  const headerTr = document.createElement("tr");
  columns.forEach(({ h, i }) => {
    const th = document.createElement("th");
    const label = (h === "__status") ? "Status" : h;

    // Arrow if this is the active sort column
    const isActive = (__runlogSort.idx === i);
    const arrow = isActive ? (__runlogSort.dir === "asc" ? " ▲" : " ▼") : "";

    th.textContent = label + arrow;

    // Make headers clickable for sorting
    th.style.cursor = "pointer";
    th.title = "Click to sort";

    th.onclick = (ev) => {
      ev.stopPropagation(); // don’t bubble to row click handlers
      if (__runlogSort.idx === i) {
        __runlogSort.dir = (__runlogSort.dir === "asc") ? "desc" : "asc";
      } else {
        __runlogSort.idx = i;
        __runlogSort.dir = "asc";
      }
      // Re-render with the same payload & context
      renderRunlog(data, project, group);
    };

    headerTr.appendChild(th);
  });
  runlogTable.appendChild(headerTr);

  // Rows (sortable)
  const sortedRows = (() => {
    const rows = Array.isArray(data.rows) ? [...data.rows] : [];
    const sortIdx = __runlogSort?.idx;

    if (sortIdx == null) return rows;

    const dir = (__runlogSort.dir === "desc") ? -1 : 1;

    const norm = (v) => {
      if (v == null) return "";
      if (typeof v === "number") return v;
      // Try numeric compare if the string looks numeric
      const n = Number(v);
      if (!Number.isNaN(n) && String(v).trim() !== "") return n;
      return String(v).toLowerCase();
    };

    // Stable-ish: fall back to display_ID / run_ID when equal
    const fallbackIdxDisplay = data.headers.indexOf("display_ID");
    const fallbackIdxRun     = data.headers.indexOf("run_ID");

    rows.sort((a, b) => {
      const A = norm(a[sortIdx]);
      const B = norm(b[sortIdx]);
      if (A < B) return -1 * dir;
      if (A > B) return  1 * dir;

      // Ties → consistent order by display_ID, then run_ID
      if (fallbackIdxDisplay >= 0) {
        const Ad = norm(a[fallbackIdxDisplay]); const Bd = norm(b[fallbackIdxDisplay]);
        if (Ad < Bd) return -1;
        if (Ad > Bd) return  1;
      }
      if (fallbackIdxRun >= 0) {
        const Ar = norm(a[fallbackIdxRun]); const Br = norm(b[fallbackIdxRun]);
        if (Ar < Br) return -1;
        if (Ar > Br) return  1;
      }
      return 0;
    });
    return rows;
  })();

  sortedRows.forEach((row) => {
    const tr = document.createElement("tr");

    columns.forEach(({ h, i }) => {
      const cell = row[i];
      const td = document.createElement("td");

      if (h === "__status") {
        const parts = String(cell ?? "").split("/");
        const completed = parseInt(parts[0], 10);
        const total = parseInt(parts[1], 10);
        const percentage = Number.isFinite(completed) && Number.isFinite(total) && total > 0
          ? Math.round((completed / total) * 100)
          : 0;
        td.innerHTML = `<span class="status-badge ${getStatusClass(percentage)}">${cell ?? ""}</span>`;
      } else if (typeof cell === "object" && cell !== null) {
        try { td.textContent = JSON.stringify(cell); } catch { td.textContent = "Complex data"; }
      } else {
        td.textContent = cell ?? "";
      }

      tr.appendChild(td);
    });

    // Resolve the run ID with the correct priority:
    const pidField = resolvePidFieldFromRunlog(data);
    const pidIndex = data.headers.indexOf(pidField);
    const internalIdx = data.headers.indexOf("_run_id");
    const runIdIndex  = data.headers.indexOf("run_ID");

    let ridSource = null;
    if (internalIdx >= 0 && row[internalIdx] != null)      ridSource = row[internalIdx];
    else if (pidIndex >= 0 && row[pidIndex] != null)       ridSource = row[pidIndex];
    else if (runIdIndex >= 0 && row[runIdIndex] != null)   ridSource = row[runIdIndex];

    const runID = ridSource != null ? String(ridSource).trim() : null;

    if (runID) {
      tr.onclick = () => {
        debug("renderRunlog:rowClick", { runID });
        openDump(project, group, runID);
      };
      tr.classList.add("clickable-row");
    }

    runlogTable.appendChild(tr);
  });

  debug("renderRunlog:done");
}

/* ------------------------------------------------------------------------------------------------
 * Status helpers
 * ------------------------------------------------------------------------------------------------ */
function getStatusClass(percentage) {
  debug("getStatusClass", { percentage });
  if (percentage >= 100) return "status-complete";
  if (percentage >= 75) return "status-good";
  if (percentage >= 50) return "status-warning";
  return "status-pending";
}

function getStatusValueClass(value) {
  const complete = ["Complete", "Uploaded", "Parsed", "Manually Completed"];
  const warning = ["Pending"];
  const error = ["Missing Required Fields"];
  if (complete.includes(value)) return "status-complete";
  if (warning.includes(value)) return "status-warning";
  if (error.includes(value) || String(value).startsWith("Missing")) return "status-error";
  return "";
}

function normalizeBreakdown(raw) {
  if (!raw || typeof raw !== "object") return {};

  const out = {};

  for (const [key, value] of Object.entries(raw)) {
    // If already an array (classic behavior)
    if (Array.isArray(value)) {
      out[key] = value;
      continue;
    }

    // If it's an object -> convert each entry into "Label: Status"
    if (typeof value === "object" && value !== null) {
      const arr = [];
      for (const [k, v] of Object.entries(value)) {
        arr.push(`${k}: ${v}`);
      }
      out[key] = arr;
      continue;
    }

    // Fallback for primitives
    out[key] = [String(value)];
  }

  return out;
}

/* ------------------------------------------------------------------------------------------------
 * Data dump loader (primary-id aware)
 * ------------------------------------------------------------------------------------------------ */
async function openDump(project, group, runID) {
  debug("openDump:start", { project, group, runID });
  try {
    setStatus(`Loading data dump for ${runID}...`);

    dumpSection.classList.remove("hidden");
    dumpTabs.innerHTML = '<div class="loading">Loading data...</div>';
    dumpContents.innerHTML = "";

    const dump = await fetchJSON(`/runlog/${project}/${group}/${encodeURIComponent(runID)}/dump`);
    debug("openDump:dumpLoaded", { keys: Object.keys(dump || {}), run_entry_keys: Object.keys(dump?.run_entry || {}) });

    // Normalize overrides summary block
    if (Array.isArray(dump.overrides)) {
      const overrideSummaryLines = dump.overrides.map(ovr => {
        const type = ovr.type || "Unknown";
        const status = ovr.status || "Note";
        const resolutionNotes = (ovr.resolution || [])
          .map(r => r?.note)
          .filter(Boolean)
          .join("; ");
        return `${status}: ${type}${resolutionNotes ? " — " + resolutionNotes : ""}`;
      });

      const originalOverrideKey = Object.keys(dump.status_breakdown || {}).find(
        k => k.toLowerCase().includes("override")
      );
      if (originalOverrideKey) {
        delete dump.status_breakdown[originalOverrideKey];
      }
      dump.status_breakdown = dump.status_breakdown || {};
      dump.status_breakdown["Override Status"] = overrideSummaryLines;
      debug("openDump:overrideSummaryInjected", overrideSummaryLines.length);
    }

    renderDataDump(dump, project, group, runID);
    setStatus(`Viewing dump for ${runID}`);
  } catch (err) {
    debug("openDump:error", err);
    dumpSection.classList.remove("hidden");
    dumpTabs.innerHTML = "";
    dumpContents.innerHTML = `<div class="error-message">Failed to load data dump: ${err.message}</div>`;
    setStatus("Failed to load data dump.");
  }
}

/* ------------------------------------------------------------------------------------------------
 * Render data dump tabs (primary-id aware when resolving runID for subwidgets)
 * ------------------------------------------------------------------------------------------------ */
function renderDataDump(dump, project = "", group = "", runID = "") {
  debug("renderDataDump:start", { project, group, runID, dumpKeys: Object.keys(dump || {}) });
  const pidField = resolvePidFieldFromDump(dump);
  const runEntry = dump.run_entry || {};
  const resolvedRunID =
    runID ||
    runEntry[pidField] ||
    runEntry.run_ID ||
    runEntry.run ||
    "";

  debug("renderDataDump:resolvedRunID", { pidField, resolvedRunID });

  const sections = {
    instructions: {
      title: "Instructions",
      content: dump.instructions && dump.instructions.length > 0
        ? dump.instructions.join("\n")
        : "No instructions available",
      format: "text",
    },
    status: {
      title: "Status Breakdown",
      content: formatStatusBreakdown(
        normalizeBreakdown(dump.status_breakdown),
        { project, group, runID: resolvedRunID }
      ),
      format: "html",
    },
    adverbs: {
      title: "Adverbs",
      content: null,
      format: "custom",
    },
    data_entry: {
      title: "Data Entry",
      content: null,
      format: "custom",
    },
    interpretation: {
      title: "Interpretation",
      content: null,
      format: "custom",
    },
    overrides: {
      title: "Overrides",
      content: null,
      format: "custom",
    },
    raw_data: { 
      title: "Raw Data", 
      content: null, 
      format: "custom" },
  };

  dumpTabs.innerHTML = "";
  dumpContents.innerHTML = "";

  Object.entries(sections).forEach(([key, section], idx) => {
    const tab = document.createElement("button");
    tab.textContent = section.title;
    tab.className = "tab-button" + (idx === 0 ? " active" : "");

    const content = document.createElement("div");
    content.className = "tab-content" + (idx === 0 ? " active" : "");
    content.dataset.tabKey = key;

    tab.onclick = () => {
      debug("tab:click", key);
      document.body.classList.remove("data-entry-active");
      if (content.dataset.tabKey === "data_entry") {
        document.body.classList.add("data-entry-active");
      }

      document.querySelectorAll(".tab-button").forEach(b => b.classList.remove("active"));
      tab.classList.add("active");

      document.querySelectorAll("#data-dump-contents .tab-content").forEach(p => {
        p.classList.remove("active");
        p.setAttribute("aria-hidden", "true");
        p.style.display = "none";
      });

      content.classList.add("active");
      content.removeAttribute("aria-hidden");
      content.style.display = "block";
    };

    if (section.format === "html") {
      content.innerHTML = section.content;

    } else if (section.format === "json") {
      const pre = document.createElement("pre");
      pre.className = "json-viewer";
      pre.textContent = section.content;
      content.appendChild(pre);

    } else if (section.format === "custom" && key === "overrides") {
      const p = project || (projectSelect?.value || "");
      const g = group || (verbGroupSelect?.value || "");
      const verbName = runEntry?.test_type || runEntry?.verb || "";
      renderOverrideEditor(content, p, g, resolvedRunID, verbName);

    } else if (section.format === "custom" && key === "adverbs") {
      const p = project || (projectSelect?.value || "");
      const g = group || (verbGroupSelect?.value || "");
      renderAdverbEditor(content, p, g, resolvedRunID);

    } else if (section.format === "custom" && key === "raw_data") {
      const p = project || (projectSelect?.value || "");
      const g = group || (verbGroupSelect?.value || "");
      renderRawDataSection(content, dump, { project: p, verb_group: g, run_id: resolvedRunID });

    } else if (section.format === "custom" && key === "data_entry") {
      const p = project || (projectSelect?.value || "");
      const g = group || (verbGroupSelect?.value || "");

      tab.addEventListener("click", async function onFirstOpen() {
        tab.removeEventListener("click", onFirstOpen);
        await mountDataEntryGrid(content, { dump, project: p, group: g, runID: resolvedRunID });
      });

      if (idx === 0 && tab.classList.contains("active")) {
        mountDataEntryGrid(content, { dump, project: p, group: g, runID: resolvedRunID });
      }

    } else if (section.format === "custom" && key === "interpretation") {
      const p = project || (projectSelect?.value || "");
      const g = group || (verbGroupSelect?.value || "");
      const verbName = runEntry?.test_type || runEntry?.verb || "";
      renderParser(content, p, g, resolvedRunID, verbName);

    } else {
      const pre = document.createElement("pre");
      pre.className = "text-viewer";
      pre.textContent = section.content;
      content.appendChild(pre);
    }

    dumpTabs.appendChild(tab);
    dumpContents.appendChild(content);
  });

  debug("renderDataDump:done");
}

/* ------------------------------------------------------------------------------------------------
 * JSON stringify helper
 * ------------------------------------------------------------------------------------------------ */
function safeStringify(obj) {
  debug("safeStringify:start");
  if (!obj) return "{}";
  const getCircularReplacer = () => {
    const seen = new WeakSet();
    return (key, value) => {
      if (typeof value === "object" && value !== null) {
        if (seen.has(value)) return "[Circular Reference]";
        seen.add(value);
      }
      return value;
    };
  };
  try {
    const s = JSON.stringify(obj, getCircularReplacer(), 2);
    debug("safeStringify:ok", s.length);
    return s;
  } catch (err) {
    debug("safeStringify:error", err);
    return JSON.stringify({ error: "Could not serialize this complex object" });
  }
}

/* ------------------------------------------------------------------------------------------------
 * Status breakdown formatter (classic + linear-aware)
 * ------------------------------------------------------------------------------------------------ */
function formatStatusBreakdown(breakdown, ctx = {}) {
  debug("formatStatusBreakdown:start", { keys: Object.keys(breakdown || {}), ctx });

  if (!breakdown || typeof breakdown !== "object" || Object.keys(breakdown).length === 0) {
    return "<div class='no-data'>No status information available</div>";
  }

  const isLinear =
    breakdown.mode === "linear" ||
    breakdown.linear_progress ||
    (breakdown.details && breakdown.details.mode === "linear") ||
    (breakdown.linear_status && Array.isArray(breakdown.linear_status.steps));

  if (isLinear) {
    return renderLinearStatus(breakdown, ctx);
  }

  const panelId = `sb-${Date.now().toString(36)}-${Math.random().toString(36).slice(2,8)}`;

  const html = [`
    <div id="${panelId}" class="status-breakdown status-bucket"
        data-project="${escapeHtml(ctx.project || '')}"
        data-group="${escapeHtml(ctx.group || '')}"
        data-runid="${escapeHtml(ctx.runID || '')}">
      <div class="sb-header">
        <span><strong>Status Breakdown</strong></span>
        <button type="button" class="btn btn-sm sb-refresh" 
                title="Refresh status" data-panel="${panelId}">⟳ Refresh</button>
      </div>
  `];
  const seenOverrideLines = new Set();

  Object.entries(breakdown).forEach(([key, value]) => {
    const isOverride = key.toLowerCase().includes("override") && Array.isArray(value);

    const rawStrings = Array.isArray(value)
      ? [...new Set(value.map(v =>
          typeof v === "string"
            ? v
            : typeof v === "object"
              ? JSON.stringify(v, null, 2)
              : String(v)
        ))]
      : [
          typeof value === "string"
            ? value
            : typeof value === "object"
              ? JSON.stringify(value, null, 2)
              : String(value)
        ];

    const renderedLines = rawStrings
      .map(raw => {
        if (isOverride) {
          if (seenOverrideLines.has(raw)) return "";
          seenOverrideLines.add(raw);
        }
        const clean = raw.replace(/^[✔❌⚠]+\s*/, "");
        const statusClass = getStatusValueClass(clean);
        const icon =
          statusClass === "status-complete" ? "✔" :
          statusClass === "status-warning"  ? "⚠" :
          statusClass === "status-error"    ? "❌" : "";
        return `<div class="status-value ${statusClass}">${icon} ${clean}</div>`;
      })
      .filter(Boolean)
      .join("");

    html.push(`
      <div class="status-item${isOverride ? " override-block" : ""}">
        <div class="status-label">${formatKey(key)}</div>
        ${renderedLines || `<div class="status-value muted">—</div>`}
      </div>
    `);
  });

  html.push("</div>");
  debug("formatStatusBreakdown:done (classic)");
  return html.join("");
}

function formatKey(key) {
  const out = key
    .replace(/_/g, " ")
    .split(" ")
    .map(word => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
  debug("formatKey", { key, out });
  return out;
}

/* ------------------------------------------------------------------------------------------------
 * Linear renderer — every step as an accordion with green check when complete
 * ------------------------------------------------------------------------------------------------ */
function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function kvList(obj) {
  return `<dl class="kv-list">` + Object.entries(obj || {})
    .map(([k,v]) => `<div class="kv-row"><dt>${escapeHtml(k)}</dt><dd>${escapeHtml(String(v ?? "—"))}</dd></div>`)
    .join("") + `</dl>`;
}

function renderLinearStatus(breakdown, ctx) {
  const stepsTotal = Number(breakdown.linear_steps_total ?? breakdown.details?.steps_total ?? 0);
  const stepsDone  = Number(breakdown.linear_steps_completed ?? breakdown.details?.steps_completed ?? 0);
  const progress   = breakdown.linear_progress || breakdown.details?.progress_text || `${stepsDone}/${stepsTotal}`;
  const firstInc   = breakdown.first_incomplete || breakdown.details?.first_incomplete || null;

  const currentIndex =
    Number.isFinite(firstInc?.index) ? Number(firstInc.index)
    : Number.isFinite(stepsDone)     ? Number(stepsDone)
    : -1;

  const panelId = `ls-${Date.now().toString(36)}-${Math.random().toString(36).slice(2,8)}`;

  const summary = `
      <div class="ls-summary">
        <div class="ls-row header-row">
          <span>Mode</span><strong>linear</strong>
          <button type="button" class="btn btn-sm ls-refresh"
                  data-panel="${panelId}" title="Refresh status">⟳ Refresh</button>
        </div>
        <div class="ls-row"><span>Linear Progress</span><strong class="ls-progress" data-value="${progress}">${progress}</strong></div>
        <div class="ls-row"><span>Linear Steps Completed</span><strong class="ls-done" data-value="${stepsDone}">${stepsDone}</strong></div>
        <div class="ls-row"><span>Linear Steps Total</span><strong class="ls-total" data-value="${stepsTotal}">${stepsTotal}</strong></div>
      </div>`;

  const panel = `
    <div id="${panelId}" class="linear-status"
         data-project="${escapeHtml(ctx.project)}"
         data-group="${escapeHtml(ctx.group)}"
         data-runid="${escapeHtml(ctx.runID)}"
         data-current-index="${String(currentIndex)}">
      ${summary}
      <div class="ls-block">
        <div class="ls-heading">Steps</div>
        <div class="ls-steps" data-role="steps"><div class="muted">Loading steps…</div></div>
      </div>
    </div>`;
  setTimeout(() => hydrateLinearPanelById(panelId), 0);
  return panel;
}

/* ---------- Steps hydration & rendering ---------- */
async function hydrateLinearPanelById(panelId) {
  const host = document.getElementById(panelId);
  if (!host) return;

  const { project, group, runid } = host.dataset;

  const res = await fetch(`/runlog/${encodeURIComponent(project)}/${encodeURIComponent(group)}/${encodeURIComponent(runid)}/status.json?t=${Date.now()}`);
  if (!res.ok) {
      throw new Error(`Failed to fetch status: ${res.statusText}`);
  }
  
  const data = await res.json();
  
  let steps = [];
  if (Array.isArray(data.steps)) {
    steps = data.steps;
  } else if (data.linear_status && Array.isArray(data.linear_status.steps)) {
    steps = data.linear_status.steps;
  }

  const ix = (data.first_incomplete && typeof data.first_incomplete.index === "number")
    ? data.first_incomplete.index
    : (typeof data.steps_completed === "number" ? data.steps_completed : -1);
  host.dataset.currentIndex = String(ix);
  
  const container = host.querySelector('[data-role="steps"]');
  container.innerHTML = steps.length
    ? steps.map((st, i) => renderStepRow(st, i, ix)).join("")
    : `<div class="muted">No steps are defined.</div>`;

  await refreshLinearSummary(host);
}

function renderStepRow(step, index, currentIndex) {
  const completed = !!step.completed;
  const isCurrent = index === Number(currentIndex);
  const label     = step.label || step.id || `Step ${index + 1}`;
  const type      = (step.type || "step").toLowerCase();

  const check = `<span class="step-check ${completed ? "ok" : ""}" aria-hidden="true">✔</span>`;

  const pill = completed
    ? `<span class="gate-pill ok">✔ Completed</span>`
    : (isCurrent ? `<span class="gate-pill current">— Current</span>` : `<span class="gate-pill pending">— Pending</span>`);

  const isGate = type === "gate";
  const btnHtml = isGate ? (() => {
    const btnDisabled = (isCurrent || completed) ? "" : `disabled aria-disabled="true" title="This step is locked until previous steps are completed."`;
    const btnLabel    = completed ? "Reopen" : "Sign off";
    const btnSetTo    = completed ? "false" : "true";
    return `
      <div class="gate-actions">
        <button class="step-toggle btn btn-sm"
                data-step-type="${type}"
                data-index="${index}"
                data-step-id="${escapeHtml(step.internal_id || step.id || "")}"
                data-set-to="${btnSetTo}"
                ${btnDisabled}>${btnLabel}</button>
      </div>`;
  })() : "";

  return `
    <details class="gate-acc ${completed ? "is-complete" : ""}" ${isCurrent ? "open" : ""} data-index="${index}" data-type="${type}">
      <summary class="gate-summary">
        <span class="gate-step">${index + 1}</span>
        <span class="gate-title">${escapeHtml(label)}</span>
        ${pill}
        ${check}
      </summary>
      <div class="gate-body">
        ${kvList({
          id: step.id || "",
          type: type,
          label,
          internal_id: step.internal_id || "—",
          required: String(Boolean(step.required)),
          source: step.source || "—",
          completed: String(completed),
          reason: step.reason || "—"
        })}
        ${btnHtml}
      </div>
    </details>`;
}

/* ---------- Summary refresher ---------- */
async function refreshLinearSummary(host) {
  const { project, group, runid } = host.dataset;
  try {
    const res = await fetch(`/runlog/${encodeURIComponent(project)}/${encodeURIComponent(group)}/${encodeURIComponent(runid)}/status.json`);
    let data;
    if (res.ok) {
      data = await res.json();
    } else {
      const res2 = await fetch(`/runlog/${encodeURIComponent(project)}/${encodeURIComponent(group)}/${encodeURIComponent(runid)}/status/linear`);
      if (!res2.ok) return;
      data = await res2.json();
    }

    const progress = data.progress || `${data.steps_completed}/${data.steps_total}`;
    const done = data.steps_completed ?? "";
    const total = data.steps_total ?? "";

    const pEl = host.querySelector(".ls-progress");
    const dEl = host.querySelector(".ls-done");
    const tEl = host.querySelector(".ls-total");
    if (pEl) pEl.textContent = progress;
    if (dEl) dEl.textContent = String(done);
    if (tEl) tEl.textContent = String(total);

    const ix = (data.first_incomplete && typeof data.first_incomplete.index === "number")
      ? data.first_incomplete.index
      : (typeof data.steps_completed === "number" ? data.steps_completed : -1);
    host.dataset.currentIndex = String(ix);
  } catch { /* ignore */ }
}

/* ---------- Delegated click handlers ---------- */

async function refreshAllGatedPanes() {
    document.querySelectorAll('[data-gated-pane="raw_data"]').forEach(host => {
        renderRawDataSection(host.parentElement, JSON.parse(host.dataset.dump), JSON.parse(host.dataset.ctx));
    });
    document.querySelectorAll('[data-gated-pane="interpretation"]').forEach(host => {
        renderParser(host.parentElement, host.dataset.project, host.dataset.group, host.dataset.runid, host.dataset.verb);
    });
    document.querySelectorAll('[data-gated-pane="adverbs"]').forEach(host => {
        renderAdverbEditor(host.parentElement, host.dataset.project, host.dataset.group, host.dataset.runid);
    });
}

if (!window.__runlogStepDelegationBound) {
  document.addEventListener("click", async (ev) => {
    const btn = ev.target.closest(".step-toggle, .gate-toggle");
    if (!btn) return;

    const host = btn.closest(".linear-status");
    if (!host) return;

    const { project, group, runid } = host.dataset;
    const stepType = (btn.dataset.stepType || "").toLowerCase();
    const btnIndex = Number(btn.dataset.index);
    const currentIndex = Number(host.dataset.currentIndex ?? -1);

    if (stepType !== "gate") {
      showToast("This step has no manual sign-off.", "warning");
      return;
    }

    const stepId = btn.dataset.stepId;
    const setTo  = btn.dataset.setTo === "true";

    if (setTo && (btnIndex !== currentIndex || btn.disabled || btn.getAttribute("aria-disabled") === "true")) {
      showToast("This step isn’t up yet. Complete earlier steps first.", "error");
      return;
    }

    btn.disabled = true;
    const original = btn.textContent;
    btn.textContent = setTo ? "Signing off…" : "Reopening…";

    try {
      const url = `/runlog/${encodeURIComponent(project)}/${encodeURIComponent(group)}/${encodeURIComponent(runid)}/gate/${encodeURIComponent(stepId)}/complete?completed=${setTo}`;
      const res = await fetch(url, { method: "POST" });
      if (!res.ok) throw new Error(await res.text());

      await hydrateLinearPanelById(host.id);
      await refreshAllGatedPanes();

    } catch (err) {
      alert(`Gate update failed:\n${String(err)}`);
    } finally {
      btn.disabled = false;
      btn.textContent = original;
    }
  });

  document.addEventListener("click", async (ev) => {
    const btn = ev.target.closest(".ls-refresh");
    // --- Bucket status refresh button ---
    const sb = ev.target.closest(".sb-refresh");
    if (sb) {
      const panelId = sb.dataset.panel;
      const host = document.getElementById(panelId);
      if (!host) return;

      const { project, group, runid } = host.dataset;
      sb.disabled = true;
      const original = sb.textContent;
      sb.textContent = "⟳ …";

      try {
        // 👇 THIS is the fix: bucket refresh now uses the dump endpoint
        const dump = await fetchJSON(
          `/runlog/${encodeURIComponent(project)}/${encodeURIComponent(group)}/${encodeURIComponent(runid)}/dump?t=${Date.now()}`
        );

        const breakdown =
          dump.status_breakdown ||
          dump.breakdown ||
          dump.status ||
          dump;

        // Re-render this exact panel
        host.outerHTML = formatStatusBreakdown(breakdown, {
          project,
          group,
          runID: runid,
        });

        showToast("Status refreshed", "success");
      } catch (err) {
        console.error("Bucket refresh failed:", err);
        showToast("Failed to refresh status", "error");
      } finally {
        sb.disabled = false;
        sb.textContent = original;
      }
      return;
    }
    if (!btn) return;
    const panelId = btn.dataset.panel;
    if (!panelId) return;

    btn.disabled = true;
    const original = btn.textContent;
    btn.textContent = "⟳ …";

    try {
      await hydrateLinearPanelById(panelId);
      await refreshAllGatedPanes();
      showToast("Status refreshed", "success");
    } catch (err) {
      console.error("Refresh failed:", err);
      showToast("Failed to refresh status", "error");
    } finally {
      btn.disabled = false;
      btn.textContent = original;
    }
  });

  window.__runlogStepDelegationBound = true;
}

/* ------------------------------------------------------------------------------------------------
 * Data Entry Grid
 * ------------------------------------------------------------------------------------------------ */
async function mountDataEntryGrid(hostEl, { dump, project, group, runID }) {
  debug("mountDataEntryGrid:start", { project, group, runID });
  try {
    const v = Date.now();
    const { mountDataGrid, defaultEndpoints } =
      await import(`/static/scripts/data_grid.js?v=${v}`);

    async function getNounTypeFromVerb() {
      const verbName = dump?.run_entry?.test_type ?? dump?.run_entry?.verb;
      debug("mountDataEntryGrid:getNounTypeFromVerb", { verbName });
      if (!verbName) return null;
      try {
        const verbSchema = await fetchJSON(`/schema/verb/${project}/${encodeURIComponent(verbName)}`);
        debug("mountDataEntryGrid:verbSchema", verbSchema ? Object.keys(verbSchema) : null);
        const nounType =
          verbSchema?.data_entry_schema?.set_up_inputs?.noun_type_ref ||
          verbSchema?.data_entry_schema?.noun_type ||
          null;
        debug("mountDataEntryGrid:nounType", nounType);
        return nounType;
      } catch (err) {
        console.warn(`Failed to fetch verb schema for "${verbName}":`, err);
        return null;
      }
    }

    const nounType = await getNounTypeFromVerb();

    mountDataGrid(hostEl, {
      project,
      verbGroup: group,
      runId: runID,
      ...(nounType ? { nounType } : {}),
      endpoints: defaultEndpoints,
      readOnlyCols: ["_runID"],
      autosaveMs: 800,
      onStatus: setStatus,
      onSaved: () => showToast("Data entry saved.", "success"),
      onError: (e) => { debug("mountDataEntryGrid:onError", e); showToast("Grid error", "error"); },
      onReady: (grid) => {
        const rowHeight = grid.config?.rowHeight || 32;
        const headerHeight = grid.config?.headerHeight || 32;
        hostEl.style.height = `${headerHeight + rowHeight * grid.state.rowCount}px`;
        debug("mountDataEntryGrid:onReady", { rowCount: grid.state.rowCount });
      }
    });

    debug("mountDataEntryGrid:mounted");
  } catch (e) {
    debug("mountDataEntryGrid:failed", e);
    hostEl.innerHTML = `<div class="error-message">Failed to mount Data Grid: ${e.message || e}</div>`;
  }
}

/* ------------------------------------------------------------------------------------------------
 * Raw Data Upload UI  (robust pockets resolution + empty-state rows)
 * ------------------------------------------------------------------------------------------------ */
async function renderRawDataSection(container, dump, ctx) {
  const { project, verb_group, run_id } = ctx;
  container.innerHTML = `<div data-gated-pane="raw_data" data-dump='${escapeHtml(JSON.stringify(dump))}' data-ctx='${escapeHtml(JSON.stringify(ctx))}'></div>`;
  const host = container.firstChild;

  // 1) List files (current state)
  let listing = { pockets: {} };
  try { listing = await fetchJSON(`/runlog/${project}/${verb_group}/${run_id}/raw/list`); }
  catch (e) { debug("[raw] list error", e); }

  // 2) Resolve pockets
  let pockets = [];
  const fromMeta = dump?.meta?.raw_data_inputs;
  if (Array.isArray(fromMeta) && fromMeta.length) pockets = [...fromMeta];
  if (!pockets.length) {
    const keys = Object.keys(listing.pockets || {});
    if (keys.length) pockets = keys;
  }
  if (!pockets.length) {
    const verbName = dump?.run_entry?.test_type || dump?.run_entry?.verb || dump?.verb || dump?.meta?.verb || "";
    if (verbName) {
      try {
        const schema = await fetchJSON(`/schema/verb/${project}/${encodeURIComponent(verbName)}`);
        const fromSchema = schema?.data_entry_schema?.raw_data_inputs;
        if (Array.isArray(fromSchema) && fromSchema.length) pockets = fromSchema;
      } catch (e) { debug("[raw] schema fetch failed", e); }
    }
  }
  if (!pockets.length) {
    host.innerHTML = `<div class="muted">No raw data pockets are defined for this verb.</div>`;
    return;
  }

  // 3) Gate (per pocket)
  const gate = await computeRawUploadGate(project, verb_group, run_id, pockets);
  const banner = document.createElement("div");
  banner.className = "raw-upload-gate";
  banner.innerHTML = gate.allowed
    ? `✅ <strong>Uploads unlocked</strong>${gate.pocket ? ` — allowed pocket: <em>${escapeHtml(gate.pocket)}</em>` : ""}.`
    : `🔒 <strong>Uploads locked</strong> — current step: <em>${escapeHtml(gate.reason)}</em>.`;
  host.appendChild(banner);

  const table = document.createElement("table");
  table.className = "raw-table";
  table.innerHTML = `<thead><tr><th>Pocket</th><th>Files</th><th>Upload</th></tr></thead><tbody></tbody>`;
  const tbody = table.querySelector("tbody");

  const allowedExts = ".csv,.xlsx,.jpeg,.png,.docx,.odt,.txt,.pdf,.html,.ods,.xcf";

  function renderFilesList(pocket, files) {
    const wrap = document.createElement("div");
    wrap.className = "raw-files";
    if (!files || !files.length) {
      wrap.innerHTML = `<em class="muted">No files uploaded</em>`;
      return wrap;
    }
    const ul = document.createElement("ul");
    ul.style.listStyle = "none"; ul.style.margin = "0"; ul.style.padding = "0";
    files.forEach(f => {
      const li = document.createElement("li");
      li.style.display = "flex"; li.style.alignItems = "center"; li.style.gap = "0.5rem";

      const name = document.createElement("span");
      name.textContent = `${f.name} (${f.bytes} B)`;

      const dl = document.createElement("a");
      dl.className = "btn btn-sm";
      dl.textContent = "Download";
      dl.href = `/runlog/${encodeURIComponent(project)}/${encodeURIComponent(verb_group)}/${encodeURIComponent(run_id)}/raw/download?pocket=${encodeURIComponent(pocket)}&filename=${encodeURIComponent(f.name)}`;
      dl.setAttribute("download", "");

      const del = document.createElement("button");
      del.textContent = "Delete";
      del.onclick = async () => {
        if (!confirm(`Delete ${f.name} from ${pocket}?`)) return;
        try {
          const url = `/runlog/${encodeURIComponent(project)}/${encodeURIComponent(verb_group)}/${encodeURIComponent(run_id)}/raw/delete?pocket=${encodeURIComponent(pocket)}&filename=${encodeURIComponent(f.name)}`;
          const res = await fetch(url, { method: "DELETE" });
          if (!res.ok) throw new Error(await res.text());
          await refreshPocketRow(pocket);
          showToast(`Deleted ${f.name}`, "success");
        } catch (err) { alert(`Delete failed: ${err}`); }
      };

      li.appendChild(name);
      li.appendChild(dl);
      li.appendChild(del);
      ul.appendChild(li);
    });
    wrap.appendChild(ul);
    return wrap;
  }

  async function refreshPocketRow(pocket) {
    try {
      const res = await fetchJSON(`/runlog/${project}/${verb_group}/${run_id}/raw/list?pocket=${encodeURIComponent(pocket)}`);
      const files = res.files || [];
      const row = tbody.querySelector(`tr[data-pocket="${CSS.escape(pocket)}"]`);
      if (row) {
        const filesCell = row.querySelector("[data-cell=files]");
        filesCell.innerHTML = "";
        filesCell.appendChild(renderFilesList(pocket, files));
      }
    } catch (e) { console.error("refreshPocketRow failed", e); }
  }

  // 4) Rows
  for (const pocket of pockets) {
    const tr = document.createElement("tr");
    tr.dataset.pocket = pocket;

    const tdName = document.createElement("td");
    tdName.textContent = pocket;

    const tdFiles = document.createElement("td");
    tdFiles.setAttribute("data-cell", "files");
    tdFiles.appendChild(renderFilesList(pocket, (listing.pockets && listing.pockets[pocket]) || []));

    const tdUpload = document.createElement("td");
    const form = document.createElement("form");
    form.enctype = "multipart/form-data";
    form.onsubmit = (e) => e.preventDefault();

    const fileInput = document.createElement("input");
    fileInput.type = "file"; fileInput.accept = allowedExts;

    const overwriteLabel = document.createElement("label");
    overwriteLabel.style.display = "flex"; overwriteLabel.style.alignItems = "center"; overwriteLabel.style.gap = "0.25rem";
    const overwrite = document.createElement("input");
    overwrite.type = "checkbox"; overwrite.id = `overwrite-${pocket}`;
    overwriteLabel.appendChild(overwrite);
    const span = document.createElement("span"); span.textContent = "Allow overwrite";
    overwriteLabel.appendChild(span); overwrite.style.marginLeft = "8px";

    const uploadBtn = document.createElement("button");
    uploadBtn.type = "button"; uploadBtn.textContent = "Upload"; uploadBtn.style.marginLeft = "8px";
    uploadBtn.onclick = async () => {
      if (!fileInput.files || fileInput.files.length === 0) { alert("Choose a file first."); return; }
      const f = fileInput.files[0];
      const fd = new FormData();
      fd.set("pocket", pocket); fd.set("file", f, f.name); fd.set("filename", f.name);
      fd.set("overwrite", overwrite.checked ? "true" : "false");
      try {
        const res = await fetch(`/runlog/${project}/${verb_group}/${run_id}/raw/upload`, { method: "POST", body: fd });
        if (!res.ok) throw new Error(await res.text());
        fileInput.value = "";
        await refreshPocketRow(pocket);
        showToast(`Uploaded ${f.name}`, "success");
      } catch (err) { alert(`Upload failed: ${err}`); }
    };

    const enableHere = !!(gate.allowed && (!gate.pocket || gate.pocket.toLowerCase() === pocket.toLowerCase()));
    [fileInput, overwrite, uploadBtn].forEach(el => el.disabled = !enableHere);
    if (!enableHere) uploadBtn.title = gate.allowed ? "Not the active pocket for this step." : "Locked until Raw Data step is current.";

    form.appendChild(fileInput);
    form.appendChild(overwriteLabel);
    form.appendChild(uploadBtn);
    tdUpload.appendChild(form);

    tr.appendChild(tdName); tr.appendChild(tdFiles); tr.appendChild(tdUpload);
    tbody.appendChild(tr);
  }

  host.appendChild(table);
  debug("[raw] render complete", { pocketCount: pockets.length });
}

/* ------------------------------------------------------------------------------------------------
 * Overrides UI (unchanged logic, debug-added)
 * ------------------------------------------------------------------------------------------------ */
async function renderOverrideEditor(container, project, group, runID, verbName) {
  debug("renderOverrideEditor:start", { project, group, runID, verbName });
  container.innerHTML = "";

  function qstring(params) {
    const usp = new URLSearchParams();
    Object.entries(params || {}).forEach(([k, v]) => {
      if (Array.isArray(v)) v.forEach(x => usp.append(k, x));
      else if (v !== undefined && v !== null && v !== "") usp.append(k, v);
    });
    const s = usp.toString();
    return s ? `?${s}` : "";
  }

  async function fetchRefOptions(project, nounType, params) {
    const url = `/conjunction/reference_options/${encodeURIComponent(project)}/${encodeURIComponent(nounType)}${qstring(params)}`;
    const { options } = await fetchJSON(url);
    return Array.isArray(options) ? options : [];
  }

  const wrap = document.createElement("div");
  wrap.className = "override-editor";

  const title = document.createElement("h3");
  title.textContent = `Overrides for ${runID}`;
  wrap.appendChild(title);

  let data;
  try {
    data = await fetchJSON(`/runlog/${project}/${group}/${encodeURIComponent(runID)}/override`);
  } catch (e) {
    wrap.innerHTML += `<div class="error-message">Failed to load overrides: ${e}</div>`;
    container.appendChild(wrap);
    return;
  }
  const overrides = Array.isArray(data.conjunctions) ? data.conjunctions : [];
  const types = Array.isArray(data.available_types) ? data.available_types : [];
  const backendVerb = data.verb || verbName || "";
  debug("renderOverrideEditor:data", { overrides: overrides.length, types: types.length, backendVerb });

  const list = document.createElement("div");
  list.className = "override-list";
  if (overrides.length === 0) {
    list.innerHTML = `<div class="muted">No overrides have been added for this run.</div>`;
  } else {
    list.innerHTML = overrides.map((ovr, i) => {
      const extras = [];
      if (ovr.note) extras.push(`note: ${ovr.note}`);
      if (ovr.initials) extras.push(`initials: ${ovr.initials}`);
      if (ovr.date) extras.push(`date: ${ovr.date}`);
      if (Array.isArray(ovr["linked_submission"]) && ovr["linked_submission"].length) {
        extras.push(`linked_submission: ${ovr["linked_submission"].join(", ")}`);
      }
      if (Array.isArray(ovr["previous runs"]) && ovr["previous runs"].length) {
        extras.push(`previous runs: ${ovr["previous runs"].join(", ")}`);
      }
      if (Array.isArray(ovr["retest of"]) && ovr["retest of"].length) {
        extras.push(`retest of: ${ovr["retest of"].join(", ")}`);
      }

      const resolvedNotes = (ovr.resolution || []).map(r => r?.note).filter(Boolean);
      const isResolved = resolvedNotes.length > 0;
      const isNotification = String(ovr.status || "").toLowerCase() === "notification";
      const resolveDisabled = isResolved || isNotification;

      return `
        <div class="override-row">
          <div class="override-main">
            <span class="badge">${i}</span>
            <strong>${ovr.type || "Unknown"}</strong> → <em>${ovr.status || "Status?"}</em>
            ${extras.length ? `<span class="muted">(${extras.join("; ")})</span>` : ""}
          </div>
          <div class="override-actions">
            <button class="btn btn-small${resolveDisabled ? " disabled" : ""}"
                    data-act="resolve"
                    data-idx="${i}"
                    ${resolveDisabled ? "disabled title='Notification — no override needed'" : ""}>
              Resolve
            </button>
            <button class="btn btn-small btn-danger" data-act="delete" data-idx="${i}">Delete</button>
          </div>
        </div>
      `;
    }).join("");
  }
  wrap.appendChild(list);

  const form = document.createElement("div");
  form.className = "override-form card";
  form.innerHTML = `
    <div class="form-row">
      <label>Conjunction</label>
      <select id="ovr-type">
        ${types.map((t, idx) => `<option value="${idx}">${t.type} (${t.status})</option>`).join("")}
      </select>
    </div>
    <div id="ovr-dynamic-fields"></div>
    <div class="form-actions">
      <button class="btn" id="ovr-add">➕ Add Override</button>
    </div>
  `;
  wrap.appendChild(form);
  container.appendChild(wrap);

  const typeSelect = form.querySelector("#ovr-type");
  const dynFields  = form.querySelector("#ovr-dynamic-fields");

  async function renderFieldsForType() {
    debug("renderOverrideEditor:renderFieldsForType");
    dynFields.innerHTML = "";

    const t = types[parseInt(typeSelect.value || "0", 10)] || { fields: [] };
    const fields = Array.isArray(t.fields) ? t.fields : [];

    const seen = new Set();

    function addScalar(label) {
      const key = String(label);
      if (seen.has(key)) return;
      seen.add(key);
      const isDate = key.toLowerCase() === "date";
      const row = document.createElement("div");
      row.className = "form-row";
      row.innerHTML = `
        <label>${key}</label>
        <input ${isDate ? 'type="date"' : 'type="text"'} data-key="${key}" ${isDate ? `value="${new Date().toISOString().slice(0,10)}"` : ""}>
      `;
      dynFields.appendChild(row);
    }

    for (const f of fields) {
      if (typeof f === "string") {
        addScalar(f);
        continue;
      }
      if (f && typeof f === "object") {
        const label = f.label || f.name || "field";

        if (f.type === "reference" && f.mode === "ReferenceList") {
          if (seen.has(label)) continue;
          seen.add(label);

          const noun    = f.reference_noun;
          const filters = f.filters || {};
          const params  = { ...filters };

          if (noun === "Run") {
            params.verb_group = group;
            if (backendVerb) params.verb_name = backendVerb;
          }

          let options = [];
          try {
            options = await fetchRefOptions(project, noun, params);
          } catch (e) {
            debug("renderOverrideEditor:refOptions:error", e);
          }

          const gridId = `ref-${label.replace(/\s+/g, "_")}-${Math.random().toString(36).slice(2,8)}`;
          const row = document.createElement("div");
          row.className = "form-row ref-field";
          row.innerHTML = `
            <label>${label}</label>
            <div id="${gridId}" class="checkbox-grid">
              ${options.map(opt => `
                <label class="cb">
                  <input type="checkbox" data-key="${label}" value="${opt.value}">
                  <span>${opt.label}</span>
                </label>
              `).join("")}
            </div>
            ${options.length ? `
              <div class="mini-tools">
                <button type="button" class="mini" data-ref-tools="${gridId}" data-act="all">Select all</button>
                <button type="button" class="mini" data-ref-tools="${gridId}" data-act="none">None</button>
              </div>` : `<div class="muted">No options found for ${noun}</div>`}
          `;
          dynFields.appendChild(row);
          continue;
        }

        addScalar(label);
      }
    }
  }

  await renderFieldsForType();
  typeSelect.onchange = () => { renderFieldsForType(); };

  dynFields.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-ref-tools]");
    if (!btn) return;
    const grid = dynFields.querySelector(`#${btn.dataset.refTools}`);
    if (!grid) return;
    const boxes = grid.querySelectorAll('input[type="checkbox"][data-key]');
    const check = btn.dataset.act === "all";
    boxes.forEach(b => (b.checked = check));
  });

  list.addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-act]");
    if (!btn) return;
    const idx = parseInt(btn.dataset.idx, 10);

    if (btn.dataset.act === "delete") {
      if (!confirm("Delete this override?")) return;
      const next = overrides.filter((_, i) => i !== idx);
      await updateOverridesAPI(project, group, runID, next);
      showToast("Override deleted.", "success");
      setStatus(`Override deleted for ${runID}.`);
      renderOverrideEditor(container, project, group, runID, backendVerb);
    }

    if (btn.dataset.act === "resolve") {
      if (btn.disabled) return;
      const note = prompt("Resolution note?");
      if (!note) return;
      const next = [...overrides];
      const r = Array.isArray(next[idx].resolution) ? next[idx].resolution : [];
      r.push({ note });
      next[idx].resolution = r;
      await updateOverridesAPI(project, group, runID, next);
      showToast("Override resolved.", "success");
      setStatus(`Override resolved for ${runID}.`);
      renderOverrideEditor(container, project, group, runID, backendVerb);
    }
  });

  form.querySelector("#ovr-add").onclick = async () => {
    const tIdx = parseInt(typeSelect.value || "0", 10);
    const t = types[tIdx];
    if (!t) return;

    const payload = { run: runID, type: t.type, status: t.status, resolution: [] };

    dynFields.querySelectorAll('input[data-key]:not([type="checkbox"])').forEach(el => {
      const v = el.value.trim();
      if (v !== "") payload[el.dataset.key] = v;
    });

    const grouped = {};
    dynFields.querySelectorAll('input[type="checkbox"][data-key]').forEach(el => {
      if (!el.checked) return;
      (grouped[el.dataset.key] ||= []).push(el.value);
    });
    Object.assign(payload, grouped);

    const next = [...overrides, payload];
    await updateOverridesAPI(project, group, runID, next);
    showToast("Override added.", "success");
    setStatus(`Override added for ${runID}.`);
    renderOverrideEditor(container, project, group, runID, backendVerb);
  };

  debug("renderOverrideEditor:done");
}

function showAddOverrideDialog(overrides, runID, project, group) {
  debug("showAddOverrideDialog:start", { runID });
  const type = prompt("Override type (e.g., Quarantine, Rerun, etc):");
  if (!type) return;

  const status = prompt("Status (Exception, Notification, etc):");
  const note = prompt("Note:");
  const initials = prompt("Initials:");
  const date = new Date().toISOString().slice(0, 10);

  const newOverride = { run: runID, type, status, note, initials, date, resolution: [] };
  const updated = [...overrides, newOverride];
  updateOverridesAPI(project, group, runID, updated).then(() => openDump(project, group, runID));
}

async function updateOverridesAPI(project, group, runID, updatedOverrides) {
  debug("updateOverridesAPI:start", { project, group, runID, count: updatedOverrides?.length });
  const res = await fetch(`/runlog/${project}/${group}/${encodeURIComponent(runID)}/override/update`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ overrides: updatedOverrides })
  });

  if (!res.ok) {
    const msg = await res.text();
    showToast("Failed to update overrides", "error");
    setStatus("Failed to update overrides.");
    debug("updateOverridesAPI:error", msg);
    throw new Error(msg);
  }
  debug("updateOverridesAPI:ok");
}

/* ------------------------------------------------------------------------------------------------
 * Interpretation files + Custom Parser actions (combined)
 *  — NEW: Parsers runner section (dropdown + button)
 * ------------------------------------------------------------------------------------------------ */
async function renderParser(container, project, group, runID, dumpOrVerb) {
  debug("renderParser:start", { project, group, runID, dumpType: typeof dumpOrVerb });
  let verbName = typeof dumpOrVerb === "string"
    ? dumpOrVerb
    : (dumpOrVerb?.run_entry?.test_type || dumpOrVerb?.run_entry?.verb || dumpOrVerb?.verb || "");

  container.innerHTML = `<div data-gated-pane="interpretation" data-project="${project}" data-group="${group}" data-runid="${runID}" data-verb="${verbName}"></div>`;
  const host = container.firstChild;

  // --- Helpers for interpretation file mgmt ---
  async function interpList({ tab } = {}) {
    const qs = new URLSearchParams();
    if (tab) qs.set("tab", tab);
    if (verbName) qs.set("verb", verbName);
    return fetchJSON(`/runlog/${encodeURIComponent(project)}/${encodeURIComponent(group)}/${encodeURIComponent(runID)}/interpret/list?${qs}`);
  }
  async function interpUpload({ tab, file, overwrite }) {
    const fd = new FormData();
    fd.set("tab", tab);
    fd.set("file", file, file.name);
    fd.set("overwrite", overwrite ? "true" : "false");
    const res = await fetch(`/runlog/${encodeURIComponent(project)}/${encodeURIComponent(group)}/${encodeURIComponent(runID)}/interpret/upload`, { method: "POST", body: fd });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  }
  async function interpDelete({ tab }) {
    const qs = new URLSearchParams({ tab });
    const res = await fetch(`/runlog/${encodeURIComponent(project)}/${encodeURIComponent(group)}/${encodeURIComponent(runID)}/interpret/delete?${qs}`, { method: "DELETE" });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  }

  // --- NEW: Parsers card (workbench endpoints) ---
  const parsersCard = document.createElement("div");
  parsersCard.className = "card";
  parsersCard.innerHTML = `
    <div class="card-header">
      <strong>Parsers</strong>
      <span class="muted" style="margin-left:.5rem">(WorkBench)</span>
    </div>
    <div class="card-body">
      <div class="form-row" style="gap:.5rem;display:flex;align-items:center;flex-wrap:wrap">
        <label for="wb-parser-select" style="min-width:6rem">Select parser</label>
        <select id="wb-parser-select" style="min-width:16rem"></select>
        <button id="wb-run-parser" class="btn btn-primary btn-sm">Run Parser</button>
        <span id="wb-parser-hint" class="muted"></span>
      </div>
    </div>`;
  host.appendChild(parsersCard);

  const parserSelect = parsersCard.querySelector("#wb-parser-select");
  const runBtn       = parsersCard.querySelector("#wb-run-parser");
  const hintEl       = parsersCard.querySelector("#wb-parser-hint");

  async function loadParsers() {
    const enc = encodeURIComponent;
    const urls = [
      `/api/parser_test/list_custom_parsers?project=${enc(project)}`
    ];
    const parsers = await tryFetchAny(urls, { expectArray: true });
    debug("[workbench] parsers discovered", parsers);
    parserSelect.innerHTML = "";

    if (!parsers.length) {
      const opt = document.createElement("option");
      opt.value = "";
      opt.textContent = "(no parsers discovered)";
      parserSelect.appendChild(opt);
      runBtn.disabled = true;
      hintEl.textContent = "No parsers available for this project/group.";
      return;
    }

    const defaultOpt = document.createElement("option");
    defaultOpt.value = "";
    defaultOpt.textContent = "(select a parser)";
    parserSelect.appendChild(defaultOpt);

    parsers.forEach(p => {
      const name = typeof p === "string" ? p : (p.name || p.id || p.module || "");
      if (!name) return;
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      parserSelect.appendChild(opt);
    });

    runBtn.disabled = false;
    hintEl.textContent = "";
  }

  async function runSelectedParser() {
    const parser = parserSelect.value;
    if (!parser) {
      showToast("Choose a parser first.", "warning");
      return;
    }
    runBtn.disabled = true;
    const label0 = runBtn.textContent;
    runBtn.textContent = "Running…";

    // Candidate endpoints for running a parser (workbench + runlog fallbacks)
    const enc = encodeURIComponent;
    const body = { parser, verb: verbName, run_id: runID, run: runID, project, group };
    const candidates = [
      { url: `/api/parser_test/test_parser/${enc(project)}/${enc(parser)}?verb_group=${enc(group)}&run_id=${enc(runID)}`, mode: "json" }
    ];

    try {
      const res = await tryPostAny(candidates, body);
      if (!res.ok) throw new Error("No parser endpoint accepted the request.");

      debug("[workbench] run parser ok", res);
      showToast(`Parser "${parser}" started`, "success");
      hintEl.textContent = `Executed via ${res.url}`;
      // Refresh interpretation files (in case parser wrote outputs)
      await renderInterpTable();
      // Optionally ping linear refresh so gates/UI update
      const refreshBtn = document.querySelector('.ls-refresh');
      if (refreshBtn) refreshBtn.click();
    } catch (e) {
      console.error(e);
      showToast(`Parser run failed: ${String(e.message || e)}`, "error");
      hintEl.textContent = "Failed to run parser.";
    } finally {
      runBtn.disabled = false;
      runBtn.textContent = label0;
    }
  }

  runBtn.onclick = runSelectedParser;
  await loadParsers();

  // --- Gate banner for Interpretation actions ---
  const filesCard = document.createElement("div");
  filesCard.className = "card";
  filesCard.innerHTML = `
    <div class="card-header">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap">
        <div><strong>Interpretation Files</strong></div>
        <div class="muted">${project} • ${group} • ${verbName || "?"} • run: ${runID}</div>
      </div>
    </div>
    <div class="card-body">
      <div class="raw-upload-gate" style="margin-bottom:.5rem"></div>
      <div id="interp-body"><div class="loading">Loading…</div></div>
    </div>`;
  host.appendChild(filesCard);

  const interpHost = filesCard.querySelector("#interp-body");
  const interpBanner = filesCard.querySelector(".raw-upload-gate");
  const interpGate = await computeInterpGate(project, group, runID);
  interpBanner.innerHTML = interpGate.allowed
    ? `✅ <strong>Uploads unlocked</strong> — Interpretation step is current.`
    : `🔒 <strong>Uploads locked</strong> — current step: <em>${escapeHtml(interpGate.reason)}</em>.`;

  async function renderInterpTable() {
    let listing;
    try { listing = await interpList(); } catch (e) { interpHost.innerHTML = `<div class="error-message">Failed to load interpretation files: ${String(e)}</div>`; return; }

    const tabs = Array.isArray(listing.tabs) ? listing.tabs : [];
    if (!tabs.length) { interpHost.innerHTML = `<div class="muted">No interpretation tabs are defined.</div>`; return; }

    const table = document.createElement("table");
    table.className = "raw-table";
    table.innerHTML = `<thead><tr><th>Tab</th><th>File</th><th>Upload / Replace</th></tr></thead><tbody></tbody>`;
    const tbody = table.querySelector("tbody");
    const disabledAttr = interpGate.allowed ? "" : "disabled title='Locked until Interpretation step is current'";

    tabs.forEach(tab => {
      const info = listing.files?.[tab];
      const exists = !!(info && info.exists);
      const size = exists ? ` (${info.bytes} B)` : "";
      const fileLabel = exists ? `${info.name}${size}` : `<em class="muted">No file</em>`;
      const dlBtn = exists
        ? `<a class="btn btn-sm" href="/runlog/${encodeURIComponent(project)}/${encodeURIComponent(group)}/${encodeURIComponent(runID)}/interpret/download?tab=${encodeURIComponent(tab)}" download>Download</a>`
        : "";
      const delBtn = exists ? `<button class="btn btn-danger btn-sm" data-del="${tab}">Delete</button>` : "";
      const rid = Math.random().toString(36).slice(2, 8);
      const row = document.createElement("tr");
      row.dataset.tab = tab;
      row.innerHTML = `
        <td>${tab}</td>
        <td data-cell="file"><span class="file-label">${fileLabel}</span> ${dlBtn} ${delBtn}</td>
        <td>
          <form onsubmit="return false;">
            <input type="file" accept=".csv,.xlsx,.jpeg,.png,.docx,.odt,.txt,.pdf,.html,.ods,.xcf" data-file="${rid}" ${disabledAttr}/>
            <label class="checkbox-inline"><input type="checkbox" data-ow="${rid}" ${disabledAttr}/> Replace</label>
            <button type="button" class="btn btn-primary btn-sm" data-up="${rid}" ${disabledAttr}>Upload</button>
          </form>
        </td>`;
      tbody.appendChild(row);
    });
    interpHost.innerHTML = "";
    interpHost.appendChild(table);

    tbody.addEventListener("click", async (ev) => {
      const up = ev.target.closest("button[data-up]");
      const del = ev.target.closest("button[data-del]");
      if (up) {
        const row = up.closest("tr");
        const tab = row.dataset.tab;
        const fileInput = row.querySelector(`input[type="file"]`);
        const ow = row.querySelector(`input[type="checkbox"]`);
        if (!fileInput.files || !fileInput.files.length) { alert("Choose a file."); return; }
        try {
          await interpUpload({ tab, file: fileInput.files[0], overwrite: !!ow.checked });
          showToast(`Uploaded ${fileInput.files[0].name}`, "success");
          fileInput.value = "";
          renderInterpTable();
        } catch (e) { showToast(`Upload failed: ${e.message}`, "error"); }
      }
      if (del) {
        const tab = del.dataset.del;
        if (!confirm(`Delete file for "${tab}"?`)) return;
        try {
          await interpDelete({ tab });
          showToast(`Deleted ${tab}`, "success");
          renderInterpTable();
        } catch (e) { showToast(`Delete failed: ${e.message}`, "error"); }
      }
    });
  }
  await renderInterpTable();
}

/* ------------------------------------------------------------------------------------------------
 * Adverb editor (debug-added)
 * ------------------------------------------------------------------------------------------------ */
async function renderAdverbEditor(container, project, group, runID) {
  debug("renderAdverbEditor:start", { project, group, runID });
  container.innerHTML = `<div data-gated-pane="adverbs" data-project="${project}" data-group="${group}" data-runid="${runID}"></div>`;
  const host = container.firstChild;

  async function _fetchJSON(url, options = {}) {
    const res = await fetch(url, options);
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  }
  async function updateAdverbsAPI(project, group, runID, adverbs) {
    debug("renderAdverbEditor:updateAdverbsAPI", { count: Object.keys(adverbs || {}).length });
    return _fetchJSON(`/runlog/${project}/${group}/${encodeURIComponent(runID)}/adverb/update`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ adverbs })
    });
  }

  const wrap = document.createElement("div");
  wrap.className = "adverb-editor";
  const title = document.createElement("h3");
  title.textContent = `Adverbs for ${runID}`;
  wrap.appendChild(title);

  let payload;
  try {
    payload = await _fetchJSON(`/runlog/${project}/${group}/${encodeURIComponent(runID)}/adverb`);
    debug("renderAdverbEditor:payload", { keys: Object.keys(payload || {}) });
  } catch (e) {
    wrap.innerHTML += `<div class="error-message">Failed to load adverbs: ${e}</div>`;
    host.appendChild(wrap);
    return;
  }

  if (!payload.verb || !Array.isArray(payload.available_types) || payload.available_types.length === 0) {
    const hint = document.createElement("div");
    hint.className = "muted";
    hint.textContent = "This run has no adverbs defined in its schema.";
    wrap.appendChild(hint);
    host.appendChild(wrap);
    return;
  }

  const current = (payload && typeof payload.adverbs === "object") ? payload.adverbs : {};
  const types   = Array.isArray(payload.available_types) ? payload.available_types : [];
  const ui      = (payload && payload.ui) || {};
  const working = { ...current };

  const form = document.createElement("div");
  form.className = "adverb-form card";
  const rows = document.createElement("div");
  rows.className = "adverb-rows";
  form.appendChild(rows);

  types.forEach(t => {
      const key = t.adverb;
      const u   = ui[key] || { kind: "scalar", field_type: t.field_type || "string" };
      const row = document.createElement("div");
      row.className = "form-row";
      row.innerHTML = `<label>${key}</label><div class="adverb-control"></div>`;
      const controlHost = row.querySelector('.adverb-control');

      if (u.kind === "ref_list") {
        const grid = document.createElement("div"); grid.className = "checkbox-grid";
        const selected = new Set(Array.isArray(working[key]) ? working[key] : []);
        (u.options || []).forEach(opt => {
          const lab = document.createElement("label"); lab.className = "cb";
          const cb = document.createElement("input");
          cb.type = "checkbox"; cb.value = opt.value; cb.checked = selected.has(String(opt.value));
          cb.onchange = () => {
            const arr = new Set(Array.isArray(working[key]) ? working[key] : []);
            if (cb.checked) arr.add(cb.value); else arr.delete(cb.value);
            working[key] = Array.from(arr);
          };
          lab.append(cb, ` ${opt.label || opt.value}`);
          grid.appendChild(lab);
        });
        controlHost.appendChild(grid);
      } else if (u.kind === "ref" || u.kind === "tag") {
        const sel = document.createElement("select");
        sel.innerHTML = `<option value="">(select)</option>` + (u.options || []).map(opt =>
          `<option value="${escapeHtml(opt.value)}" ${String(working[key] ?? "") === String(opt.value) ? "selected" : ""}>${escapeHtml(opt.label || opt.value)}</option>`
        ).join("");
        sel.onchange = () => { if (sel.value) working[key] = sel.value; else delete working[key]; };
        controlHost.appendChild(sel);
      } else if (u.kind === "picture") {
        const input = document.createElement("input");
        input.type = "text"; input.disabled = true; input.placeholder = "Controlled by pipeline";
        input.value = working[key] ?? "";
        controlHost.appendChild(input);
      } else {
        const ft = String(u.field_type || t.field_type || "string").toLowerCase();
        if (ft === "boolean") {
          const cb = document.createElement("input");
          cb.type = "checkbox"; cb.checked = !!working[key];
          cb.onchange = () => { working[key] = cb.checked; };
          controlHost.appendChild(cb);
        } else {
          const input = document.createElement("input");
          input.type = ft === "number" ? "number" : (ft === "date" ? "date" : "text");
          input.value = working[key] ?? (ft === "date" ? new Date().toISOString().slice(0,10) : "");
          input.oninput = () => {
            let v = input.value;
            if (ft === "number") v = Number.isFinite(Number(v)) ? Number(v) : undefined;
            if (v === "" || v === undefined) delete working[key]; else working[key] = v;
          };
          controlHost.appendChild(input);
        }
      }
      rows.appendChild(row);
  });

  const gate = await computeAdverbsGate(project, group, runID);
  const gateBanner = document.createElement("div");
  gateBanner.className = "raw-upload-gate";
  gateBanner.style.marginBottom = ".5rem";
  gateBanner.innerHTML = gate.allowed
    ? `✅ <strong>Adverbs unlocked</strong> — Adverbs step is current.`
    : `🔒 <strong>Adverbs locked</strong> — current step: <em>${escapeHtml(gate.reason)}</em>.`;
  wrap.appendChild(gateBanner);

  const actions = document.createElement("div");
  actions.className = "form-actions";
  actions.innerHTML = `<button class="btn" id="adv-save">💾 Save</button>`;
  form.appendChild(actions);

  wrap.appendChild(form);
  host.appendChild(wrap);

  const saveBtn = form.querySelector("#adv-save");
  if (!gate.allowed) {
    saveBtn.disabled = true;
    saveBtn.title = "Locked until the Adverbs step is current.";
    form.classList.add("is-locked");
    form.querySelectorAll("input, select").forEach(el => el.disabled = true);
  }

  saveBtn.onclick = async () => {
    try {
      await updateAdverbsAPI(project, group, runID, working);
      showToast("Adverbs saved.", "success");
      await renderAdverbEditor(container, project, group, runID);
    } catch (e) {
      showToast(`Failed to save adverbs: ${e.message}`, "error");
    }
  };
  debug("renderAdverbEditor:done");
}

/* ------------------------------------------------------------------------------------------------
 * Boot strap
 * ------------------------------------------------------------------------------------------------ */
window.onload = async () => {
  debug("window.onload");

  async function refreshRunlog() {
    const project = projectSelect?.value;
    const group = verbGroupSelect?.value;
    if (!project || !group) return;

    try {
      const data = await fetchJSON(`/runlog/${encodeURIComponent(project)}/${encodeURIComponent(group)}`);
      renderRunlog(data, project, group);
      statusBar.textContent = `Loaded ${data.rows.length} runs.`;

      if (!dumpSection.classList.contains("hidden")) {
        const ls = document.querySelector(".linear-status");
        const runid = ls?.dataset?.runid;
        if (runid) await openDump(project, group, runid);
      }
    } catch (err) {
      debug("refreshRunlog:error", err);
      showToast("Failed to refresh run log.", "error");
    }
  }

  window.addEventListener("gims:action_completed", async (evt) => {
    const path = String(evt?.detail?.path || "");
    if (!path.startsWith("/runlog/")) return;

    debug("[runlog-ui] gims:action_completed", evt.detail);

    const m = path.match(/^\/runlog\/([^/]+)\/([^/]+)\/([^/]+)\//);
    const [, proj, grp, rid] = m || [];

    let clicked = false;
    if (proj && grp && rid) {
      const sel = `.linear-status[data-project="${decodeURIComponent(proj)}"][data-group="${decodeURIComponent(grp)}"][data-runid="${decodeURIComponent(rid)}"] .ls-refresh`;
      const btn = document.querySelector(sel);
      if (btn) {
        debug("[runlog-ui] auto-clicking matching linear Refresh", { sel });
        btn.click();
        clicked = true;
      }
    }

    if (!clicked) {
      const visibleBtn = Array.from(document.querySelectorAll(".ls-refresh"))
        .find(b => b.offsetParent !== null);
      if (visibleBtn) {
        debug("[runlog-ui] auto-clicking first visible Refresh");
        visibleBtn.click();
        clicked = true;
      }
    }

    if (!clicked) {
      debug("[runlog-ui] no Refresh button found, running programmatic refresh");
      try {
        await refreshRunlog();
        await refreshAllGatedPanes();
      } catch (e) {
        console.error("[runlog-ui] programmatic refresh failed", e);
      }
    }

    showToast("Action applied. Status refreshed.", "success");
  });

  try {
    initializeVerbGroupSelect();

    const projects = await fetchJSON("/runlog_data_dump/projects");
    debug("projects:loaded", projects);

    projectSelect.innerHTML = "";
    projects.forEach(p => {
      const opt = document.createElement("option");
      opt.value = p;
      opt.textContent = p;
      projectSelect.appendChild(opt);
    });

    const url = new URL(location.href);
        const presetProj  = url.searchParams.get("project");
        const presetGroup = url.searchParams.get("group");
        const presetRunId = url.searchParams.get("run_id");

        const chosen = (presetProj && projects.includes(presetProj)) ? presetProj : projects[0];
        projectSelect.value = chosen;

        // 1. Load Project (Verb Groups)
        if (projectSelect.onchange) await projectSelect.onchange();
        setStatus(`Project selected: ${chosen}.`);

        // 2. Load Group (if present and valid)
        if (presetGroup) {
          const groupOptions = Array.from(verbGroupSelect.options).map(o => o.value);
          if (groupOptions.includes(presetGroup)) {
            verbGroupSelect.value = presetGroup;
            
            // Load the runlog table
            if (verbGroupSelect.onchange) await verbGroupSelect.onchange();
            
            // 3. Open Data Dump (if Run ID present)
            if (presetRunId) {
              await openDump(chosen, presetGroup, presetRunId);
            }
          }
        }
  } catch (err) {
    debug("onload:error", err);
    setStatus("Unable to load projects.");
  }
};
