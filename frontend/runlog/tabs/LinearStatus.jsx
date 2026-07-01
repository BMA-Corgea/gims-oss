// frontend/runlog/tabs/LinearStatus.jsx — linear-mode status (5.2d redesign of renderLinearStatus +
// hydrateLinearPanelById). A ProgressRing + a Stepper overview + per-step GateAccordions, fed by
// the status.json (with /status/linear fallback) via useStatus. The gate sign-off action (onGate)
// is supplied in S7.
import { useState } from "react";
import { Icon, ProgressRing, StateBlock, Stepper } from "../../lib/ui.jsx";
import { toast } from "../../lib/api.js";
import { completeGate } from "../api.js";
import { useStatus } from "../hooks.js";
import { GateAccordion } from "../components/GateAccordion.jsx";
import { GateSignoffModal } from "../components/GateSignoffModal.jsx";

function stepState(st, i, currentIx) {
  if (st.completed) return "done";
  if ((st.type || "").toLowerCase() === "gate") return "gate";
  if (i === currentIx) return "active";
  return "pending";
}

export function LinearStatus({ project, group, runID, summary, onGateComplete }) {
  const { data, loading, error, reload } = useStatus(project, group, runID);
  const [gate, setGate] = useState(null); // { step, setTo:boolean } while the e-sign modal is open

  // GateAccordion fires (step, "true"|"false"); open the §11.200 e-sign modal.
  const openGate = (step, setToStr) => setGate({ step, setTo: setToStr === "true" });

  const confirmGate = async (esig) => {
    const { step, setTo } = gate;
    setGate(null);
    const internalId = step.internal_id || step.id || "";
    try {
      await completeGate(project, group, runID, internalId, setTo, { password: esig.password, reason: esig.reason });
      toast(setTo ? "Gate signed off." : "Gate reopened.", "ok");
      reload();                       // re-hydrate the linear panel (vanilla hydrateLinearPanelById)
      if (onGateComplete) onGateComplete(); // refresh the dump → gated panes (vanilla refreshAllGatedPanes)
    } catch (e) {
      toast(`Gate update failed: ${String(e.message || e)}`, "err");
    }
  };

  if (loading) return <StateBlock kind="loading" title="Loading status…" />;
  if (error) return <StateBlock kind="error" title="Failed to load status" message={String(error.message || error)} />;
  if (!data) return <StateBlock kind="empty" title="No status" />;

  const steps = Array.isArray(data.steps) ? data.steps
    : (data.linear_status && Array.isArray(data.linear_status.steps)) ? data.linear_status.steps : [];
  const done = Number(data.steps_completed ?? 0);
  const total = Number(data.steps_total ?? steps.length);
  const progress = data.progress || (summary && summary.linear_progress) || `${done}/${total}`;
  const currentIx = (data.first_incomplete && typeof data.first_incomplete.index === "number")
    ? data.first_incomplete.index : done;
  const pct = total ? Math.round((done / total) * 100) : 0;

  const stepperSteps = steps.map((st, i) => ({
    label: st.label || st.id || `Step ${i + 1}`,
    state: stepState(st, i, currentIx),
    detail: (st.type || "").toLowerCase() === "gate" ? "Gate" : undefined,
  }));

  return (
    <div className="rw-status-panel linear-status"
         data-project={project} data-group={group} data-runid={runID} data-current-index={String(currentIx)}>
      <div className="rw-status-head">
        <ProgressRing percent={pct} />
        <span className="rw-status-title">Linear Status</span>
        <span className="rw-status-progress chip ls-progress">{progress}</span>
        <button type="button" className="btn ghost sm rw-status-refresh ls-refresh" onClick={reload}>
          <Icon name="refresh" /> Refresh
        </button>
      </div>

      {steps.length ? <Stepper steps={stepperSteps} /> : null}

      <section className="rw-status-sub">
        <div className="rw-status-subtitle">Steps</div>
        {steps.length
          ? steps.map((st, i) => (
              <GateAccordion key={st.internal_id || st.id || i} step={st} index={i}
                             currentIndex={currentIx} onGate={openGate} />
            ))
          : <div className="muted">No steps are defined.</div>}
      </section>

      {gate ? (
        <GateSignoffModal isSignoff={gate.setTo} onConfirm={confirmGate} onCancel={() => setGate(null)} />
      ) : null}
    </div>
  );
}
