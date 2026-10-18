# ai_agent_helper_windows

Windows-only fork of `../ai_agent_helper`. It has the same radar payload and
webapp contract, with every native-Linux code path removed (no `/proc` reads,
ELF scanner, live schema rescans, `chunk_scanner.so` or Linux field baseline).

On Windows the a2x/cs2-dumper offsets match `client.dll`, so they are used
directly. The on-disk PE signature scan patches any stale global RVAs.

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

## Differences from ai_agent_helper

- Exits straight away on a non-Windows host.
- `auto_update` is off by default. GitHub releases ship the cross-platform
  build, which would replace this fork.
- `offsets_cache.json` is separate from the Linux helper's cache. Don't copy
  one across: the Linux cache holds Linux-only offsets.
