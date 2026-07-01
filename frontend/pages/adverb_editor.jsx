// frontend/pages/adverb_editor.jsx — Adverb Editor (Phase 6 React; editors track E3).
// The adverb editor is the descriptor TWIN of the adjective editor (backend: they share
// _descriptor_crud.py). The vanilla adverb page was an old table+modal-form (un-redesigned); this
// brings it up to the same master/detail UX as the adjective editor and reuses the shared lib
// components (TransferList) + adjective's ae-* master/detail CSS. The class editors are written
// fresh because the adverb shapes differ (verb scope; classes Tag/Reference/ReferenceList/Attribute/
// Picture; Tag uses display_in_label; Reference filters are flat {field:value}; ReferenceList uses
// reference_nouns[]; Attribute uses field_type+date format; base adds description/required). The
// promote/update/demote payloads are byte-identical to the vanilla collectFormData().
import { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { Icon, StateBlock, TransferList } from "../lib/ui.jsx";
import { enc, fetchJSON, mountOnAuth, toast } from "../lib/api.js";

const CLASSES = ["Tag", "Reference", "ReferenceList", "Attribute", "Picture"];
const FIELD_TYPES = ["string", "number", "date"];
const DATE_FORMATS = ["yyyy-mm-dd", "mmddyyyy"];

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

function blankFor(cls, nouns) {
  return {
    valid_options: [],
    reference_noun: nouns[0] || "",
    ref_filters: [],
    reference_nouns: [],
    field_type: "string",
    format: "yyyy-mm-dd",
  };
}

// seed editable state from an existing adverb config
function seedFrom(config, nouns) {
  const s = blankFor(config.adverb_class, nouns);
  s.valid_options = (config.valid_options || []).map((o) => ({ value: o.value || "", explanation: o.explanation || "", display_in_label: !!o.display_in_label }));
  if (config.reference_noun) s.reference_noun = config.reference_noun;
  s.ref_filters = Object.entries(config.filters || {}).map(([field, value]) => ({ field, value: value == null ? "" : String(value) }));
  s.reference_nouns = Array.isArray(config.reference_nouns) ? [...config.reference_nouns] : [];
  s.field_type = config.field_type || "string";
  s.format = config.format || "yyyy-mm-dd";
  return s;
}

function AdverbDetail({ project, verb, originalName, config, nouns, nounFields, onSaved, onDeleted }) {
  const isNew = !originalName;
  const [name, setName] = useState(originalName || "");
  const [description, setDescription] = useState(config.description || "");
  const [required, setRequired] = useState(!!config.required);
  const [cls, setCls] = useState(config.adverb_class || "Attribute");
  const [cfg, setCfg] = useState(() => seedFrom(config, nouns));

  const setC = (patch) => setCfg((c) => ({ ...c, ...patch }));
  const fieldsOf = (noun) => Object.keys((nounFields[noun] || {}));

  const buildPayload = () => {
    const p = { adverb: name.trim(), verb, adverb_class: cls, description, required };
    if (cls === "Tag") {
      p.valid_options = cfg.valid_options.filter((o) => String(o.value).trim())
        .map((o) => ({ value: String(o.value).trim(), explanation: o.explanation || "", display_in_label: !!o.display_in_label }));
    } else if (cls === "Reference") {
      p.reference_noun = cfg.reference_noun;
      const f = {}; cfg.ref_filters.forEach((r) => { if (r.field && r.value) f[r.field] = r.value; }); p.filters = f;
    } else if (cls === "ReferenceList") {
      p.reference_nouns = cfg.reference_nouns; p.filters = {};
    } else if (cls === "Attribute") {
      p.field_type = cfg.field_type; if (cfg.field_type === "date") p.format = cfg.format;
    }
    return p;
  };

  const save = async () => {
    if (!name.trim()) { toast("Adverb name is required.", "warn"); return; }
    const payload = buildPayload();
    const url = isNew
      ? `/adverb/promote/${enc(project)}/${enc(verb)}`
      : `/adverb/update/${enc(project)}/${enc(verb)}/${enc(originalName)}`;
    try {
      await fetchJSON(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      toast(`${isNew ? "Added" : "Updated"} adverb "${payload.adverb}"`, "ok");
      onSaved(payload.adverb);
    } catch (e) { toast(`Failed to save adverb: ${String(e.message || e)}`, "err"); }
  };

  const del = async () => {
    if (!window.confirm(`Delete the adverb "${originalName}"?`)) return;
    try {
      await fetchJSON(`/adverb/demote/${enc(project)}/${enc(verb)}/${enc(originalName)}`, { method: "POST", headers: { "Content-Type": "application/json" } });
      toast(`Removed adverb "${originalName}"`, "ok");
      onDeleted();
    } catch (e) { toast(`Failed to remove adverb: ${String(e.message || e)}`, "err"); }
  };

  return (
    <>
      <div className="ae-detail-head">
        <div className="ae-detail-id">
          <span className="icon-chip blue"><Icon name="adverb" /></span>
          <div><h2 className="ae-detail-name">{isNew ? "New adverb" : originalName}</h2><span className="ae-detail-class">{cls}</span></div>
        </div>
        <div className="ae-detail-actions">
          <button className="btn-primary" type="button" onClick={save}><span className="ae-btn-ico"><Icon name="save" /></span><span>Save</span></button>
          {!isNew ? <button className="btn red" type="button" onClick={del}><span className="ae-btn-ico"><Icon name="trash" /></span><span>Delete</span></button> : null}
        </div>
      </div>

      <div className="ae-detail-body">
        <FieldCard title="Adverb">
          <div className="ae-row"><span className="ae-row-label">Name</span>
            <input className="input" value={name} placeholder="Adverb name…" onChange={(e) => setName(e.target.value)} /></div>
          <div className="ae-row"><span className="ae-row-label">Description</span>
            <input className="input" value={description} placeholder="What this adverb captures…" onChange={(e) => setDescription(e.target.value)} /></div>
          <div className="ae-row"><span className="ae-row-label">Class</span>
            <select className="input select" value={cls} onChange={(e) => setCls(e.target.value)}>
              {CLASSES.map((c) => <option key={c} value={c}>{c}</option>)}
            </select></div>
          <label className="ae-rule-chk"><input type="checkbox" checked={required} onChange={(e) => setRequired(e.target.checked)} /><span>Required</span></label>
        </FieldCard>

        {cls === "Tag" ? (
          <FieldCard title="Valid options" hint="The allowed tag values; tick “Show in ID” to include a value in generated IDs.">
            <div className="ae-rules">
              {cfg.valid_options.map((o, i) => (
                <div className="ae-rule ae-tag-rule" key={i}>
                  <input className="input ae-rule-f" value={o.value} placeholder="Value" onChange={(e) => setC({ valid_options: cfg.valid_options.map((x, j) => j === i ? { ...x, value: e.target.value } : x) })} />
                  <input className="input ae-rule-f wide" value={o.explanation} placeholder="Explanation (optional)" onChange={(e) => setC({ valid_options: cfg.valid_options.map((x, j) => j === i ? { ...x, explanation: e.target.value } : x) })} />
                  <label className="ae-rule-chk"><input type="checkbox" checked={o.display_in_label} onChange={(e) => setC({ valid_options: cfg.valid_options.map((x, j) => j === i ? { ...x, display_in_label: e.target.checked } : x) })} /><span>Show in ID</span></label>
                  <button className="ae-rule-del" type="button" title="Remove" onClick={() => setC({ valid_options: cfg.valid_options.filter((_, j) => j !== i) })}><Icon name="trash" /></button>
                </div>
              ))}
              <button className="btn ghost sm" type="button" onClick={() => setC({ valid_options: [...cfg.valid_options, { value: "", explanation: "", display_in_label: false }] })}><span className="ae-btn-ico"><Icon name="plus" /></span><span>Add option</span></button>
            </div>
          </FieldCard>
        ) : null}

        {cls === "Reference" ? (
          <>
            <FieldCard title="Reference noun" hint="The single noun type this adverb references.">
              <div className="ae-row"><span className="ae-row-label">Reference noun</span>
                <select className="input select" value={cfg.reference_noun} onChange={(e) => setC({ reference_noun: e.target.value })}>
                  {nouns.map((n) => <option key={n} value={n}>{n}</option>)}
                </select></div>
            </FieldCard>
            <FieldCard title="Filters" hint="Optional constraints on the referenced records (field · value).">
              <div className="ae-rules">
                {cfg.ref_filters.map((r, i) => {
                  const fields = fieldsOf(cfg.reference_noun);
                  return (
                    <div className="ae-rule" key={i}>
                      <select className="input select ae-rule-f" value={fields.includes(r.field) ? r.field : (fields[0] || "")} onChange={(e) => setC({ ref_filters: cfg.ref_filters.map((x, j) => j === i ? { ...x, field: e.target.value } : x) })}>
                        {fields.map((f) => <option key={f} value={f}>{f}</option>)}
                      </select>
                      <input className="input ae-rule-f wide" value={r.value} placeholder="Required value" onChange={(e) => setC({ ref_filters: cfg.ref_filters.map((x, j) => j === i ? { ...x, value: e.target.value } : x) })} />
                      <button className="ae-rule-del" type="button" title="Remove filter" onClick={() => setC({ ref_filters: cfg.ref_filters.filter((_, j) => j !== i) })}><Icon name="trash" /></button>
                    </div>
                  );
                })}
                <button className="btn ghost sm" type="button" onClick={() => setC({ ref_filters: [...cfg.ref_filters, { field: fieldsOf(cfg.reference_noun)[0] || "", value: "" }] })}><span className="ae-btn-ico"><Icon name="plus" /></span><span>Add filter</span></button>
              </div>
            </FieldCard>
          </>
        ) : null}

        {cls === "ReferenceList" ? (
          <FieldCard title="Reference nouns" hint="The noun types this adverb can reference. Move types into the right pane.">
            <TransferList options={nouns.map((n) => ({ value: n, label: n }))} value={cfg.reference_nouns}
              titles={{ available: "All noun types", selected: "Reference nouns" }} onChange={(v) => setC({ reference_nouns: v })} />
          </FieldCard>
        ) : null}

        {cls === "Attribute" ? (
          <FieldCard title="Attribute type" hint="The data type captured by this adverb.">
            <div className="ae-row"><span className="ae-row-label">Field type</span>
              <select className="input select" value={cfg.field_type} onChange={(e) => setC({ field_type: e.target.value })}>
                {FIELD_TYPES.map((f) => <option key={f} value={f}>{f}</option>)}
              </select></div>
            {cfg.field_type === "date" ? (
              <div className="ae-row"><span className="ae-row-label">Date format</span>
                <select className="input select" value={cfg.format} onChange={(e) => setC({ format: e.target.value })}>
                  {DATE_FORMATS.map((f) => <option key={f} value={f}>{f}</option>)}
                </select></div>
            ) : null}
          </FieldCard>
        ) : null}

        {cls === "Picture" ? (
          <FieldCard title="Picture adverb"><p className="muted ae-noconfig">No configuration required for a Picture adverb.</p></FieldCard>
        ) : null}
      </div>
    </>
  );
}

function AdverbEditor() {
  const [projects, setProjects] = useState(null);
  const [project, setProject] = useState("");
  const [list, setList] = useState({});          // {verb: {adverb_schema}}
  const [verb, setVerb] = useState("");
  const [nouns, setNouns] = useState([]);
  const [nounFields, setNounFields] = useState({});
  const [selected, setSelected] = useState(null); // adverb name | "__new__" | null
  const [filter, setFilter] = useState("");
  const [loadKey, setLoadKey] = useState(0);

  useEffect(() => {
    fetchJSON("/adverb/projects").then((ps) => { setProjects(ps || []); if (Array.isArray(ps) && ps.length) setProject(ps[0]); })
      .catch(() => { setProjects([]); toast("Failed to load projects", "err"); });
  }, []);

  // Reset the verb/selection only when the PROJECT changes — not on a reload (loadKey bump),
  // so saving an adverb (which reloads the list) keeps the current verb + selection.
  useEffect(() => { setVerb(""); setSelected(null); }, [project]);

  useEffect(() => {
    if (!project) return;
    Promise.all([
      fetchJSON(`/adverb/list/${enc(project)}`).catch(() => ({})),
      fetchJSON(`/adverb/nouns/${enc(project)}`).catch(() => ({})),
    ]).then(([l, n]) => {
      setList(l || {});
      setNouns(Object.keys(n || {}));
      const nf = {}; Object.entries(n || {}).forEach(([noun, def]) => { nf[noun] = (def && def.fields) || {}; }); setNounFields(nf);
    });
  }, [project, loadKey]);

  const verbs = Object.keys(list);
  const adverbs = (list[verb] && list[verb].adverb_schema) || {};
  const reload = () => setLoadKey((k) => k + 1);

  const q = filter.trim().toLowerCase();
  const names = Object.keys(adverbs).filter((n) => !q || `${n} ${adverbs[n].adverb_class || ""}`.toLowerCase().includes(q));

  const detailFor = () => {
    if (selected === "__new__") return <AdverbDetail key="__new__" project={project} verb={verb} originalName="" config={{ adverb_class: "Attribute" }} nouns={nouns} nounFields={nounFields} onSaved={(nm) => { setSelected(nm); reload(); }} onDeleted={() => { setSelected(null); reload(); }} />;
    if (selected && adverbs[selected]) return <AdverbDetail key={selected} project={project} verb={verb} originalName={selected} config={adverbs[selected]} nouns={nouns} nounFields={nounFields} onSaved={(nm) => { setSelected(nm); reload(); }} onDeleted={() => { setSelected(null); reload(); }} />;
    return <StateBlock kind="empty" icon="adverb" title="No adverb selected" message={verb ? "Choose an adverb on the left, or add a new one." : "Pick a verb to see its adverbs."} />;
  };

  return (
    <>
      <section className="panel ae-toolbar">
        <div className="panel-body ae-toolbar-row">
          <label className="field ae-field"><span className="field-label">Project</span>
            <select className="input select" value={project} onChange={(e) => setProject(e.target.value)}>
              {projects == null ? <option>Loading…</option> : !projects.length ? <option value="">No projects</option> : projects.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </label>
          <label className="field ae-field"><span className="field-label">Verb</span>
            <select className="input select" value={verb} onChange={(e) => { setVerb(e.target.value); setSelected(null); }}>
              <option value="" disabled>Select a verb…</option>
              {verbs.map((v) => <option key={v} value={v}>{v}</option>)}
            </select>
          </label>
          <button className="btn ghost ae-refresh" type="button" title="Reload adverbs" onClick={reload}><Icon name="refresh" /><span>Refresh</span></button>
        </div>
      </section>

      <div className="main-container ae-main">
        <div className="panel ae-list-panel">
          <div className="panel-head">
            <Icon name="adverb" /><span className="panel-title">Adverbs</span>
            {verb ? <span className="count-pill">{names.length}</span> : null}
          </div>
          <div className="ae-list-search">
            <Icon name="filter" className="ae-search-i" />
            <input className="input" type="search" placeholder="Search adverbs…" value={filter} onChange={(e) => setFilter(e.target.value)} />
          </div>
          <div className="ae-list">
            {!verb ? <StateBlock kind="empty" icon="adverb" title="Pick a verb" message="Pick a verb to see its adverbs." />
              : !names.length ? <StateBlock kind="empty" icon="adverb" title={q ? "No matches" : "No adverbs yet"} message={q ? "Try a different term." : "Add one with the button below."} />
                : names.map((n) => (
                    <button key={n} type="button" className={"ae-adj" + (n === selected ? " selected" : "")} onClick={() => setSelected(n)}>
                      <span className="ae-adj-name" title={n}>{n}</span>
                      <span className="ae-adj-class">{adverbs[n].adverb_class || "Attribute"}</span>
                    </button>
                  ))}
          </div>
          {verb ? (
            <div className="ae-register">
              <button className="btn blue sm" type="button" onClick={() => setSelected("__new__")}><span className="ae-btn-ico"><Icon name="plus" /></span><span>Add adverb</span></button>
            </div>
          ) : null}
        </div>

        <div className="panel ae-detail-panel">
          <div className="ae-detail">{detailFor()}</div>
        </div>
      </div>
    </>
  );
}

mountOnAuth("adverb-root", (host) => createRoot(host).render(<AdverbEditor />));
