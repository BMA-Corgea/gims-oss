from __future__ import annotations
from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from core.orchestration.node import Node, NodeKind
from core.orchestration.module import Module

router = APIRouter(prefix="/state-dock", tags=["State Dock"])

_DOCK_CSS = r"""
/* State Dock — Watery-themed top-right affordance with tabs (tokens + safe fallbacks,
   since this also renders on pages that have not adopted watery.css yet). On watery
   shell pages the dock is hidden (the header profile chip replaces it). */
#state-dock {
  position: fixed;
  top: 10px;
  right: 10px;
  z-index: 9999;
  font-family: 'Inter', system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, 'Helvetica Neue', Arial, sans-serif;
}
.sd-toggle {
  display: flex; align-items: center; justify-content: center;
  width: 36px; height: 36px; border-radius: 999px;
  border: 1px solid var(--card-edge, rgba(216,189,138,0.55));
  background: var(--card, rgba(17,54,42,0.88));
  backdrop-filter: blur(4px);
  cursor: pointer; font-size: 18px; color: var(--text, #e8f4ee);
  box-shadow: var(--shadow-md, 0 2px 8px rgba(2,18,14,0.45));
}
.sd-toggle:hover { border-color: var(--card-edge-strong, rgba(230,203,152,0.82)); }
.sd-panel {
  margin-top: 8px;
  width: min(360px, calc(100vw - 24px));
  background: var(--surface, #0e2a23); color: var(--text, #e8f4ee);
  border: 1px solid var(--card-edge, rgba(216,189,138,0.55));
  border-radius: var(--radius-lg, 14px);
  box-shadow: var(--shadow-lg, 0 10px 30px rgba(2,14,11,0.6));
  display: none; overflow: hidden;
}
.sd-panel.open { display: grid; grid-template-rows: auto 1fr; }
.sd-tabs {
  display: flex; gap: 6px; flex-wrap: nowrap; overflow-x: auto;
  padding: 8px; background: var(--bg2, #0a1f1a); border-bottom: 1px solid var(--border, rgba(140,230,200,0.14));
}
.sd-tab {
  flex: 0 0 auto; display: inline-flex; align-items: center; gap: 6px;
  padding: 6px 10px; border-radius: 999px; font-size: 12px;
  border: 1px solid transparent; cursor: pointer;
  background: var(--surface2, #143a30); color: var(--text-mid, #a6cabd);
}
.sd-tab:hover { background: var(--card-2, #173f2f); color: var(--text, #e8f4ee); }
.sd-tab.active { background: var(--blue-light, rgba(79,157,255,0.18)); color: var(--text, #fff); border-color: var(--blue-border, rgba(79,157,255,0.42)); }
.sd-tab .ico { font-size: 14px; }
.sd-content {
  padding: 10px 12px; min-height: 140px; overflow: auto;
}
.sd-empty { opacity: 0.6; font-size: 13px; padding: 10px; }
"""

_DOCK_JS = r"""
/* State Dock — dumb container. Tabs are provided by other scripts. */
(function(){
  if (window.StateDock) return; // singleton
  const el = document.createElement('div');
  el.id = 'state-dock';
  document.addEventListener('DOMContentLoaded', () => document.body.appendChild(el));

  // create shell
  function buildShell(){
    el.innerHTML = '';
    const toggle = h('button', {class:'sd-toggle', title:'Open State Dock'}, [txt('🧩')]);
    const panel  = h('div', {class:'sd-panel'});
    const tabs   = h('div', {class:'sd-tabs'});
    const content= h('div', {class:'sd-content'});
    panel.appendChild(tabs); panel.appendChild(content);
    el.appendChild(toggle); el.appendChild(panel);

    toggle.addEventListener('click', ()=>panel.classList.toggle('open'));

    return {toggle, panel, tabs, content};
  }

  // tiny helpers
  const h = (tag, attrs={}, children=[])=>{
    const n = document.createElement(tag);
    for (const [k,v] of Object.entries(attrs||{})){
      if (k==='class') n.className=v;
      else if (k==='html') n.innerHTML=v;
      else if (k==='dataset') Object.assign(n.dataset, v);
      else if (v!=null) n.setAttribute(k,v);
    }
    (Array.isArray(children)?children:[children]).forEach(c=>{
      if (c==null) return;
      if (typeof c==='string') n.appendChild(document.createTextNode(c));
      else n.appendChild(c);
    });
    return n;
  };
  const txt = (s)=>document.createTextNode(s);

  const shell = buildShell();
  const providers = new Map(); // id -> provider
  let activeId = null;
  const ctx = {
    getProject(){
      const u = new URL(location.href);
      const m = u.pathname.match(/\/(LIMS-System|projects|api|gui|state|archive[^\/]*)\/([^\/]+)/i);
      if (m) return decodeURIComponent(m[2]);
      if (u.searchParams.get('project')) return u.searchParams.get('project');
      return null;
    }
  };

  function ensureTabUI(id){
    const p = providers.get(id);
    if (!p) return;
    if (!p._tabBtn){
      p._tabBtn = h('button', {class:'sd-tab', title: p.title||p.id}, [
        h('span', {class:'ico'}, [txt(p.icon || '📄')]),
        h('span', {class:'lbl'}, [txt(p.title || p.id)])
      ]);
      p._tabBtn.addEventListener('click', ()=>activate(id));
      shell.tabs.appendChild(p._tabBtn);
    }
    if (!p._mountRoot){
      p._mountRoot = h('div', {class:'sd-view', dataset:{id}});
      p._mounted = false;
    }
  }

  async function activate(id){
    const p = providers.get(id);
    if (!p) return;
    providers.forEach(q=>q._tabBtn && q._tabBtn.classList.toggle('active', q===p));
    shell.content.innerHTML = '';
    ensureTabUI(id);
    shell.content.appendChild(p._mountRoot);

    if (!p._mounted){
      p._mounted = true;
      if (typeof p.mount === 'function'){
        try { await p.mount(p._mountRoot, ctx); } catch(e){ console.error('[StateDock] mount error', e); }
      }
    }
    if (typeof p.onShow === 'function'){
      try { await p.onShow(p._mountRoot, ctx); } catch(e){ console.error('[StateDock] onShow error', e); }
    }
    activeId = id;
  }

  function openDock(){
    shell.panel.classList.add('open');
    if (!activeId){
      const first = providers.keys().next();
      if (!first.done) activate(first.value);
    }
  }

  // Public API for providers
  window.StateDock = {
    registerTabProvider(provider){
      if (!provider || !provider.id) { console.warn('[StateDock] invalid provider'); return; }
      if (providers.has(provider.id)) { console.warn('[StateDock] duplicate id:', provider.id); return; }
      providers.set(provider.id, provider);
      ensureTabUI(provider.id);
    },
    open: openDock,
    activate: activate
  };

  // auto-load CSS (served by the node)
  (function injectCSS(){
    const id = "state-dock-css";
    if (document.getElementById(id)) return;
    const link = document.createElement('link');
    link.id = id; link.rel = "stylesheet";
    link.href = "/state-dock/dock.css";
    document.head.appendChild(link);
  })();

})();
"""

# NEW: tiny "participation marker" so a module can opt-in without any other scripts
_PARTICIPATE_JS = r"""
// State Dock participation marker.
// Including this in Module.inject marks the module as participating for this path.
// The registry will then add /state-dock/inject.js (via node provides_inject).
window.__StateDockParticipating = true;
"""

@router.get("/dock.css")
def dock_css():
    return PlainTextResponse(_DOCK_CSS, media_type="text/css")

@router.get("/inject.js")
def inject_js():
    return PlainTextResponse(_DOCK_JS, media_type="application/javascript")

@router.get("/participate.js")
def participate_js():
    return PlainTextResponse(_PARTICIPATE_JS, media_type="application/javascript")

state_dock_node = Node(
    name="State Dock",
    kind=NodeKind.INFRASTRUCTURE,
    router=router,
    meta={
        "label": "State Dock",
        "icon": "🧩",
        # The real UI script is provided here; registry will add it when the module participates.
        "provides_inject": ["/state-dock/inject.js"],
    },
)

state_dock_module = Module(
    name="State Dock Module",
    nodes=[state_dock_node],
    version="0.1.1",
    description="Top-right dock UI; providers register tabs at runtime. No DB.",
    roles=set(),
)
