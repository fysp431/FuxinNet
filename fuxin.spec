# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（单文件 exe）。

构建（必须用 Python 3.11 + pythonnet 3.0.5 的环境）：
    python -m PyInstaller --noconfirm --clean fuxin.spec

产物名形如 FuxinNet_v2.0.1.exe：版本号自动从 src/fuxin/paths.py 的
APP_VERSION 读取，发版时只需改 paths.py 一处，无需再同步本文件。
同时自动写入 Windows 版本资源，可在「右键 → 属性 → 详细信息」查看版本。
"""

import os
import re
import sys

from PyInstaller.utils.hooks import collect_all


def _read_version() -> str:
    """从 paths.py 读 APP_VERSION；读不到时回落到不带版本号的名字。"""
    base = globals().get("SPECPATH") or os.getcwd()
    target = os.path.join(base, "src", "fuxin", "paths.py")
    try:
        with open(target, "r", encoding="utf-8") as fh:
            match = re.search(r'^APP_VERSION\s*=\s*["\']([^"\']+)["\']', fh.read(), re.M)
        return match.group(1) if match else ""
    except OSError:
        return ""


_VERSION = _read_version()
EXE_NAME = f"FuxinNet_{_VERSION}" if _VERSION else "FuxinNet"

# 版本资源：右键 → 属性 → 详细信息里显示的「文件版本 / 产品版本」
_BASE = globals().get("SPECPATH") or os.getcwd()
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

from version_info import write_version_file  # noqa: E402

_VERSION_FILE = write_version_file(_VERSION, EXE_NAME, os.path.join(_BASE, "build"))

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
    name=EXE_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=r"%LOCALAPPDATA%\FuxinNet\tmp",
    console=False,
    disable_windowed_traceback=False,
    icon="assets/icon.ico",
    version=_VERSION_FILE,
)
