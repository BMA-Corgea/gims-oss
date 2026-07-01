// frontend/runlog/tabs/StatusTab.jsx — the Status Breakdown tab (5.2d redesign).
// Classic mode: the bucket "pills + text grid" become a ProgressRing (overall %) + a StatusTimeline
// (one step per zone), with overrides as a tone-coded SpecList instead of a RESOLVED:… text blob.
// Linear mode is handled by S6 (LinearStatus); until then it shows a placeholder.
import { Icon, ProgressRing, SpecList, StateBlock, StatusTimeline } from "../../lib/ui.jsx";
import { classifyBreakdown, isLinearBreakdown, normalizeBreakdown } from "../status.js";
import { LinearStatus } from "./LinearStatus.jsx";

export function StatusTab({ dump, project, group, runID, onRefresh }) {
  const raw = (dump && dump.status_breakdown) || {};
  if (isLinearBreakdown(raw)) {
    // onRefresh is the dump reload → also used to refresh gated panes after a gate sign-off.
    return <LinearStatus project={project} group={group} runID={runID} summary={raw} onGateComplete={onRefresh} />;
  }
  const breakdown = normalizeBreakdown(raw);
  if (!Object.keys(breakdown).length) {
    return <StateBlock kind="empty" icon="info" title="No status information" message="This run has no status breakdown yet." />;
  }

  const { steps, overrides, details } = classifyBreakdown(breakdown);
  const done = steps.filter((s) => s.state === "done").length;
  const pct = steps.length ? Math.round((done / steps.length) * 100) : 0;

  return (
    <div className="rw-status-panel">
      <div className="rw-status-head">
        {steps.length ? <ProgressRing percent={pct} /> : null}
        <span className="rw-status-title">Status Breakdown</span>
        {onRefresh ? (
          <button type="button" className="btn ghost sm rw-status-refresh" onClick={onRefresh}>
            <Icon name="refresh" /> Refresh
          </button>
        ) : null}
      </div>

      {steps.length ? <StatusTimeline steps={steps} /> : null}

      {overrides.length ? (
        <section className="rw-status-sub">
          <div className="rw-status-subtitle">Overrides</div>
          <SpecList items={overrides} />
        </section>
      ) : null}

      {details.length ? (
        <section className="rw-status-sub">
          <SpecList items={details} compact />
        </section>
      ) : null}
    </div>
  );
}
