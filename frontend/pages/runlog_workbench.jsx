// frontend/pages/runlog_workbench.jsx — Runlog Workbench (Phase 6 React migration, runlog sub-plan).
// S3 scaffold: the React root + Workspace toolbar + RunLogPanel (read-only pickers → run table →
// row select), honoring the ?project=&group=&run_id= deep-link. The data-dump drawer, the seven
// tabs, gates/e-sign, raw/interp/override/adverb editors land in S4–S11; the live page is still
// driven by the vanilla static/scripts/runlog_workbench.js until the S12 cutover.
import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { Icon, StateBlock } from "../lib/ui.jsx";
import { mountOnAuth, queryParams } from "../lib/api.js";
import { getProjects, getVerbGroups, getRunlog } from "../runlog/api.js";
import { useAsync } from "../runlog/hooks.js";
import { WorkspaceToolbar } from "../runlog/WorkspaceToolbar.jsx";
import { RunLogPanel } from "../runlog/RunLogPanel.jsx";
import { DataDumpDrawer } from "../runlog/DataDumpDrawer.jsx";

function statusText({ project, projectsState, group, runlogState }) {
  if (projectsState.loading) return "Loading projects…";
  if (projectsState.error) return "Unable to load projects.";
  if (!project) return "Ready.";
  if (!group) return `Project selected: ${project}.`;
  if (runlogState.loading) return "Loading run log…";
  if (runlogState.error) return "Error loading run log.";
  const n = runlogState.data && runlogState.data.rows ? runlogState.data.rows.length : 0;
  return `Loaded ${n} run${n === 1 ? "" : "s"}.`;
}

function RunlogWorkbench() {
  const qp = queryParams();
  const projectsState = useAsync(getProjects, []);
  const projects = projectsState.data || [];

  const [project, setProject] = useState("");
  const [group, setGroup] = useState("");
  const [run, setRun] = useState(qp.get("run_id") || null);
  const [flash, setFlash] = useState(null); // transient status (dump load / grid save), overrides derived text

  // Pick the initial project once the list loads (deep-link ?project=, else the first project).
  useEffect(() => {
    if (!projects.length || project) return;
    const preset = qp.get("project");
    setProject(preset && projects.includes(preset) ? preset : projects[0]);
  }, [projects]); // eslint-disable-line react-hooks/exhaustive-deps

  const groupsState = useAsync(() => getVerbGroups(project), [project], !!project);
  const groups = groupsState.data || [];

  // Honor the deep-link ?group= once that project's groups load.
  useEffect(() => {
    if (!groups.length || group) return;
    const preset = qp.get("group");
    if (preset && groups.includes(preset)) setGroup(preset);
  }, [groups]); // eslint-disable-line react-hooks/exhaustive-deps

  const runlogState = useAsync(() => getRunlog(project, group), [project, group], !!(project && group));

  const onProject = (p) => { setProject(p); setGroup(""); setRun(null); setFlash(null); };
  const onGroup = (g) => { setGroup(g); setRun(null); setFlash(null); };
  const closeDump = () => { setRun(null); setFlash(null); };

  const status = flash || statusText({ project, projectsState, group, runlogState });

  return (
    <>
      <WorkspaceToolbar
        projects={projects} project={project} onProject={onProject}
        groups={groups} groupsState={groupsState} group={group} onGroup={onGroup}
      />

      <section className="panel" id="runlog-section">
        <div className="panel-head">
          <Icon name="runlog" />
          <span className="panel-title">Run Log</span>
        </div>
        <div className="panel-body">
          {group
            ? <RunLogPanel
                data={runlogState.data} loading={runlogState.loading} error={runlogState.error}
                onOpenRun={setRun} selectedRun={run} onRetry={runlogState.reload}
              />
            : <StateBlock kind="empty" title="No verb group selected"
                          message="Pick a project and a verb group to view its runs." />}
        </div>
      </section>

      {project && group && run
        ? <DataDumpDrawer project={project} group={group} runID={run} onClose={closeDump} onStatus={setFlash} />
        : null}

      <section className="panel rw-status">
        <div className="status-bar" id="status-bar">{status}</div>
      </section>
    </>
  );
}

mountOnAuth("runlog-root", (host) => createRoot(host).render(<RunlogWorkbench />));
