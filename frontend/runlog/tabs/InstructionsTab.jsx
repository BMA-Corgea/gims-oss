// frontend/runlog/tabs/InstructionsTab.jsx — the Instructions tab (read-only text).
import { StateBlock } from "../../lib/ui.jsx";

export function InstructionsTab({ dump }) {
  const lines = (dump && dump.instructions) || [];
  if (!lines.length) {
    return <StateBlock kind="empty" icon="info" title="No instructions" message="This run has no instructions." />;
  }
  return <pre className="text-viewer rw-instructions">{lines.join("\n")}</pre>;
}
