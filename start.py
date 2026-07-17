#!/usr/bin/env python3
"""CS2 WebRadar — cross-platform launcher.

Usage:
    python start.py                # auto-detect OS, ask mode
    python start.py normal         # skip mode prompt
    python start.py overlay
    python start.py esp
"""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT   = Path(__file__).parent.resolve()
WEBAPP = ROOT / "webapp"
BACK   = ROOT / "ai_agent_helper" / "main.py"

IS_LINUX   = sys.platform.startswith("linux")
IS_WINDOWS = sys.platform == "win32"


def _find_python() -> str:
    for candidate in [
        ROOT / ".venv" / ("Scripts" if IS_WINDOWS else "bin") / ("python.exe" if IS_WINDOWS else "python"),
        Path(sys.executable),
    ]:
        if Path(candidate).exists():
            return str(candidate)
    return sys.executable


def _find_npm() -> str:
    for name in (["npm.cmd", "npm"] if IS_WINDOWS else ["npm"]):
        if subprocess.run(["where" if IS_WINDOWS else "which", name],
                          capture_output=True).returncode == 0:
            return name
    return "npm"


def _vite_bin() -> Path | None:
    p = WEBAPP / "node_modules" / ".bin" / ("vite.cmd" if IS_WINDOWS else "vite")
    return p if p.exists() else None


def ask_mode(argv: list[str]) -> str:
    for a in argv[1:]:
        a = a.lstrip("-")
        if a in ("normal", "overlay", "esp", "minimap"):
            return a
    print("  Mode selection:")
    print("    1. Normal   — browser tab (default)")
    print("    2. Minimap  — small draggable overlay")
    print("    3. ESP      — full-screen overlay")
    choice = input("  Select [1-3, Enter=1]: ").strip()
    return {"2": "overlay", "3": "esp"}.get(choice, "normal")


def check_deps():
    if not WEBAPP.exists():
        sys.exit(f"[error] webapp/ directory not found at {WEBAPP}")
    if not BACK.exists():
        sys.exit(f"[error] ai_agent_helper/main.py not found")
    vite = _vite_bin()
    if not vite:
        sys.exit(
            f"[error] webapp node_modules missing.\n"
            f"        Run: cd {WEBAPP} && npm install"
        )
    try:
        import websockets  # noqa: F401
    except ImportError:
        sys.exit(
            "[error] Python package 'websockets' not installed.\n"
            "        Run: pip install -r ai_agent_helper/requirements.txt"
        )


# ── Linux ──────────────────────────────────────────────────────────────────────

def run_linux(mode: str):
    python = _find_python()
    vite   = str(_vite_bin())
    mode_flag = f"--{mode}"

    # Pre-authenticate sudo so the password prompt is interactive (not
    # hidden inside a backgrounded process).
    if os.geteuid() != 0:
        print("[sudo] The backend reads CS2 memory and needs root access.")
        ret = subprocess.run(["sudo", "-v"]).returncode
        if ret != 0:
            sys.exit("[error] sudo authentication failed")

    procs: list[subprocess.Popen] = []

    def _cleanup(sig=None, frame=None):
        print("\nShutting down...")
        for p in procs:
            try:
                p.terminate()
            except Exception:
                pass
        for p in procs:
            try:
                p.wait(timeout=5)
            except Exception:
                pass
        sys.exit(0)

    signal.signal(signal.SIGINT,  _cleanup)
    signal.signal(signal.SIGTERM, _cleanup)

    print("[1/2] Starting webapp: http://localhost:5173")
    vite_proc = subprocess.Popen(
        [vite, "--host", "0.0.0.0", "--port", "5173"],
        cwd=str(WEBAPP),
    )
    procs.append(vite_proc)
    time.sleep(1)

    backend_cmd = [python, str(BACK), mode_flag, "--no-funnel"]
    if os.geteuid() != 0:
        backend_cmd = ["sudo", "-n"] + backend_cmd  # -n = non-interactive (already authed)

    print(f"[2/2] Starting Python backend: {mode_flag}")
    back_proc = subprocess.Popen(backend_cmd)
    procs.append(back_proc)

    print(f"\nCS2 WebRadar is running.  Open: http://localhost:5173")
    print("Press Ctrl+C to stop.\n")

    # Wait; if either exits unexpectedly, shut everything down.
    while True:
        for p in procs:
            if p.poll() is not None:
                _cleanup()
        time.sleep(1)


# ── Windows ────────────────────────────────────────────────────────────────────

def run_windows(mode: str):
    python = _find_python()
    vite   = str(_vite_bin())
    mode_flag = f"--{mode}"

    procs: list[subprocess.Popen] = []

    def _cleanup(sig=None, frame=None):
        print("\nShutting down...")
        for p in procs:
            try:
                p.terminate()
            except Exception:
                pass
        sys.exit(0)

    signal.signal(signal.SIGINT,  _cleanup)
    signal.signal(signal.SIGTERM, _cleanup)

    print("[1/2] Starting webapp: http://localhost:5173")
    vite_proc = subprocess.Popen(
        [vite, "--host", "0.0.0.0", "--port", "5173"],
        cwd=str(WEBAPP),
    )
    procs.append(vite_proc)
    time.sleep(2)

    print(f"[2/2] Starting Python backend: {mode_flag}")
    back_proc = subprocess.Popen([python, str(BACK), mode_flag, "--no-funnel"])
    procs.append(back_proc)

    print(f"\nCS2 WebRadar is running.  Open: http://localhost:5173")
    print("Press Ctrl+C to stop.\n")

    while True:
        for p in procs:
            if p.poll() is not None:
                _cleanup()
        time.sleep(1)


# ── main ───────────────────────────────────────────────────────────────────────

def main():
    print("\n  CS2 WebRadar\n  " + "─" * 30)

    if IS_WINDOWS:
        import ctypes
        if not ctypes.windll.shell32.IsUserAnAdmin():
            sys.exit(
                "[error] Run this script as Administrator.\n"
                "        Right-click the script → 'Run as administrator'."
            )

    check_deps()
    mode = ask_mode(sys.argv)

    print(f"\n  OS: {'Linux' if IS_LINUX else 'Windows'}   mode: {mode}\n")

    if IS_LINUX:
        run_linux(mode)
    elif IS_WINDOWS:
        run_windows(mode)
    else:
        sys.exit(f"[error] Unsupported platform: {sys.platform}")


if __name__ == "__main__":
    main()
