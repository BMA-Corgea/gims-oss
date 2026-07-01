// frontend/runlog/WorkspaceToolbar.jsx — project + verb-group pickers (the page's toolbar).
// Mirrors the vanilla #project-select / #verbgroup-select contract (ids preserved) + the
// "+ Create New" link to the verb workbench.
import { useEffect, useState } from "react";
import { Icon } from "../lib/ui.jsx";
import { getTimeStatus } from "./api.js";

function groupPlaceholder(project, groupsState, groups) {
  if (!project) return "Select a project first";
  if (groupsState.loading) return "Loading verb groups…";
  if (groupsState.error) return "Error loading verb groups";
  return (groups && groups.length) ? "Select a verb group" : "No verb groups found";
}

// 21 CFR Part 11 §11.70(i): show whether the host clock that stamps the audit trail (and anchors
// the Duration tickers) is validated against the configured NTP reference. Polls /compliance/time
// (cached server-side ~60 s). synced=true → verified, false → skew beyond tolerance, null → no
// reference configured/reachable (unvalidated host clock). Best-effort; never blocks the page.
const _BADGE = {
  base: { display: "inline-flex", alignItems: "center", gap: 6, marginLeft: "auto",
          padding: "2px 10px", borderRadius: 999, fontSize: 11, fontWeight: 700,
          border: "1px solid", whiteSpace: "nowrap" },
  synced:      { color: "#0a7d52", borderColor: "#9fe0c4", background: "#e8f7ef" },
  skewed:      { color: "#b4232a", borderColor: "#f1b4b7", background: "#fdecec" },
  unvalidated: { color: "#5b6b6a", borderColor: "#d6dede", background: "#eef2f2" },
};
function ClockBadge() {
  const [st, setSt] = useState(null);
  useEffect(() => {
    let live = true;
    const load = () => getTimeStatus().then((s) => { if (live) setSt(s); }).catch(() => { if (live) setSt(null); });
    load();
    const id = setInterval(load, 30000);
    return () => { live = false; clearInterval(id); };
  }, []);
  if (!st) return null;
  const kind = st.synced === true ? "synced" : st.synced === false ? "skewed" : "unvalidated";
  const off = st.offset_seconds;
  const label = kind === "synced" ? `Clock verified${off != null ? ` ±${Math.abs(off)}s` : ""}`
              : kind === "skewed" ? `Clock skew ${off > 0 ? "+" : ""}${off}s`
              : "Clock unvalidated";
  const dot = kind === "synced" ? "#16a34a" : kind === "skewed" ? "#dc2626" : "#9aa7a6";
  return (
    <span style={{ ..._BADGE.base, ..._BADGE[kind] }} title={st.note || ""}>
      <span style={{ width: 8, height: 8, borderRadius: 999, background: dot, display: "inline-block" }} />
      {label}
    </span>
  );
}

export function WorkspaceToolbar({ projects, project, onProject, groups, groupsState, group, onGroup }) {
  const groupsDisabled = !project || groupsState.loading || !!groupsState.error || !(groups && groups.length);
  return (
    <section className="panel rw-toolbar">
      <div className="panel-head">
        <Icon name="folder" />
        <span className="panel-title">Workspace</span>
        <ClockBadge />
      </div>
      <div className="panel-body rw-toolbar-row">
        <label className="field rw-field">
          <span className="field-label">Project</span>
          <select id="project-select" className="input select" value={project || ""}
                  onChange={(e) => onProject(e.target.value)}>
            {(projects || []).map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
        </label>
        <label className="field rw-field">
          <span className="field-label">Verb Group</span>
          <select id="verbgroup-select" className="input select" value={group || ""}
                  disabled={groupsDisabled} onChange={(e) => onGroup(e.target.value)}>
            <option value="">{groupPlaceholder(project, groupsState, groups)}</option>
            {(groups || []).map((g) => <option key={g} value={g}>{g}</option>)}
          </select>
        </label>
        <a id="create-verb-instance-btn" className="btn btn-primary btn-sm rw-create" href="/verb_workbench">+ Create New</a>
      </div>
    </section>
  );
}
