// frontend/runlog/hooks.js — small data hooks for the runlog React page.
import { useEffect, useState } from "react";
import { getDump, getStatusJson } from "./api.js";

// useAsync(fn, deps, enabled): run fn whenever deps change (and enabled is truthy), tracking
// {data, error, loading}. reload() forces a re-run. Stale results are dropped on unmount/dep-change.
export function useAsync(fn, deps, enabled = true) {
  const [state, setState] = useState({ data: null, error: null, loading: !!enabled });
  const [nonce, setNonce] = useState(0);
  useEffect(() => {
    if (!enabled) { setState({ data: null, error: null, loading: false }); return undefined; }
    let alive = true;
    setState((s) => ({ data: s.data, error: null, loading: true }));
    Promise.resolve().then(fn).then(
      (data) => { if (alive) setState({ data, error: null, loading: false }); },
      (error) => { if (alive) setState({ data: null, error, loading: false }); },
    );
    return () => { alive = false; };
  }, [...deps, enabled, nonce]); // eslint-disable-line react-hooks/exhaustive-deps
  return { ...state, reload: () => setNonce((n) => n + 1) };
}

// Port of the vanilla openDump overrides-summary injection: collapse dump.overrides into a
// "Override Status" entry in status_breakdown (replacing any pre-existing override-ish key), so the
// Status tab (S5/S6) renders it like the rest. Returns a NEW dump (no mutation of the fetched one).
function normalizeDump(dump) {
  if (!dump || !Array.isArray(dump.overrides)) return dump;
  const lines = dump.overrides.map((ovr) => {
    const type = ovr.type || "Unknown";
    const status = ovr.status || "Note";
    const notes = (ovr.resolution || []).map((r) => r && r.note).filter(Boolean).join("; ");
    return `${status}: ${type}${notes ? " — " + notes : ""}`;
  });
  const sb = { ...(dump.status_breakdown || {}) };
  const overrideKey = Object.keys(sb).find((k) => k.toLowerCase().includes("override"));
  if (overrideKey) delete sb[overrideKey];
  sb["Override Status"] = lines;
  return { ...dump, status_breakdown: sb };
}

// useDump: fetch + normalize a run's data dump (enabled only when all three keys are present).
export function useDump(project, group, runID) {
  return useAsync(
    async () => normalizeDump(await getDump(project, group, runID)),
    [project, group, runID],
    !!(project && group && runID),
  );
}

// useStatus: fetch the linear status.json (with /status/linear fallback) for a run.
export function useStatus(project, group, runID) {
  return useAsync(
    () => getStatusJson(project, group, runID),
    [project, group, runID],
    !!(project && group && runID),
  );
}
