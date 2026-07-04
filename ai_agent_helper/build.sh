#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEBAPP_DIR="$DIR/../webapp"
PYTHON="${PYTHON:-python3}"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "[error] python3 not found"
  exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "[error] npm not found"
  exit 1
fi

echo "[1/3] Building frontend..."
(cd "$WEBAPP_DIR" && npm run build)

echo "[2/3] Installing Python build dependencies..."
"$PYTHON" -m pip install --user -r "$DIR/requirements.txt" pyinstaller

echo "[3/3] Building Linux binary..."
cd "$DIR"
rm -rf build
"$PYTHON" -m PyInstaller radar.spec --noconfirm

echo
echo "Done - dist/cs2-radar"
echo "Copy config.json next to the binary before running, if you use a custom config."
