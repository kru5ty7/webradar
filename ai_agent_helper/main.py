#!/usr/bin/env python3
"""
cs2_radar — combined CS2 memory reader + WebSocket server + HTTP static server.
Double-click the compiled exe to start everything; a browser tab opens automatically.
"""
import asyncio
import ctypes
import errno
import socket
import http.server
import json
import logging
import logging.handlers
import math
import os
import shutil
import struct
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

from platform_utils import (
    IS_LINUX,
    IS_WINDOWS,
    client_module_aliases,
    cs2_process_names,
    find_client_binary,
    find_client_binary_from_pid,
    find_cs2_root,
    find_cs2_root_from_pid,
)

if IS_WINDOWS:
    import ctypes.wintypes as wintypes
else:
    wintypes = None

try:
    import websockets
except ImportError:
    print("[error] websockets not installed. Run: pip install websockets")
    sys.exit(1)

try:
    from version import VERSION
except ImportError:
    VERSION = "v12"

GITHUB_REPO = "kru5ty7/webradar"
GITHUB_API  = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

# ── config ────────────────────────────────────────────────────────────────────
# When frozen (PyInstaller onefile), __file__ is inside the temp _MEIPASS dir
# which is deleted on exit. Use sys.executable's dir so config persists next to the exe.
ROOT = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
CONFIG_FILE = ROOT / "config.json"
CACHE_FILE  = ROOT / "offsets_cache.json"
LOG_FILE    = ROOT / "radar.log"

WS_PORT       = 22006
HTTP_PORT     = 5173   # built-dist server; same port as Vite so URLs always match

def _get_local_ip() -> str:
    """Return this machine's LAN IP by probing a UDP route (no packet sent)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "localhost"

_LOCAL_IP = _get_local_ip()   # cached once at startup

def _get_tailscale_ip() -> str | None:
    """Return the Tailscale IP (100.64.0.0/10) if Tailscale is running, else None."""
    # Try the CLI first — most reliable
    try:
        import subprocess
        r = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True, text=True, timeout=3
        )
        ip = r.stdout.strip()
        if r.returncode == 0 and ip:
            return ip
    except Exception:
        pass
    # Fallback: scan network interfaces for Tailscale's CGNAT range 100.64–100.127
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            parts = ip.split(".")
            if len(parts) == 4 and parts[0] == "100" and 64 <= int(parts[1]) <= 127:
                return ip
    except Exception:
        pass
    return None

_TAILSCALE_IP = _get_tailscale_ip()   # None if Tailscale not running

# ── Tailscale Funnel ──────────────────────────────────────────────────────────

def _tailscale_installed() -> bool:
    import shutil
    found = shutil.which("tailscale") is not None
    log.debug("tailscale installed: %s", found)
    return found

def _download_with_progress(url: str, dest: str):
    import time
    log.info("tailscale: downloading %s → %s", url, dest)
    start = time.time()
    def _hook(count, block, total):
        done = count * block
        elapsed = max(time.time() - start, 0.001)
        speed = done / elapsed
        if total > 0:
            pct = min(100, done * 100 // total)
            bar = "█" * (pct // 3) + "░" * (33 - pct // 3)
            print(f"\r  [{bar}] {pct:3d}%  {speed/1024:6.0f} KB/s", end="", flush=True)
        else:
            print(f"\r  {done//1024} KB  {speed/1024:.0f} KB/s", end="", flush=True)
    try:
        urllib.request.urlretrieve(url, dest, _hook)
        print()
        elapsed = time.time() - start
        log.info("tailscale: download complete in %.1fs", elapsed)
    except Exception:
        print()
        log.exception("tailscale: download failed")
        raise

def _install_tailscale():
    import subprocess, tempfile

    if IS_WINDOWS:
        log.info("tailscale: attempting install via winget")
        print("  Installing Tailscale via winget...")
        try:
            r = subprocess.run(
                ["winget", "install", "--id", "Tailscale.Tailscale",
                 "--silent", "--accept-package-agreements", "--accept-source-agreements"],
                capture_output=True, text=True, timeout=120
            )
            log.info("tailscale: winget exit=%d stdout=%s stderr=%s",
                     r.returncode, r.stdout.strip()[:200], r.stderr.strip()[:200])
            if r.returncode == 0:
                log.info("tailscale: winget install succeeded")
                return
        except Exception:
            log.exception("tailscale: winget raised an exception")

        log.warning("tailscale: winget failed — falling back to MSI download")
        print("  winget failed — downloading MSI installer...")
        msi = tempfile.mktemp(suffix=".exe")
        try:
            _download_with_progress(
                "https://pkgs.tailscale.com/stable/tailscale-setup-latest.exe", msi
            )
            r2 = subprocess.run([msi, "/quiet"], capture_output=True, text=True, timeout=120)
            log.info("tailscale: MSI install exit=%d stderr=%s", r2.returncode, r2.stderr.strip()[:200])
        except Exception:
            log.exception("tailscale: MSI install raised an exception")
        finally:
            try:
                os.unlink(msi)
            except Exception:
                pass
        return

    if IS_LINUX:
        def _with_sudo(args: list[str]) -> list[str] | None:
            if hasattr(os, "geteuid") and os.geteuid() == 0:
                return args
            sudo = shutil.which("sudo")
            return [sudo, "-n", *args] if sudo else None

        def _run(args: list[str], timeout: int = 180) -> bool:
            try:
                r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
                log.info("tailscale: %s exit=%d stdout=%s stderr=%s",
                         args[0], r.returncode, r.stdout.strip()[:200], r.stderr.strip()[:200])
                return r.returncode == 0
            except Exception:
                log.exception("tailscale: command failed: %s", args)
                return False

        installers: list[list[str]] = []
        if shutil.which("apt-get"):
            update = _with_sudo(["apt-get", "update"])
            install = _with_sudo(["apt-get", "install", "-y", "tailscale"])
            if update and install:
                installers.extend([update, install])
        elif shutil.which("dnf"):
            cmd = _with_sudo(["dnf", "install", "-y", "tailscale"])
            if cmd:
                installers.append(cmd)
        elif shutil.which("yum"):
            cmd = _with_sudo(["yum", "install", "-y", "tailscale"])
            if cmd:
                installers.append(cmd)
        elif shutil.which("pacman"):
            cmd = _with_sudo(["pacman", "-Sy", "--noconfirm", "tailscale"])
            if cmd:
                installers.append(cmd)
        elif shutil.which("zypper"):
            cmd = _with_sudo(["zypper", "--non-interactive", "install", "tailscale"])
            if cmd:
                installers.append(cmd)

        if installers:
            print("  Installing Tailscale with the system package manager...")
            if all(_run(cmd) for cmd in installers):
                service = _with_sudo(["systemctl", "enable", "--now", "tailscaled"]) if shutil.which("systemctl") else None
                if service:
                    _run(service, timeout=60)
                return

        log.warning("tailscale: automatic Linux install unavailable or failed")
        print("  Tailscale install failed. Install it with your distro package manager, then rerun.\n")
        return

    log.warning("tailscale: automatic install unsupported on %s", sys.platform)

def _tailscale_logged_in() -> bool:
    import subprocess
    try:
        r = subprocess.run(["tailscale", "status", "--json"],
                           capture_output=True, text=True, timeout=5)
        log.debug("tailscale status exit=%d", r.returncode)
        if r.returncode != 0:
            log.warning("tailscale: status check failed (exit %d): %s", r.returncode, r.stderr.strip())
            return False
        data = json.loads(r.stdout)
        state = data.get("BackendState", "unknown")
        log.debug("tailscale BackendState: %s", state)
        return state == "Running"
    except Exception:
        log.exception("tailscale: logged-in check raised an exception")
        return False

def _get_funnel_url() -> str | None:
    import subprocess
    try:
        r = subprocess.run(["tailscale", "status", "--json"],
                           capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            log.warning("tailscale: status failed when fetching funnel URL")
            return None
        data = json.loads(r.stdout)
        dns = data.get("Self", {}).get("DNSName", "").rstrip(".")
        if dns:
            url = f"https://{dns}"
            log.info("tailscale: funnel URL = %s", url)
            return url
        log.warning("tailscale: DNSName not found in status output")
    except Exception:
        log.exception("tailscale: _get_funnel_url raised an exception")
    return None

def _start_funnel(port: int = None):
    import subprocess
    p = port or HTTP_PORT
    log.info("tailscale: enabling funnel on port %d", p)
    try:
        # Step 1: serve — register local port with Tailscale (exits quickly with --bg)
        r1 = subprocess.run(
            ["tailscale", "serve", "--bg", str(p)],
            capture_output=True, text=True, timeout=15,
        )
        log.info("tailscale: serve --bg exit=%d stdout=%s stderr=%s",
                 r1.returncode, r1.stdout.strip()[:200], r1.stderr.strip()[:200])

        # Step 2: funnel — expose serve endpoint publicly (exits quickly with --bg)
        r2 = subprocess.run(
            ["tailscale", "funnel", "--bg", str(p)],
            capture_output=True, text=True, timeout=15,
        )
        log.info("tailscale: funnel --bg exit=%d stdout=%s stderr=%s",
                 r2.returncode, r2.stdout.strip()[:200], r2.stderr.strip()[:200])
    except Exception:
        log.exception("tailscale: _start_funnel raised an exception")

def _setup_tailscale(cfg: dict) -> str | None:
    """First-run interactive Tailscale Funnel setup. Returns public URL or None."""
    log.info("tailscale: setup called (tailscale_funnel in cfg: %s)",
             "tailscale_funnel" in cfg)
    try:
        if "tailscale_funnel" in cfg:
            if not cfg["tailscale_funnel"]:
                log.info("tailscale: funnel disabled in config — skipping")
                return None
            log.info("tailscale: funnel previously enabled — re-activating")
            if _tailscale_installed() and _tailscale_logged_in():
                _start_funnel()
                return _get_funnel_url()
            log.warning("tailscale: not installed or not logged in — cannot re-activate")
            return None

        # First time — ask user
        print("\n" + "=" * 50)
        print("  Tailscale Funnel — Global Access")
        print("=" * 50)
        print("  Let friends open the radar from ANYWHERE.")
        print("  (They just need a browser — no install.)")
        print("=" * 50)
        try:
            choice = input("  Enable Tailscale Funnel? (y/N): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            choice = "n"

        enabled = choice == "y"
        log.info("tailscale: user chose enabled=%s", enabled)
        cfg["tailscale_funnel"] = enabled
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2))

        if not enabled:
            print("  Skipped. Edit config.json to enable later.\n")
            return None

        if not _tailscale_installed():
            _install_tailscale()
            if not _tailscale_installed():
                log.error("tailscale: install failed — tailscale CLI still not found after install")
                print("  Tailscale install failed — skipping Funnel.\n")
                return None

        if not _tailscale_logged_in():
            import subprocess
            log.info("tailscale: not logged in — opening browser login")
            print("  Opening Tailscale login in your browser...")
            try:
                subprocess.run(["tailscale", "login"], timeout=120)
            except Exception:
                log.exception("tailscale: login command raised an exception")
            if not _tailscale_logged_in():
                log.error("tailscale: still not logged in after login attempt")
                print("  Login incomplete — skipping Funnel.\n")
                return None

        _start_funnel()
        url = _get_funnel_url()
        if url:
            log.info("tailscale: funnel active at %s", url)
            print(f"  Funnel active: {url}\n")
        else:
            log.warning("tailscale: funnel started but could not determine public URL")
        return url

    except Exception:
        log.exception("tailscale: _setup_tailscale raised an unexpected exception")
        return None

_FUNNEL_URL: str | None = None   # assigned in __main__ after _setup_tailscale()

POLL_INTERVAL = 0.033  # ~30 Hz
CACHE_MAX_AGE = 3600
DUMPER_BASE   = "https://raw.githubusercontent.com/a2x/cs2-dumper/main/output"

# ── logging ───────────────────────────────────────────────────────────────────
def _setup_logging() -> logging.Logger:
    fmt = logging.Formatter(
        fmt="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    log = logging.getLogger("radar")
    log.setLevel(logging.DEBUG)

    # Console — INFO and above, coloured level tag
    _COLOURS = {
        "DEBUG":    "\033[36m",   # cyan
        "INFO":     "\033[32m",   # green
        "WARNING":  "\033[33m",   # yellow
        "ERROR":    "\033[31m",   # red
        "CRITICAL": "\033[35m",   # magenta
    }
    _RESET = "\033[0m"

    class _ColouredFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            colour = _COLOURS.get(record.levelname, "")
            record.levelname = f"{colour}{record.levelname:<8}{_RESET}"
            return super().format(record)

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG)
    console.setFormatter(_ColouredFormatter(
        fmt="%(asctime)s  %(levelname)s  %(message)s",
        datefmt="%H:%M:%S",
    ))
    log.addHandler(console)

    # Rotating file — DEBUG and above, plain text, keeps last 2 × 1 MB
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=1_000_000, backupCount=2, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    log.addHandler(file_handler)

    # Silence noisy websocket-client internal chatter
    logging.getLogger("websocket").setLevel(logging.WARNING)
    # Suppress spurious "did not receive a valid HTTP request" from plain-HTTP
    # probes hitting the WS port (non-fatal, server keeps running fine)
    logging.getLogger("websockets.server").setLevel(logging.CRITICAL)
    logging.getLogger("websockets.asyncio.server").setLevel(logging.CRITICAL)

    return log

log = _setup_logging()

# ── memory ────────────────────────────────────────────────────────────────────
class _MemoryPrimitives:
    def ptr(self, address: int) -> int:
        v = struct.unpack_from("<Q", self._read(address, 8))[0]
        # Require a plausible canonical user-space pointer.
        return v if 0x1000 <= v < 0x7FFFFFFFFFFF else 0

    def u64(self, address: int) -> int:
        return struct.unpack_from("<Q", self._read(address, 8))[0]

    def u32(self, address: int) -> int:
        return struct.unpack_from("<I", self._read(address, 4))[0]

    def i32(self, address: int) -> int:
        return struct.unpack_from("<i", self._read(address, 4))[0]

    def f32(self, address: int) -> float:
        return struct.unpack_from("<f", self._read(address, 4))[0]

    def bool8(self, address: int) -> bool:
        d = self._read(address, 1)
        return bool(d[0])

    def cstring(self, address: int, max_len: int = 256) -> str:
        if not address:
            return ""
        data = self._read(address, max_len)
        end  = data.find(b"\x00")
        raw  = data[:end] if end != -1 else data
        return raw.decode("utf-8", errors="ignore")

    def msvc_string(self, address: int) -> str:
        """Read an MSVC std::string object stored at address in game memory."""
        if not address:
            return ""
        size = self.u32(address + 0x10)
        if size == 0 or size > 512:
            return ""
        if size < 16:
            raw = self._read(address, size)
        else:
            ptr = self.ptr(address)
            if not ptr:
                return ""
            raw = self._read(ptr, size)
        return raw.decode("utf-8", errors="ignore").rstrip("\x00")

    def string_field(self, address: int) -> str:
        """Read a schema string field — tries char* first, then MSVC string."""
        ptr = self.ptr(address)
        if ptr:
            s = self.cstring(ptr)
            if s:
                return s
        return self.msvc_string(address)


if IS_WINDOWS:
    # ── Windows Toolhelp + ReadProcessMemory API ─────────────────────────────
    TH32CS_SNAPPROCESS  = 0x00000002
    TH32CS_SNAPMODULE   = 0x00000008
    TH32CS_SNAPMODULE32 = 0x00000010
    PROCESS_VM_READ     = 0x0010
    PROCESS_QUERY_INFO  = 0x0400
    INVALID_HANDLE      = ctypes.c_void_p(-1).value

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize",              wintypes.DWORD),
            ("cntUsage",            wintypes.DWORD),
            ("th32ProcessID",       wintypes.DWORD),
            ("th32DefaultHeapID",   ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID",        wintypes.DWORD),
            ("cntThreads",          wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase",      wintypes.LONG),
            ("dwFlags",             wintypes.DWORD),
            ("szExeFile",           ctypes.c_char * 260),
        ]

    class MODULEENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize",        wintypes.DWORD),
            ("th32ModuleID",  wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("GlblcntUsage",  wintypes.DWORD),
            ("ProccntUsage",  wintypes.DWORD),
            ("modBaseAddr",   ctypes.POINTER(wintypes.BYTE)),
            ("modBaseSize",   wintypes.DWORD),
            ("hModule",       wintypes.HMODULE),
            ("szModule",      ctypes.c_char * 256),
            ("szExePath",     ctypes.c_char * 260),
        ]

    class Memory(_MemoryPrimitives):
        def __init__(self):
            self.handle = None
            self.pid    = None

        def find_pid(self, name: str) -> int | None:
            wanted = {name.lower()}
            if name.lower() == "cs2.exe":
                wanted.update(n.lower() for n in cs2_process_names())

            snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
            if snap == INVALID_HANDLE:
                return None
            entry = PROCESSENTRY32()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
            try:
                if kernel32.Process32First(snap, ctypes.byref(entry)):
                    while True:
                        if entry.szExeFile.decode(errors="ignore").lower() in wanted:
                            return entry.th32ProcessID
                        if not kernel32.Process32Next(snap, ctypes.byref(entry)):
                            break
            finally:
                kernel32.CloseHandle(snap)
            return None

        def open(self, pid: int) -> bool:
            self.pid    = pid
            self.handle = kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFO, False, pid)
            return bool(self.handle)

        def close(self):
            if self.handle:
                kernel32.CloseHandle(self.handle)
                self.handle = None
                self.pid    = None

        def get_module_base(self, dll: str) -> int:
            snap = kernel32.CreateToolhelp32Snapshot(
                TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, self.pid)
            if snap == INVALID_HANDLE:
                return 0
            entry = MODULEENTRY32()
            entry.dwSize = ctypes.sizeof(MODULEENTRY32)
            try:
                if kernel32.Module32First(snap, ctypes.byref(entry)):
                    while True:
                        if entry.szModule.decode(errors="ignore").lower() in client_module_aliases(dll):
                            return ctypes.cast(entry.modBaseAddr, ctypes.c_void_p).value or 0
                        if not kernel32.Module32Next(snap, ctypes.byref(entry)):
                            break
            finally:
                kernel32.CloseHandle(snap)
            return 0

        def _read(self, address: int, size: int) -> bytes:
            if not address:
                return bytes(size)
            buf  = ctypes.create_string_buffer(size)
            read = ctypes.c_size_t()
            ok   = kernel32.ReadProcessMemory(
                self.handle, ctypes.c_void_p(address), buf, size, ctypes.byref(read))
            if not ok:
                err = ctypes.get_last_error()
                # ERROR_INVALID_HANDLE (6) or ERROR_ACCESS_DENIED (5) = process gone
                if err in (5, 6):
                    raise OSError(err, f"ReadProcessMemory failed: winerror={err} (process died)")
                # ERROR_PARTIAL_COPY (299) is normal at page boundaries; return zeros.
            return buf.raw

elif IS_LINUX:
    class _IOVec(ctypes.Structure):
        _fields_ = [
            ("iov_base", ctypes.c_void_p),
            ("iov_len", ctypes.c_size_t),
        ]

    class Memory(_MemoryPrimitives):
        def __init__(self):
            self.handle = None
            self.pid: int | None = None
            self._mem_fd: int | None = None
            self._process_vm_readv = self._load_process_vm_readv()

        def _load_process_vm_readv(self):
            try:
                libc = ctypes.CDLL("libc.so.6", use_errno=True)
                fn = libc.process_vm_readv
                fn.argtypes = [
                    ctypes.c_int,
                    ctypes.POINTER(_IOVec),
                    ctypes.c_ulong,
                    ctypes.POINTER(_IOVec),
                    ctypes.c_ulong,
                    ctypes.c_ulong,
                ]
                fn.restype = ctypes.c_ssize_t
                return fn
            except Exception:
                return None

        def find_pid(self, name: str) -> int | None:
            wanted = {name.lower()}
            if name.lower() in {"cs2.exe", "cs2"}:
                wanted.update(n.lower() for n in cs2_process_names())

            def _basename(value: str) -> str:
                return Path(value.replace("\\", "/")).name.lower()

            proc_root = Path("/proc")
            try:
                pids = sorted((int(p.name), p) for p in proc_root.iterdir() if p.name.isdigit())
            except Exception:
                return None

            for _pid, proc in pids:
                names = set()
                try:
                    comm = (proc / "comm").read_text(errors="ignore").strip()
                    if comm:
                        names.add(comm.lower())
                except Exception:
                    pass
                try:
                    exe = os.readlink(proc / "exe")
                    names.add(_basename(exe))
                except Exception:
                    pass
                try:
                    cmdline = (proc / "cmdline").read_bytes().split(b"\x00", 1)[0]
                    if cmdline:
                        names.add(_basename(cmdline.decode(errors="ignore")))
                except Exception:
                    pass
                if names & wanted:
                    return _pid
            return None

        def open(self, pid: int) -> bool:
            self.pid = pid
            self.handle = True

            try:
                self._mem_fd = os.open(f"/proc/{pid}/mem", os.O_RDONLY)
            except PermissionError:
                self._mem_fd = None
            except OSError:
                self._mem_fd = None

            probe = self._first_readable_address()
            if probe:
                try:
                    self._read(probe, 1)
                    return True
                except PermissionError as exc:
                    log.error(
                        "Linux memory read denied for pid=%d (%s). Run as root or grant ptrace access "
                        "(for example CAP_SYS_PTRACE / ptrace_scope settings).",
                        pid, exc,
                    )
                except OSError as exc:
                    log.error("Linux memory read probe failed for pid=%d: %s", pid, exc)

            self.close()
            return False

        def close(self):
            if self._mem_fd is not None:
                try:
                    os.close(self._mem_fd)
                except OSError:
                    pass
            self._mem_fd = None
            self.handle = None
            self.pid = None

        def _maps_lines(self) -> list[str]:
            if not self.pid:
                return []
            try:
                return Path(f"/proc/{self.pid}/maps").read_text(errors="ignore").splitlines()
            except Exception:
                return []

        def _first_readable_address(self) -> int:
            for line in self._maps_lines():
                parts = line.split(maxsplit=5)
                if len(parts) < 2 or "r" not in parts[1]:
                    continue
                try:
                    return int(parts[0].split("-", 1)[0], 16)
                except ValueError:
                    continue
            return 0

        def get_module_base(self, dll: str) -> int:
            aliases = client_module_aliases(dll)
            fallback = 0
            for line in self._maps_lines():
                parts = line.split(maxsplit=5)
                if len(parts) < 6:
                    continue
                addr_range, _perms, offset, _dev, _inode, path = parts
                clean_path = path.removesuffix(" (deleted)")
                name = Path(clean_path).name.lower()
                if name not in aliases:
                    continue
                try:
                    start = int(addr_range.split("-", 1)[0], 16)
                    file_offset = int(offset, 16)
                except ValueError:
                    continue
                if file_offset == 0:
                    return start
                fallback = start if not fallback else min(fallback, start)
            return fallback

        def _read_process_vm(self, address: int, size: int) -> bytes:
            local = ctypes.create_string_buffer(size)
            local_iov = _IOVec(ctypes.cast(local, ctypes.c_void_p), size)
            remote_iov = _IOVec(ctypes.c_void_p(address), size)
            nread = self._process_vm_readv(
                int(self.pid),
                ctypes.byref(local_iov),
                1,
                ctypes.byref(remote_iov),
                1,
                0,
            )
            if nread < 0:
                err = ctypes.get_errno()
                if err in (errno.EPERM, errno.EACCES):
                    raise PermissionError(err, os.strerror(err))
                if err in (errno.ESRCH, errno.EINVAL):
                    raise OSError(err, os.strerror(err))
                return bytes(size)
            data = local.raw[:nread]
            return data.ljust(size, b"\x00")

        def _read_mem_file(self, address: int, size: int) -> bytes:
            if self._mem_fd is None:
                self._mem_fd = os.open(f"/proc/{self.pid}/mem", os.O_RDONLY)
            try:
                data = os.pread(self._mem_fd, size, address)
            except PermissionError:
                raise
            except OSError as exc:
                if exc.errno in (errno.EPERM, errno.EACCES):
                    raise PermissionError(exc.errno, exc.strerror)
                if exc.errno in (errno.ESRCH, errno.EINVAL, errno.EIO):
                    return bytes(size)
                raise
            return data.ljust(size, b"\x00")

        def _read(self, address: int, size: int) -> bytes:
            if not address:
                return bytes(size)
            if not self.pid:
                raise OSError(errno.ESRCH, "process is not open")

            if self._process_vm_readv is not None:
                data = self._read_process_vm(address, size)
                if data:
                    return data

            return self._read_mem_file(address, size)

else:
    class Memory(_MemoryPrimitives):
        def __init__(self):
            self.handle = None
            self.pid = None

        def find_pid(self, _name: str) -> int | None:
            return None

        def open(self, _pid: int) -> bool:
            log.error("unsupported platform for process memory access: %s", sys.platform)
            return False

        def close(self):
            self.handle = None
            self.pid = None

        def get_module_base(self, _dll: str) -> int:
            return 0

        def _read(self, _address: int, size: int) -> bytes:
            return bytes(size)


# ── offset loading ────────────────────────────────────────────────────────────
def _fetch(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=12) as r:
        return json.loads(r.read())


def _parse_fields(raw: dict) -> dict:
    """Normalize cs2-dumper client_dll.json into {ClassName: {field: offset}}."""
    # Unwrap {"client.dll": {"classes": {...}}} — actual format from cs2-dumper
    if len(raw) == 1:
        inner = next(iter(raw.values()))
        if isinstance(inner, dict):
            raw = inner

    # Unwrap {"classes": {...}, "enums": {...}}
    if "classes" in raw and isinstance(raw["classes"], dict):
        raw = raw["classes"]

    out = {}
    for cls, data in raw.items():
        if not isinstance(data, dict):
            continue
        fields = {}
        inner_fields = data.get("fields", data)  # prefer explicit "fields" key

        for fname, fdata in inner_fields.items():
            if isinstance(fdata, int):
                fields[fname] = fdata
            elif isinstance(fdata, dict):
                off = fdata.get("offset") or fdata.get("value", 0)
                if off:
                    fields[fname] = off

        if fields:
            out[cls] = fields

    log.debug("_parse_fields: parsed %d classes", len(out))
    return out


def load_offsets() -> dict:
    dll_path  = _find_client_dll()
    dll_mtime = dll_path.stat().st_mtime if dll_path else 0

    if CACHE_FILE.exists():
        try:
            cached   = json.loads(CACHE_FILE.read_text())
            cache_ts = cached.get("_ts", 0)
            # Cache is valid as long as client.dll hasn't changed since it was written.
            # No time-based expiry — offsets only go stale when the game updates.
            if cache_ts > dll_mtime:
                log.info("using cached offsets (client.dll unchanged)")
                return cached
            log.info("client.dll updated since last cache — rescanning...")
        except Exception:
            pass

    log.info("fetching offsets from cs2-dumper...")
    result = None
    try:
        raw_off    = _fetch(f"{DUMPER_BASE}/offsets.json")
        raw_client = _fetch(f"{DUMPER_BASE}/client_dll.json")

        client_globals = (
            raw_off.get("client.dll") or
            raw_off.get("offsets", {}).get("client.dll") or {}
        )

        result = {
            "_ts":     time.time(),
            "globals": client_globals,
            "fields":  _parse_fields(raw_client),
        }
        log.info("offsets fetched from cs2-dumper")
    except Exception as exc:
        log.warning("fetch failed: %s", exc)
        if CACHE_FILE.exists():
            log.info("falling back to cached offsets")
            result = json.loads(CACHE_FILE.read_text())
        else:
            log.critical("no cached offsets and fetch failed — cannot continue")
            sys.exit(1)

    # Always run local scanner to patch any offsets cs2-dumper may have wrong
    # for the currently installed client.dll build
    log.info("running local binary scan to verify/patch offsets...")
    _scan_globals_from_dll(result)   # saves cache internally
    return result

def _find_client_dll(pid: int | None = None) -> Path | None:
    """Locate the client module on disk for cache invalidation/local scans."""
    path = find_client_binary()
    if path:
        return path
    if IS_LINUX and pid:
        return find_client_binary_from_pid(pid)
    return None


# How often the main loop re-checks whether CS2's client module changed on disk
# (i.e. the game was updated) so it can auto-refresh offsets without a restart.
OFFSET_RECHECK_SEC = 600.0

def _client_dll_mtime(pid: int | None = None) -> float:
    """On-disk mtime of the client module — the ground-truth signal that the game
    was updated and offsets may have moved. 0.0 if it can't be located."""
    p = _find_client_dll(pid)
    try:
        return p.stat().st_mtime if p else 0.0
    except OSError:
        return 0.0


def _scan_globals_from_elf(path: Path, offsets: dict) -> bool:
    """
    Scan a Linux ELF client module for global offsets using RIP-relative store patterns.
    Equivalent to the PE scanner but for native Linux CS2 (libclient.so / client_client.so).
    """
    try:
        data = path.read_bytes()
    except Exception as exc:
        log.warning("offset scanner ELF: could not read %s: %s", path.name, exc)
        return False

    if data[:4] != b'\x7fELF':
        return False

    try:
        e_shoff     = struct.unpack_from('<Q', data, 0x28)[0]
        e_shentsize = struct.unpack_from('<H', data, 0x3A)[0]
        e_shnum     = struct.unpack_from('<H', data, 0x3C)[0]
    except struct.error:
        return False

    exec_sections = []
    for i in range(e_shnum):
        b = e_shoff + i * e_shentsize
        sh_flags  = struct.unpack_from('<Q', data, b + 8)[0]
        sh_addr   = struct.unpack_from('<Q', data, b + 16)[0]
        sh_offset = struct.unpack_from('<Q', data, b + 24)[0]
        sh_size   = struct.unpack_from('<Q', data, b + 32)[0]
        if (sh_flags & 0x4) and sh_size:   # SHF_EXECINSTR
            exec_sections.append((sh_offset, sh_addr, sh_size))

    if not exec_sections:
        log.warning("offset scanner ELF: no executable sections found in %s", path.name)
        return False

    def file_off_to_vma(off: int) -> int | None:
        for sh_off, sh_addr, sh_size in exec_sections:
            if sh_off <= off < sh_off + sh_size:
                return sh_addr + (off - sh_off)
        return None

    # Context bytes at instruction_start+7 (immediately after the 7-byte RIP-relative store).
    # We match any 64-bit RIP-relative store (REX.W 0x89 ModRM where ModRM & 0xC7 == 0x05)
    # to handle whatever register the Linux compiler chose.
    SIGS = {
        "dwEntityList":            b"\xe9",
        "dwGlobalVars":            b"\x48\x89\x42",
        "dwLocalPlayerController": b"\x41\x89\xBE",
        "dwViewMatrix":            b"\x48\xC1\xE0\x06",
    }

    found_any = False
    for name, ctx in SIGS.items():
        pos = 0
        while True:
            best = len(data)
            for rex in (b"\x48\x89", b"\x4C\x89"):
                i = data.find(rex, pos)
                if 0 <= i < best:
                    best = i
            if best == len(data):
                break
            idx = best
            modrm = data[idx + 2] if idx + 2 < len(data) else 0
            if (modrm & 0xC7) == 0x05 and data[idx + 7: idx + 7 + len(ctx)] == ctx:
                vma_end = file_off_to_vma(idx + 7)
                if vma_end is not None:
                    rel32 = struct.unpack_from('<i', data, idx + 3)[0]
                    target_vma = (vma_end + rel32) & 0xFFFFFFFFFFFFFFFF
                    offsets['globals'][name] = target_vma
                    log.info("offset scanner: %s = 0x%X (ELF)", name, target_vma)
                    found_any = True
                    break
            pos = idx + 1

    if found_any:
        offsets['_ts'] = time.time()
        try:
            CACHE_FILE.write_text(json.dumps(offsets, indent=2))
        except Exception:
            pass

    return found_any


def _scan_globals_from_dll(offsets: dict, pid: int | None = None) -> bool:
    """
    Scan client.dll on disk for global offsets using byte signatures.
    Patches offsets['globals'] in-place and saves the cache.
    Returns True if at least dwEntityList was found.
    """
    dll_path = _find_client_dll(pid)
    if not dll_path:
        log.warning("offset scanner: client.dll not found on disk")
        return False

    log.info("offset scanner: scanning %s ...", dll_path.name)
    try:
        data = dll_path.read_bytes()
    except Exception as exc:
        log.warning("offset scanner: could not read client.dll: %s", exc)
        return False
    if data[:2] != b"MZ":
        log.info("offset scanner: %s is not a PE — trying ELF scanner", dll_path.name)
        return _scan_globals_from_elf(dll_path, offsets)

    # Parse PE section table to build raw-offset -> RVA mapping
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    ns = struct.unpack_from("<H", data, pe + 6)[0]
    os_ = struct.unpack_from("<H", data, pe + 20)[0]
    so = pe + 24 + os_
    sections = []
    for i in range(ns):
        s = so + i * 40
        va  = struct.unpack_from("<I", data, s + 12)[0]
        rs  = struct.unpack_from("<I", data, s + 16)[0]
        ro  = struct.unpack_from("<I", data, s + 20)[0]
        vs  = struct.unpack_from("<I", data, s + 8)[0]
        sections.append((va, ro, min(vs, rs)))

    def raw_to_rva(raw: int) -> int | None:
        for va, ro, sz in sections:
            if ro <= raw < ro + sz:
                return va + (raw - ro)
        return None

    # sig: (3-byte opcode, context bytes at +7)
    SIGS = {
        "dwEntityList":            (b"\x48\x89\x0D", b"\xe9"),
        "dwGlobalVars":            (b"\x48\x89\x15", b"\x48\x89\x42"),
        "dwLocalPlayerController": (b"\x48\x8B\x05", b"\x41\x89\xBE"),
        "dwViewMatrix":            (b"\x48\x8D\x0D", b"\x48\xC1\xE0\x06"),
    }

    found_any = False
    for name, (sig, ctx) in SIGS.items():
        pos = 0
        while True:
            idx = data.find(sig, pos)
            if idx == -1:
                break
            rva_end = raw_to_rva(idx + 7)
            if rva_end is not None and data[idx + 7: idx + 7 + len(ctx)] == ctx:
                rel32 = struct.unpack_from("<i", data, idx + 3)[0]
                target_rva = (rva_end + rel32) & 0xFFFFFFFF
                offsets["globals"][name] = target_rva
                log.info("offset scanner: %s = 0x%X", name, target_rva)
                found_any = True
                break
            pos = idx + 1

    # Also scan for field offsets stored in schema metadata (name string → nearby offset)
    FIELD_NAMES = {
        ("C_CSPlayerPawn",  "m_angEyeAngles"): 0x10,   # offset is +16 bytes after name ptr in schema entry
    }
    image_base_va = struct.unpack_from("<Q", data, pe + 24 + 24)[0]
    for (cls, field), schema_off in FIELD_NAMES.items():
        needle = field.encode() + b"\x00"
        str_raw = data.find(needle)
        if str_raw == -1:
            continue
        # Find section containing the string to get its VA
        for va, ro, sz in sections:
            if ro <= str_raw < ro + sz:
                str_va = image_base_va + va + (str_raw - ro)
                break
        else:
            continue
        # Search for pointer to this string
        str_va_bytes = struct.pack("<Q", str_va)
        ref = data.find(str_va_bytes)
        if ref == -1:
            continue
        # Read field offset at schema_off bytes after the name pointer
        field_off_raw = struct.unpack_from("<I", data, ref + schema_off)[0]
        if 0x100 < field_off_raw < 0x10000:
            offsets["fields"].setdefault(cls, {})[field] = field_off_raw
            log.info("offset scanner: %s.%s = 0x%X", cls, field, field_off_raw)
            found_any = True

    if found_any:
        offsets["_ts"] = time.time()
        try:
            CACHE_FILE.write_text(json.dumps(offsets, indent=2))
        except Exception:
            pass

    return found_any


# ── cs2 entity reading ────────────────────────────────────────────────────────
ENT_ENTRY_MASK   = 0x7FFF
INVALID_EHANDLE  = 0xFFFFFFFF

# weapon type ids from CCSWeaponBaseVData::m_WeaponType
_PRIMARY_TYPES   = {2, 3, 4, 5, 6}   # smg, rifle, shotgun, sniper, mg
_PISTOL_TYPE     = 1
_MELEE_TYPES     = {0, 8}             # knife, taser
_GRENADE_TYPE    = 9

_GRENADE_CLASS_TO_TYPE = {
    # Projectile classes (entities that fly through the air)
    "C_SmokeGrenadeProjectile": "smoke",
    "C_MolotovProjectile":      "molly",   # in-flight trajectory
    "C_HEGrenadeProjectile":    "he",
    "C_FlashbangProjectile":    "flash",   # C_Flashbang is inventory item — use projectile
    "C_DecoyProjectile":        "decoy",
    # Post-land effects
    "C_Inferno":                "molly",   # fire zone after landing
}
_GRENADE_CLASSES = set(_GRENADE_CLASS_TO_TYPE)

# hardcoded offsets that never come from schema
_CURTIME_OFF          = 0x30    # CGlobalVarsBase::m_curtime
_MAP_NAME_OFF         = 0x188   # CGlobalVarsBase::map name string
_IDENTITY_CLASS_OFF   = 0x08    # CEntityIdentity::m_pClassInfo
_IDENTITY_IDX_OFF     = 0x10    # CEntityIdentity::m_Idx
_CLASSINFO_NAME1_OFF  = 0xE0    # class_info -> intermediate ptr (updated for CS2 build 14153+)
_CLASSINFO_NAME2_OFF  = 0x08    # intermediate -> name string ptr
_SMOKE_DID_EFFECT_OFF = 0x11B8  # fallback: C_SmokeGrenadeProjectile::m_bDidSmokeEffect (may be stale)

# Exact basenames of CS2's client module across builds/platforms. Match on the
# *basename* (not a substring) so we never latch onto unrelated libraries such as
# steamclient.so or libwayland-client.so — those contain "client.so" but are not
# libclient.so, and matching them corrupts the scanned .rodata/.data ranges.
_CLIENT_MODULE_BASENAMES = ("libclient.so", "client_client.so", "client.so")

# Schema field names the radar reads. On Linux these offsets are re-resolved
# from the running client module at startup (see _linux_refresh_field_offsets),
# because cs2-dumper's static offsets go stale whenever the game updates ahead
# of the dumper — the Windows build avoids this by dumping the schema live.
_RADAR_NETVARS = (
    "m_hOwnerEntity", "m_iHealth", "m_iTeamNum", "m_nSubclassID", "m_pGameSceneNode",
    "m_bIsLocalPlayerController", "m_hPawn", "m_hController", "m_hPlayerPawn", "m_steamID",
    "m_pItemServices", "m_pWeaponServices", "m_iAccount", "m_iCompTeammateColor",
    "m_pInGameMoneyServices", "m_sSanitizedPlayerName", "m_bHasDefuser", "m_bHasHelmet",
    "m_angEyeAngles", "m_ArmorValue", "m_szName", "m_WeaponType", "m_pEntity",
    "m_vecAbsOrigin", "m_bInPostEffectTime", "m_firePositions", "m_nFireCount",
    "m_bBeingDefused", "m_bBombDefused", "m_bBombTicking", "m_flC4Blow", "m_flDefuseCountDown",
    "m_hActiveWeapon", "m_hMyWeapons", "m_nSmokeEffectTickBegin",
)


def _maps_basename(path_field: str) -> str:
    """Basename of a /proc/maps path field, minus any ' (deleted)' suffix."""
    return path_field.rsplit('/', 1)[-1].removesuffix(' (deleted)').strip()


def _valid_ptr(p: int) -> bool:
    """True for a plausible x86-64 userspace pointer.

    Covers the whole 47-bit canonical range (0x1_0000 .. 0x8000_0000_0000). The
    old code hard-coded a 0x7F00_0000_0000 lower bound, which silently rejected
    every real pointer on machines where ASLR maps CS2's libraries lower (e.g.
    0x7388_xxxx_xxxx) — breaking interface resolution and every entity read.
    """
    return 0x10000 <= p < 0x800000000000


class CS2Reader:
    def __init__(self, mem: Memory, offsets: dict):
        self.mem      = mem
        self._offsets = offsets          # full dict; needed for correct cache writes
        self._g       = offsets.setdefault("globals", {})
        self._f       = offsets.get("fields", {})

        self.client_base        = 0
        self.entity_system      = 0
        self.gvars              = 0
        self.lpc_addr           = 0   # address of local player controller pointer
        self._bomb_own_idx      = 0   # persisted across frames
        self._last_local_team   = 0   # last known valid team (2=T, 3=CT)
        self._lpc_fallback      = 0   # cached lpc when dwLocalPlayerController offset is wrong (Linux)
        self._last_lpc_scan     = 0.0 # monotonic time of last _find_lpc_fallback scan
        # On Linux these may be patched after a live memory scan
        self._entity_chunk_off  = int(self._g.get("_linux_chunk_off", 16))
        self._entity_stride     = int(self._g.get("_linux_entity_stride", 120))
        self._last_es_scan      = 0.0 # monotonic time of last _linux_scan_entity_system call
        # NEVER seed this from the on-disk cache: it is an absolute (ASLR-randomized)
        # runtime address, only valid within the CS2 process that produced it. A value
        # persisted from a previous session points into unmapped memory after CS2
        # restarts, and _entity_ptr would prefer it over the correct es+chunk_off,
        # making every chunk-0 read return 0 (0 controllers). Re-derived each session.
        self._linux_chunk0_abs  = 0
        self._linux_es_ptr_addr = 0   # addr holding the CGameEntitySystem ptr (instance+0x50)
        self._nv                = {}   # live-scanned schema field offsets {name: off} (Linux)

    def _nv_off(self, name: str, fallback: int = 0) -> int:
        """Live-scanned schema offset by field name, else a fallback."""
        return self._nv.get(name, fallback)

    def _linux_refresh_field_offsets(self) -> int:
        """Resolve schema field offsets from the running client module (auto-refresh).

        cs2-dumper's static offsets drift whenever CS2 updates ahead of the
        dumper. Each schema field descriptor in the client module is laid out as
        {name_ptr, type_ptr, int32 offset, ...}; find the descriptor for each
        field the radar reads (by its name string) and take the offset at
        name_ptr+0x10. Patches self._f in place and records self._nv. This is the
        Linux equivalent of the C++ usermode's live schema::setup() dump.
        """
        if not IS_LINUX or not getattr(self.mem, "pid", 0) or not self.client_base:
            return 0
        base = self.client_base
        end = base
        try:
            for line in Path(f"/proc/{self.mem.pid}/maps").read_text(errors="ignore").splitlines():
                parts = line.split(maxsplit=5)
                if len(parts) >= 6 and _maps_basename(parts[5]) in _CLIENT_MODULE_BASENAMES:
                    s, e = (int(x, 16) for x in parts[0].split('-'))
                    if s >= base:
                        end = max(end, e)
        except Exception:
            return 0
        if not (0 < end - base <= 512 * 1024 * 1024):
            return 0
        try:
            dump = self.mem._read(base, end - base)
        except Exception as exc:
            log.warning("offset refresh: could not read client module: %s", exc)
            return 0

        def _scan(name: bytes) -> int:
            si = dump.find(name + b"\x00")
            if si < 0:
                return 0
            needle = struct.pack("<Q", base + si)
            pos = dump.find(needle)
            while pos >= 0:
                if pos + 0x14 <= len(dump):
                    off = struct.unpack_from("<I", dump, pos + 0x10)[0]
                    if 0 < off < 0x8000:
                        return off
                pos = dump.find(needle, pos + 8)
            return 0

        found = {}
        for fname in _RADAR_NETVARS:
            off = _scan(fname.encode())
            if off:
                found[fname] = off
        if not found:
            log.warning("offset refresh: no schema fields resolved — falling back to cs2-dumper offsets")
            return 0

        self._nv = found
        patched = 0
        for cls, fields in self._f.items():
            for fname, off in found.items():
                if fname in fields and fields[fname] != off:
                    fields[fname] = off
                    patched += 1
        self._offsets["_linux_fields"] = {"_ts": time.time(), "map": found}
        try:
            CACHE_FILE.write_text(json.dumps(self._offsets, indent=2))
        except Exception:
            pass
        log.info("offset refresh: resolved %d live schema fields (patched %d class entries)",
                 len(found), patched)
        return len(found)

    def _linux_detect_stride(self, chunk0_abs: int) -> int:
        """Pick the CEntityIdentity stride (bytes) that makes the most slots'
        stored m_Idx match their slot number. Robust across CS2 builds where the
        identity size has been 112 or 120."""
        best_st, best_n = self._entity_stride, -1
        for st in (112, 120, 128):
            n = 0
            for slot in range(256):
                idobj = chunk0_abs + st * slot
                if not _valid_ptr(self.mem.u64(idobj)):
                    continue
                midx = self.mem.u32(idobj + _IDENTITY_IDX_OFF)
                if (midx & 0x7FFF) == slot and (midx >> 15) > 0:
                    n += 1
            if n > best_n:
                best_n, best_st = n, st
        return best_st

    def _world_entity_ok(self, chunk0_abs: int) -> bool:
        """True if chunk-0's slot-0 identity is the world entity (serial >= 1)."""
        if not _valid_ptr(chunk0_abs):
            return False
        v0 = self.mem.u32(chunk0_abs + _IDENTITY_IDX_OFF)
        if (v0 & 0x7FFF) != 0 or (v0 >> 15) == 0:
            return False
        return _valid_ptr(self.mem.u64(chunk0_abs))

    def _read_utl_string(self, address: int) -> str:
        """Read a CS2 CUtlString — tries ptr at +0 then ptr at +8."""
        for delta in (0, 8):
            ptr = self.mem.ptr(address + delta)
            if ptr:
                s = self.mem.cstring(ptr)
                if s:
                    return s
        return ""

    def _off(self, cls: str, field: str, default: int = 0) -> int:
        return self._f.get(cls, {}).get(field, default)

    # ── setup ─────────────────────────────────────────────────────────────────
    def setup(self) -> bool:
        self.client_base = self.mem.get_module_base("client.dll")
        if not self.client_base:
            log.error("CS2 client module not loaded (%s) — is CS2 running?",
                      ", ".join(client_module_aliases("client.dll")))
            return False

        dw_list  = self._g.get("dwEntityList", 0)
        dw_gvars = self._g.get("dwGlobalVars",  0)
        dw_lpc   = self._g.get("dwLocalPlayerController", 0)

        if not dw_list or not dw_gvars or not dw_lpc:
            log.error("critical global offsets missing from cs2-dumper cache — delete offsets_cache.json and retry")
            return False

        log.debug("dwEntityList=0x%X  dwGlobalVars=0x%X  dwLocalPlayerController=0x%X",
                  dw_list, dw_gvars, dw_lpc)

        # Native Linux: re-resolve schema field offsets from the live client
        # module so stale cs2-dumper offsets can't break entity reads.
        if IS_LINUX:
            self._linux_refresh_field_offsets()

        raw_list = self.mem._read(self.client_base + dw_list, 8)
        self.entity_system = self.mem.ptr(self.client_base + dw_list)
        self.gvars         = self.mem.ptr(self.client_base + dw_gvars)
        self.lpc_addr      = self.client_base + dw_lpc

        dw_vm = self._g.get("dwViewMatrix", 0)
        self.view_matrix_addr = (self.client_base + dw_vm) if dw_vm else 0

        if not self.entity_system:
            log.warning("entity system ptr is null (raw bytes @ dwEntityList: %s) — CS2 may be in main menu or offsets are stale",
                        raw_list.hex())
            return False

        # On native Linux always override the entity system with the value from
        # GameResourceServiceClientV0 in libengine2.so — the Windows dwEntityList
        # RVA is meaningless on Linux.
        if IS_LINUX:
            real_es = self._linux_find_entity_system_via_interface()
            if real_es:
                self.entity_system = real_es
            else:
                log.warning("interface scan failed — entity reads may be wrong")

        if IS_LINUX:
            # Fast path: cached chunk0 from a previous run. Validate it's still
            # live (sequential m_Idx still holds) before trusting it.
            def _validate_chunk0(addr: int, stride: int) -> bool:
                if not (_valid_ptr(addr)):
                    return False
                # CS2 uses 0x7FFF in free slots — do NOT check sequential indices.
                # Instead just verify the world entity (slot 0, serial always >= 1).
                v0 = self.mem.u32(addr + _IDENTITY_IDX_OFF)
                if (v0 & 0x7FFF) != 0:
                    return False
                if (v0 >> 15) == 0:
                    return False
                ptr = self.mem.u64(addr)
                return _valid_ptr(ptr)

            if self._linux_chunk0_abs and _validate_chunk0(self._linux_chunk0_abs, self._entity_stride):
                log.info("entity chunk-0 reused from cache: 0x%X stride=%d",
                         self._linux_chunk0_abs, self._entity_stride)
            else:
                # Cached chunk is stale (CS2 restarted) or not yet found.
                # Clear it so _entity_ptr can never prefer a stale absolute address
                # over the correct es+chunk_off path; only a fresh same-session
                # direct scan (below) may re-set it.
                self._linux_chunk0_abs = 0
                # Trust the interface-scan entity system if its chunk-0 array
                # holds a live world entity (slot 0, serial >= 1). The old
                # "chunk0 far from entity_system" heuristic was Windows-specific
                # and wrongly rejected the correct Linux layout (gap is ~90 KB).
                chunk0_test = self.mem.u64(self.entity_system + self._entity_chunk_off)
                chunk0_ok   = _validate_chunk0(chunk0_test, self._entity_stride)
                if chunk0_ok:
                    # Detect the CEntityIdentity stride for this build (112/120/128)
                    # — the world entity at slot 0 is stride-independent, so a wrong
                    # default silently hides every other entity.
                    self._entity_stride = self._linux_detect_stride(chunk0_test)
                    self._g["_linux_entity_stride"] = self._entity_stride
                    log.info("entity system validated via interface scan "
                             "(es=0x%X chunk0=0x%X chunk_off=%d stride=%d)",
                             self.entity_system, chunk0_test,
                             self._entity_chunk_off, self._entity_stride)
                if not chunk0_ok:
                    log.warning(
                        "entity system chunk0 bad (0x%X) — "
                        "cached dwEntityList is likely a Windows RVA; scanning live memory",
                        chunk0_test)
                    new_vma, new_chunk_off, new_stride = self._linux_scan_entity_system()
                    if new_vma:
                        new_es = self.mem.u64(self.client_base + new_vma)
                        if _valid_ptr(new_es):
                            self.entity_system     = new_es
                            self._entity_chunk_off = new_chunk_off
                            self._entity_stride    = new_stride
                            self._g["dwEntityList"]         = new_vma
                            self._g["_linux_chunk_off"]     = new_chunk_off
                            self._g["_linux_entity_stride"] = new_stride
                            try:
                                self._offsets["_ts"] = time.time()
                                CACHE_FILE.write_text(json.dumps(self._offsets, indent=2))
                            except Exception:
                                pass
                            log.info("entity system fixed via scan @ 0x%X (vma=0x%X chunk_off=%d stride=%d)",
                                     new_es, new_vma, new_chunk_off, new_stride)
                        else:
                            log.warning("entity scan returned vma 0x%X but ptr reads 0x%X — deferring to collect()", new_vma, new_es)
                    else:
                        # vtable scan inconclusive — try direct chunk-0 scan
                        chunk0_abs, new_stride = self._linux_find_chunk0_direct()
                        if chunk0_abs:
                            self._linux_chunk0_abs = chunk0_abs
                            self._entity_stride    = new_stride
                            # Do NOT persist _linux_chunk0_abs: it is an absolute
                            # ASLR address, valid only in this process. Stride is
                            # a build constant and safe to cache.
                            self._g["_linux_entity_stride"] = new_stride
                            try:
                                self._offsets["_ts"] = time.time()
                                CACHE_FILE.write_text(json.dumps(self._offsets, indent=2))
                            except Exception:
                                pass
                            log.info("entity chunk-0 found directly at 0x%X stride=%d",
                                     chunk0_abs, new_stride)
                        else:
                            log.warning("entity scan inconclusive — will retry each collect() until in-match")

        log.info("client.dll    @ 0x%016X", self.client_base)
        log.info("entity system @ 0x%016X", self.entity_system)
        log.info("global vars   @ 0x%016X", self.gvars)
        return True

    # ── entity list ───────────────────────────────────────────────────────────
    def _entity_ptr(self, idx: int, chunk_cache: dict) -> int:
        chunk = idx >> 9
        if chunk not in chunk_cache:
            if chunk == 0 and self._linux_chunk0_abs:
                chunk_cache[0] = self._linux_chunk0_abs
            else:
                chunk_cache[chunk] = self.mem.ptr(
                    self.entity_system + 8 * chunk + self._entity_chunk_off)
        entry_list = chunk_cache[chunk]
        if not entry_list:
            return 0
        return self.mem.ptr(entry_list + self._entity_stride * (idx & 0x1FF))

    def _linux_scan_entity_system(self) -> tuple[int, int, int]:
        """
        Scan libclient.so's writable data section at runtime to locate the entity
        system global pointer.  Only called on native Linux when the cached
        dwEntityList offset appears wrong (chunk0 reads null).

        Returns (vma_offset_from_client_base, chunk_array_offset, entity_stride)
        or (0, 16, 112) on failure.
        """
        if not IS_LINUX or not getattr(self.mem, "pid", 0):
            return 0, 16, 112

        try:
            maps_text = Path(f"/proc/{self.mem.pid}/maps").read_text(errors="ignore")
        except Exception:
            return 0, 16, 112

        rodata_start = rodata_end = 0
        data_start_abs = data_end_abs = 0
        saw_exec = False

        for line in maps_text.splitlines():
            # maxsplit=5 preserves spaced pathnames (parts[-1] breaks on a
            # trailing " (deleted)" and on install dirs that contain spaces).
            parts = line.split(maxsplit=5)
            if len(parts) < 6:
                continue
            if _maps_basename(parts[5]) not in _CLIENT_MODULE_BASENAMES:
                continue
            perms = parts[1]
            lo, hi = parts[0].split('-')
            s, e = int(lo, 16), int(hi, 16)
            if 'x' in perms:
                saw_exec = True
            elif saw_exec and 'w' in perms:
                if not data_start_abs:
                    data_start_abs = s
                data_end_abs = e
            elif saw_exec and 'r' in perms and 'x' not in perms and 'w' not in perms:
                if not rodata_start:
                    rodata_start = s
                    rodata_end = e

        if not rodata_start or not data_start_abs:
            log.warning("entity scan: could not parse libclient.so sections from /proc/maps")
            return 0, 16, 112

        log.info("entity scan: rodata=[0x%X,0x%X)  data=[0x%X,0x%X)",
                 rodata_start, rodata_end, data_start_abs, data_end_abs)

        data_size = data_end_abs - data_start_abs
        # libclient.so's writable segment is well under a few MB. A larger span
        # means the section detection latched onto an unrelated mapping; refuse
        # to bulk-read it (a multi-GB read would pin gigabytes of RAM and stall).
        if not (0 < data_size <= 64 * 1024 * 1024):
            log.warning("entity scan: implausible data-section size 0x%X — aborting scan", data_size)
            return 0, 16, 112
        try:
            raw = self.mem._read(data_start_abs, data_size)
        except Exception as exc:
            log.warning("entity scan: failed to bulk-read data section: %s", exc)
            return 0, 16, 112

        if len(raw) < 8:
            return 0, 16, 112

        # Find all 8-byte-aligned slots in the data section that contain a
        # pointer to a C++ object whose vtable sits in .rodata.
        candidates = []
        for off in range(0, len(raw) - 7, 8):
            ptr_val = struct.unpack_from('<Q', raw, off)[0]
            if not (_valid_ptr(ptr_val)):
                continue
            vtable = self.mem.u64(ptr_val)
            if rodata_start <= vtable < rodata_end:
                candidates.append((data_start_abs + off, ptr_val))

        log.info("entity scan: %d vtable-objects in data section — probing for entity system",
                 len(candidates))

        # Validate: which object is CGameEntitySystem?
        # The entity system's chunk pointer array sits at a small fixed offset.
        # No distance filter — we rely solely on the sequential m_Idx check (8+
        # consecutive slots) which is statistically impossible to match by accident.
        off_is_local = self._off("CBasePlayerController", "m_bIsLocalPlayerController")

        for abs_addr, es_ptr in candidates:
            vma_off = abs_addr - self.client_base
            for chunk_off in (16, 8, 24, 32, 40, 48, 56, 64, 72, 80, 88, 96, 112, 128):
                chunk0 = self.mem.u64(es_ptr + chunk_off)
                if not (_valid_ptr(chunk0)):
                    continue
                for stride in (112, 120, 128):
                    # Primary check: CS2 initialises ALL 512 entity slots at
                    # startup with (m_Idx & 0x7FFF) == slot_number even when no
                    # entity occupies the slot (serial may be 0). Check 24
                    # consecutive slots — any stride/offset mismatch breaks it.
                    idx_match = False
                    for idx_off in (_IDENTITY_IDX_OFF, 0x18, 0x08, 0x20):
                        if all((self.mem.u32(chunk0 + stride * s + idx_off) & 0x7FFF) == s
                               for s in range(24)):
                            idx_match = True
                            break
                    if not idx_match:
                        continue

                    # Secondary: at least one non-null entity must have a vtable in .rodata.
                    valid = 0
                    local_found = False
                    for ent_idx in range(24):
                        ent_ptr = self.mem.u64(chunk0 + stride * ent_idx)
                        if not (_valid_ptr(ent_ptr)):
                            continue
                        if abs(ent_ptr - chunk0) < 0x10000:
                            continue
                        vt = self.mem.u64(ent_ptr)
                        if rodata_start <= vt < rodata_end:
                            valid += 1
                            if off_is_local:
                                try:
                                    if self.mem.bool8(ent_ptr + off_is_local):
                                        local_found = True
                                except Exception:
                                    pass

                    log.info(
                        "entity scan: idx-validated! vma_off=0x%X chunk_off=%d stride=%d"
                        "  es=0x%X chunk0=0x%X vtable_valid=%d local=%s",
                        vma_off, chunk_off, stride, es_ptr, chunk0, valid, local_found)
                    return vma_off, chunk_off, stride

        log.warning("entity scan: no entity system found via vtable scan")
        return 0, 16, 112

    # ── C-speed chunk scanner ─────────────────────────────────────────────────
    _chunk_scanner_lib  = None   # ctypes CDLL, loaded once
    _chunk_scanner_func = None   # the scan_for_chunk function

    @classmethod
    def _load_chunk_scanner(cls):
        """Load the compiled C scanner from chunk_scanner.so next to this file."""
        if cls._chunk_scanner_lib is not None:
            return cls._chunk_scanner_func is not None
        import ctypes as _ct
        so = Path(__file__).with_name("chunk_scanner.so")
        if not so.exists():
            log.debug("chunk_scanner.so not found — falling back to Python scan")
            cls._chunk_scanner_lib = False
            return False
        try:
            lib = _ct.CDLL(str(so))
            fn = lib.scan_for_chunk
            fn.argtypes = [_ct.c_char_p, _ct.c_size_t,
                           _ct.c_int, _ct.c_int, _ct.c_int]
            fn.restype = _ct.c_int64
            cls._chunk_scanner_lib  = lib
            cls._chunk_scanner_func = fn
            log.debug("chunk_scanner.so loaded")
            return True
        except Exception as exc:
            log.debug("chunk_scanner.so load failed: %s", exc)
            cls._chunk_scanner_lib = False
            return False

    @staticmethod
    def _linux_elf_find_export(path: str, symbol_name: str) -> int:
        """Return the VMA (load-relative) of an ELF64 exported symbol, or 0."""
        try:
            data = Path(path).read_bytes()
        except Exception:
            return 0
        if data[:4] != b'\x7fELF':
            return 0

        e_phoff     = struct.unpack_from('<Q', data, 0x20)[0]
        e_phentsize = struct.unpack_from('<H', data, 0x36)[0]
        e_phnum     = struct.unpack_from('<H', data, 0x38)[0]

        def vma_to_off(vma: int) -> int:
            for i in range(e_phnum):
                ph = e_phoff + i * e_phentsize
                if struct.unpack_from('<I', data, ph)[0] != 1:  # PT_LOAD
                    continue
                p_off   = struct.unpack_from('<Q', data, ph + 0x08)[0]
                p_vaddr = struct.unpack_from('<Q', data, ph + 0x10)[0]
                p_fsz   = struct.unpack_from('<Q', data, ph + 0x20)[0]
                if p_vaddr <= vma < p_vaddr + p_fsz:
                    return p_off + (vma - p_vaddr)
            return 0

        # Find PT_DYNAMIC
        dyn_vaddr = dyn_fsz = 0
        for i in range(e_phnum):
            ph = e_phoff + i * e_phentsize
            if struct.unpack_from('<I', data, ph)[0] == 2:  # PT_DYNAMIC
                dyn_vaddr = struct.unpack_from('<Q', data, ph + 0x10)[0]
                dyn_fsz   = struct.unpack_from('<Q', data, ph + 0x20)[0]
                break
        if not dyn_vaddr:
            return 0

        dyn_off = vma_to_off(dyn_vaddr)
        if not dyn_off:
            return 0

        strtab_vma = symtab_vma = syment = 0
        for j in range(dyn_fsz // 16):
            tag = struct.unpack_from('<Q', data, dyn_off + j * 16)[0]
            val = struct.unpack_from('<Q', data, dyn_off + j * 16 + 8)[0]
            if tag == 0:   break
            if tag == 5:   strtab_vma = val
            elif tag == 6: symtab_vma = val
            elif tag == 11: syment = val
        if not syment:
            syment = 24

        strtab_off = vma_to_off(strtab_vma)
        symtab_off = vma_to_off(symtab_vma)
        if not strtab_off or not symtab_off:
            return 0

        want = symbol_name.encode()
        pos = symtab_off + syment  # skip null entry
        while pos + syment <= len(data):
            st_name  = struct.unpack_from('<I', data, pos)[0]
            st_value = struct.unpack_from('<Q', data, pos + 8)[0]
            if st_name and st_value:
                name_start = strtab_off + st_name
                name_end   = data.find(b'\x00', name_start)
                if name_end >= 0 and data[name_start:name_end] == want:
                    return st_value
            pos += syment
        return 0

    def _linux_find_entity_system_via_interface(self) -> int:
        """
        Find CGameEntitySystem via GameResourceServiceClientV0 from libengine2.so.
        Adapted from avitran0/cs2-radar (Rust).  No Windows RVAs needed.
        """
        if not IS_LINUX or not getattr(self.mem, "pid", None):
            return 0

        maps_text = Path(f"/proc/{self.mem.pid}/maps").read_text(errors="ignore")

        engine_base = 0
        engine_path = ""
        for line in maps_text.splitlines():
            # maxsplit=5 keeps the pathname intact even when the CS2 install dir
            # contains spaces (e.g. ".../Counter-Strike Global Offensive/...").
            parts = line.split(maxsplit=5)
            if len(parts) < 6 or 'libengine2.so' not in parts[5]:
                continue
            path = parts[5].removesuffix(' (deleted)').strip()
            start = int(parts[0].split('-')[0], 16)
            # Prefer the file-offset==0 mapping as the load base (as get_module_base does);
            # fall back to the first mapping seen if none has offset 0.
            if int(parts[2], 16) == 0:
                engine_base, engine_path = start, path
                break
            if not engine_base:
                engine_base, engine_path = start, path

        if not engine_base:
            log.warning("interface scan: libengine2.so not in /proc maps")
            return 0

        rva = self._linux_elf_find_export(engine_path, "CreateInterface")
        if not rva:
            log.warning("interface scan: CreateInterface not found in %s", engine_path)
            return 0

        create_iface_addr = engine_base + rva
        log.debug("interface scan: CreateInterface @ 0x%X", create_iface_addr)

        # Reach the `mov reg, [rip+disp32]` that loads the interface list head.
        # On some builds the exported CreateInterface is a 5-byte `jmp rel32`
        # thunk to the real body; on others (current Linux CS2) the export IS the
        # body. Detect the thunk by its opcode instead of assuming one is present.
        first_byte = self.mem._read(create_iface_addr, 1)
        if first_byte and first_byte[0] == 0xE9:          # jmp rel32 thunk
            body = create_iface_addr + 5 + self.mem.i32(create_iface_addr + 1)
        else:                                             # export is the body itself
            body = create_iface_addr
        # The list-head load sits 0x10 bytes into the body: mov reg,[rip+disp32].
        export_address = body + 0x10

        # interface_entry = *(export_address + 0x07 + *(u32)(export_address + 0x03))
        rel2 = self.mem.i32(export_address + 3)
        interface_entry = self.mem.u64(export_address + 7 + rel2)

        seen: set[int] = set()
        while _valid_ptr(interface_entry):
            if interface_entry in seen:
                break
            seen.add(interface_entry)

            name_ptr = self.mem.u64(interface_entry + 8)
            if name_ptr:
                iface_name = self.mem.cstring(name_ptr)
                if iface_name and iface_name.startswith("GameResourceServiceClientV0"):
                    # Resolve instance pointer through vtable
                    vfunc_addr = self.mem.u64(interface_entry)
                    rel3 = self.mem.i32(vfunc_addr + 3)
                    instance = (vfunc_addr + 7 + rel3) & 0xFFFFFFFFFFFFFFFF
                    # entity system lives at instance + 0x50
                    es_ptr_addr = instance + 0x50
                    entity_system = self.mem.u64(es_ptr_addr)
                    if _valid_ptr(entity_system):
                        # Cache the slot so collect() can cheaply re-resolve the
                        # entity system each frame (it can move on map load)
                        # without ever touching the meaningless Windows RVA.
                        self._linux_es_ptr_addr = es_ptr_addr
                        log.info("interface scan: entity system @ 0x%X (via %s)",
                                 entity_system, iface_name)
                        return entity_system

            interface_entry = self.mem.u64(interface_entry + 0x10)

        log.warning("interface scan: GameResourceServiceClientV0 not found")
        return 0

    def _linux_find_chunk0_direct(self) -> tuple[int, int]:
        """
        Find the entity identity chunk-0 array by scanning for live CS2 entity
        objects (vtable pointer in libclient.so .rodata), then following each
        object's m_pEntity pointer to its CEntityIdentity and computing the
        chunk-0 base from that.

        CS2 on Linux uses 0x7FFF in the low 15 bits of m_Idx for unoccupied
        slots (free-list marker), so the old sequential-index scan is unreliable.
        This approach only requires finding ONE live entity in chunk-0
        (absolute slot < 512).

        Returns (chunk0_abs, stride) or (0, 112) on failure.
        """
        if not IS_LINUX or not getattr(self.mem, "pid", 0):
            return 0, 112

        try:
            maps_text = Path(f"/proc/{self.mem.pid}/maps").read_text(errors="ignore")
        except Exception:
            return 0, 112

        # libclient.so .rodata: the r--p segment that does NOT start at client_base
        rodata_s = rodata_e = 0
        for line in maps_text.splitlines():
            # maxsplit=5 preserves spaced pathnames; match on exact basename so
            # steamclient.so / libwayland-client.so are never mistaken for it.
            parts = line.split(maxsplit=5)
            if (len(parts) >= 6 and parts[1] == 'r--p'
                    and _maps_basename(parts[5]) in _CLIENT_MODULE_BASENAMES):
                s2, e2 = (int(x, 16) for x in parts[0].split('-'))
                if s2 != self.client_base:
                    rodata_s, rodata_e = s2, e2
                    break

        if not rodata_s:
            log.warning("direct chunk scan: could not find libclient.so .rodata")
            return 0, 112

        log.info("direct chunk scan: vtable scan for rodata=[0x%X,0x%X)",
                 rodata_s, rodata_e)

        # Collect up to 2 chunk-0 entities (slot < 512) to pin the stride
        chunk0_entities: dict[int, int] = {}  # slot -> identity_addr

        for line in maps_text.splitlines():
            parts = line.split()
            if len(parts) < 5 or parts[1] != 'rw-p':
                continue
            lo, hi = (int(x, 16) for x in parts[0].split('-'))
            size = hi - lo
            # Only small-to-medium regions — entity objects are heap-allocated
            # individually and won't be in giant (>16MB) regions.
            if size < 256 or size > 16 * 1024 * 1024:
                continue
            try:
                raw = self.mem._read(lo, size)
            except Exception:
                continue
            if not raw:
                continue

            # Scan for vtable pointers in libclient.so .rodata
            for off in range(0, len(raw) - 32, 8):
                vtable = struct.unpack_from('<Q', raw, off)[0]
                if not (rodata_s <= vtable < rodata_e):
                    continue
                obj_addr = lo + off
                # Follow m_pEntity at obj+0x10
                identity = self.mem.u64(obj_addr + 0x10)
                if not (_valid_ptr(identity)):
                    continue
                # Verify back-pointer: identity[0] == obj_addr
                if self.mem.u64(identity) != obj_addr:
                    continue
                m_Idx  = self.mem.u32(identity + _IDENTITY_IDX_OFF)
                slot   = m_Idx & 0x7FFF
                serial = m_Idx >> 15
                # 0x7FFF = free-slot marker; skip; also skip invalid serial
                if slot == 0x7FFF or serial == 0:
                    continue
                # Only chunk-0 entities (absolute slot 0-511)
                if slot >= 512:
                    continue
                chunk0_entities[slot] = identity
                if len(chunk0_entities) >= 2:
                    break

            if len(chunk0_entities) >= 2:
                break

        if not chunk0_entities:
            log.warning("direct chunk scan: no live entities found in chunk-0")
            return 0, 112

        log.debug("direct chunk scan: found %d chunk-0 entity/entities: slots=%s",
                  len(chunk0_entities), list(chunk0_entities.keys()))

        # Determine stride and compute chunk-0 start
        slots = sorted(chunk0_entities.keys())

        if len(slots) >= 2:
            s0, s1 = slots[0], slots[1]
            id0, id1 = chunk0_entities[s0], chunk0_entities[s1]
            stride_exact = (id1 - id0) / (s1 - s0)
        else:
            stride_exact = None

        for stride in (112, 120, 128):
            if stride_exact is not None and abs(stride - stride_exact) > 1.0:
                continue
            s0   = slots[0]
            id0  = chunk0_entities[s0]
            chunk_start = id0 - stride * s0
            if not (_valid_ptr(chunk_start)):
                continue
            # Sanity: slot 0 of this chunk must have a live entity in rodata
            m_Idx0 = self.mem.u32(chunk_start + _IDENTITY_IDX_OFF)
            slot0  = m_Idx0 & 0x7FFF
            ser0   = m_Idx0 >> 15
            if slot0 != 0 or ser0 == 0:
                continue
            m_pObj0 = self.mem.u64(chunk_start)
            if not (_valid_ptr(m_pObj0)):
                continue
            vt0 = self.mem.u64(m_pObj0)
            if not (rodata_s <= vt0 < rodata_e):
                continue
            log.info("chunk-0 found via entity scan: 0x%X stride=%d", chunk_start, stride)
            return chunk_start, stride

        log.warning("direct chunk scan: entities found but could not confirm chunk-0 layout")
        return 0, 112

    def _entity_by_handle(self, handle: int, chunk_cache: dict) -> int:
        if handle == INVALID_EHANDLE:
            return 0
        return self._entity_ptr(handle & ENT_ENTRY_MASK, chunk_cache)

    def _class_name(self, entity_ptr: int) -> str:
        """Resolve the schema class name for an entity."""
        off_pent = self._off("CEntityInstance", "m_pEntity", 0x10)
        identity = self.mem.ptr(entity_ptr + off_pent)
        if not identity:
            return ""
        # Check validity via m_Idx
        m_idx = self.mem.u32(identity + _IDENTITY_IDX_OFF)
        if (m_idx & ENT_ENTRY_MASK) == ENT_ENTRY_MASK:
            return ""
        class_info = self.mem.ptr(identity + _IDENTITY_CLASS_OFF)
        if not class_info:
            return ""
        unk1 = self.mem.ptr(class_info + _CLASSINFO_NAME1_OFF)
        if not unk1:
            return ""
        unk2 = self.mem.ptr(unk1 + _CLASSINFO_NAME2_OFF)
        if not unk2:
            return ""
        # unk2 is an MSVC std::string object — size at +0x10, data at ptr(unk2) if >= 16
        sz = self.mem.u32(unk2 + 0x10)
        if sz == 0 or sz > 256:
            return ""
        if sz < 16:
            return self.mem.cstring(unk2)
        heap = self.mem.ptr(unk2)
        return self.mem.cstring(heap) if heap else ""

    # ── scene origin ──────────────────────────────────────────────────────────
    def _origin(self, entity_ptr: int) -> tuple[float, float, float]:
        off_node   = self._off("C_BaseEntity", "m_pGameSceneNode")
        off_origin = self._off("CGameSceneNode", "m_vecAbsOrigin")
        if not off_node or not off_origin:
            return 0.0, 0.0, 0.0
        node = self.mem.ptr(entity_ptr + off_node)
        if not node:
            return 0.0, 0.0, 0.0
        base = node + off_origin
        return self.mem.f32(base), self.mem.f32(base + 4), self.mem.f32(base + 8)

    # ── weapons ───────────────────────────────────────────────────────────────
    def _read_weapons(self, pawn_ptr: int, chunk_cache: dict) -> dict:
        weapons = {"m_primary": "", "m_secondary": "", "m_active": "",
                   "m_melee": [], "m_utilities": []}

        off_wsvc    = self._off("C_BasePlayerPawn",    "m_pWeaponServices")
        off_my_wpns = self._off("CPlayer_WeaponServices", "m_hMyWeapons")
        off_active  = self._off("CPlayer_WeaponServices", "m_hActiveWeapon")
        off_subid   = self._off("C_BaseEntity",           "m_nSubclassID")
        off_wtype   = self._off("CCSWeaponBaseVData",     "m_WeaponType")
        off_wname   = self._off("CCSWeaponBaseVData",     "m_szName")

        if not all([off_wsvc, off_my_wpns, off_subid, off_wtype, off_wname]):
            return weapons

        wsvc = self.mem.ptr(pawn_ptr + off_wsvc)
        if not wsvc:
            return weapons

        # CNetworkUtlVectorBase: m_size (u32 @ +0x00), m_elements (ptr @ +0x08)
        vec      = wsvc + off_my_wpns
        w_size   = self.mem.u32(vec)
        w_elems  = self.mem.ptr(vec + 0x08)

        if w_elems and 0 < w_size < 64:
            for i in range(w_size):
                w_handle = self.mem.i32(w_elems + i * 4)
                if w_handle == -1:
                    continue
                w_ptr = self._entity_by_handle(w_handle & ENT_ENTRY_MASK, chunk_cache)
                if not w_ptr:
                    continue
                wdata = self.mem.ptr(w_ptr + off_subid + 0x08)
                if not wdata:
                    continue
                w_type = self.mem.u32(wdata + off_wtype)
                wname  = self.mem.string_field(wdata + off_wname)
                if not wname:
                    continue
                if wname.startswith("weapon_"):
                    wname = wname[7:]
                if w_type in _PRIMARY_TYPES:
                    weapons["m_primary"] = wname
                elif w_type == _PISTOL_TYPE:
                    weapons["m_secondary"] = wname
                elif w_type in _MELEE_TYPES and wname not in weapons["m_melee"]:
                    weapons["m_melee"].append(wname)
                elif w_type == _GRENADE_TYPE and wname not in weapons["m_utilities"]:
                    weapons["m_utilities"].append(wname)

        # Active weapon
        if off_active:
            ah = self.mem.u32(wsvc + off_active)
            if ah != INVALID_EHANDLE:
                aw_ptr = self._entity_by_handle(ah, chunk_cache)
                if aw_ptr:
                    wdata = self.mem.ptr(aw_ptr + off_subid + 0x08)
                    if wdata:
                        aname = self.mem.string_field(wdata + off_wname)
                        if aname.startswith("weapon_"):
                            aname = aname[7:]
                        weapons["m_active"] = aname

        return weapons

    # ── local player controller fallback (Linux / wrong dwLocalPlayerController RVA) ──
    def _find_lpc_fallback(self) -> int:
        """
        Scan the entity list for the CCSPlayerController with
        m_bIsLocalPlayerController == True.  Used when dwLocalPlayerController
        is the wrong Windows RVA on native Linux CS2.
        Caches the result so we only scan once per session.
        Throttled to once per 2 seconds while not found.
        """
        if self._lpc_fallback:
            return self._lpc_fallback
        now = time.monotonic()
        if now - self._last_lpc_scan < 2.0:
            return 0
        self._last_lpc_scan = now

        off_is_local = self._off("CBasePlayerController", "m_bIsLocalPlayerController")
        off_hctrl    = self._off("C_BasePlayerPawn", "m_hController") or self._nv_off("m_hController")
        if not off_is_local or not self.entity_system:
            return 0

        cache: dict[int, int] = {}
        found_any = 0
        for idx in range(1024):
            ent = self._entity_ptr(idx, cache)
            if not ent:
                continue
            cls = self._class_name(ent)

            # Direct controller (Windows, and Linux when controllers are listed).
            if cls == "CCSPlayerController":
                controller = ent
            # Linux: the entity list reliably exposes pawns; walk back to the
            # controller via m_hController since class_name for controllers is
            # unreliable across the Win/Linux ABI difference.
            elif cls in ("C_CSPlayerPawn", "C_CSObserverPawn") and off_hctrl:
                h = self.mem.u32(ent + off_hctrl)
                controller = self._entity_by_handle(h, cache) if h != INVALID_EHANDLE else 0
                if not controller:
                    continue
            else:
                continue

            found_any += 1
            try:
                if self.mem.bool8(controller + off_is_local):
                    log.info("lpc fallback: local controller found via idx=%d (%s) ctrl=0x%X",
                             idx, cls, controller)
                    self._lpc_fallback = controller
                    return controller
            except Exception:
                pass

        log.debug("lpc fallback: scanned 1024 slots, found %d controllers", found_any)
        return 0

    # ── main collect loop ─────────────────────────────────────────────────────
    def collect(self) -> dict | None:
        # Re-read entity_system every frame — CS2 can update this pointer
        # during map loads or round resets.  Caching it in setup() causes all
        # entity reads to silently return 0 if the pointer moves.
        if IS_LINUX:
            # Re-resolve cheaply from the cached interface instance (the entity
            # system pointer can move on map load). NEVER read the Windows
            # dwEntityList RVA on Linux — it points at unrelated bytes.
            if self._linux_es_ptr_addr:
                es = self.mem.u64(self._linux_es_ptr_addr)
                if _valid_ptr(es):
                    self.entity_system = es
        else:
            dw_list = self._g.get("dwEntityList", 0)
            if dw_list:
                es = self.mem.u64(self.client_base + dw_list)
                if _valid_ptr(es):
                    self.entity_system = es
        # On Linux, if the entity system still looks wrong (chunk-0 has no live
        # world entity), retry the live scan — throttled to once every 5 s so we
        # don't pay bulk-read overhead every 100 ms frame.
        if IS_LINUX and self.entity_system:
            chunk0_probe = self.mem.u64(self.entity_system + self._entity_chunk_off)
            chunk0_healthy = self._world_entity_ok(chunk0_probe)
            if not chunk0_healthy and not self._linux_chunk0_abs:
                now = time.monotonic()
                if now - self._last_es_scan >= 5.0:
                    self._last_es_scan = now
                    new_vma, new_chunk_off, new_stride = self._linux_scan_entity_system()
                    if new_vma:
                        new_es = self.mem.u64(self.client_base + new_vma)
                        if _valid_ptr(new_es):
                            self.entity_system     = new_es
                            self._entity_chunk_off = new_chunk_off
                            self._entity_stride    = new_stride
                            self._g["dwEntityList"]         = new_vma
                            self._g["_linux_chunk_off"]     = new_chunk_off
                            self._g["_linux_entity_stride"] = new_stride
                            try:
                                self._offsets["_ts"] = time.time()
                                CACHE_FILE.write_text(json.dumps(self._offsets, indent=2))
                            except Exception:
                                pass
                    else:
                        chunk0_abs, new_stride = self._linux_find_chunk0_direct()
                        if chunk0_abs:
                            self._linux_chunk0_abs = chunk0_abs
                            self._entity_stride    = new_stride
                            # Do NOT persist _linux_chunk0_abs: it is an absolute
                            # ASLR address, valid only in this process. Stride is
                            # a build constant and safe to cache.
                            self._g["_linux_entity_stride"] = new_stride
                            try:
                                self._offsets["_ts"] = time.time()
                                CACHE_FILE.write_text(json.dumps(self._offsets, indent=2))
                            except Exception:
                                pass
        if not self.entity_system:
            return None

        # Refresh global vars pointer for the same reason
        dw_gvars = self._g.get("dwGlobalVars", 0)
        if dw_gvars:
            gv = self.mem.ptr(self.client_base + dw_gvars)
            if gv:
                self.gvars = gv

        lpc = self.mem.ptr(self.lpc_addr)
        if not lpc:
            lpc = self._find_lpc_fallback()
            if not lpc:
                return None

        off_team = self._off("C_BaseEntity", "m_iTeamNum")
        local_team = self.mem.u32(lpc + off_team) if off_team else 0

        # Keep broadcasting with the last known team during brief transitions
        # (half-time swap, round restart) so the webapp doesn't freeze.
        if local_team in (2, 3):
            self._last_local_team = local_team
        elif hasattr(self, "_last_local_team") and self._last_local_team:
            local_team = self._last_local_team
        else:
            return None

        map_name = self._get_map_name()

        off_health   = self._off("C_BaseEntity",         "m_iHealth")
        off_team_    = self._off("C_BaseEntity",         "m_iTeamNum")
        off_owner    = self._off("C_BaseEntity",         "m_hOwnerEntity")
        off_hpawn    = self._off("CBasePlayerController","m_hPawn")
        off_hctrl    = self._off("C_BasePlayerPawn", "m_hController") or self._nv_off("m_hController")
        off_steam    = self._off("CBasePlayerController","m_steamID")
        off_name     = self._off("CCSPlayerController",  "m_sSanitizedPlayerName")
        off_money_s  = self._off("CCSPlayerController",  "m_pInGameMoneyServices")
        off_color    = self._off("CCSPlayerController",  "m_iCompTeammateColor")
        off_armor    = self._off("C_CSPlayerPawn",       "m_ArmorValue")
        off_eye      = self._off("C_CSPlayerPawn",       "m_angEyeAngles")
        off_isvc     = self._off("C_BasePlayerPawn",     "m_pItemServices")
        off_defuser  = self._off("CCSPlayer_ItemServices","m_bHasDefuser")
        off_helmet   = self._off("CCSPlayer_ItemServices","m_bHasHelmet")
        off_money    = self._off("CCSPlayerController_InGameMoneyServices","m_iAccount")
        off_ticking  = self._off("C_PlantedC4",          "m_bBombTicking")
        off_blow     = self._off("C_PlantedC4",          "m_flC4Blow")
        off_defused  = self._off("C_PlantedC4",          "m_bBombDefused")
        off_defusing = self._off("C_PlantedC4",          "m_bBeingDefused")
        off_defuse_t = self._off("C_PlantedC4",          "m_flDefuseCountDown")
        off_subid    = self._off("C_BaseEntity",          "m_nSubclassID")
        off_wtype    = self._off("CCSWeaponBaseVData",    "m_WeaponType")
        off_wname    = self._off("CCSWeaponBaseVData",    "m_szName")

        curtime = self.mem.f32(self.gvars + _CURTIME_OFF) if self.gvars else 0.0

        players   = []
        bomb_data = {}
        grenades  = []
        dropped   = []
        bomb_own_idx = self._bomb_own_idx

        chunk_cache: dict[int, int] = {}
        seen_ctrls: set[int] = set()   # dedupe: a player is reachable via both its controller and pawn

        for idx in range(1024):
            ent = self._entity_ptr(idx, chunk_cache)
            if not ent:
                continue

            cls = self._class_name(ent)
            if not cls:
                continue

            # ── player controller ─────────────────────────────────────────────
            # Windows enters on the controller; native Linux enters on the pawn
            # (its class_name resolves reliably where the controller's may not)
            # and walks back to the controller via m_hController.
            _is_ctrl = (cls == "CCSPlayerController")
            _is_pawn = (cls in ("C_CSPlayerPawn", "C_CSObserverPawn"))
            if _is_ctrl or (IS_LINUX and _is_pawn):
                if _is_ctrl:
                    ctrl = ent
                    h_pawn = self.mem.u32(ctrl + off_hpawn) if off_hpawn else INVALID_EHANDLE
                    if h_pawn == INVALID_EHANDLE:
                        continue
                    pawn = self._entity_by_handle(h_pawn, chunk_cache)
                    if not pawn:
                        continue
                else:
                    pawn = ent
                    h_ctrl = self.mem.u32(pawn + off_hctrl) if off_hctrl else INVALID_EHANDLE
                    ctrl = self._entity_by_handle(h_ctrl, chunk_cache) if h_ctrl != INVALID_EHANDLE else 0
                    if not ctrl:
                        continue
                    h_pawn = self.mem.u32(ctrl + off_hpawn) if off_hpawn else INVALID_EHANDLE

                if ctrl in seen_ctrls:
                    continue
                seen_ctrls.add(ctrl)

                # Team can sit on the controller or the pawn depending on build.
                team = self.mem.u32(ctrl + off_team_) if off_team_ else 0
                if team not in (2, 3):
                    team = self.mem.u32(pawn + off_team_) if off_team_ else team
                if team not in (2, 3):
                    continue

                health  = self.mem.i32(pawn + off_health) if off_health else 0
                is_dead = health <= 0

                x, y, z = self._origin(pawn)
                eye_yaw  = self.mem.f32(pawn + off_eye + 4) if off_eye else 0.0
                steam_id = self.mem.u64(ctrl + off_steam) if off_steam else 0
                armor    = self.mem.i32(pawn + off_armor) if off_armor else 0

                pname = self._read_utl_string(ctrl + off_name) if off_name else ""

                color = 5
                if off_color:
                    c = self.mem.u32(ctrl + off_color)
                    color = c if c != 0xFFFFFFFF else 5

                money = 0
                if off_money_s and off_money:
                    ms = self.mem.ptr(ctrl + off_money_s)
                    if ms:
                        money = self.mem.i32(ms + off_money)

                has_helmet  = False
                has_defuser = False
                if off_isvc:
                    isvc = self.mem.ptr(pawn + off_isvc)
                    if isvc:
                        has_helmet  = self.mem.bool8(isvc + off_helmet)  if off_helmet  else False
                        has_defuser = self.mem.bool8(isvc + off_defuser) if off_defuser else False

                weapons  = self._read_weapons(pawn, chunk_cache)
                has_bomb = False
                if team == 2 and not is_dead and bomb_own_idx:
                    has_bomb = bomb_own_idx == ((h_pawn & ENT_ENTRY_MASK) & 0xFFFF)

                players.append({
                    "m_idx":        idx,
                    "m_name":       pname,
                    "m_color":      color,
                    "m_team":       team,
                    "m_health":     health,
                    "m_is_dead":    is_dead,
                    "m_is_local":   (ctrl == lpc),
                    "m_model_name": "",
                    "m_steam_id":   str(steam_id),
                    "m_money":      money,
                    "m_armor":      armor,
                    "m_position":   {"x": x, "y": y, "z": z},
                    "m_eye_angle":  eye_yaw,
                    "m_has_helmet": has_helmet,
                    "m_has_defuser":has_defuser,
                    "m_weapons":    weapons,
                    "m_has_bomb":   has_bomb,
                })

            # ── carried c4 ───────────────────────────────────────────────────
            elif cls == "C_C4":
                if off_owner:
                    h_own = self.mem.u32(ent + off_owner)
                    self._bomb_own_idx = h_own & 0xFFFF
                    bomb_own_idx       = self._bomb_own_idx
                x, y, _ = self._origin(ent)
                if x or y:
                    bomb_data = {"x": x, "y": y}

            # ── planted c4 ───────────────────────────────────────────────────
            elif cls == "C_PlantedC4":
                if off_ticking and self.mem.bool8(ent + off_ticking):
                    blow_time = (self.mem.f32(ent + off_blow) - curtime) if off_blow else 0.0
                    if blow_time > 0:
                        x, y, _ = self._origin(ent)
                        bomb_data = {
                            "x":             x,
                            "y":             y,
                            "m_blow_time":   blow_time,
                            "m_is_defused":  self.mem.bool8(ent + off_defused)  if off_defused  else False,
                            "m_is_defusing": self.mem.bool8(ent + off_defusing) if off_defusing else False,
                            "m_defuse_time": (self.mem.f32(ent + off_defuse_t) - curtime) if off_defuse_t else 0.0,
                        }

            # ── grenades ─────────────────────────────────────────────────────
            elif cls in _GRENADE_CLASSES:
                deployed = False

                if cls == "C_SmokeGrenadeProjectile":
                    off_stb = self._off("C_SmokeGrenadeProjectile", "m_nSmokeEffectTickBegin")
                    if off_stb:
                        deployed = self.mem.u32(ent + off_stb) > 0
                    else:
                        deployed = self.mem.bool8(ent + _SMOKE_DID_EFFECT_OFF)
                    # Always show: small dot while in-flight, full circle when deployed

                elif cls == "C_Inferno":
                    off_post = self._off("C_Inferno", "m_bInPostEffectTime")
                    if off_post and self.mem.bool8(ent + off_post):
                        continue  # fire ended naturally or smoked out
                    deployed = True

                elif cls == "C_MolotovProjectile":
                    deployed = False  # show as in-flight dot only

                x, y, z = self._origin(ent)
                if x or y:
                    entry = {"x": x, "y": y, "z": z,
                             "type":     _GRENADE_CLASS_TO_TYPE[cls],
                             "deployed": deployed}

                    # For C_Inferno, attach individual fire positions for accurate shape
                    if cls == "C_Inferno":
                        off_fpos = self._off("C_Inferno", "m_firePositions")
                        off_fcnt = self._off("C_Inferno", "m_nFireCount")
                        if off_fpos and off_fcnt:
                            fire_count = min(self.mem.u32(ent + off_fcnt), 64)
                            if fire_count > 0:
                                raw = self.mem._read(ent + off_fpos, fire_count * 12)
                                fire_pts = []
                                for fi in range(fire_count):
                                    fx, fy = struct.unpack_from("<ff", raw, fi * 12)
                                    fire_pts.append({"x": fx, "y": fy})
                                entry["firePts"] = fire_pts

                    grenades.append(entry)

            # ── dropped weapons ───────────────────────────────────────────────
            elif off_subid and off_wtype and off_wname and off_owner:
                owner = self.mem.u32(ent + off_owner)
                if owner == INVALID_EHANDLE:
                    wdata = self.mem.ptr(ent + off_subid + 0x08)
                    if wdata:
                        wtype = self.mem.u32(wdata + off_wtype)
                        if wtype in {*_PRIMARY_TYPES, _PISTOL_TYPE, 8, _GRENADE_TYPE}:  # 8=zeus
                            wname = self.mem.string_field(wdata + off_wname)
                            if wname and wname.startswith("weapon_"):
                                x, y, _ = self._origin(ent)
                                if x or y:
                                    dropped.append({"x": x, "y": y, "name": wname[7:]})

        view_matrix = []
        if self.view_matrix_addr:
            raw_vm = self.mem._read(self.view_matrix_addr, 64)
            if raw_vm and len(raw_vm) == 64:
                try:
                    view_matrix = list(struct.unpack_from("<16f", raw_vm))
                    if not all(math.isfinite(v) for v in view_matrix):
                        view_matrix = []
                except Exception:
                    pass

        return {
            "m_local_team":   local_team,
            "m_players":      players,
            "m_bomb":         bomb_data,
            "m_grenades":     grenades,
            "m_dropped":      dropped,
            "m_map":          map_name,
            "m_view_matrix":  view_matrix,
            "m_server_ip":    _LOCAL_IP,
            "m_tailscale_ip": _TAILSCALE_IP,
            "m_funnel_url":   _FUNNEL_URL,
            "m_http_port":    HTTP_PORT,
        }

    def _linux_map_from_fds(self) -> str:
        """Derive the current map from the loaded map .vpk in /proc/<pid>/fd.

        Reliable and offset-free: CS2 keeps the level's vpk (e.g.
        ".../csgo/maps/de_mirage.vpk") open while in a match. Skips prefab /
        subdirectory vpks and known non-level vpks.
        """
        pid = getattr(self.mem, "pid", 0)
        if not pid:
            return ""
        marker = "/csgo/maps/"
        try:
            entries = os.listdir(f"/proc/{pid}/fd")
        except OSError:
            return ""
        for fd in entries:
            try:
                target = os.readlink(f"/proc/{pid}/fd/{fd}")
            except OSError:
                continue
            i = target.find(marker)
            if i < 0 or not target.endswith(".vpk"):
                continue
            name = target[i + len(marker):-4]   # between marker and ".vpk"
            if not name or "/" in name or name in ("graphics_settings",):
                continue
            return name
        return ""

    def _get_map_name(self) -> str:
        if IS_LINUX:
            name = self._linux_map_from_fds()
            if name:
                return name
        if not self.gvars:
            return "invalid"
        char_ptr = self.mem.ptr(self.gvars + _MAP_NAME_OFF)
        name = self.mem.cstring(char_ptr) if char_ptr else ""
        if not name or "<empty>" in name or len(name) < 3:
            return "invalid"
        # Strip path prefix (e.g. "maps/de_dust2" → "de_dust2")
        if "/" in name:
            name = name.rsplit("/", 1)[-1]
        # Strip file extension (e.g. "de_dust2.vpk" → "de_dust2")
        if "." in name:
            name = name.rsplit(".", 1)[0]
        return name


# ── config ───────────────────────────────────────────────────────────────────
def load_config() -> dict:
    default = {
        "m_use_localhost": True,
        "m_local_ip": "localhost",
        "m_public_ip": "",
        "auto_update": True,
        "github_token": "",
    }
    if CONFIG_FILE.exists():
        saved = json.loads(CONFIG_FILE.read_text())
        return {**default, **saved}
    CONFIG_FILE.write_text(json.dumps(default, indent=2))
    return default


def _check_for_update(config: dict) -> None:
    """On startup: check GitHub for a newer release and self-update if auto_update is on."""
    if not config.get("auto_update", True):
        log.info("auto-update disabled")
        return

    token = config.get("github_token", "")
    headers = {
        "User-Agent":  "cs2-radar/updater",
        "Accept":      "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        req = urllib.request.Request(GITHUB_API, headers=headers)
        with urllib.request.urlopen(req, timeout=8) as r:
            release = json.loads(r.read())
    except Exception as exc:
        log.debug("update check skipped: %s", exc)
        return

    latest_tag = release.get("tag_name", "")
    if not latest_tag or latest_tag <= VERSION:
        log.info("up to date (%s)", VERSION)
        return

    print(f"\n  Update available: {VERSION} -> {latest_tag}")

    if not getattr(sys, "frozen", False):
        log.info("(dev mode — skipping auto-download)")
        return

    assets = release.get("assets", [])

    def _pick_asset() -> dict | None:
        if IS_WINDOWS:
            return next((a for a in assets if a["name"].lower().endswith(".exe")), None)
        if IS_LINUX:
            linux_assets = [
                a for a in assets
                if "linux" in a["name"].lower() or a["name"].lower().endswith(".appimage")
            ]
            for ext in (".appimage", ".run", ".bin"):
                found = next((a for a in linux_assets if a["name"].lower().endswith(ext)), None)
                if found:
                    return found
        return None

    exe_asset = _pick_asset()
    if not exe_asset:
        log.warning("update: no compatible %s asset found in release %s", sys.platform, latest_tag)
        return

    total_bytes = exe_asset["size"]
    print(f"  Downloading {exe_asset['name']} ({total_bytes // 1024 // 1024} MB)...")
    log.info("update: downloading %s (%d bytes)", exe_asset["name"], total_bytes)
    exe_path = Path(sys.executable)
    asset_suffix = "".join(Path(exe_asset["name"]).suffixes)
    new_path = exe_path.with_name(f"_update_new{asset_suffix if asset_suffix else exe_path.suffix}")

    try:
        import time as _time
        dl_headers = dict(headers)
        dl_headers["Accept"] = "application/octet-stream"
        dl_req = urllib.request.Request(exe_asset["browser_download_url"], headers=dl_headers)
        with urllib.request.urlopen(dl_req, timeout=180) as r:
            _start = _time.time()
            _done  = 0
            _chunk = 65536  # 64 KB chunks
            with open(new_path, "wb") as f:
                while True:
                    buf = r.read(_chunk)
                    if not buf:
                        break
                    f.write(buf)
                    _done += len(buf)
                    _elapsed = max(_time.time() - _start, 0.001)
                    _speed   = _done / _elapsed          # bytes/sec
                    if total_bytes > 0:
                        _pct = min(100, _done * 100 // total_bytes)
                        _bar = "█" * (_pct // 3) + "░" * (33 - _pct // 3)
                        print(f"\r  [{_bar}] {_pct:3d}%  {_speed/1024:6.0f} KB/s", end="", flush=True)
                    else:
                        print(f"\r  {_done//1024} KB  {_speed/1024:.0f} KB/s", end="", flush=True)
        print()
        log.info("update: download complete in %.1fs", _time.time() - _start)
        if IS_LINUX:
            new_path.chmod(new_path.stat().st_mode | 0o755)
    except Exception as exc:
        print()
        log.warning("update download failed: %s", exc)
        try:
            new_path.unlink()
        except Exception:
            pass
        return

    print(f"  Restarting to apply {latest_tag}...\n")
    import subprocess
    if IS_WINDOWS:
        bat = exe_path.parent / "_update.bat"
        bat.write_text(
            "@echo off\n"
            "timeout /t 2 /nobreak >nul\n"
            f'move /y "{new_path}" "{exe_path}" >nul\n'
            f'start "" "{exe_path}"\n'
            "del \"%~f0\"\n",
            encoding="utf-8",
        )
        subprocess.Popen(["cmd", "/c", str(bat)], creationflags=subprocess.CREATE_NO_WINDOW)
    elif IS_LINUX:
        import shlex

        sh = exe_path.parent / "_update.sh"
        sh.write_text(
            "#!/bin/sh\n"
            "sleep 2\n"
            f"mv -f {shlex.quote(str(new_path))} {shlex.quote(str(exe_path))}\n"
            f"chmod +x {shlex.quote(str(exe_path))}\n"
            f"{shlex.quote(str(exe_path))} >/dev/null 2>&1 &\n"
            "rm -- \"$0\"\n",
            encoding="utf-8",
        )
        sh.chmod(sh.stat().st_mode | 0o755)
        subprocess.Popen(["/bin/sh", str(sh)], start_new_session=True)
    else:
        log.warning("update: restart script unsupported on %s", sys.platform)
        return
    sys.exit(0)


# ── connected browser clients ─────────────────────────────────────────────────
_clients: set = set()


def _json_sanitize(value):
    if isinstance(value, float):
        return value if math.isfinite(value) else 0.0
    if isinstance(value, list):
        return [_json_sanitize(item) for item in value]
    if isinstance(value, tuple):
        return [_json_sanitize(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_sanitize(item) for key, item in value.items()}
    return value


async def _ws_handler(websocket):
    _clients.add(websocket)
    log.info("browser connected  (%d total)", len(_clients))
    try:
        async for msg in websocket:
            try:
                data = json.loads(msg)
                if data.get("type") == "set_auto_update":
                    cfg = load_config()
                    cfg["auto_update"] = bool(data.get("value", True))
                    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
                    log.info("auto-update -> %s", cfg["auto_update"])
            except Exception:
                pass
    except Exception:
        pass
    finally:
        _clients.discard(websocket)
        log.info("browser disconnected (%d total)", len(_clients))


async def _broadcast(payload: str):
    dead = set()
    for ws in list(_clients):
        try:
            await ws.send(payload)
        except Exception:
            dead.add(ws)
    _clients.difference_update(dead)


def _static_path() -> str | None:
    if getattr(sys, "frozen", False):
        p = Path(sys._MEIPASS) / "webapp_dist"
    else:
        p = Path(__file__).parent.parent / "webapp" / "dist"
    if p.exists():
        return str(p)
    log.warning("static dir not found at %s — HTTP server disabled (run npm run build)", p)
    return None


def _maps_cache_path() -> Path:
    """Writable directory where auto-extracted map data is stored."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "maps_cache"
    return Path(__file__).parent.parent / "webapp" / "public" / "data"


# ── map extractor ─────────────────────────────────────────────────────────────
class MapExtractor:
    """Reads CS2 overview files and extracts map data on demand."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cs2_dir   = self._find_cs2()
        self._seen: set[str] = set()
        if self.cs2_dir:
            log.info("map extractor: CS2 found at %s", self.cs2_dir)
        else:
            log.warning("map extractor: CS2 install not found — auto-extraction disabled")

    def _find_cs2(self) -> Path | None:
        return find_cs2_root()

    def _parse_overview_text(self, content: str) -> dict | None:
        import re
        px = re.search(r'"pos_x"\s+"([^"]+)"', content)
        py = re.search(r'"pos_y"\s+"([^"]+)"', content)
        sc = re.search(r'"scale"\s+"([^"]+)"', content)
        if not (px and py and sc):
            return None
        try:
            return {"x": float(px.group(1)), "y": float(py.group(1)), "scale": float(sc.group(1))}
        except ValueError:
            return None

    def _parse_overview(self, txt: Path) -> dict | None:
        try:
            return self._parse_overview_text(txt.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            return None

    def _extract_from_vpk(self, map_name: str, out: Path) -> bool:
        """Extract map overview txt + radar png from CS2's pak01 VPK archives."""
        dir_vpk = self.cs2_dir / "game" / "csgo" / "pak01_dir.vpk"
        if not dir_vpk.exists():
            log.warning("map extractor: pak01_dir.vpk not found")
            return False
        try:
            raw = dir_vpk.read_bytes()
            sig, ver = struct.unpack_from("<II", raw, 0)
            if sig != 0x55AA1234:
                log.warning("map extractor: bad VPK signature 0x%X", sig)
                return False
            tree_start = 28 if ver == 2 else 12  # v2 has 16 extra header bytes

            pos = tree_start

            def read_str():
                nonlocal pos
                end = raw.index(b'\x00', pos)
                s = raw[pos:end].decode('utf-8', errors='ignore')
                pos = end + 1
                return s

            # What we want: (ext, path, name)
            targets = {
                ("txt", "resource/overviews", map_name),
                ("png", "resource/overviews", f"{map_name}_radar"),
            }
            found = {}

            while pos < len(raw):
                ext = read_str()
                if not ext:
                    break
                while True:
                    path = read_str()
                    if not path:
                        break
                    # VPK stores paths without leading slash, forward-slash separated
                    norm_path = path.replace("\\", "/").strip("/")
                    while True:
                        name = read_str()
                        if not name:
                            break
                        # VPKDirectoryEntry: crc32 preload_bytes archive_index entry_offset entry_length suffix
                        crc32, pre_bytes, arch_idx, entry_off, entry_len, suffix = \
                            struct.unpack_from("<IHHIIH", raw, pos)
                        pos += 18
                        preload = raw[pos:pos + pre_bytes]
                        pos += pre_bytes

                        key = (ext, norm_path, name)
                        if key in targets:
                            found[key] = (arch_idx, entry_off, entry_len, preload)

            def _read_entry(arch_idx, entry_off, entry_len, preload):
                if entry_len == 0:
                    return preload
                pak = self.cs2_dir / "game" / "csgo" / f"pak01_{arch_idx:03d}.vpk"
                with open(pak, "rb") as f:
                    f.seek(entry_off)
                    return preload + f.read(entry_len)

            txt_key = ("txt", "resource/overviews", map_name)
            png_key = ("png", "resource/overviews", f"{map_name}_radar")

            if txt_key not in found or png_key not in found:
                log.warning("map extractor: %s not found in VPK (txt=%s png=%s)",
                            map_name, txt_key in found, png_key in found)
                return False

            txt_bytes = _read_entry(*found[txt_key])
            png_bytes = _read_entry(*found[png_key])

            overview = self._parse_overview_text(txt_bytes.decode("utf-8", errors="ignore"))
            if not overview:
                log.warning("map extractor: failed to parse VPK overview txt for %s", map_name)
                return False

            out.mkdir(parents=True, exist_ok=True)
            (out / "data.json").write_text(json.dumps(overview))
            (out / "radar.png").write_bytes(png_bytes)
            (out / "background.png").write_bytes(png_bytes)
            (out / "callouts.json").write_text(json.dumps({"map": map_name, "callouts": []}))
            log.info("map extractor: VPK extracted %s  (x=%.0f y=%.0f scale=%.2f)",
                     map_name, overview["x"], overview["y"], overview["scale"])
            return True

        except Exception:
            log.exception("map extractor: VPK extraction failed for %s", map_name)
            return False

    def ensure(self, map_name: str) -> bool:
        """
        Extract map data from CS2 if not already in cache.
        Returns True if data is available (pre-existing or just extracted).
        """
        if map_name in self._seen or map_name == "invalid":
            return map_name in self._seen

        out = self.cache_dir / map_name
        if (out / "data.json").exists() and (out / "radar.png").exists():
            self._seen.add(map_name)
            return True

        if not self.cs2_dir:
            return False

        # Try loose files first (workshop maps / older installs)
        ov_dir  = self.cs2_dir / "game" / "csgo" / "resource" / "overviews"
        txt_src = ov_dir / f"{map_name}.txt"
        png_src = ov_dir / f"{map_name}_radar.png"

        if txt_src.exists() and png_src.exists():
            import shutil
            data = self._parse_overview(txt_src)
            if data:
                try:
                    out.mkdir(parents=True, exist_ok=True)
                    (out / "data.json").write_text(json.dumps(data))
                    shutil.copy(png_src, out / "radar.png")
                    shutil.copy(png_src, out / "background.png")
                    (out / "callouts.json").write_text(json.dumps({"map": map_name, "callouts": []}))
                    log.info("map extractor: extracted %s  (x=%.0f y=%.0f scale=%.2f)",
                             map_name, data["x"], data["y"], data["scale"])
                    self._seen.add(map_name)
                    return True
                except Exception as exc:
                    log.error("map extractor: write failed for %s: %s", map_name, exc)

        # Standard CS2 maps live inside VPK archives
        if self._extract_from_vpk(map_name, out):
            self._seen.add(map_name)
            return True

        log.warning("map extractor: could not extract %s (not in loose files or VPK)", map_name)
        return False
        return True


def _start_http(static_dir: str, maps_cache: Path):
    """
    Serve the webapp with a dual-directory handler:
      /data/<map>/* → maps_cache first, then static_dir fallback
      everything else → static_dir
    """
    import posixpath, urllib.parse, mimetypes

    _static = static_dir
    _maps   = maps_cache

    class _Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def log_error(self, *_): pass

        def _resolve(self, url_path: str) -> Path | None:
            p = urllib.parse.unquote(url_path).split("?")[0]
            p = posixpath.normpath(p).lstrip("/")
            parts = p.split("/")

            # /data/<map>/... → check maps_cache first
            if len(parts) >= 2 and parts[0] == "data":
                candidate = _maps / Path(*parts[1:])
                if candidate.exists() and candidate.is_file():
                    return candidate

            # fallback to static dir
            candidate = Path(_static) / Path(*parts) if parts else Path(_static)
            if candidate.is_file():
                return candidate
            # try index.html for SPA routes
            index = Path(_static) / "index.html"
            return index if index.exists() else None

        def do_GET(self):
            # Proxy WebSocket upgrades to the WS server at WS_PORT
            if (self.path.split("?")[0] == "/cs2_webradar" and
                    self.headers.get("Upgrade", "").lower() == "websocket"):
                self._proxy_ws()
                return
            path = self._resolve(self.path)
            if path is None or not path.exists():
                self.send_error(404)
                return
            mime, _ = mimetypes.guess_type(str(path))
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mime or "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(data)

        def _proxy_ws(self):
            import hashlib, base64
            key = self.headers.get("Sec-WebSocket-Key", "")
            accept = base64.b64encode(
                hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
            ).decode()
            # Complete the handshake with the browser
            self.send_response(101, "Switching Protocols")
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept)
            self.end_headers()
            self.wfile.flush()
            # Open a raw TCP connection to the actual WS server
            try:
                backend = socket.create_connection(("localhost", WS_PORT), timeout=5)
            except Exception as e:
                log.warning("ws proxy: backend connect failed: %s", e)
                return
            # Send a proper WebSocket upgrade to the backend
            backend.sendall((
                f"GET /cs2_webradar HTTP/1.1\r\n"
                f"Host: localhost:{WS_PORT}\r\n"
                f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
            ).encode())
            # Consume the backend's 101 response before forwarding raw frames
            resp = b""
            while b"\r\n\r\n" not in resp:
                chunk = backend.recv(4096)
                if not chunk:
                    backend.close()
                    return
                resp += chunk
            # Remove socket timeouts then forward bytes in both directions
            client_sock = self.connection
            client_sock.settimeout(None)
            backend.settimeout(None)
            def _fwd(src, dst):
                try:
                    while True:
                        d = src.recv(65536)
                        if not d:
                            break
                        dst.sendall(d)
                except Exception:
                    pass
                finally:
                    for s in (src, dst):
                        try: s.close()
                        except: pass
            t1 = threading.Thread(target=_fwd, args=(client_sock, backend), daemon=True)
            t2 = threading.Thread(target=_fwd, args=(backend, client_sock), daemon=True)
            t1.start(); t2.start()
            t1.join(); t2.join()
            # Tell the HTTP machinery not to reuse this socket after the proxy ends
            self.close_connection = True

    import socketserver
    class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
        def handle_error(self, request, client_address):
            # Suppress noisy but harmless "client closed connection" errors
            exc = sys.exc_info()[1]
            if isinstance(exc, (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)):
                return
            super().handle_error(request, client_address)

    srv = _Server(("0.0.0.0", HTTP_PORT), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log.info("HTTP  -> http://0.0.0.0:%d  (maps_cache=%s)", HTTP_PORT, _maps)


def _ensure_firewall_rules():
    """
    Ensure the local firewall allows inbound traffic on both ports when possible.
    Uses program-based rules (most reliable) + port-based rules as backup.
    Force-deletes then re-adds so locale/state issues never cause a stale rule.
    """
    import subprocess

    if IS_LINUX:
        def _sudo(args: list[str]) -> list[str] | None:
            if hasattr(os, "geteuid") and os.geteuid() == 0:
                return args
            sudo = shutil.which("sudo")
            return [sudo, "-n", *args] if sudo else None

        def _run(args: list[str] | None, timeout: int = 20) -> tuple[bool, str]:
            if not args:
                return False, "sudo unavailable"
            try:
                r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
                return r.returncode == 0, (r.stdout + r.stderr).strip()
            except Exception as exc:
                return False, str(exc)

        touched = False
        if shutil.which("ufw"):
            ok, out = _run(["ufw", "status"], timeout=10)
            if ok and "status: active" in out.lower():
                for port in (WS_PORT, HTTP_PORT):
                    ok, msg = _run(_sudo(["ufw", "allow", f"{port}/tcp"]), timeout=30)
                    if ok:
                        touched = True
                        log.info("firewall: ufw allowed tcp/%d", port)
                    else:
                        log.warning("firewall: ufw allow tcp/%d failed: %s", port, msg)

        if shutil.which("firewall-cmd"):
            ok, _ = _run(["firewall-cmd", "--state"], timeout=10)
            if ok:
                for port in (WS_PORT, HTTP_PORT):
                    ok, msg = _run(_sudo(["firewall-cmd", "--add-port", f"{port}/tcp"]), timeout=30)
                    if ok:
                        touched = True
                        log.info("firewall: firewalld allowed tcp/%d", port)
                    else:
                        log.warning("firewall: firewalld allow tcp/%d failed: %s", port, msg)

        if not touched:
            log.info("firewall: no active Linux firewall was changed; ensure tcp/%d and tcp/%d are reachable if needed",
                     WS_PORT, HTTP_PORT)
        return

    if not IS_WINDOWS:
        log.info("firewall: unsupported platform %s; skipping automatic rules", sys.platform)
        return

    exe = sys.executable  # path to the running exe (or python.exe in dev)

    def _netsh(*args):
        r = subprocess.run(
            ["netsh", "advfirewall", "firewall", *args],
            capture_output=True, text=True, encoding="utf-8", errors="ignore"
        )
        return r.returncode == 0, (r.stdout + r.stderr).strip()

    # 1. Program-based rule — allows all ports used by this exe
    prog_rule = "CS2Radar-Program"
    _netsh("delete", "rule", f"name={prog_rule}")  # remove stale copy if any
    ok, out = _netsh(
        "add", "rule", f"name={prog_rule}",
        "dir=in", "action=allow", "protocol=TCP",
        f"program={exe}", "enable=yes",
    )
    if ok:
        log.info("firewall: program rule added for %s", exe)
    else:
        log.warning("firewall: program rule failed: %s", out)

    # 2. Port-based rules as fallback
    for rule_name, port in [("CS2Radar-WS", WS_PORT), ("CS2Radar-HTTP", HTTP_PORT)]:
        _netsh("delete", "rule", f"name={rule_name}")
        ok, out = _netsh(
            "add", "rule", f"name={rule_name}",
            "dir=in", "action=allow", "protocol=TCP",
            f"localport={port}", "enable=yes",
        )
        if ok:
            log.info("firewall: port rule added  %s -> %d", rule_name, port)
        else:
            log.warning("firewall: port rule failed %s: %s", rule_name, out)


# ── main async loop ───────────────────────────────────────────────────────────
async def _run_async():
    load_config()
    _ensure_firewall_rules()
    offsets = load_offsets()
    # Baseline for auto-refresh: the client-module mtime that `offsets` reflects.
    # When the on-disk module changes (CS2 update) we re-run load_offsets().
    offset_dll_mtime = _client_dll_mtime()
    _last_offset_check = time.monotonic()

    mem    = Memory()
    reader: CS2Reader | None = None
    _last_waiting_log = 0.0
    loop = asyncio.get_event_loop()
    process_names = cs2_process_names()
    process_label = ", ".join(process_names)

    # host=None → bind all interfaces (IPv4 + IPv6) so WebView2's ::1 also works
    async with websockets.serve(_ws_handler, None, WS_PORT):
        log.info("WS    -> ws://localhost:%d/cs2_webradar", WS_PORT)

        static_dir = _static_path()
        maps_cache = _maps_cache_path()
        extractor  = MapExtractor(maps_cache)
        if static_dir:
            try:
                _start_http(static_dir, maps_cache)
            except OSError as e:
                log.warning("HTTP server could not start on port %d (%s) — Vite dev server already running there", HTTP_PORT, e)
            webbrowser.open(f"http://localhost:{HTTP_PORT}")

        _last_map = None
        _none_streak = 0          # consecutive collect() → None frames
        _last_good_data = 0.0     # time of last successful broadcast

        _setup_failures = 0
        _did_local_scan  = False

        while True:
            # ── ensure CS2 is open ────────────────────────────────────────────
            if not mem.handle:
                pid = await loop.run_in_executor(None, lambda: mem.find_pid(process_names[0]))
                if not pid:
                    log.info("waiting for CS2 process to start (%s)...", process_label)
                    await asyncio.sleep(3)
                    continue
                if not mem.open(pid):
                    if IS_WINDOWS:
                        log.error("OpenProcess failed — run as administrator")
                    elif IS_LINUX:
                        log.error("process memory open failed — run with ptrace permission/root")
                    else:
                        log.error("process memory open failed")
                    await asyncio.sleep(3)
                    continue
                log.info("found CS2  pid=%d", pid)
                if IS_LINUX and extractor.cs2_dir is None:
                    cs2_root = find_cs2_root_from_pid(pid)
                    if cs2_root:
                        extractor.cs2_dir = cs2_root
                        log.info("CS2 root resolved from process maps: %s", cs2_root)
                reader = None
                _setup_failures = 0
                # Force an offset freshness check on this newly-attached process:
                # CS2 may have been updated and relaunched while we kept running.
                _last_offset_check = 0.0

            # ── auto-refresh offsets when the game updates ────────────────────
            # The client module's on-disk mtime is the ground-truth "game was
            # updated" signal. When it advances past what `offsets` reflects,
            # re-run load_offsets() (re-fetch cs2-dumper + re-scan the binary)
            # and rebuild the reader so setup() re-resolves everything live —
            # no manual restart needed. Throttled; also fired once per attach.
            now_m = time.monotonic()
            if mem.handle and now_m - _last_offset_check >= OFFSET_RECHECK_SEC:
                _last_offset_check = now_m
                cur_mtime = await loop.run_in_executor(
                    None, lambda: _client_dll_mtime(mem.pid))
                if cur_mtime and cur_mtime > offset_dll_mtime + 1.0:
                    log.info("client module changed on disk (game update) — "
                             "auto-refreshing offsets")
                    try:
                        new_off = await loop.run_in_executor(None, load_offsets)
                        offsets.clear()
                        offsets.update(new_off)
                        offset_dll_mtime = cur_mtime
                        reader = None            # force setup() with fresh offsets
                        _did_local_scan = False
                    except Exception as exc:
                        # Keep running on the current offsets; retry next interval.
                        log.warning("offset auto-refresh failed (%s) — keeping current offsets", exc)

            # ── detect stale handle (CS2 restarted without triggering OSError) ─
            # After several consecutive setup failures, verify the process is
            # still alive. ReadProcessMemory on a dead handle returns zeros
            # silently, which looks identical to "entity system not ready".
            if reader is None and _setup_failures > 0 and _setup_failures % 10 == 0:
                live_pid = await loop.run_in_executor(None, lambda: mem.find_pid(process_names[0]))
                if live_pid and live_pid != mem.pid:
                    log.warning("CS2 restarted (old pid=%d new pid=%d) — reopening handle",
                                mem.pid, live_pid)
                    mem.close()
                    continue   # re-enter loop to reopen handle

            # ── ensure reader is initialised ──────────────────────────────────
            if reader is None:
                r = CS2Reader(mem, offsets)
                ok = await loop.run_in_executor(None, r.setup)
                if not ok:
                    _setup_failures += 1
                    # After ~5s of null entity-system, scan client.dll for updated offsets
                    if not _did_local_scan and _setup_failures >= 5:
                        log.warning("offsets may be stale — scanning client.dll for updated values...")
                        patched = await loop.run_in_executor(None, lambda: _scan_globals_from_dll(offsets, mem.pid))
                        _did_local_scan = True
                        if patched:
                            log.info("offset scan complete — retrying with new values")
                    now = time.time()
                    if now - _last_waiting_log >= 5:
                        log.info("waiting for CS2 to load into a game...")
                        _last_waiting_log = now
                    await asyncio.sleep(1)
                    continue
                _setup_failures = 0
                reader = r
                log.info("reader ready — watching entity list at 10 Hz")

            # ── collect + broadcast ───────────────────────────────────────────
            try:
                data = await loop.run_in_executor(None, reader.collect)
                if data is None:
                    _none_streak += 1
                    now = time.time()
                    if now - _last_waiting_log >= 5:
                        log.info("in CS2 but not in an active match (team=spectator/none)")
                        _last_waiting_log = now
                    # If data has been None for >30s and we had good data before,
                    # verify CS2 is still alive (silent RPM failure won't raise OSError
                    # until the next valid address read; this catches the gap).
                    if _none_streak > 100 and _last_good_data > 0:
                        live_pid = await loop.run_in_executor(None, lambda: mem.find_pid(process_names[0]))
                        if not live_pid or live_pid != mem.pid:
                            log.warning("CS2 gone (watchdog) — detaching")
                            mem.close()
                            reader = None
                            _none_streak = 0
                else:
                    _none_streak = 0
                    _last_good_data = time.time()
                    map_name = data.get("m_map", "invalid")
                    if map_name != _last_map and map_name != "invalid":
                        _last_map = map_name
                        await loop.run_in_executor(None, extractor.ensure, map_name)
                    await _broadcast(json.dumps(_json_sanitize(data), allow_nan=False))
            except OSError as exc:
                log.warning("CS2 process lost (%s) — detaching", exc)
                mem.close()
                reader = None
                _none_streak = 0
            except Exception as exc:
                log.error("unexpected error in collect/send: %s", exc, exc_info=True)

            await asyncio.sleep(POLL_INTERVAL)


def run(overlay: bool = False, esp: bool = False):
    mode = "esp" if esp else ("minimap" if overlay else "browser")
    log.info("mode: %s", mode)

    if mode in ("minimap", "esp"):
        base_url = (f"http://localhost:{HTTP_PORT}"
                    if _static_path() is not None
                    else "http://localhost:5173")
        log.info("%s will load from %s", mode, base_url)

        t = threading.Thread(
            target=lambda: asyncio.run(_run_async()),
            daemon=True, name="radar-backend"
        )
        t.start()
        time.sleep(1.5)

        if mode == "minimap":
            import overlay as ov
            ov.start(f"{base_url}?mode=minimap")
        else:
            import esp as esp_mod
            esp_mod.start(f"{base_url}?mode=esp")

        t.join()
    else:
        asyncio.run(_run_async())


def _pick_mode() -> str:
    """
    Interactive mode selector shown at startup when no CLI flag is passed.
    Returns "browser", "minimap", or "esp".
    """
    print("\n" + "=" * 50)
    print("  CS2 Radar — Select Mode")
    print("=" * 50)
    print("  1  Normal  — browser radar (open in any browser)")
    print("  2  Overlay — small draggable minimap on screen")
    print("  3  ESP     — full-screen transparent player boxes")
    print("               (CS2 must be in Borderless Windowed!)")
    print("=" * 50)

    while True:
        try:
            choice = input("  Enter 1 / 2 / 3: ").strip()
        except (EOFError, KeyboardInterrupt):
            choice = "1"

        if choice == "1":
            print("  -> Normal mode\n")
            return "browser"
        elif choice == "2":
            print("  -> Minimap overlay  (drag the bar to reposition)\n")
            return "minimap"
        elif choice == "3":
            print("  -> ESP overlay  (Ctrl+C in this window to exit)\n")
            return "esp"
        else:
            print("  Please enter 1, 2 or 3.")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="CS2 Radar")
    ap.add_argument("--normal",     action="store_true", help="Browser radar mode (default)")
    ap.add_argument("--overlay",    action="store_true", help="Small draggable minimap overlay")
    ap.add_argument("--esp",        action="store_true", help="Full-screen transparent ESP overlay")
    ap.add_argument("--no-funnel",  action="store_true", help="Skip Tailscale Funnel this run")
    args = ap.parse_args()

    cfg = load_config()
    threading.Thread(target=_check_for_update, args=(cfg,), daemon=True).start()

    if args.no_funnel:
        log.info("tailscale: skipped (--no-funnel)")
        _FUNNEL_URL = None
    else:
        _FUNNEL_URL = _setup_tailscale(cfg)

    if args.esp:
        _mode = "esp"
    elif args.overlay:
        _mode = "minimap"
    elif args.normal:
        _mode = "browser"
    else:
        _mode = _pick_mode()

    run(overlay=(_mode == "minimap"), esp=(_mode == "esp"))
