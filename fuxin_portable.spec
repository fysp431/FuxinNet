# -*- mode: python ; coding: utf-8 -*-
"""免解压（onedir）打包配置：生成一个文件夹，双击其中的 exe 直接运行。

用途：如果单文件版因为临时目录策略（杀软/重定向/权限）无法解包，
可以用这个目录版，行为完全一致。
    python -m PyInstaller --noconfirm --clean fuxin_portable.spec
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
    name="福信校网通",
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
    name="福信校网通",
)
