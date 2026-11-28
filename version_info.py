"""生成 Windows 可执行文件的版本资源（右键 → 属性 → 详细信息）。

打包时由 ``fuxin.spec`` / ``fuxin_portable.spec`` 调用，版本号唯一来源仍是
``src/fuxin/paths.py`` 的 ``APP_VERSION``，避免两处维护。
"""

from __future__ import annotations

import os
import re

_VERSION_LANG_ID = 2052  # 中文（简体）
_VERSION_CODEPAGE = 1200  # Unicode
_STRING_TABLE_KEY = f"{_VERSION_LANG_ID:04x}{_VERSION_CODEPAGE:04x}"


def version_numbers(version: str) -> tuple[int, int, int, int]:
    """把 ``v2.0.1`` / ``2.0.1.0`` 归一化成四段数字版本。"""
    cleaned = (version or "").strip().lstrip("vV")
    numbers: list[int] = []
    for part in cleaned.split("."):
        match = re.match(r"\d+", part)
        numbers.append(int(match.group()) if match else 0)
    numbers = (numbers + [0, 0, 0, 0])[:4]
    return numbers[0], numbers[1], numbers[2], numbers[3]


def write_version_file(version: str, exe_name: str, target_dir: str) -> str:
    """写出 PyInstaller 可读取的版本资源文件，返回其路径。"""
    numbers = version_numbers(version)
    text_version = f"{numbers[0]}.{numbers[1]}.{numbers[2]}"
    os.makedirs(target_dir, exist_ok=True)
    path = os.path.join(target_dir, "version_info.txt")
    content = f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={numbers},
    prodvers={numbers},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
    ),
  kids=[
    StringFileInfo(
      [
      StringTable(
        '{_STRING_TABLE_KEY}',
        [StringStruct('CompanyName', 'fysp431'),
        StringStruct('FileDescription', '福信校网通 校园网自动认证助手'),
        StringStruct('FileVersion', '{text_version}'),
        StringStruct('InternalName', 'FuxinNet'),
        StringStruct('LegalCopyright', 'Copyright (C) fysp431'),
        StringStruct('OriginalFilename', '{exe_name}.exe'),
        StringStruct('ProductName', '福信校网通'),
        StringStruct('ProductVersion', '{text_version}')])
      ]),
    VarFileInfo([VarStruct('Translation', [{_VERSION_LANG_ID}, {_VERSION_CODEPAGE}])])
  ]
)
"""
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)
    return path
