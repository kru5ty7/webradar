#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEBAPP_DIR="$ROOT_DIR/webapp"
BACKEND_DIR="$ROOT_DIR/ai_agent_helper"
PYTHON_BIN="${PYTHON:-$ROOT_DIR/.venv/bin/python}"
VITE_BIN="$WEBAPP_DIR/node_modules/.bin/vite"

MODE="--normal"
NO_FUNNEL=1
BACKEND_EXTRA_ARGS=()
FRONTEND_PID=""
BACKEND_PID=""

usage() {
  cat <<EOF
Usage: ./run-linux.sh [normal|overlay|esp] [--funnel] [backend args...]

Starts:
  - Vite webapp on http://localhost:5173
  - Python backend WebSocket on ws://localhost:22006/cs2_webradar

Examples:
  ./run-linux.sh
  ./run-linux.sh overlay
  ./run-linux.sh esp --funnel

Environment:
  PYTHON=/path/to/python   Override Python interpreter
  NO_SUDO=1                Run backend without sudo
EOF
}

for arg in "$@"; do
  case "$arg" in
    normal|--normal)
      MODE="--normal"
      ;;
    overlay|--overlay|minimap|--minimap)
      MODE="--overlay"
      ;;
    esp|--esp)
      MODE="--esp"
      ;;
    --funnel)
      NO_FUNNEL=0
      ;;
    --no-funnel)
      NO_FUNNEL=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      BACKEND_EXTRA_ARGS+=("$arg")
      ;;
  esac
done

cleanup() {
  local status=$?
  trap - EXIT INT TERM

  if [[ -n "$BACKEND_PID" ]] && kill -0 "$BACKEND_PID" 2>/dev/null; then
    kill "$BACKEND_PID" 2>/dev/null || true
  fi

  if [[ -n "$FRONTEND_PID" ]] && kill -0 "$FRONTEND_PID" 2>/dev/null; then
    kill "$FRONTEND_PID" 2>/dev/null || true
  fi

  wait "$BACKEND_PID" 2>/dev/null || true
  wait "$FRONTEND_PID" 2>/dev/null || true
  exit "$status"
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "[error] '$1' is required but was not found in PATH"
    exit 1
  fi
}

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python3 || true)"
fi

if [[ -z "$PYTHON_BIN" ]]; then
  echo "[error] Python not found. Install Python 3 or set PYTHON=/path/to/python."
  exit 1
fi

require_cmd npm

if [[ ! -x "$VITE_BIN" ]]; then
  echo "[error] webapp dependencies are missing."
  echo "        Run: cd '$WEBAPP_DIR' && npm install"
  exit 1
fi

if ! "$PYTHON_BIN" -c "import websockets, PIL, lz4.block, vpk" >/dev/null 2>&1; then
  echo "[error] Python dependencies are missing for: $PYTHON_BIN"
  echo "        Run: '$PYTHON_BIN' -m pip install -r '$BACKEND_DIR/requirements.txt'"
  exit 1
fi

BACKEND_ARGS=("$MODE")
if [[ "$NO_FUNNEL" -eq 1 ]]; then
  BACKEND_ARGS+=("--no-funnel")
fi
BACKEND_ARGS+=("${BACKEND_EXTRA_ARGS[@]}")

BACKEND_CMD=("$PYTHON_BIN" "$BACKEND_DIR/main.py" "${BACKEND_ARGS[@]}")
if [[ "${NO_SUDO:-0}" != "1" && "$(id -u)" -ne 0 ]]; then
  require_cmd sudo
  BACKEND_CMD=(sudo -E "${BACKEND_CMD[@]}")
fi

trap cleanup EXIT INT TERM

echo "[1/2] Starting webapp: http://localhost:5173"
(
  cd "$WEBAPP_DIR"
  "$VITE_BIN" --host 0.0.0.0 --port 5173
) &
FRONTEND_PID=$!

sleep 1

echo "[2/2] Starting Python backend: ${BACKEND_ARGS[*]}"
"${BACKEND_CMD[@]}" &
BACKEND_PID=$!

cat <<EOF

CS2 WebRadar is starting.
Open: http://localhost:5173

Press Ctrl+C to stop both services.
EOF

wait -n "$FRONTEND_PID" "$BACKEND_PID"
