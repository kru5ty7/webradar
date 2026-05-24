"""
CS2 Radar ESP — launches the compiled cs2-external-esp.exe.
The C++ binary handles its own transparent DX11 overlay and memory reading.
"""
import logging
import os
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("radar")


def _esp_exe_path() -> Path:
    """Find the bundled esp exe whether running frozen or from source."""
    if getattr(sys, "frozen", False):
        # PyInstaller extracts datas to sys._MEIPASS
        return Path(sys._MEIPASS) / "esp_bin" / "cs2-external-esp.exe"
    # Running from source — relative to this file
    return Path(__file__).parent.parent / "cs2-external-esp" / "x64" / "Release" / "cs2-external-esp.exe"


def start(_url: str = ""):
    """Launch the C++ ESP exe. Blocks until it exits."""
    exe = _esp_exe_path()
    if not exe.exists():
        log.error("esp: cs2-external-esp.exe not found at %s", exe)
        print(f"\n[ERROR] ESP binary not found: {exe}\n")
        return

    log.info("esp: launching %s", exe)
    print(f"  Launching CS2 ESP overlay...\n  ({exe.name})\n  Close its window or press Ctrl+C to stop.\n")

    try:
        proc = subprocess.run([str(exe)], cwd=str(exe.parent))
        log.info("esp: exited with code %d", proc.returncode)
    except KeyboardInterrupt:
        log.info("esp: interrupted by user")
    except Exception as e:
        log.error("esp: failed to launch: %s", e)
        print(f"[ERROR] Could not launch ESP: {e}\n")
