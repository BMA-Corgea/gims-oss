// GlideTable.tsx
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  forwardRef,
  useImperativeHandle,
} from "react";
import DataEditor, {
  GridCell,
  GridCellKind,
  GridColumn,
  Item,
} from "@glideapps/glide-data-grid";
import "@glideapps/glide-data-grid/dist/index.css";

/** ---------- Types (stable adapter contract) ---------- */
export type Row = Record<string, string>;
export type GridData = { headers: string[]; rows: Row[] };

export type Selection = { col: number; row: number } | null;

export interface GlideTableHandle {
  /** Replace all data (validated & normalized) */
  setData(data: GridData): void;
  /** Current data snapshot */
  getData(): GridData;
  /** Append N blank rows */
  addRows(n?: number): void;
  /** Current single-cell selection (if any) */
  getSelection(): Selection;
  /** Move selection by dx,dy (keeps rangeStack intact) */
  moveBy(dx: number, dy: number): void;
  /** Manual save now (returns server JSON or void if no saveUrl) */
  saveNow(): Promise<any | void>;
  /** Load (replaces data) — only if fetchUrl is set */
  reload(): Promise<void>;
}

/** ---------- Small runtime guards (no extra deps) ---------- */
function isStringRecord(x: unknown): x is Record<string, string> {
  if (!x || typeof x !== "object") return false;
  for (const v of Object.values(x as any)) {
    if (
      !(
        v === "" ||
        typeof v === "string" ||
        typeof v === "number" ||
        typeof v === "boolean" ||
        v === null
      )
    ) {
      return false;
    }
  }
  return true;
}

function validateGridData(x: any): asserts x is GridData {
  if (!x || typeof x !== "object") throw new Error("GridData must be an object");
  if (!Array.isArray(x.headers)) throw new Error("headers must be an array");
  if (!Array.isArray(x.rows)) throw new Error("rows must be an array");
  for (const h of x.headers) {
    if (typeof h !== "string" || !h.trim()) throw new Error("headers: non-empty strings only");
  }
  for (const r of x.rows) {
    if (!isStringRecord(r)) throw new Error("rows: must be array of flat string-like records");
  }
}

/** ---------- Abortable fetch helper ---------- */
function makeAbortable() {
  let ctrl: AbortController | null = null;
  return {
    async run<T>(fn: (signal: AbortSignal) => Promise<T>): Promise<T> {
      ctrl?.abort();
      ctrl = new AbortController();
      try {
        return await fn(ctrl.signal);
      } finally {
        ctrl = null;
      }
    },
    abort() {
      ctrl?.abort();
      ctrl = null;
    },
  };
}

/** ---------- Error Boundary ---------- */
class GridErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { error?: Error }
> {
  constructor(props: any) {
    super(props);
    this.state = { error: undefined };
  }
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  componentDidCatch(err: Error) {
    // eslint-disable-next-line no-console
    console.error("GlideTable crashed:", err);
  }
  render() {
    if (this.state.error) {
      return (
        <div
          style={{
            padding: 12,
            border: "1px solid #e99",
            background: "#2b0f0f",
            color: "#ffdede",
            borderRadius: 8,
            fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
          }}
        >
          <div style={{ fontWeight: 700, marginBottom: 6 }}>Grid Error</div>
          <div style={{ whiteSpace: "pre-wrap", opacity: 0.9 }}>
            {this.state.error.message}
          </div>
        </div>
      );
    }
    return this.props.children as any;
  }
}

/** ---------- Component ---------- */
type Props = {
  /** Provide either fetchUrl or initialData */
  fetchUrl?: string;
  /** Optional POST endpoint to receive { headers, rows } on save */
  saveUrl?: string;
  /** Optional initial normalized data */
  initialData?: GridData;
  /** Make some columns read-only */
  readOnlyCols?: string[];
  /** Called after successful save */
  onSaved?: (res: any) => void;
  /** Debounced autosave ms (if saveUrl set). 0/undefined disables autosave. */
  autosaveMs?: number;
};

const GlideTable = forwardRef<GlideTableHandle, Props>(function GlideTable(
  {
    fetchUrl,
    saveUrl,
    initialData,
    readOnlyCols = [],
    onSaved,
    autosaveMs = 0,
  },
  ref
) {
  /** ---- size via ResizeObserver -> numeric width/height ---- */
  const hostRef = useRef<HTMLDivElement | null>(null);
  const [dims, setDims] = useState<{ w: number; h: number }>({ w: 800, h: 420 });
  useEffect(() => {
    const el = hostRef.current;
    if (!el) return;
    const measure = () => {
      const r = el.getBoundingClientRect();
      setDims({
        w: Math.max(240, Math.floor(r.width)),
        h: Math.max(180, Math.floor(r.height)),
      });
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    window.addEventListener("resize", measure);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, []);

  /** ---- data state ---- */
  const [headers, setHeaders] = useState<string[]>(initialData?.headers ?? []);
  const [rows, setRows] = useState<Row[]>(initialData?.rows ?? []);

  /** ---- columns ---- */
  const columns: GridColumn[] = useMemo(
    () => headers.map((h) => ({ id: h, title: h })),
    [headers]
  );

  /** ---- selection (as provided by Glide) ---- */
  const [gridSelection, setGridSelection] = useState<any | undefined>(undefined);

  /** ---- imperative moveBy respecting Glide's selection shape ---- */
  const moveBy = useCallback(
    (dx: number, dy: number) => {
      if (!columns.length) return;
      const base = gridSelection ?? {
        columns: undefined,
        rows: undefined,
        current: {
          cell: [0, 0] as [number, number],
          range: { x: 0, y: 0, width: 1, height: 1 },
          rangeStack: [] as any[],
        },
      };
      const cell = base.current?.cell as [number, number] | undefined;
      if (!cell) return;
      const [c, r] = cell;
      const maxC = Math.max(0, columns.length - 1);
      const maxR = Math.max(0, rows.length - 1);
      const nc = Math.min(maxC, Math.max(0, c + dx));
      const nr = Math.min(maxR, Math.max(0, r + dy));
      setGridSelection({
        ...base,
        current: {
          ...base.current,
          cell: [nc, nr],
          range: { x: nc, y: nr, width: 1, height: 1 },
          rangeStack: Array.isArray(base.current?.rangeStack)
            ? base.current.rangeStack
            : [],
        },
      });
    },
    [columns.length, rows.length, gridSelection]
  );

  /** ---- I/O helpers ---- */
  const abortable = useRef(makeAbortable()).current;

  const reload = useCallback(async () => {
    if (!fetchUrl) return;
    await abortable.run(async (signal) => {
      const r = await fetch(fetchUrl, { signal });
      if (!r.ok) throw new Error(await r.text());
      const data = (await r.json()) as unknown;
      validateGridData(data);
      setHeaders(data.headers);
      setRows(data.rows);
      setGridSelection(undefined);
    });
  }, [fetchUrl]);

  const saveNow = useCallback(async () => {
    if (!saveUrl) return;
    const payload: GridData = { headers, rows };
    validateGridData(payload);
    const r = await fetch(saveUrl, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!r.ok) throw new Error(await r.text());
    const res = await r.json().catch(() => ({}));
    onSaved?.(res);
    return res;
  }, [headers, rows, saveUrl, onSaved]);

  /** ---- autosave (debounced) ---- */
  const autosaveTimer = useRef<number | null>(null);
  const queueAutosave = useCallback(() => {
    if (!autosaveMs || !saveUrl) return;
    if (autosaveTimer.current) window.clearTimeout(autosaveTimer.current);
    autosaveTimer.current = window.setTimeout(() => {
      autosaveTimer.current = null;
      // fire and forget; surface errors in console
      saveNow().catch((e) => console.error("[GlideTable autosave]", e));
    }, Math.max(200, autosaveMs));
  }, [autosaveMs, saveUrl, saveNow]);

  /** ---- initial load ---- */
  useEffect(() => {
    if (fetchUrl) reload().catch((e) => console.error("GlideTable fetch error:", e));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fetchUrl]);

  /** ---- cell content & edit ---- */
  const getCellContent = useCallback(
    ([col, row]: Item): GridCell => {
      const key = columns[col]?.title ?? "";
      const val = rows[row]?.[key];
      const readOnly = readOnlyCols.includes(key);
      return {
        kind: GridCellKind.Text,
        data: val == null ? "" : String(val),
        displayData: val == null ? "" : String(val),
        allowOverlay: !readOnly,
        readonly: readOnly,
      };
    },
    [columns, rows, readOnlyCols]
  );

  const onCellEdited = useCallback(
    (cell: Item, newValue: GridCell) => {
      const [col, rowIdx] = cell;
      if (newValue.kind !== GridCellKind.Text) return;
      const key = columns[col]?.title ?? "";
      if (readOnlyCols.includes(key)) return;
      setRows((prev) => {
        const next = [...prev];
        const row = { ...(next[rowIdx] || {}) };
        row[key] = String(newValue.data ?? "");
        next[rowIdx] = row;
        return next;
      });
      queueAutosave();
    },
    [columns, readOnlyCols, queueAutosave]
  );

  /** ---- commands ---- */
  const addRows = useCallback(
    (n = 1) => {
      const blank = Object.fromEntries(headers.map((h) => [h, ""])) as Row;
      setRows((prev) => [...prev, ...Array.from({ length: n }, () => ({ ...blank }))]);
      queueAutosave();
    },
    [headers, queueAutosave]
  );

  /** ---- imperative adapter ---- */
  useImperativeHandle(
    ref,
    (): GlideTableHandle => ({
      setData(d: GridData) {
        validateGridData(d);
        setHeaders(d.headers);
        setRows(d.rows);
        setGridSelection(undefined);
      },
      getData() {
        return { headers: [...headers], rows: [...rows] };
      },
      addRows,
      getSelection() {
        const c = gridSelection?.current?.cell as [number, number] | undefined;
        return c ? { col: c[0], row: c[1] } : null;
      },
      moveBy,
      saveNow,
      reload,
    }),
    [headers, rows, gridSelection, addRows, moveBy, saveNow, reload]
  );

  /** ---- render ---- */
  return (
    <div
      ref={hostRef}
      style={{ height: "100%", display: "grid", gridTemplateRows: "auto 1fr", gap: 8 }}
    >
      <div style={{ display: "flex", gap: 8 }}>
        <button onClick={() => addRows(1)}>+ Row</button>
        {saveUrl && <button onClick={() => saveNow().catch(console.error)}>Save</button>}
        <div style={{ opacity: 0.6, marginLeft: "auto" }}>
          Rows: {rows.length} • Cols: {headers.length}
        </div>
      </div>

      <GridErrorBoundary>
        <DataEditor
          columns={columns}
          rows={rows.length}
          getCellContent={getCellContent}
          onCellEdited={onCellEdited}
          onGridSelectionChange={setGridSelection}
          rowMarkers="both"
          smoothScrollX
          smoothScrollY
          width={dims.w}
          height={dims.h}
          {...(gridSelection ? { gridSelection } : {})}
        />
      </GridErrorBoundary>
    </div>
  );
});

export default GlideTable;
