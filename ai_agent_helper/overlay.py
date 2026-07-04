"""
CS2 Radar minimap/ESP web overlay.

Windows gets the original Win32 always-on-top tweaks. Linux uses pywebview's
portable window APIs, so the overlay can start without importing user32.dll.
"""
from __future__ import annotations

import logging
import sys
import time

log = logging.getLogger("radar")

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import ctypes
    import ctypes.wintypes

    # Win32 constants
    GWL_EXSTYLE        = -20
    WS_EX_LAYERED      = 0x00080000
    SWP_NOSIZE         = 0x0001
    SWP_NOZORDER       = 0x0004
    HWND_TOPMOST       = -1

    _SW = ctypes.windll.user32.GetSystemMetrics(0)
    _SH = ctypes.windll.user32.GetSystemMetrics(1)
else:
    ctypes = None
    GWL_EXSTYLE = WS_EX_LAYERED = SWP_NOSIZE = SWP_NOZORDER = HWND_TOPMOST = 0
    _SW = 100_000
    _SH = 100_000


# Default minimap window size (pixels)
MINIMAP_W = 320
MINIMAP_H = 340  # slightly taller to leave room for the drag bar

_hwnd: int = 0


def _get_hwnd(win) -> int:
    if not IS_WINDOWS:
        return 0
    try:
        h = int(win.native_handle)
        if h:
            return h
    except Exception:
        pass
    return ctypes.windll.user32.FindWindowW(None, win.title) or 0


class _OverlayAPI:
    """
    Exposed to the webapp via window.pywebview.api.
    Provides move()/close() calls so JS drag events can reposition the window.
    """

    def __init__(self, width: int, height: int, x: int, y: int):
        self._hwnd = 0
        self._win = None
        self._width = width
        self._height = height
        self._pos = [x, y]

    def set_window(self, win):
        self._win = win

    def set_hwnd(self, hwnd: int):
        self._hwnd = hwnd

    def move(self, x: float, y: float):
        """Move the overlay window to screen position (x, y)."""
        x = max(0, min(int(x), _SW - self._width))
        y = max(0, min(int(y), _SH - self._height))
        self._pos = [x, y]

        if IS_WINDOWS and self._hwnd:
            ctypes.windll.user32.SetWindowPos(
                self._hwnd, 0, x, y, 0, 0,
                SWP_NOSIZE | SWP_NOZORDER,
            )
            return

        if self._win is not None and hasattr(self._win, "move"):
            try:
                self._win.move(x, y)
            except Exception:
                log.debug("overlay: portable move failed", exc_info=True)

    def get_position(self):
        """Return current [x, y] screen position of the window."""
        if IS_WINDOWS and self._hwnd:
            rect = ctypes.wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(self._hwnd, ctypes.byref(rect))
            return [rect.left, rect.top]
        return list(self._pos)

    def close(self):
        """Called from the close button in the drag bar."""
        if IS_WINDOWS and self._hwnd:
            ctypes.windll.user32.PostMessageW(self._hwnd, 0x0010, 0, 0)  # WM_CLOSE
            return
        if self._win is not None:
            try:
                self._win.destroy()
            except Exception:
                log.debug("overlay: portable close failed", exc_info=True)


def start(url: str, width: int = MINIMAP_W, height: int = MINIMAP_H,
          x: int = 10, y: int = 10, *, fullscreen: bool = False,
          transparent: bool = False, title: str = "CS2Radar"):
    """
    Start a pywebview overlay window.
    Blocks until the window is closed; call from the main thread.
    """
    try:
        import webview
    except ImportError:
        log.error("pywebview not installed. Run: pip install pywebview")
        return

    global _hwnd

    api = _OverlayAPI(width, height, x, y)

    win = webview.create_window(
        title,
        url,
        x=x, y=y,
        width=width,
        height=height,
        resizable=True,
        frameless=True,
        on_top=True,
        transparent=transparent,
        background_color="#00000000" if transparent else "#0a141e",
        text_select=False,
        zoomable=False,
        fullscreen=fullscreen,
        js_api=api,
    )
    api.set_window(win)

    def _on_shown():
        global _hwnd
        time.sleep(0.3)
        if not IS_WINDOWS:
            log.info("overlay ready via pywebview  %dx%d", width, height)
            return

        _hwnd = _get_hwnd(win)
        if not _hwnd:
            log.warning("overlay: could not get HWND")
            return
        api.set_hwnd(_hwnd)
        ctypes.windll.user32.SetWindowPos(_hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOSIZE | 0x0002)
        ex = ctypes.windll.user32.GetWindowLongW(_hwnd, GWL_EXSTYLE)
        ex |= WS_EX_LAYERED
        ctypes.windll.user32.SetWindowLongW(_hwnd, GWL_EXSTYLE, ex)
        log.info("overlay ready  hwnd=0x%X  %dx%d", _hwnd, width, height)

    win.events.shown += _on_shown
    log.info("overlay: %dx%d @ (%d,%d) -> %s", width, height, x, y, url)
    if IS_WINDOWS:
        webview.start(gui="edgechromium", debug=False)
    else:
        webview.start(debug=False)
    log.info("overlay: closed")
