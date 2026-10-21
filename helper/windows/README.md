# helper/windows

Windows-only fork of `../linux`. It has the same radar payload and
webapp contract, with every native-Linux code path removed (no `/proc` reads,
ELF scanner, live schema rescans, `chunk_scanner.so` or Linux field baseline).

On Windows the a2x/cs2-dumper offsets match `client.dll`, so they are used
directly. The on-disk PE signature scan patches any stale global RVAs.

## First-time setup

From an **administrator** PowerShell in this folder:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_windows.ps1   # no Python yet? use this
python setup_windows.py            # or, if Python 3.10+ is already installed
python setup_windows.py --check    # just report what is missing
python setup_windows.py --build    # also build webapp/dist
```

It checks each item and installs only what is missing or too old, using
winget: Python 3.10+, Node.js 18+ with npm, the WebView2 runtime (needed by the
minimap/overlay window), the pip packages in `requirements.txt`, and the
webapp's npm packages. Re-running it is safe.

## Run

```bat
run.bat            :: as Administrator — installs deps, starts webapp + helper
:: or
pip install -r requirements.txt
python main.py --normal
```

## Build an exe

```bat
build.bat          :: -> dist\cs2-webradar.exe (copy config.json next to it)
```

## Differences from helper/linux

- Exits straight away on a non-Windows host.
- `auto_update` is off by default. GitHub releases ship the cross-platform
  build, which would replace this fork.
- `offsets_cache.json` is separate from the Linux helper's cache. Don't copy
  one across: the Linux cache holds Linux-only offsets.
