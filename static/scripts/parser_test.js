// /static/scripts/parser_test.js
// Frontend JavaScript for parser testing interface

// Debug control - set to false to disable all grid debug logging
const DEBUG_ENABLED = false; // Change to true to enable debug logs
// Debug helper that respects the flag
const debug = DEBUG_ENABLED ? console.debug.bind(console) : () => {};

const API_BASE = "/api/parser_test";
const CUSTOM_API = "/custom"; // still used for listing available scripts

class ParserTestInterface {
  constructor() {
    this.currentProject = null;

    // card/kind definitions
    this.cards = {
      custom: {
        selectId: "custom-select",
        buttonId: "custom-button",
        spinnerId: "custom-spinner",
        resultId: "custom-result",
        statusDotId: "custom-dot",
        statusTextId: "custom-text",
        expectedId: "custom-expected",
        // created dynamically:
        runWrapId: "custom-run-wrap",
        runSelectId: "custom-run-select",
      },
      prep: {
        selectId: "prep-select",
        buttonId: "prep-button",
        spinnerId: "prep-spinner",
        resultId: "prep-result",
        statusDotId: "prep-dot",
        statusTextId: "prep-text",
        expectedId: "prep-expected",

        // dynamically created UI for pre-phrase settings
        settingsWrapId: "prep-settings-wrap",
        settingsCardId: "prep-settings-card",
        settingsBodyId: "prep-settings-body",
        settingsFormId: "prep-settings-form",
        settingsConfirmBtnId: "prep-settings-confirm",
        settingsResetBtnId: "prep-settings-reset",
        settingsInfoId: "prep-settings-info",
      },
    };

    this.init();
  }

  // ---------- bootstrap ----------
  init() {
    debug("[init] boot");
    this.loadProjects();
    this.setupEventListeners();
  }

  setupEventListeners() {
    // Run test buttons
    for (const kind of Object.keys(this.cards)) {
      const btn = document.getElementById(this.cards[kind].buttonId);
      if (btn) btn.addEventListener("click", () => this.testSelected(kind));
    }

    // Project selector
    const projectSelect = document.getElementById("project-select");
    if (projectSelect) {
      projectSelect.addEventListener("change", (e) =>
        this.onProjectChange(e.target.value)
      );
    }

    // Dropdown changes
    const customSelect = document.getElementById(this.cards.custom.selectId);
    if (customSelect) {
      customSelect.addEventListener("change", () => {
        const name = this.getSelectedName("custom");
        debug("[custom.select] changed →", name);
        this.updateExpected("custom");
        // Clear prior run selection/area when parser changes
        this.ensureRunSelect("custom", true);
        this.setButtonEnabled("custom", false);
        if (name) this.checkSelected("custom");
        else this.updateStatus("custom", "error", "Select a parser");
      });
    }

    const prepSelect = document.getElementById(this.cards.prep.selectId);
    if (prepSelect) {
      prepSelect.addEventListener("change", async () => {
        const name = this.getSelectedName("prep");
        debug("[prep.select] changed →", name);
        this.updateExpected("prep");
        this.setButtonEnabled("prep", !!name);
        if (name) {
          await this.checkSelected("prep");
          // Build / update the settings card for the chosen pre-phrase
          this.handlePphraseSelected(name);
        } else {
          // clear settings card if no selection
          this.ensurePrepSettingsCard(true);
          this.updateStatus("prep", "error", "Select a prepositional phrase");
        }
      });
    }
  }

  // ---------- projects ----------
  async loadProjects() {
    const projectSelect = document.getElementById("project-select");
    if (!projectSelect) return;

    try {
      debug("[projects] loading…");
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
      debug("[projects] defaulting to:", projects[0]);
      this.onProjectChange(projects[0]);
    } catch (err) {
      console.error("Error loading projects:", err);
      projectSelect.innerHTML = `<option value="">Error loading projects</option>`;
      projectSelect.disabled = true;
    }
  }

  async onProjectChange(projectName) {
    debug("[project.change] ->", projectName);
    if (!projectName) {
      this.currentProject = null;
      this.resetCards();
      return;
    }
    this.currentProject = projectName;
    this.resetCards();
    await this.populateScriptDropdowns();
    this.loadProjectInfo(); // optional
  }

  // ---------- dropdown population ----------
  async populateScriptDropdowns() {
    if (!this.currentProject) return;

    const customSel = document.getElementById(this.cards.custom.selectId);
    const prepSel = document.getElementById(this.cards.prep.selectId);

    if (customSel) customSel.innerHTML = `<option value="">-- Select a parser --</option>`;
    if (prepSel)   prepSel.innerHTML   = `<option value="">-- Select a prepositional phrase --</option>`;

    this.updateStatus("custom", "checking", "Checking...");
    this.updateStatus("prep", "checking", "Checking...");
    this.setButtonEnabled("custom", false);
    this.setButtonEnabled("prep", false);

    try {
      debug("[populate] listing scripts for project:", this.currentProject);
      // Your existing list endpoint now returns {parsers:[...], pphrases:[...]}
      const res = await fetch(
        `${CUSTOM_API}/parser/list?project=${encodeURIComponent(this.currentProject)}`
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`);
      const data = await res.json();
      debug("[populate] response:", data);

      const parsers = data?.parsers || data?.items || []; // tolerate old shape
      if (customSel) {
        parsers.forEach((p) => {
          const name = p.name || p;
          const opt = document.createElement("option");
          opt.value = name;
          opt.textContent = name;
          customSel.appendChild(opt);
        });
      }

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

      this.updateStatus("custom", parsers.length ? "error" : "error", parsers.length ? "Select a parser" : "No parsers found");
      this.updateStatus("prep", pphrases.length ? "error" : "error", pphrases.length ? "Select a prepositional phrase" : "No pphrases found");

      this.updateExpected("custom");
      this.updateExpected("prep");
    } catch (err) {
      console.error("Error loading scripts:", err);
      this.updateStatus("custom", "error", "Failed to list parsers");
      this.updateStatus("prep", "error", "Failed to list prepositional phrases");
    }
  }

  // ---------- status/helpers ----------
  resetCards() {
    debug("[reset] resetting cards");
    for (const kind of Object.keys(this.cards)) {
      const { resultId } = this.cards[kind];
      this.updateStatus(kind, "checking", "Checking...");
      this.setButtonEnabled(kind, false);

      const area = document.getElementById(resultId);
      if (area) {
        area.textContent = "";
        area.className = "result-area";
        area.style.display = "none";
      }

      // reset expected placeholder
      this.updateExpected(kind, true);

      // clear run select if exists (custom)
      if (kind === "custom") this.ensureRunSelect("custom", true);

      // clear dynamic settings card (prep)
      if (kind === "prep") this.ensurePrepSettingsCard(true);
    }
  }

  updateExpected(kind, placeholderOnly = false) {
    const codeEl = document.getElementById(this.cards[kind].expectedId);
    if (!codeEl) return;

    const name = this.getSelectedName(kind);
    const base = kind === "custom" ? "custom/parsers" : "custom/prepositional phrases";
    if (name && !placeholderOnly) {
      codeEl.textContent = `${base}/${name}/${name}.py`;
    } else {
      codeEl.textContent =
        kind === "custom"
          ? "custom/parsers/{parser_name}/{parser_name}.py"
          : "custom/prepositional phrases/{pphrase_name}/{pphrase_name}.py";
    }
  }

  updateStatus(kind, state, text) {
    debug("[status]", kind, "→", state, "|", text);
    const dot = document.getElementById(this.cards[kind].statusDotId);
    const t = document.getElementById(this.cards[kind].statusTextId);
    if (dot) dot.className = `status-dot ${state}`;
    if (t) t.textContent = text;
  }

  setButtonEnabled(kind, enabled) {
    debug("[button.enable]", kind, "→", enabled);
    const btn = document.getElementById(this.cards[kind].buttonId);
    if (btn) btn.disabled = !enabled;
  }

  getSelectedName(kind) {
    const sel = document.getElementById(this.cards[kind].selectId);
    const v = sel ? (sel.value || "").trim() : "";
    return v;
  }

  // Ensure run select exists/cleared in custom card
  ensureRunSelect(kind, clearOnly = false) {
    if (kind !== "custom") return;
    const cardBody = document.querySelector(`#${this.cards.custom.resultId}`)?.closest(".card-body")
                   || document.getElementById(this.cards.custom.selectId)?.closest(".card-body");
    if (!cardBody) return;

    let wrap = document.getElementById(this.cards.custom.runWrapId);
    if (!wrap && !clearOnly) {
      debug("[custom.run] creating run selector UI");
      wrap = document.createElement("div");
      wrap.id = this.cards.custom.runWrapId;
      wrap.style.marginTop = "12px";
      wrap.innerHTML = `
        <label for="${this.cards.custom.runSelectId}">Select Run:</label>
        <select id="${this.cards.custom.runSelectId}" class="script-select">
          <option value="">-- Select a run --</option>
        </select>
      `;
      // insert after parser select
      const parserSelect = document.getElementById(this.cards.custom.selectId);
      parserSelect?.parentElement?.insertBefore(wrap, parserSelect.nextSibling);
      const runSel = document.getElementById(this.cards.custom.runSelectId);
      runSel.addEventListener("change", () => {
        const has = !!runSel.value;
        if (has) this.setButtonEnabled("custom", true);
        else this.setButtonEnabled("custom", false);
      });
      return;
    }

    // clear options
    const runSel = document.getElementById(this.cards.custom.runSelectId);
    if (runSel) {
      debug("[custom.run] clearing run selector options");
      runSel.innerHTML = `<option value="">-- Select a run --</option>`;
    }
  }

  // ---------- dynamic PREP settings card ----------
  ensurePrepSettingsCard(clearOnly = false) {
    const ids = this.cards.prep;
    const anchor = document.getElementById(ids.selectId)?.closest(".card-body");
    if (!anchor) {
      debug("[prep.ui] no anchor .card-body found for settings card");
      return null;
    }

    let wrap = document.getElementById(ids.settingsWrapId);
    if (wrap && clearOnly) {
      debug("[prep.ui] clearing settings card");
      wrap.remove();
      return null;
    }
    if (wrap) return wrap;

    // create
    wrap = document.createElement("div");
    wrap.id = ids.settingsWrapId;
    wrap.style.marginTop = "12px";
    wrap.innerHTML = `
      <div class="card" id="${ids.settingsCardId}">
        <div class="card-header">Pre-phrase settings</div>
        <div class="card-body" id="${ids.settingsBodyId}">
          <div id="${ids.settingsInfoId}" class="muted"></div>
          <form id="${ids.settingsFormId}" style="margin-top:8px;"></form>
          <div style="margin-top:10px; display:flex; gap:8px;">
            <button type="button" id="${ids.settingsConfirmBtnId}" class="btn btn-primary">Confirm settings</button>
            <button type="button" id="${ids.settingsResetBtnId}" class="btn">Reset</button>
          </div>
        </div>
      </div>
    `;
    anchor.appendChild(wrap);

    // wire buttons
    const confirmBtn = document.getElementById(ids.settingsConfirmBtnId);
    const resetBtn = document.getElementById(ids.settingsResetBtnId);
    if (confirmBtn) {
      confirmBtn.addEventListener("click", () => this.confirmPrepSettings());
    }
    if (resetBtn) {
      resetBtn.addEventListener("click", () => this.resetPrepSettings());
    }

    debug("[prep.ui] settings card created");
    return wrap;
  }

  // --- Build UI spec directly from expanded prephrase settings ---
  buildUiSpecFromPrephrasePayload(payload) {
    debug("[prep.ui] buildUiSpecFromPrephrasePayload: begin", payload);

    // Always prefer the expanded settings coming from backend
    const settingsSrc = Array.isArray(payload?.expanded)
      ? payload.expanded
      : [];

    debug("[prep.ui] settings source chosen; fields:", settingsSrc.length);

    const fields = settingsSrc.map((s, idx) => {
      const kind  = String(s.kind || "").toLowerCase();
      const id    = s.id || `field_${idx}`;
      const label = s.label || id;
      const def   = s.hasOwnProperty("default") ? s.default : undefined;
      const opts  = Array.isArray(s.options) ? s.options : [];

      debug(`[prep.ui] field[${idx}]`, { id, kind, label, default: def, options: opts });

      // Map GIMS kinds → UI types
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
          debug(`[prep.ui][warn] field kind not recognized; coercing to text:`, kind);
          return { name: id, label, type: "text", default: (def ?? "") };
      }
    });

    const uiSpec = {
      title: `Configure “${payload?.pphrase_name || "pre-phrase"}”`,
      fields
    };

    debug("[prep.ui] buildUiSpecFromPrephrasePayload: built uiSpec", uiSpec);
    return uiSpec;
  }

  async handlePphraseSelected(pphraseName) {
    debug("[prep.ui] handle selection:", pphraseName, "project:", this.currentProject);
    const wrap = this.ensurePrepSettingsCard();
    const ids = this.cards.prep;
    const infoEl = document.getElementById(ids.settingsInfoId);
    const formEl = document.getElementById(ids.settingsFormId);

    if (!wrap || !infoEl || !formEl) {
      debug("[prep.ui] missing elements for settings card");
      return;
    }

    formEl.innerHTML = "";
    infoEl.textContent = "Loading settings…";
    this.setButtonEnabled("prep", false);

    const expandUrl = `${API_BASE}/prephrase/expand/${encodeURIComponent(this.currentProject)}`;
    debug("[prep.ui] fetching expanded prephrase settings:", expandUrl);

    let payload = null;
    try {
      const res = await fetch(expandUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          pphrase_name: pphraseName,   // ✅ send prephrase name
          settings: [],                // backend will load PREPHRASE_SETTINGS
          user_values: {}              // initial empty values
        })
      });
      debug("[prep.ui] expand status:", res.status, res.statusText);
      payload = await res.json();
    } catch (e) {
      debug("[prep.ui][error] expand fetch/parse failed:", e);
      infoEl.textContent = "Failed to load settings (see console).";
      this.updateStatus("prep", "error", "Failed to load settings");
      return;
    }

    if (!payload || payload.ok !== true) {
      debug("[prep.ui][error] invalid payload:", payload);
      infoEl.textContent = "Settings not available for this pre-phrase.";
      this.setButtonEnabled("prep", true);
      this.updateStatus("prep", "ready", "Ready (no settings)");
      return;
    }

    const uiSpec = this.buildUiSpecFromPrephrasePayload(payload);

    if (!Array.isArray(uiSpec.fields) || uiSpec.fields.length === 0) {
      infoEl.textContent = `This pre-phrase has no settings. You can run it directly.`;
      this.setButtonEnabled("prep", true);
      this.updateStatus("prep", "ready", "Ready (no settings)");
      return;
    }

    infoEl.textContent = (uiSpec.title || "Configure options");
    this.renderUiSpecForm(formEl, uiSpec);
    this.updateStatus("prep", "error", "Adjust settings then click Confirm");
    debug("[prep.ui] form rendered with fields:", uiSpec.fields.map(f => f.name));
  }

  renderUiSpecForm(formEl, uiSpec) {
    debug("[prep.ui] renderUiSpecForm: begin", uiSpec);

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
      debug(`[prep.ui] render field[${idx}]`, { name: f.name, type: f.type, default: f.default, optionsLen: (f.options || []).length });

      const kind = String(f.type || "text").toLowerCase();
      console.debug("[ui.switch] start", { name: f.name, kind, options: f.options, default: f.default });

      switch (kind) {
        case "checkbox":
          inputEl = document.createElement("input");
          inputEl.type = "checkbox";
          inputEl.id = id;
          inputEl.name = f.name;
          inputEl.checked = !!f.default;
          console.debug("[ui.switch] checkbox", { id, checked: inputEl.checked });
          break;

        case "number":
          inputEl = document.createElement("input");
          inputEl.type = "number";
          inputEl.id = id;
          inputEl.name = f.name;
          if (f.default !== undefined && f.default !== null) inputEl.value = String(f.default);
          console.debug("[ui.switch] number", { id, value: inputEl.value });
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
          console.debug("[ui.switch] select", { id, optionsLen: (f.options || []).length, default: f.default });
          break;

        case "multiselect":
          // Container div for checkboxes
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

          console.debug("[ui.switch] multiselect-checkboxes", {
            id,
            optionsLen: (f.options || []).length,
            defaults: defArr
          });
          break;

        default:
          inputEl = document.createElement("input");
          inputEl.type = "text";
          inputEl.id = id;
          inputEl.name = f.name;
          if (f.default !== undefined) inputEl.value = String(f.default);
          console.debug("[ui.switch] default->text", { id, value: inputEl.value });
      }


      row.appendChild(label);
      row.appendChild(inputEl);
      formEl.appendChild(row);
    });

    debug("[prep.ui] renderUiSpecForm: done");
  }

  confirmPrepSettings() {
    const ids = this.cards.prep;
    const form = document.getElementById(ids.settingsFormId);
    const infoEl = document.getElementById(ids.settingsInfoId);
    if (!form) {
      debug("[prep.ui] confirm clicked but no form present");
      return;
    }

    const params = this.collectFormValues(form);
    debug("[prep.ui] collected params (final):", params);

    // Store into the shared params textarea if it exists
    const paramsEl = document.getElementById("prep-params-json");
    if (paramsEl) {
      paramsEl.value = JSON.stringify(params, null, 2);
      debug("[prep.ui] wrote params to #prep-params-json:", paramsEl.value);
    } else {
      debug("[prep.ui] no #prep-params-json field found; skipping write");
    }

    // Enable Run button and update status
    this.setButtonEnabled("prep", true);
    this.updateStatus("prep", "ready", "Ready with settings");
    if (infoEl) infoEl.textContent = "Settings confirmed. You can run the pre-phrase.";
  }

  resetPrepSettings() {
    const ids = this.cards.prep;
    const form = document.getElementById(ids.settingsFormId);
    const infoEl = document.getElementById(ids.settingsInfoId);
    if (form) {
      debug("[prep.ui] reset clicked; clearing form");
      form.reset();
    }
    const paramsEl = document.getElementById("prep-params-json");
    if (paramsEl) paramsEl.value = "";
    if (infoEl) infoEl.textContent = "Settings cleared.";
    this.setButtonEnabled("prep", false);
    this.updateStatus("prep", "error", "Adjust settings or run if none required");
  }

  collectFormValues(form) {
    const params = {};
    const fields = Array.from(form.querySelectorAll("input, select, textarea"));

    // Pre-scan to count how many controls share each name
    const nameCounts = fields.reduce((m, el) => {
      const n = el.getAttribute("name");
      if (!n) return m;
      m[n] = (m[n] || 0) + 1;
      return m;
    }, {});

    for (const el of fields) {
      const name = el.getAttribute("name");
      if (!name) continue;

      // daterange fields: name__start/name__end → [start, end]
      if (name.endsWith("__start") || name.endsWith("__end")) {
        const base = name.replace(/__(start|end)$/, "");
        const start = form.querySelector(`[name="${base}__start"]`)?.value || "";
        const end   = form.querySelector(`[name="${base}__end"]`)?.value || "";
        params[base] = [start, end];
        continue;
      }

      if (el instanceof HTMLInputElement) {
        if (el.type === "checkbox") {
          const isGroup = nameCounts[name] > 1; // multiple checkboxes with same name
          if (isGroup) {
            if (!Array.isArray(params[name])) params[name] = [];
            if (el.checked) params[name].push(el.value);
          } else {
            // single checkbox → boolean
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


  // ---------- check existence/then load runs for custom ----------
  async checkSelected(kind) {
    const name = this.getSelectedName(kind);
    if (!this.currentProject || !name) return;

    this.updateStatus(kind, "checking", "Checking...");

    try {
      const parserType = kind === "custom" ? "custom_parser" : "prep_phrase_parser";
      const url = `${API_BASE}/check_parser/${encodeURIComponent(this.currentProject)}/${encodeURIComponent(name)}?type=${parserType}`;
      debug("[checkSelected]", kind, "→", url);
      const res = await fetch(url);
      const data = await res.json();

      if (data.error) {
        this.updateStatus(kind, "error", `Error: ${data.error}`);
        this.setButtonEnabled(kind, false);
        return;
      }

      if (data.exists && data.valid) {
        this.updateStatus(kind, "ready", "Ready");
        // show brief info
        this.displayInfo(kind, data);

        // For custom parsers we now must choose a run before enabling Test
        if (kind === "custom") {
          this.setButtonEnabled("custom", false);
          await this.loadRunsForParser(name);
        } else {
          // prep stays disabled until settings are confirmed (if any)
          this.setButtonEnabled("prep", true);
        }
      } else if (data.exists && !data.valid) {
        this.updateStatus(kind, "error", "Invalid script");
        this.setButtonEnabled(kind, false);
        this.displayInfo(kind, data);
      } else {
        this.updateStatus(kind, "error", "Not found");
        this.setButtonEnabled(kind, false);
      }
    } catch (err) {
      console.error("checkSelected error:", err);
      this.updateStatus(kind, "error", "Check failed");
      this.setButtonEnabled(kind, false);
    }
  }

  async loadRunsForParser(parserName) {
    // ensure the UI slot exists
    this.ensureRunSelect("custom");
    const runSel = document.getElementById(this.cards.custom.runSelectId);
    if (!runSel) return;

    runSel.disabled = true;
    runSel.innerHTML = `<option value="">Loading runs…</option>`;

    try {
      debug("[custom.run] fetching runs for:", parserName);
      const res = await fetch(`${API_BASE}/check_parser/get_runs/${encodeURIComponent(this.currentProject)}/${encodeURIComponent(parserName)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`);
      const data = await res.json();

      debug("loadRunsForParser response:", data);

      const runs = Array.isArray(data) ? data : (data?.runs || []);
      const options = [];

      // Show last ~20 runs
      runs.slice(0, 20).forEach((r) => {
        const labelBits = [r.verb_group, r.run_id];
        if (r.test_type) labelBits.push(`(${r.test_type})`);
        if (r.date_tested) labelBits.push(`@ ${r.date_tested}`);
        const label = labelBits.join(" — ");
        const value = JSON.stringify({ verb_group: r.verb_group, run_id: r.run_id });
        options.push({ label, value });
      });

      if (options.length === 0) {
        runSel.innerHTML = `<option value="">No recent runs found</option>`;
        this.setButtonEnabled("custom", false);
      } else {
        runSel.innerHTML = `<option value="">-- Select a run --</option>`;
        options.forEach((o) => {
          const opt = document.createElement("option");
          opt.value = o.value;
          opt.textContent = o.label;
          runSel.appendChild(opt);
        });
      }
    } catch (err) {
      console.error("loadRunsForParser error:", err);
      runSel.innerHTML = `<option value="">Failed to load runs</option>`;
      this.setButtonEnabled("custom", false);
    } finally {
      runSel.disabled = false;
    }
  }

  displayInfo(kind, data) {
    const area = document.getElementById(this.cards[kind].resultId);
    if (!area) return;
    let info = `Script Info:\n`;
    info += `- exists: ${!!data.exists}\n`;
    info += `- has TOOL spec: ${!!data.has_tool}\n`;
    info += `- has run(): ${!!data.has_run}\n`;
    info += `- valid: ${!!data.valid}\n`;
    if (data.tool_spec) {
      info += `\nTOOL Spec:\n${JSON.stringify(data.tool_spec, null, 2)}\n`;
    }
    if (data.load_error) {
      info += `\nLoad Error: ${data.load_error}\n`;
    }
    area.textContent = info;
    area.className = "result-area info";
    area.style.display = "block";
  }

  // ---------- run test ----------
  async testSelected(kind) {
    const t0 = performance.now();
    console.groupCollapsed(`[ParserTest] testSelected(kind="${kind}") start`);
    try {
      const name = this.getSelectedName(kind);
      console.debug(`[ParserTest] selected name:`, name, `project:`, this.currentProject);

      if (!this.currentProject || !name) {
        console.warn(`[ParserTest] Missing project or parser name`, { project: this.currentProject, name });
        return;
      }

      const btn = document.getElementById(this.cards[kind].buttonId);
      const spn = document.getElementById(this.cards[kind].spinnerId);
      const area = document.getElementById(this.cards[kind].resultId);

      if (!btn || !spn || !area) {
        console.error(`[ParserTest] Missing UI elements`, { btn: !!btn, spn: !!spn, area: !!area });
        return;
      }

      // For custom parsers, require a run selection
      let runCtx = null;
      if (kind === "custom") {
        const runSel = document.getElementById(this.cards.custom.runSelectId);
        console.debug(`[ParserTest] run selector present:`, !!runSel, `value:`, runSel?.value);
        if (!runSel || !runSel.value) {
          this.updateStatus("custom", "error", "Select a run");
          console.warn(`[ParserTest] No run selected for custom parser`);
          return;
        }
        try {
          runCtx = JSON.parse(runSel.value); // {verb_group, run_id}
          if (!runCtx?.verb_group || !runCtx?.run_id) {
            throw new Error("Parsed run lacks verb_group/run_id");
          }
          console.info(`[ParserTest] Using run context:`, runCtx);
        } catch (e) {
          console.error(`[ParserTest] Failed to parse run selection`, e);
          this.updateStatus("custom", "error", "Invalid run selection");
          return;
        }
      }

      // UI state
      btn.disabled = true;
      spn.classList.add("active");
      area.className = "result-area";
      area.style.display = "none";

      // Optional toggles (if present)
      const execModeEl = document.getElementById(`${kind}-exec-mode`);
      const wasmPathEl = document.getElementById(`${kind}-python-wasm`);
      const paramsEl = document.getElementById(`${kind}-params-json`);

      const execMode = (execModeEl?.value || "native").toLowerCase(); // "native" | "wasm"
      const pythonWasm = (wasmPathEl?.value || "").trim();
      console.debug(`[ParserTest] execMode: ${execMode}`, `pythonWasm:`, pythonWasm || "(none)");

      // Params parsing
      let params = {};

      if (kind === "prep") {
        // grab directly from the live form
        const form = document.getElementById(this.cards.prep.settingsFormId);
        if (form) {
          params = this.collectFormValues(form);
        }

        // fallback: if form empty, use textarea if present
        if (!Object.keys(params).length) {
          const paramsEl = document.getElementById("prep-params-json");
          if (paramsEl && paramsEl.value.trim()) {
            try {
              params = JSON.parse(paramsEl.value);
            } catch (e) {
              console.warn(`[ParserTest] prep params invalid JSON, using empty {}`, e);
            }
          }
        }
      } else {
        // existing flow for custom parsers
        const paramsEl = document.getElementById(`${kind}-params-json`);
        if (paramsEl && paramsEl.value.trim()) {
          try {
            params = JSON.parse(paramsEl.value);
          } catch (e) {
            console.warn(`[ParserTest] params JSON invalid, falling back to {}`, e, `raw:`, paramsEl.value);
            params = {};
          }
        }
      }

      console.debug(`[ParserTest] params payload:`, params);

      // Build query for backend
      const qp = new URLSearchParams();
      qp.set("parser_type", kind === "custom" ? "custom_parser" : "prep_phrase_parser");
      qp.set("exec_mode", execMode);
      if (execMode === "wasm" && pythonWasm) qp.set("python_wasm", pythonWasm);
      if (kind === "custom") {
        qp.set("verb_group", runCtx.verb_group);
        qp.set("run_id", runCtx.run_id);
      }

      let endpoint = `${API_BASE}/test_parser/${encodeURIComponent(this.currentProject)}/${encodeURIComponent(name)}?${qp.toString()}`;
      let method = "POST";
      let body = { params };

      console.groupCollapsed(`[ParserTest] Request`);
      console.info(`→ ${method} ${endpoint}`);
      console.debug(`Headers:`, { "Content-Type": "application/json" });
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
        console.error(`[ParserTest] Fetch threw (network/connection) after ${dt}ms:`, netErr);
        this.displayTestError(kind, { error: `Network error: ${String(netErr?.message || netErr)}` });
        return;
      }

      const tFetch1 = performance.now();
      const fetchMs = (tFetch1 - tFetch0).toFixed(1);

      console.groupCollapsed(`[ParserTest] Response Meta (${fetchMs}ms)`);
      console.info(`Status: ${res.status} ${res.statusText}`);
      console.debug(`OK:`, res.ok, `URL:`, res.url);
      console.groupEnd();

      let text = "";
      try {
        text = await res.text();
      } catch (rxErr) {
        console.error(`[ParserTest] Failed reading response body as text`, rxErr);
        this.displayTestError(kind, { error: `Failed to read response: ${String(rxErr?.message || rxErr)}` });
        return;
      }

      let result = null;
      try {
        result = text ? JSON.parse(text) : {};
      } catch (jxErr) {
        console.warn(`[ParserTest] Response was not valid JSON; showing raw text`, jxErr, `raw:`, text);
        result = { ok: false, error: "Non-JSON response from server", raw: text };
      }

      console.groupCollapsed(`[ParserTest] Parsed Result`);
      console.debug(result);
      console.groupEnd();

      // Non-2xx handling: surface server-provided details if present
      if (!res.ok) {
        const msg = result?.detail || result?.error || `${res.status} ${res.statusText}`;
        console.error(`[ParserTest] HTTP not OK:`, msg, `result:`, result);
        this.displayTestError(kind, { error: msg, traceback: result?.traceback });
        return;
      }

      // Normal success path
      this.renderTestResult(kind, result);

    } catch (err) {
      console.error(`[ParserTest] Unexpected error in testSelected`, err);
      this.displayTestError(kind, { error: String(err?.message || err) });
    } finally {
      // Always restore UI + log timing
      const btn = document.getElementById(this.cards[kind].buttonId);
      const spn = document.getElementById(this.cards[kind].spinnerId);
      if (btn) btn.disabled = false;
      if (spn) spn.classList.remove("active");

      const t1 = performance.now();
      const totalMs = (t1 - t0).toFixed(1);
      console.info(`[ParserTest] testSelected(kind="${kind}") end — ${totalMs}ms`);
      console.groupEnd();
    }
  }

  renderTestResult(kind, result) {
    if (result?.error) return this.displayTestError(kind, result);
    this.displayTestSuccess(kind, result);
  }

  displayTestSuccess(kind, result) {
    const area = document.getElementById(this.cards[kind].resultId);
    if (!area) return;

    let out = `✅ ${kind === "custom" ? "Custom Parser" : "Prepositional Phrase"} executed successfully!\n\n`;
    out += `Status: ${result.ok ? "Success" : "Failed"}\n`;

    if (Array.isArray(result.produced) && result.produced.length) {
      out += `\nProduced files:\n`;
      result.produced.forEach((f) => (out += `- ${f}\n`));
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
      out += `- Status: ${result.post_doc.ok ? "Success" : "Failed"}\n`;
      if (result.post_doc.error) out += `- Error: ${result.post_doc.error}\n`;
      if (result.post_doc.return) out += `- Return value: ${JSON.stringify(result.post_doc.return)}\n`;
    }

    if (Array.isArray(result.logs) && result.logs.length) {
      out += `\nLogs:\n`;
      result.logs.forEach((l) => (out += `${l}\n`));
    }

    area.textContent = out;
    area.className = "result-area success";
    area.style.display = "block";
  }

  displayTestError(kind, result) {
    const area = document.getElementById(this.cards[kind].resultId);
    if (!area) return;

    let out = `❌ ${kind === "custom" ? "Custom Parser" : "Prepositional Phrase"} test failed!\n\n`;
    out += `Error: ${result?.error}\n`;
    if (result?.traceback) out += `\nTraceback:\n${result.traceback}`;

    area.textContent = out;
    area.className = "result-area error";
    area.style.display = "block";
  }

  // ---------- optional ----------
  async loadProjectInfo() {
    if (!this.currentProject) return;
    try {
      const res = await fetch(`${API_BASE}/project_info/${encodeURIComponent(this.currentProject)}`);
      const info = await res.json();
      debug("[project.info]", info);
      if (!info?.project_exists) console.warn("Project directory missing:", info?.project_path);
      if (!info?.custom_dir_exists) console.warn("Custom dir missing:", info?.custom_dir);
    } catch (err) {
      console.error("Error loading project info:", err);
    }
  }
}

// Init
document.addEventListener("DOMContentLoaded", () => new ParserTestInterface());

// Utilities (unchanged)
class ApiClient {
  static async get(endpoint) {
    const res = await fetch(`${API_BASE}${endpoint}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`);
    return await res.json();
  }
  static async post(endpoint, data = {}) {
    const res = await fetch(`${API_BASE}${endpoint}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`);
    return await res.json();
  }
}

// Global error handlers (optional)
window.addEventListener("unhandledrejection", (e) => {
  console.error("Unhandled promise rejection:", e.reason);
});
window.addEventListener("error", (e) => console.error("Global error:", e.error));

// Shortcuts
document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey && event.key === "r") || event.key === "F5") {
    event.preventDefault();
    location.reload();
  }
  if (event.ctrlKey && event.key === "t") {
    event.preventDefault();
    const customBtn = document.getElementById("custom-button");
    const prepBtn = document.getElementById("prep-button");
    if (customBtn && !customBtn.disabled) customBtn.click();
    setTimeout(() => {
      if (prepBtn && !prepBtn.disabled) prepBtn.click();
    }, 800);
  }
});
