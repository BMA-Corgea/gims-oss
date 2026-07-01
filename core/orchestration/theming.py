"""Modular theme system shared by EVERY GIMS page (app-shell, launcher, node pages).

A theme is one CSS file at ``static/styles/<name>.css`` whose rules are all scoped under
``html[data-theme="<name>"]``, plus one row in :data:`THEMES`. ``watery`` is the always-loaded
BASE (default `:root` tokens + the component layer in ``watery.css``); every other theme is a
thin override file that redefines those tokens (and a few component bevels) under its scope.

Because the wiring lives here and is injected uniformly, **adding a theme is one file + one
THEMES row** — it then loads, appears in the header switcher, applies, and persists on every
page automatically. To add e.g. Nocturne: write ``static/styles/nocturne.css`` (scoped under
``html[data-theme="nocturne"]``) and append ``("nocturne", "Nocturne")`` below.

The pieces every page needs:
  * :func:`theme_head_tags`  — a pre-paint ``<script>`` (sets ``data-theme`` from localStorage
    before first paint, no flash) + the alternate-theme ``<link>`` tags (loaded LAST so their
    scoped overrides win ties against page CSS).
  * :func:`theme_switcher_html` — the header ``<select>`` (built from THEMES).
The shell template calls these directly; standalone/node pages get them spliced in by
``make_page_node`` (non-shell branch) or by importing these helpers.
"""
from __future__ import annotations

# (name, label). The FIRST row is the default/base theme — it owns the bare `:root`
# (no data-theme attribute) and ships no separate override file.
THEMES: list[tuple[str, str]] = [
    ("watery", "Watery"),
    ("classic", "Classic"),
]
DEFAULT_THEME = THEMES[0][0]

# Storage key shared with shell.js wireThemeSwitch(); keep them in sync.
THEME_STORAGE_KEY = "gims_theme"

# Pre-paint: apply the saved theme to <html> before first paint so an alternate skin
# renders with no flash. Plain concatenation (no f-string) so the JS braces survive.
THEME_BOOT_JS = (
    "(function(){try{var t=localStorage.getItem('" + THEME_STORAGE_KEY + "');"
    "if(t&&t!=='" + DEFAULT_THEME + "')document.documentElement.setAttribute('data-theme',t);}"
    "catch(e){}})();"
)


def theme_head_tags() -> str:
    """`<head>` tags every page needs: the pre-paint script + alternate-theme stylesheet links.

    Returned as a single string to splice once (ideally just before ``</head>`` so the links
    load last). Inert by default — each theme file only activates under its ``data-theme`` scope.
    """
    links = "\n  ".join(
        f'<link rel="stylesheet" href="/static/styles/{name}.css">'
        for name, _label in THEMES
        if name != DEFAULT_THEME
    )
    # pre-paint (inline, no defer) + alternate-skin links + the switcher binder (deferred,
    # runs after DOM so it finds the <select> — works on shell AND standalone pages).
    return (
        f"<script>{THEME_BOOT_JS}</script>\n  {links}\n  "
        '<script defer src="/static/lib/theme-switch.js"></script>'
    )


def theme_switcher_html() -> str:
    """The header skin ``<select>`` (palette icon). Options are built from THEMES, so a new
    theme appears in the switcher on every page with no markup change."""
    opts = "".join(f'<option value="{name}">{label}</option>' for name, label in THEMES)
    return (
        '<label class="theme-switch" title="Interface skin" aria-label="Interface skin">'
        '<svg class="icon"><use href="/static/icons.svg#i-palette"/></svg>'
        f'<select class="theme-select" id="gims-theme">{opts}</select>'
        "</label>"
    )
