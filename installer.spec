# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

project = Path(SPECPATH)
extension_files = [
    "manifest.json",
    "background.js",
    "content.js",
    "popup.css",
    "popup.html",
    "popup.js",
    "shared.js",
    "sidepanel.html",
    "sidepanel.css",
    "logoYTB.png",
    "icon.svg",
]

datas = [(str(project / "extension" / filename), f"payload/extension") for filename in extension_files]
binaries = [(str(project / "dist" / "YTQuickDownload.exe"), "payload")]

a = Analysis(
    [str(project / "installer.py")],
    pathex=[str(project)],
    binaries=binaries,
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="YTQuickDownloadSetup",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
)
