# -*- mode: python ; coding: utf-8 -*-
import os
import sys

block_cipher = None
_IS_WINDOWS = sys.platform == 'win32'

_ESP = os.environ.get(
    'ESP_BIN',
    os.path.abspath(os.path.join('..', 'cs2-external-esp', 'x64', 'Release', 'cs2-external-esp.exe')),
)
_extra_datas = [(_ESP, 'esp_bin')] if _IS_WINDOWS and os.path.exists(_ESP) else []
_webview_platforms = (
    ['webview.platforms.edgechromium'] if _IS_WINDOWS
    else ['webview.platforms.gtk', 'webview.platforms.qt']
)

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('../webapp/dist', 'webapp_dist'),
        *_extra_datas,
    ],
    hiddenimports=[
        'websockets', 'websockets.server', 'websockets.connection',
        'websockets.exceptions', 'websockets.frames', 'websockets.http11',
        'websockets.streams', 'websockets.legacy', 'websockets.legacy.server',
        'webview', 'webview.platforms', *_webview_platforms,
        'webview.js', 'webview.event', 'webview.util', 'webview.screen',
        'webview.window', 'webview.menu',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='GameOverlayService' if _IS_WINDOWS else 'cs2-radar',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    manifest='radar.manifest' if _IS_WINDOWS else None,
    uac_admin=_IS_WINDOWS,
)
