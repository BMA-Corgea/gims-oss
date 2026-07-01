// tutorial.js — wires the onboarding page's "Tour this page" CTA to the shared
// tour hook (the guided-tour engine, once present, listens for gims:tour:start).
(() => {
  "use strict";
  const btn = document.getElementById("tut-tour-launcher");
  if (!btn) return;
  btn.addEventListener("click", () => {
    const ev = new CustomEvent("gims:tour:start", { cancelable: true, detail: { page: "tutorial" } });
    const handled = !document.dispatchEvent(ev);
    if (!handled && window.GIMS && typeof window.GIMS.toast === "function") {
      window.GIMS.toast("A guided tour for this page is coming soon.", "info");
    }
  });
})();
