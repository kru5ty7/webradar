# CS2 WebRadar

A browser-based radar and overlay for Counter-Strike 2, for Windows and native Linux. No kernel driver — it reads game memory from user space and streams the data to a React frontend over WebSocket.

> ## ⚠️ Disclaimer — educational use only
>
> This project is published **for educational and research purposes only**: it shows how external memory reading, offset discovery and a WebSocket data pipeline work.
>
> - **Using it can get your account banned.** Reading the memory of a running game is detected by anti-cheat systems (including VAC) and breaks the game's and Steam's terms of service. Radar and ESP tools give an unfair advantage in online matches.
> - **The authors and contributors take no responsibility** for bans, account loss, lost items, or any other consequence of running this software. You use it entirely at your own risk.
> - Do not use it in matchmaking, on official servers or in any competitive setting. Keep it to offline use, your own private servers, or learning.
> - It is provided "as is", without warranty of any kind (see [LICENSE](LICENSE)). It is not affiliated with, endorsed by or connected to Valve Corporation. Counter-Strike and Steam are trademarks of Valve.

## Features

- **Browser radar** — real-time minimap with player dots, view cones, bomb timer, grenades and (where available) map callouts
- **Minimap overlay** — small draggable transparent window on top of the game
- **ESP overlay** — full-screen transparent player boxes (CS2 must be Fullscreen Windowed)
- **Self-healing offsets** — detects CS2 updates, fetches fresh offsets and scans the game binary to patch stale values
- **Status pill** — shows why the radar is empty (no game, menu, stale offsets, no players) with a one-click "Refresh offsets"
- **Zoom and follow-me** — zoom the map 1x–4x and keep your own dot centred
- **Players and utility options** — show/hide allies, ally and enemy names, and per-utility circle / name / emoji display
- **~30 Hz updates** with smooth interpolation; settings are saved in the browser

## Requirements

- **Windows 10/11** or **Linux** (native CS2 on Steam)
- [Node.js](https://nodejs.org/en/download) 18+ and [Python 3.10+](https://www.python.org/downloads/) when running from source
- CS2 running. Windows: start the helper as **Administrator**. Linux: the helper needs permission to read `/proc/<pid>/mem` (the launcher uses `sudo`).

## Repository layout

```
helper/
  linux/     Python helper for Linux (also runs on Windows)
  windows/   Windows-only fork (own fixes; what the release exe is built from)
    src/       the helper code (main.py is the entry point)
    scripts/   run / build launchers (+ setup_windows on Windows)
    tools/     offset-scanning utilities
webapp/      React + Vite frontend
```

## Quick start (release build)

1. Download `cs2-webradar.exe` (Windows) or `cs2-radar-linux` (Linux) from [Releases](../../releases)
2. Run it as Administrator / with the permissions described above
3. Pick a mode (Normal / Overlay / ESP) at the prompt
4. Open `http://localhost:5173` (Normal mode opens it for you)

The Windows build ships with auto-update off; replace the exe yourself to update.

## Running from source

**Windows** (administrator PowerShell, first time only: `helper\windows\scripts\setup_windows.ps1` installs Python, Node and the dependencies):

```bat
cd helper\windows
scripts\run.bat
```

`run.bat` installs dependencies, shows the mode selector and starts the Vite dev server and the Python backend together.

**Linux:**

```bash
cd helper/linux
scripts/run-linux.sh              # own .venv, backend under sudo; add --overlay or --esp
```

`scripts/run.sh` does the same without `sudo`.

**By hand** (two terminals):

```bash
# 1: frontend
cd webapp && npm install && npm run dev

# 2: backend
cd helper/windows    # or helper/linux
pip install -r requirements.txt
python src/main.py --normal       # or --overlay / --esp
```

## Modes

| Flag | Description |
|------|-------------|
| `--normal` | Browser radar — open `localhost:5173` in any browser |
| `--overlay` | Small draggable minimap window |
| `--esp` | Full-screen transparent player boxes (CS2 must be Fullscreen Windowed) |

## Building the exe

```bat
cd helper\windows
scripts\build.bat
```

Needs `pyinstaller` and `pywebview`. Output: `helper\windows\dist\cs2-webradar.exe`. Releases are built by GitHub Actions when a `v*` tag is pushed.

## How offsets work

1. If `client.dll` is older than the cached offsets, the cache is used — no network needed
2. Otherwise offsets are fetched from [cs2-dumper](https://github.com/a2x/cs2-dumper) and the game binary is scanned locally to patch values that are not updated yet (on Linux the dumper's offsets are Windows-only, so a Linux scan and field baseline are applied)
3. The result is cached; the "Refresh offsets" button forces a re-fetch

## Network and security

The helper serves the webapp on port 5173 and the data on WebSocket port 22006, on all interfaces so a phone or second PC on your LAN can watch.

- It rejects WebSocket connections from other websites and from unexpected hostnames (DNS-rebinding protection). A custom domain or ngrok host must be listed under `allowed_hosts` in `config.json`.
- `refresh_offsets` and `set_auto_update` are accepted only from the machine running the helper, unless `allow_remote_control` is `true`.
- Don't expose the ports to the internet unless you mean to.

## Notes

- `config.json`, `offsets_cache.json` and `radar.log` are created next to the helper (not tracked in git)
- Tested with CS2 via Steam on Windows 11 and Linux
