// frontend/pages/adjective_editor.jsx — Adjective Editor (Phase 6 React; editors track E2).
// React port of the 5.2b master/detail redesign: left = project/noun pickers + searchable
// class-badged adjective list + register-new; right = the selected adjective's per-class component
// editor (ActionRequirement→MatrixEditor, ReferenceList→TransferList+filters, Reference, Tag,
// Picture, Generic). The /adjective/update payload is built byte-identically to the vanilla via a
// per-class registerPayload(base) closure (NOT DOM-scraping). Endpoints unchanged.
import { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { Icon, MatrixEditor, StateBlock, TransferList } from "../lib/ui.jsx";
import { enc, fetchJSON, mountOnAuth, toast } from "../lib/api.js";

const serializeVal = (v) => (v && typeof v === "object") ? JSON.stringify(v) : (v == null ? "" : String(v));

function FieldCard({ title, hint, children }) {
  return (
    <section className="ae-card">
      <div className="ae-card-head">
        <span className="ae-card-title">{title}</span>
        {hint ? <span className="ae-card-hint">{hint}</span> : null}
      </div>
      {children}
    </section>
  );
}

// ── shared filter-rule editor (noun? · field · value) ──────────────────────────────────────────
// mode "list": each rule has its own noun (from getNouns()); "single": a fixed noun (getNoun()).
function FilterRules({ mode, filters, onChange, nounTypes, getNouns, getNoun }) {
  const fieldsOf = (noun) => Object.keys((nounTypes[noun] || {}).fields || {});
  const set = (i, patch) => onChange(filters.map((f, j) => (j === i ? { ...f, ...patch } : f)));
  const del = (i) => onChange(filters.filter((_, j) => j !== i));
  const add = () => {
    if (mode === "list") { const nouns = getNouns() || []; const n = nouns[0] || ""; onChange([...filters, { noun: n, attr: fieldsOf(n)[0] || "", value: "" }]); }
    else { const n = getNoun(); onChange([...filters, { attr: fieldsOf(n)[0] || "", value: "" }]); }
  };
  return (
    <div className="ae-rules">
      {filters.map((f, i) => {
        const attrNoun = mode === "list" ? f.noun : getNoun();
        const fields = fieldsOf(attrNoun);
        return (
          <div className="ae-rule" key={i}>
            {mode === "list" ? (
              <select className="input select ae-rule-f" value={f.noun || ""} onChange={(e) => set(i, { noun: e.target.value, attr: fieldsOf(e.target.value)[0] || "" })}>
                {(getNouns() || []).map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
            ) : null}
            <select className="input select ae-rule-f" value={fields.includes(f.attr) ? f.attr : (fields[0] || "")} onChange={(e) => set(i, { attr: e.target.value })}>
              {fields.map((fld) => <option key={fld} value={fld}>{fld}</option>)}
            </select>
            <input className="input ae-rule-f wide" value={f.value} placeholder="Value (JSON ok)" onChange={(e) => set(i, { value: e.target.value })} />
            <button className="ae-rule-del" type="button" title="Remove filter" onClick={() => del(i)}><Icon name="trash" /></button>
          </div>
        );
      })}
      <button className="btn ghost sm" type="button" onClick={add}><span className="ae-btn-ico"><Icon name="plus" /></span><span>Add filter</span></button>
    </div>
  );
}

function buildFilters(filters, nounForRule) {
  const out = {};
  filters.forEach((f) => {
    const noun = nounForRule(f);
    if (!noun || !f.attr) return;
    let v = f.value; try { v = JSON.parse(v); } catch { /* keep string */ }
    (out[noun] = out[noun] || {})[f.attr] = v;
  });
  return out;
}

// ── per-class editors: each registers a readPayload(base) closure via registerPayload ───────────
function ActionRequirementEditor({ config, verbTypes, registerPayload }) {
  const cols = useMemo(() => Object.entries(verbTypes || {}).map(([k, v]) => ({ key: k, label: k, group: (v && v.verb_group) || "Tests" })), [verbTypes]);
  const reqOpts = config.request_options || {};
  const valRef = useRef(reqOpts);
  useEffect(() => { registerPayload((base) => ({ ...base, request_options: valRef.current })); }, []); // eslint-disable-line
  return (
    <FieldCard title="Request labels → verbs" hint="Each request label maps to the verbs (tests) it triggers. Search labels, collapse verb groups, click a column header to toggle it for every label.">
      <MatrixEditor rows={Object.keys(reqOpts)} cols={cols} value={reqOpts} editableRows search
        rowHeader="Request label" addLabel="Add request label" addPlaceholder="New request label…" rowPlaceholder="Label…"
        onChange={(v) => { valRef.current = v; }} />
    </FieldCard>
  );
}

function ReferenceListEditor({ config, nounTypes, registerPayload }) {
  const options = useMemo(() => Object.keys(nounTypes).map((n) => ({ value: n, label: n })), [nounTypes]);
  const initNouns = Array.isArray(config.reference_noun) ? [...config.reference_noun] : (config.reference_noun ? [config.reference_noun] : []);
  const [nouns, setNouns] = useState(initNouns);
  const initFilters = [];
  Object.entries(config.filters || {}).forEach(([noun, m]) => Object.entries(m || {}).forEach(([attr, value]) => initFilters.push({ noun, attr, value: serializeVal(value) })));
  const [filters, setFilters] = useState(initFilters);
  const nounsRef = useRef(nouns); nounsRef.current = nouns;
  const filtersRef = useRef(filters); filtersRef.current = filters;
  useEffect(() => {
    registerPayload((base) => ({
      adjective: base.adjective, adjective_class: base.adjective_class, applies_to: base.applies_to,
      reference_noun: nounsRef.current,
      filters: buildFilters(filtersRef.current, (f) => f.noun),
    }));
  }, []); // eslint-disable-line
  return (
    <>
      <FieldCard title="Reference nouns" hint="The noun types this adjective can reference. Search, then move types into the right pane.">
        <TransferList options={options} value={nouns} titles={{ available: "All noun types", selected: "Reference nouns" }} onChange={setNouns} />
      </FieldCard>
      <FieldCard title="Filters" hint="Optional constraints on referenced records (noun · field · value).">
        <FilterRules mode="list" filters={filters} onChange={setFilters} nounTypes={nounTypes} getNouns={() => nounsRef.current} />
      </FieldCard>
    </>
  );
}

function ReferenceEditor({ config, nounTypes, registerPayload }) {
  const nounNames = Object.keys(nounTypes);
  const [noun, setNoun] = useState(config.reference_noun || nounNames[0] || "");
  const firstMap = (config.filters && config.filters[config.reference_noun]) || (config.filters ? Object.values(config.filters)[0] : null);
  const initFilters = Object.entries(firstMap || {}).map(([attr, value]) => ({ attr, value: serializeVal(value) }));
  const [filters, setFilters] = useState(initFilters);
  const nounRef = useRef(noun); nounRef.current = noun;
  const filtersRef = useRef(filters); filtersRef.current = filters;
  useEffect(() => {
    registerPayload((base) => ({
      adjective: base.adjective, adjective_class: base.adjective_class, applies_to: base.applies_to,
      reference_noun: nounRef.current,
      filters: buildFilters(filtersRef.current, () => nounRef.current),
    }));
  }, []); // eslint-disable-line
  return (
    <>
      <FieldCard title="Reference noun" hint="The single noun type this adjective references.">
        <div className="ae-row">
          <span className="ae-row-label">Reference noun</span>
          <select className="input select" value={noun} onChange={(e) => setNoun(e.target.value)}>
            {nounNames.map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </div>
      </FieldCard>
      <FieldCard title="Filters" hint="Optional constraints on the referenced records (field · value).">
        <FilterRules mode="single" filters={filters} onChange={setFilters} nounTypes={nounTypes} getNoun={() => nounRef.current} />
      </FieldCard>
    </>
  );
}

function TagEditor({ config, registerPayload }) {
  const [definition, setDefinition] = useState(config.definition || "");
  const [options, setOptions] = useState((config.valid_options || []).map((o) => ({ value: o.value || "", explanation: o.explanation || "", display_in_id: !!o.display_in_id })));
  const defRef = useRef(definition); defRef.current = definition;
  const optRef = useRef(options); optRef.current = options;
  useEffect(() => {
    registerPayload((base) => ({
      adjective: base.adjective, adjective_class: base.adjective_class, applies_to: base.applies_to,
      definition: defRef.current.trim(),
      valid_options: optRef.current.filter((o) => String(o.value).trim()).map((o) => ({ value: String(o.value).trim(), explanation: String(o.explanation || "").trim(), display_in_id: !!o.display_in_id })),
    }));
  }, []); // eslint-disable-line
  const set = (i, patch) => setOptions((os) => os.map((o, j) => (j === i ? { ...o, ...patch } : o)));
  return (
    <>
      <FieldCard title="Definition" hint="A short description of the tag.">
        <div className="ae-row">
          <span className="ae-row-label">Definition</span>
          <input className="input" value={definition} placeholder="What this tag means…" onChange={(e) => setDefinition(e.target.value)} />
        </div>
      </FieldCard>
      <FieldCard title="Valid options" hint="The allowed tag values; tick “Show in ID” to include a value in generated IDs.">
        <div className="ae-rules">
          {options.map((o, i) => (
            <div className="ae-rule ae-tag-rule" key={i}>
              <input className="input ae-rule-f" value={o.value} placeholder="Value" onChange={(e) => set(i, { value: e.target.value })} />
              <input className="input ae-rule-f wide" value={o.explanation} placeholder="Explanation (tooltip)" onChange={(e) => set(i, { explanation: e.target.value })} />
              <label className="ae-rule-chk"><input type="checkbox" checked={o.display_in_id} onChange={(e) => set(i, { display_in_id: e.target.checked })} /><span>Show in ID</span></label>
              <button className="ae-rule-del" type="button" title="Remove" onClick={() => setOptions((os) => os.filter((_, j) => j !== i))}><Icon name="trash" /></button>
            </div>
          ))}
          <button className="btn ghost sm" type="button" onClick={() => setOptions((os) => [...os, { value: "", explanation: "", display_in_id: false }])}>
            <span className="ae-btn-ico"><Icon name="plus" /></span><span>Add option</span>
          </button>
        </div>
      </FieldCard>
    </>
  );
}

function PictureEditor({ registerPayload }) {
  useEffect(() => { registerPayload((base) => ({ adjective: base.adjective, adjective_class: base.adjective_class, applies_to: base.applies_to, [base.adjective]: "" })); }, []); // eslint-disable-line
  return <FieldCard title="Picture adjective"><p className="muted ae-noconfig">No configuration required for a Picture adjective.</p></FieldCard>;
}

function DurationEditor({ config, noun, nounTypes, registerPayload }) {
  // Bind two of THIS noun's date/datetime fields (sibling fields) — the live interval anchors.
  const fields = (nounTypes[noun] || {}).fields || {};
  const dateFields = Object.keys(fields).filter((k) => { const t = (fields[k] || {}).type; return t === "date" || t === "datetime"; });
  const candidates = dateFields.length ? dateFields : Object.keys(fields).filter((k) => (fields[k] || {}).type !== "adjective");
  const [startField, setStartField] = useState(config.start_field || candidates[0] || "");
  const [endField, setEndField] = useState(config.end_field || candidates[1] || "");
  const [mode, setMode] = useState(config.mode || "both");
  const [unit, setUnit] = useState(config.unit || "auto");
  const r = useRef({}); r.current = { startField, endField, mode, unit };
  useEffect(() => {
    registerPayload((base) => ({
      adjective: base.adjective, adjective_class: base.adjective_class, applies_to: base.applies_to,
      start_field: r.current.startField || null,
      end_field: r.current.endField || null,
      mode: r.current.mode, unit: r.current.unit,
      overdue_style: base.overdue_style || "negative",
    }));
  }, []); // eslint-disable-line
  const opt = (k) => <option key={k} value={k}>{k}</option>;
  return (
    <>
      <FieldCard title="Anchors" hint="Two date/datetime fields of this noun. “Start” drives “how long has it been here?” (elapsed); “End” drives “how long until it’s due?” (remaining). Leave one blank for a one-sided clock.">
        <div className="ae-row">
          <span className="ae-row-label">Start field (brought-in)</span>
          <select className="input select" value={startField} onChange={(e) => setStartField(e.target.value)}>
            <option value="">— none —</option>{candidates.map(opt)}
          </select>
        </div>
        <div className="ae-row">
          <span className="ae-row-label">End field (due)</span>
          <select className="input select" value={endField} onChange={(e) => setEndField(e.target.value)}>
            <option value="">— none —</option>{candidates.map(opt)}
          </select>
        </div>
      </FieldCard>
      <FieldCard title="Display" hint="What the live ticker shows, and the coarsest unit. The clock is anchored to the server time; an overdue (past-due) reading is shown but does not raise an alert by itself.">
        <div className="ae-row">
          <span className="ae-row-label">Mode</span>
          <select className="input select" value={mode} onChange={(e) => setMode(e.target.value)}>
            <option value="elapsed">elapsed — time since start</option>
            <option value="remaining">remaining — time until end</option>
            <option value="both">both — elapsed · remaining</option>
          </select>
        </div>
        <div className="ae-row">
          <span className="ae-row-label">Unit</span>
          <select className="input select" value={unit} onChange={(e) => setUnit(e.target.value)}>
            {["auto", "days", "hours", "minutes", "seconds"].map(opt)}
          </select>
        </div>
      </FieldCard>
    </>
  );
}

function GenericEditor({ config, registerPayload }) {
  const [rows, setRows] = useState(Object.entries(config || {}).map(([key, value]) => ({ key, value: value == null ? "" : String(value) })));
  const rowsRef = useRef(rows); rowsRef.current = rows;
  useEffect(() => { registerPayload(async () => { const d = {}; rowsRef.current.forEach(({ key, value }) => { d[key] = value; }); return d; }); }, []); // eslint-disable-line
  return (
    <FieldCard title="Configuration">
      <div className="ae-rules">
        {rows.map((r, i) => (
          <div className="ae-rule" key={r.key}>
            <span className="ae-rule-key" title={r.key}>{r.key}</span>
            <input className="input ae-rule-f wide" value={r.value} onChange={(e) => setRows((rs) => rs.map((x, j) => (j === i ? { ...x, value: e.target.value } : x)))} />
          </div>
        ))}
      </div>
    </FieldCard>
  );
}

const CLASS_EDITORS = {
  ActionRequirement: ActionRequirementEditor,
  ReferenceList: ReferenceListEditor,
  Reference: ReferenceEditor,
  Tag: TagEditor,
  Picture: PictureEditor,
  Duration: DurationEditor,
};

// ── right: detail panel ─────────────────────────────────────────────────────────────────────
function DetailPanel({ adj, project, noun, nounTypes, verbTypes, onSaved, onDemoted, reloadKey }) {
  const [state, setState] = useState({ status: "loading" });
  const payloadRef = useRef(null);
  const registerPayload = (fn) => { payloadRef.current = fn; };

  useEffect(() => {
    let live = true;
    setState({ status: "loading" });
    fetchJSON(`/adjective/options/${enc(project)}/${enc(noun)}/${enc(adj.adjective)}`)
      .then((config) => { if (live) setState({ status: "ok", config }); })
      .catch((e) => { if (live) setState({ status: "error", message: String(e.message || e) }); });
    return () => { live = false; };
  }, [project, noun, adj.adjective, reloadKey]);

  const save = async () => {
    if (!payloadRef.current) return;
    try {
      const base = await fetchJSON(`/adjective/configure/${enc(project)}/${enc(noun)}/${enc(adj.adjective)}`);
      const payload = await payloadRef.current(base);
      await fetchJSON(`/adjective/update/${enc(project)}/${enc(noun)}/${enc(adj.adjective)}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      });
      toast(`Saved ${adj.adjective}`, "ok");
      if (onSaved) onSaved();
    } catch (e) { toast(`Failed to save ${adj.adjective}: ${String(e.message || e)}`, "err"); }
  };

  const demote = async () => {
    if (!window.confirm(`Demote '${adj.adjective}' back to a plain attribute? This cannot be undone.`)) return;
    try {
      await fetchJSON(`/adjective/demote/${enc(project)}/${enc(noun)}/${enc(adj.adjective)}`, { method: "POST", headers: { "Content-Type": "application/json" } });
      toast(`Demoted '${adj.adjective}' back to attribute`, "ok");
      if (onDemoted) onDemoted();
    } catch (e) { toast(`Error demoting '${adj.adjective}': ${String(e.message || e)}`, "err"); }
  };

  if (state.status === "loading") return <StateBlock kind="loading" title="Loading configuration…" />;
  if (state.status === "error") return <StateBlock kind="error" title="Could not load" message={state.message} />;

  const Editor = CLASS_EDITORS[adj.adjective_class] || GenericEditor;
  return (
    <>
      <div className="ae-detail-head">
        <div className="ae-detail-id">
          <span className="icon-chip blue"><Icon name="adjective" /></span>
          <div><h2 className="ae-detail-name">{adj.adjective}</h2><span className="ae-detail-class">{adj.adjective_class}</span></div>
        </div>
        <div className="ae-detail-actions">
          <button className="btn-primary" type="button" onClick={save}><span className="ae-btn-ico"><Icon name="save" /></span><span>Save</span></button>
          <button className="btn ghost" type="button" onClick={() => setState((s) => ({ ...s }))}><span className="ae-btn-ico"><Icon name="refresh" /></span><span>Reset</span></button>
          <button className="btn red" type="button" onClick={demote}><span className="ae-btn-ico"><Icon name="trash" /></span><span>Demote</span></button>
        </div>
      </div>
      <div className="ae-detail-body">
        <Editor key={adj.adjective} config={state.config} noun={noun} nounTypes={nounTypes} verbTypes={verbTypes} registerPayload={registerPayload} />
      </div>
    </>
  );
}

// ── root ────────────────────────────────────────────────────────────────────────────────────
function AdjectiveEditor() {
  const [projects, setProjects] = useState(null);
  const [project, setProject] = useState("");
  const [nouns, setNouns] = useState([]);
  const [noun, setNoun] = useState("");
  const [data, setData] = useState({ adjectives: [], classes: [], nounTypes: {}, verbTypes: {} });
  const [selected, setSelected] = useState(null);
  const [filter, setFilter] = useState("");
  const [loadKey, setLoadKey] = useState(0);
  const [listState, setListState] = useState("idle");

  useEffect(() => {
    fetchJSON("/adjective/projects").then((ps) => {
      setProjects(ps || []);
      if (Array.isArray(ps) && ps.length) setProject(ps[0]);
    }).catch(() => { setProjects([]); toast("Failed to load projects", "err"); });
  }, []);

  useEffect(() => {
    if (!project) return;
    setNoun(""); setSelected(null); setData((d) => ({ ...d, adjectives: [] }));
    fetchJSON(`/adjective/nouns/${enc(project)}`).then((n) => setNouns(Object.keys(n || {})))
      .catch(() => { setNouns([]); toast("Failed to load nouns", "err"); });
  }, [project]);

  useEffect(() => {
    if (!project || !noun) return;
    setListState("loading");
    Promise.all([
      fetchJSON(`/adjective/list/${enc(project)}/${enc(noun)}`),
      fetchJSON("/adjective/classes"),
      fetchJSON(`/project/${enc(project)}/noun_types`),
      fetchJSON(`/project/${enc(project)}/verb_types`).catch(() => ({})),
    ]).then(([adjectives, classes, nounTypes, verbTypes]) => {
      setData({ adjectives: adjectives || [], classes: classes || [], nounTypes: nounTypes || {}, verbTypes: verbTypes || {} });
      setListState("ok");
      setSelected((sel) => (sel && (adjectives || []).some((a) => a.adjective === sel) ? sel : null));
    }).catch((e) => { setListState("error"); toast("Failed to load adjectives: " + (e.message || e), "err"); });
  }, [project, noun, loadKey]);

  const reload = () => setLoadKey((k) => k + 1);

  const q = filter.trim().toLowerCase();
  const items = q ? data.adjectives.filter((a) => `${a.adjective} ${a.adjective_class}`.toLowerCase().includes(q)) : data.adjectives;
  const existing = new Set(data.adjectives.map((a) => a.adjective));
  const registerFields = Object.keys((data.nounTypes[noun] || {}).fields || {}).filter((f) => !existing.has(f));
  const selectedAdj = data.adjectives.find((a) => a.adjective === selected) || null;

  const register = async (field, cls) => {
    try {
      await fetchJSON(`/adjective/promote/${enc(project)}/${enc(noun)}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ adjective: field, adjective_class: cls, applies_to: [noun] }),
      });
      toast(`Registered '${field}'`, "ok");
      setSelected(field); reload();
    } catch (e) { toast(`Error registering '${field}': ${String(e.message || e)}`, "err"); }
  };

  return (
    <>
      <section className="panel ae-toolbar">
        <div className="panel-body ae-toolbar-row">
          <label className="field ae-field"><span className="field-label">Project</span>
            <select id="project" className="input select" value={project} onChange={(e) => setProject(e.target.value)}>
              {projects == null ? <option>Loading…</option> : !projects.length ? <option value="">No projects</option>
                : projects.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </label>
          <label className="field ae-field"><span className="field-label">Noun type</span>
            <select id="noun" className="input select" value={noun} onChange={(e) => { setNoun(e.target.value); setSelected(null); }}>
              <option value="" disabled>Select a noun…</option>
              {nouns.map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
          <button id="refresh" className="btn ghost ae-refresh" type="button" title="Reload adjectives" onClick={reload}><Icon name="refresh" /><span>Refresh</span></button>
        </div>
      </section>

      <div className="main-container ae-main">
        <div className="panel ae-list-panel">
          <div className="panel-head">
            <Icon name="adjective" /><span className="panel-title">Adjectives</span>
            {noun ? <span className="count-pill" id="adj-count">{items.length}</span> : null}
          </div>
          <div className="ae-list-search">
            <Icon name="filter" className="ae-search-i" />
            <input id="adj-filter" className="input" type="search" placeholder="Search adjectives…" value={filter} onChange={(e) => setFilter(e.target.value)} />
          </div>
          <div id="adj-list" className="ae-list">
            {!noun ? <StateBlock kind="empty" icon="adjective" title="Pick a noun type" message="Pick a noun type to see its adjectives." />
              : listState === "loading" ? <StateBlock kind="loading" title="Loading adjectives…" />
                : !items.length ? <StateBlock kind="empty" icon="adjective" title={q ? "No matches" : "No adjectives yet"} message={q ? "Try a different term." : "Promote a field below to create one."} />
                  : items.map((a) => (
                      <button key={a.adjective} type="button" className={"ae-adj" + (a.adjective === selected ? " selected" : "")} onClick={() => setSelected(a.adjective)}>
                        <span className="ae-adj-name" title={a.adjective}>{a.adjective}</span>
                        <span className="ae-adj-class">{a.adjective_class}</span>
                      </button>
                    ))}
          </div>
          {registerFields.length ? (
            <div id="register" className="ae-register">
              <div className="ae-register-head"><span className="ae-register-icon"><Icon name="plus" /></span><span className="ae-register-title">Register new adjective</span></div>
              {registerFields.map((field) => <RegisterRow key={field} field={field} classes={data.classes} onRegister={register} />)}
            </div>
          ) : null}
        </div>

        <div className="panel ae-detail-panel">
          <div id="detail" className="ae-detail">
            {selectedAdj
              ? <DetailPanel adj={selectedAdj} project={project} noun={noun} nounTypes={data.nounTypes} verbTypes={data.verbTypes}
                             onSaved={reload} onDemoted={() => { setSelected(null); reload(); }} reloadKey={loadKey} />
              : <StateBlock kind="empty" icon="adjective" title="No adjective selected" message="Choose an adjective on the left to view and edit its configuration." />}
          </div>
        </div>
      </div>
    </>
  );
}

function RegisterRow({ field, classes, onRegister }) {
  const [cls, setCls] = useState(classes[0] || "");
  return (
    <div className="ae-register-row">
      <span className="ae-register-field" title={field}>{field}</span>
      <select className="input select ae-register-class" aria-label={`Class for ${field}`} value={cls} onChange={(e) => setCls(e.target.value)}>
        {classes.map((c) => <option key={c} value={c}>{c}</option>)}
      </select>
      <button className="btn blue sm" type="button" onClick={() => onRegister(field, cls)}><span className="ae-btn-ico"><Icon name="plus" /></span><span>Register</span></button>
    </div>
  );
}

mountOnAuth("adjective-root", (host) => createRoot(host).render(<AdjectiveEditor />));
