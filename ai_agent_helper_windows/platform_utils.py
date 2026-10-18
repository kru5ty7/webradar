"""Platform discovery helpers for the CS2 radar tools."""
from __future__ import annotations

import os
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

CS2_APP_DIR = "Counter-Strike Global Offensive"


def _dedupe(paths):
    seen = set()
    out = []
    for path in paths:
        if not path:
            continue
        p = Path(path).expanduser()
        key = str(p).lower() if IS_WINDOWS else str(p)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _vdf_unescape(value: str) -> str:
    return value.replace("\\\\", "\\")


def _read_steam_library_paths(steam_root: Path) -> list[Path]:
    """Return Steam library roots from libraryfolders.vdf."""
    library_vdf = steam_root / "steamapps" / "libraryfolders.vdf"
    if not library_vdf.exists():
        return []

    roots: list[Path] = []
    try:
        for raw_line in library_vdf.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw_line.strip()
            parts = line.split('"')
            if len(parts) < 4:
                continue
            key = parts[1].lower()
            value = _vdf_unescape(parts[3])
            if key == "path" or key.isdigit():
                roots.append(Path(value))
    except Exception:
        return []
    return roots


def steam_roots() -> list[Path]:
    """Return candidate Steam installation/library roots for the current OS."""
    env_roots = []
    for var in ("STEAM_DIR", "STEAM_ROOT", "STEAM_HOME", "STEAM_COMPAT_CLIENT_INSTALL_PATH"):
        value = os.environ.get(var)
        if value:
            env_roots.append(Path(value))

    roots: list[Path] = []
    try:
        import winreg

        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for sub in (r"SOFTWARE\Valve\Steam", r"SOFTWARE\WOW6432Node\Valve\Steam"):
                try:
                    with winreg.OpenKey(hive, sub) as key:
                        roots.append(Path(winreg.QueryValueEx(key, "InstallPath")[0]))
                except Exception:
                    pass
    except Exception:
        pass

    for drive in ("C", "D", "E", "F", "G"):
        roots.extend([
            Path(f"{drive}:/Program Files (x86)/Steam"),
            Path(f"{drive}:/Program Files/Steam"),
            Path(f"{drive}:/Steam"),
        ])

    roots = _dedupe([*env_roots, *roots])

    # Add library roots referenced by each discovered Steam root.
    libraries: list[Path] = []
    for root in roots:
        libraries.extend(_read_steam_library_paths(root))

    return _dedupe([*roots, *libraries])


def steamapps_dirs() -> list[Path]:
    dirs = []
    for root in steam_roots():
        dirs.append(root if root.name.lower() == "steamapps" else root / "steamapps")
    return _dedupe(dirs)


def find_cs2_root() -> Path | None:
    """Return the CS2 install root, not the inner game/csgo directory."""
    for var in ("CS2_DIR", "CSGO_DIR"):
        value = os.environ.get(var)
        if value:
            candidate = Path(value).expanduser()
            if (candidate / "game" / "csgo").exists():
                return candidate
            if candidate.name == "csgo" and candidate.parent.name == "game":
                return candidate.parent.parent

    for steamapps in steamapps_dirs():
        candidate = steamapps / "common" / CS2_APP_DIR
        if (candidate / "game" / "csgo").exists():
            return candidate
    return None


def client_module_names() -> tuple[str, ...]:
    return ("client.dll",)


def client_module_aliases(requested: str) -> tuple[str, ...]:
    requested_l = requested.lower()
    names = {requested_l}
    if requested_l == "client.dll":
        names.update(name.lower() for name in client_module_names())
    return tuple(sorted(names))


def find_client_binary() -> Path | None:
    cs2 = find_cs2_root()
    if not cs2:
        return None

    candidates = [
        cs2 / "game" / "csgo" / "bin" / "win64" / "client.dll",
    ]
    for path in candidates:
        if path.exists():
            return path

    wanted = {name.lower() for name in client_module_names()}
    try:
        for path in cs2.rglob("*"):
            if path.is_file() and path.name.lower() in wanted:
                return path
    except Exception:
        return None
    return None


def cs2_process_names() -> tuple[str, ...]:
    return ("cs2.exe",)

