# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller recipe for the service embedded by the Electron desktop app."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files


ROOT = Path(SPECPATH).resolve()
PACKAGE = ROOT / "local_app" / "package"
FRONTEND_RUNTIME = PACKAGE / "frontend_runtime"
NODE = PACKAGE / "node" / "node.exe"
FFMPEG = PACKAGE / "ffmpeg"

datas = [
    (str(ROOT / "local_app" / "review_ui.js"), "local_app"),
    (str(FRONTEND_RUNTIME), "frontend_runtime"),
]
binaries = [(str(NODE), "node")]
hiddenimports = [
    "aiosqlite",
    "sqlalchemy.dialects.sqlite.aiosqlite",
    "src.sim.tool_hang",
]

# Simulation and video packages contain XML models, shared libraries and
# helper executables which static import analysis cannot discover reliably.
datas += collect_data_files("robosuite")
for package_name in ("mujoco", "glfw", "imageio_ffmpeg"):
    package_datas, package_binaries, package_hidden = collect_all(package_name)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

if FFMPEG.is_dir():
    for executable in FFMPEG.glob("*.exe"):
        binaries.append((str(executable), "ffmpeg"))

a = Analysis(
    [str(ROOT / "local_app" / "web_shell.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "PySide6", "webview", "pythonnet", "clr_loader",
        "torch", "tensorflow", "pytest", "matplotlib", "dvc",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TeleCollectBackend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="TeleCollectBackend",
)
