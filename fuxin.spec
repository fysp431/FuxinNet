# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（单文件 exe）。

构建（必须用 Python 3.11 + pythonnet 3.0.5 的环境）：
    python -m PyInstaller --noconfirm --clean fuxin.spec
"""

from PyInstaller.utils.hooks import collect_all

datas = [
    ("ui", "ui"),
    ("assets/icon.ico", "assets"),
    ("assets/icon.png", "assets"),
]
binaries = []
hiddenimports = [
    "webview.platforms.winforms",
    "webview.platforms.edgechromium",
    "clr",
    "pystray",
    "pystray._win32",
    "PIL",
    "PIL.Image",
    "PIL.ImageDraw",
]

# pythonnet 需要把 Python.Runtime.dll 等运行期文件一起带上
for package in ("pythonnet", "clr_loader"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

a = Analysis(
    ["run.py"],
    pathex=["src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "setuptools", "pip"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="福信校网通",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=".",
    console=False,
    disable_windowed_traceback=False,
    icon="assets/icon.ico",
    version=None,
)
