// frontend/lib/ui.jsx — React port of the gims-ui component layer (Watery classes preserved,
// so it is DOM-compatible with the vanilla window.GIMSUI components during the migration).
import { Fragment, useEffect, useMemo, useRef, useState } from "react";

export function Icon({ name, className }) {
  return (
    <svg className={"icon" + (className ? " " + className : "")}>
      <use href={`/static/icons.svg#i-${name}`} />
    </svg>
  );
}

export function KindBadge({ kind }) {
  const cls = "ui-kind kind-" + String(kind || "").toLowerCase().replace(/[^a-z0-9]+/g, "-");
  return <span className={cls}>{kind || "?"}</span>;
}

// ref: {kind, id, label, href, title}
export function EntityChip({ kind, id, label, href, title }) {
  const text = label != null ? String(label) : (id != null ? String(id) : "—");
  const inner = (
    <>
      <KindBadge kind={kind} />
      <span className="ui-entity-label">{text}</span>
      {href ? <span className="ui-entity-go"><Icon name="arrow" /></span> : null}
    </>
  );
  return href
    ? <a className="ui-entity linked" href={href} title={title || label}>{inner}</a>
    : <span className="ui-entity" title={title || label}>{inner}</span>;
}

// items: [{label, value, tone, node}]
export function SpecList({ items, compact }) {
  return (
    <dl className={"ui-spec" + (compact ? " compact" : "")}>
      {(items || []).map((it, i) => (
        <Fragment key={i}>
          <dt className="ui-spec-k">{it.label}</dt>
          <dd className={"ui-spec-v" + (it.tone ? " tone-" + it.tone : "")}>
            {it.node != null ? it.node : (it.value == null ? "—" : String(it.value))}
          </dd>
        </Fragment>
      ))}
    </dl>
  );
}

export function EntityList({ children }) {
  return <div className="ui-entity-list">{children}</div>;
}

// ── Status visuals (port of GIMSUI.statusTimeline/stepper/progressRing; same ui-* classes) ──
// state: 'done' | 'pending' | 'gate' | 'error' | 'active'
const STATE_ICON = { done: "check", pending: "clock", gate: "lock", error: "close", active: "play" };

// steps:[{label, state, detail}] — horizontal zones with state colour + icon + tooltip.
export function StatusTimeline({ steps }) {
  const list = steps || [];
  return (
    <div className="ui-timeline">
      {list.map((s, i) => (
        <Fragment key={i}>
          {i ? <div className={"ui-timeline-link " + ((s.state === "done" || list[i - 1].state === "done") ? "done" : "")} /> : null}
          <div className={"ui-timeline-step state-" + (s.state || "pending")} title={s.detail || s.label}>
            <span className="ui-timeline-dot"><Icon name={STATE_ICON[s.state] || "clock"} /></span>
            <span className="ui-timeline-label">{s.label}</span>
            {s.detail ? <span className="ui-timeline-detail">{s.detail}</span> : null}
          </div>
        </Fragment>
      ))}
    </div>
  );
}

// steps:[{label, state, detail}] — vertical stepper for linear workflows.
export function Stepper({ steps }) {
  return (
    <div className="ui-stepper">
      {(steps || []).map((s, i) => (
        <div key={i} className={"ui-stepper-step state-" + (s.state || "pending")} title={s.detail || s.label}>
          <span className="ui-stepper-dot"><Icon name={STATE_ICON[s.state] || "clock"} /></span>
          <span className="ui-stepper-label">{s.label}</span>
          {s.detail ? <span className="ui-stepper-detail">{s.detail}</span> : null}
        </div>
      ))}
    </div>
  );
}

// conic ring with % in the centre. tone defaults by percent (ok≥100, accent≥50, else warn).
export function ProgressRing({ percent, tone: tone0 }) {
  const p = Math.max(0, Math.min(100, Math.round(percent || 0)));
  const tone = tone0 || (p >= 100 ? "ok" : p >= 50 ? "accent" : "warn");
  return (
    <div className={"ui-ring tone-" + tone} role="img" aria-label={p + "% complete"} style={{ "--p": String(p) }}>
      <span className="ui-ring-num">{p + "%"}</span>
    </div>
  );
}

// ── StateBlock: the four canonical states (matches gims.js renderEmpty/renderLoading/renderError DOM) ──
// kind: 'empty' | 'loading' | 'error'
export function StateBlock({ kind = "empty", icon, title, message, children }) {
  const ico = icon || (kind === "error" ? "warning" : "info");
  return (
    <div className={"gims-state is-" + kind}>
      {kind === "loading"
        ? <span className="gims-spinner" aria-hidden="true" />
        : <span className="gims-state-mark icon-chip round"><Icon name={ico} /></span>}
      {title ? <h3 className="gims-state-title">{title}</h3> : null}
      {message ? <p className="gims-state-msg">{message}</p> : null}
      {children}
    </div>
  );
}

// ── GridTable: sortable, sticky-header, truncate+title, reactive row-select (port of GIMSUI.gridTable) ──
// columns:[{key,label,width,align,type:'num',render(val,row),sortable:false}]; rows:[obj].
// getKey(row,i) keys a row; onSelect(row,key) fires on click; selectedKey controls the highlight;
// sort:{key,dir}; empty:{icon,title,message}; maxHeight scrolls the body under a sticky header.
function gridCmp(a, b, key, type) {
  let x = a[key], y = b[key];
  if (type === "num") { x = parseFloat(x) || 0; y = parseFloat(y) || 0; return x - y; }
  x = String(x == null ? "" : x).toLowerCase(); y = String(y == null ? "" : y).toLowerCase();
  return x < y ? -1 : x > y ? 1 : 0;
}
export function GridTable({ columns, rows, getKey = (r, i) => (r.__k != null ? r.__k : i), onSelect, selectedKey, sort: sort0 = null, empty, maxHeight }) {
  const [sort, setSort] = useState(sort0);
  const [sel, setSel] = useState(selectedKey != null ? selectedKey : null);
  useEffect(() => { setSel(selectedKey != null ? selectedKey : null); }, [selectedKey]);

  const view = useMemo(() => {
    if (!sort) return rows;
    const col = columns.find((c) => c.key === sort.key);
    const f = sort.dir === "desc" ? -1 : 1;
    return rows.slice().sort((a, b) => gridCmp(a, b, sort.key, col && col.type) * f);
  }, [rows, sort, columns]);

  if (!rows || !rows.length) {
    return <StateBlock kind="empty" icon={(empty && empty.icon) || "info"} title={(empty && empty.title) || "No records"} message={empty && empty.message} />;
  }

  const toggleSort = (c) => {
    if (c.sortable === false) return;
    setSort((s) => ({ key: c.key, dir: s && s.key === c.key && s.dir === "asc" ? "desc" : "asc" }));
  };
  const pick = (row, key) => { setSel(key); if (onSelect) onSelect(row, key); };
  const clickable = !!onSelect;

  return (
    <div className="ui-grid-wrap" style={maxHeight ? { maxHeight } : undefined}>
      <table className="ui-grid">
        <thead>
          <tr>
            {columns.map((c) => {
              const sorted = sort && sort.key === c.key;
              const cls = "align-" + (c.align || "left")
                + (sorted ? " sorted " + sort.dir : "")
                + (c.sortable === false ? "" : " sortable");
              return (
                <th key={c.key} className={cls} title={c.label}
                    style={c.width ? { width: c.width } : undefined}
                    onClick={() => toggleSort(c)}>
                  <span className="ui-grid-th">{c.label}</span>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {view.map((row, i) => {
            const key = getKey(row, i);
            const selected = sel != null && key === sel;
            return (
              <tr key={key}
                  className={(selected ? "selected" : "") + (clickable ? " clickable" : "")}
                  tabIndex={clickable ? 0 : undefined}
                  onClick={clickable ? () => pick(row, key) : undefined}
                  onKeyDown={clickable ? (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(row, key); } } : undefined}>
                {columns.map((c) => {
                  const val = row[c.key];
                  const out = c.render ? c.render(val, row) : (val == null ? "" : String(val));
                  const title = c.render ? undefined : (val == null ? "" : String(val));
                  return <td key={c.key} className={"align-" + (c.align || "left")} title={title}>{out}</td>;
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ── Option helpers (shared by MultiSelect / TransferList / MatrixEditor) ──────────────────────
function normOpts(options) {
  return (options || []).map((o) => (typeof o === "string"
    ? { value: o, label: o, group: "" }
    : { value: o.value, label: o.label != null ? o.label : o.value, group: o.group || "" }));
}
function normCols(cols) {
  return (cols || []).map((c) => (typeof c === "string"
    ? { key: c, label: c, group: "" }
    : { key: c.key, label: c.label != null ? c.label : c.key, group: c.group || "" }));
}
function matchq(text, q) { return !q || String(text == null ? "" : text).toLowerCase().includes(q); }
function byGroup(opts) {
  const order = [], map = new Map();
  opts.forEach((o) => { const g = o.group || ""; if (!map.has(g)) { map.set(g, []); order.push(g); } map.get(g).push(o); });
  return order.map((g) => ({ group: g, items: map.get(g) }));
}
// Uncontrolled search box (keeps focus across parent re-renders). onInput gets the trimmed lowercase value.
function SearchInput({ placeholder, onInput }) {
  return (
    <div className="ui-search">
      <span className="ui-search-i"><Icon name="filter" /></span>
      <input className="ui-search-input input" type="search" placeholder={placeholder || "Search…"}
             aria-label={placeholder || "Search"} onChange={(e) => onInput(e.target.value.trim().toLowerCase())} />
    </div>
  );
}

// ── MultiSelect: searchable (optionally grouped) checkbox list + selected chips ───────────────
// options:[{value,label,group}|str]; value:[...]; onChange(value[]); groups?, search?, placeholder, emptyText
export function MultiSelect({ options, value, onChange, groups, search, placeholder, emptyText }) {
  const opts = useMemo(() => normOpts(options), [options]);
  const useGroups = groups != null ? groups : opts.some((o) => o.group);
  const useSearch = search != null ? search : opts.length > 8;
  const [q, setQ] = useState("");
  const sel = new Set((value || []).map(String));
  const emit = (s) => { if (onChange) onChange(opts.filter((o) => s.has(String(o.value))).map((o) => o.value)); };
  const toggle = (val, on) => { const s = new Set(sel); if (on) s.add(String(val)); else s.delete(String(val)); emit(s); };
  const vis = opts.filter((o) => matchq(o.label, q));
  const selObjs = opts.filter((o) => sel.has(String(o.value)));
  const setAll = (on) => { const s = new Set(sel); vis.forEach((o) => { if (on) s.add(String(o.value)); else s.delete(String(o.value)); }); emit(s); };

  const optRow = (o) => (
    <label className="ui-ms-opt" key={o.value}>
      <input type="checkbox" checked={sel.has(String(o.value))} onChange={(e) => toggle(o.value, e.target.checked)} />
      <span className="ui-ms-opt-label" title={o.label}>{o.label}</span>
    </label>
  );

  return (
    <div className="ui-ms">
      {useSearch ? <SearchInput placeholder={placeholder || "Search options…"} onInput={setQ} /> : null}
      <div className="ui-ms-tools">
        <button className="ui-link-btn" type="button" onClick={() => setAll(true)}>Select all</button>
        <button className="ui-link-btn" type="button" onClick={() => setAll(false)}>Clear</button>
      </div>
      <div className="ui-ms-chips">
        {selObjs.length ? selObjs.map((o) => (
          <span className="ui-ms-chip" key={o.value}>
            <span>{o.label}</span>
            <button className="ui-chip-x" type="button" title="Remove" aria-label={"Remove " + o.label}
                    onClick={() => toggle(o.value, false)}><Icon name="close" /></button>
          </span>
        )) : <span className="ui-ms-none">{emptyText || "None selected"}</span>}
      </div>
      <div className="ui-ms-list" role="group">
        {!vis.length ? <div className="ui-ms-empty">No matches</div>
          : useGroups ? byGroup(vis).map((g) => (
              <Fragment key={g.group}>
                {g.group ? <div className="ui-ms-group">{g.group}</div> : null}
                {g.items.map(optRow)}
              </Fragment>
            ))
          : vis.map(optRow)}
      </div>
    </div>
  );
}

// ── TransferList: two-pane available ↔ selected (searchable, optionally grouped) ──────────────
// options; value:[...]; onChange(value[]); groups?, search?, titles:{available,selected}
export function TransferList({ options, value, onChange, groups, titles }) {
  const opts = useMemo(() => normOpts(options), [options]);
  const useGroups = groups != null ? groups : opts.some((o) => o.group);
  const t = titles || {};
  const [qa, setQa] = useState("");
  const [qs, setQs] = useState("");
  const sel = new Set((value || []).map(String));
  const emit = (s) => { if (onChange) onChange(opts.filter((o) => s.has(String(o.value))).map((o) => o.value)); };
  const move = (val, toSelected) => { const s = new Set(sel); if (toSelected) s.add(String(val)); else s.delete(String(val)); emit(s); };

  const itemBtn = (o, selected) => (
    <button className="ui-tl-item" type="button" title={o.label} key={o.value} onClick={() => move(o.value, !selected)}>
      <span className="ui-tl-item-go"><Icon name={selected ? "close" : "arrow"} /></span>
      <span className="ui-tl-item-label">{o.label}</span>
    </button>
  );
  const renderPane = (selected) => {
    const q = selected ? qs : qa, setQ = selected ? setQs : setQa;
    const items = opts.filter((o) => (selected ? sel.has(String(o.value)) : !sel.has(String(o.value))) && matchq(o.label, q));
    return (
      <div className="ui-tl-pane">
        <div className="ui-tl-head">
          <span className="ui-tl-title">{selected ? (t.selected || "Selected") : (t.available || "Available")}</span>
          <span className="count-pill">{items.length}</span>
        </div>
        <SearchInput placeholder="Search…" onInput={setQ} />
        <div className="ui-tl-list" role="listbox">
          {!items.length ? <div className="ui-ms-empty">{selected ? "Nothing selected yet" : "No options"}</div>
            : useGroups ? byGroup(items).map((g) => (
                <Fragment key={g.group}>
                  {g.group ? <div className="ui-ms-group">{g.group}</div> : null}
                  {g.items.map((o) => itemBtn(o, selected))}
                </Fragment>
              ))
            : items.map((o) => itemBtn(o, selected))}
        </div>
      </div>
    );
  };
  const addAll = () => { const s = new Set(sel); opts.filter((o) => !sel.has(String(o.value)) && matchq(o.label, qa)).forEach((o) => s.add(String(o.value))); emit(s); };
  const remAll = () => { const s = new Set(sel); opts.filter((o) => sel.has(String(o.value)) && matchq(o.label, qs)).forEach((o) => s.delete(String(o.value))); emit(s); };

  return (
    <div className="ui-tl">
      {renderPane(false)}
      <div className="ui-tl-mid">
        <button className="btn ghost ui-tl-allbtn" type="button" title="Add all" onClick={addAll}><Icon name="arrow" /></button>
        <button className="btn ghost ui-tl-allbtn flip" type="button" title="Remove all" onClick={remAll}><Icon name="arrow" /></button>
      </div>
      {renderPane(true)}
    </div>
  );
}

// ── MatrixEditor: searchable rows × grouped/collapsible columns checkbox grid ─────────────────
// rows:[{key,label}|str]; cols:[{key,label,group}|str]; value:{rowKey:[colKeys]}; onChange(value);
// editableRows → row headers become rename inputs + delete + an add-row control, and getValue is keyed
// by the CURRENT trimmed row label (request_options shape). Seed-once + onChange — give it a `key` that
// changes per dataset (e.g. per adjective) so switching records remounts + reseeds it.
export function MatrixEditor({
  rows, cols, value: value0, editableRows, addLabel, addPlaceholder, rowPlaceholder, rowHeader,
  searchPlaceholder, search, onChange, onToggle,
}) {
  const editable = !!editableRows;
  const seed = useMemo(() => {
    let seq = 0;
    const rl = (rows || []).map((r) => (typeof r === "string"
      ? { key: r, label: r }
      : { key: r.key != null ? r.key : ("__r" + (seq++)), label: r.label != null ? r.label : String(r.key) }));
    const val = {};
    rl.forEach((r) => { val[r.key] = new Set((((value0 || {})[r.key]) || []).map(String)); });
    return { rl, val, seq };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const [rowList, setRowList] = useState(seed.rl);
  const [value, setValue] = useState(seed.val);
  const seqRef = useRef(seed.seq);
  const cols2 = useMemo(() => normCols(cols), [cols]);
  const groups = useMemo(() => byGroup(cols2), [cols2]);
  const hasGroups = groups.some((g) => g.group);
  const [collapsed, setCollapsed] = useState(() => new Set());
  const [q, setQ] = useState("");
  const useSearch = search != null ? search : rowList.length > 8;

  const getValue = (rl, val) => {
    const out = {};
    rl.forEach((r) => { const k = editable ? String(r.label).trim() : r.key; if (editable && !k) return; out[k] = [...(val[r.key] || [])]; });
    return out;
  };
  const commit = (rl, val) => { setRowList(rl); setValue(val); if (onChange) onChange(getValue(rl, val)); };
  const visCols = cols2.filter((c) => !collapsed.has(c.group || ""));
  const visRows = rowList.filter((r) => matchq(r.label, q));

  const setCell = (rk, ck, on) => {
    const val = { ...value, [rk]: new Set(value[rk] || []) };
    if (on) val[rk].add(String(ck)); else val[rk].delete(String(ck));
    if (onToggle) onToggle(rk, ck, on);
    commit(rowList, val);
  };
  const toggleCol = (ck) => {
    const all = visRows.every((r) => (value[r.key] || new Set()).has(String(ck)));
    const val = { ...value };
    visRows.forEach((r) => { val[r.key] = new Set(value[r.key] || []); if (all) val[r.key].delete(String(ck)); else val[r.key].add(String(ck)); });
    commit(rowList, val);
  };
  const toggleRow = (rk) => {
    const all = visCols.every((c) => (value[rk] || new Set()).has(String(c.key)));
    const val = { ...value, [rk]: new Set(value[rk] || []) };
    visCols.forEach((c) => { if (all) val[rk].delete(String(c.key)); else val[rk].add(String(c.key)); });
    commit(rowList, val);
  };
  const renameRow = (rk, label) => commit(rowList.map((r) => (r.key === rk ? { ...r, label } : r)), value);
  const delRow = (rk) => { const val = { ...value }; delete val[rk]; commit(rowList.filter((r) => r.key !== rk), val); };
  const addRow = (name) => {
    const nm = name.trim(); if (!nm) return;
    const key = "__new" + (seqRef.current++);
    commit([...rowList, { key, label: nm }], { ...value, [key]: new Set() });
  };
  const toggleGroup = (g) => { const c = new Set(collapsed); if (c.has(g)) c.delete(g); else c.add(g); setCollapsed(c); };
  const colSelCount = (ck) => rowList.reduce((n, r) => n + ((value[r.key] || new Set()).has(String(ck)) ? 1 : 0), 0);

  return (
    <div className={"ui-mx" + (editable ? " editable" : "")}>
      {useSearch ? <SearchInput placeholder={searchPlaceholder || "Search rows…"} onInput={setQ} /> : null}
      <div className="ui-mx-wrap">
        <table className="ui-mx-table">
          {hasGroups ? (
            <thead>
              <tr className="ui-mx-grouprow">
                <th className="ui-mx-corner" />
                {groups.map((g) => {
                  const isC = collapsed.has(g.group || "");
                  const selN = g.items.reduce((n, c) => n + colSelCount(c.key), 0);
                  return (
                    <th key={g.group} className={"ui-mx-grouph" + (isC ? " collapsed" : "")} colSpan={isC ? 1 : g.items.length}>
                      <button className="ui-mx-grouptoggle" type="button"
                              title={(isC ? "Expand " : "Collapse ") + (g.group || "group")} onClick={() => toggleGroup(g.group || "")}>
                        <span className="ui-mx-caret"><Icon name={isC ? "arrow" : "filter"} /></span>
                        <span>{g.group || "—"}</span>
                        {selN ? <span className="count-pill">{selN}</span> : null}
                      </button>
                    </th>
                  );
                })}
              </tr>
            </thead>
          ) : null}
          <tbody>
            <tr className="ui-mx-colrow">
              <th className="ui-mx-corner ui-mx-rowhead-h">{rowHeader || ""}</th>
              {visCols.map((c) => (
                <th key={c.key} className="ui-mx-colh" title={"Toggle column · " + c.label}>
                  <button className="ui-mx-colbtn" type="button" onClick={() => toggleCol(c.key)}>
                    <span className="ui-mx-colh-label">{c.label}</span>
                  </button>
                </th>
              ))}
            </tr>
            {!visRows.length ? (
              <tr><td className="ui-mx-empty" colSpan={visCols.length + 1}>{editable ? "No request labels yet — add one below." : "No rows match"}</td></tr>
            ) : visRows.map((r) => {
              const rsel = visCols.reduce((n, c) => n + ((value[r.key] || new Set()).has(String(c.key)) ? 1 : 0), 0);
              return (
                <tr key={r.key} data-row={r.label}>
                  <th className="ui-mx-rowhead" scope="row">
                    {editable ? (
                      <div className="ui-mx-rowedit-wrap">
                        <input className="ui-mx-rowedit input" value={r.label} placeholder={rowPlaceholder || "Label…"}
                               aria-label="Row label" onChange={(e) => renameRow(r.key, e.target.value)} />
                        {rsel ? <span className="count-pill">{rsel}</span> : null}
                        <button className="ui-mx-rowdel" type="button" title="Remove row" aria-label="Remove row"
                                onClick={() => delRow(r.key)}><Icon name="trash" /></button>
                      </div>
                    ) : (
                      <button className="ui-mx-rowbtn" type="button" title="Toggle all in row" onClick={() => toggleRow(r.key)}>
                        <span className="ui-mx-rowhead-label" title={r.label}>{r.label}</span>
                        {rsel ? <span className="count-pill">{rsel}</span> : null}
                      </button>
                    )}
                  </th>
                  {visCols.map((c) => (
                    <td key={c.key} className="ui-mx-cell">
                      <input type="checkbox" aria-label={r.label + " · " + c.label}
                             checked={(value[r.key] || new Set()).has(String(c.key))}
                             onChange={(e) => setCell(r.key, c.key, e.target.checked)} />
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {editable ? <AddRow addLabel={addLabel} addPlaceholder={addPlaceholder} onAdd={addRow} /> : null}
    </div>
  );
}

function AddRow({ addLabel, addPlaceholder, onAdd }) {
  const ref = useRef(null);
  const doAdd = () => { const v = ref.current ? ref.current.value : ""; onAdd(v); if (ref.current) { ref.current.value = ""; ref.current.focus(); } };
  return (
    <div className="ui-mx-addrow">
      <input ref={ref} className="ui-mx-addinput input" placeholder={addPlaceholder || "New request label…"} aria-label="New row label"
             onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); doAdd(); } }} />
      <button className="btn ghost sm" type="button" onClick={doAdd}>
        <span className="ui-mx-addicon"><Icon name="plus" /></span>
        <span>{addLabel || "Add label"}</span>
      </button>
    </div>
  );
}
