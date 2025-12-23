/* Audit Workbench JS
   - Calls /api/audit/{project}
   - Renders summary, filters, paginated findings table
   - Exports JSON/CSV
*/

(() => {
  // Debug helpers
  const DEBUG_ENABLED = false;
  const debug = (...a) => { if (DEBUG_ENABLED) console.debug("[audit.js]", ...a); };

  // DOM helpers
  const qs  = (s, el=document) => el.querySelector(s);
  const qsa = (s, el=document) => Array.from(el.querySelectorAll(s));
  const el = (tag, attrs={}, children=[]) => {
    const n = document.createElement(tag);
    for (const [k,v] of Object.entries(attrs)) {
      if (k === "class") n.className = v;
      else if (k === "html") n.innerHTML = v;
      else if (k === "text") n.textContent = v;
      else if (v !== undefined && v !== null) n.setAttribute(k, v);
    }
    (Array.isArray(children) ? children : [children]).forEach(c => {
      if (c == null) return;
      if (typeof c === "string") n.appendChild(document.createTextNode(c));
      else n.appendChild(c);
    });
    return n;
  };

  // Toast & spinner
  const toastNode = qs("#aw-toast");
  const spinner = qs("#aw-spinner");
  const showToast = (msg, timeout=3000) => {
    toastNode.textContent = msg;
    toastNode.classList.add("show");
    setTimeout(() => toastNode.classList.remove("show"), timeout);
  };
  const spin = (on) => {
    spinner.classList.toggle("hidden", !on);
    spinner.setAttribute("aria-hidden", String(!on));
  };

  // State
  const state = {
    project: "",
    includeIndex: false,
    findings: [],
    summary: { errors: 0, warnings: 0, infos: 0, total: 0, by_code: {} },
    debug: null,
    filtered: [],
    page: 1,
    pageSize: 50,
    codeFilter: new Set(),     // selected codes
    severity: new Set(["error", "warn", "info"]),
    search: "",
  };

  // Elements
  const $projectSelect = qs("#aw-project");
  const $includeIdx  = qs("#aw-include-index");
  const $run         = qs("#aw-run");
  const $loadGroups  = qs("#aw-load-groups");

  const $sumTotal    = qs("#sum-total");
  const $sumErrors   = qs("#sum-errors");
  const $sumWarns    = qs("#sum-warnings");
  const $sumInfos    = qs("#sum-infos");
  const $runtime     = qs("#aw-runtime");

  const $sevChecks   = qsa(".aw-sev");
  const $codeSelect  = qs("#aw-code-filter");
  const $search      = qs("#aw-search");

  const $clear       = qs("#aw-clear-filters");
  const $tbody       = qs("#aw-tbody");
  const $page        = qs("#aw-page");
  const $prev        = qs("#aw-prev");
  const $next        = qs("#aw-next");
  const $pageSize    = qs("#aw-page-size");
  const $visible     = qs("#aw-visible-count");

  const $debugPre    = qs("#aw-debug-pre");

  const $exportJSON  = qs("#aw-export-json");
  const $exportCSV   = qs("#aw-export-csv");

  // Utils
  const fmtInt = (n) => (n ?? 0).toLocaleString();
  const download = (filename, text, type="application/json") => {
    const blob = new Blob([text], {type});
    const url  = URL.createObjectURL(blob);
    const a = el("a", {href:url, download:filename});
    document.body.appendChild(a);
    a.click();
    setTimeout(() => {
      URL.revokeObjectURL(url);
      a.remove();
    }, 0);
  };

  const severityBadge = (sev) => {
    const span = el("span", {class:`sev sev--${sev}`}, sev);
    return span;
  };

  const whereToString = (w) => {
    if (!w || typeof w !== "object") return "";
    const scope = w.scope || "";
    if (scope === "run") {
      const bits = [];
      if (w.verb) bits.push(`[${w.verb}]`);
      if (w._runID) bits.push(`#${w._runID}`);
      if (w.group) bits.push(`@${w.group}`);
      return bits.join(" ");
    }
    if (scope === "noun") {
      const bits = [];
      if (w.noun_type) bits.push(w.noun_type);
      if (w.primary) bits.push(`#${w.primary}`);
      if (w.field) bits.push(`.${w.field}`);
      return bits.join(" ");
    }
    if (scope === "adverb_type") {
      const bits = [];
      if (w.adverb) bits.push(w.adverb);
      if (w.verb) bits.push(`→ ${w.verb}`);
      return bits.join(" ");
    }
    if (scope === "noun_type") {
      const bits = [];
      if (w.noun_type) bits.push(w.noun_type);
      if (w.field) bits.push(`.${w.field}`);
      return bits.join(" ");
    }
    // generic fallback
    return Object.entries(w).map(([k,v]) => `${k}=${v}`).join(" ");
  };

  const flattenFinding = (f) => ({
    severity: f.severity || "",
    code:     f.code || "",
    scope:    f.where?.scope || "",
    where:    whereToString(f.where),
    message:  f.message || "",
    details:  JSON.stringify(f.details ?? {}),
  });

  const toCSV = (rows) => {
    const headers = ["severity","code","scope","where","message","details"];
    const escape = (s) => `"${String(s).replace(/"/g,'""')}"`;
    const lines = [headers.map(escape).join(",")];
    for (const r of rows) {
      lines.push(headers.map(h => escape(r[h] ?? "")).join(","));
    }
    return lines.join("\n");
  };

  // API
  const apiBase = "/api/audit";

  async function runAudit(project, includeIndex=false) {
    const url = `${apiBase}/${encodeURIComponent(project)}${includeIndex ? "?include_noun_index=true" : ""}`;
    debug("GET", url);
    spin(true);
    const t0 = performance.now();
    try {
      const res = await fetch(url, {headers: {"Accept":"application/json"}});
      if (!res.ok) {
        const text = await res.text().catch(()=>res.statusText);
        throw new Error(`HTTP ${res.status}: ${text}`);
      }
      const data = await res.json();
      const dt = performance.now() - t0;
      debug("audit result", data);
      return {data, ms: dt};
    } finally {
      spin(false);
    }
  }

  async function listVerbGroups(project) {
    const url = `${apiBase}/${encodeURIComponent(project)}/verb_groups`;
    debug("GET", url);
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } catch (e) {
      showToast(`Verb groups error: ${e.message}`);
      return {verb_groups: []};
    }
  }

  // Rendering
  function renderSummary(summary, ms) {
    $sumTotal.textContent  = fmtInt(summary.total || 0);
    $sumErrors.textContent = fmtInt(summary.errors || 0);
    $sumWarns.textContent  = fmtInt(summary.warnings || 0);
    $sumInfos.textContent  = fmtInt(summary.infos || 0);
    $runtime.textContent   = ms ? `(${Math.round(ms)} ms)` : "";
  }

  function populateCodeFilter(byCode) {
    // preserve selections when possible
    const selected = new Set(Array.from($codeSelect.selectedOptions).map(o=>o.value));
    $codeSelect.innerHTML = "";
    const codes = Object.keys(byCode || {}).sort();
    for (const c of codes) {
      const opt = el("option", {value:c, text:`${c} (${byCode[c]})`});
      if (selected.has(c)) opt.selected = true;
      $codeSelect.appendChild(opt);
    }
    // Adjust size to show up to 6 options without scrolling
    $codeSelect.size = Math.min(6, Math.max(1, codes.length));
  }

  function applyFilters() {
    const sv = state.search.trim().toLowerCase();
    const codeActive = state.codeFilter.size > 0;

    const filtered = state.findings.filter(f => {
      if (!state.severity.has(f.severity)) return false;
      if (codeActive && !state.codeFilter.has(f.code)) return false;

      if (sv) {
        const hay = [
          f.code, f.severity, f.message,
          JSON.stringify(f.where || {}),
          JSON.stringify(f.details || {})
        ].join(" ").toLowerCase();
        if (!hay.includes(sv)) return false;
      }
      return true;
    });

    state.filtered = filtered;
    state.page = 1;
    renderTable();
    $visible.textContent = fmtInt(filtered.length);
  }

	async function loadProjects() {
		try {
			const res = await fetch("/api/audit/projects");
			if (!res.ok) throw new Error(`HTTP ${res.status}`);
			const projects = await res.json();
			debug("Projects:", projects);

			$projectSelect.innerHTML = "";
			if (!projects.length) {
				$projectSelect.appendChild(el("option", {text:"(no projects found)", disabled:true}));
				return;
			}

			// Default option (you can choose which one)
			const defaultProj = projects[0];

			for (const p of projects) {
				const opt = el("option", {value:p, text:p});
				if (p === defaultProj) opt.selected = true;
				$projectSelect.appendChild(opt);
			}

			// Save into state
			state.project = defaultProj;
		} catch (e) {
			debug("Failed to load projects", e);
			showToast(`Projects load failed: ${e.message}`);
		}
	}

  function renderTable() {
    const start = (state.page - 1) * state.pageSize;
    const end   = start + state.pageSize;
    const pageRows = state.filtered.slice(start, end);

    $tbody.innerHTML = "";
    const frag = document.createDocumentFragment();

    for (const f of pageRows) {
      const tr = el("tr");

      const tdSev = el("td", {class:"col-sev"});
      tdSev.appendChild(severityBadge(f.severity));

      const tdCode = el("td", {class:"col-code"}, f.code);

      const scopeStr = f.where?.scope || "";
      const tdScope = el("td", {class:"col-scope"}, scopeStr);

      const tdWhere = el("td", {class:"col-where"}, whereToString(f.where));

      const tdMsg = el("td", {class:"col-msg"});
      tdMsg.appendChild(el("div", {class:"msg"}, f.message || ""));

      const tdAct = el("td", {class:"col-actions"});
      const btn = el("button", {class:"aw-btn aw-btn--sm"}, "View");
      const panel = el("div", {class:"aw-details hidden"});
      const pre = el("pre", {class:"aw-json"}, JSON.stringify({where:f.where, details:f.details}, null, 2));
      panel.appendChild(pre);

      btn.addEventListener("click", () => {
        panel.classList.toggle("hidden");
        btn.textContent = panel.classList.contains("hidden") ? "View" : "Hide";
      });

      tdAct.appendChild(btn);
      tdAct.appendChild(panel);

      tr.append(tdSev, tdCode, tdScope, tdWhere, tdMsg, tdAct);
      frag.appendChild(tr);
    }

    $tbody.appendChild(frag);

    // Paging UI
    const totalPages = Math.max(1, Math.ceil(state.filtered.length / state.pageSize));
    $page.textContent = `Page ${state.page} / ${totalPages}`;
    $prev.disabled = state.page <= 1;
    $next.disabled = state.page >= totalPages;
  }

  // Events
  $run.addEventListener("click", async () => {
    state.project = $projectSelect.value;
    state.includeIndex = $includeIdx.checked;
    if (!state.project) {
      showToast("Enter a project name or absolute path");
      $project.focus();
      return;
    }
    try {
      const {data, ms} = await runAudit(state.project, state.includeIndex);
      state.findings = Array.isArray(data.findings) ? data.findings : [];
      state.summary  = data.summary || state.summary;
      state.debug    = data.debug || null;

      renderSummary(state.summary, ms);
      populateCodeFilter(state.summary.by_code || {});
      // clear filters and set defaults
      state.severity = new Set(["error","warn","info"]);
      state.codeFilter.clear();
      state.search = "";
      $search.value = "";

      applyFilters();

      // Debug panel
      const dbgLines = [];
      dbgLines.push(`Project: ${state.project}`);
      dbgLines.push(`Findings: ${fmtInt(state.summary.total)}`);
      if (state.debug) {
        dbgLines.push(`Verb groups: ${(state.debug.verb_groups || []).join(", ") || "—"}`);
        dbgLines.push("Noun index sizes:");
        for (const [k,v] of Object.entries(state.debug.noun_index_sizes || {})) {
          dbgLines.push(`  - ${k}: ${v}`);
        }
      } else {
        dbgLines.push("(no extra debug included)");
      }
      $debugPre.textContent = dbgLines.join("\n");
      showToast("Audit complete");
    } catch (e) {
      debug("audit error", e);
      showToast(`Audit failed: ${e.message}`);
    }
  });

  $loadGroups.addEventListener("click", async () => {
    const project = $project.value.trim();
    if (!project) {
      showToast("Enter a project first");
      return;
    }
    const data = await listVerbGroups(project);
    $debugPre.textContent = JSON.stringify(data, null, 2);
  });

  // Severity filter
  $sevChecks.forEach(chk => {
    chk.addEventListener("change", () => {
      const val = chk.value;
      if (chk.checked) state.severity.add(val);
      else state.severity.delete(val);
      applyFilters();
    });
  });

  // Code filter (multi-select)
  $codeSelect.addEventListener("change", () => {
    state.codeFilter = new Set(Array.from($codeSelect.selectedOptions).map(o => o.value));
    applyFilters();
  });

  // Search
  $search.addEventListener("input", () => {
    state.search = $search.value || "";
    applyFilters();
  });

  // Clear filters
  $clear.addEventListener("click", () => {
    state.severity = new Set(["error","warn","info"]);
    $sevChecks.forEach(c => c.checked = true);
    state.codeFilter.clear();
    Array.from($codeSelect.options).forEach(o => o.selected = false);
    state.search = "";
    $search.value = "";
    applyFilters();
  });

  // Paging
  $pageSize.addEventListener("change", () => {
    state.pageSize = parseInt($pageSize.value, 10) || 50;
    state.page = 1;
    renderTable();
  });
  $prev.addEventListener("click", () => {
    if (state.page > 1) {
      state.page--;
      renderTable();
    }
  });
  $next.addEventListener("click", () => {
    const totalPages = Math.max(1, Math.ceil(state.filtered.length / state.pageSize));
    if (state.page < totalPages) {
      state.page++;
      renderTable();
    }
  });

  // Exports
  $exportJSON.addEventListener("click", () => {
    if (!state.findings.length) return showToast("Nothing to export");
    download("audit_findings.json", JSON.stringify({
      project: state.project,
      summary: state.summary,
      findings: state.findings
    }, null, 2));
  });

  $exportCSV.addEventListener("click", () => {
    if (!state.filtered.length) return showToast("Nothing to export");
    const flat = state.filtered.map(flattenFinding);
    const csv = toCSV(flat);
    download("audit_findings.csv", csv, "text/csv");
  });

  // Initial
  (function init() {
		loadProjects();
    debug("Audit UI ready");
  })();
})();