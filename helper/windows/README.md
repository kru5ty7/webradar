# helper/windows

Windows-only fork of `../linux`. It has the same radar payload and
webapp contract, with every native-Linux code path removed (no `/proc` reads,
ELF scanner, live schema rescans, `chunk_scanner.so` or Linux field baseline).

On Windows the a2x/cs2-dumper offsets match `client.dll`, so they are used
directly. The on-disk PE signature scan patches any stale global RVAs.

## First-time setup

From an **administrator** PowerShell in this folder:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1   # no Python yet? use this
python scripts\setup_windows.py            # or, if Python 3.10+ is already installed
python scripts\setup_windows.py --check    # just report what is missing
python scripts\setup_windows.py --build    # also build webapp/dist
```

It checks each item and installs only what is missing or too old, using
winget: Python 3.10+, Node.js 18+ with npm, the WebView2 runtime (needed by the
minimap/overlay window), the pip packages in `requirements.txt`, and the
webapp's npm packages. Re-running it is safe.

## Run

```bat
scripts\run.bat    :: as Administrator — installs deps, starts webapp + helper
:: or
pip install -r requirements.txt
python src\main.py --normal
```

## Build an exe

```bat
scripts\build.bat  :: -> dist\cs2-webradar.exe (copy config.json next to it)
```

## Differences from helper/linux

- Exits straight away on a non-Windows host.
- `auto_update` is off by default. GitHub releases ship the cross-platform
  build, which would replace this fork.
- `offsets_cache.json` is separate from the Linux helper's cache. Don't copy
  one across: the Linux cache holds Linux-only offsets.

## Layout

```
src/       main.py and the modules it imports (version.py is stamped by the release build)
scripts/   run.bat, build.bat, setup_windows.py / .ps1
tools/     offset scanning helpers (scan_offsets.py, scan_field_offset.py)
radar.spec, radar.manifest, requirements.txt   PyInstaller and pip config
```

`config.json`, `offsets_cache.json` and `radar.log` live in this folder (next to the exe when frozen).
