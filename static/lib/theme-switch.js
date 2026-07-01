// theme-switch.js — binds the header skin <select id="gims-theme"> on EVERY page that hosts
// it (app-shell, launcher, node pages). Part of the modular theme system: it flips data-theme
// on <html>, persists the choice to localStorage, and live-swaps (the alternate skin CSS is
// already loaded, so no reload). The pre-paint inline script (see theming.theme_head_tags)
// already applied the saved theme before first paint; this just reflects it in the control and
// handles changes. Loaded via theme_head_tags() so it travels with the switcher automatically.
(() => {
  "use strict";
  const KEY = "gims_theme"; // keep in sync with theming.THEME_STORAGE_KEY
  function wire() {
    const sel = document.getElementById("gims-theme");
    if (!sel) return;
    let cur = "watery";
    try { cur = localStorage.getItem(KEY) || "watery"; } catch (e) { /* private mode */ }
    sel.value = cur;
    if (cur && cur !== "watery") document.documentElement.setAttribute("data-theme", cur);
    sel.addEventListener("change", () => {
      const v = sel.value;
      try { localStorage.setItem(KEY, v); } catch (e) { /* private mode */ }
      if (v === "watery") document.documentElement.removeAttribute("data-theme");
      else document.documentElement.setAttribute("data-theme", v);
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wire);
  else wire();
})();
