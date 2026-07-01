"""Factory for the ~20 near-identical UI page nodes (R21).

Every ``nodes/pages/*.py`` repeated the same ``_resolve_html`` + ``_tags_for`` + page handler + ``Node``
(~70 lines each, ~1400 total). :func:`make_page_node` captures that one pattern so a page node is
declared in a few lines — one place to fix the HTML-resolution / asset-injection logic, not 20.

Tier-0 front-end refactor: page nodes can opt into the shared **app-shell** (``shell=True``). The
factory then wraps a *content-only* fragment (``gui/components/<file>``) in the Watery app-shell —
section rail (workspace nav) + header (title + profile chip) + first-class login card + content slot
+ toasts — and links ``watery.css`` + ``shell.css`` + the ``gims.js`` toolkit. Pages keep only their
content; the chrome lives here, in one place. Legacy pages stay ``shell=False`` (full standalone docs).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, PlainTextResponse

from core.orchestration.node import Node, NodeKind
from core.orchestration.registry import registry
from core.orchestration.theming import theme_head_tags, theme_switcher_html


def _inject_theme(html: str) -> str:
    """Wire the modular theme system into a STANDALONE page (non-shell, e.g. the launcher):
    splice the pre-paint script + alternate-theme links before </head>, and drop the header
    skin switcher wherever the page left a ``<!--gims:theme-switch-->`` marker. Shell pages get
    the same wiring from :func:`_shell_html` instead."""
    if "</head>" in html:
        html = html.replace("</head>", f"  {theme_head_tags()}\n</head>", 1)
    return html.replace("<!--gims:theme-switch-->", theme_switcher_html())


def _resolve_html(html_file: str) -> Optional[Path]:
    """Locate ``gui/components/<html_file>`` (CWD-relative or anchored at the repo root)."""
    from utils.paths import repo_root  # lazy: avoid import cost at module load
    for p in (Path(f"gui/components/{html_file}"), repo_root() / "gui" / "components" / html_file):
        if p.exists():
            return p
    return None


def _inject_tags(target: str) -> str:
    """Registry-provided <link>/<script> tags for this page (login shims, app assets, …)."""
    inj = registry.gather_injections(target)
    tags = [f'<link rel="stylesheet" href="{h}">' for h in inj.get("stylesheets", [])]
    tags += [f'<script src="{s}"></script>' for s in inj.get("scripts", [])]
    return "\n".join(tags)


# ── App-shell: the reusable chrome every shell page renders inside ───────────────
# (nav_key, route, label, icon-id, feature-tag) — the rail mirrors the launcher's
# sections; feature-tags drive role-gating via /login/inject.js (hides links the
# user can't access); nav_key matches body[data-page] for the active highlight.
WORKSPACE_NAV: list[tuple[str, list[tuple[str, str, str, str, str]]]] = [
    ("Schemas", [
        ("noun_configure", "/noun_configure", "Noun Configure", "i-noun", "noun-configure"),
        ("adjective_editor", "/adjective_editor", "Adjective Editor", "i-adjective", "adjective-editor"),
        ("verb_editor", "/verb_editor", "Verb Editor", "i-verb", "verb-editor"),
        ("adverb_editor", "/adverb_editor", "Adverb Editor", "i-adverb", "adverb-editor"),
        ("conjunction_editor", "/conjunction_editor", "Conjunction Editor", "i-conjunction", "conjunction-editor"),
    ]),
    ("Searches", [
        ("investigation", "/investigation", "Investigation", "i-investigation", "investigation"),
        ("deep_search", "/deep_search", "Deep Search", "i-search", "deep-search"),
        ("audit", "/audit", "Audit", "i-audit", "audit"),
        ("archive_workbench", "/archive-workbench", "Archive", "i-archive", "archive-workbench"),
    ]),
    ("Operations", [
        ("runlog_workbench", "/runlog_workbench", "Runlog Workbench", "i-runlog", "runlog-workbench"),
        ("noun_workbench", "/noun_workbench", "Noun Workbench", "i-grid", "noun-workbench"),
        ("verb_workbench", "/verb_workbench", "Verb Workbench", "i-sparkle", "verb-workbench"),
    ]),
    ("Tools", [
        ("camera", "/camera", "Image Capture", "i-camera", "camera"),
        ("custom_upload", "/custom_upload", "Custom Parsers", "i-parser", "custom-upload"),
        ("prepositional_phrase_runner", "/prepositional_phrase_runner", "Prep Phrase Runner", "i-terminal", "prepositional-phrase-runner"),
    ]),
    ("Admin", [
        ("template", "/template", "Template Manager", "i-template", "template"),
        ("account_roles", "/account_roles", "Account & Roles", "i-key", "account-roles"),
        ("backup", "/backup", "Backup Manager", "i-backup", "backup"),
        ("nodes_compliance", "/nodes_compliance", "Nodes Compliance", "i-compliance", "nodes-compliance"),
    ]),
    ("Tests", [
        ("test_parser", "/test_parser", "Parser Test", "i-robot", "parser-test"),
    ]),
]


def _render_rail_nav() -> str:
    groups = []
    for label, links in WORKSPACE_NAV:
        items = "\n".join(
            f'        <a class="rail-link" href="{route}" data-nav="{nav}" data-tag="{tag}" data-tooltip="{lbl}">'
            f'<span class="rail-link-icon icon-chip"><svg class="icon"><use href="/static/icons.svg#{icon}"/></svg></span>'
            f'<span class="rail-link-text">{lbl}</span></a>'
            for nav, route, lbl, icon, tag in links
        )
        groups.append(f'        <div class="rail-group-label">{label}</div>\n{items}')
    return "\n".join(groups)


def _shell_html(
    *,
    content: str,
    title: str,
    kicker: str,
    nav_key: str,
    page_css: Optional[str],
    page_script: Optional[str],
    page_module: bool = False,
    head_extra: str = "",
) -> str:
    """Wrap a content-only fragment in the Watery app-shell."""
    css_link = f'\n  <link rel="stylesheet" href="/static/styles/{page_css}">' if page_css else ""
    css_link += ("\n  " + head_extra) if head_extra else ""  # raw <head> extras (e.g. a vendor CSS/importmap)
    _type = ' type="module"' if page_module else ""
    script_tag = f'\n  <script{_type} src="/static/scripts/{page_script}"></script>' if page_script else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <link rel="icon" href="/static/favicon.ico" type="image/x-icon">
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} · GIMS</title>
  <link rel="stylesheet" href="/static/styles/watery.css">
  <link rel="stylesheet" href="/static/styles/shell.css">
  <link rel="stylesheet" href="/static/styles/components.css">
  <link rel="stylesheet" href="/static/lib/tour.css">{css_link}
  {theme_head_tags()}
</head>
<!-- is-loading until /login/inject.js resolves auth, then is-anon | is-authed -->
<body class="watery-bg is-loading" data-page="{nav_key}">
  <div class="app-shell">
    <aside class="rail" id="gims-rail" aria-label="Workspaces">
      <a class="rail-brand" href="/launcher">
        <span class="rail-brand-mark icon-chip"><svg class="icon"><use href="/static/icons.svg#i-compass"/></svg></span>
        <span class="rail-brand-text">GIMS</span>
      </a>
      <nav class="rail-nav" aria-label="Workspaces">
{_render_rail_nav()}
      </nav>
      <div class="rail-foot">
        <a class="rail-link" href="/tutorial" data-nav="tutorial" data-tooltip="How GIMS works">
          <span class="rail-link-icon icon-chip"><svg class="icon"><use href="/static/icons.svg#i-help"/></svg></span>
          <span class="rail-link-text">Tutorial</span>
        </a>
        <a class="rail-link" href="/launcher" data-nav="launcher" data-tooltip="Back to launcher">
          <span class="rail-link-icon icon-chip"><svg class="icon"><use href="/static/icons.svg#i-compass"/></svg></span>
          <span class="rail-link-text">Launcher</span>
        </a>
      </div>
    </aside>

    <div class="app-main">
      <header class="shell-header">
        <span class="shell-head-text">
          <span class="shell-kicker">{kicker}</span>
          <h1 class="shell-title">{title}</h1>
        </span>
        <div class="shell-head-actions">
          {theme_switcher_html()}
          <button class="shell-help" id="gims-help" type="button" title="Show me how" aria-label="Show me how">
            <svg class="icon"><use href="/static/icons.svg#i-help"/></svg>
          </button>
          <div class="userchip" id="gims-userchip">
            <button class="userchip-btn" id="userchip-btn" type="button" aria-haspopup="true" aria-expanded="false">
              <span class="userchip-avatar icon-chip blue round"><svg class="icon"><use href="/static/icons.svg#i-user"/></svg></span>
              <span class="userchip-meta">
                <span class="userchip-name" data-userchip-name>Account</span>
                <span class="userchip-sub" data-userchip-sub>signed in</span>
              </span>
              <svg class="icon userchip-caret"><use href="/static/icons.svg#i-chevron"/></svg>
            </button>
            <div class="userchip-menu w-pop" id="userchip-menu" hidden>
              <div class="userchip-id">
                <span class="userchip-avatar lg icon-chip blue round"><svg class="icon"><use href="/static/icons.svg#i-user"/></svg></span>
                <span class="userchip-id-text">
                  <span class="userchip-name" data-userchip-name>Account</span>
                  <span class="userchip-email" data-userchip-email>—</span>
                </span>
              </div>
              <div class="userchip-badges" data-userchip-badges></div>
              <div class="userchip-kv"><span>Projects</span><span data-userchip-projects>—</span></div>
              <div class="userchip-menu-foot">
                <a class="btn sm ghost" href="/account_roles"><svg class="icon"><use href="/static/icons.svg#i-key"/></svg>Roles &amp; tags</a>
                <button class="btn sm blue" id="auth-logout" type="button"><svg class="icon"><use href="/static/icons.svg#i-logout"/></svg>Sign out</button>
              </div>
            </div>
          </div>
        </div>
      </header>

      <div class="auth-loading" id="auth-loading" role="status" aria-live="polite">
        <span class="auth-loading-dot"></span> Loading workspace…
      </div>

      <section class="auth-card panel" id="gims-login" aria-labelledby="auth-title">
        <div class="auth-head">
          <span class="auth-mark icon-chip"><svg class="icon"><use href="/static/icons.svg#i-lock"/></svg></span>
          <span class="auth-head-text">
            <h2 class="auth-title" id="auth-title">Welcome back</h2>
            <p class="auth-sub" id="auth-sub">Sign in to open your GIMS workspace.</p>
          </span>
        </div>
        <form class="auth-form" id="gims-login-form" autocomplete="on" novalidate>
          <label class="field">
            <span class="field-label"><svg class="icon"><use href="/static/icons.svg#i-mail"/></svg>Email</span>
            <input class="input" id="auth-email" name="email" type="email" autocomplete="username" placeholder="you@example.com" required>
          </label>
          <label class="field">
            <span class="field-label"><svg class="icon"><use href="/static/icons.svg#i-lock"/></svg>Password</span>
            <input class="input" id="auth-pass" name="password" type="password" autocomplete="current-password" placeholder="••••••••" required>
          </label>
          <label class="field" id="auth-project-row" hidden>
            <span class="field-label"><svg class="icon"><use href="/static/icons.svg#i-compass"/></svg>Project code</span>
            <input class="input" id="auth-project" name="project" type="text" autocomplete="off" placeholder="e.g. LIMS-System">
          </label>
          <div class="auth-msg" id="auth-msg" role="alert" hidden></div>
          <button class="btn-primary auth-submit" id="auth-submit" type="submit">Sign in</button>
        </form>
        <p class="auth-foot">
          <span id="auth-foot-text">New to GIMS?</span>
          <button class="auth-switch" id="auth-toggle" type="button">Create an account</button>
        </p>
      </section>

      <main class="shell-content" id="gims-content">
{content}
      </main>
    </div>
  </div>

  <div id="tooltip" class="tooltip"></div>
  <div class="toasts" aria-live="polite" aria-atomic="true"></div>

  <script src="/static/scripts/gims.js"></script>
  <script src="/static/scripts/shell.js"></script>
  <script src="/static/lib/tour.js"></script>
  <script src="/static/scripts/tours.js"></script>{script_tag}
</body>
</html>
"""


def make_page_node(
    *,
    name: str,
    route: str,
    html_file: str,
    kind: NodeKind = NodeKind.UI,
    icon: Optional[str] = None,
    label: Optional[str] = None,
    in_schema: bool = False,
    extra_meta: Optional[dict] = None,
    shell: bool = False,
    title: Optional[str] = None,
    kicker: str = "GIMS Workspace",
    nav_key: Optional[str] = None,
    page_css: Optional[str] = None,
    page_script: Optional[str] = None,
    page_module: bool = False,
    head_extra: str = "",
) -> Node:
    """Build a UI page Node that serves ``gui/components/<html_file>`` with registry asset injection.

    With ``shell=True`` the HTML file is treated as a *content-only fragment* and wrapped in the
    shared Watery app-shell (``watery.css`` + ``shell.css`` + ``gims.js`` toolkit + ``page_css`` /
    ``page_script``); the registry still splices login/orchestrate/state-dock before ``</body>``.
    """
    router = APIRouter()
    _nav = nav_key or route.strip("/").split("/")[-1]
    _title = title or label or name

    @router.get(route, response_class=HTMLResponse, include_in_schema=in_schema)
    async def _serve_page():
        html_path = _resolve_html(html_file)
        if not html_path:
            return PlainTextResponse(
                f"{html_file} not found. Expected at gui/components/{html_file}", status_code=404
            )
        html = html_path.read_text(encoding="utf-8")
        if shell:
            html = _shell_html(
                content=html, title=_title, kicker=kicker, nav_key=_nav,
                page_css=page_css, page_script=page_script, page_module=page_module,
                head_extra=head_extra,
            )
        else:
            html = _inject_theme(html)  # standalone pages (launcher) get the modular theme wiring too
        inject = _inject_tags(route)
        if inject:
            html = (
                html.replace("</body>", f"{inject}\n</body>")
                if "</body>" in html
                else html + "\n" + inject
            )
        return HTMLResponse(html)

    meta: dict = {"entry_path": route}
    if icon:
        meta["icon"] = icon
    if label:
        meta["label"] = label
    if extra_meta:
        meta.update(extra_meta)

    return Node(name=name, kind=kind, router=router, template=_resolve_html(html_file), meta=meta)
