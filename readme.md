# CS2 WebRadar

A browser-based radar and overlay for Counter-Strike 2. No kernel driver required — reads game memory via `ReadProcessMemory` and streams data to a React frontend over WebSocket.

## Features

- **Browser radar** — real-time minimap with player dots, view cones, bomb timer, grenades and callouts
- **Minimap overlay** — small draggable transparent window sitting on top of your game
- **ESP overlay** — full-screen transparent player boxes through walls (CS2 must be Fullscreen Windowed)
- **Self-healing offsets** — automatically detects CS2 updates, scans `client.dll` and fixes offsets with zero manual steps
- **30 Hz polling** — near real-time updates with smooth interpolation
- **Settings panel** — dot size, bomb color, view cones, name labels, grenade icons, death crosses, all persistent

## Requirements

- Windows 10/11
- [Node.js](https://nodejs.org/en/download) (for running from source)
- [Python 3.11+](https://www.python.org/downloads/) (for running from source)
- CS2 running (the exe must be launched as **Administrator**)

## Quick Start (exe)

1. Download `GameOverlayService_v12.exe` from [Releases](../../releases)
2. Run as **Administrator**
3. Select mode at the prompt (Normal / Overlay / ESP)
4. Open `http://localhost:5173` in your browser (Normal mode opens it automatically)

## Running from Source

```bat
cd ai_agent_helper
run.bat
```

`run.bat` installs dependencies, shows the mode selector, starts the Vite dev server and Python backend together.

Or manually:

```bat
# Terminal 1 — frontend
cd webapp
npm install
npm run dev

# Terminal 2 — backend (run as Administrator)
cd ai_agent_helper
pip install -r requirements.txt
python main.py --normal     # or --overlay / --esp
```

## Modes

| Flag | Description |
|------|-------------|
| `--normal` | Browser radar — open `localhost:5173` in any browser |
| `--overlay` | Small draggable minimap window on screen |
| `--esp` | Full-screen transparent player boxes (CS2 must be Fullscreen Windowed) |

## Building the exe

```bat
cd ai_agent_helper
build.bat
```

Requires `pyinstaller` and `pywebview` (`pip install pyinstaller pywebview`). Output: `dist/GameOverlayService_v12.exe`.

## How Offsets Work

On startup the backend:
1. Checks if `client.dll` has changed since the last cached offsets
2. If unchanged — uses the cache instantly, no network needed
3. If updated — fetches latest offsets from [cs2-dumper](https://github.com/a2x/cs2-dumper), then scans `client.dll` locally to patch any values cs2-dumper hasn't updated yet
4. Saves the result — next start is instant again

This means the radar self-heals after CS2 updates with no manual intervention.

## Notes

- Must be run as **Administrator** for `ReadProcessMemory` to work
- CS2 must be in **Fullscreen Windowed** mode for the ESP overlay
- Tested on Windows 11 with CS2 via Steam
