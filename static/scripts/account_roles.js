/* Accounts & Roles Admin — Frontend (optimized)
   - Cancels overlapping refreshes via AbortController
   - Dedupes concurrent API calls so only one per resource is in-flight
   - Keeps CSRF & project discovery lean
*/

const DEBUG_ENABLED = false;
const debug = DEBUG_ENABLED ? console.debug.bind(console, "[admin-gui]") : () => {};

window.GIMS = window.GIMS || {};
(function prepareInjection() {
  window.GIMS.accountAdminQueue = window.GIMS.accountAdminQueue || [];
  window.GIMS.accountAdmin = {
    use(fn) {
      try {
        const ctx = { addStyles(css){ const s=document.createElement("style"); s.textContent=css; document.head.appendChild(s); }, getProject, setProject, refreshAll, debug };
        fn(ctx);
        debug("Injected plugin mounted:", fn.name || "(anonymous)");
      } catch (e) { console.error("Injection failed:", e); }
    },
  };
  for (const fn of window.GIMS.accountAdminQueue) { try { window.GIMS.accountAdmin.use(fn); } catch {} }
  window.GIMS.accountAdminQueue.length = 0;
})();

// ───────────────────────────── Auth + CSRF helpers ──────────────────────────
function token() { return localStorage.getItem("gims_token") || ""; }
function authHeaders(extra) { return Object.assign({ Authorization: "Bearer " + token() }, extra || {}); }
function assertAuthed() {
  if (!token()) { alert("Please sign in first."); if (window.gimsAuthOpen) window.gimsAuthOpen(); return false; }
  return true;
}

let CSRF_TOKEN = null;
let CSRF_PROMISE = null;
async function ensureCsrf() {
  if (CSRF_TOKEN) return CSRF_TOKEN;
  if (CSRF_PROMISE) return CSRF_PROMISE;
  const p = encodeURIComponent(getProject());
  CSRF_PROMISE = fetch(`/login/${p}/csrf`, { credentials: "include", signal: CURRENT_SIGNAL })
    .then(async (res) => {
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json().catch(() => ({}));
      CSRF_TOKEN = data?.csrf || data?.token || data?.value || null;
      debug("CSRF fetched:", !!CSRF_TOKEN);
      return CSRF_TOKEN;
    })
    .finally(() => { CSRF_PROMISE = null; });
  return CSRF_PROMISE;
}

// ────────────────────────────── In-flight dedupe ────────────────────────────
const inflight = new Map(); // key -> Promise
function dedupe(key, fn) {
  if (inflight.has(key)) return inflight.get(key);
  const p = (async () => fn())().finally(() => inflight.delete(key));
  inflight.set(key, p);
  return p;
}

// ───────────────────────────────── State ────────────────────────────────────
const STATE = {
  project: "LIMS-System",
  me: null,
  roles: [],
  roleUsage: {},
  users: [],
  pending: [],
  policies: { password_min_len: 8, mfa_required: false },
  catalog: { nouns: [], verb_groups: [], modules: { canonical: [], custom: [], all: [] }, signoff_gates: [], projects: [] },
  editingRole: null,
  projectCode: null,
};

// Abort handling for overlapping refreshes
let CURRENT_CONTROLLER = null;
let CURRENT_SIGNAL = undefined;
function newRefreshContext() {
  if (CURRENT_CONTROLLER) CURRENT_CONTROLLER.abort();
  CURRENT_CONTROLLER = new AbortController();
  CURRENT_SIGNAL = CURRENT_CONTROLLER.signal;
}

// Detect project from URL path if present
function detectProjectFromPath() {
  const m = location.pathname.match(/\/([A-Za-z0-9._-]+)\//);
  return (m && m[1]) || "LIMS-System";
}
function getProject() { return STATE.project; }
function setProject(p) {
  if (STATE.project !== p) {
    STATE.project = p;
    debug("Project set:", p);
    CSRF_TOKEN = null; // reset CSRF when project changes
    CSRF_PROMISE = null;
    refreshAll();
  }
}

// ─────────────────────────────── DOM handles ────────────────────────────────
const el = {
  projectSelect: document.getElementById("projectSelect"),
  refreshBtn: document.getElementById("refreshBtn"),

  // Pending & Users
  pendingList: document.getElementById("pendingList"),
  usersList: document.getElementById("usersList"),
  userFilter: document.getElementById("userFilter"),
  reloadUsersBtn: document.getElementById("reloadUsersBtn"),

  // Roles & creation/edit form
  rolesList: document.getElementById("rolesList"),
  roleUsage: document.getElementById("roleUsage"),
  roleName: document.getElementById("roleName"),
  roleDesc: document.getElementById("roleDesc"),
  roleScopes: document.getElementById("roleScopes"),
  roleTags: document.getElementById("roleTags"),
  createRoleBtn: document.getElementById("createRoleBtn"),
  exportRolesBtn: document.getElementById("exportRolesBtn"),

  // Audit
  auditLimit: document.getElementById("auditLimit"),
  reloadAuditBtn: document.getElementById("reloadAuditBtn"),
  auditTable: document.getElementById("auditTable"),

  // Policies & sessions
  polMinLen: document.getElementById("polMinLen"),
  polMfa: document.getElementById("polMfa"),
  savePoliciesBtn: document.getElementById("savePoliciesBtn"),
  revokeUserId: document.getElementById("revokeUserId"),
  revokeSessionsBtn: document.getElementById("revokeSessionsBtn"),

  // Password reset helpers (Policies tab)
  resetEmail: document.getElementById("resetEmail"),
  issueResetBtn: document.getElementById("issueResetBtn"),
  issuedTokenOut: document.getElementById("issuedTokenOut"),
  copyIssuedTokenBtn: document.getElementById("copyIssuedTokenBtn"),
  resetTokenField: document.getElementById("resetToken"),
  resetNewPassField: document.getElementById("resetNewPass"),
  performResetBtn: document.getElementById("performResetBtn"),

  // Project Code tab
  projectCodeOut: document.getElementById("projectCodeOut"),
  copyProjectCodeBtn: document.getElementById("copyProjectCodeBtn"),
  reloadProjectCodeBtn: document.getElementById("reloadProjectCodeBtn"),

  // Admin (injected)
  adminUserSelect: null,
  adminProjectSelect: null,
  adminRoleInput: null,
  adminAddBtn: null,
  adminRemoveBtn: null,
  adminReloadBtn: null,
  adminMembershipsList: null,

  // Debug
  debugOut: document.getElementById("debugOut"),
};

// ───────────────────────────── Tabs (bind/rebind safe) ─────────────────────
function bindTabs() {
  document.querySelectorAll(".tab").forEach((btn) => {
    btn.onclick = () => {
      document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      const sel = btn.getAttribute("data-target");
      const panel = document.querySelector(sel);
      if (panel) panel.classList.add("active");
    };
  });
}
bindTabs();

// ───────────────────────────── Event wiring (static) ────────────────────────
el.refreshBtn.addEventListener("click", refreshAll);
el.projectSelect.addEventListener("change", (e) => setProject(e.target.value));
el.reloadUsersBtn.addEventListener("click", loadUsers);

if (el.reloadProjectCodeBtn) el.reloadProjectCodeBtn.addEventListener("click", loadProjectCode);
if (el.copyProjectCodeBtn) {
  el.copyProjectCodeBtn.addEventListener("click", async () => {
    if (!el.projectCodeOut?.value) return;
    try { await navigator.clipboard.writeText(el.projectCodeOut.value); } catch {}
  });
}

el.createRoleBtn.addEventListener("click", () => { if (STATE.editingRole) onUpdateRole(); else onCreateRole(); });
el.exportRolesBtn.addEventListener("click", exportRoles);

el.reloadAuditBtn.addEventListener("click", loadAudit);
el.savePoliciesBtn.addEventListener("click", savePolicies);
el.revokeSessionsBtn.addEventListener("click", revokeSessions);

// Debounce filter to avoid re-render thrash
let filterTimer = null;
el.userFilter.addEventListener("input", () => {
  clearTimeout(filterTimer);
  filterTimer = setTimeout(() => renderUsers(STATE.users), 120);
});

// Policies tab — password reset helpers
if (document.getElementById("issueResetBtn")) {
  el.issueResetBtn.addEventListener("click", async () => {
    try {
      if (!assertAuthed()) return;
      const email = (el.resetEmail.value || "").trim();
      if (!email) return alert("Enter an email address.");
      const p = encodeURIComponent(getProject());
      await ensureCsrf();
      const data = await sendJSON(`/api/account_roles/${p}/users/reset/initiate`, "POST", { email });
      el.issuedTokenOut.value = data?.token || "";
      if (!el.issuedTokenOut.value) alert("No token returned.");
    } catch (e) { alert("Issue token failed: " + (e.message || e)); }
  });
}
if (document.getElementById("copyIssuedTokenBtn")) {
  el.copyIssuedTokenBtn.addEventListener("click", async () => {
    if (!el.issuedTokenOut?.value) return;
    await navigator.clipboard.writeText(el.issuedTokenOut.value).catch(() => {});
  });
}
if (document.getElementById("performResetBtn")) {
  el.performResetBtn.addEventListener("click", async () => {
    try {
      if (!assertAuthed()) return;
      const token = (el.resetTokenField.value || "").trim();
      const new_password = (el.resetNewPassField.value || "").trim();
      if (!token || !new_password) return alert("Token and new password are required.");
      const p = encodeURIComponent(getProject());
      await ensureCsrf();
      await sendJSON(`/api/account_roles/${p}/users/reset/perform`, "POST", { token, new_password });
      alert("Password reset ok.");
      el.resetTokenField.value = "";
      el.resetNewPassField.value = "";
    } catch (e) { alert("Reset failed: " + (e.message || e)); }
  });
}

// ────────────────────────────── Utilities ───────────────────────────────────
function setBusy(b) { document.body.classList.toggle("busy", !!b); }
function splitCSV(s) { return (s || "").split(",").map((t) => t.trim()).filter(Boolean); }
function escapeHtml(s) { if (s === null || s === undefined) return ""; return String(s).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;"); }
function insertAfter(newNode, referenceNode) { referenceNode.parentNode.insertBefore(newNode, referenceNode.nextSibling); }

async function getJSON(url, extraHeaders) {
  const res = await fetch(url, { headers: authHeaders(extraHeaders), credentials: "include", signal: CURRENT_SIGNAL });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}
async function sendJSON(url, method, body, extraHeaders) {
  if (method !== "GET") await ensureCsrf();
  const headers = authHeaders({ "Content-Type": "application/json", ...(extraHeaders || {}) });
  if (CSRF_TOKEN) headers["X-CSRF-Token"] = CSRF_TOKEN;
  const res = await fetch(url, { method, headers, body: JSON.stringify(body), credentials: "include", signal: CURRENT_SIGNAL });
  if (!res.ok) throw new Error(await res.text());
  return res.json().catch(() => ({}));
}

// ────────────────────────────── Loaders (deduped) ───────────────────────────
const Loaders = {
  async projects() {
    return dedupe("projects", async () => {
      const data = await getJSON(`/api/account_roles/projects`);
      const opts = data.projects || [];
      const select = el.projectSelect;
      const prev = Array.from(select.options).map(o => o.value).join("|");
      const next = opts.join("|");
      if (prev !== next) {
        select.innerHTML = opts.map((p) => `<option value="${p}">${p}</option>`).join("");
      }
      const detected = detectProjectFromPath();
      if (opts.includes(detected)) STATE.project = detected;
      select.value = STATE.project;
      debug("Projects loaded:", opts);
    });
  },
  async me() {
    const key = `me:${getProject()}`;
    return dedupe(key, async () => {
      const p = encodeURIComponent(getProject());
      const tryFetch = async (path) => {
        try { const res = await fetch(path, { headers: authHeaders(), credentials: "include", signal: CURRENT_SIGNAL }); if (res.ok) return await res.json(); } catch {}
        return null;
      };
      let me = await tryFetch(`/api/account_roles/${p}/me`);
      if (!me) me = await tryFetch(`/api/account_roles/me`);
      if (!me && window.GIMS?.me) me = window.GIMS.me;
      STATE.me = me || null;
      debug("Me:", STATE.me);
      return STATE.me;
    });
  },
  async catalog() {
    const key = `catalog:${getProject()}`;
    return dedupe(key, async () => {
      const p = encodeURIComponent(getProject());
      const data = await getJSON(`/api/account_roles/${p}/catalog`);
      STATE.catalog = {
        nouns: data.nouns || [],
        verb_groups: data.verb_groups || [],
        signoff_gates: data.signoff_gates || [],
        modules: data.modules || { canonical: [], custom: [], all: [] },
        projects: data.projects || [],
      };
      renderRolePermissionCheckboxes();
    });
  },
  async roles() {
    const key = `roles:${getProject()}`;
    return dedupe(key, async () => {
      const p = encodeURIComponent(getProject());
      const data = await getJSON(`/api/account_roles/${p}/roles`);
      STATE.roles = data.roles || [];
      if (STATE.users && STATE.users.length) renderUsers(STATE.users);
      renderRoles();
    });
  },
  async roleUsage() {
    const key = `roleUsage:${getProject()}`;
    return dedupe(key, async () => {
      const p = encodeURIComponent(getProject());
      const data = await getJSON(`/api/account_roles/${p}/roles/usage`);
      STATE.roleUsage = data.usage || {};
      renderRoles();
    });
  },
  async pending() {
    const key = `pending:${getProject()}`;
    return dedupe(key, async () => {
      const p = encodeURIComponent(getProject());
      const data = await getJSON(`/api/account_roles/${p}/users/pending`);
      STATE.pending = data.pending || [];
      renderPending(STATE.pending);
    });
  },
  async users() {
    const key = `users:${getProject()}`;
    return dedupe(key, async () => {
      const p = encodeURIComponent(getProject());
      const data = await getJSON(`/api/account_roles/${p}/users`);
      STATE.users = data.users || [];
      renderUsers(STATE.users);
    });
  },
  async audit(limit = 200) {
    const key = `audit:${getProject()}:${Number(limit)||200}`;
    return dedupe(key, async () => {
      const p = encodeURIComponent(getProject());
      const data = await getJSON(`/api/account_roles/${p}/audit?limit=${Number(limit) || 200}`);
      renderAudit(data.audit || []);
    });
  },
  async policies() {
    const key = `policies:${getProject()}`;
    return dedupe(key, async () => {
      const p = encodeURIComponent(getProject());
      const res = await fetch(`/api/account_roles/${p}/policies`, { headers: authHeaders(), credentials: "include", signal: CURRENT_SIGNAL });
      if (!res.ok) return;
      const data = await res.json();
      STATE.policies = data;
      if (el.polMinLen) el.polMinLen.value = data.password_min_len ?? 8;
      if (el.polMfa) el.polMfa.value = String(Boolean(data.mfa_required));
    });
  },
  async projectCode() {
    const key = `projectCode:${getProject()}`;
    return dedupe(key, async () => {
      const p = encodeURIComponent(getProject());
      const data = await getJSON(`/api/account_roles/${p}/project_code`);
      STATE.projectCode = data?.project_code || null;
      renderProjectCode();
    });
  },
};

// ───────────────────────────── Rendering: Pending ───────────────────────────
function renderPending(items) {
  debug("Render pending:", items?.length);
  el.pendingList.innerHTML = "";
  if (!items?.length) { el.pendingList.innerHTML = `<div class="muted">No pending accounts.</div>`; return; }
  const roleNames = STATE.roles.map((r) => r.name);
  for (const u of items) {
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `
      <div class="row">
        <div class="grow">
          <div class="title">${u.email}</div>
          <div class="muted">id: ${u.id}</div>
          <div class="muted">active: ${u.is_active} | verified: ${u.is_verified}</div>
        </div>
        <div class="assign">
          <label>Assign roles</label>
          <select class="role-select" multiple size="4">
            ${roleNames.map((rn) => `<option value="${rn}">${rn}</option>`).join("")}
          </select>
        </div>
      </div>
      <div class="actions">
        <button class="primary approve">Approve</button>
        <button class="danger reject">Reject</button>
      </div>
    `;
    card.querySelector(".approve").addEventListener("click", async () => {
      const sel = Array.from(card.querySelector(".role-select").selectedOptions).map((o) => o.value);
      await approveUser(u.id, true, sel);
    });
    card.querySelector(".reject").addEventListener("click", async () => { await approveUser(u.id, false, []); });
    el.pendingList.appendChild(card);
  }
}

// ───────────────────────────── Rendering: Users ─────────────────────────────
function filteredUsers() {
  const f = (el.userFilter.value || "").toLowerCase();
  if (!f) return STATE.users;
  return STATE.users.filter((u) => u.email.toLowerCase().includes(f));
}
function renderUsers() {
  const list = filteredUsers();
  debug("Render users:", list.length);
  el.usersList.innerHTML = "";
  if (!list.length) { el.usersList.innerHTML = `<div class="muted">No users found.</div>`; return; }
  const rolesIndex = new Set(STATE.roles.map((r) => r.name));

  for (const u of list) {
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `
      <div class="row">
        <div class="grow">
          <div class="title">${u.email}</div>
          <div class="muted">id: ${u.id} | active: ${u.is_active} | superuser: ${u.is_superuser} | verified: ${u.is_verified}</div>
          <div class="muted">roles: ${u.roles.join(", ") || "(none)"} </div>
          <div class="muted">overrides: ${u.overrides.map(o => o.scope || o.feature_tag).filter(Boolean).join(", ") || "(none)"}</div>
        </div>
        <div class="col">
          <label>Roles</label>
          <select class="role-select" multiple size="4">
            ${Array.from(rolesIndex).map((rn) => `<option value="${rn}" ${u.roles.includes(rn) ? "selected" : ""}>${rn}</option>`).join("")}
          </select>
        </div>
      </div>

      <details class="collapsible">
        <summary>Edit overrides</summary>
        <div class="form-grid">
          <label>Add scopes (comma)</label>
          <input class="add-scopes" placeholder="proj:resource:action" />
          <label>Remove scopes (comma)</label>
          <input class="rem-scopes" />
          <label>Add feature tags (comma)</label>
          <input class="add-tags" placeholder="noun:Sample, verb:Tests, module:archive-workbench" />
          <label>Remove feature tags (comma)</label>
          <input class="rem-tags" />
          <div class="muted small" style="grid-column: 1 / -1; margin-top: .25rem;">
            Tip: you can use tags like <code>noun:Batch</code>, <code>verb:Tests</code>, <code>module:archive-workbench</code>
          </div>
        </div>
      </details>

      <details class="collapsible">
        <summary>Password Reset (Beta)</summary>
        <div class="form-grid">
          <label>Issue reset token (copy to user)</label>
          <div class="row">
            <button class="ghost issue-reset">Issue Token</button>
            <button class="ghost copy-token">Copy</button>
          </div>
          <input class="reset-token" placeholder="token appears here" readonly />

          <label>Perform reset (paste token + new password)</label>
          <input type="password" class="reset-newpass" placeholder="new password (min 8 chars)" />
          <button class="primary perform-reset">Perform Reset</button>
        </div>
      </details>

      <div class="actions">
        <button class="primary save-roles">Save Roles</button>
        <button class="ghost save-overrides">Save Overrides</button>
        <span class="spacer"></span>
        <button class="ghost toggle-active">${u.is_active ? "Deactivate" : "Activate"}</button>
        <button class="danger soft-delete">Soft Delete</button>
        <button class="danger hard-delete">Purge</button>
      </div>
    `;

    // Roles
    card.querySelector(".save-roles").addEventListener("click", async () => {
      const selected = Array.from(card.querySelector(".role-select").selectedOptions).map((o) => o.value);
      await saveRoles(u.id, selected);
    });

    // Overrides
    card.querySelector(".save-overrides").addEventListener("click", async () => {
      const addScopes = splitCSV(card.querySelector(".add-scopes").value);
      const remScopes = splitCSV(card.querySelector(".rem-scopes").value);
      const addTags = splitCSV(card.querySelector(".add-tags").value);
      const remTags = splitCSV(card.querySelector(".rem-tags").value);
      await saveOverrides(u.id, {
        add_scopes: addScopes,
        remove_scopes: remScopes,
        add_feature_tags: addTags,
        remove_feature_tags: remTags
      });
      card.querySelector(".add-scopes").value = "";
      card.querySelector(".rem-scopes").value = "";
      card.querySelector(".add-tags").value = "";
      card.querySelector(".rem-tags").value = "";
    });

    // Activate / delete
    card.querySelector(".toggle-active").addEventListener("click", async () => { await setUserStatus(u.id, !u.is_active); });
    card.querySelector(".soft-delete").addEventListener("click", async () => { if (!confirm("Soft delete this user (deactivate + clear roles/overrides)?")) return; await deleteUser(u.id, false); });
    card.querySelector(".hard-delete").addEventListener("click", async () => { if (!confirm("Permanently purge this user from this project? This cannot be undone.")) return; await deleteUser(u.id, true); });

    // Password reset (per-user)
    const resetTokenInput = card.querySelector(".reset-token");
    const newPassInput = card.querySelector(".reset-newpass");

    card.querySelector(".issue-reset").addEventListener("click", async () => {
      try {
        if (!assertAuthed()) return;
        const p = encodeURIComponent(getProject());
        await ensureCsrf();
        const data = await sendJSON(`/api/account_roles/${p}/users/reset/initiate`, "POST", { email: u.email });
        resetTokenInput.value = data?.token || "";
        if (!resetTokenInput.value) alert("No token returned.");
      } catch (e) { alert("Issue token failed: " + (e.message || e)); }
    });

    card.querySelector(".copy-token").addEventListener("click", async () => {
      if (!resetTokenInput.value) return;
      await navigator.clipboard.writeText(resetTokenInput.value).catch(() => {});
    });

    card.querySelector(".perform-reset").addEventListener("click", async () => {
      try {
        if (!assertAuthed()) return;
        const token = (resetTokenInput.value || "").trim();
        const new_password = (newPassInput.value || "").trim();
        if (!token || !new_password) return alert("Token and new password are required.");
        const p = encodeURIComponent(getProject());
        await ensureCsrf();
        await sendJSON(`/api/account_roles/${p}/users/reset/perform`, "POST", { token, new_password });
        alert("Password reset ok.");
        newPassInput.value = "";
      } catch (e) { alert("Reset failed: " + (e.message || e)); }
    });

    el.usersList.appendChild(card);
  }
}

// ───────────────────────────── Rendering: Roles ─────────────────────────────
function renderRoles() {
  debug("Render roles:", STATE.roles.length);
  el.rolesList.innerHTML = "";
  if (!STATE.roles.length) { el.rolesList.innerHTML = `<div class="muted">No roles defined.</div>`; return; }
  for (const r of STATE.roles) {
    const item = document.createElement("div");
    item.className = "role-item";
    item.innerHTML = `
      <div class="grow">
        <div class="title">${r.name}</div>
        <div class="muted">${r.description || "(no description)"}</div>
        <details class="tags-accordion">
          <summary>tags (${r.feature_tags.length})</summary>
          <div class="code small">
            ${r.feature_tags.map(t => `<div>${escapeHtml(t)}</div>`).join("") || "(none)"}
          </div>
        </details>
      </div>
      <div class="actions">
        <button class="ghost edit">Edit</button>
        <button class="danger del">Delete</button>
      </div>
    `;
    item.querySelector(".del").addEventListener("click", async () => {
      if (!confirm(`Delete role "${r.name}"?`)) return;
      await deleteRole(r.name);
    });
    item.querySelector(".edit").addEventListener("click", async () => { enterEditMode(r); });
    el.rolesList.appendChild(item);
  }
  el.roleUsage.textContent = JSON.stringify(STATE.roleUsage, null, 2);
}

// ─────────────────────── Edit Mode helpers (roles) ──────────────────────────
function ensureCancelEditBtn() {
  let btn = document.getElementById("cancelEditBtn");
  if (!btn) {
    btn = document.createElement("button");
    btn.id = "cancelEditBtn";
    btn.textContent = "Cancel Edit";
    btn.className = "ghost";
    btn.style.marginLeft = "8px";
    btn.addEventListener("click", exitEditMode);
    el.createRoleBtn.insertAdjacentElement("afterend", btn);
  }
  return btn;
}
function clearAllPermissionCheckboxes() { document.querySelectorAll("#rolePerms input.perm-box").forEach(cb => cb.checked = false); }
function setCheckboxesFromTags(tags) {
  clearAllPermissionCheckboxes();
  const tagSet = new Set(tags || []);
  document.querySelectorAll("#rolePerms input.perm-box.perm-noun").forEach(cb => { if (tagSet.has(`noun:${cb.dataset.val}`)) cb.checked = true; });
  document.querySelectorAll("#rolePerms input.perm-box.perm-verb").forEach(cb => { if (tagSet.has(`verb:${cb.dataset.val}`)) cb.checked = true; });
  document.querySelectorAll("#rolePerms input.perm-box.perm-mod-can").forEach(cb => { if (tagSet.has(`module:${cb.dataset.val}`)) cb.checked = true; });
  document.querySelectorAll("#rolePerms input.perm-box.perm-mod-cus").forEach(cb => { if (tagSet.has(`module:${cb.dataset.val}`)) cb.checked = true; });
  document.querySelectorAll("#rolePerms input.perm-box.perm-signoff").forEach(cb => { if (tagSet.has(`signoff:${cb.dataset.val}`)) cb.checked = true; });
}
function collectRolePermissionTags() {
  const out = [];
  document.querySelectorAll('#rolePerms input.perm-box.perm-noun:checked').forEach(cb => out.push(`noun:${cb.dataset.val}`));
  document.querySelectorAll('#rolePerms input.perm-box.perm-verb:checked').forEach(cb => out.push(`verb:${cb.dataset.val}`));
  document.querySelectorAll('#rolePerms input.perm-box.perm-mod-can:checked, #rolePerms input.perm-box.perm-mod-cus:checked').forEach(cb => out.push(`module:${cb.dataset.val}`));
  document.querySelectorAll('#rolePerms input.perm-box.perm-signoff:checked').forEach(cb => out.push(`signoff:${cb.dataset.val}`));
  return out;
}
function setProjectCheckboxes(projects) {
  const set = new Set(projects || []);
  document.querySelectorAll("#rolePerms input.perm-box.perm-proj").forEach(cb => { cb.checked = set.has(cb.dataset.val); });
}
function unmatchedTagsForTextField(tags) {
  const nouns = new Set(STATE.catalog.nouns || []);
  const verbs = new Set(STATE.catalog.verb_groups || []);
  const mods  = new Set((STATE.catalog.modules?.all) || [...(STATE.catalog.modules?.canonical || []), ...(STATE.catalog.modules?.custom || [])]);
  const sign  = new Set(STATE.catalog.signoff_gates || []);
  const leftover = [];
  for (const t of (tags || [])) {
    if (t.startsWith("noun:")) { if (!nouns.has(t.slice(5))) leftover.push(t);
    } else if (t.startsWith("verb:")) { if (!verbs.has(t.slice(5))) leftover.push(t);
    } else if (t.startsWith("module:")) { if (!mods.has(t.slice(7))) leftover.push(t);
    } else if (t.startsWith("signoff:")) { if (!sign.has(t.slice(8))) leftover.push(t);
    } else { leftover.push(t); }
  }
  return leftover;
}
function enterEditMode(role) {
  STATE.editingRole = role.name;
  debug("Enter edit mode:", role);
  if (el.roleName) { el.roleName.value = role.name; el.roleName.disabled = true; }
  if (el.roleDesc) el.roleDesc.value = role.description || "";
  if (el.roleScopes) el.roleScopes.value = (role.scopes || []).join(", ");
  setCheckboxesFromTags(role.feature_tags || []);
  if (el.roleTags) el.roleTags.value = unmatchedTagsForTextField(role.feature_tags || []).join(", ");
  setProjectCheckboxes(role.projects || []);
  el.createRoleBtn.textContent = "Save Changes";
  const cancelBtn = ensureCancelEditBtn();
  cancelBtn.style.display = "inline-block";
}
function exitEditMode() {
  debug("Exit edit mode");
  STATE.editingRole = null;
  if (el.roleName) { el.roleName.value = ""; el.roleName.disabled = false; }
  if (el.roleDesc) el.roleDesc.value = "";
  if (el.roleScopes) el.roleScopes.value = "";
  if (el.roleTags) el.roleTags.value = "";
  clearAllPermissionCheckboxes();
  el.createRoleBtn.textContent = "Create Role";
  const cancelBtn = document.getElementById("cancelEditBtn");
  if (cancelBtn) cancelBtn.style.display = "none";
}

// ───────────────────────────── Rendering: Audit ─────────────────────────────
function renderAudit(rows) {
  debug("Render audit:", rows.length);
  const headers = ["ts", "user_id", "action", "resource", "resource_id", "ip", "path", "method"];
  let html = `<table><thead><tr>${headers.map(h => `<th>${h}</th>`).join("")}</tr></thead><tbody>`;
  for (const a of rows) { html += `<tr>${headers.map(h => `<td>${escapeHtml(a[h])}</td>`).join("")}</tr>`; }
  html += `</tbody></table>`;
  el.auditTable.innerHTML = html;
}

// ───────────────────────── Rendering: Project Code ──────────────────────────
function renderProjectCode() {
  const code = STATE.projectCode ?? "";
  if (el.projectCodeOut) el.projectCodeOut.value = code || "";
}

// ─────────────── Role Creation: Checkbox-based permissions UI ───────────────
function buildCheckboxSection(title, items, namePrefix, emptyText = "(none)") {
  const wrap = document.createElement("div");
  wrap.className = "perm-section";
  wrap.innerHTML = `
    <div class="perm-header">
      <strong>${title}</strong>
      <span class="perm-actions">
        <button class="mini check-all" type="button">All</button>
        <button class="mini uncheck-all" type="button">None</button>
      </span>
    </div>
    <div class="perm-grid"></div>
  `;
  const grid = wrap.querySelector(".perm-grid");
  if (!items?.length) {
    grid.innerHTML = `<div class="muted">${emptyText}</div>`;
  } else {
    const frag = document.createDocumentFragment();
    for (const key of items) {
      const id = `${namePrefix}__${key}`.replace(/\s+/g, "_");
      const div = document.createElement("label");
      div.className = "perm-item";
      div.innerHTML = `
        <input type="checkbox" class="perm-box ${namePrefix}" data-val="${key}" id="${id}" />
        <span>${key}</span>
      `;
      frag.appendChild(div);
    }
    grid.appendChild(frag);
  }
  wrap.querySelector(".check-all").addEventListener("click", () => { grid.querySelectorAll(`input.perm-box.${namePrefix}`).forEach(cb => cb.checked = true); });
  wrap.querySelector(".uncheck-all").addEventListener("click", () => { grid.querySelectorAll(`input.perm-box.${namePrefix}`).forEach(cb => cb.checked = false); });
  return wrap;
}
function ensureRolePermContainer() {
  let container = document.getElementById("rolePerms");
  if (container) return container;
  container = document.createElement("div");
  container.id = "rolePerms";
  container.className = "card role-perms";
  const anchor = el.roleTags || el.createRoleBtn || el.rolesList;
  const title = document.createElement("div");
  title.className = "title";
  title.textContent = "Role Permissions (checkboxes)";
  container.appendChild(title);
  insertAfter(container, anchor);
  const css = `
    .role-perms { margin-top: .75rem; padding: .75rem; }
    .perm-section { margin-top: .5rem; }
    .perm-header { display:flex; align-items:center; justify-content:space-between; margin-bottom: .25rem; border:1px solid rgba(155,155,200,.35); border-radius:6px; padding:6px 10px; background: rgba(255,255,255,.04); }
    .perm-actions .mini { font-size: 11px; padding: 2px 6px; margin-left: 6px; background: rgba(128,90,213,.15); border:1px solid rgba(128,90,213,.4); border-radius:4px; color:#cdb5ff; font-weight:500; }
    .perm-actions .mini:hover { background: rgba(128,90,213,.3); border-color: rgba(128,90,213,.7); }
    .perm-grid { display:grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: .35rem .75rem; }
    .perm-item { display:flex; align-items:center; gap:.45rem; font-size: 13px; }
    .perm-item input { transform: translateY(1px); }
    .tags-accordion summary{ cursor:pointer; font-weight:500; }
    .tags-accordion[open] summary{ color:var(--accent, #4b7cff); }
    #cancelEditBtn{ display:none; }
  `;
  const s = document.createElement("style"); s.textContent = css; document.head.appendChild(s);
  return container;
}
function renderRolePermissionCheckboxes() {
  const c = ensureRolePermContainer();
  c.querySelectorAll(".perm-section, .muted").forEach(n => n.remove());
  const nouns = STATE.catalog.nouns || [];
  const verbs = STATE.catalog.verb_groups || [];
  const canon = STATE.catalog.modules?.canonical || [];
  const custom = STATE.catalog.modules?.custom || [];
  const gates = STATE.catalog.signoff_gates || [];

  const nounSec = buildCheckboxSection("Noun Types", nouns, "perm-noun");
  const verbSec = buildCheckboxSection("Verb Groups", verbs, "perm-verb");
  const canSec  = buildCheckboxSection("Canonical Modules", canon, "perm-mod-can", "(none)");
  const cusSec  = buildCheckboxSection("Custom Modules", custom, "perm-mod-cus", "(no custom modules)");
  const signSec = buildCheckboxSection("Sign-off Gates", gates, "perm-signoff", "(no gates discovered)");

  c.appendChild(nounSec); c.appendChild(verbSec); c.appendChild(canSec); c.appendChild(cusSec); c.appendChild(signSec);

  if (STATE.editingRole) {
    const r = STATE.roles.find(x => x.name === STATE.editingRole);
    if (r) { setCheckboxesFromTags(r.feature_tags || []); setProjectCheckboxes(r.projects || []); }
  }
}

// ───────────────────────────── CRUD: Roles/Users/etc ────────────────────────
async function exportRoles() {
  if (!assertAuthed()) return;
  const res = await fetch(`/api/account_roles/${encodeURIComponent(getProject())}/roles/export`, { headers: authHeaders(), credentials: "include", signal: CURRENT_SIGNAL });
  const text = await res.text();
  const blob = new Blob([text], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a"); a.href = url; a.download = `roles_${getProject()}.json`; a.click();
  URL.revokeObjectURL(url);
}
async function updateRole(name, body) {
  const p = encodeURIComponent(getProject());
  await sendJSON(`/api/account_roles/${p}/roles/${encodeURIComponent(name)}`, "PATCH", body);
  await Loaders.roles();
}
async function deleteRole(name) {
  const p = encodeURIComponent(getProject());
  const res = await fetch(`/api/account_roles/${p}/roles/${encodeURIComponent(name)}`, { method: "DELETE", headers: authHeaders(), credentials: "include", signal: CURRENT_SIGNAL });
  if (!res.ok) return alert(await res.text());
  await Promise.all([Loaders.roles(), Loaders.users()]);
}
async function approveUser(userId, approve, assignRoles) {
  const p = encodeURIComponent(getProject());
  await sendJSON(`/api/account_roles/${p}/users/${encodeURIComponent(userId)}/approve`, "POST", { approve, assign_roles: assignRoles || [] });
  await Promise.all([Loaders.pending(), Loaders.users(), Loaders.me()]);
}
async function saveRoles(userId, roleNames) {
  const p = encodeURIComponent(getProject());
  await sendJSON(`/api/account_roles/${p}/users/${encodeURIComponent(userId)}/roles`, "POST", { role_names: roleNames || [] });
  await Loaders.users();
}
async function saveOverrides(userId, body) {
  const p = encodeURIComponent(getProject());
  await sendJSON(`/api/account_roles/${p}/users/${encodeURIComponent(userId)}/overrides`, "POST", body);
  await Loaders.users();
}
async function setUserStatus(userId, isActive) {
  const p = encodeURIComponent(getProject());
  await sendJSON(`/api/account_roles/${p}/users/${encodeURIComponent(userId)}/status`, "POST", { is_active: !!isActive });
  await Loaders.users();
}
async function deleteUser(userId, hard) {
  const p = encodeURIComponent(getProject());
  const res = await fetch(`/api/account_roles/${p}/users/${encodeURIComponent(userId)}?hard=${hard ? "true" : "false"}`, { method: "DELETE", headers: authHeaders(), credentials: "include", signal: CURRENT_SIGNAL });
  if (!res.ok) return alert(await res.text());
  await Loaders.users();
}
async function loadUsers() { await Loaders.users(); }
async function loadAudit() { await Loaders.audit(Number(el.auditLimit.value || 200)); }
async function loadProjectCode() { await Loaders.projectCode(); }
async function savePolicies() {
  const p = encodeURIComponent(getProject());
  const body = { password_min_len: Number(el.polMinLen.value || 8), mfa_required: el.polMfa.value === "true" };
  await sendJSON(`/api/account_roles/${p}/policies`, "POST", body);
  alert("Policies saved.");
}
async function revokeSessions() {
  const userId = (el.revokeUserId.value || "").trim();
  if (!userId) return alert("Enter a user ID.");
  const p = encodeURIComponent(getProject());
  const res = await fetch(`/api/account_roles/${p}/users/${encodeURIComponent(userId)}/sessions/revoke`, { method: "POST", headers: authHeaders(), credentials: "include", signal: CURRENT_SIGNAL });
  const data = await res.json();
  alert(data?.note || "Done.");
}

// ───────────────────── Admin (memberships) API helpers ─────────────────────
async function membershipsList(userId) {
  const key = `memberships:${userId}`;
  return dedupe(key, async () => {
    const data = await getJSON(`/api/account_roles/memberships/${encodeURIComponent(userId)}`);
    return data || { user_id: userId, memberships: [] };
  });
}
async function membershipsUpdate({ user_id, project, op, role_name }) {
  await sendJSON(`/api/account_roles/memberships/update`, "POST", { user_id, project, op, role_name: role_name || null });
}

// ───────── Admin (memberships) UI helpers + dynamic element references ─────
function refreshAdminRefs() {
  el.adminUserSelect = document.getElementById("adminUserSelect");
  el.adminProjectSelect = document.getElementById("adminProjectSelect");
  el.adminRoleInput = document.getElementById("adminRoleInput");
  el.adminAddBtn = document.getElementById("adminAddBtn");
  el.adminRemoveBtn = document.getElementById("adminRemoveBtn");
  el.adminReloadBtn = document.getElementById("adminReloadBtn");
  el.adminMembershipsList = document.getElementById("adminMembershipsList");
}
function renderAdminMemberships(userId, memberships) {
  const list = el.adminMembershipsList;
  list.innerHTML = "";
  if (!memberships?.length) { list.innerHTML = `<div class="muted">(no memberships)</div>`; return; }
  for (const m of memberships) {
    const row = document.createElement("div");
    row.className = "role-item";
    row.innerHTML = `
      <div class="grow">
        <div class="title">${escapeHtml(m.project)}</div>
        <div class="muted">role_name: ${escapeHtml(m.role_name ?? "(none)")}</div>
      </div>
      <div class="actions">
        <button class="danger mini demote">Remove</button>
      </div>
    `;
    row.querySelector(".demote").addEventListener("click", async () => {
      const uid = el.adminUserSelect.value;
      if (!uid) return;
      if (!confirm(`Remove ${uid} from project "${m.project}"?`)) return;
      await membershipsUpdate({ user_id: uid, project: m.project, op: "remove" });
      await refreshAdminMemberships();
    });
    list.appendChild(row);
  }
}
function populateAdminUserSelect() {
  const sel = el.adminUserSelect;
  const cur = sel.value;
  const users = (STATE.users || []).slice().sort((a,b) => a.email.localeCompare(b.email));
  sel.innerHTML = users.map(u => `<option value="${u.id}">${escapeHtml(u.email)} — ${u.id}</option>`).join("");
  if (cur && users.some(u => u.id === cur)) sel.value = cur;
  else if (users.length) sel.value = users[0].id;
}
function populateAdminProjectSelect() {
  const sel = el.adminProjectSelect;
  const cur = sel.value;
  const projs = STATE.catalog?.projects || [];
  sel.innerHTML = projs.map(p => `<option value="${p}">${p}</option>`).join("");
  sel.value = projs.includes(getProject()) ? getProject() : (cur || projs[0] || "");
}
async function refreshAdminMemberships() {
  const uid = el.adminUserSelect?.value;
  if (!uid) { el.adminMembershipsList.innerHTML = `<div class="muted">Select a user.</div>`; return; }
  try { const data = await membershipsList(uid); renderAdminMemberships(uid, data.memberships || []); }
  catch (e) { el.adminMembershipsList.innerHTML = `<div class="muted">Failed to load memberships: ${escapeHtml(e.message || e)}</div>`; }
}

// Inject Admin tab & panel for superusers only
function maybeInsertAdminTab() {
  if (!(STATE.me?.is_superuser || STATE.me?.user?.is_superuser)) return;
  if (document.getElementById("tab-admin")) return;

  const debugTabBtn = document.querySelector('nav.tabs button[data-target="#tab-debug"]');

  const adminBtn = document.createElement("button");
  adminBtn.className = "tab";
  adminBtn.dataset.target = "#tab-admin";
  adminBtn.textContent = "Admin";
  debugTabBtn.insertAdjacentElement("beforebegin", adminBtn);

  const adminPanel = document.createElement("section");
  adminPanel.id = "tab-admin";
  adminPanel.className = "panel";
  adminPanel.innerHTML = `
    <div class="panel-head">
      <h2>Admin — Project Memberships</h2>
      <p>Promote or demote users from projects (superusers only).</p>
    </div>

    <div class="card">
      <div class="form-grid">
        <label for="adminUserSelect">User</label>
        <select id="adminUserSelect"><option value="">(loading users…)</option></select>

        <label for="adminProjectSelect">Project</label>
        <select id="adminProjectSelect"><option value="">(loading projects…)</option></select>

        <label for="adminRoleInput">Role name (optional)</label>
        <input id="adminRoleInput" placeholder="e.g. analyst (stored on link)" />
      </div>

      <div class="actions">
        <button id="adminAddBtn" class="primary">Add Membership</button>
        <button id="adminRemoveBtn" class="danger">Remove Membership</button>
        <button id="adminReloadBtn" class="ghost">Reload Memberships</button>
      </div>
    </div>

    <div class="card">
      <h3>Current Memberships</h3>
      <div id="adminMembershipsList" class="list"></div>
    </div>
  `;
  document.querySelector("main").appendChild(adminPanel);

  bindTabs();
  refreshAdminRefs();
  if (el.adminUserSelect) el.adminUserSelect.addEventListener("change", refreshAdminMemberships);
  if (el.adminReloadBtn) el.adminReloadBtn.addEventListener("click", refreshAdminMemberships);
  if (el.adminAddBtn) {
    el.adminAddBtn.addEventListener("click", async () => {
      try {
        if (!assertAuthed()) return;
        const user_id = el.adminUserSelect.value;
        const project = el.adminProjectSelect.value;
        const role_name = (el.adminRoleInput.value || "").trim() || null;
        if (!user_id || !project) return alert("Pick a user and a project.");
        await membershipsUpdate({ user_id, project, op: "add", role_name });
        await refreshAdminMemberships();
      } catch (e) { alert("Add membership failed: " + (e.message || e)); }
    });
  }
  if (el.adminRemoveBtn) {
    el.adminRemoveBtn.addEventListener("click", async () => {
      try {
        if (!assertAuthed()) return;
        const user_id = el.adminUserSelect.value;
        const project = el.adminProjectSelect.value;
        if (!user_id || !project) return alert("Pick a user and a project.");
        if (!confirm(`Remove user from project "${project}"?`)) return;
        await membershipsUpdate({ user_id, project, op: "remove" });
        await refreshAdminMemberships();
      } catch (e) { alert("Remove membership failed: " + (e.message || e)); }
    });
  }
  populateAdminUserSelect();
  populateAdminProjectSelect();
  refreshAdminMemberships();
}

// ───────────────────────────── Create / Update Role (handlers) ──────────────
async function onCreateRole() {
  if (!assertAuthed()) return;
  const name = (el.roleName.value || "").trim();
  if (!name) return alert("Role name required");
  const description = el.roleDesc.value || null;
  const typedScopes = splitCSV(el.roleScopes?.value || "");
  const typedTags   = splitCSV(el.roleTags?.value || "");
  const pickedTags  = collectRolePermissionTags();
  const feature_tags = Array.from(new Set([...typedTags, ...pickedTags]));
  const scopes = typedScopes;

  debug("Creating role:", { name, description, scopes, feature_tags });

  try {
    const p = encodeURIComponent(getProject());
    await sendJSON(`/api/account_roles/${p}/roles`, "POST", { name, description, scopes, feature_tags });
    exitEditMode();
    await Loaders.roles();
  } catch (e) {
    alert("Create role failed: " + (e.message || e));
  }
}
async function onUpdateRole() {
  if (!assertAuthed()) return;
  const name = STATE.editingRole;
  if (!name) return;
  const description = el.roleDesc.value || null;
  const typedScopes = splitCSV(el.roleScopes?.value || "");
  const typedTags   = splitCSV(el.roleTags?.value || "");
  const pickedTags  = collectRolePermissionTags();
  const feature_tags = Array.from(new Set([...typedTags, ...pickedTags]));
  const scopes = typedScopes;

  debug("Updating role:", { name, description, scopes, feature_tags });

  try {
    await updateRole(name, { description, scopes, feature_tags });
    exitEditMode();
  } catch (e) {
    alert("Update role failed: " + (e.message || e));
  }
}

// ───────────────────────────── Page startup / Refresh ───────────────────────
async function refreshAll() {
  if (!assertAuthed()) return;
  newRefreshContext(); // abort prior refresh; set CURRENT_SIGNAL
  debug("Refresh all begin");
  setBusy(true);
  try {
    // 1) Light, ordered deps
    await Loaders.projects();
    await Loaders.me();
    await Loaders.catalog();

    // 2) Ensure roles before user/pending render
    await Loaders.roles();

    // 3) Parallel bulk (no duplicate roles here)
    await Promise.all([
      Loaders.pending(),
      Loaders.users(),
      Loaders.audit(Number(el.auditLimit.value || 200)),
      Loaders.policies(),
      Loaders.projectCode(),
    ]);

    // 4) Usage stats (UI nicety)
    await Loaders.roleUsage();

    // 5) Inject Admin tab ONLY for superusers
    maybeInsertAdminTab();
  } catch (e) {
    if (e?.name === "AbortError") { debug("Refresh aborted"); }
    else { console.error(e); alert("Failed to refresh: " + (e.message || e)); }
  } finally {
    setBusy(false);
    debug("Refresh all end");
  }
}

(async function boot() {
  try {
    // Fast project detection before first refresh
    const detected = detectProjectFromPath();
    if (detected) STATE.project = detected;
    await Loaders.projects();
    bindTabs();
    await refreshAll();
  } catch (e) {
    console.error(e);
    alert("Failed to load admin console: " + (e.message || e));
  }
})();
