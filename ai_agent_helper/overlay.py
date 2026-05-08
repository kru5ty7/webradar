"""
CS2 Radar minimap overlay — small always-on-top window showing the 2D radar.
Frameless, draggable by the top bar, positioned top-left by default.
"""
import ctypes
import ctypes.wintypes
import logging
import time

log = logging.getLogger("radar")

# Win32 constants
GWL_EXSTYLE        = -20
WS_EX_LAYERED      = 0x00080000
WS_EX_TRANSPARENT  = 0x00000020
SWP_NOSIZE         = 0x0001
SWP_NOZORDER       = 0x0004
HWND_TOPMOST       = -1

# Default minimap window size (pixels)
MINIMAP_W = 320
MINIMAP_H = 340  # slightly taller to leave room for the drag bar

_hwnd: int = 0

# Primary monitor resolution (for bounds clamping when dragging)
_SW = ctypes.windll.user32.GetSystemMetrics(0)
_SH = ctypes.windll.user32.GetSystemMetrics(1)


def _get_hwnd(win) -> int:
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
    Provides the move() call so JS drag events can reposition the window.
    """

    def __init__(self):
        self._hwnd = 0

    def set_hwnd(self, hwnd: int):
        self._hwnd = hwnd

    def move(self, x: float, y: float):
        """Move the overlay window to screen position (x, y)."""
        if not self._hwnd:
            return
        # Clamp so the window can't be dragged fully off-screen
        x = max(0, min(int(x), _SW - MINIMAP_W))
        y = max(0, min(int(y), _SH - MINIMAP_H))
        ctypes.windll.user32.SetWindowPos(
            self._hwnd, 0, x, y, 0, 0,
            SWP_NOSIZE | SWP_NOZORDER,
        )

    def get_position(self):
        """Return current [x, y] screen position of the window."""
        if not self._hwnd:
            return [10, 10]
        rect = ctypes.wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(self._hwnd, ctypes.byref(rect))
        return [rect.left, rect.top]

    def close(self):
        """Called from the × button in the drag bar."""
        if self._hwnd:
            ctypes.windll.user32.PostMessageW(self._hwnd, 0x0010, 0, 0)  # WM_CLOSE


def start(url: str, width: int = MINIMAP_W, height: int = MINIMAP_H,
          x: int = 10, y: int = 10):
    """
    Small draggable minimap overlay.
    Blocks until the window is closed — call from the main thread.
    """
    try:
        import webview
    except ImportError:
        log.error("pywebview not installed. Run: pip install pywebview")
        return

    global _hwnd

    api = _OverlayAPI()

    win = webview.create_window(
        "CS2Radar",
        url,
        x=x, y=y,
        width=width,
        height=height,
        resizable=True,
        frameless=True,
        on_top=True,
        transparent=False,
        background_color="#0a141e",
        text_select=False,
        zoomable=False,
        js_api=api,
    )

    def _on_shown():
        global _hwnd
        time.sleep(0.3)
        _hwnd = _get_hwnd(win)
        if not _hwnd:
            log.warning("overlay: could not get HWND")
            return
        api.set_hwnd(_hwnd)
        ctypes.windll.user32.SetWindowPos(_hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOSIZE | 0x0002)
        ex = ctypes.windll.user32.GetWindowLongW(_hwnd, GWL_EXSTYLE)
        ex |= WS_EX_LAYERED
        ctypes.windll.user32.SetWindowLongW(_hwnd, GWL_EXSTYLE, ex)
        log.info("minimap overlay ready  hwnd=0x%X  %dx%d", _hwnd, width, height)

    win.events.shown += _on_shown
    log.info("overlay: minimap  %dx%d @ (%d,%d) → %s", width, height, x, y, url)
    webview.start(gui="edgechromium", debug=False)
    log.info("overlay: closed")


def start_esp(*_):
    """Replaced by pygame-based ESP — see esp_overlay.py."""
    log.info("start_esp: use esp_overlay.py instead")
