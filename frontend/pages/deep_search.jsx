// frontend/pages/deep_search.jsx — Deep Search (Phase 6 React; tool pages T4).
// React port of the 499-line vanilla deep_search.js: a read-only cross-project search over schemas,
// noun instances, and verb runs, with All/Schema/Nouns/Verbs result tabs and term highlighting.
// Reuses deep_search.css (the .ds-*/.tab-button/.result-*/.schema|noun|verb-result/.search-stats +
// #project-select/#search-input/#results-list/#no-results contract reproduced — tour targets kept).
//
// No mutations — the only backend contract is GET /deep_search/projects and
// GET /deep_search/{project}?term=<encoded>. Highlighting is XSS-safe by construction (text is split
// on the term and matches wrapped in <mark>, never innerHTML). The vanilla's debug console.log dropped.
import { Fragment, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { Icon } from "../lib/ui.jsx";
import { enc, fetchJSON, mountOnAuth, toast } from "../lib/api.js";

const showError = (m) => toast(m, "err");

function Highlight({ text, term }) {
  const s = text == null ? "" : (typeof text === "object" ? JSON.stringify(text, null, 2) : String(text));
  if (!term) return s;
  const escaped = term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const parts = s.split(new RegExp(`(${escaped})`, "gi"));
  return parts.map((p, i) => (i % 2 === 1 ? <mark key={i}>{p}</mark> : <Fragment key={i}>{p}</Fragment>));
}

const Lines = ({ rows }) => rows.filter(Boolean).map(([label, value], i) => (
  <Fragment key={i}><strong>{label}:</strong> {value}<br /></Fragment>
));

function SchemaItem({ match, term }) {
  return (
    <div className="result-item schema-result">
      <h3>{match.schema_type}: {match.schema_name}</h3>
      <div className="result-path">{match.path || ""}</div>
      <div className="result-details"><strong>Match:</strong> <Highlight text={JSON.stringify(match.match_context, null, 2)} term={term} /></div>
    </div>
  );
}

function resolveNounPrimary(match) {
  const nounType = match._noun_type || "Unknown Type";
  let value = "", field = "";
  if (match.match_context) {
    const cf = Object.keys(match.match_context)[0];
    const cv = match.match_context[cf];
    if (cf.toLowerCase().includes("id") || cf.toLowerCase() === "name") { value = cv; field = cf; }
  }
  if (!value) {
    if (match["Sample ID"]) { value = match["Sample ID"]; field = "Sample ID"; }
    else {
      const candidates = [`${nounType.toLowerCase()}_id`, `${nounType} ID`, `${nounType} Id`, `${nounType.toLowerCase()} id`,
        "id", "ID", "sample_id", "Sample ID", "batch_id", "Batch ID", "submission_id", "Submission ID", "run_id", "Run ID", "name"];
      for (const f of candidates) { if (match[f]) { value = match[f]; field = f; break; } }
    }
  }
  return { nounType, value, field };
}

// Deep-link a result to the read-only Instance Inspector (the "solo page" for a sample/run).
const inspectNounHref = (project, type, id) => `/inspect?project=${enc(project)}&kind=noun&type=${enc(type || "")}&id=${enc(id == null ? "" : id)}`;
const inspectRunHref = (project, group, runId, verb) => `/inspect?project=${enc(project)}&kind=run&group=${enc(group || "")}&run_id=${enc(runId || "")}&verb=${enc(verb || "")}`;

function NounItem({ project, match, term }) {
  const { nounType, value, field } = resolveNounPrimary(match);
  const titleText = value || match.name || match.Name || nounType;
  const ctxKey = match.match_context ? Object.keys(match.match_context)[0] : null;
  const linkId = value || match.name || match.Name;
  return (
    <div className="result-item noun-result">
      <h3 data-noun-type={linkId ? nounType : undefined}>
        {linkId ? <a className="result-link" href={inspectNounHref(project, nounType, linkId)} title="Open in Inspector">{titleText}</a> : titleText}</h3>
      <div className="result-subtitle">Type: {nounType}</div>
      <div className="result-path">
        <Lines rows={[
          value && titleText !== value ? [field, value] : null,
          match._runID ? ["Run ID", match._runID] : null,
        ]} />
      </div>
      <div className="result-details">
        {match.match_context
          ? <><strong>Matched in field "{ctxKey}":</strong> <Highlight text={match.match_context[ctxKey]} term={term} /></>
          : <><strong>Match:</strong> <Highlight text={JSON.stringify(match, null, 2)} term={term} /></>}
      </div>
    </div>
  );
}

function resolveVerbRunId(match) {
  if (match._primary_id_field_resolved || match._primary_id_field) {
    const pf = match._primary_id_field_resolved || match._primary_id_field;
    return match[pf] || "Unknown Run";
  }
  return match["general ID"] || match.run_ID || match.run_id || match.runID || match.RunID || match["Run ID"] || match.id || match.ID || "Unknown Run";
}

function VerbItem({ project, match, term }) {
  const runId = resolveVerbRunId(match);
  const ctxKey = match.match_context ? Object.keys(match.match_context)[0] : null;
  const dateField = match.date_tested || match.date || match.run_date || match.timestamp || null;
  const linkable = runId && runId !== "Unknown Run";
  return (
    <div className="result-item verb-result">
      <h3>{linkable ? <a className="result-link" href={inspectRunHref(project, match._verb_group, runId, match.test_type || match.verb)} title="Open in Inspector">{runId}</a> : runId}</h3>
      <div className="result-subtitle">Verb: {match._verb_group || "Unknown"}</div>
      <div className="result-path">
        <Lines rows={[
          match.test_type ? ["Test Type", match.test_type] : null,
          dateField ? ["Date", dateField] : null,
          match.status ? ["Status", match.status] : null,
          match.result ? ["Result", match.result] : null,
          match.operator ? ["Operator", match.operator] : null,
        ]} />
      </div>
      <div className="result-details">
        {match.match_context
          ? <><strong>Matched in field "{ctxKey}":</strong> <Highlight text={match.match_context[ctxKey]} term={term} /></>
          : <><strong>Match:</strong> <Highlight text={JSON.stringify(match, null, 2)} term={term} /></>}
        {match.match_score ? <><br /><small>Match score: {match.match_score} ({match.match_type || "unknown type"})</small></> : null}
      </div>
    </div>
  );
}

function DeepSearch() {
  const [projects, setProjects] = useState(null);
  const [project, setProject] = useState("");
  const [term, setTerm] = useState("");
  const [searchedTerm, setSearchedTerm] = useState("");
  const [results, setResults] = useState(null);
  const [tab, setTab] = useState("all");
  const [loading, setLoading] = useState(false);
  const [stats, setStats] = useState("");
  const [emptyMsg, setEmptyMsg] = useState({ text: "Enter a search term to begin", error: false });

  useEffect(() => {
    fetchJSON("/deep_search/projects").then((ps) => {
      if (!Array.isArray(ps) || !ps.length) { setProjects([]); showError("No projects available"); return; }
      setProjects(ps); setProject(ps[0]);
    }).catch((e) => { setProjects([]); showError("Error loading projects: " + (e.message || e)); });
  }, []);

  const clearResults = () => { setResults(null); setStats(""); setEmptyMsg({ text: "Enter a search term to begin", error: false }); };

  const search = async () => {
    const t = term.trim();
    if (!t) { showError("Please enter a search term"); return; }
    if (!project) { showError("Please select a project"); return; }
    setLoading(true); setResults(null); setStats("");
    try {
      const data = await fetchJSON(`/deep_search/${enc(project)}?term=${enc(t)}`);
      setSearchedTerm(t); setResults(data.results || {});
    } catch (e) {
      const msg = e.status === 500 ? "Internal server error - Search engine encountered a problem" : (e.message || `Search failed`);
      setEmptyMsg({ text: msg, error: true }); setStats("Search failed"); showError("Search error: " + msg);
    } finally { setLoading(false); }
  };

  const count = (r) => (r ? ((r.schema || []).length + (r.noun_instances || []).length + (r.verb_runs || []).length) : 0);
  const total = count(results);
  useEffect(() => {
    if (results == null) return;
    if (total === 0) { setStats("No matches found"); setEmptyMsg({ text: "No results found", error: false }); }
    else setStats(`Found ${total} matches`);
  }, [results, total]);

  const show = (key) => tab === "all" || tab === key;
  const TABS = [["all", "All"], ["schema", "Schema"], ["noun", "Nouns"], ["verb", "Verbs"]];

  return (
    <>
      <section className="panel ds-search">
        <div className="panel-body ds-search-row">
          <label className="field ds-project"><span className="field-label">Project</span>
            <select id="project-select" className="input select" value={project} onChange={(e) => { setProject(e.target.value); clearResults(); }}>
              {projects == null ? <option value="">Loading…</option> : !projects.length ? <option value="">No projects available</option> : projects.map((p) => <option key={p} value={p}>{p}</option>)}
            </select></label>
          <div className="ds-searchbox">
            <span className="ds-search-icon"><Icon name="search" /></span>
            <input type="search" id="search-input" className="input ds-input" placeholder="Search across schemas, nouns, and verb runs…"
                   value={term} onChange={(e) => setTerm(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") search(); }} />
            <button id="search-button" className="btn-primary ds-go" onClick={search}><Icon name="search" />Search</button>
          </div>
        </div>
      </section>

      <section className="panel ds-results-panel">
        <div className="panel-head ds-results-head">
          <div className="results-tabs" role="tablist" aria-label="Result types">
            {TABS.map(([k, label]) => <button key={k} className={"tab-button" + (tab === k ? " active" : "")} role="tab" aria-selected={tab === k} onClick={() => setTab(k)}>{label}</button>)}
          </div>
          <div className="search-stats" id="search-stats" aria-live="polite">{stats}</div>
        </div>
        <div className="ds-results-body">
          {loading ? <div className="loading-indicator" id="loading-indicator" role="status"><span className="gims-spinner" /><p>Searching…</p></div> : null}
          <div className="results-list" id="results-list">
            {results && total > 0 ? (
              <>
                {show("schema") && (results.schema || []).length ? (
                  <div className="result-section" data-type="schema"><h2>Schema Matches</h2>{results.schema.map((m, i) => <SchemaItem key={i} match={m} term={searchedTerm} />)}</div>
                ) : null}
                {show("noun") && (results.noun_instances || []).length ? (
                  <div className="result-section" data-type="noun"><h2>Noun Matches</h2>{results.noun_instances.map((m, i) => <NounItem key={i} project={project} match={m} term={searchedTerm} />)}</div>
                ) : null}
                {show("verb") && (results.verb_runs || []).length ? (
                  <div className="result-section" data-type="verb"><h2>Verb Matches</h2>{results.verb_runs.map((m, i) => <VerbItem key={i} project={project} match={m} term={searchedTerm} />)}</div>
                ) : null}
              </>
            ) : null}
          </div>
          {(!results || total === 0) && !loading ? (
            <div className="no-results" id="no-results">
              {emptyMsg.error
                ? <div className="error-message"><h3>Search Error</h3><p>{emptyMsg.text}</p><p className="error-help">Please try a different search term or contact your administrator.</p></div>
                : <><span className="gims-state-mark icon-chip round"><Icon name="search" /></span><p>{emptyMsg.text}</p></>}
            </div>
          ) : null}
        </div>
      </section>
      <div id="status-bar" />
    </>
  );
}

mountOnAuth("deep-search-root", (host) => createRoot(host).render(<DeepSearch />));
