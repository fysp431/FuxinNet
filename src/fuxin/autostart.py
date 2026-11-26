"""开机自启。

两种作用域（界面下拉可选）：

1. 用户级（已实现，默认）—— 当前用户登录后启动，写
       HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run
   无需管理员权限，不弹 UAC。值内容形如：
       "D:\\path\\福信校网通.exe" --min
   带 --min 表示开机后直接驻留托盘，不弹主窗口。

2. 系统级（预留接口，尚未开发）—— 计划是写 HKLM 的同名键（或注册计划任务，
   /RL HIGHEST），对所有用户生效，但需要管理员权限（UAC 提权）。
   本文件已经把调用面固定成 `scope="system"`，实现时只需填充
   `_enable_system()` / `_disable_system()` / `_is_enabled_system()`，
   上层 api/界面无需再改。
"""

from __future__ import annotations

import os
import sys
from typing import Optional

SCOPE_USER = "user"
SCOPE_SYSTEM = "system"
SCOPES = (SCOPE_USER, SCOPE_SYSTEM)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def normalize_scope(scope: Optional[str]) -> str:
    value = (scope or SCOPE_USER).strip().lower()
    return value if value in SCOPES else SCOPE_USER


def is_supported(scope: str) -> bool:
    """系统级自启暂未实现，界面据此把开关置灰。"""
    return normalize_scope(scope) == SCOPE_USER


def _exe_path() -> str:
    """当前可执行文件路径（打包后即 exe 本身）。"""
    if getattr(sys, "frozen", False):
        return os.path.abspath(sys.executable)
    # 源码运行时用 pythonw.exe 没有意义，返回空串交给上层拒绝
    return ""


def _value_data() -> Optional[str]:
    exe = _exe_path()
    if not exe:
        return None
    from . import paths

    return f'"{exe}" {paths.MINIMIZED_FLAG}'


def _value_name() -> str:
    from . import paths

    return paths.RUN_VALUE_NAME


# ---------------------------------------------------------------- 用户级实现
def _is_enabled_user() -> bool:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            try:
                value, _ = winreg.QueryValueEx(key, _value_name())
            except FileNotFoundError:
                return False
    except OSError:
        return False

    value = str(value)
    exe = _exe_path()
    if not exe:
        return bool(value)
    # 只认指向当前 exe 的条目，换目录/换版本后需重新打开开关
    return os.path.normcase(exe) in os.path.normcase(value)


def _enable_user() -> tuple[bool, str]:
    data = _value_data()
    if not data:
        return False, "源码运行模式不支持写入自启，请使用打包后的 exe"
    try:
        import winreg

        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, _value_name(), 0, winreg.REG_SZ, data)
        return True, data
    except OSError as exc:
        return False, f"写入注册表失败：{exc}"


def _disable_user() -> tuple[bool, str]:
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, _value_name())
        return True, ""
    except FileNotFoundError:
        return True, ""
    except OSError as exc:
        return False, f"删除注册表项失败：{exc}"


# ---------------------------------------------------------------- 系统级（预留）
def _is_enabled_system() -> bool:
    """TODO(system-scope): 读取 HKLM 同名值或计划任务状态。"""
    return False


def _enable_system() -> tuple[bool, str]:
    """TODO(system-scope):
    1) 通过 ShellExecuteW(..., "runas", ...) 提权启动自身代理进程；
    2) 代理进程写 HKLM\\...\\Run（或 schtasks /Create /RL HIGHEST）；
    3) 回传结果。
    未实现前统一返回"开发中"。
    """
    return False, "系统级自启开发中，敬请期待"


def _disable_system() -> tuple[bool, str]:
    """TODO(system-scope): 提权删除 HKLM 值或计划任务。"""
    return False, "系统级自启开发中，敬请期待"


# ---------------------------------------------------------------- 对外接口
def is_enabled(scope: str = SCOPE_USER) -> bool:
    if normalize_scope(scope) == SCOPE_SYSTEM:
        return _is_enabled_system()
    return _is_enabled_user()


def enable(scope: str = SCOPE_USER) -> tuple[bool, str]:
    if normalize_scope(scope) == SCOPE_SYSTEM:
        return _enable_system()
    return _enable_user()


def disable(scope: str = SCOPE_USER) -> tuple[bool, str]:
    if normalize_scope(scope) == SCOPE_SYSTEM:
        return _disable_system()
    return _disable_user()
