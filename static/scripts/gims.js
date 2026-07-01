// gims.js — shared front-end toolkit (Tier 0 foundation)
// One place for the primitives every page used to reinvent: fetchJSON, loadProjects,
// toast, escapeHtml, the four canonical state renderers, and small DOM/icon helpers.
// Loaded by the app-shell on every UI page (see core/orchestration/page_node.py).
//
// Auth/login UI lives in /login/inject.js; the global fetch wrapper in
// /orchestrate/inject.js. This file deliberately does NOT touch auth state — it only
// pre-creates window.GIMS + a deferred authReady so load order never matters.
(() => {
  "use strict";
  const GIMS = (window.GIMS = window.GIMS || {});
  if (GIMS.__toolkitInstalled) return;
  GIMS.__toolkitInstalled = true;

  // Pre-create the auth-ready promise so page scripts can await it regardless of
  // whether /login/inject.js has run yet (it adopts this same promise if present).
  if (!GIMS.authReady) {
    let _resolve;
    GIMS.authReady = new Promise((res) => { _resolve = res; });
    GIMS.__resolveAuthReady = _resolve;
  }

  // ── DOM helpers ──────────────────────────────────────────────────────────
  const qs = (sel, root = document) => root.querySelector(sel);
  const qsa = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  function el(tag, attrs, kids) {
    const n = document.createElement(tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (v == null) continue;
        if (k === "class") n.className = v;
        else if (k === "html") n.innerHTML = v;
        else if (k === "text") n.textContent = v;
        else if (k === "dataset") Object.assign(n.dataset, v);
        else if (k in n && k !== "list") { try { n[k] = v; } catch { n.setAttribute(k, v); } }
        else n.setAttribute(k, v);
      }
    }
    if (kids != null) {
      (Array.isArray(kids) ? kids : [kids]).forEach((c) => {
        if (c == null) return;
        n.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
      });
    }
    return n;
  }

  function escapeHtml(s) {
    if (s == null) return "";
    return String(s)
      .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;").replaceAll("'", "&#39;");
  }

  // ── Icons (mirror the .icon + sprite convention from watery.css) ──────────
  function iconSvg(name, cls) {
    return `<svg class="icon${cls ? " " + cls : ""}"><use href="/static/icons.svg#i-${name}"/></svg>`;
  }
  function iconEl(name, cls) {
    const wrap = el("span", { html: iconSvg(name, cls) });
    return wrap.firstElementChild;
  }

  // ── onAuthed: the canonical page-init hook. Run cb once the user is authenticated
  //    (immediately if already signed in, otherwise on the next sign-in). Pages use this
  //    to defer data loading until /login/inject.js has resolved auth + forwarded the JWT,
  //    so a deep-linked page doesn't fire 401s behind the login card. ──────────────────
  function onAuthed(cb, opts = {}) {
    const { once = true } = opts;
    const fire = () => { try { cb(); } catch (e) { console.error("[gims] onAuthed cb error", e); } };
    const st = GIMS.__authState;
    if (st && st.authed) { fire(); if (once) return; }
    const handler = (e) => {
      if (e.detail && e.detail.authed) { fire(); if (once) document.removeEventListener("gims:authapplied", handler); }
    };
    document.addEventListener("gims:authapplied", handler);
  }

  // ── Project detection (matches /login/inject.js so a project-in-path is consistent) ──
  function detectProject() {
    if (window.GIMS_PROJECT) return window.GIMS_PROJECT;
    const m = location.pathname.match(/\/([A-Za-z0-9._-]+)\//);
    return (m && m[1]) || "LIMS-System";
  }

  // ── fetchJSON: window.fetch is the orchestrated wrapper; this adds JSON +
  //    a single, predictable error contract (throws Error with a clean message
  //    and .status / .body attached). ──────────────────────────────────────
  function _messageFromBody(body, fallback) {
    if (body == null) return fallback;
    if (typeof body === "string") return body || fallback;
    const d = body.detail;
    if (typeof d === "string" && d) return d;
    if (d && typeof d === "object") return d.reason || d.message || d.error || fallback;
    return body.message || body.error || body.error_code || fallback;
  }

  async function fetchJSON(url, opts = {}) {
    const init = { ...opts };
    init.headers = new Headers(opts.headers || {});
    if (init.body != null && !(init.body instanceof FormData) && typeof init.body !== "string") {
      init.body = JSON.stringify(init.body);
      if (!init.headers.has("Content-Type")) init.headers.set("Content-Type", "application/json");
    }
    let res;
    try {
      res = await fetch(url, init);
    } catch (e) {
      const err = new Error("Network error — could not reach the server.");
      err.cause = e; err.status = 0;
      throw err;
    }
    const ct = res.headers.get("content-type") || "";
    let body = null;
    if (ct.includes("application/json")) { try { body = await res.json(); } catch { body = null; } }
    else { try { body = await res.text(); } catch { body = null; } }
    if (!res.ok) {
      const err = new Error(_messageFromBody(body, `Request failed (${res.status})`));
      err.status = res.status; err.body = body;
      throw err;
    }
    return body;
  }

  // ── loadProjects: GET /projects → <select>; default-first; optional onChange ──
  async function loadProjects(select, opts = {}) {
    if (typeof select === "string") select = qs(select);
    const { selected = null, onChange = null, includeBlank = false, blankLabel = "Select a project…" } = opts;
    let list = [];
    try {
      list = await fetchJSON("/projects");
      if (!Array.isArray(list)) list = [];
    } catch (e) {
      console.warn("[gims] loadProjects failed:", e);
      if (select) select.innerHTML = `<option value="">(could not load projects)</option>`;
      throw e;
    }
    if (select) {
      select.innerHTML = "";
      if (includeBlank) select.appendChild(el("option", { value: "", text: blankLabel }));
      list.forEach((name) => select.appendChild(el("option", { value: name, text: name })));
      const want = selected && list.includes(selected) ? selected : (includeBlank ? "" : list[0]);
      if (want != null) select.value = want;
      if (onChange) {
        select.addEventListener("change", () => onChange(select.value));
        if (select.value) onChange(select.value); // fire once for the default
      }
    }
    return list;
  }

  // ── Toasts (uses the watery.css .toasts / .toast layer) ───────────────────
  function toastHost() {
    let host = qs(".toasts");
    if (!host) { host = el("div", { class: "toasts", "aria-live": "polite", "aria-atomic": "true" }); document.body.appendChild(host); }
    return host;
  }
  function toast(message, kind = "ok", opts = {}) {
    const { timeout = kind === "err" ? 6000 : 3500 } = opts;
    const icon = kind === "err" ? "warning" : kind === "info" ? "info" : "check";
    const node = el("div", { class: "toast " + (kind === "err" ? "err" : kind === "info" ? "" : "ok"), role: "status" }, [
      iconEl(icon), el("span", { text: String(message) }),
    ]);
    toastHost().appendChild(node);
    if (timeout) setTimeout(() => { node.style.opacity = "0"; node.style.transition = "opacity .25s"; setTimeout(() => node.remove(), 250); }, timeout);
    return node;
  }

  // ── Canonical states: empty / loading / error / (results is the page's own) ──
  function _stateBlock(cls, inner) {
    return el("div", { class: "gims-state " + cls }, inner);
  }
  function renderLoading(container, opts = {}) {
    if (typeof container === "string") container = qs(container);
    const { message = "Loading…" } = opts;
    if (!container) return;
    container.replaceChildren(_stateBlock("is-loading", [
      el("span", { class: "gims-spinner", "aria-hidden": "true" }),
      el("p", { class: "gims-state-msg", text: message }),
    ]));
  }
  function renderEmpty(container, opts = {}) {
    if (typeof container === "string") container = qs(container);
    const { icon = "info", title = "Nothing here yet", message = "", action = null } = opts;
    if (!container) return;
    const kids = [
      el("span", { class: "gims-state-mark icon-chip round", html: iconSvg(icon) }),
      el("h3", { class: "gims-state-title", text: title }),
    ];
    if (message) kids.push(el("p", { class: "gims-state-msg", text: message }));
    if (action && action.label) {
      const btn = el("button", { class: "btn blue", type: "button", html: (action.icon ? iconSvg(action.icon) : "") + escapeHtml(action.label) });
      if (action.onClick) btn.addEventListener("click", action.onClick);
      kids.push(btn);
    }
    container.replaceChildren(_stateBlock("is-empty", kids));
  }
  function renderError(container, opts = {}) {
    if (typeof container === "string") container = qs(container);
    const { message = "Something went wrong.", onRetry = null } = opts;
    if (!container) return;
    const kids = [
      el("span", { class: "gims-state-mark icon-chip round", html: iconSvg("warning") }),
      el("h3", { class: "gims-state-title", text: "Couldn’t load this" }),
      el("p", { class: "gims-state-msg", text: String(message) }),
    ];
    if (onRetry) {
      const btn = el("button", { class: "btn blue", type: "button", html: iconSvg("refresh") + "Retry" });
      btn.addEventListener("click", onRetry);
      kids.push(btn);
    }
    container.replaceChildren(_stateBlock("is-error", kids));
  }

  Object.assign(GIMS, {
    qs, qsa, el, escapeHtml, iconSvg, iconEl, detectProject, onAuthed,
    fetchJSON, loadProjects, toast, renderLoading, renderEmpty, renderError,
  });
})();
