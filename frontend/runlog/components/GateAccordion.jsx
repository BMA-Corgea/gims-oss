// frontend/runlog/components/GateAccordion.jsx — one linear step as a <details> accordion
// (port of renderStepRow): number + title + state pill + check in the summary; a SpecList of the
// step fields in the body; for GATE steps, a Sign off / Reopen button (only when onGate is wired —
// the actual e-sign action arrives in S7). Reuses the existing gate-acc/gate-* page CSS.
import { Icon, SpecList } from "../../lib/ui.jsx";

export function GateAccordion({ step, index, currentIndex, onGate }) {
  const completed = !!step.completed;
  const isCurrent = index === Number(currentIndex);
  const label = step.label || step.id || `Step ${index + 1}`;
  const type = (step.type || "step").toLowerCase();
  const isGate = type === "gate";

  const pill = completed
    ? <span className="gate-pill ok"><Icon name="check" /> Completed</span>
    : isCurrent ? <span className="gate-pill current">Current</span>
      : <span className="gate-pill pending">Pending</span>;

  const specs = [
    { label: "id", value: step.id || "" },
    { label: "type", value: type },
    { label: "label", value: label },
    { label: "internal_id", value: step.internal_id || "—" },
    { label: "required", value: String(Boolean(step.required)) },
    { label: "source", value: step.source || "—" },
    { label: "completed", value: String(completed), tone: completed ? "ok" : undefined },
    { label: "reason", value: step.reason || "—" },
  ];
  const canAct = isCurrent || completed;

  return (
    <details className={"gate-acc" + (completed ? " is-complete" : "")} open={isCurrent}
             data-index={index} data-type={type}>
      <summary className="gate-summary">
        <span className="gate-step">{index + 1}</span>
        <span className="gate-title">{label}</span>
        {pill}
        <span className={"step-check" + (completed ? " ok" : "")} aria-hidden="true"><Icon name="check" /></span>
      </summary>
      <div className="gate-body">
        <SpecList items={specs} compact />
        {isGate && onGate ? (
          <div className="gate-actions">
            <button type="button" className="step-toggle btn btn-sm" disabled={!canAct}
                    title={canAct ? undefined : "This step is locked until previous steps are completed."}
                    onClick={() => onGate(step, completed ? "false" : "true")}>
              {completed ? "Reopen" : "Sign off"}
            </button>
          </div>
        ) : null}
      </div>
    </details>
  );
}
