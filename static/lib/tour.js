/* ════════════════════════════════════════════════════════════════════════
   Tour — a dependency-free guided spotlight / coachmark engine.

   A narrator (optional character image + speech bubble) walks a first-time
   visitor through a UI. The current control is left in a bright "hole" while
   the rest of the screen is dimmed and click-blocked, so only the one correct
   element is reachable. Data-driven: you pass a STEPS array; the engine owns
   the overlay, measuring, repositioning on resize/scroll, and persistence.

   The dim is built from four "picture-frame" panels around the target rect
   (not a z-index race against a full-screen blocker) so the target stays
   natively clickable regardless of how it is nested. That is what makes this
   robust across arbitrary host UIs.

   ── Usage ───────────────────────────────────────────────────────────────
     Tour.start({
       storageKey: "myapp_tour_done",     // localStorage flag; omit to always run
       narrator: { image: "assets/guide.png", name: "Guide" },
       finishLabel: "Done",
       onFinish: () => {}, onSkip: () => {},
       steps: [
         { target: null, title: "Welcome", text: "Hi!", placement: "center" },
         { target: "#loginBtn", title: "Sign in", text: "Click here.",
           advanceOn: "target-click",
           beforeShow: (api) => { api.set("#email","you@lab.com"); } },
         { target: "#row", placement: "right", spotlight: true },
         { target: "#confirm", spotlight: false, raise: "#myModal",
           beforeShow: (api) => api.set("#pw","x") },   // modal lifted above the dim
       ],
     });
     Tour.replay({ storageKey, narrator, steps });   // clears flag + restarts

   ── Step fields ─────────────────────────────────────────────────────────
     target        CSS selector | () => Element | null (null = centered, no hole)
     title, text   strings  (or `html` for raw markup in the bubble)
     placement     "auto" | "top" | "bottom" | "left" | "right" | "center"
     spotlight     false to skip the cutout (e.g. a step over a modal)
     raise         selector | selector[] — lift element(s) above the dim
                   (use for modals so they stay visible & interactive)
     shield        selector | selector[] — set pointer-events:none on it so its own
                   surface can't catch a stray click (e.g. a raised modal's backdrop
                   that would otherwise close the dialog). Restored on step change.
     interactive   selector | selector[] — force pointer-events:auto (the parts that
                   must stay clickable when an ancestor is shielded, e.g. the dialog)
     advanceOn     "next" (default; Next button) | "target-click" | a DOM event name
     advanceDelay  ms to wait after a target-click before advancing (lets the
                   host's own handler run — open a modal, swap a view, …)
     padding       px around the target for the hole (default 8)
     beforeShow(api), afterShow(api)   hooks; may return a Promise
   ════════════════════════════════════════════════════════════════════════ */
(function (global) {
  "use strict";

  var DEFAULTS = {
    padding: 8,
    radius: 10,
    zIndex: 9000,
    dim: "rgba(6,10,20,0.74)",
    ring: "#4f6ef7",
    advanceDelay: 380,
    finishLabel: "Done",
    keyboard: true,
  };

  function isVisible(elm) {
    if (!elm || !elm.getBoundingClientRect) return false;
    var r = elm.getBoundingClientRect();
    if (r.width <= 0 && r.height <= 0) return false;
    var s = getComputedStyle(elm);
    return s.display !== "none" && s.visibility !== "hidden";
  }

  // Resolve a selector/function target, retrying briefly so a step can point at
  // an element that appears asynchronously (a modal opening, a view swapping in).
  function resolve(sel) {
    return new Promise(function (res) {
      if (!sel) return res(null); // intentional centered step
      var tries = 0;
      (function poll() {
        var e = typeof sel === "function" ? sel() : document.querySelector(sel);
        if (e && isVisible(e)) return res(e);
        if (++tries > 48) return res(isVisible(e) ? e : null);
        requestAnimationFrame(function () { setTimeout(poll, 25); });
      })();
    });
  }

  function wait(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  function Tour(config) {
    this.cfg = Object.assign({}, DEFAULTS, config);
    this.steps = (config.steps || []).slice();
    this.i = -1;
    this.raised = [];
    this.styled = [];
    this.dead = false;
  }

  Tour.prototype.start = function (opts) {
    opts = opts || {};
    var key = this.cfg.storageKey;
    if (key && !opts.force) {
      try { if (localStorage.getItem(key) === "done") return false; } catch (e) {}
    }
    this._build();
    this._goto(0);
    return true;
  };

  Tour.prototype._build = function () {
    var z = this.cfg.zIndex, self = this;
    var root = document.createElement("div");
    root.className = "tour-root";
    root.style.setProperty("--tour-dim", this.cfg.dim);
    root.style.setProperty("--tour-ring", this.cfg.ring);
    root.style.setProperty("--tour-radius", this.cfg.radius + "px");

    // four dim panels (the "picture frame" around the hole) + a highlight ring
    this.panels = {};
    ["top", "bottom", "left", "right"].forEach(function (k) {
      var p = document.createElement("div");
      p.className = "tour-dimp";
      p.style.zIndex = z;
      root.appendChild(p);
      self.panels[k] = p;
    });
    var ring = document.createElement("div");
    ring.className = "tour-ring";
    ring.style.zIndex = z + 1;
    root.appendChild(ring);

    var narr = document.createElement("div");
    narr.className = "tour-narrator";
    narr.style.zIndex = z + 3;
    var avatar = this.cfg.narrator && this.cfg.narrator.image
      ? '<img class="tour-avatar" src="' + this.cfg.narrator.image + '" alt="" />' : "";
    narr.innerHTML =
      avatar +
      '<div class="tour-bubble">' +
        '<div class="tour-title"></div>' +
        '<div class="tour-text"></div>' +
        '<div class="tour-foot">' +
          '<span class="tour-progress"></span>' +
          '<span class="tour-grow"></span>' +
          '<button type="button" class="tour-btn tour-skip">Skip</button>' +
          '<button type="button" class="tour-btn tour-back">Back</button>' +
          '<button type="button" class="tour-btn tour-primary tour-next">Next</button>' +
        '</div>' +
      '</div>';
    root.appendChild(narr);

    document.body.appendChild(root);
    document.body.classList.add("tour-on");

    this.root = root; this.ring = ring; this.narr = narr;
    this.elTitle = narr.querySelector(".tour-title");
    this.elText = narr.querySelector(".tour-text");
    this.elProg = narr.querySelector(".tour-progress");
    this.elBubble = narr.querySelector(".tour-bubble");
    this.btnNext = narr.querySelector(".tour-next");
    this.btnBack = narr.querySelector(".tour-back");
    this.btnSkip = narr.querySelector(".tour-skip");

    // Peek control: a fixed pill in the screen's bottom-right corner (outside the bubble), NOT in
    // the footer — so it never crowds the nav buttons. Toggles "view the whole page" mode.
    var peek = document.createElement("button");
    peek.type = "button";
    peek.className = "tour-btn tour-peek";
    peek.title = "Hide the spotlight to see and scroll the whole page, then resume";
    peek.textContent = "View page";
    peek.style.zIndex = z + 4;
    root.appendChild(peek);
    this.btnPeek = peek;

    this.btnNext.addEventListener("click", function () { self.next(); });
    this.btnBack.addEventListener("click", function () { self.back(); });
    this.btnSkip.addEventListener("click", function () { self.skip(); });
    this.btnPeek.addEventListener("click", function () { self.togglePeek(); });

    this._onReflow = function () { self._reposition(); };
    window.addEventListener("resize", this._onReflow, { passive: true });
    window.addEventListener("scroll", this._onReflow, { passive: true, capture: true });
    // re-pin the peek control once the bubble finishes sliding into place
    narr.addEventListener("transitionend", function (e) {
      if (e.propertyName === "left" || e.propertyName === "top") self._positionPeek();
    });

    if (this.cfg.keyboard) {
      this._onKey = function (e) {
        if (e.key === "Escape") { self.skip(); return; }
        if (e.key === "ArrowLeft") { self.back(); return; }
        if (e.key === "Enter" || e.key === "ArrowRight") {
          e.preventDefault();
          var s = self.curStep;
          // On a target-click step, Enter/→ performs the real action (so the host runs and
          // the tour advances together); on a DOM-event step, ignore it (let the event
          // drive); otherwise advance manually.
          if (s && s.advanceOn === "target-click" && self.curTarget && isVisible(self.curTarget)) self.curTarget.click();
          else if (!s || !s.advanceOn || s.advanceOn === "next") self.next();
        }
      };
      document.addEventListener("keydown", this._onKey);
    }
  };

  Tour.prototype._goto = function (idx) {
    var self = this;
    if (this.dead) return;
    if (idx >= this.steps.length) return this.finish();
    if (idx < 0) idx = 0;
    this.i = idx;
    var step = this.steps[idx];

    this._unraise();
    this._detachAdvance();
    this._setPeek(false, true);   // each step starts with the spotlight on

    this.btnBack.style.visibility = idx === 0 ? "hidden" : "visible";
    this.btnNext.textContent = idx === this.steps.length - 1 ? this.cfg.finishLabel : "Next";
    // On steps that require a specific host action (advanceOn target-click / a DOM event),
    // hide the generic Next button so the visitor can't skip past the action that gates the
    // next target. Plain ("next") steps keep it.
    var manualAdvance = !step.advanceOn || step.advanceOn === "next";
    this.btnNext.style.display = manualAdvance ? "" : "none";
    this.elProg.textContent = (idx + 1) + " / " + this.steps.length;
    // Show this step's text NOW (before beforeShow runs), so a beforeShow that waits for the page to
    // catch up — promoting a field, a panel loading in — never leaves the bubble showing the previous
    // step's text. The spotlight still lands once the target resolves below.
    this.elTitle.textContent = step.title || "";
    this.elTitle.style.display = step.title ? "" : "none";
    if (step.html != null) this.elText.innerHTML = step.html;
    else this.elText.textContent = step.text || "";

    Promise.resolve(step.beforeShow ? step.beforeShow(this._api()) : null)
      .catch(function () { /* a failing beforeShow must not freeze the tour on the old spotlight */ })
      .then(function () { return resolve(step.target); })
      .then(function (target) {
        if (self.dead || self.i !== idx) return;
        self.curStep = step; self.curTarget = target;

        if (step.raise) {
          (Array.isArray(step.raise) ? step.raise : [step.raise]).forEach(function (rs) {
            var re = typeof rs === "function" ? rs() : document.querySelector(rs);
            if (re) self._raise(re, self.cfg.zIndex + 2);
          });
        }
        // shield: make an element's own surface ignore clicks (so e.g. a raised modal's
        // backdrop can't swallow a stray click and close itself) while `interactive`
        // selectors stay clickable. Both are restored when the step changes.
        if (step.shield) {
          (Array.isArray(step.shield) ? step.shield : [step.shield]).forEach(function (sel) {
            var e = typeof sel === "function" ? sel() : document.querySelector(sel);
            if (e) self._setStyle(e, "pointerEvents", "none");
          });
        }
        if (step.interactive) {
          (Array.isArray(step.interactive) ? step.interactive : [step.interactive]).forEach(function (sel) {
            var e = typeof sel === "function" ? sel() : document.querySelector(sel);
            if (e) self._setStyle(e, "pointerEvents", "auto");
          });
        }
        if (target) {
          try { target.scrollIntoView({ block: "center", inline: "center", behavior: "smooth" }); } catch (e) {}
        }

        setTimeout(function () {
          if (self.dead || self.i !== idx) return;
          self._reposition();
          if (step.afterShow) step.afterShow(self._api());
          self._attachAdvance(step, target);
        }, target ? 240 : 0);
      });
  };

  Tour.prototype._reposition = function () {
    if (this.dead || !this.curStep) return;
    var step = this.curStep, t = this.curTarget, vw = window.innerWidth, vh = window.innerHeight;
    var on = t && step.spotlight !== false && isVisible(t);
    if (on) {
      var pad = step.padding != null ? step.padding : this.cfg.padding;
      var r = t.getBoundingClientRect();
      var l = Math.max(0, r.left - pad), tp = Math.max(0, r.top - pad);
      var w = Math.min(vw - l, r.width + 2 * pad), h = Math.min(vh - tp, r.height + 2 * pad);
      this._frame(l, tp, w, h, false);
      this.ring.style.display = "block";
      this.ring.style.left = l + "px"; this.ring.style.top = tp + "px";
      this.ring.style.width = w + "px"; this.ring.style.height = h + "px";
      this._placeNarrator({ left: l, top: tp, width: w, height: h, right: l + w, bottom: tp + h }, step.placement);
    } else {
      this._frame(0, 0, vw, vh, true);
      this.ring.style.display = "none";
      // spotlight suppressed but a target resolved (e.g. a modal step): still honor the
      // authored placement so the narrator sits beside the target, not centered over it.
      if (t && isVisible(t)) {
        var rr = t.getBoundingClientRect();
        this._placeNarrator({ left: rr.left, top: rr.top, width: rr.width, height: rr.height, right: rr.right, bottom: rr.bottom }, step.placement);
      } else {
        this._placeNarrator(null, "center");
      }
    }
    this._positionPeek();
  };

  // Pin the peek control just below the bubble's bottom-right corner (the corner of the textbox,
  // not the screen). Skipped while peeking — the bubble is hidden then, so the Resume pill stays put.
  Tour.prototype._positionPeek = function () {
    if (!this.btnPeek || this.peeking || !this.elBubble) return;
    var vw = window.innerWidth, vh = window.innerHeight;
    var b = this.elBubble.getBoundingClientRect();
    var pw = this.btnPeek.offsetWidth || 92, ph = this.btnPeek.offsetHeight || 32;
    this.btnPeek.style.right = "auto"; this.btnPeek.style.bottom = "auto";
    this.btnPeek.style.left = Math.max(6, Math.min(b.right - pw, vw - pw - 6)) + "px";
    this.btnPeek.style.top = Math.max(6, Math.min(b.bottom + 8, vh - ph - 6)) + "px";
  };

  Tour.prototype._frame = function (l, t, w, h, full) {
    var vw = window.innerWidth, vh = window.innerHeight, p = this.panels;
    function set(e, x, y, ww, hh) { e.style.left = x + "px"; e.style.top = y + "px"; e.style.width = Math.max(0, ww) + "px"; e.style.height = Math.max(0, hh) + "px"; }
    if (full) { set(p.top, 0, 0, vw, vh); set(p.bottom, 0, 0, 0, 0); set(p.left, 0, 0, 0, 0); set(p.right, 0, 0, 0, 0); return; }
    set(p.top, 0, 0, vw, t);
    set(p.bottom, 0, t + h, vw, vh - (t + h));
    set(p.left, 0, t, l, h);
    set(p.right, l + w, t, vw - (l + w), h);
  };

  Tour.prototype._placeNarrator = function (rect, placement) {
    var n = this.narr, vw = window.innerWidth, vh = window.innerHeight, margin = 14, gap = 16;
    n.style.maxWidth = Math.min(360, vw - 2 * margin) + "px";
    var nb = n.getBoundingClientRect(), nw = nb.width || 320, nh = nb.height || 160;
    var p = placement || "auto";

    if (!rect || p === "center") {
      n.style.left = Math.max(margin, Math.min((vw - nw) / 2, vw - nw - margin)) + "px";
      n.style.top = Math.max(margin, Math.min((vh - nh) / 2, vh - nh - margin)) + "px";
      return;
    }

    function posFor(side) {
      if (side === "bottom") return { left: rect.left + rect.width / 2 - nw / 2, top: rect.bottom + gap };
      if (side === "top")    return { left: rect.left + rect.width / 2 - nw / 2, top: rect.top - gap - nh };
      if (side === "right")  return { left: rect.right + gap, top: rect.top + rect.height / 2 - nh / 2 };
      return { left: rect.left - gap - nw, top: rect.top + rect.height / 2 - nh / 2 };  // left
    }
    function clamp(q) {
      return { left: Math.max(margin, Math.min(q.left, vw - nw - margin)),
               top: Math.max(margin, Math.min(q.top, vh - nh - margin)) };
    }
    // Does the narrator (gnome + bubble) at clamped position c cover the spotlighted rect? A small
    // buffer keeps it clear of the highlight ring too.
    function covers(c) {
      var pad = 8;
      return c.left < rect.right + pad && c.left + nw > rect.left - pad &&
             c.top < rect.bottom + pad && c.top + nh > rect.top - pad;
    }

    // Try the requested side first, then the auto-preference order; take the first placement that,
    // AFTER clamping to the viewport, does NOT cover the target — so the gnome can never sit over the
    // control it's pointing at (which happened when a requested side had no room and got clamped back
    // onto the target).
    var order = [];
    if (p !== "auto") order.push(p);
    ["bottom", "top", "right", "left"].forEach(function (s) { if (order.indexOf(s) < 0) order.push(s); });

    var pick = null;
    for (var i = 0; i < order.length; i++) {
      var c = clamp(posFor(order[i]));
      if (!covers(c)) { pick = c; break; }
    }
    if (!pick) {
      // No side clears the target — drop the narrator into a corner: bottom-right first (out of the
      // way, where the View-page pill sits), then top-left if that still overlaps.
      var br = { left: vw - nw - margin, top: vh - nh - margin };
      var tl = { left: margin, top: margin };
      pick = !covers(br) ? br : (!covers(tl) ? tl : br);
    }

    n.style.left = pick.left + "px";
    n.style.top = pick.top + "px";
  };

  Tour.prototype._attachAdvance = function (step, target) {
    var self = this;
    if (step.advanceOn === "target-click" && target) {
      this._advTarget = target;
      this._advHandler = function () {
        self._detachAdvance();
        setTimeout(function () { self.next(); }, step.advanceDelay != null ? step.advanceDelay : self.cfg.advanceDelay);
      };
      target.addEventListener("click", this._advHandler, true);
    } else if (typeof step.advanceOn === "string" && step.advanceOn !== "next") {
      this._advEvent = step.advanceOn;
      this._advHandler = function () { self._detachAdvance(); self.next(); };
      document.addEventListener(step.advanceOn, this._advHandler, { once: true });
    }
  };

  Tour.prototype._detachAdvance = function () {
    if (this._advHandler && this._advTarget) this._advTarget.removeEventListener("click", this._advHandler, true);
    if (this._advHandler && this._advEvent) document.removeEventListener(this._advEvent, this._advHandler);
    this._advHandler = this._advTarget = this._advEvent = null;
  };

  Tour.prototype._raise = function (elm, z) {
    this.raised.push({ el: elm, pos: elm.style.position, z: elm.style.zIndex });
    if (getComputedStyle(elm).position === "static") elm.style.position = "relative";
    elm.style.zIndex = z;
    elm.classList.add("tour-raised");
  };
  Tour.prototype._setStyle = function (elm, prop, val) {
    this.styled.push({ el: elm, prop: prop, prev: elm.style[prop] });
    elm.style[prop] = val;
  };
  Tour.prototype._unraise = function () {
    this.raised.forEach(function (p) {
      p.el.style.position = p.pos; p.el.style.zIndex = p.z; p.el.classList.remove("tour-raised");
    });
    this.raised = [];
    this.styled.forEach(function (s) { s.el.style[s.prop] = s.prev; });
    this.styled = [];
  };

  // Peek / "view the whole page": fade the dim + ring and collapse the narrator to a small
  // "Resume tour" pill, so the visitor can see and scroll the real page in context, then snap back
  // to the current step. Reset automatically on every step change.
  Tour.prototype._setPeek = function (on, quiet) {
    this.peeking = !!on;
    if (!this.root) return;
    this.root.classList.toggle("tour-peeking", this.peeking);
    if (this.btnPeek) {
      this.btnPeek.textContent = this.peeking ? "▸ Resume tour" : "View page";
      this.btnPeek.classList.toggle("tour-primary", this.peeking);
      if (this.peeking) {
        // the bubble is hidden while peeking — park the glowing Resume pill in the screen's
        // bottom-right corner (un-peek re-pins it to the bubble via _positionPeek).
        this.btnPeek.style.left = "auto"; this.btnPeek.style.top = "auto";
        this.btnPeek.style.right = "16px"; this.btnPeek.style.bottom = "16px";
      }
    }
    if (!this.peeking && !quiet) {
      // returning from peek: bring the target back into view and re-measure the spotlight
      var self = this, t = this.curTarget;
      if (t) { try { t.scrollIntoView({ block: "center", inline: "center", behavior: "smooth" }); } catch (e) {} }
      setTimeout(function () { self._reposition(); }, 80);
    }
  };
  Tour.prototype.togglePeek = function () { this._setPeek(!this.peeking); };

  Tour.prototype._api = function () {
    var self = this;
    return {
      index: function () { return self.i; },
      next: function () { self.next(); },
      back: function () { self.back(); },
      wait: wait,
      el: function (sel) { return document.querySelector(sel); },
      click: function (sel) { var e = document.querySelector(sel); if (e) e.click(); return e; },
      set: function (sel, val) {
        var e = document.querySelector(sel);
        if (e) { e.value = val; e.dispatchEvent(new Event("input", { bubbles: true })); e.dispatchEvent(new Event("change", { bubbles: true })); }
        return e;
      },
    };
  };

  Tour.prototype.next = function () { this._goto(this.i + 1); };
  Tour.prototype.back = function () { this._goto(this.i - 1); };
  Tour.prototype.skip = function () { this._end(this.cfg.onSkip, false); };
  Tour.prototype.finish = function () { this._end(this.cfg.onFinish, true); };

  Tour.prototype._end = function (cb, completed) {
    if (this.dead) return;
    this.dead = true;
    this._unraise(); this._detachAdvance();
    if (this.cfg.storageKey) { try { localStorage.setItem(this.cfg.storageKey, "done"); } catch (e) {} }
    window.removeEventListener("resize", this._onReflow);
    window.removeEventListener("scroll", this._onReflow, { capture: true });
    if (this._onKey) document.removeEventListener("keydown", this._onKey);
    if (this.root && this.root.parentNode) this.root.parentNode.removeChild(this.root);
    document.body.classList.remove("tour-on");
    if (typeof cb === "function") cb({ completed: !!completed });
  };

  var active = null;
  global.Tour = {
    start: function (config) {
      if (active && !active.dead) active._end();
      active = new Tour(config);
      var shown = active.start();
      return { instance: active, shown: shown };
    },
    replay: function (config) {
      if (config && config.storageKey) { try { localStorage.removeItem(config.storageKey); } catch (e) {} }
      if (active && !active.dead) active._end();
      active = new Tour(config);
      active.start({ force: true });
      return active;
    },
    stop: function () { if (active && !active.dead) active._end(); },
  };
})(window);
