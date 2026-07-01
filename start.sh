#!/usr/bin/env bash
# Location-agnostic launcher for the GIMS open-core build.
#
# Anchors to its OWN directory (not the caller's cwd), so it works no matter where this repo
# lives or how it's invoked:  ./start.sh  ·  bash /any/path/gims-oss/start.sh  ·  from cron.
# If the repo is moved, the venv is transparently recreated at the new path.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

APP_HOST="${GIMS_HOST:-127.0.0.1}"
APP_PORT="${GIMS_PORT:-8100}"
PY="${PYTHON:-python3}"

# (Re)create the venv if it is missing OR was created under a different path (repo moved/renamed).
_venv_ok() {
  [[ -d ".venv" ]] || return 1
  grep -qF "$DIR/.venv" ".venv/bin/activate" 2>/dev/null
}
if ! _venv_ok; then
  echo "[gims] creating .venv at $DIR/.venv ..."
  rm -rf .venv
  "$PY" -m venv .venv
fi

# shellcheck disable=SC1091
source ".venv/bin/activate"

python -m pip install --upgrade pip -q
if [[ -f requirements.txt ]]; then
  echo "[gims] installing requirements ..."
  python -m pip install -r requirements.txt -q
fi

echo "[gims] starting GIMS (open core) on http://${APP_HOST}:${APP_PORT}  —  Ctrl-C to stop"
exec python -m uvicorn api.app:app --host "$APP_HOST" --port "$APP_PORT"
