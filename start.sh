#!/bin/bash
# CS2 WebRadar — avitran backend + webradar frontend

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RADAR_DIR="$HOME/Workspaces/cs2-radar"
VENV="$SCRIPT_DIR/.venv/bin/python"

# Kill old instances
pkill -f "python.*avitran_bridge" 2>/dev/null || true
pkill -f "tsx index.ts" 2>/dev/null || true
pkill -f "vite" 2>/dev/null || true
sleep 1

# Build Rust radar if needed
if [ ! -f "$RADAR_DIR/target/release/radar" ]; then
    echo "Building Rust radar (first time, takes ~1-2 min)..."
    cd "$RADAR_DIR" && cargo build --release
fi

# Start avitran radar (needs sudo for CS2 memory)
echo "Starting avitran cs2-radar on port 9001..."
xterm -T "CS2 Radar Backend" -geometry 100x25 \
    -e "cd '$RADAR_DIR' && sudo env PATH=\"\$PATH\" npx tsx index.ts; read -p 'Press Enter'" &

sleep 2

# Start bridge (converts avitran format → webradar format)
echo "Starting format bridge on port 22006..."
xterm -T "CS2 Radar Bridge" -geometry 100x15 \
    -e "cd '$SCRIPT_DIR' && $VENV avitran_bridge.py; read -p 'Press Enter'" &

sleep 1

# Start webapp
echo "Starting web UI on http://localhost:5173 ..."
xterm -T "CS2 Radar Web UI" -geometry 120x20 \
    -e "cd '$SCRIPT_DIR/webapp' && npx vite; read -p 'Press Enter'" &

echo ""
echo "All services starting. Open http://localhost:5173 in your browser."
echo "Join a CS2 match for player data to appear."
