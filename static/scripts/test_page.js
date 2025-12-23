// test_page.js — DataEntry viewer/editor with schema-aware behavior

// Use one consistent React build and force esm.sh to emit real components.
// No ?target=es2022 on glide — it breaks the component export structure.

import React, {
  useEffect,
  useMemo,
  useState,
  useCallback,
  useRef,
} from "https://esm.sh/react@18.3.1";

import { createRoot } from "https://esm.sh/react-dom@18.3.1/client";

import {
  DataEditor,
  GridCellKind
} from "https://esm.sh/@glideapps/glide-data-grid@6.0.3?deps=react@18.3.1&deps=react-dom@18.3.1";

// ---- low-level debug about the library wiring ----
console.debug("[grid/bootstrap] React version:", React.version);
console.debug("[grid/bootstrap] DataEditor typeof:", typeof DataEditor);
if (DataEditor && DataEditor.$$typeof) {
  console.warn("[grid/bootstrap] DataEditor looks like a React element, not a component:", DataEditor);
}

// ---------- tiny helpers ----------
const $ = (s) => document.querySelector(s);
const setStatus = (m) => { const el = $("#status"); if (el) el.textContent = m; };
const pretty = (x) => JSON.stringify(x, null, 2);
const GET = async (u) => { const r = await fetch(u); if (!r.ok) throw new Error(await r.text().catch(() => r.statusText)); return r.json(); };
const POST = async (u, b) => { const r = await fetch(u, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(b) }); if (!r.ok) throw new Error(await r.text().catch(() => r.statusText)); return r.json().catch(() => ({})); };

// ---------- ErrorBoundary (keeps the page alive if DataEditor throws) ----------
class ErrorBoundary extends React.Component {
  constructor(p) { super(p); this.state = { hasError: false, error: null, info: null }; }
  static getDerivedStateFromError(err) { return { hasError: true }; }
  componentDidCatch(error, info) {
    console.error("Uncaught error:", error, info);
    const stack = info?.componentStack || "";
    console.error({ componentStack: stack });
    setStatus("Grid crash captured — check console for details.");
    this.setState({ error, info });
  }
  render() {
    if (this.state.hasError) {
      return React.createElement("div", { className: "muted", style: { padding: "8px", color: "#ff8a8a" } },
        "The grid encountered an error and was isolated. Try reloading a run.");
    }
    return this.props.children;
  }
}

// ---------- history with robust batching + AFTER-snapshot ----------
function useHistory() {
  const ref = useRef({
    stack: [], idx: -1, batching: false, commitTimer: null, lastSig: null,
  });
  const snapshot = (rows) => JSON.parse(JSON.stringify(rows));
  const sign = (rows) => JSON.stringify(rows);

  const _push = (rows) => {
    const h = ref.current;
    const sig = sign(rows);
    if (sig === h.lastSig) return;
    const snap = snapshot(rows);
    h.stack = h.stack.slice(0, h.idx + 1);
    h.stack.push(snap);
    h.idx = h.stack.length - 1;
    h.lastSig = sig;
    console.debug("[grid][hist] push idx", h.idx, "len", h.stack.length);
  };

  const beginBatch = (rows) => {
    const h = ref.current;
    if (!h.batching) { _push(rows); h.batching = true; }
    if (h.commitTimer) { clearTimeout(h.commitTimer); h.commitTimer = null; }
  };

  // IMPORTANT: commit AFTER the edit with a real timeout
  const scheduleCommit = (getRows, delay = 350) => {
    const h = ref.current;
    if (h.commitTimer) clearTimeout(h.commitTimer);
    h.commitTimer = setTimeout(() => {
      try {
        if (h.batching && typeof getRows === "function") {
          const rowsNow = getRows();
          _push(rowsNow); // AFTER-snapshot
        }
      } finally {
        h.batching = false;
        h.commitTimer = null;
        console.debug("[grid][hist] commit");
      }
    }, delay);
  };

  const undo = (curr) => {
    const h = ref.current;
    if (h.idx <= 0) return curr;
    h.idx--;
    const rows = snapshot(h.stack[h.idx]);
    h.lastSig = sign(rows);
    return rows;
  };

  const redo = (curr) => {
    const h = ref.current;
    if (h.idx >= h.stack.length - 1) return curr;
    h.idx++;
    const rows = snapshot(h.stack[h.idx]);
    h.lastSig = sign(rows);
    return rows;
  };

  const reset = () => {
    const h = ref.current;
    if (h.commitTimer) clearTimeout(h.commitTimer);
    ref.current = { stack: [], idx: -1, batching: false, commitTimer: null, lastSig: null };
    console.debug("[grid][hist] reset");
  };

  return { beginBatch, scheduleCommit, undo, redo, reset };
}

// ---------- ordering helpers ----------
function orderHeadersByRules(hs, schemaOrder, primaryFromSchema) {
  const base = Array.from(new Set(hs));
  const primary =
    primaryFromSchema ||
    base.find(h => /_id$/i.test(h) && !h.startsWith("_")) ||
    base.find(h => !h.startsWith("_")) ||
    base[0] || "";

  let ordered = schemaOrder && schemaOrder.length ? schemaOrder.filter(h => base.includes(h)) : base;
  for (const h of base) if (!ordered.includes(h)) ordered.push(h);
  if (primary) ordered = [primary, ...ordered.filter(h => h !== primary)];
  if (ordered.includes("_runID")) ordered = ordered.filter(h => h !== "_runID").concat(["_runID"]);
  return ordered;
}

// ---------- ensure portal exists (for Glide overlay editors) ----------
function ensurePortal() {
  let p = document.getElementById("portal");
  if (!p) {
    p = document.createElement("div");
    p.id = "portal";
    p.style.position = "static";
    p.style.width = "0";
    p.style.height = "0";
    p.style.overflow = "hidden";
    document.body.appendChild(p);
  }
}

// ---------- App (verbose debug, schema-aware refs) ----------
function App() {
  const DEBUG = true;
  const dbg = (...a) => DEBUG && console.debug("[grid]", ...a);

  // loading + race guard
  const [loading, setLoading] = useState(false);
  const loadSeq = useRef(0);

  // force-unmount/remount key for DataEditor
  const [resetKey, setResetKey] = useState(0);

  // ensure Glide overlay portal
  useEffect(() => { ensurePortal(); dbg("portal ensured"); }, []);

  // controls
  const [project, setProject]   = useState($("#project")?.value || "LIMS-System");
  const [verbGroup, setVerbGroup] = useState($("#verb-group")?.value || "Tests");
  const [nounType, setNounType] = useState($("#noun-type")?.value || "Sample");

  const [runs, setRuns] = useState([]);
  const [runId, setRunId] = useState("");

  // ---- Unified grid state (atomic) ----
  const [gridState, setGridState] = useState({
    headers: [],
    rows: [],
    schemaPrimary: "",
    autoGen: false,
    pictureCols: new Set(), // Set<string>
    refCols: new Set(),     // Set<string>
    refDetail: {},          // { field: {...} }
    retestIDs: [],          // string[]
  });

  const { headers, rows, schemaPrimary, autoGen, pictureCols, refCols, refDetail, retestIDs } = gridState;

  // mirror rows to a ref for history batching
  const rowsRef = useRef(rows);
  useEffect(() => { rowsRef.current = rows; }, [rows]);

  // grid sizing + focus + selection
  const hostRef = useRef(null);
  const editorRef = useRef(null);
  const [dims, setDims] = useState({ w: 800, h: 480 });
  const [selection, setSelection] = useState(undefined);
  const focusGrid = useCallback(() => {
    try { editorRef.current?.focus({ preventScroll: true }); dbg("focusGrid()"); }
    catch (e) { dbg("focusGrid error", e); }
  }, []);
  const isGridFocused = () => {
    const el = document.getElementById("grid-host");
    const f = el && el.contains(document.activeElement);
    DEBUG && console.debug("[grid] isGridFocused =", f, "active:", document.activeElement?.tagName, document.activeElement?.id);
    return f;
  };

  // menu infra
  const menuRef = useRef(null);
  function ensureMenu() {
    if (!menuRef.current) {
      const m = document.createElement("div");
      Object.assign(m.style, {
        position: "fixed", zIndex: 99999, border: "1px solid #263039",
        background: "#0f131b", borderRadius: "8px", padding: "6px",
        boxShadow: "0 10px 26px rgba(0,0,0,.4)", maxHeight: "50vh", overflow: "auto"
      });
      m.hidden = true;
      document.body.appendChild(m);
      menuRef.current = m;
      dbg("created ref menu host");
    }
    return menuRef.current;
  }
  function hideMenu() {
    const m = menuRef.current;
    if (!m || m.hidden) return;
    if (m._onDocDown) document.removeEventListener("mousedown", m._onDocDown, true);
    if (m._onKey) window.removeEventListener("keydown", m._onKey, true);
    window.removeEventListener("scroll", hideMenu, true);
    window.removeEventListener("resize", hideMenu, true);
    m.hidden = true;
  }
  function openChoiceMenu(titleText, options, onPick) {
    if (!Array.isArray(options) || !options.length) return;
    const m = ensureMenu();
    m.innerHTML = "";

    const header = document.createElement("div");
    Object.assign(header.style, { display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "6px" });

    const title = document.createElement("div");
    title.textContent = titleText;
    Object.assign(title.style, { fontSize: "12px", opacity: 0.75 });

    const close = document.createElement("button");
    close.textContent = "×";
    Object.assign(close.style, {
      border: "none", background: "transparent", color: "#eaeff4",
      fontSize: "16px", lineHeight: "16px", cursor: "pointer", padding: "2px 4px"
    });
    close.addEventListener("click", () => { hideMenu(); requestAnimationFrame(focusGrid); });

    header.appendChild(title);
    header.appendChild(close);
    m.appendChild(header);

    options.forEach(opt => {
      const b = document.createElement("button");
      b.textContent = opt;
      Object.assign(b.style, {
        display: "block", width: "100%", textAlign: "left",
        padding: "6px 8px", background: "transparent",
        color: "#eaeff4", border: "none", cursor: "pointer", borderRadius: "6px"
      });
      b.addEventListener("mouseenter", () => b.style.background = "rgba(255,255,255,0.06)");
      b.addEventListener("mouseleave", () => b.style.background = "transparent");
      b.addEventListener("click", () => { onPick(opt); hideMenu(); requestAnimationFrame(focusGrid); });
      m.appendChild(b);
    });

    m.hidden = false;
    m.style.left = `${(window.innerWidth - 320) / 2}px`;
    m.style.top  = `${(window.innerHeight - 260) / 2}px`;
    m.style.width = "320px";

    m._onDocDown = (ev) => { if (!m.contains(ev.target)) { hideMenu(); requestAnimationFrame(focusGrid); } };
    document.addEventListener("mousedown", m._onDocDown, true);
    m._onKey = (ev) => { const k = ev.key; if (k === "Escape" || k === "Tab" || k.startsWith("Arrow")) hideMenu(); };
    window.addEventListener("keydown", m._onKey, true);
    window.addEventListener("scroll", hideMenu, true);
    window.addEventListener("resize", hideMenu, true);
  }
  async function openRefMenu(field, rowIndex) {
    try {
      const d = refDetail[field] || {};
      dbg("F1 menu → fetch options", { field, rowIndex, project, nounType, detail: d });
      const t0 = performance.now();
      const { options } = await GET(`/grid/ref_options/${encodeURIComponent(project)}/${encodeURIComponent(nounType)}/${encodeURIComponent(field)}`);
      const dt = (performance.now() - t0).toFixed(1);
      const opts = Array.isArray(options) ? options : [];
      dbg("F1 menu: options", { count: opts.length, sample: opts.slice(0, 10), ms: dt, target: d.reference_noun, pid: d.target_primary_id });
      if (!opts.length) { setStatus("No options for reference"); return; }
      setGridState(prev => {
        const nextRows = [...prev.rows];
        nextRows[rowIndex] = { ...(nextRows[rowIndex] || {}), [field]: opts[0] }; // will be replaced by click handler
        return { ...prev, rows: nextRows };
      });
      openChoiceMenu(field, opts, (opt) => {
        setGridState(prev => {
          const nextRows = [...prev.rows];
          nextRows[rowIndex] = { ...(nextRows[rowIndex] || {}), [field]: opt };
          return { ...prev, rows: nextRows };
        });
      });
    } catch (e) {
      setStatus(`Ref options error: ${e.message || e}`);
      dbg("F1 menu error", e);
    }
  }
  function openRetestMenu(rowIndex) {
    if (!schemaPrimary || !retestIDs.length) return;
    dbg("F1 retest menu →", { rowIndex, count: retestIDs.length, sample: retestIDs.slice(0, 8) });
    openChoiceMenu("Select prior ID", retestIDs, (opt) => {
      setGridState(prev => {
        const nextRows = [...prev.rows];
        nextRows[rowIndex] = { ...(nextRows[rowIndex] || {}), [schemaPrimary]: opt };
        return { ...prev, rows: nextRows };
      });
    });
  }

  // size watcher
  useEffect(() => {
    const el = hostRef.current; if (!el) return;
    const measure = () => {
      const r = el.getBoundingClientRect();
      const d = { w: Math.max(240, r.width|0), h: Math.max(240, r.height|0) };
      setDims(d);
      DEBUG && console.debug("[grid] measure", d);
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    window.addEventListener("resize", measure);
    return () => { ro.disconnect(); window.removeEventListener("resize", measure); };
  }, []);

  // readonly = underscores + autogenerated primary ID + Picture adjectives
  const readOnlyCols = useMemo(() => {
    const s = new Set(headers.filter(h => h.startsWith("_")));
    if (schemaPrimary && autoGen) s.add(schemaPrimary);
    pictureCols.forEach(p => s.add(p));
    return s;
  }, [headers, schemaPrimary, autoGen, pictureCols]);

  // helpers
  const isRefField = useCallback((key) => refCols.has(key), [refCols]);

  // reference options cache
  const [refOptionsMap, setRefOptionsMap] = useState(new Map()); // Map<field, Set<string>>
  async function ensureRefOptions(field) {
    if (!isRefField(field)) return new Set();
    const cached = refOptionsMap.get(field);
    if (cached) return cached;
    const { options } = await GET(`/grid/ref_options/${encodeURIComponent(project)}/${encodeURIComponent(nounType)}/${encodeURIComponent(field)}`);
    const set = new Set((options || []).map(String));
    setRefOptionsMap(prev => new Map(prev).set(field, set));
    return set;
  }

  // columns
  const columns = useMemo(() => {
    if (!headers || !headers.length) return [];
    
    const count = Math.max(1, headers.length);
    const usable = Math.max(200, dims.w - 64);
    const per = Math.max(120, Math.floor(usable / count));
    
    const cols = headers.map(h => ({ 
      id: h, 
      title: h, 
      width: per,
      // Add these for extra safety
      resizable: true,
      sortable: false
    }));
    
    DEBUG && console.debug("[grid] columns", cols.map(c => ({ title: c.title, width: c.width })));
    return cols;
  }, [headers, dims.w]);

  // ---- derive readiness + safeColumns ----
  const ready = Array.isArray(headers) && headers.length > 0 && Array.isArray(rows) && !loading;
  const safeColumns = ready ? columns : [{ id: "__placeholder__", title: "__", width: 120 }];

  // preview JSON
  useEffect(() => { $("#json-preview").textContent = pretty({ headers, rows }); }, [headers, rows]);

  // inputs
  useEffect(() => {
    const onP = (e) => { dbg("project input:", e.target.value); setProject(e.target.value); };
    const onV = (e) => { dbg("verb-group input:", e.target.value); setVerbGroup(e.target.value); };
    const onN = (e) => { dbg("noun-type input:", e.target.value); setNounType(e.target.value); };
    $("#project")?.addEventListener("input", onP);
    $("#verb-group")?.addEventListener("input", onV);
    $("#noun-type")?.addEventListener("input", onN);
    return () => {
      $("#project")?.removeEventListener("input", onP);
      $("#verb-group")?.removeEventListener("input", onV);
      $("#noun-type")?.removeEventListener("input", onN);
    };
  }, []);

  // runs
  const refreshRuns = useCallback(async () => {
    try {
      dbg("refreshRuns →", { project, verbGroup });
      const t0 = performance.now();
      const res = await GET(`/grid/runs/${encodeURIComponent(project)}/${encodeURIComponent(verbGroup)}`);
      const ms = (performance.now() - t0).toFixed(1);
      const list = Array.isArray(res.runs) ? res.runs : [];
      dbg("runs:", { count: list.length, first: list[0], ms });
      setRuns(list);
      setRunId(prev => (prev && list.includes(prev)) ? prev : (list[0] || ""));
      setStatus(list.length ? `Found ${list.length} runs` : "No runs");
    } catch (e) {
      console.error(e); setRuns([]); setRunId(""); setStatus(`Run list error: ${e.message || e}`);
    }
  }, [project, verbGroup]);
  useEffect(() => { refreshRuns(); }, [refreshRuns]);

  // run select UI (and shutdown grid on change)
  useEffect(() => {
    const sel = /** @type {HTMLSelectElement|null} */ ($("#run"));
    if (!sel) return;

    const onChange = (e) => {
      dbg("run select change:", e.target.value);
      hideMenu();
      $("#save").disabled = true;
      setSelection(undefined);
      // hard reset component + clear unified state
      setResetKey(prev => prev + 1);
      setGridState({
        headers: [],
        rows: [],
        schemaPrimary: "",
        autoGen: false,
        pictureCols: new Set(),
        refCols: new Set(),
        refDetail: {},
        retestIDs: [],
      });
      setStatus("Run changed — click Load to open.");
      setRunId(e.target.value);
    };

    sel.replaceChildren();
    for (const id of runs) { const o = document.createElement("option"); o.value = o.textContent = id; sel.appendChild(o); }
    sel.value = runId || "";

    sel.addEventListener("change", onChange);
    return () => sel.removeEventListener("change", onChange);
  }, [runs, runId]);

  // history
  const hist = useHistory();

  // ---- LOAD (atomic setGridState once) ----
  const loadOne = useCallback(async () => {
    if (!project || !verbGroup || !runId) {
      dbg("loadOne guard fail", { project, verbGroup, runId });
      return;
    }
    const seq = ++loadSeq.current;
    setLoading(true);
    setStatus("Loading...");
    setSelection(undefined);
    hideMenu();
    hist.reset();
    $("#save").disabled = true;

    // Clear state immediately and increment reset key
    setResetKey(prev => prev + 1);
    setGridState({
      headers: [],
      rows: [],
      schemaPrimary: "",
      autoGen: false,
      pictureCols: new Set(),
      refCols: new Set(),
      refDetail: {},
      retestIDs: [],
    });

    try {
      dbg("loadOne →", { project, verbGroup, runId, seq });
      const t0 = performance.now();

      const [mainData, nounInfo, refInfo, retestInfo] = await Promise.all([
        GET(`/grid/load/${encodeURIComponent(project)}/${encodeURIComponent(verbGroup)}/${encodeURIComponent(runId)}`),
        GET(`/grid/noun_info/${encodeURIComponent(project)}/${encodeURIComponent(nounType)}`),
        GET(`/grid/reference_adjectives/${encodeURIComponent(project)}/${encodeURIComponent(nounType)}`),
        GET(`/grid/retest_options/${encodeURIComponent(project)}/${encodeURIComponent(verbGroup)}/${encodeURIComponent(runId)}`)
      ]);

      if (seq !== loadSeq.current) { 
        dbg("loadOne aborted (seq mismatch)"); 
        setLoading(false);
        return; 
      }

      const msLoad = (performance.now() - t0).toFixed(1);
      dbg(`All data fetched in ${msLoad}ms`);

      // Process data...
      let hs = [], rs = [];
      if (mainData && Array.isArray(mainData.headers) && Array.isArray(mainData.rows)) {
        hs = mainData.headers;
        rs = mainData.rows;
      } else if (Array.isArray(mainData)) {
        hs = Array.from(new Set(mainData.flatMap(r => Object.keys(r || {}))));
        rs = mainData;
      }

      const primary = nounInfo?.primary_id || "";
      const schemaOrder = Array.isArray(nounInfo?.headers_from_schema) ? nounInfo.headers_from_schema : [];
      const ag = !!nounInfo?.autogenerate_id;
      const picList = Array.isArray(nounInfo?.picture_fields) ? nounInfo.picture_fields : [];
      const refNames = Array.isArray(refInfo?.reference_fields) ? refInfo.reference_fields : [];
      const refDets = refInfo?.detail || {};
      const retestOpts = Array.isArray(retestInfo?.options) ? retestInfo.options : [];

      const ordered = orderHeadersByRules(hs, schemaOrder, primary);

      // Validate we have valid data before setting state
      if (!ordered.length) {
        setStatus("No columns found in data");
        setLoading(false);
        return;
      }

      // Single atomic update
      setGridState({
        headers: ordered,
        rows: rs,
        schemaPrimary: primary,
        autoGen: ag,
        pictureCols: new Set(picList),
        refCols: new Set(refNames),
        refDetail: refDets,
        retestIDs: retestOpts,
      });

      setStatus(`Loaded ${rs.length} rows • ${ordered.length} cols`);
      $("#save").disabled = false;

      // Set selection after a brief delay to ensure grid is rendered
      setTimeout(() => {
        if (ordered.length > 0 && rs.length > 0) {
          setSelection({
            current: { cell: [0, 0], range: { x: 0, y: 0, width: 1, height: 1 } },
          });
        }
        focusGrid();
      }, 50);

    } catch (e) {
      if (seq !== loadSeq.current) return;
      console.error("Error during load sequence:", e);
      setGridState({
        headers: [],
        rows: [],
        schemaPrimary: "",
        autoGen: false,
        pictureCols: new Set(),
        refCols: new Set(),
        refDetail: {},
        retestIDs: [],
      });
      $("#save").disabled = true;
      setStatus(`Load error: ${e.message || "Failed to fetch data"}`);
    } finally {
      if (seq === loadSeq.current) setLoading(false);
    }
  }, [project, verbGroup, runId, nounType, focusGrid, hist]);

  // ---- SAVE ----
  const save = useCallback(async () => {
    if (!project || !verbGroup || !runId) return;
    try {
      const withRun = rows.map(r => ({ ...r, _runID: runId }));
      dbg("save →", { rows: withRun.length, headers: headers.length });

      console.log("[SAVE trigger]", {
        endpoint: `/gui/grid/save/${encodeURIComponent(project)}/${encodeURIComponent(verbGroup)}/${encodeURIComponent(runId)}`,
        project, verbGroup, runId, headers,
        rowsPreview: withRun.slice(0, 3),
        totalRows: withRun.length
      });

      const t0 = performance.now();
      const res = await POST(
        `/gui/grid/save/${encodeURIComponent(project)}/${encodeURIComponent(verbGroup)}/${encodeURIComponent(runId)}`,
        { headers, rows: withRun }
      );
      dbg("save result", res);

      setGridState(prev => ({ ...prev, rows: withRun }));
      const ms = (performance.now() - t0).toFixed(1);
      setStatus(`Saved ${withRun.length} rows [${ms}ms]`);
    } catch (e) {
      console.error(e);
      setStatus(`Save error: ${e.message || e}`);
      dbg("save error", e);
    }
  }, [project, verbGroup, runId, headers, rows]);

  // ---- ADD ROW ----
  const addRow = useCallback(() => {
    const blank = Object.fromEntries(headers.map(h => [h, ""]));
    if (headers.includes("_runID")) blank["_runID"] = runId;
    dbg("addRow", blank);
    setGridState(prev => ({ ...prev, rows: [...prev.rows, blank] }));
    requestAnimationFrame(() => focusGrid());
  }, [headers, runId, focusGrid]);

  // ---- GENERATE ID (F2) ----
  const generateId = useCallback(async () => {
    const pid = schemaPrimary
      || headers.find(h => /_id$/i.test(h) && !h.startsWith("_"))
      || headers.find(h => !h.startsWith("_"))
      || headers[0];
    if (!pid) { dbg("generateId: no pid"); return; }

    const existing = Array.from(new Set(rows.map(r => String(r?.[pid] || "")).filter(Boolean)));
    dbg("generateId →", { pid, existingCount: existing.length, sample: existing.slice(0, 6) });
    let id = "";
    try {
      const t0 = performance.now();
      const r = await POST(`/grid/generate_id/${encodeURIComponent(project)}/${encodeURIComponent(nounType)}`, { existing_ids: existing });
      id = r?.id || "";
      dbg("generateId ok", { id, ms: (performance.now()-t0).toFixed(1) });
    } catch (e) {
      id = `ID${Date.now()}`; dbg("generateId fallback", e);
    }
    if (!id) return;

    let idx = rows.findIndex(r => !String(r?.[pid] || "").trim());
    const patch = (r) => ({ ...r, [pid]: id, ...(headers.includes("_runID") ? { _runID: runId } : {}) });
    setGridState(prev => {
      const nextRows = [...prev.rows];
      if (idx >= 0) {
        nextRows[idx] = patch(nextRows[idx] || {});
      } else {
        const blank = Object.fromEntries(headers.map(h => [h, ""]));
        nextRows.push(patch(blank));
      }
      return { ...prev, rows: nextRows };
    });
    setStatus(`Generated ID: ${id}`);
    requestAnimationFrame(() => focusGrid());
  }, [headers, rows, runId, project, nounType, schemaPrimary, focusGrid]);

  // ---- COPY (Ctrl+C) fallback from selection ----
  const buildTSVFromSelection = () => {
    const sel = selection?.current?.range;
    if (!sel) return "";
    const { x, y, width, height } = sel;
    const xs = []; for (let dx = 0; dx < width; dx++) xs.push(x + dx);
    const ys = []; for (let dy = 0; dy < height; dy++) ys.push(y + dy);
    const tsv = ys.map(rowIdx => xs.map(colIdx => {
      const key = safeColumns[colIdx]?.title ?? "";
      const v = rows[rowIdx]?.[key];
      const s = v == null ? "" : String(v);
      return s.includes("\t") || s.includes("\n") ? `"${s.replace(/"/g,'""')}"` : s;
    }).join("\t")).join("\n");
    dbg("copy selection", { region: { x, y, width, height }, tsvPreview: tsv.slice(0, 200) });
    return tsv;
  };

  // ---- Buttons ----
  useEffect(() => {
    const onRefresh = () => refreshRuns();
    const onLoad = () => loadOne();
    const onSave = () => save();
    const onAdd = () => addRow();
    const onGen = () => generateId();

    $("#refresh-runs")?.addEventListener("click", onRefresh);
    $("#load")?.addEventListener("click", onLoad);
    $("#save")?.addEventListener("click", onSave);
    $("#add-row")?.addEventListener("click", onAdd);
    $("#gen-id")?.addEventListener("click", onGen);
    $("#copy-json")?.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(pretty({ headers, rows })); setStatus("Copied JSON"); dbg("copy-json clicked"); }
      catch (e) { setStatus("Copy failed"); dbg("copy-json error", e); }
    });
    return () => {
      $("#refresh-runs")?.removeEventListener("click", onRefresh);
      $("#load")?.removeEventListener("click", onLoad);
      $("#save")?.removeEventListener("click", onSave);
      $("#add-row")?.removeEventListener("click", onAdd);
      $("#gen-id")?.removeEventListener("click", onGen);
    };
  }, [headers, rows, refreshRuns, loadOne, save, addRow, generateId]);

  // ---- Selection ----
  const onGridSelectionChange = useCallback((sel) => {
    setSelection(sel);
    const cell = sel?.current?.cell;
    const rng  = sel?.current?.range;
    dbg("selection change", { cell, range: rng });
    if (menuRef.current && !menuRef.current.hidden) hideMenu();
  }, []);

  // ---- Editing (locks) ----
  const onCellEdited = useCallback(async (cell, newValue) => {
    const [col, r] = cell;
    if (newValue.kind !== GridCellKind.Text) return;

    const key = safeColumns[col]?.title ?? "";
    const incoming = String(newValue.data ?? "");

    if (
      readOnlyCols.has(key) ||
      (schemaPrimary && autoGen && key === schemaPrimary) ||
      pictureCols.has(key)
    ) {
      dbg("edit blocked (locked field)", { key, row: r, autoGen, schemaPrimary, isPicture: pictureCols.has(key) });
      return;
    }

    hist.beginBatch(rowsRef.current);

    if (isRefField(key) && incoming) {
      try {
        const allowed = await ensureRefOptions(key);
        if (!allowed.has(incoming)) {
          setStatus(`Invalid ${key} value. Press F1 for allowed options.`);
          dbg("ref validation failed", { key, incoming, allowedSample: Array.from(allowed).slice(0, 8) });
          hist.scheduleCommit(() => rowsRef.current);
          return;
        }
      } catch (e) {
        setStatus(`Ref validation error: ${e.message || e}`);
        hist.scheduleCommit(() => rowsRef.current);
        return;
      }
    }

    const val = incoming;
    dbg("edit", { row: r, col, key, valPreview: val.slice(0, 120) });
    setGridState(prev => {
      const next = [...prev.rows];
      const row = { ...(next[r] || {}) };
      row[key] = val;
      next[r] = row;
      return { ...prev, rows: next };
    });

    hist.scheduleCommit(() => rowsRef.current);
  }, [safeColumns, readOnlyCols, isRefField, ensureRefOptions, hist, schemaPrimary, autoGen, pictureCols]);

  // ---- Shortcuts ----
  useEffect(() => {
    const onKey = async (e) => {
      const k = e.key.toLowerCase();

      if (e.ctrlKey && k === "c" && isGridFocused()) {
        const tsv = buildTSVFromSelection();
        if (tsv) { e.preventDefault(); await navigator.clipboard.writeText(tsv); setStatus("Copied"); }
        return;
      }

      if (e.ctrlKey && k === "s") { e.preventDefault(); dbg("ctrl+s"); if (!loading) save(); }
      else if (e.ctrlKey && k === "z") { e.preventDefault(); dbg("ctrl+z"); setGridState(prev => ({ ...prev, rows: hist.undo(rowsRef.current) })); }
      else if (e.ctrlKey && k === "y") { e.preventDefault(); dbg("ctrl+y"); setGridState(prev => ({ ...prev, rows: hist.redo(rowsRef.current) })); }
      else if (k === "f2") { e.preventDefault(); dbg("f2 generateId"); generateId(); }
      else if (k === "f1") {
        const cell = selection?.current?.cell;
        if (!cell) return;
        const [cx, cy] = cell;
        const field = safeColumns[cx]?.title ?? "";
        const onPrimary = schemaPrimary && field === schemaPrimary;

        if (onPrimary && retestIDs.length) {
          e.preventDefault();
          openRetestMenu(cy);
          return;
        }
        if (refCols.has(field)) {
          e.preventDefault();
          openRefMenu(field, cy);
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selection, safeColumns, refCols, schemaPrimary, retestIDs, save, generateId, hist, loading]);

  // ---- Render ----
  const getCellContent = useCallback(([col, row]) => {
    const key = safeColumns[col]?.title ?? "";
    const val = rows[row]?.[key];
    const ro =
      readOnlyCols.has(key) ||
      (schemaPrimary && autoGen && key === schemaPrimary) ||
      pictureCols.has(key);
    return {
      kind: GridCellKind.Text,
      data: val == null ? "" : String(val),
      displayData: val == null ? "" : String(val),
      allowOverlay: !ro,
      readonly: ro,
    };
  }, [safeColumns, rows, readOnlyCols, schemaPrimary, autoGen, pictureCols]);

  // Add this validation before the return
  const isValidForRender = ready && columns.length > 0 && !loading;
  
  return React.createElement(
    "div",
    { ref: hostRef, id: "grid-host", onClick: () => { focusGrid(); dbg("host click → focus"); } },
    React.createElement(ErrorBoundary, null,
      // Only render DataEditor when we have valid data
      isValidForRender ? React.createElement(DataEditor, {
        key: `editor-${resetKey}-${runId || "empty"}-${headers.length}`, // More specific key
        ref: editorRef,
        columns: columns, // Use the computed columns, not safeColumns
        rows: rows.length,
        getCellContent,
        onCellEdited,
        onGridSelectionChange,
        ...(selection ? { gridSelection: selection } : {}),
        rowMarkers: "both",
        smoothScrollX: true,
        smoothScrollY: true,
        width: dims.w,
        height: dims.h,
      }) : React.createElement("div", { 
        className: "muted", 
        style: { 
          padding: "8px", 
          color: "#888",
          textAlign: "center",
          lineHeight: `${dims.h}px`
        } 
      }, loading ? "Loading grid..." : "No data loaded")
    )
  );
}



// bootstrap
function mount() {
  const root = createRoot(document.getElementById("grid-host"));
  root.render(React.createElement(App));
}
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mount, { once: true });
} else {
  mount();
}
