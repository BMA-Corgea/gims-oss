// launcher.js — minimal core for launcher.html
// - Tooltips for .launcher-button
// - Tiny plugin API so nodes can inject behavior (JS/CSS/images)
// - Helpers exposed to injected scripts (role gating, nav guard, etc.)

(() => {
  // ---- tiny DOM helpers ----
  const qs  = (s, el = document) => el.querySelector(s);
  const qsa = (s, el = document) => Array.from(el.querySelectorAll(s));
  const norm = (xs) => Array.from(new Set((xs || []).map(x => String(x).trim().toLowerCase()).filter(Boolean)));

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
    link.rel = "stylesheet";
    link.href = href;
    Object.assign(link, attrs);
    document.head.appendChild(link);
    return link;
  }

  function addScript(src, attrs = {}) {
    return new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = src;
      Object.assign(s, attrs);
      s.onload = () => resolve(s);
      s.onerror = reject;
      document.body.appendChild(s);
    });
  }

  function addImage(src, attrs = {}, parent = document.body) {
    const img = document.createElement("img");
    img.src = src;
    Object.assign(img, attrs);
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
    // Let a plugin observe/guard navigations from launcher buttons
    buttons().forEach(btn => {
      btn.addEventListener("click", (e) => {
        if (typeof handler === "function") {
          const res = handler({ event: e, href: btn.href, el: btn });
          if (res === false) {
            e.preventDefault();
            e.stopPropagation();
          }
        }
      });
    });
  }

  // ---- tooltips (kept from your original) ----
  function setupTooltips() {
    const tooltip = qs("#tooltip");
    if (!tooltip) return;

    buttons().forEach(button => {
      button.addEventListener("mouseenter", () => {
        const text = button.getAttribute("data-tooltip");
        if (text) {
          tooltip.textContent = text;
          tooltip.style.opacity = "1";
        }
      });

      button.addEventListener("mousemove", (e) => {
        tooltip.style.top = (e.pageY + 15) + "px";
        tooltip.style.left = (e.pageX + 15) + "px";
      });

      button.addEventListener("mouseleave", () => {
        tooltip.style.opacity = "0";
      });

      button.addEventListener("click", () => {
        console.log(`Navigating to: ${button.href}`);
      });
    });
  }

  // ---- expose plugin API ----
  const api = {
    use,
    buttons,
    addStyles,
    addStylesheet,
    addScript,
    addImage,
    applyRoleGates,
    onNavigate,
  };
  window.GIMS = window.GIMS || {};
  window.GIMS.launcher = api;

  // ---- boot ----
  document.addEventListener("DOMContentLoaded", () => {
    setupTooltips();
    // run all registered plugin callbacks with the API
    hooks.splice(0).forEach(fn => {
      try { fn(api); } catch (e) { console.error("[launcher plugin error]", e); }
    });
  });
})();
