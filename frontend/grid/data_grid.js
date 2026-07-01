// frontend/grid/data_grid.js — bundled by esbuild to static/lib/data_grid.js (ESM).
// Parameterized, reusable Data Grid with monotonic-token race guards on load/save.
// React + Glide are externalized to the vendor globals (window.React / window.GlideDataGrid)
// via frontend/vendor/shims/* — bundled once, no CDN, no second React (Phase 6).

import React, {
  useEffect,
  useMemo,
  useState,
  useCallback,
  useRef,
  useImperativeHandle,
  forwardRef,
  useLayoutEffect,
} from "react";

import { createRoot } from "react-dom/client";

import DataEditor, {
  GridCellKind
} from "@glideapps/glide-data-grid";

// ---------- tiny helpers ----------
// Debug control — OFF by default (opt in at runtime via window.__GRID_DEBUG__ = true).
const DEBUG_ENABLED = (typeof window !== "undefined" && window.__GRID_DEBUG__ === true);

// Debug helper that respects the flag
const debug = DEBUG_ENABLED ? console.debug.bind(console) : () => {};

const pretty = (x) => JSON.stringify(x, null, 2);
const enc = (s) => {
  const v = s == null ? "" : String(s);
  return encodeURIComponent(/^(null|undefined|none)$/i.test(v) ? "" : v);
};
const GET = async (u) => {
  debug("[grid][net][GET]", u);
  const r = await fetch(u);
  if (!r.ok) throw new Error(await r.text().catch(() => r.statusText));
  return r.json();
};
const POST = async (u, b) => {
  debug("[grid][net][POST]", u, b);
  const r = await fetch(u, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(b),
  });
  if (!r.ok) throw new Error(await r.text().catch(() => r.statusText));
  return r.json().catch(() => ({}));
};

// ---------- endpoints (overridable) ----------
export const defaultEndpoints = {
  load: ({ project, verbGroup, runId }) =>
    `/grid/load/${enc(project)}/${enc(verbGroup)}/${enc(runId)}`,
  save: ({ project, verbGroup, runId }) =>
    `/gui/grid/save/${enc(project)}/${enc(verbGroup)}/${enc(runId)}`,
  nounInfo: ({ project, nounType }) =>
    `/grid/noun_info/${enc(project)}/${enc(nounType)}`,
  refAdjectives: ({ project, nounType }) =>
    `/grid/reference_adjectives/${enc(project)}/${enc(nounType)}`,
  durationAdjectives: ({ project, nounType }) =>
    `/grid/duration_adjectives/${enc(project)}/${enc(nounType)}`,
  retestOptions: ({ project, verbGroup, runId }) =>
    `/grid/retest_options/${enc(project)}/${enc(verbGroup)}/${enc(runId)}`,
  refOptions: ({ project, nounType, field }) =>
    `/grid/ref_options/${encodeURIComponent(project)}/${encodeURIComponent(nounType)}/${encodeURIComponent(field)}`,
  generateId: ({ project, nounType }) =>
    `/grid/generate_id/${enc(project)}/${enc(nounType)}`,
};

// Glide CSS now ships in static/lib/glide-vendor.css (linked by the node head_extra), bundled by
// esbuild from frontend/vendor/glide.js — no runtime CDN injection (ensureGlideStyles removed).

// ---------- duration (time-aware adjective) helpers ----------
// Parse a stored anchor value (a date/datetime field) to epoch ms, or null. ISO instants
// (with/without time, fractional seconds, trailing Z) go through Date.parse; the common
// declared date-format tokens are parsed at UTC midnight so a day-granular field still works.
const _FMT_TOKEN = {
  "mmddyy":     /^(\d{2})(\d{2})(\d{2})$/,        // -> m,d,y2
  "mmddyyyy":   /^(\d{2})(\d{2})(\d{4})$/,        // -> m,d,Y
  "yyyymmdd":   /^(\d{4})(\d{2})(\d{2})$/,        // -> Y,m,d
  "yyyy-mm-dd": /^(\d{4})-(\d{2})-(\d{2})$/,      // -> Y,m,d
};
function parseInstant(value, meta) {
  const s = (value == null ? "" : String(value)).trim();
  if (!s) return null;
  const fmt = meta && meta.format;
  const re = fmt && _FMT_TOKEN[fmt];
  if (re) {
    const m = s.match(re);
    if (m) {
      let Y, Mo, D;
      if (fmt === "mmddyy")        { Mo = +m[1]; D = +m[2]; Y = 2000 + +m[3]; }
      else if (fmt === "mmddyyyy") { Mo = +m[1]; D = +m[2]; Y = +m[3]; }
      else                         { Y = +m[1]; Mo = +m[2]; D = +m[3]; }  // yyyymmdd / yyyy-mm-dd
      const ms = Date.UTC(Y, Mo - 1, D);
      return Number.isNaN(ms) ? null : ms;
    }
  }
  // ISO interchange form (Date.parse: bare date => UTC midnight; "T"/space datetime accepted).
  const ms = Date.parse(s.replace(" ", "T"));
  return Number.isNaN(ms) ? null : ms;
}

// Humanize a signed duration in ms to a compact string (sign preserved). `unit` clamps the
// granularity; "auto" shows the two largest sensible units down to seconds for short spans.
function humanizeDuration(ms, unit = "auto") {
  const neg = ms < 0;
  let s = Math.floor(Math.abs(ms) / 1000);
  const d = Math.floor(s / 86400); s -= d * 86400;
  const h = Math.floor(s / 3600);  s -= h * 3600;
  const m = Math.floor(s / 60);    s -= m * 60;
  const sec = s;
  let parts;
  if (unit === "days")          parts = [`${d}d`];
  else if (unit === "hours")    parts = [d ? `${d}d` : null, `${h}h`].filter(Boolean);
  else if (unit === "minutes")  parts = [d ? `${d}d` : null, h ? `${h}h` : null, `${m}m`].filter(Boolean);
  else if (unit === "seconds")  parts = [d ? `${d}d` : null, h ? `${h}h` : null, m ? `${m}m` : null, `${sec}s`].filter(Boolean);
  else { // auto
    if (d > 0)      parts = [`${d}d`, `${h}h`];
    else if (h > 0) parts = [`${h}h`, `${m}m`];
    else if (m > 0) parts = [`${m}m`, `${sec}s`];
    else            parts = [`${sec}s`];
  }
  return (neg ? "-" : "") + parts.join(" ");
}

// Render one Duration cell from its two bound anchors + the (server-anchored) clock.
function formatDurationCell(row, cfg, nowMs) {
  if (!cfg) return "";
  const startMs = parseInstant(row?.[cfg.start_field], cfg.start_meta);
  const endMs   = parseInstant(row?.[cfg.end_field],   cfg.end_meta);
  const unit = cfg.unit || "auto";
  const mode = cfg.mode || "elapsed";
  const pieces = [];
  if (mode === "elapsed" || mode === "both") {
    pieces.push(startMs == null ? "—" : `${humanizeDuration(nowMs - startMs, unit)} in`);
  }
  if (mode === "remaining" || mode === "both") {
    if (endMs == null) pieces.push("—");
    else {
      const rem = endMs - nowMs;
      pieces.push(rem >= 0 ? `${humanizeDuration(rem, unit)} left`
                           : `OVERDUE ${humanizeDuration(-rem, unit)}`);
    }
  }
  return pieces.join("  ·  ");
}

// ---------- error boundary ----------
class ErrorBoundary extends React.Component {
  constructor(p) {
    super(p);
    this.state = { hasError: false };
  }
  static getDerivedStateFromError() {
    return { hasError: true };
  }
  componentDidCatch(error, info) {
    console.error("Grid crash:", error, info);
    this.props.onStatus?.("Grid crashed - see console.");
  }
  render() {
    return this.state.hasError
      ? React.createElement(
          "div",
          { className: "muted", style: { padding: "8px", color: "#ff8a8a" } },
          "The grid encountered an error."
        )
      : this.props.children;
  }
}

// ---------- history batching (FIXED) ----------
function useHistory() {
  const ref = useRef({
    stack: [],
    idx: -1,
    batching: false,
    commitTimer: null,
    lastSig: null,
  });
  const snapshot = (rows) => JSON.parse(JSON.stringify(rows));
  const sign = (rows) => JSON.stringify(rows);

  const _push = (rows) => {
    const h = ref.current;
    const sig = sign(rows);
    if (sig === h.lastSig) return;
    h.stack = h.stack.slice(0, h.idx + 1);
    h.stack.push(snapshot(rows));
    h.idx = h.stack.length - 1;
    h.lastSig = sig;
    debug("[grid][hist] push idx", h.idx, "len", h.stack.length);
  };

  const beginBatch = (rows) => {
    const h = ref.current;
    if (!h.batching) {
      debug("[grid][hist] beginBatch");
      _push(rows);
      h.batching = true;
    }
    if (h.commitTimer) {
      clearTimeout(h.commitTimer);
      h.commitTimer = null;
    }
  };

  const scheduleCommit = (getRows, delay = 350) => {
    const h = ref.current;
    if (h.commitTimer) clearTimeout(h.commitTimer);
    h.commitTimer = setTimeout(() => {
      try {
        if (h.batching && typeof getRows === "function") _push(getRows());
      } finally {
        h.batching = false;
        h.commitTimer = null;
        debug("[grid][hist] commit");
      }
    }, delay);
  };

  const undo = (curr) => {
    const h = ref.current;
    if (h.idx <= 0) return curr;
    h.idx--;
    debug("[grid][hist] undo → idx", h.idx);
    const rows = snapshot(h.stack[h.idx]);
    h.lastSig = sign(rows);
    return rows;
  };

  const redo = (curr) => {
    const h = ref.current;
    if (h.idx >= h.stack.length - 1) return curr;
    h.idx++;
    debug("[grid][hist] redo → idx", h.idx);
    const rows = snapshot(h.stack[h.idx]);
    h.lastSig = sign(rows);
    return rows;
  };

  const reset = () => {
    const h = ref.current;
    if (h.commitTimer) clearTimeout(h.commitTimer);
    ref.current = {
      stack: [],
      idx: -1,
      batching: false,
      commitTimer: null,
      lastSig: null,
    };
    debug("[grid][hist] reset");
  };

  return { beginBatch, scheduleCommit, undo, redo, reset };
}

// ---------- utilities ----------
// Find nearest scrollable ancestor; fall back to document.body.
function getScrollParent(el) {
  let node = el;
  while (node && node !== document.body) {
    const cs = getComputedStyle(node);
    if (/(auto|scroll|overlay)/.test(cs.overflowY) ||
        /(auto|scroll|overlay)/.test(cs.overflowX) ||
        /(auto|scroll|overlay)/.test(cs.overflow)) {
      return node;
    }
    node = node.parentElement;
  }
  return document.body;
}

// Create/move #portal under the correct container without clipping or blocking events.
function ensurePortal(anchorEl) {
  const container = getScrollParent(anchorEl || document.body);

  let p = document.getElementById("portal");
  if (!p) {
    p = document.createElement("div");
    p.id = "portal";
  }

  // Attach to the scroll container (or body)
  if (p.parentElement !== container) container.appendChild(p);

  // Make sure the container can position absolutely-positioned children
  const cs = getComputedStyle(container);
  if (container !== document.body && cs.position === "static") {
    container.style.position = "relative";
  }

  // IMPORTANT: Do NOT set width/height 0 or overflow hidden or pointer-events none.
  // Just anchor it; the overlay will position itself.
  Object.assign(p.style, {
    position: container === document.body ? "fixed" : "absolute",
    top: "0",
    left: "0",
    zIndex: "2147483647",
  });
}

function orderHeadersByRules(hs, schemaOrder, primaryFromSchema) {
  const base = Array.from(new Set(hs));
  const primary =
    primaryFromSchema ||
    base.find((h) => /_id$/i.test(h) && !h.startsWith("_")) ||
    base.find((h) => !h.startsWith("_")) ||
    base[0] ||
    "";
  let ordered =
    schemaOrder && schemaOrder.length
      ? schemaOrder.filter((h) => base.includes(h))
      : base;
  for (const h of base) if (!ordered.includes(h)) ordered.push(h);
  if (primary) ordered = [primary, ...ordered.filter((h) => h !== primary)];
  if (ordered.includes("_runID"))
    ordered = ordered.filter((h) => h !== "_runID").concat(["_runID"]);
  return ordered;
}

const PLACEHOLDER_COLUMNS = Object.freeze([
  { id: "__loading__", title: "Loading...", width: 120, resizable: false, sortable: false },
]);

// ---------- Data Grid App ----------
export const DataGridApp = forwardRef(function DataGridApp(props, ref) {
  const {
    project,
    verbGroup,
    runId,
    nounType = null,

    endpoints = defaultEndpoints,
    readOnlyCols = ["_runID"],
    autosaveMs = 800,
    showToolbar = true,

    enableF1Menus = true,
    enableRetestMenus = true,
    enableCopyPaste = true,

    onStatus,
    onSaved,
    onError,

    initialData,
  } = props;

  // --- stabilize external refs ---
  const endpointsRef = useRef(endpoints);
  const onStatusRef = useRef(onStatus);
  const onErrorRef = useRef(onError);
  const onSavedRef = useRef(onSaved);

  useEffect(() => {
    endpointsRef.current = endpoints;
    onStatusRef.current = onStatus;
    onErrorRef.current = onError;
    onSavedRef.current = onSaved;
  }, [endpoints, onStatus, onError, onSaved]);

  // explicit phase machine: idle -> loading -> ready
  const [phase, setPhase] = useState("idle");

  // sequence token for loads
  const loadSeq = useRef(0);
  const readySeqRef = useRef(null);

  // remount key when run changes
  const [resetKey, setResetKey] = useState(0);
  const lastRunRef = useRef(runId);

  // ensure overlay portal (Glide CSS ships in glide-vendor.css now)
  useEffect(() => {
    debug("[grid][init] ensurePortal");
    Promise.resolve().then(() => ensurePortal(hostRef.current));
  }, []);

  useEffect(() => {
    if (runId && runId !== lastRunRef.current) {
      setResetKey((p) => p + 1);
      lastRunRef.current = runId;
      debug("[grid][run] changed, resetKey++");
    }
  }, [runId]);

  // unified grid state
  const [gridState, setGridState] = useState(() => {
    const initHeaders =
      Array.isArray(initialData?.headers) && initialData.headers.length > 0
        ? initialData.headers
        : [];
    const initRows = Array.isArray(initialData?.rows) ? initialData.rows : [];
    debug("[grid][state] init", { initHeadersLen: initHeaders.length, initRowsLen: initRows.length });
    return {
      headers: initHeaders,
      rows: initRows,
      schemaPrimary: "",
      autoGen: false,
      pictureCols: new Set(),
      refCols: new Set(),
      durationCols: new Set(),
      durationCfg: {},
      retestIDs: [],
    };
  });

  const {
    headers,
    rows,
    schemaPrimary,
    autoGen,
    pictureCols,
    refCols,
    durationCols,
    durationCfg,
    retestIDs,
  } = gridState;

  // Server-anchored monotonic clock for Duration tickers: nowMs = serverMs + (perf.now() - perfMs).
  // We NEVER use the raw browser wall clock to decide a duration/overdue — only to interpolate
  // smoothly between server anchors (re-anchored on each load). serverMs=null ⇒ no anchor yet.
  const serverClockRef = useRef({ serverMs: null, perfMs: 0 });
  const anchoredNow = useCallback(() => {
    const c = serverClockRef.current;
    return c.serverMs == null ? Date.now() : c.serverMs + (performance.now() - c.perfMs);
  }, []);
  // Drives the per-second re-render of Duration cells (see the interval effect below).
  const [tick, setTick] = useState(0);

  // mirror rows for history batching
  const rowsRef = useRef(rows);
  useEffect(() => {
    rowsRef.current = rows;
  }, [rows]);

  // dims + selection
  const hostRef = useRef(null);
  const editorRef = useRef(null);
  const toolbarRef = useRef(null);
  const [dims, setDims] = useState({ w: 800, h: 480 });

  // Keep these in sync with Glide defaults
  const ROW_HEIGHT = 34;
  const HEADER_HEIGHT = 36;

  // selection (guarded)
  const [selection, setSelection] = useState(undefined);
  const selectionRef = useRef(undefined);
  const sameSelection = (a, b) => {
    const ac = a?.current, bc = b?.current;
    if (!ac && !bc) return true;
    if (!ac || !bc) return false;
    const acell = ac.cell || [], bcell = bc.cell || [];
    const ar = ac.range || {}, br = bc.range || {};
    return (
      acell[0] === bcell[0] &&
      acell[1] === bcell[1] &&
      ar.x === br.x && ar.y === br.y && ar.width === br.width && ar.height === br.height
    );
  };

  const focusGrid = useCallback(() => {
    try {
      editorRef.current?.focus({ preventScroll: true });
      debug("[grid][focus] editor focused");
    } catch {}
  }, []);

  // Measure width only; compute height from content (no feedback loop)
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    let mounted = true;
    let lastW = -1;
    let rafId = null;

    const recompute = () => {
      if (!mounted) return;
      if (rafId) cancelAnimationFrame(rafId);

      rafId = requestAnimationFrame(() => {
        if (!mounted) return;
        const w = Math.max(240, Math.round((host.parentElement || host).clientWidth));

        if (w !== lastW) {
          lastW = w;

          // Compute desired grid height from content
          const ROW_H = 28;          // Glide default-ish row height
          const HEADER_H = 34;       // column header strip
          const TOOLBAR_H = showToolbar ? 40 : 0;
          const PADDING = 8;

          const rowCount = Array.isArray(rows) ? rows.length : 0;
          const contentH = ROW_H * rowCount + HEADER_H + TOOLBAR_H + PADDING;

          // Limit to a reasonable chunk of viewport so a huge table doesn't explode the page
          const MAX_H = Math.floor(window.innerHeight * 0.85);

          setDims({
            w,
            h: Math.max(240, Math.min(contentH, MAX_H)),
          });

          debug("[grid][resize] setDims (width+content)", { w, h: Math.max(240, Math.min(contentH, MAX_H)) });
        }
      });
    };

    // Observe width changes
    const ro = new ResizeObserver(recompute);
    ro.observe(host.parentElement || host);

    // Initial compute
    recompute();

    return () => {
      mounted = false;
      if (rafId) cancelAnimationFrame(rafId);
      ro.disconnect();
    };
  }, [rows, showToolbar]);

  // one global so we can fully clean up between opens
  let __choiceMenu = null;

  function openChoiceMenu(title, opts, onPick) {
    // If menu is already open for same anchor, first tear it down
    if (__choiceMenu) {
      __choiceMenu.remove();
      __choiceMenu = null;
    }

    const wrap = document.createElement("div");
    wrap.className = "gims-choice-menu";
    wrap.setAttribute("role", "dialog");
    wrap.style.position = "fixed";         // not tied to scrolled parents
    wrap.style.top = "var(--cm-top, 120px)";
    wrap.style.left = "var(--cm-left, 120px)";
    wrap.style.zIndex = "999999";
    wrap.style.maxHeight = "50vh";
    wrap.style.width = "320px";
    wrap.style.overflow = "hidden";
    wrap.style.boxShadow = "0 8px 24px rgba(0,0,0,.35)";
    wrap.style.borderRadius = "10px";
    wrap.style.background = "var(--card, #101316)";
    wrap.style.color = "var(--ink, #eaeff4)";

    // header
    const hdr = document.createElement("div");
    hdr.style.display = "flex";
    hdr.style.justifyContent = "space-between";
    hdr.style.alignItems = "center";
    hdr.style.padding = "8px 10px";
    hdr.style.fontWeight = "600";
    hdr.textContent = title || "Select";
    const x = document.createElement("button");
    x.textContent = "×";
    x.style.fontSize = "18px";
    x.style.lineHeight = "1";
    x.style.color = "#fff";          // bright white on dark
    x.style.fontWeight = "700";      // thicker glyph
    x.style.opacity = "0.85";        // slight fade to match design
    x.onmouseenter = () => { x.style.opacity = "1"; };
    x.onmouseleave = () => { x.style.opacity = "0.85"; };
    x.style.background = "transparent";
    x.style.border = "none";
    x.style.cursor = "pointer";
    x.onclick = close;
    hdr.appendChild(x);

    // scrollable list (STOP wheel/scroll bubbling!)
    const list = document.createElement("div");
    list.style.maxHeight = "calc(50vh - 40px)";
    list.style.overflow = "auto";
    list.style.padding = "4px 0";

    // Prevent the menu’s scrolling from bubbling and triggering grid/page scroll hooks
    const stopAll = (e) => { e.stopPropagation(); };
    list.addEventListener("wheel", (e) => { e.stopPropagation(); }, { passive: true });
    list.addEventListener("touchmove", stopAll, { passive: false });
    list.addEventListener("scroll", stopAll);

    for (const v of opts) {
      const it = document.createElement("button");
      it.type = "button";
      it.textContent = String(v);
      it.style.display = "block";
      it.style.width = "100%";
      it.style.textAlign = "left";
      it.style.padding = "8px 10px";
      it.style.border = "none";
      it.style.background = "transparent";
      it.style.cursor = "pointer";
      it.style.color = "#fff"; // or "var(--ink, #fff)" if you want to keep the CSS var fallback
      it.style.fontSize = "14px";
      it.style.fontWeight = "500";
      it.style.letterSpacing = "0.25px";
      it.style.lineHeight = "1.4";
      it.style.fontFamily = "system-ui, sans-serif"; // clean default
      it.onmouseenter = () => (it.style.background = "rgba(255,255,255,.06)");
      it.onmouseleave = () => (it.style.background = "transparent");
      it.onclick = () => { onPick?.(v); close(); };
      list.appendChild(it);
    }

    wrap.appendChild(hdr);
    wrap.appendChild(list);
    document.body.appendChild(wrap);
    __choiceMenu = wrap;

    // Outside-click & Escape to close
    const onDocClick = (e) => {
      if (!wrap.contains(e.target)) close();
    };
    const onKey = (e) => {
      if (e.key === "Escape") close();
    };
    document.addEventListener("mousedown", onDocClick, true);
    document.addEventListener("keydown", onKey, true);

    // IMPORTANT: Do NOT close on window scroll/wheel – that caused your disappearing bug
    function close() {
      if (!__choiceMenu) return;
      document.removeEventListener("mousedown", onDocClick, true);
      document.removeEventListener("keydown", onKey, true);
      __choiceMenu.remove();
      __choiceMenu = null;

      // Ensure grid can catch F1 again without leaving cell
      try {
        editorRef?.current?.focus?.();
      } catch (e) {
        console.warn("[choiceMenu] Could not refocus grid:", e);
      }
    }
  }

  // columns
  const columns = useMemo(() => {
    if (!headers || !headers.length) {
      return PLACEHOLDER_COLUMNS;
    }
    const count = Math.max(1, headers.length);
    const usable = Math.max(200, dims.w - 64);
    const per = Math.max(120, Math.floor(usable / count));
    const cols = headers.map((h) => ({
      id: h,
      title: h,
      width: per,
      resizable: true,
      sortable: false,
    }));
    debug("[grid][cols]", cols.map(c => ({ title: c.title, width: c.width })));
    return cols;
  }, [headers, dims.w]);

  // readonly set
  const readOnlySet = useMemo(() => {
    const s = new Set(Array.isArray(headers) ? headers.filter((h) => h.startsWith("_")) : []);
    for (const k of readOnlyCols) s.add(k);
    if (schemaPrimary && autoGen) s.add(schemaPrimary);
    pictureCols.forEach((p) => s.add(p));
    durationCols.forEach((d) => s.add(d));  // virtual/derived — never user-editable
    return s;
  }, [headers, readOnlyCols, schemaPrimary, autoGen, pictureCols, durationCols]);

  const norm = (s) => String(s || "").trim().toLowerCase().replace(/[\s_-]+/g, " ");
  const refColsNorm = useMemo(() => new Set([...refCols].map(norm)), [refCols]);
  const isRefField = useCallback((key) => refColsNorm.has(norm(key)), [refColsNorm]);

  const durationColsNorm = useMemo(() => new Set([...durationCols].map(norm)), [durationCols]);
  const isDurationField = useCallback((key) => durationColsNorm.has(norm(key)), [durationColsNorm]);
  // Normalized-key lookup of each Duration column's binding config (mode/unit/anchors/formats).
  const durationCfgByNorm = useMemo(() => {
    const m = new Map();
    for (const [k, v] of Object.entries(durationCfg || {})) m.set(norm(k), v);
    return m;
  }, [durationCfg]);

  // One shared per-second ticker — mounted ONLY when Duration columns exist, paused when the
  // tab is hidden. Bumping `tick` re-creates getCellContent so Glide repaints the visible cells.
  useEffect(() => {
    if (!durationColsNorm.size) return;
    let id = null;
    const start = () => { if (id == null) id = setInterval(() => setTick((t) => t + 1), 1000); };
    const stop = () => { if (id != null) { clearInterval(id); id = null; } };
    const onVis = () => (document.hidden ? stop() : start());
    if (!document.hidden) start();
    document.addEventListener("visibilitychange", onVis);
    return () => { stop(); document.removeEventListener("visibilitychange", onVis); };
  }, [durationColsNorm]);

  // Cache of reference options per field (lowercased)
  const refOptionsCacheRef = useRef(new Map());
  const ensureRefOptions = useCallback(async (field) => {
    const name = String(field || "").toLowerCase();
    const cache = refOptionsCacheRef.current;
    if (cache.has(name)) return cache.get(name);

    const { options } = await GET(
      endpointsRef.current.refOptions({ project, nounType, field })
    );
    const set = new Set(Array.isArray(options) ? options.map(String) : []);
    cache.set(name, set);
    return set;
  }, [project, nounType]);

  // history (FIXED implementation)
  const hist = useHistory();

  // ---- LOAD ----
  const loadOne = useCallback(async () => {
    if (!project || !verbGroup || !runId) {
      debug("[grid][load] guard fail", { project, verbGroup, runId });
      return;
    }

    const seq = ++loadSeq.current;
    setPhase("loading");
    onStatusRef.current?.("Loading...");
    setSelection(undefined);
    selectionRef.current = undefined;
    hist.reset();

    const saveBtn = document.getElementById("save");
    if (saveBtn) saveBtn.disabled = true;

    setGridState((prev) => ({
      ...prev,
      headers: [],
      rows: [],
    }));

    try {
      debug("[grid][load] begin", { project, verbGroup, runId, seq });
      const wantNoun = !!(nounType && nounType !== "null" && nounType !== "undefined");

      const [mainData, nounInfo, refInfo, retestInfo, durInfo] = await Promise.all([
        GET(endpointsRef.current.load({ project, verbGroup, runId })),
        wantNoun ? GET(endpointsRef.current.nounInfo({ project, nounType })) : Promise.resolve({}),
        wantNoun ? GET(endpointsRef.current.refAdjectives({ project, nounType })) : Promise.resolve({}),
        GET(endpointsRef.current.retestOptions({ project, verbGroup, runId })),
        wantNoun && endpointsRef.current.durationAdjectives
          ? GET(endpointsRef.current.durationAdjectives({ project, nounType })).catch(() => ({}))
          : Promise.resolve({}),
      ]);

      if (seq !== loadSeq.current) {
        debug("[grid][load] aborted (seq mismatch)");
        return;
      }

      // Anchor the Duration ticker to the server clock (now_iso_ms), measured against perf.now().
      const srvNow = durInfo?.server_now ? Date.parse(durInfo.server_now) : NaN;
      serverClockRef.current = Number.isNaN(srvNow)
        ? { serverMs: null, perfMs: 0 }
        : { serverMs: srvNow, perfMs: performance.now() };

      // Normalize incoming data
      let hs = [], rs = [];
      if (mainData && Array.isArray(mainData.headers) && Array.isArray(mainData.rows)) {
        hs = mainData.headers;
        rs = mainData.rows;
      } else if (Array.isArray(mainData)) {
        hs = Array.from(new Set(mainData.flatMap((r) => Object.keys(r || {}))));
        rs = mainData;
      }

      // Pull schema info
      const primary = nounInfo?.primary_id || "";
      const schemaOrder = Array.isArray(nounInfo?.headers_from_schema) ? nounInfo.headers_from_schema : [];

      // ✅ If no headers came back from file, seed from schema
      if ((!hs || hs.length === 0) && schemaOrder.length > 0) {
        hs = [...schemaOrder];
      }

      let ordered = orderHeadersByRules(hs, schemaOrder, primary);

      // Duration columns are VIRTUAL — they hold no stored value, so they aren't in the data
      // headers and orderHeadersByRules drops them. Re-insert them (before the trailing _runID)
      // so the live ticker column is shown even on a row set that has never stored it.
      const durNames = Array.isArray(durInfo?.names) ? durInfo.names : [];
      const extraDur = durNames.filter((d) => d && !ordered.includes(d));
      if (extraDur.length) {
        const hasRun = ordered.includes("_runID");
        ordered = ordered.filter((h) => h !== "_runID").concat(extraDur).concat(hasRun ? ["_runID"] : []);
      }

      if (!ordered.length) {
        onStatusRef.current?.("No columns found in data");
        setPhase("idle");
        setGridState((prev) => ({ ...prev, headers: [], rows: [] }));
        return;
      }

      // atomic state update
      setGridState({
        headers: ordered,
        rows: (rs && rs.length > 0)
          ? rs
          : [Object.fromEntries((ordered || []).map(h => [h, ""]))],
        schemaPrimary: primary,
        autoGen: !!(nounInfo?.auto_generate_primary_id || nounInfo?.autoGen),
        pictureCols: new Set(Array.isArray(nounInfo?.picture_cols) ? nounInfo.picture_cols : Array.isArray(nounInfo?.pictureCols) ? nounInfo.pictureCols : []),
        refCols: new Set(Array.isArray(refInfo?.names) ? refInfo.names : Array.isArray(refInfo) ? refInfo : Object.keys(refInfo || {})),
        durationCols: new Set(Array.isArray(durInfo?.names) ? durInfo.names : []),
        durationCfg: (durInfo && typeof durInfo.detail === "object" && durInfo.detail) ? durInfo.detail : {},
        retestIDs: (Array.isArray(retestInfo?.ids) ? retestInfo.ids : Array.isArray(retestInfo?.options) ? retestInfo.options : Array.isArray(retestInfo) ? retestInfo : []),
      });

      readySeqRef.current = seq;
      setPhase("ready");

      if (saveBtn) saveBtn.disabled = false;
      onStatusRef.current?.(`Loaded ${rs.length} rows • ${ordered.length} cols`);

      debug("[grid][load] ready", {
        rows: rs.length,
        cols: ordered.length,
        primary,
        autoGen: !!(nounInfo?.auto_generate_primary_id || nounInfo?.autoGen),
        pictureCols: (Array.isArray(nounInfo?.picture_cols) ? nounInfo.picture_cols : Array.isArray(nounInfo?.pictureCols) ? nounInfo.pictureCols : []),
        refCols: (Array.isArray(refInfo?.names) ? refInfo.names : Array.isArray(refInfo) ? refInfo : Object.keys(refInfo || {})),
      });

      requestAnimationFrame(focusGrid);
    } catch (e) {
      if (seq !== loadSeq.current) return;
      console.error("Error during load sequence:", e);

      setGridState((prev) => ({ ...prev, headers: [], rows: [] }));

      const saveBtn2 = document.getElementById("save");
      if (saveBtn2) saveBtn2.disabled = true;

      setPhase("idle");
      onStatusRef.current?.(`Load error: ${e.message || "Failed to fetch data"}`);
    }
  }, [project, verbGroup, runId, nounType, focusGrid, hist]);

  // reset history when the run changes
  useEffect(() => {
    if (runId) hist.reset();
  }, [runId]);

  // autoload on mount / changes
  useEffect(() => {
    loadOne();
  }, [project, verbGroup, runId, nounType]);

  // After becoming "ready", apply focus
  useLayoutEffect(() => {
    if (phase !== "ready") return;
    if (readySeqRef.current == null || readySeqRef.current !== loadSeq.current) return;
    requestAnimationFrame(focusGrid);
  }, [phase, focusGrid]);

  // ---- SAVE ----
  const dirtyRef = useRef(false);
  const pendingAutosaveRef = useRef(false);
  const lastEditSourceRef = useRef("typed");

  const save = useCallback(async () => {
    if (!project || !verbGroup || !runId) return;
    try {
      // Duration columns are virtual/derived — strip them so an empty/stale value can never be
      // persisted to DataEntry.json or the instances store (the audited record holds only the
      // two anchor dates the user actually entered).
      const stripDur = (r) => {
        if (!durationColsNorm.size) return r;
        const o = {};
        for (const k of Object.keys(r || {})) if (!durationColsNorm.has(norm(k))) o[k] = r[k];
        return o;
      };
      const withRun = (rows || []).map((r) => ({ ...stripDur(r), _runID: runId }));
      const url = endpointsRef.current.save({ project, verbGroup, runId });
      const res = await POST(url, { headers: headers || [], rows: withRun });
      onSavedRef.current?.(res);
      setGridState((prev) => ({ ...prev, rows: withRun }));
      dirtyRef.current = false;
      onStatusRef.current?.(`Saved ${withRun.length} rows`);
      debug("[grid][save] complete", { rows: withRun.length });
    } catch (e) {
      onErrorRef.current?.(e);
      onStatusRef.current?.(`Save error: ${e.message || e}`);
      console.error("[grid][save] error", e);
    }
  }, [project, verbGroup, runId, headers, rows, durationColsNorm]);

  const queueAutosave = useCallback((source = "typed") => {
    if (source !== "typed") {
      debug("[grid][autosave] skipped (source =", source, ")");
      return;
    }
    if (!autosaveMs) return;

    dirtyRef.current = true;
    if (pendingAutosaveRef.current) return;

    pendingAutosaveRef.current = true;
    const started = performance.now();

    const pump = () => {
      const elapsed = performance.now() - started;
      if (elapsed >= autosaveMs) {
        pendingAutosaveRef.current = false;
        if (dirtyRef.current) save();
      } else {
        Promise.resolve().then(pump);
      }
    };

    Promise.resolve().then(pump);
    debug("[grid][autosave] queued (typed)", { autosaveMs });
  }, [autosaveMs, save]);

  // ---- ADD ROW ----
  const addRow = useCallback(() => {
    // ADD HISTORY TRACKING:
    hist.beginBatch(rowsRef.current);
    
    const h = Array.isArray(headers) ? headers : [];
    const blank = Object.fromEntries(h.map((k) => [k, ""]));
    setGridState((prev) => ({ ...prev, rows: [...(prev.rows || []), blank] }));
    
    // SCHEDULE COMMIT AFTER STATE UPDATE:
    hist.scheduleCommit(() => rowsRef.current);
    queueAutosave("programmatic");
    
    requestAnimationFrame(focusGrid);
    debug("[grid][row] add (no _runID prefill)");
  }, [headers, focusGrid, queueAutosave, hist]); // ADD hist to dependencies

  // ---- GENERATE ID (F2) ----
  const generateId = useCallback(async () => {
    if (!nounType) return;
    const h = Array.isArray(headers) ? headers : [];
    const pid =
      schemaPrimary ||
      h.find((x) => /_id$/i.test(x) && !x.startsWith("_")) ||
      h.find((x) => !x.startsWith("_")) ||
      h[0];
    if (!pid) return;

    const existing = Array.from(
      new Set((rows || []).map((r) => String(r?.[pid] || "")).filter(Boolean))
    );
    let id = "";
    try {
      const url = endpointsRef.current.generateId({ project, nounType });
      const r = await POST(url, { existing_ids: existing });
      id = r?.id || "";
    } catch {
      id = `ID${Date.now()}`;
      onStatusRef.current?.("ID gen failed; using fallback");
    }
    if (!id) return;

    // ADD HISTORY TRACKING:
    hist.beginBatch(rowsRef.current);

    const patch = (r) => ({
      ...r,
      [pid]: id,
      ...(h.includes("_runID") ? { _runID: runId } : {}),
    });
    let idx = (rows || []).findIndex((r) => !String(r?.[pid] || "").trim());
    setGridState((prev) => {
      const next = [...(prev.rows || [])];
      if (idx >= 0) next[idx] = patch(next[idx] || {});
      else {
        const blank = Object.fromEntries(h.map((k) => [k, ""]));
        next.push(patch(blank));
      }
      return { ...prev, rows: next };
    });
    
    // SCHEDULE COMMIT AFTER STATE UPDATE:
    hist.scheduleCommit(() => rowsRef.current);
    queueAutosave("programmatic");
    
    onStatusRef.current?.(`Generated ID: ${id}`);
    requestAnimationFrame(focusGrid);
    debug("[grid][id] generated", { field: pid, id });
  }, [headers, rows, runId, project, nounType, schemaPrimary, focusGrid, queueAutosave, hist]);

  // ---- COPY (Ctrl+C) fallback from selection ----
  const buildTSVFromSelection = () => {
    const sel = selectionRef.current?.current?.range;
    if (!sel) return "";
    const { x, y, width, height } = sel;
    const xs = Array.from({ length: width }, (_, d) => x + d);
    const ys = Array.from({ length: height }, (_, d) => y + d);
    return ys
      .map((rowIdx) =>
        xs
          .map((colIdx) => {
            const key = columns[colIdx]?.title ?? "";
            const v = rows?.[rowIdx]?.[key];
            const s = v == null ? "" : String(v);
            return s.includes("\t") || s.includes("\n")
              ? ('"' + s.replace(/"/g, '""') + '"')
              : s;
          })
          .join("\t")
      )
      .join("\n");
  };

  // ---- Selection ----
  const onGridSelectionChange = useCallback((sel) => {
    if (sameSelection(sel, selectionRef.current)) {
      return;
    }
    selectionRef.current = sel;
    setSelection(sel);
    debug("[grid][select] change", sel?.current);
  }, []);

  // ---- Edit handler ----
  const onCellEdited = useCallback(
    async (cell, newValue) => {
      const [col, r] = cell;
      if (newValue.kind !== GridCellKind.Text) return;

      const key = columns[col]?.title ?? "";
      const incoming = String(newValue.data ?? "");

      if (
        readOnlySet.has(key) ||
        (schemaPrimary && autoGen && key === schemaPrimary) ||
        pictureCols.has(key)
      )
        return;

      hist.beginBatch(rowsRef.current);

      if (enableF1Menus && nounType && isRefField(key) && incoming) {
        try {
          const allowed = await ensureRefOptions(key);
          if (!allowed.has(incoming)) {
            onStatusRef.current?.(
              `Invalid ${key} value. Press F1 for allowed options.`
            );
            hist.scheduleCommit(() => rowsRef.current);
            return;
          }
        } catch (e) {
          onStatusRef.current?.(`Ref validation error: ${e.message || e}`);
          hist.scheduleCommit(() => rowsRef.current);
          return;
        }
      }

      setGridState((prev) => {
        const next = [...(prev.rows || [])];
        const row = { ...(next[r] || {}) };
        row[key] = incoming;
        next[r] = row;
        return { ...prev, rows: next };
      });

      hist.scheduleCommit(() => rowsRef.current);

      // Treat paste as programmatic so autosave does not fire immediately
      const src = lastEditSourceRef.current; // "typed" | "programmatic" | "paste"
      queueAutosave(src);

      debug("[grid][edit]", { col, row: r, key, value: incoming, src });
    },
    [
      columns,
      readOnlySet,
      enableF1Menus,
      nounType,
      isRefField,
      ensureRefOptions,
      hist,
      queueAutosave,
      schemaPrimary,
      autoGen,
      pictureCols,
    ]
  );

  // ---- Shortcuts ----
  useEffect(() => {
    const isGridFocused = () => {
      const el = hostRef.current;
      const active = document.activeElement;
      const portal = document.getElementById("portal");
      return !!(
        el &&
        (el.contains(active) || (portal && portal.contains(active)))
      );
    };

    const onKey = async (e) => {
      const t = e.target;
      const tag = (t && t.tagName) ? t.tagName.toLowerCase() : "";
      const inEditor =
        tag === "input" ||
        tag === "textarea" ||
        (t && (t.isContentEditable || t.getAttribute?.("role") === "textbox"));

      // Declare k ONCE and allow F1/F2 even while editing
      const k = (e.key || "").toLowerCase();
      const isFnKey = k === "f1" || k === "f2";

      // Track paste/typing intent while editing; block other keys (but NOT F1/F2)
      if (inEditor && !isFnKey) {
        if (e.ctrlKey && k === "v") {
          lastEditSourceRef.current = "paste";
        } else if (!e.ctrlKey && e.key.length === 1) {
          lastEditSourceRef.current = "typed";
        } else if (e.key === "Enter" || e.key === "Tab" || e.key.startsWith("Arrow")) {
          lastEditSourceRef.current = "programmatic";
        }
        return;
      }

      // When not in editor, track typing intent
      if (!inEditor && !e.ctrlKey && e.key.length === 1) {
        lastEditSourceRef.current = "typed";
      }

      if (e.ctrlKey && k === "s") {
        e.preventDefault();
        if (phase === "ready") save();
        return;
      }

      if (!isGridFocused() && k !== "f1" && k !== "f2") return;

      if (enableCopyPaste && e.ctrlKey && k === "c") {
        const tsv = buildTSVFromSelection();
        if (tsv) {
          e.preventDefault();
          await navigator.clipboard.writeText(tsv);
          onStatusRef.current?.("Copied");
          debug("[grid][kb] copy selection");
        }
        return;
      }

      if (e.ctrlKey && k === "z") {
        e.preventDefault();
        setGridState(prev => ({ ...prev, rows: hist.undo(rowsRef.current) }));
        debug("[grid][kb] undo");
        return;
      }

      if (e.ctrlKey && k === "y") {
        e.preventDefault();
        setGridState(prev => ({ ...prev, rows: hist.redo(rowsRef.current) }));
        debug("[grid][kb] redo");
        return;
      }

      if (k === "f2") {
        e.preventDefault();
        debug("[grid][kb] F2 pressed", { nounType, schemaPrimary });
        if (!nounType) {
          onStatusRef.current?.("Can't generate ID: noun type is unknown (check /schema/verb for this verb).");
          return;
        }
        generateId();
        return;
      }

      if (k === "f1") {
        // Always stop browser help
        e.preventDefault();

        // Visibility for debugging
        debug("[grid][kb] F1 pressed", {
          enableF1Menus,
          hasSelection: !!selectionRef.current?.current,
          retestCount: (retestIDs || []).length,
        });

        if (!enableF1Menus) {
          onStatusRef.current?.("F1 menus are disabled");
          return;
        }

        const cell = selectionRef.current?.current?.cell;
        if (!cell) {
          onStatusRef.current?.("Select a cell first to use F1.");
          return;
        }

        const [cx, cy] = cell;
        const field = columns[cx]?.title ?? "";
        const fallbackPrimary =
          schemaPrimary ||
          (columns.find(c => /_id$/i.test(c.title) && !c.title.startsWith("_"))?.title) ||
          (columns.find(c => !c.title.startsWith("_"))?.title) || "";
        const onPrimary = field === fallbackPrimary;

        // Retest menu on primary ID
        if (enableRetestMenus && onPrimary && retestIDs.length) {
          openChoiceMenu("Select prior ID", retestIDs, (opt) => {
            hist.beginBatch(rowsRef.current);
            setGridState((prev) => {
              const nextRows = [...(prev.rows || [])];
              nextRows[cy] = { ...(nextRows[cy] || {}), [schemaPrimary]: opt };
              return { ...prev, rows: nextRows };
            });
            hist.scheduleCommit(() => rowsRef.current);
            queueAutosave("programmatic");
          });
          return;
        }

        // Reference options menu
        const maybeRef =
          isRefField(field) || /(^reference\b|\bid\b)/i.test(field); // allows “Reference …” or “… ID”
        if (maybeRef) {
          if (!nounType) {
            onStatusRef.current?.(`F1 menu needs a noun type to fetch options. The verb schema endpoint returned nothing for this verb.`);
            debug("[grid][kb] F1: nounType missing for ref field", { field });
            return;
          }
          try {
            const allowed = await ensureRefOptions(field);
            const opts = [...allowed];
            if (!opts.length) {
              onStatusRef.current?.("No options for reference");
              return;
            }
            openChoiceMenu(field, opts, (opt) => {
              hist.beginBatch(rowsRef.current);
              setGridState((prev) => {
                const nextRows = [...(prev.rows || [])];
                nextRows[cy] = { ...(nextRows[cy] || {}), [field]: opt };
                return { ...prev, rows: nextRows };
              });
              hist.scheduleCommit(() => rowsRef.current);
              queueAutosave("programmatic");
            });
          } catch (er) {
            onStatusRef.current?.(`Ref options error: ${er.message || er}`);
          }
          return;
        }

        // Nothing to show for this field
        onStatusRef.current?.(`No F1 menu for "${field}".`);
        debug("[grid][kb] F1: no menu available", {
          field,
          onPrimary,
          hasRef: refCols.has(field),
        });
        return;
      }
    };

    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [
    columns,
    schemaPrimary,
    retestIDs,
    enableF1Menus,
    enableRetestMenus,
    enableCopyPaste,
    nounType,
    refCols,
    phase,
    save,
    generateId,
    hist,
    queueAutosave,
    project,
  ]);

  useEffect(() => {
    const onMouseDown = () => { lastEditSourceRef.current = "programmatic"; };
    window.addEventListener("mousedown", onMouseDown, true);
    return () => window.removeEventListener("mousedown", onMouseDown, true);
  }, []);

  // ---- Cell renderer ----
  const getCellContent = useCallback(
    ([col, row]) => {
      if (!columns.length) {
        return {
          kind: GridCellKind.Text,
          data: "",
          displayData: "",
          allowOverlay: false,
          readonly: true,
        };
      }

      if (phase === "loading" && columns === PLACEHOLDER_COLUMNS) {
        return {
          kind: GridCellKind.Text,
          data: "Loading...",
          displayData: "Loading...",
          allowOverlay: false,
          readonly: true,
        };
      }

      const key = columns[col]?.title ?? "";

      // Duration (time-aware) column: virtual/derived, computed live from its two bound
      // anchors + the server-anchored clock. `tick` is read so this re-runs each second.
      if (isDurationField(key)) {
        void tick;
        const cfg = durationCfgByNorm.get(norm(key));
        const text = formatDurationCell(rows?.[row] || {}, cfg, anchoredNow());
        return {
          kind: GridCellKind.Text,
          data: "",
          displayData: text,
          allowOverlay: false,
          readonly: true,
        };
      }

      const val = rows?.[row]?.[key];
      const ro =
        readOnlySet.has(key) ||
        (schemaPrimary && autoGen && key === schemaPrimary) ||
        pictureCols.has(key);
      return {
        kind: GridCellKind.Text,
        data: val == null ? "" : String(val),
        displayData: val == null ? "" : String(val),
        allowOverlay: !ro,
        readonly: ro,
      };
    },
    [columns, rows, readOnlySet, schemaPrimary, autoGen, pictureCols, phase,
     isDurationField, durationCfgByNorm, anchoredNow, tick]
  );

  // ---- imperative API ----
  useImperativeHandle(
    ref,
    () => ({
      getData: () => ({
        headers: [...(headers || [])],
        rows: JSON.parse(JSON.stringify(rows || [])),
      }),
      setData: (data) =>
        setGridState((prev) => ({
          ...prev,
          headers: [...(Array.isArray(data?.headers) ? data.headers : [])],
          rows: [...(Array.isArray(data?.rows) ? data.rows : [])],
        })),
      addRows: (n = 1) => {
        for (let i = 0; i < n; i++) addRow();
      },
      save,
      load: loadOne,
      focus: focusGrid,
      generateId: nounType ? generateId : undefined,
      isDirty: () => !!dirtyRef.current,
    }),
    [headers, rows, addRow, save, loadOne, focusGrid, generateId, nounType]
  );

  const isReady = phase === "ready" && Array.isArray(headers) && headers.length > 0;

  const gridKey = useMemo(
    () => `${project}-${verbGroup}-${runId}-${resetKey}`,
    [project, verbGroup, runId, resetKey]
  );

  debug("[grid] render", {
    phase,
    headersLen: headers?.length ?? null,
    colsLen: columns.length,
    rowCount: Array.isArray(rows) ? rows.length : 0,
    gridKey,
    dims,
  });

  // ---- Height: render at natural content height; outer host will scroll
  const rowCount = Math.max(0, (rows?.length || 0));
  const naturalHeight = HEADER_HEIGHT + ROW_HEIGHT * rowCount + 2; // +2 for borders/rounding
  // Keep a sensible floor so tiny datasets still have some canvas area
  const editorHeight = Math.max(240, naturalHeight);

  return React.createElement(
    "div",
    {
      ref: hostRef,
      className: "gims-grid-host",
      style: {
        position: "relative",
        width: "100%",
        height: "auto",
        display: "block",
        maxHeight: "70vh",          // cap visual height
        overflowY: "auto",          // ← scrollbar appears on the side
        scrollbarGutter: "stable",  // optional: prevents layout shift when bar shows
      },
    },
    showToolbar &&
      React.createElement(
        "div",
        { style: { display: "flex", gap: 8, alignItems: "center", marginBottom: 8 } },
        React.createElement(
          "button",
          { id: "addRow", onClick: addRow, disabled: !isReady },
          "+ Row"
        ),
        React.createElement(
          "button",
          { id: "genId", onClick: generateId, disabled: !nounType || !isReady },
          "Generate ID"
        ),
        React.createElement(
          "button",
          {
            id: "copyJson",
            onClick: () =>
              navigator.clipboard
                .writeText(pretty({ headers: headers || [], rows: rows || [] }))
                .then(() => onStatusRef.current?.("Copied JSON"))
                .catch(() => onStatusRef.current?.("Copy failed")),
            disabled: !isReady,
          },
          "Copy JSON"
        ),
        // NEW: Download CSV
        React.createElement(
          "button",
          {
            id: "downloadCsv",
            onClick: () => {
              try {
                const hs = Array.isArray(headers) ? headers : [];
                const rs = Array.isArray(rows) ? rows : [];
                const esc = (v) => {
                  const s = v == null ? "" : String(v);
                  const needsQuotes = /[",\n]/.test(s);
                  const doubled = s.replace(/"/g, '""');
                  return needsQuotes ? `"${doubled}"` : doubled;
                };
                const lines = [];
                lines.push(hs.map(esc).join(","));
                for (const r of rs) lines.push(hs.map((h) => esc(r?.[h])).join(","));
                const csv = lines.join("\n");

                // BOM helps Excel open as UTF-8
                const blob = new Blob([new Uint8Array([0xEF, 0xBB, 0xBF]), csv], { type: "text/csv;charset=utf-8" });
                const a = document.createElement("a");
                a.href = URL.createObjectURL(blob);
                a.download = `grid_${runId || "data"}.csv`;
                document.body.appendChild(a);
                a.click();
                setTimeout(() => {
                  URL.revokeObjectURL(a.href);
                  a.remove();
                }, 0);

                onStatusRef.current?.("CSV downloaded");
                debug("[grid][csv] downloaded", { rows: rs.length, cols: hs.length });
              } catch (e) {
                onStatusRef.current?.("CSV download failed");
                console.error("[grid][csv] error", e);
              }
            },
            disabled: !isReady,
          },
          "Download CSV"
        ),
        React.createElement(
          "button",
          { type: "button", tabIndex: -1, id: "save", onClick: save, disabled: !isReady },
          "Save"
        ),
        React.createElement(
          "div",
          { style: { marginLeft: "auto", opacity: 0.7 } },
          `Run: ${runId}`
        )
      ),

    React.createElement(
      ErrorBoundary,
      { onStatus: onStatusRef.current },
      React.createElement(DataEditor, {
        key: gridKey,
        ref: editorRef,
        columns,
        rows: Math.max(0, (rows?.length || 0)),
        getCellContent,
        onCellEdited,
        onGridSelectionChange,
        ...(selectionRef.current?.current ? { gridSelection: selectionRef.current } : {}),
        rowMarkers: isReady ? "both" : "none",
        smoothScrollX: true,
        smoothScrollY: true,
        width: Math.max(240, dims.w),
        rowHeight: ROW_HEIGHT,
        headerHeight: HEADER_HEIGHT,
        height: editorHeight,
      })
    )
  );
});

// ---------- mount helper ----------
export function mountDataGrid(el, config) {
  if (!el) throw new Error("mountDataGrid: host element is required");

  el.style.display = "block";

  // Do not force any height; let the grid compute it
  el.style.minHeight = "";
  el.style.height = "";

  if (!el.style.position) el.style.position = "relative";

  debug("[grid][mount] host prepared", {
    minHeight: el.style.minHeight,
    height: el.style.height,
    position: el.style.position,
  });

  const root = createRoot(el);
  const ref = React.createRef();
  root.render(React.createElement(DataGridApp, { ...config, ref }));
  debug("[grid][mount] rendered", { config });

  return {
    unmount() {
      try {
        root.unmount();
        debug("[grid][mount] unmounted");
      } catch (e) {
        console.warn("[grid][mount] unmount error", e);
      }
    },
    getData() {
      const v = ref.current?.getData?.() || { headers: [], rows: [] };
      debug("[grid][mount] getData", { headers: v.headers.length, rows: v.rows.length });
      return v;
    },
    setData(data) {
      debug("[grid][mount] setData", { headers: data?.headers?.length, rows: data?.rows?.length });
      return ref.current?.setData?.(data);
    },
    addRows(n) {
      debug("[grid][mount] addRows", { n });
      return ref.current?.addRows?.(n);
    },
    save() {
      debug("[grid][mount] save()");
      return ref.current?.save?.();
    },
    load() {
      debug("[grid][mount] load()");
      return ref.current?.load?.();
    },
    focus() {
      debug("[grid][mount] focus()");
      return ref.current?.focus?.();
    },
    generateId() {
      debug("[grid][mount] generateId()");
      return ref.current?.generateId?.();
    },
    isDirty() {
      const v = !!ref.current?.isDirty?.();
      debug("[grid][mount] isDirty →", v);
      return v;
    },
  };
}