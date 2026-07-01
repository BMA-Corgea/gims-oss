# nodes/star_spirits_ui_node.py
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

# Orchestration glue
from core.orchestration.node import Node, NodeKind
from core.orchestration.module import Module

router = APIRouter(prefix="/star-spirits-ui", tags=["Star Spirits – UI"])

# ──────────────────────────────────────────────────────────────────────────────
# Overlay script: spawns pixel-art spirits on the page and talks to the state
# node at /star-spirits/{project}/…
# ──────────────────────────────────────────────────────────────────────────────
INJECT_JS = r"""
(function(){
  "use strict";

  // config
  const SPIRITS = [
    { id: "s1", name: "Eldstar",  color: "#ffd54a" },
    { id: "s2", name: "Mamar",    color: "#ff7aa2" },
    { id: "s3", name: "Skolar",   color: "#98c1ff" },
    { id: "s4", name: "Muskular", color: "#b5ff8a" },
    { id: "s5", name: "Misstar",  color: "#ffb3ff" },
    { id: "s6", name: "Klevar",   color: "#b39ddb" },
    { id: "s7", name: "Kalmar",   color: "#80cbc4" },
  ];

  // ---- auth hook so we can react to login/logout without touching the login node
  function hookAuthEvents(){
    if (!window.GIMS) window.GIMS = {};
    if (window.GIMS.__authHooked) return;
    const g = window.GIMS;
    const prev = g.__applyAuthMe;
    g.__applyAuthMe = function(me){
      try { prev && prev(me); } finally {
        window.dispatchEvent(new CustomEvent("gims:auth-changed", { detail: me }));
      }
    };
    g.__authHooked = true;
  }
  hookAuthEvents();

  function detectProject() {
    if (window.GIMS_PROJECT) return window.GIMS_PROJECT;
    const m = location.pathname.match(/\/([A-Za-z0-9._-]+)\//);
    return (m && m[1]) || "LIMS-System";
  }
  const project = detectProject();

  async function me() {
    const tok = localStorage.getItem("gims_token");
    if (!tok) return null;
    const res = await fetch("/login/" + encodeURIComponent(project) + "/auth/me", {
      headers: { "Authorization": "Bearer " + tok }
    });
    if (!res.ok) return null;
    return res.json();
  }

  function mountStyles() {
    if (document.getElementById("star-spirits-css")) return;
    const s = document.createElement("style");
    s.id = "star-spirits-css";
    s.textContent = `
      .ss-layer{position:fixed;inset:0;pointer-events:none;z-index:2147483000}
      .ss-sprite{position:absolute; width:18px; height:18px; image-rendering:pixelated; cursor:pointer; pointer-events:auto; box-shadow:0 0 0 1px rgba(0,0,0,.25)}
      .ss-pop{position:fixed;left:12px;bottom:12px;background:rgba(13,13,20,.85);color:#e8eefb;border:1px solid #2b2b40;border-radius:10px;padding:8px 10px;font:12px/1.2 system-ui, sans-serif;transition:opacity .2s}
      .ss-pop b{font-weight:600}
      .ss-fireworks{position:fixed;inset:0;pointer-events:none;z-index:2147483001}
    `;
    document.head.appendChild(s);
  }

  function layer(){
    let el = document.querySelector(".ss-layer");
    if (!el){
      el = document.createElement("div");
      el.className = "ss-layer";
      document.body.appendChild(el);
    }
    return el;
  }

  function makeSprite(color) {
    const el = document.createElement("div");
    el.className = "ss-sprite";
    el.style.background = "transparent";
    const pix = document.createElement("canvas");
    pix.width = 9; pix.height = 9; el.appendChild(pix);
    const ctx = pix.getContext("2d");
    ctx.imageSmoothingEnabled = false;
    ctx.fillStyle = color;
    const pts = [
      [4,0],[3,2],[1,3],[3,4],[2,6],[4,5],[6,6],[5,4],[7,3],[5,2]
    ];
    pts.forEach(([x,y])=>{ ctx.fillRect(x,y,1,1); });
    pix.style.width = "100%"; pix.style.height = "100%";
    return el;
  }

  function randomPos(el) {
    const vw = Math.max(document.documentElement.clientWidth, window.innerWidth||0);
    const vh = Math.max(document.documentElement.clientHeight, window.innerHeight||0);
    const x = Math.floor(Math.random()*(vw-24))+4;
    const y = Math.floor(Math.random()*(vh-48))+4;
    el.style.left = x+"px"; el.style.top = y+"px";
  }

  async function getProgress(user_id) {
    const url = `/star-spirits/${encodeURIComponent(project)}/progress?user_id=${encodeURIComponent(user_id)}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error("progress fetch failed");
    return res.json();
  }
  async function collect(user_id, spirit) {
    const url = `/star-spirits/${encodeURIComponent(project)}/collect`;
    const res = await fetch(url, {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({user_id, spirit})
    });
    if (!res.ok) throw new Error("collect failed");
    return res.json();
  }

  function toast(msg){
    let p = document.querySelector(".ss-pop");
    if (!p) {
      p = document.createElement("div");
      p.className = "ss-pop";
      document.body.appendChild(p);
    }
    p.textContent = msg;
    p.style.opacity = "1";
    clearTimeout(p._t);
    p._t = setTimeout(()=>{ p.style.opacity = "0"; }, 1800);
  }

  // ── fireworks with cleanup and reduced-motion support
  let _fwCanvas = null, _fwRAF = 0, _fwResize = null;
  function fireworks(ms){
    // Respect user preference
    try {
      if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
        toast("⭐ Congratulations!");
        return () => {};
      }
    } catch {}

    // If an old canvas exists, clean it first
    if (_fwCanvas) {
      try {
        cancelAnimationFrame(_fwRAF);
        if (_fwResize) window.removeEventListener("resize", _fwResize);
        _fwCanvas.remove();
      } catch {}
      _fwCanvas = null; _fwRAF = 0; _fwResize = null;
    }

    const c = document.createElement("canvas");
    c.className = "ss-fireworks";
    document.body.appendChild(c);
    const ctx = c.getContext("2d");

    function fit(){
      const dpr = Math.max(1, window.devicePixelRatio||1);
      c.width  = Math.floor(innerWidth * dpr);
      c.height = Math.floor(innerHeight * dpr);
      c.style.width  = innerWidth + "px";
      c.style.height = innerHeight + "px";
      ctx.setTransform(dpr,0,0,dpr,0,0);
    }
    fit();
    _fwResize = fit;
    window.addEventListener("resize", fit);

    const parts = [];
    const colors = ['#ffd54a','#ff7aa2','#98c1ff','#b5ff8a','#ffb3ff','#b39ddb','#80cbc4','#ffffff'];

    function burst(x,y){
      const n = 40 + Math.floor(Math.random()*20);
      for (let i=0;i<n;i++){
        const a = Math.random()*Math.PI*2;
        const s = (Math.random()*3+1);
        parts.push({
          x, y, vx: Math.cos(a)*s, vy: Math.sin(a)*s,
          life: 40+Math.random()*20,
          color: colors[(Math.random()*colors.length)|0]
        });
      }
    }

    burst(innerWidth*0.25, innerHeight*0.35);
    burst(innerWidth*0.5,  innerHeight*0.28);
    burst(innerWidth*0.75, innerHeight*0.35);

    let start = performance.now(), last = 0;
    (function tick(t){
      _fwRAF = requestAnimationFrame(tick);
      if (t-last < 16) return; last = t;

      ctx.fillStyle = "rgba(0,0,0,0.15)";
      ctx.fillRect(0,0,innerWidth,innerHeight);

      if (Math.random() < 0.06) burst(Math.random()*innerWidth, Math.random()*innerHeight*0.8 + innerHeight*0.1);

      ctx.globalCompositeOperation = "lighter";
      for (let i=parts.length-1;i>=0;i--){
        const p = parts[i];
        p.x += p.vx; p.y += p.vy; p.vy += 0.05; p.life -= 1;
        ctx.fillStyle = p.color;
        ctx.beginPath();
        ctx.arc(p.x, p.y, 2, 0, Math.PI*2);
        ctx.fill();
        if (p.life <= 0) parts.splice(i,1);
      }

      if (t-start > (ms||2500)){
        cleanup();
      }
    })();

    function cleanup(){
      try { cancelAnimationFrame(_fwRAF); } catch {}
      try { if (_fwResize) window.removeEventListener("resize", _fwResize); } catch {}
      try { c.remove(); } catch {}
      _fwCanvas = null; _fwRAF = 0; _fwResize = null;
    }

    _fwCanvas = c;
    return cleanup;
  }

  let _spawning = false;
  async function spawnForCurrentUser(){
    if (_spawning) return;
    _spawning = true;
    try {
      mountStyles();
      const l = layer();
      l.innerHTML = "";

      const who = await me();
      if (!who) {
        toast("Sign in to collect star spirits!");
        return;
      }
      const user_id = who.user.id;

      const prog = await getProgress(user_id);
      const uncollected = SPIRITS.filter(s => !prog[s.id]);

      if (uncollected.length === 0) {
        toast("All 7 star spirits collected. ⭐");
        fireworks(2500);
        return;
      }

      const remaining = new Set(uncollected.map(s => s.id));

      uncollected.forEach(sp => {
        const sprite = makeSprite(sp.color);
        sprite.dataset.sid = sp.id;
        sprite.title = `${sp.name} (${sp.id})`;
        randomPos(sprite);
        sprite.addEventListener("click", async () => {
          try {
            await collect(user_id, sp.id);
            remaining.delete(sp.id);
            sprite.replaceWith(); // remove without retaining listeners
            toast(`Collected ${sp.name}!`);
            if (remaining.size === 0) {
              fireworks(2500);
              window.dispatchEvent(new CustomEvent("star-spirits:completed", { detail: { user_id } }));
              toast("All 7 star spirits collected. ⭐");
            } else {
              window.dispatchEvent(new CustomEvent("star-spirits:updated", { detail: { user_id, spirit: sp.id } }));
            }
          } catch {
            toast("Collect failed.");
          }
        });
        l.appendChild(sprite);
      });
    } finally {
      _spawning = false;
    }
  }

  // react to auth changes (login/logout) by respawning for the new user
  window.addEventListener("gims:auth-changed", () => { spawnForCurrentUser().catch(()=>{}); });

  // expose tiny dev API for manual triggering if needed
  window.StarSpirits = Object.freeze({
    fireworks,
    spawnForCurrentUser
  });

  // initial spawn
  spawnForCurrentUser().catch(()=>{});
})();
"""

# State Dock tab JS unchanged (kept as-is)
STATE_TAB_JS = r"""
(function(){
  function whenDock(cb){
    if (window.StateDock) return cb();
    const t = setInterval(()=>{ if (window.StateDock){ clearInterval(t); cb(); } }, 50);
  }
  function detectProject(){
    if (window.GIMS_PROJECT) return window.GIMS_PROJECT;
    const m = location.pathname.match(/\/([A-Za-z0-9._-]+)\//);
    return (m && m[1]) || "LIMS-System";
  }
  // auth hook (same idea as overlay)
  function hookAuthEvents(){
    if (!window.GIMS) window.GIMS = {};
    if (window.GIMS.__authHooked) return;
    const g = window.GIMS;
    const prev = g.__applyAuthMe;
    g.__applyAuthMe = function(me){
      try { prev && prev(me); } finally {
        window.dispatchEvent(new CustomEvent("gims:auth-changed", { detail: me }));
      }
    };
    g.__authHooked = true;
  }
  hookAuthEvents();

  async function me(project){
    const tok = localStorage.getItem("gims_token");
    if (!tok) return null;
    const r = await fetch("/login/" + encodeURIComponent(project) + "/auth/me",
                          { headers:{Authorization:"Bearer "+tok} });
    if (!r.ok) return null;
    return r.json();
  }
  async function getProgress(project, user_id){
    const r = await fetch(`/star-spirits/${encodeURIComponent(project)}/progress?user_id=${encodeURIComponent(user_id)}`);
    if (!r.ok) throw new Error("progress failed");
    return r.json();
  }
  async function resetProgress(project, user_id){
    const r = await fetch(`/star-spirits/${encodeURIComponent(project)}/reset?user_id=${encodeURIComponent(user_id)}`, {method:"POST"});
    if (!r.ok) throw new Error("reset failed");
    return r.json();
  }
  function chip(label, on){
    const el = document.createElement("span");
    el.textContent = label;
    el.className = "ss-chip";
    el.style.cssText = `display:inline-block;margin:2px 4px;padding:3px 8px;border-radius:999px;
      border:1px solid rgba(255,255,255,.12);font:12px/1 system-ui,sans-serif;${on?"background:#244e2a;color:#c9ffd0;border-color:#2f6b37":"background:#232323;color:#cfd3df"}`;
    return el;
  }
  function mountStyles(){
    if (document.getElementById("ss-dock-css")) return;
    const s = document.createElement("style");
    s.id = "ss-dock-css";
    s.textContent = `
      .ss-row{margin:6px 0}
      .ss-actions{display:flex;gap:8px;margin:8px 0;flex-wrap:wrap}
      .ss-btn{all:unset;cursor:pointer;padding:6px 10px;border-radius:8px;
        border:1px solid rgba(255,255,255,.2);background:#252525}
      .ss-btn:hover{background:#2f2f2f}
    `;
    document.head.appendChild(s);
  }
  function ensureOverlayLoaded(){
    if (document.querySelector('script[data-ss-overlay="1"]')) return;
    const s = document.createElement("script");
    s.src = "/star-spirits-ui/inject.js";
    s.async = true;
    s.dataset.ssOverlay = "1";
    document.head.appendChild(s);
  }

  whenDock(function(){
    mountStyles();
    window.StateDock.registerTabProvider({
      id: "star-spirits",
      title: "Star Spirits",
      icon: "⭐",
      async mount(root){
        const project = detectProject();
        root.innerHTML = "";
        const body = document.createElement("div");
        body.style.fontSize = "12px";
        root.appendChild(body);

        async function render(){
          body.innerHTML = "";
          const who = await me(project);
          if (!who){
            body.textContent = "Sign in to track and collect star spirits.";
            return;
          }
          const currentUserId = who.user.id;
          const prog = await getProgress(project, currentUserId);

          const h = document.createElement("div");
          h.className = "ss-row";
          h.innerHTML = `<b>User:</b> ${who.user.email}`;
          body.appendChild(h);

          const chips = document.createElement("div");
          chips.className = "ss-row";
          const names = ["Eldstar","Mamar","Skolar","Muskular","Misstar","Klevar","Kalmar"];
          for (let i=1;i<=7;i++){
            const id = "s"+i;
            chips.appendChild(chip(`${names[i-1]} (${id})`, !!prog[id]));
          }
          body.appendChild(chips);

          const actions = document.createElement("div");
          actions.className = "ss-actions";
          const spawn = document.createElement("button");
          spawn.className = "ss-btn"; spawn.textContent = "Spawn remaining on page";
          spawn.onclick = ()=> ensureOverlayLoaded();
          const reset = document.createElement("button");
          reset.className = "ss-btn"; reset.textContent = "Reset progress";
          reset.onclick = async()=>{ 
            await resetProgress(project, currentUserId); 
            window.dispatchEvent(new Event("star-spirits:updated")); 
          };
          actions.append(spawn, reset);
          body.appendChild(actions);
        }

        // de-dupe listeners, then attach
        window.removeEventListener("gims:auth-changed", render);
        window.removeEventListener("star-spirits:updated", render);
        window.removeEventListener("star-spirits:completed", render);
        window.addEventListener("gims:auth-changed", render);
        window.addEventListener("star-spirits:updated", render);
        window.addEventListener("star-spirits:completed", render);

        await render();
      },
      onShow(){ /* no-op */ }
    });
  });
})();
"""

@router.get("/inject.js")
async def inject_js():
  return PlainTextResponse(INJECT_JS, media_type="application/javascript")

@router.get("/state-tab.js")
async def state_tab_js():
  return PlainTextResponse(STATE_TAB_JS, media_type="application/javascript")

star_ui_node = Node(
    name="Star Spirits – UI",
    kind=NodeKind.STATE,
    router=router,
    meta={
        "provides_inject": [
            "/star-spirits-ui/state-tab.js",
            "/star-spirits-ui/inject.js",
        ],
        "icon": "🟊",
        "label": "Star Spirits UI",
    },
)

ui_module = Module(
    name="Star Spirits UI",
    nodes=[star_ui_node],
    version="0.1.4",
    description="Pixel-art spirits overlay; StateDock tab; reduced-motion aware fireworks; idempotent spawn and cleanup.",
    roles=set(),
)
