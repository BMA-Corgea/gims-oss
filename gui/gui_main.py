# gui/gui_main.py — BACK-COMPAT SHIM.
#
# The ASGI app moved to api/app.py (the routers it mounts now live under api/routers/,
# so the real entrypoint belongs next to them, not in the HTML/components dir). This shim
# preserves the legacy import path `gui.gui_main:app` for external launchers that may still
# reference it (start.sh / Procfile / PyInstaller spec / deployment configs). New code should
# import `api.app` directly.
from api.app import app  # noqa: F401  (re-exported for back-compat)

__all__ = ["app"]
