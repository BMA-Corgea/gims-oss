// frontend/runlog/RunLogPanel.jsx — the run log, restructured onto the shared React GridTable
// (sortable, sticky-header, reactive row-select) instead of the hand-rolled <table>.
// Faithfully reproduces the vanilla renderRunlog column logic:
//   • display_ID first, then every non-hidden header (hidden: run_ID / _run_id / primary_id_field)
//   • the __status column renders a status-badge whose class comes from completed/total percent
//   • a row's runID resolves _run_id > primary_id_field > run_ID; rows without one aren't openable
import { GridTable, StateBlock } from "../lib/ui.jsx";

function statusClass(pct) {
  if (pct >= 100) return "status-complete";
  if (pct >= 75) return "status-good";
  if (pct >= 50) return "status-warning";
  return "status-pending";
}
function statusPercent(cell) {
  const parts = String(cell == null ? "" : cell).split("/");
  const done = parseInt(parts[0], 10), total = parseInt(parts[1], 10);
  return Number.isFinite(done) && Number.isFinite(total) && total > 0 ? Math.round((done / total) * 100) : 0;
}
function cellText(v) {
  if (v && typeof v === "object") { try { return JSON.stringify(v); } catch { return "Complex data"; } }
  return v == null ? "" : String(v);
}

// build the visible columns + row objects (keyed by header) from the {headers, rows, meta} payload.
function shape(data) {
  const headers = data.headers || [];
  const pidField = (data.meta && data.meta.primary_id_field) || "run_ID";
  const hidden = new Set(["run_ID", "_run_id", pidField]);
  const idx = (h) => headers.indexOf(h);

  const chosen = [];
  if (idx("display_ID") >= 0) chosen.push("display_ID");
  headers.forEach((h) => { if (!hidden.has(String(h)) && h !== "display_ID") chosen.push(h); });

  const columns = chosen.map((h) => ({
    key: h,
    label: h === "__status" ? "Status" : h,
    render: h === "__status"
      ? (v) => <span className={"status-badge " + statusClass(statusPercent(v))}>{cellText(v)}</span>
      : (v) => cellText(v),
  }));

  const iRun = idx("_run_id"), iPid = idx(pidField), iLegacy = idx("run_ID");
  const rows = (data.rows || []).map((arr) => {
    const o = {};
    headers.forEach((h, i) => { o[h] = arr[i]; });
    let rid = null;
    if (iRun >= 0 && arr[iRun] != null) rid = arr[iRun];
    else if (iPid >= 0 && arr[iPid] != null) rid = arr[iPid];
    else if (iLegacy >= 0 && arr[iLegacy] != null) rid = arr[iLegacy];
    o.__k = rid != null ? String(rid).trim() : undefined;
    return o;
  });
  return { columns, rows };
}

export function RunLogPanel({ data, loading, error, onOpenRun, selectedRun, onRetry }) {
  if (loading) return <StateBlock kind="loading" title="Loading run log…" />;
  if (error) {
    return (
      <StateBlock kind="error" title="Couldn’t load the run log" message={String(error.message || error)}>
        {onRetry ? <button className="btn ghost" type="button" onClick={onRetry}>Retry</button> : null}
      </StateBlock>
    );
  }
  if (!data) return <StateBlock kind="empty" title="No run log loaded" />;

  const { columns, rows } = shape(data);
  return (
    <GridTable
      columns={columns}
      rows={rows}
      selectedKey={selectedRun}
      onSelect={(row) => { if (row.__k != null) onOpenRun(row.__k); }}
      empty={{ title: "No runs", message: "This verb group has no runs yet." }}
      maxHeight="60vh"
    />
  );
}
