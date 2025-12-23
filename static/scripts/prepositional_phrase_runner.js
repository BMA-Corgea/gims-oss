// /static/scripts/prephrase_runner.js
// Frontend JavaScript for prepositional phrase runner interface

// Debug control
const DEBUG_ENABLED = false;
const debug = DEBUG_ENABLED ? console.debug.bind(console) : () => {};

const API_BASE = "/api/parser_test";
const CUSTOM_API = "/api/parser_test";

class PrephraseRunner {
  constructor() {
    this.currentProject = null;
    this.isWaitingForSignature = false;
    this.signatureRetryCount = 0;
    this.maxSignatureRetries = 30; // 30 seconds max wait
    this.originalRequest = null;

    // UI element IDs
    this.ui = {
      projectSelect: "project-select",
      prepSelect: "prep-select",
      prepButton: "prep-button",
      prepSpinner: "prep-spinner",
      prepResult: "prep-result",
      prepDot: "prep-dot",
      prepText: "prep-text",
      prepExpected: "prep-expected",
      resultHeader: "result-header",
      clearResults: "clear-results",
      
      // Dynamic settings card IDs
      settingsWrapId: "prep-settings-wrap",
      settingsCardId: "prep-settings-card",
      settingsBodyId: "prep-settings-body",
      settingsFormId: "prep-settings-form",
      settingsConfirmBtnId: "prep-settings-confirm",
      settingsResetBtnId: "prep-settings-reset",
      settingsInfoId: "prep-settings-info",

      // Outputs browser
      outputsWrapId: "pphrase-outputs-wrap",
      outputsBodyId: "pphrase-outputs-body",
      outputsRefreshBtnId: "pphrase-outputs-refresh",
    };

    this.init();
  }

  // ---------- Bootstrap ----------
  init() {
    debug("[init] Booting prepositional phrase runner");
    this.setupSignatureListener();
    this.loadProjects();
    this.setupEventListeners();
  }

  setupSignatureListener() {
    // Listen for signature completion events
    window.addEventListener('orch:trigger', (event) => {
      if (this.isWaitingForSignature && event.detail && event.detail.type === 'signature_required') {
        debug("[signature] Received trigger event:", event.detail);
        // The signature modal will handle the retry, we just need to wait
      }
    });

    // Also listen for custom completion event that might be fired by our retry logic
    window.addEventListener('prephrase:signature_complete', (event) => {
      if (this.isWaitingForSignature) {
        debug("[signature] Signature process completed");
        this.handleSignatureCompletion(event.detail);
      }
    });
  }

  setupEventListeners() {
    // Run button
    const btn = document.getElementById(this.ui.prepButton);
    if (btn) btn.addEventListener("click", () => this.runPrephrase());

    // Project selector
    const projectSelect = document.getElementById(this.ui.projectSelect);
    if (projectSelect) {
      projectSelect.addEventListener("change", (e) =>
        this.onProjectChange(e.target.value)
      );
    }

    // Prephrase selector
    const prepSelect = document.getElementById(this.ui.prepSelect);
    if (prepSelect) {
      prepSelect.addEventListener("change", async () => {
        const name = this.getSelectedPrephrase();
        debug("[prep.select] Changed to:", name);
        this.updateExpected();
        this.setButtonEnabled(!!name);
        if (name) {
          await this.checkSelected();
          await this.handlePphraseSelected(name);
        } else {
          this.ensurePrepSettingsCard(true);
          this.updateStatus("error", "Select a prepositional phrase");
        }
      });
    }

    // Clear results button
    const clearBtn = document.getElementById(this.ui.clearResults);
    if (clearBtn) {
      clearBtn.addEventListener("click", () => this.clearResults());
    }

    // Keyboard shortcuts
    document.addEventListener("keydown", (event) => {
      // Ctrl+R to reload
      if ((event.ctrlKey && event.key === "r") || event.key === "F5") {
        event.preventDefault();
        location.reload();
      }
      // Ctrl+Enter to run
      if (event.ctrlKey && event.key === "Enter") {
        event.preventDefault();
        const runBtn = document.getElementById(this.ui.prepButton);
        if (runBtn && !runBtn.disabled) runBtn.click();
      }
    });
  }

  // ---------- Projects ----------
  async loadProjects() {
    const projectSelect = document.getElementById(this.ui.projectSelect);
    if (!projectSelect) return;

    try {
      debug("[projects] Loading projects...");
      const res = await fetch(`${API_BASE}/check_parser/projects`);
      const projects = await res.json();
      projectSelect.innerHTML = "";

      if (!projects || projects.length === 0) {
        projectSelect.innerHTML = `<option value="">No projects found</option>`;
        projectSelect.disabled = true;
        return;
      }

      projectSelect.innerHTML = `<option value="">Select a project...</option>`;
      projects.forEach((p) => {
        const opt = document.createElement("option");
        opt.value = p;
        opt.textContent = p;
        projectSelect.appendChild(opt);
      });

      projectSelect.value = projects[0];
      debug("[projects] Defaulting to:", projects[0]);
      this.onProjectChange(projects[0]);
    } catch (err) {
      console.error("Error loading projects:", err);
      projectSelect.innerHTML = `<option value="">Error loading projects</option>`;
      projectSelect.disabled = true;
    }
  }

  async onProjectChange(projectName) {
    debug("[project.change] Changing to:", projectName);
    if (!projectName) {
      this.currentProject = null;
      this.resetInterface();
      return;
    }
    this.currentProject = projectName;
    this.resetInterface();
    this.ensureOutputsCard();
    await this.loadOutputsTree();
    await this.populatePrephrases();
  }

  // ---------- Prephrase Population ----------
  async populatePrephrases() {
    if (!this.currentProject) return;

    const prepSel = document.getElementById(this.ui.prepSelect);
    if (prepSel) prepSel.innerHTML = `<option value="">-- Select a prepositional phrase --</option>`;

    this.updateStatus("checking", "Loading prepositional phrases...");
    this.setButtonEnabled(false);

    try {
      debug("[populate] Listing prephrases for project:", this.currentProject);
      const res = await fetch(
        `${CUSTOM_API}/list_prepositional_phrases?project=${encodeURIComponent(this.currentProject)}`
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`);
      const data = await res.json();
      debug("[populate] Response:", data);

      const pphrases = data?.pphrases || [];
      if (prepSel) {
        pphrases.forEach((p) => {
          const name = p.name || p;
          const opt = document.createElement("option");
          opt.value = name;
          opt.textContent = name;
          prepSel.appendChild(opt);
        });
      }

      this.updateStatus(
        pphrases.length ? "error" : "error",
        pphrases.length ? "Select a prepositional phrase" : "No prepositional phrases found"
      );
      this.updateExpected();
    } catch (err) {
      console.error("Error loading prephrases:", err);
      this.updateStatus("error", "Failed to load prepositional phrases");
    }
  }

  // ---------- Status Management ----------
  resetInterface() {
    debug("[reset] Resetting interface");
    this.updateStatus("checking", "Initializing...");
    this.setButtonEnabled(false);
    this.clearResults();
    this.updateExpected(true);
    this.ensurePrepSettingsCard(true);
    this.ensureOutputsCard(true);
    this.isWaitingForSignature = false;
    this.signatureRetryCount = 0;
    this.originalRequest = null;
  }

  updateExpected(placeholderOnly = false) {
    const codeEl = document.getElementById(this.ui.prepExpected);
    if (!codeEl) return;

    const name = this.getSelectedPrephrase();
    if (name && !placeholderOnly) {
      codeEl.textContent = `custom/prepositional phrases/${name}/${name}.py`;
    } else {
      codeEl.textContent = "custom/prepositional phrases/{pphrase_name}/{pphrase_name}.py";
    }
  }

  updateStatus(state, text) {
    debug("[status] State:", state, "Text:", text);
    const dot = document.getElementById(this.ui.prepDot);
    const t = document.getElementById(this.ui.prepText);
    if (dot) dot.className = `status-dot ${state}`;
    if (t) t.textContent = text;
  }

  setButtonEnabled(enabled) {
    debug("[button.enable]", enabled);
    const btn = document.getElementById(this.ui.prepButton);
    if (btn) btn.disabled = !enabled;
  }

  getSelectedPrephrase() {
    const sel = document.getElementById(this.ui.prepSelect);
    return sel ? (sel.value || "").trim() : "";
  }

  clearResults() {
    const area = document.getElementById(this.ui.prepResult);
    const header = document.getElementById(this.ui.resultHeader);
    if (area) {
      area.textContent = "";
      area.className = "result-area";
      area.style.display = "none";
    }
    if (header) header.style.display = "none";
  }

  // ---------- Check Selected Prephrase ----------
  async checkSelected() {
    const name = this.getSelectedPrephrase();
    if (!this.currentProject || !name) return;

    this.updateStatus("checking", "Checking prepositional phrase...");

    try {
      const url = `${API_BASE}/check_parser/${encodeURIComponent(this.currentProject)}/${encodeURIComponent(name)}?type=prep_phrase_parser`;
      debug("[checkSelected] URL:", url);
      const res = await fetch(url);
      const data = await res.json();

      if (data.error) {
        this.updateStatus("error", `Error: ${data.error}`);
        this.setButtonEnabled(false);
        return;
      }

      if (data.exists && data.valid) {
        this.updateStatus("ready", "Ready to configure");
        this.displayInfo(data);
      } else if (data.exists && !data.valid) {
        this.updateStatus("error", "Invalid script");
        this.setButtonEnabled(false);
        this.displayInfo(data);
      } else {
        this.updateStatus("error", "Script not found");
        this.setButtonEnabled(false);
      }
    } catch (err) {
      console.error("checkSelected error:", err);
      this.updateStatus("error", "Check failed");
      this.setButtonEnabled(false);
    }
  }

  displayInfo(data) {
    const area = document.getElementById(this.ui.prepResult);
    const header = document.getElementById(this.ui.resultHeader);
    if (!area) return;

    let info = `Script Info:\n`;
    info += `- Exists: ${!!data.exists}\n`;
    info += `- Has TOOL spec: ${!!data.has_tool}\n`;
    info += `- Has run(): ${!!data.has_run}\n`;
    info += `- Valid: ${!!data.valid}\n`;
    if (data.tool_spec) {
      info += `\nTOOL Spec:\n${JSON.stringify(data.tool_spec, null, 2)}\n`;
    }
    if (data.load_error) {
      info += `\nLoad Error: ${data.load_error}\n`;
    }

    area.textContent = info;
    area.className = "result-area info";
    area.style.display = "block";
    if (header) header.style.display = "flex";
  }

  // ---------- Dynamic Settings Card ----------
  ensurePrepSettingsCard(clearOnly = false) {
    const anchor = document.getElementById(this.ui.prepSelect)?.closest(".card-body");
    if (!anchor) {
      debug("[prep.ui] No anchor found for settings card");
      return null;
    }

    let wrap = document.getElementById(this.ui.settingsWrapId);
    if (wrap && clearOnly) {
      debug("[prep.ui] Clearing settings card");
      wrap.remove();
      return null;
    }
    if (wrap) return wrap;

    // Create settings card
    wrap = document.createElement("div");
    wrap.id = this.ui.settingsWrapId;
    wrap.style.marginTop = "12px";
    wrap.innerHTML = `
      <div class="card" id="${this.ui.settingsCardId}">
        <div class="card-header">Configuration Settings</div>
        <div class="card-body" id="${this.ui.settingsBodyId}">
          <div id="${this.ui.settingsInfoId}" class="muted"></div>
          <form id="${this.ui.settingsFormId}" style="margin-top:8px;"></form>
          <div style="margin-top:10px; display:flex; gap:8px;">
            <button type="button" id="${this.ui.settingsConfirmBtnId}" class="btn btn-primary">Confirm Settings</button>
            <button type="button" id="${this.ui.settingsResetBtnId}" class="btn">Reset</button>
          </div>
        </div>
      </div>
    `;
    
    // Insert before the run button
    const runBtn = document.getElementById(this.ui.prepButton);
    if (runBtn) {
      runBtn.parentElement.insertBefore(wrap, runBtn);
    } else {
      anchor.appendChild(wrap);
    }

    // Wire buttons
    const confirmBtn = document.getElementById(this.ui.settingsConfirmBtnId);
    const resetBtn = document.getElementById(this.ui.settingsResetBtnId);
    if (confirmBtn) {
      confirmBtn.addEventListener("click", () => this.confirmPrepSettings());
    }
    if (resetBtn) {
      resetBtn.addEventListener("click", () => this.resetPrepSettings());
    }

    debug("[prep.ui] Settings card created");
    return wrap;
  }

  // ----------- Outputs Card -----------
  ensureOutputsCard(clearOnly = false) {
    const anchor = document.getElementById(this.ui.prepSelect)?.closest(".card-body");
    if (!anchor) return null;

    let wrap = document.getElementById(this.ui.outputsWrapId);
    if (wrap && clearOnly) { wrap.remove(); return null; }
    if (wrap) return wrap;

    wrap = document.createElement("div");
    wrap.id = this.ui.outputsWrapId;
    wrap.style.marginTop = "12px";
    wrap.innerHTML = `
      <div class="card">
        <div class="card-header" style="display:flex;align-items:center;gap:8px;">
          <span>Prepositional Phrase Outputs</span>
          <button type="button" id="${this.ui.outputsRefreshBtnId}" class="btn btn-small" style="margin-left:auto;">Refresh</button>
        </div>
        <div class="card-body" id="${this.ui.outputsBodyId}">
          <div class="muted">Choose a project to list outputs.</div>
        </div>
      </div>
    `;
    anchor.appendChild(wrap);

    const refresh = document.getElementById(this.ui.outputsRefreshBtnId);
    if (refresh) refresh.addEventListener("click", () => this.loadOutputsTree());

    return wrap;
  }

  // ----------- Outputs Tree + Download -----------
  async loadOutputsTree() {
    this.ensureOutputsCard();
    const body = document.getElementById(this.ui.outputsBodyId);
    if (!body) return;

    if (!this.currentProject) {
      body.innerHTML = `<div class="muted">Select a project to view outputs.</div>`;
      return;
    }

    body.innerHTML = `<div class="muted">Loading…</div>`;
    const url = `${API_BASE}/pphrase_outputs/${encodeURIComponent(this.currentProject)}/tree`;

    try {
      const res = await fetch(url);
      const data = await res.json();
      if (!res.ok) throw new Error(data?.detail || `${res.status}`);

      const tree = data.tree;
      body.innerHTML = "";
      const children = Array.isArray(tree.children) ? tree.children : [];
      children.forEach(child => body.appendChild(this.renderOutputsAccordion(child)));
    } catch (e) {
      console.error("[outputs] fetch error", e);
      body.innerHTML = `<div class="error">Failed to load outputs: ${String(e.message || e)}</div>`;
    }
  }

  renderOutputsAccordion(node) {
    // Files
    if (node.type === "file") {
      const row = document.createElement("div");
      row.className = "file-row";
      row.style.display = "flex";
      row.style.alignItems = "center";
      row.style.justifyContent = "space-between";
      row.style.gap = "8px";

      const link = document.createElement("a");
      link.textContent = node.name;
      link.href = `${API_BASE}/pphrase_outputs/${encodeURIComponent(this.currentProject)}/download?path=${encodeURIComponent(node.path)}`;
      link.download = node.name;

      const meta = document.createElement("span");
      meta.className = "muted";
      meta.textContent = `${(node.size ?? 0).toLocaleString()} bytes · ${node.mtime ?? ""}`;

      row.appendChild(link);
      row.appendChild(meta);
      return row;
    }

    // Directories
    const det = document.createElement("details");
    det.open = node.path === "" || node.path === "."; // open top-level
    const sum = document.createElement("summary");
    sum.textContent = node.name || "(root)";
    sum.style.cursor = "pointer";
    sum.style.fontWeight = "600";
    det.appendChild(sum);

    const inner = document.createElement("div");
    inner.style.paddingLeft = "12px";

    const kids = Array.isArray(node.children) ? node.children : [];
    if (!kids.length) {
      const empty = document.createElement("div");
      empty.className = "muted";
      empty.textContent = "— empty —";
      inner.appendChild(empty);
    } else {
      for (const child of kids) {
        inner.appendChild(this.renderOutputsAccordion(child));
      }
    }

    det.appendChild(inner);
    return det;
  }

  buildUiSpecFromPrephrasePayload(payload) {
    debug("[prep.ui] Building UI spec from payload:", payload);

    const settingsSrc = Array.isArray(payload?.expanded) ? payload.expanded : [];
    debug("[prep.ui] Settings fields count:", settingsSrc.length);

    const fields = settingsSrc.map((s, idx) => {
      const kind = String(s.kind || "").toLowerCase();
      const id = s.id || `field_${idx}`;
      const label = s.label || id;
      const def = s.hasOwnProperty("default") ? s.default : undefined;
      const opts = Array.isArray(s.options) ? s.options : [];

      debug(`[prep.ui] Field[${idx}]`, { id, kind, label, default: def, options: opts });

      // Map GIMS kinds to UI types
      switch (kind) {
        case "bool":
          return { name: id, label, type: "checkbox", default: !!def };
        case "number":
          return { name: id, label, type: "number", default: (def ?? null) };
        case "text":
          return { name: id, label, type: "text", default: (def ?? "") };
        case "single":
          return { name: id, label, type: "select", options: opts, default: (def ?? "") };
        case "multi":
          return { name: id, label, type: "multiselect", options: opts, default: Array.isArray(def) ? def : [] };
        default:
          debug(`[prep.ui][warn] Unknown field kind, using text:`, kind);
          return { name: id, label, type: "text", default: (def ?? "") };
      }
    });

    const uiSpec = {
      title: `Configure "${payload?.pphrase_name || "pre-phrase"}"`,
      fields
    };

    debug("[prep.ui] UI spec built:", uiSpec);
    return uiSpec;
  }

  async handlePphraseSelected(pphraseName) {
    debug("[prep.ui] Handling selection:", pphraseName);
    const wrap = this.ensurePrepSettingsCard();
    const infoEl = document.getElementById(this.ui.settingsInfoId);
    const formEl = document.getElementById(this.ui.settingsFormId);

    if (!wrap || !infoEl || !formEl) {
      debug("[prep.ui] Missing elements for settings card");
      return;
    }

    formEl.innerHTML = "";
    infoEl.textContent = "Loading settings...";
    this.setButtonEnabled(false);

    const expandUrl = `${API_BASE}/prephrase/expand/${encodeURIComponent(this.currentProject)}`;
    debug("[prep.ui] Fetching expanded settings:", expandUrl);

    let payload = null;
    try {
      const res = await fetch(expandUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          pphrase_name: pphraseName,
          settings: [],
          user_values: {}
        })
      });
      debug("[prep.ui] Expand status:", res.status, res.statusText);
      payload = await res.json();
    } catch (e) {
      debug("[prep.ui][error] Expand fetch failed:", e);
      infoEl.textContent = "Failed to load settings (see console).";
      this.updateStatus("error", "Failed to load settings");
      return;
    }

    if (!payload || payload.ok !== true) {
      debug("[prep.ui][error] Invalid payload:", payload);
      infoEl.textContent = "Settings not available for this pre-phrase.";
      this.setButtonEnabled(true);
      this.updateStatus("ready", "Ready (no settings)");
      return;
    }

    const uiSpec = this.buildUiSpecFromPrephrasePayload(payload);

    if (!Array.isArray(uiSpec.fields) || uiSpec.fields.length === 0) {
      infoEl.textContent = `This pre-phrase has no settings. You can run it directly.`;
      this.setButtonEnabled(true);
      this.updateStatus("ready", "Ready to run");
      return;
    }

    infoEl.textContent = (uiSpec.title || "Configure options");
    this.renderUiSpecForm(formEl, uiSpec);
    this.updateStatus("error", "Configure settings then click Confirm");
    debug("[prep.ui] Form rendered with fields:", uiSpec.fields.map(f => f.name));
  }

  renderUiSpecForm(formEl, uiSpec) {
    debug("[prep.ui] Rendering form:", uiSpec);

    (uiSpec.fields || []).forEach((f, idx) => {
      const row = document.createElement("div");
      row.className = "form-row";
      row.style.marginBottom = "8px";

      const id = `pphrase-${f.name}`;
      const label = document.createElement("label");
      label.setAttribute("for", id);
      label.textContent = f.label || f.name || `field_${idx}`;
      label.style.display = "block";
      label.style.fontWeight = "600";
      label.style.marginBottom = "2px";

      let inputEl;
      const kind = String(f.type || "text").toLowerCase();

      switch (kind) {
        case "checkbox":
          inputEl = document.createElement("input");
          inputEl.type = "checkbox";
          inputEl.id = id;
          inputEl.name = f.name;
          inputEl.checked = !!f.default;
          break;

        case "number":
          inputEl = document.createElement("input");
          inputEl.type = "number";
          inputEl.id = id;
          inputEl.name = f.name;
          if (f.default !== undefined && f.default !== null) inputEl.value = String(f.default);
          break;

        case "select":
          inputEl = document.createElement("select");
          inputEl.id = id;
          inputEl.name = f.name;
          (f.options || []).forEach((opt, j) => {
            const o = document.createElement("option");
            o.value = String(opt.value ?? opt.label ?? `opt_${j}`);
            o.textContent = String(opt.label ?? opt.value ?? `opt_${j}`);
            if (String(f.default) === String(o.value)) o.selected = true;
            inputEl.appendChild(o);
          });
          break;

        case "multiselect":
          inputEl = document.createElement("div");
          inputEl.id = id;
          inputEl.name = f.name;
          inputEl.className = "multiselect-checkboxes";

          const defArr = Array.isArray(f.default) ? f.default.map(String) : [];

          (f.options || []).forEach((opt, j) => {
            const checkboxId = `${id}-${j}`;
            const wrapper = document.createElement("div");
            wrapper.className = "multiselect-option";

            const cb = document.createElement("input");
            cb.type = "checkbox";
            cb.id = checkboxId;
            cb.name = f.name;
            cb.value = String(opt.value ?? opt.label ?? `opt_${j}`);
            cb.checked = defArr.includes(cb.value);

            const lbl = document.createElement("label");
            lbl.setAttribute("for", checkboxId);
            lbl.textContent = String(opt.label ?? opt.value ?? `opt_${j}`);

            wrapper.appendChild(cb);
            wrapper.appendChild(lbl);
            inputEl.appendChild(wrapper);
          });
          break;

        default:
          inputEl = document.createElement("input");
          inputEl.type = "text";
          inputEl.id = id;
          inputEl.name = f.name;
          if (f.default !== undefined) inputEl.value = String(f.default);
      }

      row.appendChild(label);
      row.appendChild(inputEl);
      formEl.appendChild(row);
    });

    debug("[prep.ui] Form rendering complete");
  }

  confirmPrepSettings() {
    const form = document.getElementById(this.ui.settingsFormId);
    const infoEl = document.getElementById(this.ui.settingsInfoId);
    if (!form) {
      debug("[prep.ui] No form to confirm");
      return;
    }

    const params = this.collectFormValues(form);
    debug("[prep.ui] Collected params:", params);

    // Store params for running
    const paramsEl = document.getElementById("prep-params-json");
    if (paramsEl) {
      paramsEl.value = JSON.stringify(params, null, 2);
      debug("[prep.ui] Stored params in hidden field");
    }

    // Enable run button
    this.setButtonEnabled(true);
    this.updateStatus("ready", "Ready to run");
    if (infoEl) infoEl.textContent = "Settings confirmed. You can now run the pre-phrase.";
  }

  resetPrepSettings() {
    const form = document.getElementById(this.ui.settingsFormId);
    const infoEl = document.getElementById(this.ui.settingsInfoId);
    if (form) {
      debug("[prep.ui] Resetting form");
      form.reset();
    }
    const paramsEl = document.getElementById("prep-params-json");
    if (paramsEl) paramsEl.value = "";
    if (infoEl) infoEl.textContent = "Settings cleared.";
    this.setButtonEnabled(false);
    this.updateStatus("error", "Configure settings before running");
  }

  collectFormValues(form) {
    const params = {};
    const fields = Array.from(form.querySelectorAll("input, select, textarea"));

    // Count how many controls share each name
    const nameCounts = fields.reduce((m, el) => {
      const n = el.getAttribute("name");
      if (!n) return m;
      m[n] = (m[n] || 0) + 1;
      return m;
    }, {});

    for (const el of fields) {
      const name = el.getAttribute("name");
      if (!name) continue;

      // Handle date range fields
      if (name.endsWith("__start") || name.endsWith("__end")) {
        const base = name.replace(/__(start|end)$/, "");
        const start = form.querySelector(`[name="${base}__start"]`)?.value || "";
        const end = form.querySelector(`[name="${base}__end"]`)?.value || "";
        params[base] = [start, end];
        continue;
      }

      if (el instanceof HTMLInputElement) {
        if (el.type === "checkbox") {
          const isGroup = nameCounts[name] > 1;
          if (isGroup) {
            if (!Array.isArray(params[name])) params[name] = [];
            if (el.checked) params[name].push(el.value);
          } else {
            params[name] = !!el.checked;
          }
        } else if (el.type === "number") {
          const v = el.value;
          params[name] = (v === "" || v === null || Number.isNaN(Number(v))) ? null : Number(v);
        } else {
          params[name] = el.value;
        }
      } else if (el instanceof HTMLSelectElement) {
        if (el.multiple) {
          params[name] = Array.from(el.selectedOptions).map(o => o.value);
        } else {
          params[name] = el.value;
        }
      } else {
        params[name] = el.value;
      }
    }

    return params;
  }

  // ---------- Signature Handling ----------
  async waitForSignatureCompletion(originalRequest) {
    return new Promise((resolve) => {
      this.isWaitingForSignature = true;
      this.originalRequest = originalRequest;
      
      const completionHandler = (event) => {
        if (event.detail && event.detail.completed) {
          window.removeEventListener('prephrase:signature_resolved', completionHandler);
          this.isWaitingForSignature = false;
          resolve(event.detail.result);
        }
      };
      
      window.addEventListener('prephrase:signature_resolved', completionHandler);
      
      // Fallback timeout
      setTimeout(() => {
        if (this.isWaitingForSignature) {
          console.warn("[signature] Signature wait timeout");
          window.removeEventListener('prephrase:signature_resolved', completionHandler);
          this.isWaitingForSignature = false;
          resolve({ error: "Signature process timeout" });
        }
      }, this.maxSignatureRetries * 1000);
    });
  }

  handleSignatureCompletion(result) {
    this.isWaitingForSignature = false;
    this.signatureRetryCount = 0;
    
    if (result && result.ok) {
      this.renderResult(result);
    } else {
      this.displayError(result || { error: "Signature process completed but result unavailable" });
    }
  }

  // ---------- Run Prephrase ----------
  async runPrephrase() {
    const t0 = performance.now();
    console.groupCollapsed(`[PrephraseRunner] Running prepositional phrase`);
    
    try {
      const name = this.getSelectedPrephrase();
      console.debug(`[PrephraseRunner] Selected:`, name, `Project:`, this.currentProject);

      if (!this.currentProject || !name) {
        console.warn(`[PrephraseRunner] Missing project or prephrase name`);
        return;
      }

      const btn = document.getElementById(this.ui.prepButton);
      const spn = document.getElementById(this.ui.prepSpinner);
      const area = document.getElementById(this.ui.prepResult);
      const header = document.getElementById(this.ui.resultHeader);

      if (!btn || !spn || !area) {
        console.error(`[PrephraseRunner] Missing UI elements`);
        return;
      }

      // UI state
      btn.disabled = true;
      spn.classList.add("active");
      area.className = "result-area";
      area.style.display = "none";
      if (header) header.style.display = "none";

      // Collect params from form
      let params = {};
      const form = document.getElementById(this.ui.settingsFormId);
      if (form) {
        params = this.collectFormValues(form);
      }

      // Fallback to textarea if form empty
      if (!Object.keys(params).length) {
        const paramsEl = document.getElementById("prep-params-json");
        if (paramsEl && paramsEl.value.trim()) {
          try {
            params = JSON.parse(paramsEl.value);
          } catch (e) {
            console.warn(`[PrephraseRunner] Invalid params JSON, using empty {}`, e);
          }
        }
      }

      console.debug(`[PrephraseRunner] Params:`, params);

      // Build request
      const qp = new URLSearchParams();
      qp.set("parser_type", "prep_phrase_parser");
      qp.set("exec_mode", "native");

      const endpoint = `${API_BASE}/test_parser/${encodeURIComponent(this.currentProject)}/${encodeURIComponent(name)}?${qp.toString()}`;
      const method = "POST";
      const body = { params };

      console.groupCollapsed(`[PrephraseRunner] Request`);
      console.info(`→ ${method} ${endpoint}`);
      console.debug(`Body:`, body);
      console.groupEnd();

      const tFetch0 = performance.now();
      let res;
      try {
        res = await fetch(endpoint, {
          method,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
      } catch (netErr) {
        const dt = (performance.now() - tFetch0).toFixed(1);
        console.error(`[PrephraseRunner] Network error after ${dt}ms:`, netErr);
        this.displayError({ error: `Network error: ${String(netErr?.message || netErr)}` });
        return;
      }

      const fetchMs = (performance.now() - tFetch0).toFixed(1);
      console.info(`[PrephraseRunner] Response: ${res.status} ${res.statusText} (${fetchMs}ms)`);

      let text = "";
      try {
        text = await res.text();
      } catch (rxErr) {
        console.error(`[PrephraseRunner] Failed reading response`, rxErr);
        this.displayError({ error: `Failed to read response: ${String(rxErr?.message || rxErr)}` });
        return;
      }

      let result = null;
      try {
        result = text ? JSON.parse(text) : {};
      } catch (jxErr) {
        console.warn(`[PrephraseRunner] Non-JSON response`, jxErr);
        result = { ok: false, error: "Non-JSON response from server", raw: text };
      }

      // Check for the 202 "Accepted" status, which indicates a pending trigger
      if (res.status === 202 && (result?.trigger || result?.handled)) {
        console.warn(`[PrephraseRunner] Operation paused for signature trigger.`);
        
        // Update UI to show waiting state
        this.updateStatus("checking", "Waiting for e-signature...");
        area.textContent = "⏳ E-signature required. Please complete the signature dialog.\n\nThe operation will continue automatically after signing.";
        area.className = "result-area info";
        area.style.display = "block";
        if (header) header.style.display = "flex";
        
        // Store the original request for potential retry
        const originalRequest = { endpoint, method, body };
        
        // Wait for signature completion with timeout
        try {
          console.info(`[PrephraseRunner] Waiting for signature completion...`);
          const finalResult = await this.waitForSignatureCompletion(originalRequest);
          
          if (finalResult && !finalResult.error) {
            console.info(`[PrephraseRunner] Signature process completed successfully`);
            this.renderResult(finalResult);
          } else {
            console.warn(`[PrephraseRunner] Signature process completed with error:`, finalResult?.error);
            this.displayError(finalResult || { error: "Signature process failed" });
          }
        } catch (waitErr) {
          console.error(`[PrephraseRunner] Error waiting for signature:`, waitErr);
          this.displayError({ error: `Error during signature process: ${String(waitErr?.message || waitErr)}` });
        }
        
        return;
      }

      console.debug(`[PrephraseRunner] Result:`, result);

      // Handle errors
      if (!res.ok) {
        const msg = result?.detail || result?.error || `${res.status} ${res.statusText}`;
        console.error(`[PrephraseRunner] HTTP error:`, msg);
        this.displayError({ error: msg, traceback: result?.traceback });
        return;
      }

      // Display success
      this.renderResult(result);

    } catch (err) {
      console.error(`[PrephraseRunner] Unexpected error`, err);
      this.displayError({ error: String(err?.message || err) });
    } finally {
      // Restore UI
      const btn = document.getElementById(this.ui.prepButton);
      const spn = document.getElementById(this.ui.prepSpinner);
      if (btn) btn.disabled = false;
      if (spn) spn.classList.remove("active");

      const totalMs = (performance.now() - t0).toFixed(1);
      console.info(`[PrephraseRunner] Complete — ${totalMs}ms`);
      console.groupEnd();
    }
  }

  renderResult(result) {
    if (result?.error) return this.displayError(result);
    this.displaySuccess(result);
  }

  displaySuccess(result) {
    const area = document.getElementById(this.ui.prepResult);
    const header = document.getElementById(this.ui.resultHeader);
    if (!area) return;

    let out = `✅ Prepositional Phrase executed successfully!\n\n`;
    out += `Status: ${result.status || (result.ok ? "Success" : "Failed")}\n`;

    if (Array.isArray(result.produced) && result.produced.length) {
      out += `\nProduced files:\n`;
      result.produced.forEach((f) => (out += `  • ${f}\n`));
    }

    if (result.output_files) {
      out += `\nOutput file contents:\n`;
      for (const [fn, fd] of Object.entries(result.output_files)) {
        out += `\n--- ${fn} ---\n`;
        if (fd.error) out += `Error: ${fd.error}\n`;
        else out += `${fd.content}\n(${fd.size} characters)\n`;
      }
    }

    if (result.post_doc) {
      out += `\nPost-doc execution:\n`;
      out += `  • Status: ${result.post_doc.ok ? "Success" : "Failed"}\n`;
      if (result.post_doc.error) out += `  • Error: ${result.post_doc.error}\n`;
      if (result.post_doc.return) out += `  • Return value: ${JSON.stringify(result.post_doc.return)}\n`;
    }

    if (Array.isArray(result.logs) && result.logs.length) {
      out += `\nLogs:\n`;
      result.logs.forEach((l) => (out += `${l}\n`));
    }

    area.textContent = out;
    area.className = "result-area success";
    area.style.display = "block";
    if (header) header.style.display = "flex";

    // Refresh outputs so newly produced files appear
    this.loadOutputsTree();
  }

  displayError(result) {
    const area = document.getElementById(this.ui.prepResult);
    const header = document.getElementById(this.ui.resultHeader);
    if (!area) return;

    let out = `❌ Prepositional Phrase execution failed!\n\n`;
    out += `Error: ${result?.error}\n`;
    if (result?.traceback) out += `\nTraceback:\n${result.traceback}`;

    area.textContent = out;
    area.className = "result-area error";
    area.style.display = "block";
    if (header) header.style.display = "flex";
  }
}

// Initialize
document.addEventListener("DOMContentLoaded", () => new PrephraseRunner());