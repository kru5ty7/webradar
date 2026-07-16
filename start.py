#!/usr/bin/env python3
"""CS2 WebRadar — cross-platform launcher."""

import sys
import subprocess
import platform
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
RADAR_DIR  = Path.home() / "Workspaces" / "cs2-radar"
IS_LINUX   = sys.platform.startswith("linux")
IS_WINDOWS = sys.platform == "win32"

# ── helpers ───────────────────────────────────────────────────────────────────

def banner():
    print("\n" + "="*50)
    print("        CS2 WebRadar Launcher")
    print("="*50 + "\n")

def ask_os() -> str:
    detected = "Linux" if IS_LINUX else "Windows" if IS_WINDOWS else platform.system()
    print(f"Detected OS: {detected}")
    print()
    print("  1. Linux")
    print("  2. Windows")
    print()
    while True:
        choice = input("Select OS (1/2) or Enter to use detected: ").strip()
        if choice == "" or choice == "1" and IS_LINUX or choice == "2" and IS_WINDOWS:
            return detected
        if choice == "1":
            return "Linux"
        if choice == "2":
            return "Windows"
        print("  Enter 1 or 2")

def _run(*args, need_sudo=False):
    cmd = (["sudo"] + list(args)) if need_sudo else list(args)
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def kill_old():
    print("Killing old instances...")
    if IS_LINUX:
        _run("pkill", "-f", "tsx index.ts",  need_sudo=True)
        _run("pkill", "-f", "main.py",        need_sudo=True)
        _run("pkill", "-f", "vite")
        _run("fuser", "-k", "9001/tcp",       need_sudo=True)
        _run("fuser", "-k", "22006/tcp",      need_sudo=True)
        _run("fuser", "-k", "5173/tcp")
    else:
        _run("taskkill", "/F", "/IM", "node.exe",   "/T")
        _run("taskkill", "/F", "/IM", "python.exe", "/T")

# ── terminal openers ──────────────────────────────────────────────────────────

def open_linux_term(title: str, cmd: str):
    """Open a new xterm window running cmd."""
    full = f"{cmd}; echo '--- process ended ---'; read -p 'Press Enter to close'"
    subprocess.Popen(["xterm", "-T", title, "-geometry", "110x25", "-e", full])

def open_windows_term(title: str, cmd: str):
    """Open a new cmd window running cmd."""
    subprocess.Popen(
        f'start "{title}" cmd /K "{cmd}"',
        shell=True,
        cwd=str(SCRIPT_DIR)
    )

# ── Linux flow ────────────────────────────────────────────────────────────────

def run_linux():
    # Build avitran radar if needed
    radar_bin = RADAR_DIR / "target" / "release" / "radar"
    if not radar_bin.exists():
        print(f"Building Rust radar (first run, ~1-2 min)...")
        subprocess.run(["cargo", "build", "--release"], cwd=str(RADAR_DIR), check=True)
        print("Build done.\n")

    # Avitran radar (port 9001) — needs sudo
    print("Starting avitran radar on http://localhost:9001 ...")
    open_linux_term(
        "CS2 Radar Backend",
        f"cd '{RADAR_DIR}' && sudo env PATH=\"$PATH\" npx tsx index.ts"
    )
    time.sleep(3)

    # Webapp Vite (port 5173) — optional, skip if using avitran UI
    print("Starting web UI on http://localhost:5173 ...")
    open_linux_term(
        "CS2 Radar Web UI",
        f"cd '{SCRIPT_DIR}/webapp' && npx vite"
    )

    print("\n" + "="*50)
    print("  Avitran UI : http://localhost:9001")
    print("  Webradar UI: http://localhost:5173")
    print("="*50)
    print("\nJoin a CS2 match for data to appear.")

# ── Windows flow ──────────────────────────────────────────────────────────────

def run_windows():
    venv_python = SCRIPT_DIR / ".venv" / "Scripts" / "python.exe"
    if not venv_python.exists():
        venv_python = Path(sys.executable)

    # Python main.py backend (port 22006)
    print("Starting CS2 radar backend (main.py) ...")
    open_windows_term(
        "CS2 Radar Backend",
        f'"{venv_python}" "{SCRIPT_DIR / "ai_agent_helper" / "main.py"}" --normal'
    )
    time.sleep(2)

    # Webapp Vite (port 5173)
    npm = "npm"
    print("Starting web UI on http://localhost:5173 ...")
    open_windows_term(
        "CS2 Radar Web UI",
        f"cd /d \"{SCRIPT_DIR / 'webapp'}\" && {npm} run dev"
    )

    print("\n" + "="*50)
    print("  Radar UI: http://localhost:5173")
    print("="*50)
    print("\nJoin a CS2 match for data to appear.")

# ── main ──────────────────────────────────────────────────────────────────────

def main():
    banner()
    chosen_os = ask_os()
    print()
    kill_old()
    time.sleep(1)
    print()

    if chosen_os == "Linux":
        run_linux()
    elif chosen_os == "Windows":
        run_windows()
    else:
        print(f"Unsupported OS: {chosen_os}")
        sys.exit(1)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled.")
