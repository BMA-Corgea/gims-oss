// frontend/runlog/api.js — runlog endpoint wrappers.
// At runtime these GETs go through the injected /orchestrate fetch wrapper (the node injects
// /orchestrate/inject.js), exactly as the vanilla page did; the verify harness mocks them.
import { enc, fetchJSON } from "../lib/api.js";

export const getProjects = () => fetchJSON("/runlog_data_dump/projects");
export const getVerbGroups = (project) => fetchJSON(`/runlog_data_dump/verb_groups/${enc(project)}`);
export const getRunlog = (project, group) => fetchJSON(`/runlog/${enc(project)}/${enc(group)}`);
export const getDump = (project, group, runID) =>
  fetchJSON(`/runlog/${enc(project)}/${enc(group)}/${enc(runID)}/dump`);
export const getVerbSchema = (project, verbName) =>
  fetchJSON(`/schema/verb/${enc(project)}/${enc(verbName)}`);

// ── 21 CFR Part 11 §11.70(i) trusted-time status (clock-trust badge) ──
export const getTimeStatus = () => fetchJSON("/compliance/time");

// ── Raw data (per-pocket attachments) ──
export const getRawList = (project, group, runID, pocket) =>
  fetchJSON(`/runlog/${enc(project)}/${enc(group)}/${enc(runID)}/raw/list${pocket ? `?pocket=${enc(pocket)}` : ""}`);

export const rawDownloadUrl = (project, group, runID, pocket, filename) =>
  `/runlog/${enc(project)}/${enc(group)}/${enc(runID)}/raw/download?pocket=${enc(pocket)}&filename=${enc(filename)}`;

export const deleteRawFile = (project, group, runID, pocket, filename) =>
  fetchJSON(`/runlog/${enc(project)}/${enc(group)}/${enc(runID)}/raw/delete?pocket=${enc(pocket)}&filename=${enc(filename)}`,
            { method: "DELETE" });

// multipart upload (pocket, file, filename, overwrite) — byte-identical FormData to the vanilla.
export function uploadRawFile(project, group, runID, pocket, file, overwrite) {
  const fd = new FormData();
  fd.set("pocket", pocket);
  fd.set("file", file, file.name);
  fd.set("filename", file.name);
  fd.set("overwrite", overwrite ? "true" : "false");
  return fetchJSON(`/runlog/${enc(project)}/${enc(group)}/${enc(runID)}/raw/upload`, { method: "POST", body: fd });
}

// ── Interpretation (parsers + per-tab interpretation files) ──
export async function listCustomParsers(project) {
  const r = await fetchJSON(`/api/parser_test/list_custom_parsers?project=${enc(project)}`);
  if (Array.isArray(r)) return r;
  if (r && Array.isArray(r.parsers)) return r.parsers;
  if (r && Array.isArray(r.items)) return r.items;
  return [];
}

export const runParser = (project, parser, group, runID, body) =>
  fetchJSON(`/api/parser_test/test_parser/${enc(project)}/${enc(parser)}?verb_group=${enc(group)}&run_id=${enc(runID)}`,
            { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

export const interpList = (project, group, runID, verbName) =>
  fetchJSON(`/runlog/${enc(project)}/${enc(group)}/${enc(runID)}/interpret/list${verbName ? `?verb=${enc(verbName)}` : ""}`);

export function uploadInterpFile(project, group, runID, tab, file, overwrite) {
  const fd = new FormData();
  fd.set("tab", tab);
  fd.set("file", file, file.name);
  fd.set("overwrite", overwrite ? "true" : "false");
  return fetchJSON(`/runlog/${enc(project)}/${enc(group)}/${enc(runID)}/interpret/upload`, { method: "POST", body: fd });
}

export const deleteInterpFile = (project, group, runID, tab) =>
  fetchJSON(`/runlog/${enc(project)}/${enc(group)}/${enc(runID)}/interpret/delete?tab=${enc(tab)}`, { method: "DELETE" });

export const interpDownloadUrl = (project, group, runID, tab) =>
  `/runlog/${enc(project)}/${enc(group)}/${enc(runID)}/interpret/download?tab=${enc(tab)}`;

// ── Overrides (conjunctions) ──
export const getOverrides = (project, group, runID) =>
  fetchJSON(`/runlog/${enc(project)}/${enc(group)}/${enc(runID)}/override`);

export const updateOverrides = (project, group, runID, overrides) =>
  fetchJSON(`/runlog/${enc(project)}/${enc(group)}/${enc(runID)}/override/update`,
            { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ overrides }) });

// Reference options for a ReferenceList field (shared by overrides + adverbs). Array/null params
// append repeated keys; empties are dropped — same query shape as the vanilla qstring().
export async function refOptions(project, nounType, params) {
  const usp = new URLSearchParams();
  Object.entries(params || {}).forEach(([k, v]) => {
    if (Array.isArray(v)) v.forEach((x) => usp.append(k, x));
    else if (v !== undefined && v !== null && v !== "") usp.append(k, v);
  });
  const qs = usp.toString();
  const r = await fetchJSON(`/conjunction/reference_options/${enc(project)}/${enc(nounType)}${qs ? `?${qs}` : ""}`);
  return Array.isArray(r && r.options) ? r.options : [];
}

// ── Adverbs ──
export const getAdverbs = (project, group, runID) =>
  fetchJSON(`/runlog/${enc(project)}/${enc(group)}/${enc(runID)}/adverb`);

export const updateAdverbs = (project, group, runID, adverbs) =>
  fetchJSON(`/runlog/${enc(project)}/${enc(group)}/${enc(runID)}/adverb/update`,
            { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ adverbs }) });

// §11.200 gate sign-off / reopen. Goes through the injected /orchestrate wrapper (gate-complete is
// ALWAYS intercepted, isGateCompletePath) — byte-identical to the vanilla path/query/body/creds:
// POST /runlog/{p}/{g}/{id}/gate/{internal_id}/complete?completed={true|false} with {password, reason}.
// The server's enforce_gate_signoff_reauth is the source of truth and is untouched.
export function completeGate(project, group, runID, internalId, completed, body) {
  return fetchJSON(
    `/runlog/${enc(project)}/${enc(group)}/${enc(runID)}/gate/${enc(internalId)}/complete?completed=${completed}`,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) },
  );
}

// Linear status: prefer the full status.json (cache-busted, as the vanilla did), fall back to
// the /status/linear summary. Returns {steps, steps_completed, steps_total, progress, first_incomplete}.
export async function getStatusJson(project, group, runID) {
  const base = `/runlog/${enc(project)}/${enc(group)}/${enc(runID)}`;
  try {
    return await fetchJSON(`${base}/status.json?t=${Date.now()}`);
  } catch {
    return fetchJSON(`${base}/status/linear`);
  }
}
