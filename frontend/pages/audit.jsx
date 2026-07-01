// frontend/pages/audit.jsx — Audit Workbench (Phase 6 React, 2nd exemplar page).
// Faithful port of the vanilla audit.js (project picker, severity/code/search filters, JSON/CSV
// export, debug panel), with the hand-rolled findings <table> + per-row "View" JSON restructured
// onto the shared React GridTable (sticky/sortable/reactive-select) + a structured SpecList detail
// panel — the Phase-5 "structured, not data-as-text" bar, and the live proof of the React lib.
import { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { Icon, GridTable, SpecList, StateBlock } from "../lib/ui.jsx";
import { enc, fetchJSON, mountOnAuth } from "../lib/api.js";

const SEVERITIES = ["error", "warn", "info"];
const EMPTY_SUMMARY = { errors: 0, warnings: 0, infos: 0, total: 0, by_code: {} };
const toast = (msg, kind = "ok") => { const G = window.GIMS; if (G && G.toast) G.toast(msg, kind); };
const fmtInt = (n) => Number(n == null ? 0 : n).toLocaleString();

// ── value helpers (ported verbatim from audit.js) ────────────────────────────────
function whereToString(w) {
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
  return Object.entries(w).map(([k, v]) => `${k}=${v}`).join(" ");
}

function flattenFinding(f) {
  return {
    severity: f.severity || "",
    code: f.code || "",
    scope: (f.where && f.where.scope) || "",
    where: whereToString(f.where),
    message: f.message || "",
    details: JSON.stringify(f.details == null ? {} : f.details),
  };
}

function toCSV(rows) {
  const headers = ["severity", "code", "scope", "where", "message", "details"];
  const escape = (s) => `"${String(s).replace(/"/g, '""')}"`;
  const lines = [headers.map(escape).join(",")];
  for (const r of rows) lines.push(headers.map((h) => escape(r[h] == null ? "" : r[h])).join(","));
  return lines.join("\n");
}

function download(filename, text, type = "application/json") {
  const blob = new Blob([text], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click();
  setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 0);
}

// ── findings table columns ────────────────────────────────────────────────────────
const COLUMNS = [
  { key: "severity", label: "Severity", width: "96px", render: (v) => <span className={"sev sev--" + v}>{v}</span> },
  { key: "code", label: "Code", width: "150px" },
  { key: "scope", label: "Scope", width: "110px" },
  { key: "where", label: "Where", width: "200px" },
  { key: "message", label: "Message" },
];

function FindingDetail({ f }) {
  if (!f) return null;
  const items = [
    { label: "Code", value: f.code || "—" },
    { label: "Severity", value: f.severity || "—", tone: f.severity === "error" ? "err" : f.severity === "warn" ? "warn" : "info" },
    { label: "Scope", value: (f.where && f.where.scope) || "—" },
    { label: "Where", value: whereToString(f.where) || "—" },
    { label: "Message", value: f.message || "—" },
  ];
  const hasDetails = f.details && typeof f.details === "object" && Object.keys(f.details).length > 0;
  return (
    <section className="panel aw-detail">
      <div className="panel-head">
        <Icon name="info" /><span className="panel-title">Finding detail</span>
      </div>
      <div className="panel-body">
        <SpecList items={items} />
        {hasDetails ? (
          <details className="aw-details-raw" open>
            <summary>Raw details + location</summary>
            <pre className="aw-json">{JSON.stringify({ where: f.where, details: f.details }, null, 2)}</pre>
          </details>
        ) : null}
      </div>
    </section>
  );
}

function App() {
  const [projects, setProjects] = useState([]);
  const [project, setProject] = useState("");
  const [includeIndex, setIncludeIndex] = useState(false);
  const [loading, setLoading] = useState(false);
  const [ran, setRan] = useState(false);
  const [findings, setFindings] = useState([]);          // raw findings, each tagged with __k
  const [summary, setSummary] = useState(EMPTY_SUMMARY);
  const [runtimeMs, setRuntimeMs] = useState(null);
  const [debugText, setDebugText] = useState("Run an audit to see debug output.");
  // filters
  const [severity, setSeverity] = useState(new Set(SEVERITIES));
  const [codeFilter, setCodeFilter] = useState(new Set());
  const [search, setSearch] = useState("");
  const [selKey, setSelKey] = useState(null);

  // load projects once auth resolves (App is only mounted after auth)
  useEffect(() => {
    (async () => {
      try {
        const list = await fetchJSON("/api/audit/projects");
        setProjects(Array.isArray(list) ? list : []);
        if (Array.isArray(list) && list.length) setProject(list[0]);
      } catch (e) { toast("Projects load failed: " + (e.message || e), "err"); }
    })();
  }, []);

  async function runAudit() {
    if (!project) { toast("Select a project first", "err"); return; }
    setLoading(true);
    const t0 = performance.now();
    try {
      const url = `/api/audit/${enc(project)}${includeIndex ? "?include_noun_index=true" : ""}`;
      const data = await fetchJSON(url);
      const ms = performance.now() - t0;
      const fs = (Array.isArray(data.findings) ? data.findings : []).map((f, i) => ({ ...f, __k: i }));
      const sum = data.summary || EMPTY_SUMMARY;
      setFindings(fs);
      setSummary(sum);
      setRuntimeMs(ms);
      setSeverity(new Set(SEVERITIES));
      setCodeFilter(new Set());
      setSearch("");
      setSelKey(null);
      setRan(true);
      // debug panel
      const lines = [`Project: ${project}`, `Findings: ${fmtInt(sum.total)}`];
      if (data.debug) {
        lines.push(`Verb groups: ${(data.debug.verb_groups || []).join(", ") || "—"}`);
        lines.push("Noun index sizes:");
        for (const [k, v] of Object.entries(data.debug.noun_index_sizes || {})) lines.push(`  - ${k}: ${v}`);
      } else {
        lines.push("(no extra debug included)");
      }
      setDebugText(lines.join("\n"));
      toast("Audit complete", "ok");
    } catch (e) {
      toast("Audit failed: " + (e.message || e), "err");
    } finally {
      setLoading(false);
    }
  }

  async function loadVerbGroups() {
    if (!project) { toast("Select a project first", "err"); return; }
    try {
      const data = await fetchJSON(`/api/audit/${enc(project)}/verb_groups`);
      setDebugText(JSON.stringify(data, null, 2));
    } catch (e) { toast("Verb groups error: " + (e.message || e), "err"); }
  }

  // derived: filtered findings
  const filtered = useMemo(() => {
    const sv = search.trim().toLowerCase();
    const codeActive = codeFilter.size > 0;
    return findings.filter((f) => {
      if (!severity.has(f.severity)) return false;
      if (codeActive && !codeFilter.has(f.code)) return false;
      if (sv) {
        const hay = [f.code, f.severity, f.message, JSON.stringify(f.where || {}), JSON.stringify(f.details || {})].join(" ").toLowerCase();
        if (!hay.includes(sv)) return false;
      }
      return true;
    });
  }, [findings, severity, codeFilter, search]);

  const gridRows = useMemo(() => filtered.map((f) => ({
    __k: f.__k,
    severity: f.severity || "",
    code: f.code || "",
    scope: (f.where && f.where.scope) || "",
    where: whereToString(f.where),
    message: f.message || "",
  })), [filtered]);

  const selected = useMemo(() => filtered.find((f) => f.__k === selKey) || null, [filtered, selKey]);

  const codes = useMemo(() => Object.keys(summary.by_code || {}).sort(), [summary]);

  const toggleSeverity = (val, on) => {
    setSeverity((prev) => { const next = new Set(prev); if (on) next.add(val); else next.delete(val); return next; });
  };
  const onCodeChange = (e) => setCodeFilter(new Set(Array.from(e.target.selectedOptions).map((o) => o.value)));
  const clearFilters = () => { setSeverity(new Set(SEVERITIES)); setCodeFilter(new Set()); setSearch(""); };

  const exportJSON = () => {
    if (!findings.length) return toast("Nothing to export", "info");
    download("audit_findings.json", JSON.stringify({ project, summary, findings: findings.map(({ __k, ...f }) => f) }, null, 2));
  };
  const exportCSV = () => {
    if (!filtered.length) return toast("Nothing to export", "info");
    download("audit_findings.csv", toCSV(filtered.map(flattenFinding)), "text/csv");
  };

  return (
    <>
      <section className="panel aw-toolbar">
        <div className="panel-head">
          <Icon name="audit" /><span className="panel-title">Run an audit</span>
        </div>
        <div className="panel-body aw-toolbar-row">
          <label className="field aw-field">
            <span className="field-label">Project</span>
            <select id="aw-project" className="input select aw-select" value={project} onChange={(e) => setProject(e.target.value)}>
              {projects.length === 0 ? <option value="" disabled>— Select Project —</option> : null}
              {projects.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </label>
          <label className="aw-checkbox">
            <input type="checkbox" checked={includeIndex} onChange={(e) => setIncludeIndex(e.target.checked)} />
            <span>Include noun index (debug)</span>
          </label>
          <div className="aw-toolbar-actions">
            <button id="aw-run" className="btn-primary aw-btn aw-btn--primary" disabled={loading} onClick={runAudit}>
              <Icon name="play" />{loading ? "Running…" : "Run Audit"}
            </button>
            <button id="aw-load-groups" className="btn aw-btn" title="List verb groups (debug)" onClick={loadVerbGroups}>
              <Icon name="runlog" />Verb Groups
            </button>
          </div>
        </div>
      </section>

      <section className="aw-summary">
        <div className="aw-sum-item aw-sum-item--total panel">
          <div className="aw-sum-label">Total</div>
          <div className="aw-sum-value">{ran ? fmtInt(summary.total) : "—"}</div>
        </div>
        <div className="aw-sum-item aw-sum-item--error panel">
          <div className="aw-sum-label">Errors</div>
          <div className="aw-sum-value">{ran ? fmtInt(summary.errors) : "—"}</div>
        </div>
        <div className="aw-sum-item aw-sum-item--warn panel">
          <div className="aw-sum-label">Warnings</div>
          <div className="aw-sum-value">{ran ? fmtInt(summary.warnings) : "—"}</div>
        </div>
        <div className="aw-sum-item aw-sum-item--info panel">
          <div className="aw-sum-label">Info</div>
          <div className="aw-sum-value">{ran ? fmtInt(summary.infos) : "—"}</div>
        </div>
        <div className="aw-summary__export panel">
          <span className="aw-sum-label">Export</span>
          <button className="btn aw-btn sm" onClick={exportJSON}><Icon name="download" />JSON</button>
          <button className="btn aw-btn sm" onClick={exportCSV}><Icon name="download" />CSV</button>
          <span className="aw-runtime">{runtimeMs ? `(${Math.round(runtimeMs)} ms)` : ""}</span>
        </div>
      </section>

      <section className="aw-filters panel">
        <div className="aw-filter-row panel-body">
          <div className="aw-filter-group">
            <span className="aw-filter-label">Severity</span>
            {SEVERITIES.map((sv) => (
              <label className="aw-chip" key={sv}>
                <input type="checkbox" className="aw-sev" value={sv} checked={severity.has(sv)} onChange={(e) => toggleSeverity(sv, e.target.checked)} />
                <span className={"chip chip--" + sv}>{sv}</span>
              </label>
            ))}
          </div>
          <div className="aw-filter-group">
            <label className="field aw-field">
              <span className="field-label">Code</span>
              <select id="aw-code-filter" className="input select aw-select" multiple
                      size={Math.min(6, Math.max(1, codes.length))}
                      title="Filter by code (multi-select)"
                      value={Array.from(codeFilter)} onChange={onCodeChange}>
                {codes.map((c) => <option key={c} value={c}>{`${c} (${summary.by_code[c]})`}</option>)}
              </select>
            </label>
          </div>
          <div className="aw-filter-group aw-filter-grow">
            <label className="field aw-field aw-field--grow">
              <span className="field-label">Search</span>
              <input id="aw-search" className="input aw-input" type="search" placeholder="message, scope, where…"
                     value={search} onChange={(e) => setSearch(e.target.value)} />
            </label>
          </div>
          <div className="aw-filter-group">
            <button id="aw-clear-filters" className="btn aw-btn" onClick={clearFilters}><Icon name="close" />Clear</button>
          </div>
        </div>
      </section>

      <section className="aw-results panel">
        <div className="aw-results__header panel-head">
          <div className="aw-results__count">
            <span id="aw-visible-count" className="count-pill">{fmtInt(filtered.length)}</span> findings
            {ran && filtered.length !== findings.length ? <span className="aw-runtime">of {fmtInt(findings.length)}</span> : null}
          </div>
        </div>
        <div className="aw-grid-host">
          {!ran
            ? <StateBlock kind="empty" icon="audit" title="No audit yet" message="Pick a project and run an audit to see findings." />
            : loading
              ? <StateBlock kind="loading" message="Running audit…" />
              : <GridTable columns={COLUMNS} rows={gridRows} selectedKey={selKey}
                           onSelect={(_r, key) => setSelKey(key)} maxHeight="62vh"
                           empty={{ icon: "check", title: "No findings match", message: "Nothing matches the current filters." }} />}
        </div>
      </section>

      {selected ? <FindingDetail f={selected} /> : null}

      <section id="aw-debug" className="panel aw-debug">
        <details>
          <summary className="panel-head"><Icon name="terminal" /><span className="panel-title">Debug</span></summary>
          <pre id="aw-debug-pre">{debugText}</pre>
        </details>
      </section>
    </>
  );
}

mountOnAuth("audit-root", (host) => createRoot(host).render(<App />));
