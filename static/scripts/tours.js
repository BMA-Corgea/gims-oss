// tours.js — wires the vendored guided-tour engine (static/lib/tour.js) to the GIMS shell.
// The header "?" button and the tutorial page's "Tour this page" CTA dispatch a cancelable
// `gims:tour:start` event; this listener runs the current page's STEPS with the gnome narrator
// (Watery-themed), and calls preventDefault() so the shell's "coming soon" fallback is skipped.
// Per-page STEPS live here (data, not engine) — add more pages over time.
(() => {
  "use strict";
  const NARRATOR = { image: "/static/images/gnome-tour.png", name: "GIMS Gnome" };
  const THEME = { dim: "rgba(3,12,9,0.78)", ring: "#2dd4bf", radius: 12 };

  // STEPS keyed by body[data-page]. Targets are stable ids/classes the pages already expose.
  const STEPS = {
    tutorial: [
      { target: null, placement: "center", title: "Welcome to GIMS",
        text: "A 60-second tour of how the app is laid out. Use Next, or press → / Esc." },
      { target: "#gims-rail", placement: "right", title: "The workspace rail",
        text: "Every workspace lives here, grouped by what it does — Schemas, Searches, Operations, Tools, Admin." },
      { target: ".tut-grid", placement: "top", title: "Five parts of speech",
        text: "GIMS models your lab as a grammar: nouns (entities), adjectives (descriptors), verbs (actions), adverbs (modifiers), conjunctions (overrides)." },
      { target: ".tut-flow", placement: "top", title: "The everyday flow",
        text: "Configure schemas → enter data in the workbenches → operate in the runlog → find & verify." },
      { target: "#gims-userchip", placement: "bottom", title: "Your account",
        text: "Your profile, project memberships, roles, and sign-out live here." },
      { target: "#gims-help", placement: "bottom", title: "Replay any time",
        text: "Hit this ? on any page to replay its tour." },
    ],
    noun_configure: [
      { target: null, placement: "center", title: "Noun Configure",
        text: "Define your entity types (Sample, Batch, …) — their fields and primary ID." },
      { target: "#project", placement: "bottom", title: "1 · Pick a project", text: "Everything is scoped to a project." },
      { target: "#noun", placement: "bottom", title: "2 · Choose a noun type", text: "Or scroll down to register a brand-new one." },
      { target: "#configure-button", placement: "left", title: "3 · View or edit", text: "Run to load the noun's field schema for viewing or editing." },
    ],
    deep_search: [
      { target: null, placement: "center", title: "Deep Search",
        text: "Search across schemas, noun instances, and verb runs in a project." },
      { target: "#project-select", placement: "bottom", title: "Pick a project", text: "Scope the search." },
      { target: "#search-input", placement: "bottom", title: "Type a term", text: "Press Enter or the Search button." },
      { target: ".results-tabs", placement: "bottom", title: "Filter by type", text: "All, Schema, Nouns, or Verbs." },
    ],
    audit: [
      { target: null, placement: "center", title: "Audit Workbench",
        text: "Run data-quality checks across a project and browse the findings." },
      { target: "#aw-project", placement: "bottom", title: "Pick a project", text: "Choose what to audit." },
      { target: "#aw-run", placement: "bottom", title: "Run the audit", text: "Findings appear below, grouped by severity." },
      { target: ".aw-filter-row", placement: "top", title: "Filter findings", text: "Narrow by severity, code, or free text." },
      { target: ".aw-results", placement: "top", title: "The findings", text: "Sort the grid and click a row to see its full detail." },
    ],
    investigation: [
      { target: null, placement: "center", title: "Lineage Investigator",
        text: "Trace where a record came from and what references it." },
      { target: "#project", placement: "bottom", title: "Project & noun type", text: "Pick the project and the kind of record." },
      { target: "#load-button", placement: "bottom", title: "Load records", text: "Optionally filter first, then load the list." },
      { target: ".records-panel", placement: "right", title: "Pick a record", text: "Click any row to see its lineage on the right." },
      { target: ".lineage-panel", placement: "left", title: "Lineage detail", text: "Referenced runs, parents/siblings, and override retests." },
    ],
    noun_workbench: [
      { target: null, placement: "center", title: "Noun Workbench",
        text: "Create and edit noun instances — one at a time or in bulk." },
      { target: "#project-select", placement: "bottom", title: "Project & noun type", text: "Pick what you're entering." },
      { target: "#dynamicForm", placement: "top", title: "The form", text: "Fields are driven by the noun's schema; Validate then Save." },
      { target: "#fileInput", placement: "top", title: "Bulk upload", text: "Or import a CSV/XLSX — preview the diff, then commit valid rows." },
    ],
    runlog_workbench: [
      { target: null, placement: "center", title: "Runlog Workbench",
        text: "Inspect and edit run entries: statuses, gates & sign-offs, attachments, dumps." },
      { target: "#project-select", placement: "bottom", title: "Project & verb group", text: "Choose which runs to work on." },
      { target: "#runlog-table", placement: "top", title: "The run log", text: "Click a run to open its data dump and editors below." },
    ],
  };

  function run(page) {
    const steps = STEPS[page];
    if (!steps || !window.Tour) return false;
    window.Tour.replay({
      storageKey: "gims_tour_" + page, narrator: NARRATOR, steps,
      finishLabel: "Done", dim: THEME.dim, ring: THEME.ring, radius: THEME.radius,
    });
    return true;
  }

  document.addEventListener("gims:tour:start", (e) => {
    const page = (e.detail && e.detail.page) || document.body.dataset.page;
    if (run(page)) e.preventDefault();  // handled → suppress the shell's fallback toast
  });
})();
