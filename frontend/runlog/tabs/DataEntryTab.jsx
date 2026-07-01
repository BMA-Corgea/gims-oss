// frontend/runlog/tabs/DataEntryTab.jsx — the Data Entry tab: mounts the Glide editable grid
// (a faithful port of the vanilla mountDataEntryGrid). The grid is its own React root inside the
// host node; we mount once per (project,group,runID) and unmount() on cleanup so React + Glide
// don't fight over the DOM. The atomic DataEntry.json + SQL unit-of-work save lives in data_grid.js
// and is reused verbatim — do not reimplement it here.
import { useEffect, useRef, useState } from "react";
import { StateBlock } from "../../lib/ui.jsx";
import { enc, fetchJSON, toast } from "../../lib/api.js";

async function resolveNounType(dump, project) {
  const verbName = (dump && dump.run_entry && (dump.run_entry.test_type ?? dump.run_entry.verb)) || null;
  if (!verbName) return null;
  try {
    const vs = await fetchJSON(`/schema/verb/${enc(project)}/${enc(verbName)}`);
    const de = (vs && vs.data_entry_schema) || {};
    return (de.set_up_inputs && de.set_up_inputs.noun_type_ref) || de.noun_type || null;
  } catch {
    return null; // best-effort — grid still mounts without an explicit noun type
  }
}

export function DataEntryTab({ dump, project, group, runID, onStatus }) {
  const ref = useRef(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    let cancelled = false;
    let handle = null;
    const host = ref.current;
    if (!host) return undefined;
    (async () => {
      try {
        const { mountDataGrid, defaultEndpoints } = await import("/static/lib/data_grid.js");
        const nounType = await resolveNounType(dump, project);
        if (cancelled) return;
        handle = mountDataGrid(host, {
          project,
          verbGroup: group,
          runId: runID,
          ...(nounType ? { nounType } : {}),
          endpoints: defaultEndpoints,
          readOnlyCols: ["_runID"],
          autosaveMs: 800,
          onStatus: (m) => { if (onStatus) onStatus(m); },
          onSaved: () => toast("Data entry saved.", "ok"),
          onError: () => toast("Grid error", "err"),
          onReady: (grid) => {
            const rh = (grid.config && grid.config.rowHeight) || 32;
            const hh = (grid.config && grid.config.headerHeight) || 32;
            host.style.height = `${hh + rh * grid.state.rowCount}px`;
          },
        });
      } catch (e) {
        if (!cancelled) setErr(e);
      }
    })();
    return () => {
      cancelled = true;
      try { if (handle && handle.unmount) handle.unmount(); } catch { /* noop */ }
    };
  }, [project, group, runID]); // eslint-disable-line react-hooks/exhaustive-deps

  if (err) return <StateBlock kind="error" title="Failed to mount data grid" message={String(err.message || err)} />;
  return <div className="rw-grid-host" ref={ref} />;
}
