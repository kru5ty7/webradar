"""
One-shot Windows setup for CS2 WebRadar. Run from an *administrator* PowerShell:

    python setup_windows.py              # install whatever is missing
    python setup_windows.py --build      # ...and build the webapp (webapp/dist)
    python setup_windows.py --check      # only report what is missing

Each step checks first and installs only when needed, so re-running is safe:

  1. Python >= 3.10        (re-runs itself under the new Python if upgraded)
  2. Node.js >= 18 + npm   (winget: OpenJS.NodeJS.LTS)
  3. WebView2 runtime      (winget: Microsoft.EdgeWebView2Runtime) — used by
                           the minimap/overlay window (pywebview)
  4. Python packages       (requirements.txt, only missing/outdated ones)
  5. webapp npm packages   (npm ci, only when node_modules is missing/stale)

No Python on the machine at all? Run setup_windows.ps1 instead; it installs
Python and then runs this script.
"""
from __future__ import annotations

import argparse
import ctypes
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE         = Path(__file__).resolve().parent
WEBAPP       = HERE.parent / "webapp"
REQUIREMENTS = HERE / "requirements.txt"

MIN_PYTHON = (3, 10)   # main.py uses `X | None` annotations at runtime
MIN_NODE   = 18        # vite 4 needs >= 16; 18 is the oldest supported LTS

PYTHON_WINGET_ID   = "Python.Python.3.12"
PYTHON_LAUNCHER_V  = "-3.12"                 # `py -3.12` once that is installed
NODE_WINGET_ID     = "OpenJS.NodeJS.LTS"
WEBVIEW2_WINGET_ID = "Microsoft.EdgeWebView2Runtime"
WEBVIEW2_GUID      = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"

results: list[tuple[str, str]] = []   # (component, outcome) for the summary


# ── helpers ───────────────────────────────────────────────────────────────────
def say(msg: str) -> None:
    print(f"  {msg}", flush=True)


def step(title: str) -> None:
    print(f"\n[{title}]", flush=True)


def run(cmd: list[str], check: bool = True, **kw) -> subprocess.CompletedProcess:
    say("> " + " ".join(cmd))
    return subprocess.run(cmd, check=check, **kw)


def output(cmd: list[str]) -> str | None:
    """stdout of cmd, or None if it is missing or fails."""
    exe = shutil.which(cmd[0])
    if not exe:
        return None
    try:
        return subprocess.run([exe, *cmd[1:]], capture_output=True, text=True,
                              timeout=60, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def refresh_path() -> None:
    """Reload PATH from the registry so tools winget just installed are
    found without opening a new terminal."""
    import winreg
    parts = []
    for hive, key in ((winreg.HKEY_LOCAL_MACHINE,
                       r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                      (winreg.HKEY_CURRENT_USER, r"Environment")):
        try:
            with winreg.OpenKey(hive, key) as k:
                parts.append(os.path.expandvars(winreg.QueryValueEx(k, "Path")[0]))
        except OSError:
            pass
    if parts:
        os.environ["PATH"] = ";".join(parts) + ";" + os.environ.get("PATH", "")


def winget_install(package_id: str, check_only: bool) -> None:
    """Install via winget. Callers re-check the component afterwards instead
    of trusting the exit code (winget exits non-zero for "already current")."""
    if check_only:
        say(f"would install {package_id} via winget")
        return
    if not shutil.which("winget"):
        say("winget not found. Install 'App Installer' from the Microsoft Store, "
            f"or install {package_id} manually, then re-run this script.")
        return
    run(["winget", "install", "--id", package_id, "--exact", "--silent",
         "--accept-package-agreements", "--accept-source-agreements"], check=False)
    refresh_path()


# ── 1. Python ─────────────────────────────────────────────────────────────────
def find_new_python() -> str | None:
    """A Python >= MIN_PYTHON other than the one running, via the py launcher."""
    for args in (["py", PYTHON_LAUNCHER_V], ["py", "-3"]):
        out = output([*args, "-c", "import sys; print(sys.executable); print(sys.version_info[:2])"])
        if out:
            exe, ver = out.splitlines()[0], out.splitlines()[-1]
            if tuple(int(x) for x in re.findall(r"\d+", ver)[:2]) >= MIN_PYTHON:
                return exe
    return None


def ensure_python(check_only: bool) -> None:
    step("1/5 Python")
    cur = sys.version_info[:2]
    if cur >= MIN_PYTHON:
        say(f"OK: Python {cur[0]}.{cur[1]} ({sys.executable})")
        results.append(("Python", f"ok {cur[0]}.{cur[1]}"))
        return
    say(f"Python {cur[0]}.{cur[1]} is too old (need {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+)")
    if check_only:
        results.append(("Python", "needs upgrade"))
        return
    winget_install(PYTHON_WINGET_ID, check_only)
    new = find_new_python()
    if not new:
        sys.exit("  Python install did not produce a usable interpreter. Open a new "
                 "admin PowerShell and run: py -3 setup_windows.py")
    say(f"re-running under {new}")
    sys.exit(subprocess.call([new, str(Path(__file__).resolve()), *sys.argv[1:]]))


# ── 2. Node.js / npm ──────────────────────────────────────────────────────────
def node_major() -> int | None:
    out = output(["node", "--version"])           # e.g. "v20.11.1"
    m = re.match(r"v(\d+)", out or "")
    return int(m.group(1)) if m else None


def ensure_node(check_only: bool) -> bool:
    step("2/5 Node.js + npm")
    major = node_major()
    if major and major >= MIN_NODE and shutil.which("npm"):
        say(f"OK: Node {output(['node', '--version'])}, npm {output(['npm', '--version'])}")
        results.append(("Node.js", f"ok v{major}"))
        return True
    say("Node.js missing" if not major else f"Node v{major} is too old (need {MIN_NODE}+)")
    winget_install(NODE_WINGET_ID, check_only)
    major = node_major()
    ok = bool(major and major >= MIN_NODE and shutil.which("npm"))
    results.append(("Node.js", f"installed v{major}" if ok else "MISSING"))
    if not ok and not check_only:
        say("Node still not on PATH. Open a new admin PowerShell and re-run.")
    return ok


# ── 3. WebView2 runtime ───────────────────────────────────────────────────────
def webview2_version() -> str | None:
    import winreg
    for hive, key in ((winreg.HKEY_LOCAL_MACHINE,
                       rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_GUID}"),
                      (winreg.HKEY_LOCAL_MACHINE,
                       rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_GUID}"),
                      (winreg.HKEY_CURRENT_USER,
                       rf"Software\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_GUID}")):
        try:
            with winreg.OpenKey(hive, key) as k:
                v = winreg.QueryValueEx(k, "pv")[0]
                if v and v != "0.0.0.0":
                    return v
        except OSError:
            pass
    return None


def ensure_webview2(check_only: bool) -> None:
    step("3/5 WebView2 runtime (overlay / minimap window)")
    v = webview2_version()
    if v:
        say(f"OK: WebView2 {v}")
        results.append(("WebView2", f"ok {v}"))
        return
    say("WebView2 runtime missing")
    winget_install(WEBVIEW2_WINGET_ID, check_only)
    v = webview2_version()
    results.append(("WebView2", f"installed {v}" if v else "MISSING (overlay modes won't open)"))


# ── 4. Python packages ────────────────────────────────────────────────────────
def _ver_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3])


def missing_requirements() -> list[str]:
    from importlib import metadata
    need = []
    for line in REQUIREMENTS.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        m = re.match(r"([A-Za-z0-9_.\-]+)\s*(>=\s*([\d.]+))?", line)
        if not m:
            need.append(line)
            continue
        name, minimum = m.group(1), m.group(3)
        try:
            have = metadata.version(name)
        except metadata.PackageNotFoundError:
            need.append(line)
            continue
        if minimum and _ver_tuple(have) < _ver_tuple(minimum):
            need.append(line)
    return need


def ensure_pip_packages(check_only: bool) -> None:
    step("4/5 Python packages")
    need = missing_requirements()
    if not need:
        say("OK: all packages in requirements.txt are installed")
        results.append(("pip packages", "ok"))
        return
    say("missing/outdated: " + ", ".join(need))
    if check_only:
        results.append(("pip packages", f"{len(need)} missing"))
        return
    run([sys.executable, "-m", "pip", "install", "--upgrade", "pip"], check=False)
    run([sys.executable, "-m", "pip", "install", *need])
    left = missing_requirements()
    results.append(("pip packages", "installed" if not left else "MISSING " + ", ".join(left)))


# ── 5. webapp npm packages (+ optional build) ─────────────────────────────────
def npm_needs_install() -> bool:
    lock, installed = WEBAPP / "package-lock.json", WEBAPP / "node_modules" / ".package-lock.json"
    if not installed.exists():
        return True
    return lock.exists() and lock.stat().st_mtime > installed.stat().st_mtime


def ensure_npm_packages(check_only: bool, node_ok: bool, build: bool) -> None:
    step("5/5 webapp npm packages")
    if not (WEBAPP / "package.json").exists():
        say(f"webapp not found at {WEBAPP}; skipping")
        results.append(("npm packages", "skipped (no webapp)"))
        return
    npm = shutil.which("npm")
    if not node_ok or not npm:
        results.append(("npm packages", "skipped (no Node.js)"))
        return
    if not npm_needs_install():
        say("OK: node_modules is up to date with package-lock.json")
        results.append(("npm packages", "ok"))
    elif check_only:
        say("node_modules missing or older than package-lock.json")
        results.append(("npm packages", "need install"))
    else:
        has_lock = (WEBAPP / "package-lock.json").exists()
        run([npm, "ci" if has_lock else "install"], cwd=WEBAPP)
        results.append(("npm packages", "installed"))
    if build and not check_only:
        run([npm, "run", "build"], cwd=WEBAPP)
        results.append(("webapp build", "built webapp/dist"))


# ── main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="only report what is missing")
    ap.add_argument("--build", action="store_true", help="also build the webapp (npm run build)")
    args = ap.parse_args()

    if sys.platform != "win32":
        sys.exit("This setup script is for Windows.")
    if not args.check and not ctypes.windll.shell32.IsUserAnAdmin():
        sys.exit("Run this from an administrator PowerShell (right-click > Run as administrator).")

    print("CS2 WebRadar — Windows setup" + (" (check only)" if args.check else ""))
    ensure_python(args.check)
    node_ok = ensure_node(args.check)
    ensure_webview2(args.check)
    ensure_pip_packages(args.check)
    ensure_npm_packages(args.check, node_ok, args.build)

    print("\nSummary")
    for name, outcome in results:
        print(f"  {name:<14} {outcome}")
    if any("MISSING" in o for _, o in results):
        sys.exit(1)
    if not args.check:
        print("\nDone. Start the radar with run.bat (as administrator).")


if __name__ == "__main__":
    main()
