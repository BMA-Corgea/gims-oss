// frontend/runlog/status.js — status-breakdown parsing for the Status tab.
// Ports the vanilla normalizeBreakdown + getStatusValueClass + the classic/linear split, then
// reshapes the classic breakdown (the 5.2d redesign): single-value zones → timeline steps,
// "Override Status" → tone-coded spec items, multi-line keys → detail spec items.

export function normalizeBreakdown(raw) {
  if (!raw || typeof raw !== "object") return {};
  const out = {};
  for (const [key, value] of Object.entries(raw)) {
    if (Array.isArray(value)) {
      out[key] = value.map((v) => (typeof v === "string" ? v : (v && typeof v === "object") ? JSON.stringify(v) : String(v)));
    } else if (value && typeof value === "object") {
      out[key] = Object.entries(value).map(([k, v]) => `${k}: ${v}`);
    } else {
      out[key] = [String(value)];
    }
  }
  return out;
}

// Matches the vanilla formatStatusBreakdown linear detection (operates on the RAW breakdown).
export function isLinearBreakdown(raw) {
  return !!(raw && (raw.mode === "linear" || raw.linear_progress
    || (raw.details && raw.details.mode === "linear")
    || (raw.linear_status && Array.isArray(raw.linear_status.steps))));
}

const COMPLETE = ["Complete", "Uploaded", "Parsed", "Manually Completed"];
const WARNING = ["Pending"];

// Port of getStatusValueClass → a StatusTimeline state.
export function statusState(value) {
  const v = String(value).replace(/^[✔❌⚠]+\s*/, "");
  if (COMPLETE.includes(v)) return "done";
  if (WARNING.includes(v)) return "pending";
  if (v.startsWith("Missing")) return "error";
  return "pending";
}

export function formatKey(key) {
  return key.replace(/_/g, " ").split(" ").map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
}

const OVERRIDE_TONE = { RESOLVED: "ok", NOTIFICATION: "info", EXCEPTION: "err", NOTE: "info" };

function overrideItems(lines) {
  const seen = new Set();
  const items = [];
  for (const raw of lines) {
    if (seen.has(raw)) continue;
    seen.add(raw);
    const m = String(raw).match(/^([^:]+):\s*(.*)$/);
    if (m) {
      const label = m[1].trim();
      items.push({ label, value: m[2].trim() || "—", tone: OVERRIDE_TONE[label.toUpperCase()] || "info" });
    } else {
      items.push({ label: "Note", value: raw });
    }
  }
  return items;
}

// Split a normalized classic breakdown into {steps, overrides, details}.
export function classifyBreakdown(breakdown) {
  const steps = [];
  const details = [];
  let overrides = [];
  for (const [key, lines] of Object.entries(breakdown)) {
    if (key.toLowerCase().includes("override")) { overrides = overrideItems(lines); continue; }
    const uniq = [...new Set(lines)];
    if (uniq.length === 1) {
      steps.push({ label: formatKey(key), state: statusState(uniq[0]), detail: uniq[0] });
    } else {
      details.push({ label: formatKey(key), value: uniq.join("; ") });
    }
  }
  return { steps, overrides, details };
}
