# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

CS2 WebRadar is a browser-based radar for Counter-Strike 2. It has two components:
1. **ai_agent_helper** — a Python process that reads game memory (Windows and native Linux) and streams JSON over WebSocket. `ai_agent_helper_windows/` is a Windows-only fork of it.
2. **webapp** — a React/Vite frontend that receives and renders that JSON on a radar map

## Architecture

### Data Flow

```
CS2 process (game memory)
    → ai_agent_helper/main.py (Python memory reader, 10Hz polling)
        → WebSocket server on ws://HOST:22006/cs2_webradar
            → browser WebSocket clients
                → React components
```

The helper serves the WebSocket itself and, when `webapp/dist` exists, the built webapp over HTTP on port 5173. `webapp/ws/app.js` is a standalone Node relay used only by `npm run dev`.

### webapp (React + Vite)

Entry: `webapp/src/app.jsx`

**Connection logic** (in `App` component):
- `USE_LOCALHOST` flag switches between localhost and `PUBLIC_IP`/`window.location.hostname`
- `VITE_WS_URL` env var overrides for ngrok tunnels
- WebSocket connects to `ws://HOST:22006/cs2_webradar`

**JSON payload fields** received from the helper:
- `m_players[]` — array of player objects (m_position.x/y, m_team, m_health, m_eye_angle, m_weapons, etc.)
- `m_local_team` — team number of local player
- `m_bomb` — bomb state (x, y, m_blow_time, m_is_defusing, m_defuse_time, m_is_defused)
- `m_grenades[]` — active grenade positions
- `m_map` — map name string (e.g. `"de_dust2"`) or `"invalid"`
- `m_status` — `{ state, detail }` helper status shown by `StatusPill`; also sent alone as `{ type: "status", ... }` while there is no game data

**Map data** lives in `webapp/public/data/<mapname>/`:
- `data.json` — `{ x, y, scale }` — world-space origin and units-per-pixel for coordinate mapping
- `radar.png` — overhead map image
- `background.png` — blurred background image
- `callouts.json` — named area coordinates for the callout overlay layer

**Radar coordinate conversion**: world (x, y) → radar pixel = `(worldCoord - mapOrigin) / scale`. Both `Player` and `Bomb` components implement this.

**Component structure**:
- `Radar` — container; renders radar image + all child layers
- `Player` — individual dot with view cone and name label
- `Bomb` — C4 icon on radar
- `GrenadeLayer` — all active grenades
- `CalloutLayer` — area name labels from `callouts.json`
- `PlayerCard` — sidebar card (health, weapons, armor) shown on lg+ screens
- `Latency` — ping display + settings panel trigger

Settings are persisted to `localStorage` under key `radarSettings` and merged over `DEFAULT_SETTINGS` in `app.jsx`. Notable ones: PLAYERS group (`showAllies`, `showAllyNames`, `showEnemyNames`), utility display (`showUtilCircle`/`Name`/`Emoji`), `zoom` (1–4) and `followMe` (centre the local player). Ally/enemy colour is measured against `getLocalTeam()` (the local player's own team) so a team switch can't invert it. `set_auto_update` is sent to the helper only when `autoUpdate` changes. Settings UI exists twice: `SettingsButton.jsx` (browser) and the popup in `app.jsx` (minimap) — keep them in step.

## ai_agent_helper (Python memory reader)

`ai_agent_helper/` is pure Python — no Visual Studio required.

**How it works:**
1. On first run it fetches current offsets from [a2x/cs2-dumper](https://github.com/a2x/cs2-dumper) and caches them in `offsets_cache.json`. The cache stays valid while `client.dll` is older than it; only if the DLL can't be found does a 1-hour TTL apply. The Windows fork also invalidates the cache when the game build changes.
2. Opens CS2 process via `ReadProcessMemory` (requires admin or a process with a handle CS2 already has).
3. Walks the entity list at 10 Hz, builds the JSON payload, and serves it on `ws://HOST:22006/cs2_webradar`.

**Run:**
```bat
cd ai_agent_helper
run.bat          # installs deps if needed, then starts
# or manually:
pip install -r requirements.txt
python main.py
```

**Config:** `config.json` next to the script/exe (`m_use_localhost`, `m_local_ip`, `steam_id`, `auto_update`). Created automatically on first run; not tracked in git.

**Offset updates:** use the status pill's "Refresh offsets" button (WebSocket `refresh_offsets`, 30 s cooldown) or delete `offsets_cache.json` to force a fresh fetch. On Linux the a2x offsets are Windows-only for globals and fields, so the helper patches them with a local ELF scan and a field baseline.

**WebSocket access control:** the helper rejects connections whose `Host` is not localhost, an IP, `*.ts.net` or listed in `config.json` `allowed_hosts` (DNS rebinding), and browser connections whose `Origin` host differs from `Host` (other websites). This also covers the HTTP server's `/cs2_webradar` proxy. `set_auto_update` and `refresh_offsets` are honoured only from this machine unless `allow_remote_control` is true. ngrok or custom domains must be added to `allowed_hosts`.

**Dependencies:** `requirements.txt` (`websockets`, `pywebview`, `Pillow`, `lz4`, `vpk`); memory access uses stdlib `ctypes`.

## Development Commands

### webapp
```bash
cd webapp
npm install          # install dependencies
npm run dev          # start relay server + Vite dev server (localhost:5173)
npm run build        # production build to webapp/dist/
npm run lint         # ESLint
npm run preview      # serve the production build
```

### ai_agent_helper
Windows: `run.bat` (as administrator). Linux: `run-linux.sh` (own `.venv`, Vite only for the frontend, backend under `sudo` because it reads `/proc/<pid>/mem`) or `run.sh` (no sudo). Releases are built by `.github/workflows/release.yml` on `v*` tag pushes. First-time Windows setup: `ai_agent_helper_windows/setup_windows.py`.

## Configuration

**Localhost vs. LAN/remote** (two places must match):

| File | Setting |
|------|---------|
| `ai_agent_helper/config.json` | `m_use_localhost: true/false` |
| `webapp/src/app.jsx` line ~12 | `const USE_LOCALHOST = 1/0` |

**ngrok**: Set `VITE_WS_URL=wss://<tunnel>.ngrok-free.app/cs2_webradar` in a `.env` file before running `npm run dev`.

## Adding a New Map

1. Create `webapp/public/data/<mapname>/` with `radar.png`, `background.png`, and `data.json` (origin x/y and scale from CS2 map overview files).
2. Optionally add `callouts.json` for the callout overlay.
3. No code changes needed — the map name comes from the helper payload and is used to fetch the data directory dynamically.
