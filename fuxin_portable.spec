# -*- mode: python ; coding: utf-8 -*-
"""免解压（onedir）打包配置：生成一个文件夹，双击其中的 exe 直接运行。

用途：如果单文件版因为临时目录策略（杀软/重定向/权限）无法解包，
可以用这个目录版，行为完全一致。
    python -m PyInstaller --noconfirm --clean fuxin_portable.spec

产物目录名形如 FuxinNet_v2.0.1-alpha1\：版本号自动从 src/fuxin/paths.py 的
APP_VERSION 读取，发版时只需改 paths.py 一处，无需再同步本文件。
"""

import os
import re

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
    [],
    exclude_binaries=True,
    name=EXE_NAME,
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon="assets/icon.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name=EXE_NAME,
)
