// frontend/runlog/gate.js — client-side linear-gate computation (port of computeLinearGate +
// computeRawUploadGate / computeInterpGate / computeAdverbsGate). Fetches status.json and decides
// whether the CURRENT linear step unlocks a given feature (raw upload / interpretation / adverbs).
// Fails OPEN on any error (non-linear / buckets runs are never gated), exactly like the vanilla.
import { enc, fetchJSON } from "../lib/api.js";

const RAW = ["raw data", "raw_data", "raw upload", "upload raw", "raw files", "raw_files", "raw"];
const INTERP = ["interpret", "interpretation", "parse", "parsing"];
const ADVERBS = ["adverb", "adverbs"];

export async function computeLinearGate(project, group, runID, { keywords = [], pockets = [] } = {}) {
  try {
    const data = await fetchJSON(`/runlog/${enc(project)}/${enc(group)}/${enc(runID)}/status.json`);
    const steps = Array.isArray(data.steps) ? data.steps
      : (data.linear_status && Array.isArray(data.linear_status.steps)) ? data.linear_status.steps : [];
    const linearEnabled = Boolean(data && data.linear_status && data.linear_status.enabled) || steps.length > 0;
    if (!linearEnabled) return { ok: true, allowed: true, pocket: null, reason: "not linear-enabled", currentIndex: -1 };

    const idx = (data.first_incomplete && typeof data.first_incomplete.index === "number")
      ? data.first_incomplete.index
      : (typeof data.steps_completed === "number" ? data.steps_completed : -1);
    if (idx < 0 || idx >= steps.length) return { ok: true, allowed: true, pocket: null, reason: "all steps completed", currentIndex: idx };

    const current = steps[idx] || null;
    if (!current) return { ok: true, allowed: false, pocket: null, reason: "an unknown step", currentIndex: idx };

    const hay = [current.id, current.label, current.type, current.source].map((x) => String(x || "").toLowerCase()).join(" ");
    const matchesKind = keywords.length ? keywords.some((k) => hay.includes(k)) : true;
    if (!matchesKind) return { ok: true, allowed: false, pocket: null, reason: current.label || current.id || "the current step", currentIndex: idx };

    let matchedPocket = null;
    if (pockets && pockets.length && current.source) {
      const sp = String(current.source).toLowerCase().trim();
      matchedPocket = pockets.find((p) => String(p).toLowerCase().trim() === sp) || null;
    }
    return { ok: true, allowed: true, pocket: matchedPocket, reason: current.label || current.id || "current step", currentIndex: idx };
  } catch {
    return { ok: false, allowed: true, pocket: null, reason: "status unavailable", currentIndex: -1 };
  }
}

export const computeRawUploadGate = (p, g, r, pockets = []) => computeLinearGate(p, g, r, { keywords: RAW, pockets });
export const computeInterpGate = (p, g, r) => computeLinearGate(p, g, r, { keywords: INTERP });
export const computeAdverbsGate = (p, g, r) => computeLinearGate(p, g, r, { keywords: ADVERBS });
