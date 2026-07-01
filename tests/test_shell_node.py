"""Guards for the Tier-0 app-shell wrapper in core/orchestration/page_node.py.

The shell is the leverage for the whole front-end refactor: it injects watery.css +
shell.css + the gims.js toolkit and reuses the exact login/userchip DOM contract that
/login/inject.js wires. If any of that drifts, every shell page breaks at once.
"""
from __future__ import annotations

from core.orchestration.page_node import _shell_html, _render_rail_nav, WORKSPACE_NAV, make_page_node
from core.orchestration.node import NodeKind


FRAGMENT = '<section class="panel" id="my-content">hello</section>'


def _wrap(**kw):
    defaults = dict(content=FRAGMENT, title="Noun Configure", kicker="Schema",
                    nav_key="noun_configure", page_css="noun_configure.css",
                    page_script="noun_configure.js")
    defaults.update(kw)
    return _shell_html(**defaults)


def test_shell_links_foundation_assets():
    h = _wrap()
    for needle in ("/static/styles/watery.css", "/static/styles/shell.css",
                   "/static/scripts/gims.js", "/static/scripts/shell.js"):
        assert needle in h, needle


def test_shell_includes_page_css_and_script():
    h = _wrap()
    assert "/static/styles/noun_configure.css" in h
    assert "/static/scripts/noun_configure.js" in h
    # ...and omits them cleanly when not supplied
    h2 = _wrap(page_css=None, page_script=None)
    assert "/static/styles/noun_configure.css" not in h2
    assert "/static/scripts/noun_configure.js" not in h2


def test_shell_preserves_login_and_userchip_contract():
    """These ids/classes are what /login/inject.js binds to — they must be present verbatim."""
    h = _wrap()
    for needle in ("id=\"gims-login-form\"", "id=\"auth-email\"", "id=\"auth-pass\"",
                   "id=\"auth-project-row\"", "id=\"auth-submit\"", "id=\"auth-toggle\"",
                   "id=\"gims-userchip\"", "id=\"userchip-btn\"", "id=\"auth-logout\"",
                   "data-userchip-name", "id=\"auth-loading\""):
        assert needle in h, needle
    assert 'class="watery-bg is-loading"' in h


def test_shell_embeds_content_and_nav_key():
    h = _wrap()
    assert FRAGMENT in h
    assert 'data-page="noun_configure"' in h


def test_rail_nav_renders_every_workspace_link():
    nav = _render_rail_nav()
    for _label, links in WORKSPACE_NAV:
        for nav_key, route, lbl, _icon, tag in links:
            assert f'href="{route}"' in nav
            assert f'data-nav="{nav_key}"' in nav
            assert f'data-tag="{tag}"' in nav, tag  # role-gating hook


def test_make_page_node_shell_flag_builds():
    node = make_page_node(name="X", route="/x", html_file="launcher.html",
                          kind=NodeKind.UI, shell=True, title="X")
    assert node.router is not None
