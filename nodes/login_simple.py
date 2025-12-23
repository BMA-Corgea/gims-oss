# nodes/login_simple.py
from __future__ import annotations

from fastapi import APIRouter, Request, Form
from fastapi.responses import JSONResponse, PlainTextResponse

from core.orchestration.node import Node, NodeKind

router = APIRouter()

# ── Define all feature tags (keep in one place) ────────────────────────────
ALL_TAGS = [
    "noun-configure",
    "adjective-editor",
    "verb-editor",
    "adverb-editor",
    "conjunction-manager",
    "investigation",
    "deep-search",
    "audit",
    "archive-workbench",
    "runlog-workbench",
    "noun-workbench",
    "verb-workbench",
    "camera",
    "custom-parsers",
    "prepositional-phrase-runner",
    "template-manager",
    "grid-test",
    "parser-test",
]

# Map role -> allowed tag list. Arbitrary role names supported.
ROLE_TAGS = {
    "admin":  ALL_TAGS,                                     # full access
    "admin2": [t for t in ALL_TAGS if t != "grid-test"],    # everything except Grid Test
    # add more roles here later (e.g., "qc": [...], "viewer": [...])
}

# ── Hardcoded users ────────────────────────────────────────────────────────
USERS = {
    "bob":   {"password": "123", "roles": ["admin"]},
    "alice": {"password": "abc", "roles": ["admin2"]},
}

def expand_tags(roles: list[str]) -> list[str]:
    allowed = set()
    for r in roles or []:
        for tag in ROLE_TAGS.get(r, []):
            allowed.add(tag)
    return sorted(allowed)

# ── Minimal session API (owned by this node) ───────────────────────────────
@router.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    user = USERS.get(username)
    if not user or user["password"] != password:
        return JSONResponse({"ok": False, "error": "Invalid credentials"}, status_code=401)
    roles = user["roles"]
    tags = expand_tags(roles)
    session = {"user": username, "roles": roles, "tags": tags}
    request.app.state.session = session
    return {"ok": True, **session}

@router.post("/logout")
async def logout(request: Request):
    request.app.state.session = None
    return {"ok": True}

@router.get("/api/session")
async def get_session(request: Request):
    session = getattr(request.app.state, "session", None)
    if not session:
        return {"user": None, "roles": [], "tags": []}
    return session

# ── Injected JS: gates buttons by data-tag (not by role names) ─────────────
INJECT_JS = r"""
/* login_simple inject.js (tag-gated) */
(function register() {
  function mount(api) {
    // Styles, auth bar, and modal UI
    api.addStyles(`
      .auth-bar{position:fixed;top:10px;left:12px;z-index:1000;display:flex;gap:8px;
        background:rgba(20,27,44,.75);color:#e8eefb;border:1px solid #2b3658;border-radius:12px;
        padding:6px 10px;backdrop-filter:blur(6px);}
      .auth-bar button{all:unset;cursor:pointer;padding:4px 8px;border:1px solid #2b3658;border-radius:8px}
      .auth-modal-backdrop{position:fixed;inset:0;background:rgba(0,0,0,.45);display:none;align-items:center;justify-content:center;z-index:1100}
      .auth-modal{width:360px;max-width:92vw;background:#0e1426;color:#e8eefb;border:1px solid #2b3658;border-radius:16px;padding:16px;
        box-shadow:0 10px 30px rgba(0,0,0,.35)}
      .auth-modal h3{margin:0 0 8px;font-size:18px}
      .auth-modal .row{display:grid;gap:6px;margin-top:10px}
      .auth-modal input{background:#111731;color:#e8eefb;border:1px solid #2b3658;border-radius:10px;padding:8px 10px;outline:none}
      .auth-modal .actions{display:flex;gap:8px;margin-top:14px;justify-content:flex-end}
      .auth-modal .actions button{all:unset;cursor:pointer;padding:8px 12px;border-radius:10px;border:1px solid #2b3658}
    `);

    if (!document.querySelector(".auth-bar")) {
      const bar = document.createElement("div");
      bar.className = "auth-bar";
      bar.innerHTML = `
        <span id="auth-user">Not signed in</span>
        <button id="auth-login">Sign in</button>
        <button id="auth-logout" style="display:none">Sign out</button>
      `;
      document.body.appendChild(bar);
      document.getElementById("auth-login").addEventListener("click", openModal);
      document.getElementById("auth-logout").addEventListener("click", async () => {
        await fetch("/logout", { method: "POST" });
        await refreshSession();
      });
    }

    if (!document.querySelector(".auth-modal-backdrop")) {
      const backdrop = document.createElement("div");
      backdrop.className = "auth-modal-backdrop";
      backdrop.innerHTML = `
        <div class="auth-modal">
          <h3>Sign in</h3>
          <p style="margin:4px 0 10px;color:#92a3c8">
            Use <code>bob/123</code> (admin) or <code>alice/abc</code> (admin2)
          </p>
          <div class="row"><label>Username</label><input id="auth-u" placeholder="bob"/></div>
          <div class="row"><label>Password</label><input id="auth-p" type="password" placeholder="•••"/></div>
          <div class="actions">
            <button id="auth-cancel">Cancel</button>
            <button id="auth-submit">Sign in</button>
          </div>
        </div>
      `;
      document.body.appendChild(backdrop);
      backdrop.addEventListener("click",(e)=>{ if(e.target===backdrop) closeModal(); });
      document.getElementById("auth-cancel").addEventListener("click", closeModal);
      document.getElementById("auth-submit").addEventListener("click", doLogin);
    }

    function openModal(){ document.querySelector(".auth-modal-backdrop").style.display = "flex"; }
    function closeModal(){ document.querySelector(".auth-modal-backdrop").style.display = "none"; }

    async function doLogin() {
      const u = document.getElementById("auth-u").value || "";
      const p = document.getElementById("auth-p").value || "";
      const form = new FormData();
      form.append("username", u);
      form.append("password", p);
      try {
        const res = await fetch("/login", { method: "POST", body: form });
        const data = await res.json();
        if (data && data.ok) {
          closeModal();
          await refreshSession();
        } else {
          alert((data && data.error) || "Login failed");
        }
      } catch {
        alert("Network error");
      }
    }

    function applyTagGates(tags) {
      const allowed = new Set(Array.isArray(tags) ? tags : []);
      document.querySelectorAll(".launcher-button").forEach(btn => {
        const tag = btn.getAttribute("data-tag");
        const ok = !tag || allowed.has(tag);
        if (ok) {
        btn.style.display = "";   // restore normal
        } else {
        btn.style.display = "none"; // hide completely
        }
      });
    }

    async function refreshSession() {
      const res = await fetch("/api/session", { credentials: "include" });
      const data = await res.json();
      const user = data?.user || null;
      const tags = Array.isArray(data?.tags) ? data.tags : [];
      const badge = document.getElementById("auth-user");
      const loginBtn = document.getElementById("auth-login");
      const logoutBtn = document.getElementById("auth-logout");
      if (user) {
        const roles = Array.isArray(data?.roles) ? ` (${data.roles.join(", ")})` : "";
        badge.textContent = `${user}${roles}`;
        loginBtn.style.display = "none";
        logoutBtn.style.display = "inline-block";
      } else {
        badge.textContent = "Not signed in";
        loginBtn.style.display = "inline-block";
        logoutBtn.style.display = "none";
      }
      applyTagGates(tags);
      // block clicks on gated buttons
      api.onNavigate(({ event, el }) => {
        if (el.classList.contains("is-gated")) {
          event.preventDefault();
          return false;
        }
      });
    }

    // initial session check
    refreshSession();
  }

  // wait for launcher API, then register
  (function wait() {
    if (window.GIMS && window.GIMS.launcher && typeof window.GIMS.launcher.use === "function") {
      window.GIMS.launcher.use(mount);
    } else {
      setTimeout(wait, 30);
    }
  })();
})();
"""

@router.get("/login/inject.js")
async def inject_js():
    return PlainTextResponse(INJECT_JS, media_type="application/javascript")

login_node = Node(
    name="Simple Login",
    kind=NodeKind.LOGIN,
    router=router,
    meta={"entry_path": "/login/inject.js", "icon": "🔐", "label": "Login"},
)
