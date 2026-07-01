// frontend/pages/investigation.jsx — Lineage Investigator (Phase 6 React; remaining vanilla R1).
// React port of the 277-line vanilla investigation.js: a reactive master/detail — pick project + noun,
// records load into a sortable sticky GridTable; click a row → its lineage renders as a visual STORY
// (progress ring + status timeline/stepper + typed override SpecList + deep-link entity chips). Built on
// the React component lib (frontend/lib/ui.jsx). Reuses investigation.css (the .inv-*/.records-panel/
// .lineage-panel/.controls-panel + #project/#noun/#filter/#record-grid/#output contract reproduced; the
// tour targets #project/.records-panel/.lineage-panel preserved). Read-only — no data mutations.
//
// Endpoints (the two POSTs are read/format calls — bodies preserved):
//   GET  /investigation/{projects, nouns/{p}, items/{p}/{n}}
//   POST /investigation/format_table/{p}/{n}   { records: <items> }   → { primary_id_field, columns, rows }
//   POST /investigation/lineage_ui/{p}/{n}     { record }             → { runs, parents, retests, ... }
// Deep link /investigation?project=&noun=&id= preselects + loads + opens that record's lineage (the
// Inspector's "Trace lineage" backlink). Entity chips deep-link to the read-only /inspect viewer.
import { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { Icon, GridTable, ProgressRing, StatusTimeline, Stepper, SpecList, EntityChip, EntityList, StateBlock } from "../lib/ui.jsx";
import { enc, fetchJSON, mountOnAuth, toast } from "../lib/api.js";

const API = {
  projects: "/investigation/projects",
  nouns: (p) => `/investigation/nouns/${enc(p)}`,
  items: (p, n) => `/investigation/items/${enc(p)}/${enc(n)}`,
  table: (p, n) => `/investigation/format_table/${enc(p)}/${enc(n)}`,
  lineageUI: (p, n) => `/investigation/lineage_ui/${enc(p)}/${enc(n)}`,
};
const post = (url, body) => fetchJSON(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

const nounHref = (project, type, id) => `/inspect?project=${enc(project)}&kind=noun&type=${enc(type || "")}&id=${enc(id == null ? "" : id)}`;
const runHref = (project, group, runId, verb) => `/inspect?project=${enc(project)}&kind=run&group=${enc(group || "")}&run_id=${enc(runId || "")}&verb=${enc(verb || "")}`;
function RefChip({ project, n }) {
  const type = n._noun_type || "Noun"; const pk = n._primary_id_field || "id"; const val = n[pk] != null ? n[pk] : (n.id || "");
  return <EntityChip kind={type} id={val} label={val} href={nounHref(project, type, val)} title={`${type} · ${pk}=${val}`} />;
}

function Section({ title, icon, children }) {
  return (
    <section className="inv-section">
      <div className="inv-section-head"><span className="inv-section-icon"><Icon name={icon} /></span><span className="inv-section-title">{title}</span></div>
      {children}
    </section>
  );
}

function RunCard({ project, run }) {
  const classic = run.mode !== "linear";
  const zones = classic
    ? (run.zones || []).map((z) => ({ label: z.label, state: z.ok ? "done" : "pending", detail: String(z.value == null ? "" : z.value) }))
    : ((run.linear && run.linear.breakdown) || []).map((st) => ({ label: st.label || st.id || st.type, state: st.completed ? "done" : (st.type === "gate" ? "gate" : "pending"), detail: st.completed ? "Complete" : (st.reason || "Pending") }));
  const overrides = run.override_status ? String(run.override_status).split(/\r?\n/).map((l) => l.trim()).filter(Boolean).map((line) => {
    const m = line.match(/^([A-Za-z_]+)\s*:\s*(.*)$/); const type = m ? m[1] : "NOTE"; const val = m ? m[2] : line;
    const tone = /EXCEPTION/i.test(type) ? "err" : /NOTIFICATION/i.test(type) ? "info" : "ok";
    return { label: type, value: val, tone };
  }) : [];
  return (
    <div className="inv-run panel">
      <div className="inv-run-head">
        <div className="inv-run-id"><span className="inv-run-verb">{run.verb || "(verb)"}</span>
          <EntityChip kind="Run" id={run.run_id} label={run.run_id} href={runHref(project, run.verb_group, run.run_id, run.verb)} /></div>
        <div className="inv-run-badges"><span className="chip">{run.verb_group || "Tests"}</span><span className="chip accent">{run.mode === "linear" ? "Linear" : "Classic"}</span></div>
      </div>
      <div className="inv-run-body">
        <ProgressRing percent={run.percent || 0} />
        <div className="inv-run-prog">
          {classic ? <StatusTimeline steps={zones} /> : <>{run.progress_text ? <div className="muted">{run.progress_text}</div> : null}<Stepper steps={zones} /></>}
        </div>
      </div>
      {overrides.length ? <div className="inv-run-sub"><div className="inv-sub-label">Overrides</div><SpecList items={overrides} compact /></div> : null}
      {Array.isArray(run.referencing_nouns) && run.referencing_nouns.length ? (
        <div className="inv-run-sub"><div className="inv-sub-label">Referencing nouns</div><EntityList>{run.referencing_nouns.map((n, i) => <RefChip key={i} project={project} n={n} />)}</EntityList></div>
      ) : null}
    </div>
  );
}

function Lineage({ project, data }) {
  const runs = data.runs || [];
  const doneRuns = runs.filter((r) => (r.percent || 0) >= 100).length;
  return (
    <>
      <div className="inv-lin-head">
        <div className="inv-lin-title"><span className="icon-chip blue"><Icon name="noun" /></span>
          <div><h3>{data.noun_type || "Record"}</h3><span className="inv-lin-id">{data.display_id || ""}</span></div></div>
        <div className="inv-lin-rollup"><span className="count-pill">{runs.length} run{runs.length === 1 ? "" : "s"}</span>{runs.length ? <span className="chip ok">{doneRuns} complete</span> : null}</div>
      </div>

      <Section title="Referenced runs" icon="runlog">
        <div className="inv-runs">
          {!runs.length ? <p className="muted">No runs reference this record.</p> : runs.map((run, i) => <RunCard key={i} project={project} run={run} />)}
        </div>
      </Section>

      {(data.parents || []).length ? (
        <Section title="Parents & siblings" icon="investigation">
          <div className="inv-parents">
            {data.parents.map((p, i) => (
              <div className="inv-parent" key={i}>
                <div className="inv-parent-head">
                  <EntityChip kind={p.noun_type} id={p.noun_id} label={p.noun_id} href={nounHref(project, p.noun_type, p.noun_id)} />
                  <span className="muted">{p.action_requirement ? `requires: ${p.action_requirement}` : ""}</span>
                </div>
                {(p.siblings || []).length ? <EntityList>{p.siblings.map((s, j) => <RefChip key={j} project={project} n={s} />)}</EntityList> : null}
              </div>
            ))}
          </div>
        </Section>
      ) : null}

      {(data.retests || []).length ? (
        <Section title="Retests via overrides" icon="refresh">
          <EntityList>
            {data.retests.map((rt, i) => { const n = rt.noun_instance || {}; const type = n._noun_type || "Override"; const pk = n._primary_id_field || "run"; const val = n[pk] || "";
              return <EntityChip key={i} kind={type} id={val} label={`${val} — retest of ${rt.retest_of || "?"}`} href={nounHref(project, type, val)} />; })}
          </EntityList>
        </Section>
      ) : null}
    </>
  );
}

function Investigation() {
  const [projects, setProjects] = useState(null);
  const [project, setProject] = useState("");
  const [nouns, setNouns] = useState(null);
  const [noun, setNoun] = useState("");
  const [grid, setGrid] = useState({ status: "idle", columns: [], rows: [], primary: null });
  const [filter, setFilter] = useState("");
  const [filterDebounced, setFilterDebounced] = useState("");
  const [output, setOutput] = useState({ status: "empty" }); // empty|loading|ok|error
  const [selectedKey, setSelectedKey] = useState(null);
  const deepLink = useRef(new URLSearchParams(location.search));

  useEffect(() => { const t = setTimeout(() => setFilterDebounced(filter.trim().toLowerCase()), 160); return () => clearTimeout(t); }, [filter]);

  // boot: load projects (+ deep-link project)
  useEffect(() => {
    fetchJSON(API.projects).then((ps) => {
      const list = Array.isArray(ps) ? ps : [];
      setProjects(list);
      const dlP = deepLink.current.get("project");
      const p = (dlP && list.includes(dlP)) ? dlP : (list[0] || "");
      setProject(p);
    }).catch(() => { setProjects([]); toast("Failed to load projects", "err"); });
  }, []);

  // project → nouns (+ deep-link noun)
  useEffect(() => {
    if (!project) return;
    setNoun(""); setNouns(null); setGrid({ status: "idle", columns: [], rows: [], primary: null }); setOutput({ status: "empty" }); setSelectedKey(null);
    fetchJSON(API.nouns(project)).then((ns) => {
      const list = Array.isArray(ns) ? ns : [];
      setNouns(list);
      const dlN = deepLink.current.get("noun");
      if (dlN && list.includes(dlN)) setNoun(dlN);
    }).catch(() => setNouns([]));
  }, [project]);

  // noun → records
  const loadRecords = async () => {
    if (!project || !noun) return;
    setGrid((g) => ({ ...g, status: "loading" }));
    try {
      const items = await fetchJSON(API.items(project, noun));
      const tableData = await post(API.table(project, noun), { records: items });
      const primary = tableData.primary_id_field;
      const columns = (tableData.columns || []).map((c) => ({ key: c, label: c, type: "str", render: c === primary ? (v) => <span className="inv-pid">{v == null ? "" : String(v)}</span> : undefined }));
      const rows = (tableData.rows || []).map((row) => { const o = {}; (tableData.columns || []).forEach((c, i) => { o[c] = row[i]; }); return o; });
      setGrid({ status: "ok", columns, rows, primary });
      // deep-link record selection
      const dlId = deepLink.current.get("id");
      if (dlId) {
        const row = rows.find((r) => String(primary ? r[primary] : "") === String(dlId));
        deepLink.current.delete("id");
        if (row) { setSelectedKey(primary ? row[primary] : dlId); loadLineage(row); }
      }
    } catch (e) { setGrid((g) => ({ ...g, status: "error", error: String(e.message || e) })); }
  };
  useEffect(() => { loadRecords(); }, [project, noun]); // eslint-disable-line react-hooks/exhaustive-deps

  const loadLineage = async (record) => {
    setOutput({ status: "loading" });
    try { const data = await post(API.lineageUI(project, noun), { record }); setOutput({ status: "ok", data }); }
    catch (e) { setOutput({ status: "error", error: String(e.message || e) }); }
  };

  const filteredRows = useMemo(() => (filterDebounced ? grid.rows.filter((r) => JSON.stringify(r).toLowerCase().includes(filterDebounced)) : grid.rows), [grid.rows, filterDebounced]);
  const getKey = (r) => (grid.primary ? r[grid.primary] : JSON.stringify(r));

  return (
    <>
      <section className="panel controls-panel">
        <div className="panel-body inv-controls">
          <label className="field control-group"><span className="field-label">Project</span>
            <select id="project" className="input select" value={project} onChange={(e) => setProject(e.target.value)}>
              {projects == null ? <option value="">Loading…</option> : !projects.length ? <option value="">No projects</option> : projects.map((p) => <option key={p} value={p}>{p}</option>)}
            </select></label>
          <label className="field control-group"><span className="field-label">Noun Type</span>
            <select id="noun" className="input select" value={noun} onChange={(e) => { setNoun(e.target.value); setSelectedKey(null); setOutput({ status: "empty" }); }}>
              {nouns == null ? <option value="">Loading…</option> : <><option value="" disabled>Select a noun type</option>{nouns.map((n) => <option key={n} value={n}>{n}</option>)}</>}
            </select></label>
          <label className="field control-group inv-filter"><span className="field-label">Filter records</span>
            <input id="filter" className="input" type="search" placeholder="Filter by any text…" value={filter} onChange={(e) => setFilter(e.target.value)} /></label>
          <button id="refresh-records" className="btn ghost inv-refresh" type="button" title="Reload records" onClick={loadRecords}><Icon name="refresh" /><span>Refresh</span></button>
        </div>
      </section>

      <div className="main-container">
        <div className="panel records-panel">
          <div className="panel-head"><Icon name="grid" /><span className="panel-title">Records</span>
            {grid.status === "ok" ? <span className="count-pill" id="record-count">{filteredRows.length}</span> : null}</div>
          <div className="records-container" id="record-grid">
            {grid.status === "loading" ? <StateBlock kind="loading" title="Loading records…" />
              : grid.status === "error" ? <StateBlock kind="error" title="Could not load records" message={grid.error} />
              : grid.status === "ok" ? (
                <GridTable columns={grid.columns} rows={filteredRows} getKey={getKey} selectedKey={selectedKey}
                           onSelect={(row, key) => { setSelectedKey(key); loadLineage(row); }} maxHeight="68vh"
                           empty={{ icon: "grid", title: filterDebounced ? "No records match the filter" : "No records", message: filterDebounced ? "Try a different term." : "" }} />
              ) : <StateBlock kind="empty" icon="investigation" title="Pick a noun type, then a record" />}
          </div>
        </div>

        <div className="panel lineage-panel">
          <div className="panel-head"><Icon name="investigation" /><span className="panel-title">Lineage</span></div>
          <div id="output" className="lineage-content">
            {output.status === "empty" ? <StateBlock kind="empty" icon="investigation" title="Select a record to see its lineage" />
              : output.status === "loading" ? <StateBlock kind="loading" title="Tracing lineage…" />
              : output.status === "error" ? <StateBlock kind="error" title="Could not trace lineage" message={output.error} />
              : output.status === "ok" ? <Lineage project={project} data={output.data} /> : null}
          </div>
        </div>
      </div>
    </>
  );
}

mountOnAuth("investigation-root", (host) => createRoot(host).render(<Investigation />));
