// shell.js — app-shell behaviour (Tier 0 foundation)
// The profile chip menu + login form are wired by /login/inject.js (it keys off
// #userchip-btn / #gims-login-form, which the shell emits). This file handles only
// the shell's own chrome: active rail link, data-tooltip hovers, and the help/tour hook.
(() => {
  "use strict";
  const qs = (s, r = document) => r.querySelector(s);
  const qsa = (s, r = document) => Array.from(r.querySelectorAll(s));

  function markActiveNav() {
    const key = document.body.dataset.page;
    if (!key) return;
    qsa(".rail-link[data-nav]").forEach((l) => l.classList.toggle("active", l.dataset.nav === key));
  }

  // Lightweight tooltips for any [data-tooltip] (rail links, action buttons).
  function setupTooltips() {
    const tip = qs("#tooltip");
    if (!tip) return;
    qsa("[data-tooltip]").forEach((node) => {
      node.addEventListener("mouseenter", () => {
        const text = node.getAttribute("data-tooltip");
        if (text) { tip.textContent = text; tip.style.opacity = "1"; }
      });
      node.addEventListener("mousemove", (e) => { tip.style.top = e.pageY + 15 + "px"; tip.style.left = e.pageX + 15 + "px"; });
      node.addEventListener("mouseleave", () => { tip.style.opacity = "0"; });
    });
  }

  // Help button → ask for a guided tour. The guided-tour engine (Phase 3) listens for
  // "gims:tour:start"; until it's present we fall back to a gentle toast.
  function wireHelp() {
    const btn = qs("#gims-help");
    if (!btn) return;
    btn.addEventListener("click", () => {
      const ev = new CustomEvent("gims:tour:start", { cancelable: true, detail: { page: document.body.dataset.page } });
      const handled = !document.dispatchEvent(ev); // a listener that calls preventDefault() "handles" it
      if (!handled && window.GIMS && typeof window.GIMS.toast === "function") {
        window.GIMS.toast("A guided tour for this page is coming soon.", "info");
      }
    });
  }

  // (Skin switcher behaviour lives in the shared /static/lib/theme-switch.js, loaded on every
  // page via theming.theme_head_tags() — so it works on standalone pages too, not just the shell.)

  function boot() { markActiveNav(); setupTooltips(); wireHelp(); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
