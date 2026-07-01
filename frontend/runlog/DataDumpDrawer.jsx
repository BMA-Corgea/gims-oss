// frontend/runlog/DataDumpDrawer.jsx — the 7-tab data-dump viewer (the vanilla #data-dump-section,
// renderDataDump). Tabs are lazy + keep-alive: a tab's body mounts the first time it's shown and
// stays mounted (hidden via display:none) so grid/editor state + fetched data survive tab switches.
// Tab order matches the vanilla exactly. Status/Adverbs/Interpretation/Overrides/Raw Data arrive in
// S5–S11; until then they show a PlaceholderTab.
import { useEffect, useState } from "react";
import { Icon, StateBlock } from "../lib/ui.jsx";
import { useDump } from "./hooks.js";
import { InstructionsTab } from "./tabs/InstructionsTab.jsx";
import { DataEntryTab } from "./tabs/DataEntryTab.jsx";
import { StatusTab } from "./tabs/StatusTab.jsx";
import { RawDataTab } from "./tabs/RawDataTab.jsx";
import { InterpretationTab } from "./tabs/InterpretationTab.jsx";
import { OverridesTab } from "./tabs/OverridesTab.jsx";
import { AdverbsTab } from "./tabs/AdverbsTab.jsx";

export function DataDumpDrawer({ project, group, runID, onClose, onStatus }) {
  const { data: dump, loading, error, reload } = useDump(project, group, runID);
  const [active, setActive] = useState("instructions");
  const [seen, setSeen] = useState(() => new Set(["instructions"]));

  // Reset to the first tab whenever a different run is opened.
  useEffect(() => { setActive("instructions"); setSeen(new Set(["instructions"])); }, [project, group, runID]);
  // Mirror the vanilla #status-bar message on load.
  useEffect(() => { if (dump && onStatus) onStatus(`Viewing dump for ${runID}`); }, [dump]); // eslint-disable-line react-hooks/exhaustive-deps
  // The grid needs the data-entry-active body class (CSS) while that tab is shown.
  useEffect(() => {
    document.body.classList.toggle("data-entry-active", active === "data_entry");
    return () => document.body.classList.remove("data-entry-active");
  }, [active]);

  const show = (k) => { setActive(k); setSeen((s) => (s.has(k) ? s : new Set(s).add(k))); };

  let body;
  if (loading) body = <StateBlock kind="loading" title="Loading data dump…" />;
  else if (error) body = <StateBlock kind="error" title="Failed to load data dump" message={String(error.message || error)} />;
  else if (!dump) body = <StateBlock kind="empty" title="No data" />;
  else {
    const verbName = (dump.run_entry && (dump.run_entry.test_type || dump.run_entry.verb)) || dump.verb || "";
    const tabs = [
      { key: "instructions", title: "Instructions", el: <InstructionsTab dump={dump} /> },
      { key: "status", title: "Status Breakdown", el: <StatusTab dump={dump} project={project} group={group} runID={runID} onRefresh={reload} /> },
      { key: "adverbs", title: "Adverbs", el: <AdverbsTab project={project} group={group} runID={runID} /> },
      { key: "data_entry", title: "Data Entry", el: <DataEntryTab dump={dump} project={project} group={group} runID={runID} onStatus={onStatus} /> },
      { key: "interpretation", title: "Interpretation", el: <InterpretationTab dump={dump} project={project} group={group} runID={runID} /> },
      { key: "overrides", title: "Overrides", el: <OverridesTab project={project} group={group} runID={runID} verbName={verbName} /> },
      { key: "raw_data", title: "Raw Data", el: <RawDataTab dump={dump} project={project} group={group} runID={runID} /> },
    ];
    body = (
      <>
        <div id="data-dump-tabs" className="tabs">
          {tabs.map((t) => (
            <button key={t.key} type="button"
                    className={"tab-button" + (active === t.key ? " active" : "")}
                    onClick={() => show(t.key)}>{t.title}</button>
          ))}
        </div>
        <div id="data-dump-contents">
          {tabs.map((t) => (
            <div key={t.key} data-tab-key={t.key}
                 className={"tab-content" + (active === t.key ? " active" : "")}
                 style={{ display: active === t.key ? "block" : "none" }}
                 aria-hidden={active === t.key ? undefined : "true"}>
              {seen.has(t.key) ? t.el : null}
            </div>
          ))}
        </div>
      </>
    );
  }

  return (
    <section className="panel" id="data-dump-section">
      <div className="panel-head section-header">
        <Icon name="file" />
        <span className="panel-title">Data Dump</span>
        <button id="close-dump" className="close-button" type="button" aria-label="Close data dump" onClick={onClose}>
          <Icon name="close" />
        </button>
      </div>
      <div className="panel-body">{body}</div>
    </section>
  );
}
