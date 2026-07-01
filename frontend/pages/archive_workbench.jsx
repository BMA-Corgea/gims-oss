// frontend/pages/archive_workbench.jsx — Archive Workbench (Phase 6 React; admin track A3).
// React port of the 1363-line vanilla archive_workbench.js: a per-project archive/restore console —
// an Archive Policy editor (defaults + dynamic noun/verb override cards with segmented strategy /
// on-reference, linked day/max sliders with Never/Unlimited→null, flags, noun exception chips), an
// "Archive by Policy" preview + apply, manual noun archive (searchable picker, un-archived only), noun
// restore (soft/hard split → up to two strategy-qualified POSTs), run archive + run restore pickers,
// and a console. Reuses archive_workbench.css (the .panel/.accordion/.override-card/.seg/.chip/.picker
// /.status/.console + #id contract is reproduced).
//
// Byte-identical mutation payloads (verified by archiveshot.py, real route, 0 console errors):
//   POST {API}/{p}/policy                          { default, nouns:{T:cfg}, verbs:{T:cfg} }
//   POST {API}/{p}/nouns/apply                     null (Archive Derived) | { "<noun>":[ids] } (manual)
//   POST {API}/{p}/nouns/restore/apply?strategy=soft|hard   { "<noun>":[ids] }  (per non-empty bucket)
//   POST {API}/{p}/runs/archive/apply              [ { verb_group, run_id } ]   (no strategy → backend hard)
//   POST {API}/{p}/runs/restore/apply              [ { verb_group, run_id } ]
// Policy cfg: noun = {strategy, archive_after_days|null, max_items|null, include_files, schema_versioning,
// on_reference, exceptions:[]}; verb omits on_reference/exceptions. Never/Unlimited toggles serialize null
// (not 0); include_files/schema_versioning default true (preset !== false). Preview POSTs the draft policy
// first, THEN GETs the preview (the vanilla side effect — preserved). RESTRUCTURE: the per-GET _ts
// cache-buster + the dead legacy noun-preview-checkbox flow are dropped (orchestrate forwards fresh).
import { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { Icon } from "../lib/ui.jsx";
import { enc, fetchJSON, mountOnAuth, toast } from "../lib/api.js";

const API = "/api/archive_workbench";
const FETCH_LIMIT = 5000;
const get = (url) => fetchJSON(url);
const post = (url, body) => fetchJSON(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const num = (v) => Number(v ?? 0);

// ── small controls ───────────────────────────────────────────────────────────────────────────────
function Segmented({ options, value, onChange }) {
  return (
    <div className="seg">
      {options.map((o) => (
        <button key={o} type="button" className={"seg-btn" + (value === o ? " active" : "")} onClick={() => onChange(o)}>{o}</button>
      ))}
    </div>
  );
}

// value = {value, isNull}; toggle disables range+number and serializes null
function NumNull({ label, toggleLabel, max, step, v, onChange }) {
  return (
    <div className="field">
      <div className="slider-head"><span className="label">{label}</span>
        <label className="toggle"><input type="checkbox" checked={v.isNull} onChange={(e) => onChange({ ...v, isNull: e.target.checked })} /> {toggleLabel}</label>
      </div>
      <input type="range" min="0" max={max} step={step} value={v.value} disabled={v.isNull} onChange={(e) => onChange({ ...v, value: e.target.value })} />
      <div className="slider-foot">
        <input type="number" min="0" max={max} step={step} className="text" value={v.value} disabled={v.isNull} onChange={(e) => onChange({ ...v, value: e.target.value })} />
        <span className="muted">0–{max}</span>
      </div>
    </div>
  );
}

function Chips({ values, onChange }) {
  const [draft, setDraft] = useState("");
  const add = () => { const t = draft.trim(); if (t && !values.includes(t)) onChange([...values, t]); setDraft(""); };
  return (
    <div className="chips">
      {values.map((c) => (
        <span className="chip" key={c}><span>{c}</span><button type="button" className="chip-x" onClick={() => onChange(values.filter((x) => x !== c))}>×</button></span>
      ))}
      <input className="input" value={draft} placeholder="add id, Enter" onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); add(); } }} />
    </div>
  );
}

// searchable checkbox picker; values=string[], selected=Set, onChange(Set)
function Picker({ values, selected, onChange, emptyText }) {
  const [q, setQ] = useState("");
  const vis = values.filter((v) => v.toLowerCase().includes(q.toLowerCase()));
  const setMany = (vs, on) => { const s = new Set(selected); vs.forEach((v) => { if (on) s.add(v); else s.delete(v); }); onChange(s); };
  return (
    <div className="picker" style={{ display: "grid", gridTemplateRows: "auto auto 1fr", gap: "8px", border: "1px solid var(--border2)", borderRadius: "10px", padding: "8px", maxHeight: "260px" }}>
      <input className="input" placeholder="Search…" value={q} onChange={(e) => setQ(e.target.value)} />
      <div className="row"><button type="button" className="btn subtle sm" onClick={() => setMany(vis, true)}>Select visible</button><button type="button" className="btn subtle sm" onClick={() => onChange(new Set())}>Clear</button></div>
      <div className="scroll" style={{ overflowY: "auto", display: "grid", gap: "2px" }}>
        {!vis.length ? <div className="muted">{emptyText || "No items"}</div> : vis.map((v) => (
          <label className="row" key={v} style={{ display: "flex", gap: "8px", alignItems: "center" }}>
            <input type="checkbox" checked={selected.has(v)} onChange={(e) => setMany([v], e.target.checked)} /> <span>{v}</span>
          </label>
        ))}
      </div>
    </div>
  );
}

function Status({ s }) { return s ? <div className={"status " + (s.variant || "")}>{s.msg}</div> : <div className="status" />; }

// ── override card (one noun/verb policy entry) ──
const cfgToCard = (type, cfg, kind) => ({
  type, kind,
  strategy: cfg.strategy || "soft",
  days: { value: num(cfg.archive_after_days), isNull: cfg.archive_after_days == null },
  max: { value: num(cfg.max_items), isNull: cfg.max_items == null },
  include_files: cfg.include_files !== false,
  schema_versioning: cfg.schema_versioning !== false,
  on_reference: cfg.on_reference || "tombstone",
  exceptions: Array.isArray(cfg.exceptions) ? cfg.exceptions : [],
});
const cardToCfg = (c) => {
  const out = {
    strategy: c.strategy,
    archive_after_days: c.days.isNull ? null : num(c.days.value),
    max_items: c.max.isNull ? null : num(c.max.value),
    include_files: c.include_files,
    schema_versioning: c.schema_versioning,
  };
  if (c.kind === "noun") { out.on_reference = c.on_reference; out.exceptions = c.exceptions; }
  return out;
};

function OverrideCard({ card, types, onChange, onRemove }) {
  const set = (patch) => onChange({ ...card, ...patch });
  return (
    <div className="override-card full">
      <div className="override-head"><h3>{card.kind === "noun" ? "Noun Override" : "Verb Override"}</h3><button type="button" className="btn danger subtle" onClick={onRemove}>Remove</button></div>
      <div className="override-body">
        {card.kind === "noun" ? (
          <div className="field"><span className="label">On reference</span><Segmented options={["tombstone", "detach", "error"]} value={card.on_reference} onChange={(v) => set({ on_reference: v })} /></div>
        ) : null}
        <div className="field">
          <label className="label">{card.kind === "noun" ? "Noun Type" : "Verb/Test Type"}</label>
          <select className="ov_type input select" value={card.type} onChange={(e) => set({ type: e.target.value })}>
            <option value="">— Pick one —</option>{types.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>
        <div className="field"><span className="label">Strategy</span><Segmented options={["soft", "hard"]} value={card.strategy} onChange={(v) => set({ strategy: v })} /></div>
        <NumNull label={card.kind === "verb" ? "Archive runs after days" : "Archive after days"} toggleLabel="Never" max="3650" step="1" v={card.days} onChange={(days) => set({ days })} />
        <NumNull label="Max items" toggleLabel="Unlimited" max="1000000" step="100" v={card.max} onChange={(max) => set({ max })} />
        <div className="field"><span className="label">Flags</span>
          <label className="toggle"><input type="checkbox" checked={card.include_files} onChange={(e) => set({ include_files: e.target.checked })} /> include files</label>
          <label className="toggle"><input type="checkbox" checked={card.schema_versioning} onChange={(e) => set({ schema_versioning: e.target.checked })} /> schema versioning</label>
        </div>
        {card.kind === "noun" ? <div className="field"><span className="label">Never archive these IDs</span><Chips values={card.exceptions} onChange={(exceptions) => set({ exceptions })} /></div> : null}
      </div>
    </div>
  );
}

// ── accordion item ──
function AccItem({ title, open, onToggle, children }) {
  return (
    <div className={"acc-item" + (open ? " open" : "")}>
      <button className="acc-head" type="button" onClick={onToggle}><span>{title}</span><svg className="icon chev"><use href="/static/icons.svg#i-chevron" /></svg></button>
      {open ? <div className="acc-body">{children}</div> : null}
    </div>
  );
}

// ── root ──
function ArchiveWorkbench() {
  const [projects, setProjects] = useState([]);
  const [project, setProject] = useState("");
  const [nounTypes, setNounTypes] = useState([]);
  const [verbTypes, setVerbTypes] = useState([]);
  const [def, setDef] = useState(cfgToCard("", {}, "noun")); // reuse card model for defaults (no type)
  const [nounCards, setNounCards] = useState([]);
  const [verbCards, setVerbCards] = useState([]);
  const [acc, setAcc] = useState(0); // open accordion index
  const [policyStatus, setPolicyStatus] = useState(null);
  const [log, setLog] = useState([]);

  // preview
  const [preview, setPreview] = useState(null); // {map} | null
  const [previewStatus, setPreviewStatus] = useState(null);

  // manual archive
  const [manualType, setManualType] = useState("");
  const [manualIds, setManualIds] = useState([]);
  const [manualSel, setManualSel] = useState(new Set());
  const [manualStatus, setManualStatus] = useState(null);

  // noun restore
  const [restoreType, setRestoreType] = useState("");
  const [restoreIds, setRestoreIds] = useState([]); // all archived ids
  const restoreMap = useRef(new Map()); // id -> 'soft'|'hard'
  const [restoreSel, setRestoreSel] = useState(new Set());
  const [restoreStatus, setRestoreStatus] = useState(null);

  // run archive / restore
  const [verbGroups, setVerbGroups] = useState([]);
  const [raGroup, setRaGroup] = useState(""); const [raRuns, setRaRuns] = useState([]); const [raSel, setRaSel] = useState(new Set()); const [raStatus, setRaStatus] = useState(null);
  const [rrGroup, setRrGroup] = useState(""); const [rrRuns, setRrRuns] = useState([]); const [rrSel, setRrSel] = useState(new Set()); const [rrStatus, setRrStatus] = useState(null);

  const addLog = (msg, type = "info") => setLog((l) => [{ msg, type, t: new Date().toLocaleTimeString() }, ...l].slice(0, 200));

  // ── policy serialization ──
  const readPolicy = () => {
    const d = cardToCfg(def); // {strategy, archive_after_days, max_items, include_files, schema_versioning, on_reference, exceptions}
    const dflt = { strategy: d.strategy, on_reference: def.on_reference, archive_after_days: d.archive_after_days, max_items: d.max_items, include_files: d.include_files, schema_versioning: d.schema_versioning };
    const nouns = {}; nounCards.forEach((c) => { if (c.type) nouns[c.type] = cardToCfg(c); });
    const verbs = {}; verbCards.forEach((c) => { if (c.type) verbs[c.type] = cardToCfg(c); });
    return { default: dflt, nouns, verbs };
  };
  const writePolicy = (policy) => {
    const dd = policy.default || {};
    setDef(cfgToCard("", dd, "noun"));
    setNounCards(Object.entries(policy.nouns || {}).map(([type, cfg]) => cfgToCard(type, cfg, "noun")));
    setVerbCards(Object.entries(policy.verbs || {}).map(([type, cfg]) => cfgToCard(type, cfg, "verb")));
  };

  // ── loaders ──
  const loadTypes = async (p) => {
    const [nt, vt] = await Promise.all([get(`${API}/${enc(p)}/noun_types`).catch(() => []), get(`${API}/${enc(p)}/verb_types`).catch(() => [])]);
    setNounTypes(Array.isArray(nt) ? [...nt].sort((a, b) => a.localeCompare(b)) : []);
    setVerbTypes(Array.isArray(vt) ? [...vt].sort((a, b) => a.localeCompare(b)) : []);
  };
  const loadPolicy = async (p) => {
    try { const pol = await get(`${API}/${enc(p)}/policy`); writePolicy(pol || { default: {}, nouns: {}, verbs: {} }); setPolicyStatus({ msg: "Policy loaded", variant: "ok" }); addLog("Policy loaded", "ok"); }
    catch (e) { setPolicyStatus({ msg: "Load failed: " + (e.message || e), variant: "err" }); }
  };
  const loadVerbGroups = async (p) => { const g = await get(`${API}/${enc(p)}/verb_groups`).catch(() => []); setVerbGroups(Array.isArray(g) ? g : []); };
  const loadRuns = (p, group, where) => group ? get(`${API}/${enc(p)}/runs/list?verb_group=${enc(group)}&where=${where}`).then((d) => {
    const arr = Array.isArray(d) ? d : (d && d.runs) || [];
    return arr.map((v) => String(v.run_id != null ? v.run_id : (v.id != null ? v.id : JSON.stringify(v))));
  }).catch(() => []) : Promise.resolve([]);

  const refreshAll = async (p) => {
    await loadTypes(p); await loadPolicy(p); await loadVerbGroups(p);
  };

  useEffect(() => {
    get(`${API}/projects`).then((list) => {
      const ps = Array.isArray(list) ? list : [];
      setProjects(ps);
      const url = new URLSearchParams(location.search).get("project");
      const p = ps.includes(url) ? url : (ps[0] || "");
      setProject(p); if (p) refreshAll(p);
    }).catch((e) => addLog("Failed to load projects: " + (e.message || e), "err"));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const onProject = (p) => { setProject(p); setPreview(null); setManualType(""); setManualIds([]); setManualSel(new Set()); setRestoreType(""); setRestoreIds([]); setRestoreSel(new Set()); setRaGroup(""); setRaRuns([]); setRaSel(new Set()); setRrGroup(""); setRrRuns([]); setRrSel(new Set()); refreshAll(p); };

  // ── policy actions ──
  const savePolicy = async () => { try { await post(`${API}/${enc(project)}/policy`, readPolicy()); setPolicyStatus({ msg: "Policy saved", variant: "ok" }); addLog("Policy saved", "ok"); } catch (e) { setPolicyStatus({ msg: "Save failed: " + (e.message || e), variant: "err" }); } };
  const doPreview = async () => {
    setPreviewStatus({ msg: "Previewing…", variant: "info" });
    try { await post(`${API}/${enc(project)}/policy`, readPolicy()); const map = await get(`${API}/${enc(project)}/nouns/preview`); setPreview(map || {}); setPreviewStatus({ msg: "Preview ready", variant: "ok" }); }
    catch (e) { setPreviewStatus({ msg: "Preview failed: " + (e.message || e), variant: "err" }); }
  };
  const archiveDerived = async () => { setPreviewStatus({ msg: "Applying…", variant: "info" }); try { await post(`${API}/${enc(project)}/nouns/apply`, null); setPreviewStatus({ msg: "Archive complete", variant: "ok" }); addLog("Archive (policy) complete", "ok"); refreshAll(project); } catch (e) { setPreviewStatus({ msg: "Apply failed: " + (e.message || e), variant: "err" }); } };

  // ── manual archive ──
  const refreshManual = async (noun) => {
    if (!noun) { setManualIds([]); setManualSel(new Set()); return; }
    setManualStatus({ msg: "Loading instances…", variant: "info" });
    try {
      let ids = await get(`${API}/${enc(project)}/nouns/ids?type=${enc(noun)}`).catch(() => []);
      if (!ids || !ids.length) ids = await get(`${API}/${enc(project)}/nouns/ids/${enc(noun)}`).catch(() => []);
      ids = (Array.isArray(ids) ? ids : []).map(String);
      // filter out already-archived (soft ∪ hard)
      const archived = new Set();
      for (const strat of ["soft", "hard"]) {
        const a = await get(`${API}/${enc(project)}/nouns/archived?noun=${enc(noun)}&strategy=${strat}&limit=${FETCH_LIMIT}`).catch(() => ({}));
        ((a && a[noun] && a[noun].ids) || []).forEach((x) => archived.add(String(x)));
      }
      const free = ids.filter((x) => !archived.has(x));
      setManualIds(free); setManualSel(new Set()); setManualStatus({ msg: `${free.length} instances loaded`, variant: "ok" });
    } catch (e) { setManualStatus({ msg: "Failed to load instances: " + (e.message || e), variant: "err" }); }
  };
  const applyManual = async () => {
    if (!manualType) { setManualStatus({ msg: "Pick a noun type", variant: "err" }); return; }
    const ids = [...manualSel]; if (!ids.length) { setManualStatus({ msg: "Select at least one instance", variant: "err" }); return; }
    try { await post(`${API}/${enc(project)}/nouns/apply`, { [manualType]: ids }); setManualStatus({ msg: "Archive complete", variant: "ok" }); addLog(`Archived ${ids.length} ${manualType}`, "ok"); refreshManual(manualType); } catch (e) { setManualStatus({ msg: "Apply failed: " + (e.message || e), variant: "err" }); }
  };

  // ── noun restore ──
  const refreshRestore = async (noun) => {
    if (!noun) { setRestoreIds([]); setRestoreSel(new Set()); restoreMap.current = new Map(); return; }
    setRestoreStatus({ msg: "Loading…", variant: "info" });
    try {
      const map = new Map(); let soft = 0, hard = 0;
      for (const strat of ["soft", "hard"]) {
        const a = await get(`${API}/${enc(project)}/nouns/archived?noun=${enc(noun)}&strategy=${strat}&limit=${FETCH_LIMIT}`).catch(() => ({}));
        ((a && a[noun] && a[noun].ids) || []).forEach((x) => { map.set(String(x), strat); if (strat === "soft") soft++; else hard++; });
      }
      restoreMap.current = map;
      const all = [...map.keys()];
      setRestoreIds(all); setRestoreSel(new Set());
      setRestoreStatus(all.length ? { msg: `${all.length} archived IDs (soft: ${soft}, hard: ${hard})`, variant: "ok" } : { msg: "No archived IDs", variant: "info" });
    } catch (e) { setRestoreStatus({ msg: "Failed to load: " + (e.message || e), variant: "err" }); }
  };
  const applyRestore = async () => {
    if (!restoreType) { setRestoreStatus({ msg: "Pick a noun type", variant: "err" }); return; }
    const sel = [...restoreSel]; if (!sel.length) { setRestoreStatus({ msg: "Select at least one ID", variant: "err" }); return; }
    const softIds = sel.filter((x) => restoreMap.current.get(x) === "soft");
    const hardIds = sel.filter((x) => restoreMap.current.get(x) === "hard");
    setRestoreStatus({ msg: "Restoring…", variant: "info" });
    try {
      if (softIds.length) await post(`${API}/${enc(project)}/nouns/restore/apply?strategy=soft`, { [restoreType]: softIds });
      if (hardIds.length) await post(`${API}/${enc(project)}/nouns/restore/apply?strategy=hard`, { [restoreType]: hardIds });
      setRestoreStatus({ msg: "Restore complete", variant: "ok" }); addLog(`Restored ${sel.length} ${restoreType}`, "ok"); refreshRestore(restoreType);
    } catch (e) { setRestoreStatus({ msg: "Restore failed: " + (e.message || e), variant: "err" }); }
  };

  // ── run archive / restore ──
  const onRaGroup = async (g) => { setRaGroup(g); setRaSel(new Set()); setRaRuns(await loadRuns(project, g, "active")); };
  const onRrGroup = async (g) => { setRrGroup(g); setRrSel(new Set()); setRrRuns(await loadRuns(project, g, "archived")); };
  const doRunArchive = async () => {
    if (!raGroup) { setRaStatus({ msg: "Pick a verb group", variant: "err" }); return; }
    const runs = [...raSel]; if (!runs.length) { setRaStatus({ msg: "Select at least one run", variant: "err" }); return; }
    setRaStatus({ msg: "Archiving…", variant: "info" });
    try { await post(`${API}/${enc(project)}/runs/archive/apply`, runs.map((rid) => ({ verb_group: raGroup, run_id: String(rid) }))); setRaStatus({ msg: "Archive complete", variant: "ok" }); addLog(`Archived ${runs.length} runs in ${raGroup}`, "ok"); onRaGroup(raGroup); } catch (e) { setRaStatus({ msg: "Apply failed: " + (e.message || e), variant: "err" }); }
  };
  const doRunRestore = async () => {
    if (!rrGroup) { setRrStatus({ msg: "Pick a verb group", variant: "err" }); return; }
    const runs = [...rrSel]; if (!runs.length) { setRrStatus({ msg: "Select at least one run", variant: "err" }); return; }
    setRrStatus({ msg: "Restoring…", variant: "info" });
    try { await post(`${API}/${enc(project)}/runs/restore/apply`, runs.map((rid) => ({ verb_group: rrGroup, run_id: String(rid) }))); setRrStatus({ msg: "Restore complete", variant: "ok" }); addLog(`Restored ${runs.length} runs in ${rrGroup}`, "ok"); onRrGroup(rrGroup); } catch (e) { setRrStatus({ msg: "Restore failed: " + (e.message || e), variant: "err" }); }
  };

  const previewRows = preview ? Object.keys(preview).sort((a, b) => a.localeCompare(b)) : [];

  const overrideList = (cards, setCards, kind, types) => (
    <>
      <div className="row space"><div className="muted">Create or edit overrides per {kind} type.</div>
        <button className="btn sm" onClick={() => setCards((cs) => [...cs, cfgToCard("", {}, kind)])}><Icon name="plus" />Add override</button></div>
      <div>
        {!cards.length && !types.length ? <div className="status info">No {kind} types found. Load policy or refresh projects.</div>
          : cards.map((c, i) => <OverrideCard key={i} card={c} types={types} onChange={(nc) => setCards((cs) => cs.map((x, j) => (j === i ? nc : x)))} onRemove={() => setCards((cs) => cs.filter((_, j) => j !== i))} />)}
      </div>
    </>
  );

  return (
    <>
      <section className="panel aw-toolbar">
        <div className="panel-head"><Icon name="folder" /><span className="panel-title">Workspace</span></div>
        <div className="panel-body aw-toolbar-row">
          <label className="field aw-field"><span className="field-label">Project</span>
            <select id="projectSelect" className="input select" value={project} onChange={(e) => onProject(e.target.value)}>
              {!projects.length ? <option value="">{project || "…"}</option> : projects.map((p) => <option key={p} value={p}>{p}</option>)}
            </select></label>
          <button id="refreshProjectsBtn" className="btn" onClick={() => get(`${API}/projects`).then((l) => { setProjects(Array.isArray(l) ? l : []); refreshAll(project); })}><Icon name="refresh" />Refresh</button>
        </div>
      </section>

      <section className="panel" id="policyCard">
        <div className="panel-head"><Icon name="archive" /><span className="panel-title">Archive Policy</span>
          <div className="actions"><button className="btn sm" onClick={() => loadPolicy(project)}><Icon name="download" />Load</button>
            <button className="btn primary sm" onClick={savePolicy}><Icon name="save" />Save</button></div>
        </div>
        <div className="panel-body">
          <div className="accordion" id="policyAcc">
            <AccItem title="Defaults" open={acc === 0} onToggle={() => setAcc(acc === 0 ? -1 : 0)}>
              <div className="grid two">
                <div className="field"><label className="label">Strategy</label><Segmented options={["soft", "hard"]} value={def.strategy} onChange={(v) => setDef({ ...def, strategy: v })} /></div>
                <div className="field"><label className="label">On reference</label><Segmented options={["tombstone", "detach", "error"]} value={def.on_reference} onChange={(v) => setDef({ ...def, on_reference: v })} /></div>
                <NumNull label="Archive after days" toggleLabel="Never" max="3650" step="1" v={def.days} onChange={(days) => setDef({ ...def, days })} />
                <NumNull label="Max items" toggleLabel="Unlimited" max="1000000" step="100" v={def.max} onChange={(max) => setDef({ ...def, max })} />
                <div className="field"><label className="label">Flags</label>
                  <label className="toggle"><input type="checkbox" checked={def.include_files} onChange={(e) => setDef({ ...def, include_files: e.target.checked })} /> Include files</label>
                  <label className="toggle"><input type="checkbox" checked={def.schema_versioning} onChange={(e) => setDef({ ...def, schema_versioning: e.target.checked })} /> Schema versioning</label>
                </div>
              </div>
              <Status s={policyStatus} />
            </AccItem>
            <AccItem title="Noun Overrides" open={acc === 1} onToggle={() => setAcc(acc === 1 ? -1 : 1)}>{overrideList(nounCards, setNounCards, "noun", nounTypes)}</AccItem>
            <AccItem title="Verb Overrides" open={acc === 2} onToggle={() => setAcc(acc === 2 ? -1 : 2)}>{overrideList(verbCards, setVerbCards, "verb", verbTypes)}</AccItem>
          </div>
        </div>
      </section>

      {/* Restore by Selection (noun) */}
      <section className="panel override-card full">
        <div className="panel-head"><Icon name="play" /><span className="panel-title">Restore by Selection</span>
          <div className="actions"><button className="btn primary sm" onClick={applyRestore}>Restore Selected</button></div></div>
        <div className="panel-body">
          <div className="field"><label className="label">Noun Type</label>
            <select className="input select" value={restoreType} onChange={(e) => { setRestoreType(e.target.value); refreshRestore(e.target.value); }}>
              <option value="">— Pick one —</option>{nounTypes.map((t) => <option key={t} value={t}>{t}</option>)}</select></div>
          <Picker values={restoreIds} selected={restoreSel} onChange={setRestoreSel} emptyText="No archived IDs" />
          <Status s={restoreStatus} />
        </div>
      </section>

      {/* Archive Runs by Selection */}
      <section className="panel override-card full">
        <div className="panel-head"><Icon name="archive" /><span className="panel-title">Archive Runs by Selection</span>
          <div className="actions"><button className="btn primary sm" onClick={doRunArchive}>Archive Selected</button></div></div>
        <div className="panel-body">
          <div className="field"><label className="label">Verb Group</label>
            <select className="input select" value={raGroup} onChange={(e) => onRaGroup(e.target.value)}><option value="">— Pick one —</option>{verbGroups.map((g) => <option key={g} value={g}>{g}</option>)}</select></div>
          <Picker values={raRuns} selected={raSel} onChange={setRaSel} emptyText="No runs found" />
          <Status s={raStatus} />
        </div>
      </section>

      {/* Restore Runs */}
      <section className="panel override-card full">
        <div className="panel-head"><Icon name="play" /><span className="panel-title">Restore Runs</span>
          <div className="actions"><button className="btn primary sm" onClick={doRunRestore}>Restore Selected</button></div></div>
        <div className="panel-body">
          <div className="field"><label className="label">Verb Group</label>
            <select className="input select" value={rrGroup} onChange={(e) => onRrGroup(e.target.value)}><option value="">— Pick one —</option>{verbGroups.map((g) => <option key={g} value={g}>{g}</option>)}</select></div>
          <Picker values={rrRuns} selected={rrSel} onChange={setRrSel} emptyText="No archived runs" />
          <Status s={rrStatus} />
        </div>
      </section>

      {/* Archive by Policy */}
      <section className="panel" id="policyArchiveCard">
        <div className="panel-head"><Icon name="play" /><span className="panel-title">Archive by Policy</span>
          <div className="actions"><button id="previewPolicyBtn" className="btn sm" onClick={doPreview}><Icon name="play" />Preview</button>
            <button id="applyPolicyBtn" className="btn danger sm" onClick={archiveDerived}><Icon name="archive" />Archive Derived</button></div></div>
        <div className="panel-body">
          <div className="wrap scroll"><table className="grid"><thead><tr><th>Noun Type</th><th>Strategy</th><th>Eligible Count</th><th>Eligible IDs</th></tr></thead>
            <tbody id="policyPreviewBody">
              {previewRows.map((noun) => {
                const row = preview[noun] || {};
                const ids = row.eligible_ids || row.eligible || row.ids || [];
                const strat = row.strategy || (readPolicy().nouns[noun] || {}).strategy || readPolicy().default.strategy || "soft";
                return <tr key={noun}><td>{noun}</td><td>{strat}</td><td>{ids.length}</td>
                  <td>{ids.length ? <div className="chips">{ids.map((x) => <span className="chip" key={x}><span>{String(x)}</span></span>)}</div> : <span className="muted">None eligible</span>}</td></tr>;
              })}
            </tbody></table></div>
          {!previewRows.length ? <div className="gims-state is-empty" id="policyPreviewEmpty"><span className="gims-state-mark icon-chip round"><Icon name="archive" /></span><p className="gims-state-msg">Run a preview to see which items are eligible for archiving under the current policy.</p></div> : null}
          <Status s={previewStatus} />
        </div>
      </section>

      {/* Archive by Selection (manual) */}
      <section className="panel" id="manualArchiveCard">
        <div className="panel-head"><Icon name="check" /><span className="panel-title">Archive by Selection</span>
          <div className="actions"><button id="applyManualBtn" className="btn primary sm" onClick={applyManual}><Icon name="archive" />Archive Selected</button></div></div>
        <div className="panel-body">
          <div className="grid three">
            <div className="field"><label className="label">Noun Type</label>
              <select id="manualNounType" className="input select" value={manualType} onChange={(e) => { setManualType(e.target.value); refreshManual(e.target.value); }}>
                <option value="">— Pick one —</option>{nounTypes.map((t) => <option key={t} value={t}>{t}</option>)}</select></div>
            <div className="field span-two"><label className="label">Instances</label><Picker values={manualIds} selected={manualSel} onChange={setManualSel} emptyText="No instances" /></div>
          </div>
          <Status s={manualStatus} />
        </div>
      </section>

      {/* Console */}
      <section className="panel" id="consoleCard">
        <div className="panel-head"><Icon name="terminal" /><span className="panel-title">Console</span>
          <div className="actions"><button className="btn sm" onClick={() => setLog([])}><Icon name="trash" />Clear</button></div></div>
        <div className="panel-body"><div id="console" className="console">{log.map((l, i) => <div className={"log " + l.type} key={i}>[{l.t}] {l.msg}</div>)}</div></div>
      </section>
    </>
  );
}

mountOnAuth("archive-workbench-root", (host) => createRoot(host).render(<ArchiveWorkbench />));
