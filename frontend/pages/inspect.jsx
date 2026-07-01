// frontend/pages/inspect.jsx — Instance Inspector (Phase 6: first React page).
// Faithful port of the vanilla inspect.js: a read-only viewer for a single noun or run,
// opened via deep-links. Emits the same Watery classes, so the existing Playwright harness
// (inspectshot.py) validates it unchanged.
import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { Icon, EntityChip, EntityList, SpecList } from "../lib/ui.jsx";
import { enc, fetchJSON, fmt, kvItems, queryParams, mountOnAuth } from "../lib/api.js";

function Card({ title, icon, children }) {
  return (
    <section className="insp-card">
      <div className="insp-card-head">
        {icon ? <span className="insp-card-ico"><Icon name={icon} /></span> : null}
        <span className="insp-card-title">{title}</span>
      </div>
      {children}
    </section>
  );
}

function ActionLink({ label, icon, href }) {
  return (
    <a className="btn ghost insp-action" href={href}>
      <span className="insp-action-ico"><Icon name={icon} /></span>
      <span>{label}</span>
    </a>
  );
}

function Header({ kind, title, idText, group, actions }) {
  return (
    <div className="insp-head">
      <div className="insp-head-id">
        <span className="icon-chip blue"><Icon name={kind === "Run" ? "runlog" : "noun"} /></span>
        <div>
          <div className="insp-head-titlerow">
            <span className={"insp-kind kind-" + kind.toLowerCase()}>{kind}</span>
            <h2 className="insp-head-title">{title || idText}</h2>
          </div>
          <div className="insp-head-sub">
            <span className="insp-head-idval">{idText}</span>
            {group ? <span className="chip">{group}</span> : null}
          </div>
        </div>
      </div>
      {actions && actions.length ? <div className="insp-head-actions">{actions}</div> : null}
    </div>
  );
}

function StateMsg({ kind, title, message }) {
  return (
    <div className={"gims-state is-" + kind}>
      <span className="gims-state-mark icon-chip round"><Icon name="investigation" /></span>
      <h3 className="gims-state-title">{title}</h3>
      {message ? <p className="gims-state-msg">{message}</p> : null}
    </div>
  );
}

function runChip(project, run) {
  return (
    <EntityChip
      key={run.run_id}
      kind="Run"
      id={run.run_id}
      label={`${run.verb || run.run_id}`}
      href={`/inspect?project=${enc(project)}&kind=run&group=${enc(run.verb_group || "")}&run_id=${enc(run.run_id || "")}&verb=${enc(run.verb || "")}`}
      title={`${run.verb || ""} · ${run.run_id} · ${run.percent != null ? run.percent + "%" : ""}`}
    />
  );
}

// ── noun ──────────────────────────────────────────────────────────────────────
function NounView({ project, type, id }) {
  const [s, setS] = useState({ status: "loading" });
  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const [schemas, items] = await Promise.all([
          fetchJSON(`/project/${enc(project)}/noun_types`).catch(() => ({})),
          fetchJSON(`/project/${enc(project)}/noun/${enc(type)}/items`),
        ]);
        const schema = schemas[type] || {};
        const pk = schema.primary_id_field || (type.toLowerCase() + "_id");
        const item = items.find((it) => String(it[pk]) === String(id)) || items.find((it) => String(it.id == null ? "" : it.id) === String(id));
        if (!item) { if (live) setS({ status: "error", message: `No ${type} found with ${pk} = ${id}.` }); return; }
        let runs = [];
        try {
          const lin = await fetchJSON(`/investigation/lineage_ui/${enc(project)}/${enc(type)}`, {
            method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ record: item }),
          });
          runs = (lin && lin.runs) || [];
        } catch (e) { /* best-effort */ }
        if (live) setS({ status: "ok", item, schema, pk, runs });
      } catch (e) { if (live) setS({ status: "error", message: String(e.message || e) }); }
    })();
    return () => { live = false; };
  }, [project, type, id]);

  if (s.status === "loading") return <StateMsg kind="loading" title="Loading instance…" />;
  if (s.status === "error") return <StateMsg kind="error" title="Could not load" message={s.message} />;

  const { item, schema, pk, runs } = s;
  const owningId = item._runID != null ? item._runID : (item.runID != null ? item.runID : item._run_id);
  const owningRun = runs.find((r) => String(r.run_id) === String(owningId)) || (runs.length === 1 ? runs[0] : null);

  const actions = [];
  if (owningRun && owningRun.verb_group) {
    actions.push(<ActionLink key="rl" label="Open in Runlog Workbench" icon="runlog"
      href={`/runlog_workbench?project=${enc(project)}&group=${enc(owningRun.verb_group)}&run_id=${enc(owningRun.run_id)}`} />);
  }
  actions.push(<ActionLink key="tl" label="Trace lineage" icon="investigation"
    href={`/investigation?project=${enc(project)}&noun=${enc(type)}&id=${enc(id)}`} />);

  const fields = schema.fields || {};
  const names = Object.keys(fields).length ? Object.keys(fields) : Object.keys(item).filter((k) => !k.startsWith("_"));
  const specs = names.map((name) => {
    const meta = fields[name] || {};
    const raw = item[name];
    const isRef = meta.type === "adjective" && /Reference/i.test(meta.adjective_class || "");
    return { label: name, value: fmt(raw), tone: isRef ? "info" : (meta.required && (raw == null || raw === "") ? "warn" : undefined) };
  });
  const metaItems = Object.keys(item).filter((k) => k.startsWith("_")).map((k) => ({ label: k.replace(/^_/, ""), value: fmt(item[k]), tone: "mono" }));

  return (
    <>
      <Header kind="Noun" title={type} idText={String(item[pk] == null ? id : item[pk])} actions={actions} />
      <Card title="Fields" icon="noun"><SpecList items={specs} /></Card>
      {metaItems.length ? <Card title="Metadata" icon="info"><SpecList items={metaItems} compact /></Card> : null}
      {runs.length ? (
        <Card title={`Referenced by ${runs.length} run${runs.length === 1 ? "" : "s"}`} icon="runlog">
          <EntityList>{runs.map((r) => runChip(project, r))}</EntityList>
        </Card>
      ) : null}
    </>
  );
}

// ── run ───────────────────────────────────────────────────────────────────────
function RunView({ project, group, runId, verb }) {
  const [s, setS] = useState({ status: "loading" });
  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const bundle = await fetchJSON(`/project/${enc(project)}/verb/${enc(group)}/run/${enc(runId)}/bundle`);
        if (live) setS({ status: "ok", bundle });
      } catch (e) { if (live) setS({ status: "error", message: String(e.message || e) }); }
    })();
    return () => { live = false; };
  }, [project, group, runId]);

  if (s.status === "loading") return <StateMsg kind="loading" title="Loading run…" />;
  if (s.status === "error") return <StateMsg kind="error" title="Could not load" message={s.message} />;

  const b = s.bundle || {};
  const status = b.status || {}, de = b.data_entry || {}, outs = b.outputs || [], ins = b.inputs || {};
  const inKeys = Object.keys(ins);
  const empty = !Object.keys(status).length && !Object.keys(de).length && !outs.length && !inKeys.length;

  return (
    <>
      <Header kind="Run" title={verb || runId} idText={runId} group={group} actions={[
        <ActionLink key="rl" label="Open in Runlog Workbench" icon="runlog"
          href={`/runlog_workbench?project=${enc(project)}&group=${enc(group)}&run_id=${enc(runId)}`} />,
      ]} />
      {Object.keys(status).length ? <Card title="Status" icon="check"><SpecList items={kvItems(status)} /></Card> : null}
      {Object.keys(de).length ? <Card title="Data entry" icon="noun"><SpecList items={kvItems(de)} /></Card> : null}
      {outs.length ? (
        <Card title="Output files" icon="download">
          <EntityList>
            {outs.map((f) => (
              <a className="insp-file" key={f} title={f}
                 href={`/project/${enc(project)}/verb/${enc(group)}/run/${enc(runId)}/output/${enc(f)}`}>
                <span className="insp-file-ico"><Icon name="download" /></span>
                <span className="insp-file-name">{f}</span>
              </a>
            ))}
          </EntityList>
        </Card>
      ) : null}
      {inKeys.length ? <Card title="Inputs" icon="file"><SpecList items={inKeys.map((k) => ({ label: k, value: (ins[k] || []).join(", ") || "—" }))} compact /></Card> : null}
      {empty ? <Card title="No data"><p className="muted">This run has no recorded data entry, status, or outputs yet.</p></Card> : null}
    </>
  );
}

function App() {
  const p = queryParams();
  const project = p.get("project"), kind = (p.get("kind") || "").toLowerCase();
  if (!project || !kind) return <StateMsg kind="empty" title="Nothing to inspect" message="Open the inspector from a record link — e.g. click an entity chip on the Investigation page." />;
  if (kind === "noun") return <NounView project={project} type={p.get("type")} id={p.get("id")} />;
  if (kind === "run") return <RunView project={project} group={p.get("group")} runId={p.get("run_id")} verb={p.get("verb")} />;
  return <StateMsg kind="error" title="Unknown inspect kind" message={kind} />;
}

mountOnAuth("inspect-root", (host) => createRoot(host).render(<App />));
