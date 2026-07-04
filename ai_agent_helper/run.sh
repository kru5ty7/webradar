#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEBAPP_DIR="$DIR/../webapp"
PYTHON="${PYTHON:-python3}"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "[error] python3 not found"
  exit 1
fi

if ! "$PYTHON" -c "import websockets, webview, PIL, vpk; import lz4.block" >/dev/null 2>&1; then
  echo "[info] installing Python dependencies..."
  "$PYTHON" -m pip install --user -r "$DIR/requirements.txt"
fi

echo
echo "  CS2 WebRadar - Mode Selection"
echo "  =============================="
echo "  1. Normal   (browser tab, no overlay)"
echo "  2. Minimap  (small draggable overlay window)"
echo "  3. ESP      (web ESP overlay)"
echo
read -r -p "  Select mode [1-3]: " CHOICE

case "${CHOICE:-1}" in
  1) MODE="--normal" ;;
  2) MODE="--overlay" ;;
  3) MODE="--esp" ;;
  *)
    echo "[error] invalid choice; defaulting to Normal mode"
    MODE="--normal"
    ;;
esac

FRONTEND_PID=""
cleanup() {
  if [[ -n "$FRONTEND_PID" ]] && kill -0 "$FRONTEND_PID" >/dev/null 2>&1; then
    kill "$FRONTEND_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

if command -v npm >/dev/null 2>&1; then
  echo
  echo "[1/2] Starting frontend dev server..."
  (cd "$WEBAPP_DIR" && npm run dev) &
  FRONTEND_PID="$!"
  sleep 2
else
  echo "[warn] npm not found; using built webapp if webapp/dist exists"
fi

echo "[2/2] Starting backend (mode: $MODE)..."
cd "$DIR"
"$PYTHON" main.py "$MODE"
