// launcher.js — Watery v2 launcher behaviour
// - Collapsible sections (state persisted in localStorage)
// - Section rail: jump-to + active-on-scroll
// - Reflow on auth (hide empty sections / rail links, update counts) — driven
//   by the `gims:authapplied` event dispatched from /login/inject.js
// - Tooltips for .launcher-button
// - Plugin API (window.GIMS.launcher) kept intact for injected node scripts
// Auth UI itself (login form + profile chip) lives in /login/inject.js.

(() => {
  // ---- tiny DOM helpers ----
  const qs  = (s, el = document) => el.querySelector(s);
  const qsa = (s, el = document) => Array.from(el.querySelectorAll(s));
  const norm = (xs) => Array.from(new Set((xs || []).map(x => String(x).trim().toLowerCase()).filter(Boolean)));

  const sections = () => qsa("#sections .launcher-card");
  const secKey = (sec) => sec.dataset.section || sec.id;

  // ---- plugin registry ----
  const hooks = [];
  function use(fn) { if (typeof fn === "function") hooks.push(fn); }

  // ---- helpers exposed to plugins ----
  function buttons() { return qsa(".launcher-button"); }

  function addStyles(cssText) {
    const style = document.createElement("style");
    style.textContent = cssText;
    document.head.appendChild(style);
    return style;
  }
  function addStylesheet(href, attrs = {}) {
    const link = document.createElement("link");
    link.rel = "stylesheet"; link.href = href;
    Object.assign(link, attrs);
    document.head.appendChild(link);
    return link;
  }
  function addScript(src, attrs = {}) {
    return new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = src; Object.assign(s, attrs);
      s.onload = () => resolve(s); s.onerror = reject;
      document.body.appendChild(s);
    });
  }
  function addImage(src, attrs = {}, parent = document.body) {
    const img = document.createElement("img");
    img.src = src; Object.assign(img, attrs);
    parent.appendChild(img);
    return img;
  }
  function applyRoleGates(roles, gatedClass = "is-gated") {
    const userRoles = norm(roles);
    buttons().forEach(btn => {
      const need = norm((btn.getAttribute("data-roles") || "").split(","));
      const ok = (need.length === 0) || need.some(r => userRoles.includes(r));
      if (ok) {
        btn.classList.remove(gatedClass);
        btn.removeAttribute("data-gated-msg");
        btn.tabIndex = 0;
      } else {
        btn.classList.add(gatedClass);
        btn.setAttribute("data-gated-msg", `Requires: ${need.join(", ")}`);
        btn.tabIndex = -1;
      }
    });
  }
  function onNavigate(handler) {
    buttons().forEach(btn => {
      btn.addEventListener("click", (e) => {
        if (typeof handler === "function") {
          const res = handler({ event: e, href: btn.href, el: btn });
          if (res === false) { e.preventDefault(); e.stopPropagation(); }
        }
      });
    });
  }

  // ---- collapsible sections (persisted) ----
  const STORE = "gims_launcher_collapsed";
  function loadCollapsed() {
    try { return new Set(JSON.parse(localStorage.getItem(STORE) || "[]")); }
    catch { return new Set(); }
  }
  function saveCollapsed(set) {
    try { localStorage.setItem(STORE, JSON.stringify([...set])); } catch {}
  }
  let collapsed = loadCollapsed();

  function applyCollapsed() {
    sections().forEach(sec => {
      const isC = collapsed.has(secKey(sec));
      sec.classList.toggle("collapsed", isC);
      const head = qs(".card-head", sec);
      if (head) head.setAttribute("aria-expanded", String(!isC));
    });
    updateExpandAll();
  }
  function toggleSection(sec) {
    const k = secKey(sec);
    if (collapsed.has(k)) collapsed.delete(k); else collapsed.add(k);
    saveCollapsed(collapsed);
    applyCollapsed();
  }
  function updateExpandAll() {
    const btn = qs("#rail-expand-all");
    if (!btn) return;
    const secs = sections().filter(s => !s.hidden);   // only what the user can actually see
    const anyCollapsed = secs.some(s => collapsed.has(secKey(s)));
    const allCollapsed = secs.length > 0 && secs.every(s => collapsed.has(secKey(s)));
    const text = anyCollapsed ? "Expand all" : "Collapse all";
    const label = qs("span", btn);
    if (label) label.textContent = text;
    btn.setAttribute("aria-label", text + " sections");   // survives the span being display:none on mobile
    btn.setAttribute("title", text + " sections");
    btn.classList.toggle("all-collapsed", allCollapsed);
  }
  function wireCollapse() {
    sections().forEach(sec => {
      const head = qs(".card-head", sec);
      if (head) head.addEventListener("click", () => toggleSection(sec));
    });
    const btn = qs("#rail-expand-all");
    if (btn) btn.addEventListener("click", () => {
      const secs = sections().filter(s => !s.hidden);   // act only on visible sections
      const anyCollapsed = secs.some(s => collapsed.has(secKey(s)));
      if (anyCollapsed) secs.forEach(s => collapsed.delete(secKey(s)));
      else secs.forEach(s => collapsed.add(secKey(s)));
      saveCollapsed(collapsed);
      applyCollapsed();
    });
  }

  // ---- reflow: counts + hide empty sections/rail links (after auth gating) ----
  function isVisibleButton(b) { return b.style.display !== "none"; }
  function reflow() {
    let totalVisible = 0;
    sections().forEach(sec => {
      const vis = qsa(".launcher-button", sec).filter(isVisibleButton).length;
      sec.hidden = (vis === 0);
      const count = qs(".card-count", sec);
      if (count) count.textContent = String(vis);
      const link = qs(`.rail-link[data-target="${sec.id}"]`);
      if (link) {
        link.hidden = (vis === 0);
        const lc = qs(".rail-link-count", link);
        if (lc) lc.textContent = String(vis);
      }
      totalVisible += vis;
    });
    const empty = qs("#sections-empty");
    if (empty) empty.hidden = (totalVisible > 0);
    updateExpandAll();   // visible-section set may have changed after auth gating
  }

  // ---- section rail: jump-to + active-on-scroll ----
  function setActive(id) {
    qsa(".rail-link").forEach(l => l.classList.toggle("active", l.dataset.target === id));
  }
  function wireRail() {
    qsa(".rail-link").forEach(link => {
      link.addEventListener("click", (e) => {
        const sec = link.dataset.target && document.getElementById(link.dataset.target);
        if (!sec) return;  // a real navigation rail-link (e.g. Tutorial) — let the href through
        e.preventDefault();
        const k = secKey(sec);
        if (collapsed.has(k)) { collapsed.delete(k); saveCollapsed(collapsed); applyCollapsed(); }
        sec.scrollIntoView({ behavior: "smooth", block: "start" });
        setActive(link.dataset.target);
      });
    });
  }
  function observeSections() {
    if (!("IntersectionObserver" in window)) return;
    const io = new IntersectionObserver((entries) => {
      entries.forEach(en => { if (en.isIntersecting) setActive(en.target.id); });
    }, { rootMargin: "-15% 0px -75% 0px", threshold: 0 });
    sections().forEach(s => io.observe(s));
  }

  // ---- tooltips ----
  function setupTooltips() {
    const tooltip = qs("#tooltip");
    if (!tooltip) return;
    buttons().forEach(button => {
      button.addEventListener("mouseenter", () => {
        const text = button.getAttribute("data-tooltip");
        if (text) { tooltip.textContent = text; tooltip.style.opacity = "1"; }
      });
      button.addEventListener("mousemove", (e) => {
        tooltip.style.top = (e.pageY + 15) + "px";
        tooltip.style.left = (e.pageX + 15) + "px";
      });
      button.addEventListener("mouseleave", () => { tooltip.style.opacity = "0"; });
    });
  }

  // ---- expose plugin API ----
  const api = { use, buttons, addStyles, addStylesheet, addScript, addImage, applyRoleGates, onNavigate, reflow };
  window.GIMS = window.GIMS || {};
  window.GIMS.launcher = api;

  // Auth gating (inject.js) re-runs on login/logout — re-flow each time.
  document.addEventListener("gims:authapplied", reflow);

  // ---- boot ----
  function boot() {
    applyCollapsed();
    wireCollapse();
    wireRail();
    observeSections();
    setupTooltips();
    reflow(); // also covers the case where auth already applied before boot
    hooks.splice(0).forEach(fn => {
      try { fn(api); } catch (e) { console.error("[launcher plugin error]", e); }
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
