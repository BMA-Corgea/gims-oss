// frontend/lib/api.js — shared front-end helpers for the React pages.
export const enc = encodeURIComponent;

// Pull a human message out of an AppError envelope ({error_code, message, detail, details})
// so a failed fetch surfaces "Project path not found: X" — not the raw JSON blob. Mirrors the
// vanilla gims.js _messageFromBody so React + vanilla pages report errors the same way.
function messageFromBody(body, fallback) {
  if (body == null) return fallback;
  if (typeof body === "string") return body || fallback;
  const d = body.detail;
  if (typeof d === "string" && d) return d;
  if (d && typeof d === "object") return d.reason || d.message || d.error || fallback;
  return body.message || body.error || body.error_code || fallback;
}

export async function fetchJSON(url, opts = {}) {
  let r;
  try {
    r = await fetch(url, opts);
  } catch (e) {
    const err = new Error("Network error — could not reach the server.");
    err.cause = e; err.status = 0;
    throw err;
  }
  const ct = r.headers.get("content-type") || "";
  let body = null;
  if (ct.includes("application/json")) { try { body = await r.json(); } catch { body = null; } }
  else { try { body = await r.text(); } catch { body = null; } }
  if (!r.ok) {
    const err = new Error(messageFromBody(body, `Request failed (${r.status})`));
    err.status = r.status; err.body = body;
    throw err;
  }
  return body;
}

export function fmt(v) {
  return (v && typeof v === "object") ? JSON.stringify(v) : (v == null || v === "" ? "—" : String(v));
}

export function kvItems(obj) {
  return Object.entries(obj || {}).map(([k, v]) => ({ label: k, value: fmt(v), tone: (v && typeof v === "object") ? "mono" : undefined }));
}

export function queryParams() { return new URLSearchParams(location.search); }

// Fire a Watery toast through the shell toolkit (no-op when served outside the shell).
export function toast(msg, kind = "ok") { const G = window.GIMS; if (G && typeof G.toast === "function") G.toast(msg, kind); }

// Mount a React render once auth resolves — the React analogue of the vanilla GIMS.onAuthed
// boot. Renders nothing for anonymous users (the shell shows the login card).
export function mountOnAuth(rootId, render) {
  const boot = () => { const host = document.getElementById(rootId); if (host) render(host); };
  const G = window.GIMS;
  if (G && typeof G.onAuthed === "function") G.onAuthed(boot);
  else window.addEventListener("load", boot);
}
