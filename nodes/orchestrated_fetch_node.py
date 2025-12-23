# nodes/orchestrated_fetch_node.py
# -----------------------------------------------------------------------------
# Orchestrated Fetch Node
#
# Client:
#   - /orchestrate/inject.js installs a small fetch wrapper on every page.
#   - It does a PAGE PREFLIGHT via /orchestrate (__only_guard) to run PRE hooks.
#   - It also includes __page_path=location.pathname in every envelope so that
#     the server can preflight the current page before forwarding ANY API call.
#
# Server:
#   - POST /orchestrate:
#       * Runs PRE hooks (and a page preflight if __page_path present)
#       * Optionally short-circuits for __only_guard (no proxy)
#       * Proxies the original request to same-origin (unless __only_log/guard)
#       * Runs POST hooks and returns filtered/unchanged response
# -----------------------------------------------------------------------------

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple
import json
import re
from fastapi import APIRouter, HTTPException, Request
from starlette.responses import Response, JSONResponse
import httpx

from core.orchestration.node import Node, NodeKind
from core.orchestration import triggers

DEBUG_ENABLED = False

def debug(*args, **kwargs):
    if DEBUG_ENABLED:
        print("[orchestrated_fetch_node]", *args, **kwargs, flush=True)

router = APIRouter()

INJECT_PATH = "/orchestrate/inject.js"

# Client-side shim: wraps fetch, adds page preflight guard, sidecar logging, and trigger handling
INJECT_JS = r"""
(() => {
  if (window.__orchestratedFetchInstalled) return;
  window.__orchestratedFetchInstalled = true;

  const __origFetch = window.fetch.bind(window);

  (function primeTagsAndRoles(){
    if (!window.__featureTags) {
      try {
        const cached = localStorage.getItem("gims_feature_tags");
        if (cached) {
          const tags = JSON.parse(cached);
          if (Array.isArray(tags)) window.__featureTags = tags;
        }
      } catch {}
    }
    if (typeof window.__accountRoles === "undefined") {
      try {
        const roles = localStorage.getItem("gims_account_roles");
        if (roles && typeof roles === "string") window.__accountRoles = roles;
      } catch {}
    }
  })();

  function appendTags(h) {
    try {
      if (Array.isArray(window.__featureTags) && window.__featureTags.length) {
        h.set("X-Feature-Tags", window.__featureTags.join(","));
      }
    } catch {}
  }

  function appendRoles(h) {
    try {
      if (typeof window.__accountRoles === "string" && window.__accountRoles.trim().length) {
        h.set("X-Account-Roles", window.__accountRoles.trim());
      }
    } catch {}
  }

  // ---------- PAGE GUARD ----------
  function shouldGuard(pathname) {
    if (!pathname || pathname === "/") return false;
    if (pathname.startsWith("/orchestrate")) return false;
    if (pathname.startsWith("/login/")) return false;
    if (pathname.startsWith("/static/")) return false;
    if (pathname.startsWith("/schema/")) return false;
    if (pathname.includes("/events") || pathname.includes("/stream")) return false;
    if (/\.[a-z0-9]+$/i.test(pathname)) return false;
    // Guard any top-level page like "/template", "/backup", "/audit", etc.
    return /^\/[a-z0-9_-]+(?:\/|$)/i.test(pathname);
  }

  function overlayAccessDenied(pathname, msg) {
    const message = msg || ("You don’t have permission to view " + pathname);
    document.documentElement.innerHTML = `
      <head>
        <meta charset="utf-8">
        <title>Access denied</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
          body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;background:#0b1020;color:#e6e8ee}
          .card{max-width:620px;background:#121833;border:1px solid #253059;border-radius:16px;padding:28px;box-shadow:0 6px 30px rgba(2,8,23,.45)}
          h1{font-size:20px;margin:0 0 10px}
          p{line-height:1.55;margin:0 0 14px}
          code{background:#0b1020;padding:2px 6px;border-radius:6px;color:#a9b6ff}
          .hint{opacity:.8;font-size:14px}
        </style>
      </head>
      <body>
        <div class="card">
          <h1>Access denied</h1>
          <p>You don’t have permission to view <code>${pathname}</code> with your current role.</p>
          <p class="hint">${message}</p>
        </div>
      </body>`;
  }

  async function guardPath(pathname) {
    try {
      const headers = new Headers({ "Content-Type": "application/json", "X-Orch-Client": "1" });
      appendTags(headers);
      appendRoles(headers);
      // 🔑 Forward JWT from localStorage so /auth/me works on bare page loads
      try {
        const token = localStorage.getItem("gims_token");
        if (token) headers.set("Authorization", "Bearer " + token);
      } catch {}
      const resp = await __origFetch("/orchestrate", {
        method: "POST",
        headers,
        credentials: "include",
        body: JSON.stringify({ method: "GET", path: pathname, payload: null, __only_guard: true })
      });
      if (resp.status === 403) {
        let reason = "Access denied.";
        try {
          const j = await resp.json();
          if (j && j.detail) reason = j.detail;
        } catch {}
        overlayAccessDenied(pathname, reason);
        return false;
      }
      return true;
    } catch (e) {
      console.warn("[orchestratedFetch] guard failed (allowing by default):", e);
      return true;
    }
  }

  (function installGuard(){
    const path = location.pathname;
    if (shouldGuard(path)) {
      // Fire ASAP; no need to wait for DOMContentLoaded
      guardPath(path);
    }
    // SPA navigations
    const _ps = history.pushState.bind(history);
    const _rs = history.replaceState.bind(history);
    function wrapNav(fn){
      return function(...args){
        const ret = fn(...args);
        const p = location.pathname;
        if (shouldGuard(p)) guardPath(p);
        return ret;
      }
    }
    history.pushState = wrapNav(_ps);
    history.replaceState = wrapNav(_rs);
    window.addEventListener("popstate", () => {
      const p = location.pathname;
      if (shouldGuard(p)) guardPath(p);
    });
  })();
  // ---------- /PAGE GUARD ----------

  function isGateCompletePath(p) {
    try {
      return /\/runlog\/[^/]+\/[^/]+\/[^/]+\/gate\/[^/]+\/complete(?:$|\?)/i.test(p);
    } catch { return false; }
  }

  function isCustomUploadAdminPath(p) {
    return /^\/custom_upload\/[^/]+\/(assign|unassign)\b/i.test(p);
  }

  function shouldIntercept(url, options) {
    try {
      const u = new URL(url, location.origin);
      if (u.origin !== location.origin) return false;
      const p = u.pathname;
      const method = (options?.method || "GET").toUpperCase();

      // Exclusions
      if (p.startsWith("/orchestrate")) return false;
      if (p.startsWith("/login/")) return false;
      if (p.startsWith("/static/")) return false;
      if (p.endsWith(".js") || p.endsWith(".css") || p.endsWith(".ico")) return false;
      if (p.includes("/events") || p.includes("/stream")) return false;

      // Always intercept these
      if (isGateCompletePath(p)) return true;
      if (isCustomUploadAdminPath(p) && (method === "POST" || method === "DELETE")) return true;

      // Sidecar logging for FormData uploads
      const body = options?.body;
      const isForm = (typeof FormData !== "undefined") && (body instanceof FormData);
      if (isForm) return true;

      // Intercept ALL mutating methods
      if (["POST","PUT","PATCH","DELETE"].includes(method)) return true;

      // Fallback: intercept GET and JSON requests
      const ct = (options?.headers && new Headers(options.headers).get("Content-Type")) || "";
      if (method === "GET" || ct.includes("application/json")) return true;

      return false;
    } catch {
      return false;
    }
  }

  function serializeFormData(fd) {
    const out = {};
    try {
      for (const [k, v] of fd.entries()) {
        if (v instanceof File) {
          out[k] = { __file: true, name: v.name || "blob", type: v.type || "", size: (typeof v.size === "number" ? v.size : undefined) };
        } else {
          if (Object.prototype.hasOwnProperty.call(out, k)) {
            const cur = out[k];
            if (Array.isArray(cur)) cur.push(v); else out[k] = [cur, v];
          } else {
            out[k] = v;
          }
        }
      }
    } catch {}
    return out;
  }

  async function readJsonBody(options) {
    if (!options || !options.body) return undefined;
    const body = options.body;
    if (typeof body === "string") {
      try { return JSON.parse(body); } catch { return body; }
    }
    return undefined;
  }

  async function handleOrchResponse(resp) {
    try {
      const status = resp.status || 0;
      const ct = (resp.headers && resp.headers.get("content-type")) || "";

      if (status === 202 && (ct.includes("application/vnd.orch+json") || ct.includes("application/json"))) {
        let data = null;
        try { data = await resp.clone().json(); } catch {}
        const trig = data && (data.trigger || data.__js_trigger);
        if (trig && typeof trig === "object") {
          window.dispatchEvent(new CustomEvent("orch:trigger", { detail: trig }));
          return new Response(JSON.stringify({ handled: true, trigger: trig.type || "unknown" }), {
            status: 202, headers: { "content-type": "application/json" }
          });
        }
      }

      if (status === 202 && (ct.includes("application/javascript") || ct.includes("text/javascript"))) {
        const jsCode = await resp.text();
        try {
          const s = document.createElement("script");
          s.textContent = jsCode;
          document.documentElement.appendChild(s);
          s.remove();
        } catch (e) {
          console.error("[orchestratedFetch] executing 202 JS failed:", e);
        }
        return new Response(JSON.stringify({ handled: true, executed: true }), {
          status: 202, headers: { "content-type": "application/json" }
        });
      }
    } catch (e) {
      console.error("[orchestratedFetch] handleOrchResponse error:", e);
    }
    return resp;
  }

  async function orchestratedFetch(input, init) {
    const url = (input instanceof Request) ? input.url : String(input);
    const options = (input instanceof Request) ? {
      method: input.method,
      headers: input.headers,
      body: input._bodyInit || null,
      credentials: input.credentials,
      mode: input.mode,
      cache: input.cache,
      redirect: input.redirect,
      referrer: input.referrer,
      referrerPolicy: input.referrerPolicy,
      integrity: input.integrity,
      keepalive: input.keepalive,
      signal: input.signal
    } : (init || {});

    if (!shouldIntercept(url, options)) {
      return __origFetch(input, init);
    }

    const u = new URL(url, location.origin);
    const method = (options.method || "GET").toUpperCase();
    const headers = new Headers({ "Content-Type": "application/json", "X-Orch-Client": "1" });
    appendTags(headers);
    appendRoles(headers);
    // 🔑 Forward JWT from localStorage so /auth/me works on all orchestrated calls
    try {
      const token = localStorage.getItem("gims_token");
      if (token) headers.set("Authorization", "Bearer " + token);
    } catch {}

    // Detect FormData uploads and send a SIDE-CAR logging envelope.
    const body = options?.body;
    const isForm = (typeof FormData !== "undefined") && (body instanceof FormData);
    if (isForm) {
      const payload = serializeFormData(body);
      const envelope = { method, path: u.pathname + u.search, payload, __only_log: true, __page_path: location.pathname };
      try {
        await __origFetch("/orchestrate", {
          method: "POST", headers, body: JSON.stringify(envelope),
          credentials: "include", signal: options.signal
        });
      } catch (e) { console.warn("[orchestratedFetch] sidecar log failed:", e); }
      return __origFetch(input, init);
    }

    const payload = (method === "GET") ? undefined : await readJsonBody(options);
    const envelope = {
      method,
      path: u.pathname + u.search,
      payload: (payload === undefined ? null : payload),
      __page_path: location.pathname
    };

    const resp = await __origFetch("/orchestrate", {
      method: "POST",
      headers,
      body: JSON.stringify(envelope),
      credentials: "include",
      signal: options.signal
    });

    return handleOrchResponse(resp);
  }

  Object.defineProperty(window, "fetch", {
    configurable: false,
    enumerable: false,
    writable: false,
    value: orchestratedFetch
  });

  console.debug("[orchestratedFetch] installed: page preflight guard + API orchestration active");
})();
"""

@router.get(INJECT_PATH)
def serve_inject_js() -> Response:
    debug("serve_inject_js: begin")
    resp = Response(content=INJECT_JS, media_type="application/javascript")
    debug("serve_inject_js: returning script", {"bytes": len(INJECT_JS)})
    return resp

# Only call rules hooks; no chain hooks (avoids 404 spam)
HOOKS_PRE = ("/orchestrate/rules/pre",)
HOOKS_POST = ("/orchestrate/rules/post",)

_GATE_COMPLETE_RE = re.compile(
    r"/runlog/[^/]+/[^/]+/[^/]+/gate/[^/]+/complete(?:\?.*)?$", re.IGNORECASE
)

async def _call_hook_json(
    client: httpx.AsyncClient,
    url: str,
    headers: Dict[str, str],
    payload: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], Optional[int]]:
    try:
        r = await client.post(url, headers=headers, json=payload)
    except Exception as e:
        debug("hook call error", url, repr(e)); return None, None
    if r.status_code in (404, 405):
        debug("hook not present", url, r.status_code); return None, r.status_code
    ctype = (r.headers.get("content-type") or "")
    if "application/json" not in ctype.lower():
        debug("hook non-json response", url, ctype); return None, r.status_code
    try:
        return r.json(), r.status_code
    except Exception:
        debug("hook invalid json", url); return None, r.status_code

def _maybe_parse_json_body(content: bytes, content_type: str) -> Tuple[Optional[Any], bool]:
    is_json = "application/json" in (content_type or "").lower()
    if not is_json: return None, False
    try:
        return json.loads(content.decode("utf-8")), True
    except Exception:
        return None, True

def _extract_trigger(obj: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not obj or not isinstance(obj, dict):
        return None
    if "__js_trigger" in obj and isinstance(obj["__js_trigger"], dict):
        return obj["__js_trigger"]
    if "trigger" in obj and isinstance(obj["trigger"], dict):
        return obj["trigger"]
    return None

@router.post("/orchestrate")
async def orchestrate(request: Request) -> Response:
    debug("orchestrate: begin")
    try:
        data = await request.json()
        path_dbg = (data.get("path") or "")
        if _GATE_COMPLETE_RE.search(path_dbg):
            debug("orchestrate: envelope (gate)", data)
            debug("orchestrate: headers (gate)", {
                "X-Feature-Tags": request.headers.get("X-Feature-Tags", ""),
                "X-Account-Roles": request.headers.get("X-Account-Roles", "")
            })
        else:
            debug("orchestrate: envelope parsed", "{hidden}")
    except Exception as e:
        debug("orchestrate: invalid JSON envelope", repr(e))
        raise HTTPException(status_code=400, detail="Invalid JSON envelope")

    method: str = (data.get("method") or "GET").upper()
    path: str = data.get("path") or "/"
    payload: Optional[Dict[str, Any]] = data.get("payload")
    only_log: bool = bool(data.get("__only_log"))
    only_guard: bool = bool(data.get("__only_guard"))
    page_path: Optional[str] = data.get("__page_path")

    if not path.startswith("/"):
        raise HTTPException(status_code=400, detail="Envelope 'path' must be absolute")
    if method not in ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"):
        raise HTTPException(status_code=405, detail=f"Method {method} not allowed")

    base = f"{request.url.scheme}://{request.url.netloc}"
    target = f"{base}{path}"

    fwd_headers = {
        "X-Orch-Client": request.headers.get("X-Orch-Client", "1"),
        "X-Feature-Tags": request.headers.get("X-Feature-Tags", ""),
    }
    roles_hdr = request.headers.get("X-Account-Roles")
    if roles_hdr:
        fwd_headers["X-Account-Roles"] = roles_hdr

    cookie = request.headers.get("cookie")
    if cookie: fwd_headers["cookie"] = cookie
    auth = request.headers.get("authorization")
    if auth: fwd_headers["authorization"] = auth

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=120.0) as client:  # 2 minutes for archive ops
            # PAGE PREFLIGHT (server-side): if client sent __page_path, check it first
            if page_path and isinstance(page_path, str) and page_path.startswith("/"):
                pre_guard_env = {"method": "GET", "path": page_path, "payload": None}
                deny_reason = None
                denied = False
                for hook in HOOKS_PRE:
                    hook_url = f"{base}{hook}"
                    hook_json, status = await _call_hook_json(client, hook_url, fwd_headers, pre_guard_env)
                    if hook_json is None:
                        continue
                    effect = (hook_json.get("effect") or "allow").lower()
                    if effect == "deny" or (status and status == 403):
                        denied = True
                        deny_reason = hook_json.get("reason") or "Denied by page preflight."
                        break
                if denied:
                    return JSONResponse({"detail": deny_reason}, status_code=403)

            # PRE phase for the actual API call
            pre_env = {"method": method, "path": path, "payload": payload}
            await triggers.publish_chain_pre(pre_env, request)

            collected_trigger = _extract_trigger(pre_env)

            deny_reason = None
            denied = False
            for hook in HOOKS_PRE:
                hook_url = f"{base}{hook}"
                hook_json, status = await _call_hook_json(client, hook_url, fwd_headers, pre_env)
                if hook_json is None:
                    continue

                hook_trigger = _extract_trigger(hook_json)
                if hook_trigger:
                    collected_trigger = hook_trigger
                    continue

                effect = (hook_json.get("effect") or "allow").lower()
                if effect == "deny" or (status and status == 403):
                    denied = True
                    deny_reason = hook_json.get("reason") or "Denied by pre-hook."
                    break

                if effect == "mutate":
                    payload = hook_json.get("payload", payload)
                    pre_env["payload"] = payload
                    if _GATE_COMPLETE_RE.search(path):
                        debug("orchestrate: PRE hook mutated payload for gate call", {"hook": hook})

            # 🔑 Extra call: explicitly hit login_rules_node on gate POSTs
            if method == "POST" and _GATE_COMPLETE_RE.search(path):
                login_hook_url = f"{base}/login-rules/orchestrate/rules/pre"
                hook_json, status = await _call_hook_json(client, login_hook_url, fwd_headers, pre_env)
                if hook_json:
                    effect = (hook_json.get("effect") or "allow").lower()
                    if effect == "deny" or (status and status == 403):
                        denied = True
                        deny_reason = hook_json.get("reason") or "Denied by login-rules pre-hook."
                    elif effect == "mutate":
                        payload = hook_json.get("payload", payload)
                        pre_env["payload"] = payload
                        debug("orchestrate: login-rules PRE hook mutated payload for gate call")

            # 🔔 Handle compliance-triggered signature requests (from pre_env)
            if collected_trigger:
                debug("orchestrate: collected trigger detected", collected_trigger)
                return JSONResponse(
                    {"trigger": collected_trigger},
                    status_code=202,
                    media_type="application/vnd.orch+json"
                )

            # Guard mode: only run hooks, don't forward the original request.
            if only_guard:
                if denied:
                    return JSONResponse({"detail": deny_reason}, status_code=403)
                if collected_trigger:
                    return JSONResponse({"trigger": collected_trigger},
                                        status_code=202,
                                        media_type="application/vnd.orch+json")
                return Response(status_code=204)

            if denied:
                return JSONResponse({"detail": deny_reason}, status_code=403)

            # SIDE-CAR logging: DO NOT FORWARD the request.
            if only_log:
                post_env = {"path": path, "status": 200, "body": None, "method": method}
                await triggers.publish_chain_post(post_env, request)
                filtered_body = None
                for hook in HOOKS_POST:
                    hook_url = f"{base}{hook}"
                    hook_json, _ = await _call_hook_json(client, hook_url, fwd_headers, post_env)
                    if hook_json is None:
                        continue
                    if "body" in hook_json:
                        filtered_body = hook_json["body"]
                        post_env["body"] = filtered_body
                return JSONResponse({"ok": True, "logged": True}, status_code=202)

            # Forward to the original target
            if method in ("GET", "HEAD"):
                ds = await client.request(method, target, headers=fwd_headers)
            else:
                if payload is None:
                    ds = await client.request(method, target, headers=fwd_headers)
                else:
                    fwd_headers["Content-Type"] = "application/json"
                    ds = await client.request(method, target, headers=fwd_headers, json=payload)

            # POST phase
            ct = ds.headers.get("content-type", "")
            orig_body_json, is_json_type = _maybe_parse_json_body(ds.content, ct)
            post_env = {"path": path, "status": ds.status_code, "body": orig_body_json, "method": method}
            await triggers.publish_chain_post(post_env, request)

            filtered_body = orig_body_json
            for hook in HOOKS_POST:
                hook_url = f"{base}{hook}"
                hook_json, _ = await _call_hook_json(client, hook_url, fwd_headers, post_env)
                if hook_json is None:
                    continue
                if "body" in hook_json:
                    filtered_body = hook_json["body"]
                    post_env["body"] = filtered_body
                    if _GATE_COMPLETE_RE.search(path):
                        debug("orchestrate: POST hook filtered body for gate call", {"hook": hook})

            headers_out = {}
            for k in ("content-type", "etag", "cache-control"):
                if k in ds.headers:
                    headers_out[k] = ds.headers[k]

            if is_json_type and filtered_body is not None:
                body_bytes = json.dumps(filtered_body).encode("utf-8")
                headers_out["content-type"] = "application/json; charset=utf-8"
                if _GATE_COMPLETE_RE.search(path):
                    debug("orchestrate: returning JSON for gate call", {"status": ds.status_code})
                return Response(content=body_bytes, status_code=ds.status_code, headers=headers_out)

            if _GATE_COMPLETE_RE.search(path):
                debug("orchestrate: returning raw body for gate call", {"status": ds.status_code})
            return Response(content=ds.content, status_code=ds.status_code, headers=headers_out)

    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Orchestrate forward error: {e!s}")

orchestrated_fetch_node = Node(
    name="Orchestrated Fetch",
    kind=NodeKind.INFRASTRUCTURE,
    router=router,
    route_prefix="",
    meta={"debug": True}
)