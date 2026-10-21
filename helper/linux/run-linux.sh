#!/usr/bin/env bash
# Linux launcher: frontend as your user, backend as root (venv in .venv) (reads /proc/<cs2>/mem).
#   ./run-linux.sh [--normal|--overlay|--esp]     (default: --normal)
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEBAPP_DIR="$DIR/../../webapp"
PYTHON="${PYTHON:-python3}"
MODE="${1:---normal}"

command -v "$PYTHON" >/dev/null 2>&1 || { echo "[error] python3 not found"; exit 1; }

# Debian/Ubuntu block system-wide pip (PEP 668), so use a local venv. It sees
# system packages too, which pywebview's GTK/Qt bindings need for the overlay.
VENV="$DIR/.venv"
if [[ ! -x "$VENV/bin/python" ]]; then
  echo "[info] creating virtualenv (needs python3-venv: sudo apt install python3-venv)..."
  "$PYTHON" -m venv --system-site-packages "$VENV"
fi
PYTHON="$VENV/bin/python"
if ! "$PYTHON" -c "import websockets, webview, PIL, vpk; import lz4.block" >/dev/null 2>&1; then
  echo "[info] installing Python dependencies..."
  "$PYTHON" -m pip install -r "$DIR/requirements.txt"
fi

FRONTEND_PID=""
cleanup() {
  if [[ -n "$FRONTEND_PID" ]] && kill -0 "$FRONTEND_PID" >/dev/null 2>&1; then
    # setsid made it a group leader: kill the whole group (npx + vite).
    kill -- "-$FRONTEND_PID" >/dev/null 2>&1 || kill "$FRONTEND_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

if command -v npm >/dev/null 2>&1; then
  echo "[1/2] Starting frontend dev server..."
  # Vite only: the helper serves the WebSocket on 22006 itself, and the relay
  # that `npm run dev` also starts would take that port.
  (cd "$WEBAPP_DIR" && exec setsid npx vite) &
  FRONTEND_PID="$!"
  sleep 2
else
  echo "[warn] npm not found; using built webapp if webapp/dist exists"
fi

echo "[2/2] Starting backend as root (mode: $MODE)..."
cd "$DIR"
# Pass the display vars through explicitly so the overlay window can open as root.
sudo env "HOME=$HOME" "DISPLAY=${DISPLAY:-}" "WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-}" "XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-}" "$PYTHON" main.py "$MODE"
