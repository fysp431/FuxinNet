"""路径与常量。

运行期文件优先放在程序（exe）同目录；该目录不可写（如 ``C:\\Program Files``、
只读介质、被杀软锁定的目录）时自动回退到 ``%LOCALAPPDATA%\\FuxinNet``，
避免配置、日志、运行标记全部静默失效。

兼容性：回退时若旧位置存在 ``fuxin_config.dat`` 而新位置没有，会先复制一份，
保证升级后不会出现"账号密码丢了"。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time

APP_NAME = "福信校网通"
APP_VERSION = "v2.0.1-alpha2"
APP_SUBTITLE = "校园网自动认证助手"
AUTHOR = "fysp431"
AUTHOR_EMAIL = "2872058826@qq.com"

CONFIG_NAME = "fuxin_config.dat"
LOG_NAME = "fuxin_log.txt"
RUNNING_MARKER_NAME = "fuxin_running.marker"
# 回退标记：用户目录里存在该文件表示"曾因程序目录不可写而回退过"，
# 程序目录恢复可写时据此决定是否把配置迁回，避免误复活已删除的配置。
FALLBACK_MARKER_NAME = "fuxin_fallback.marker"
MUTEX_NAME = "FUXIN_XiaoWangTong_Mutex_Py"
RUN_VALUE_NAME = "福信校网通"

DEFAULT_FREQ = 600
# 检测频率：最低 10 秒/次，不设上限（整数）
MIN_FREQ = 10

# 开机自启时的静默参数：启动后直接驻留托盘，不弹主窗口
MINIMIZED_FLAG = "--min"

_DATA_DIR_LOCK = threading.Lock()
_data_dir: str | None = None
_data_dir_note = ""


def app_dir() -> str:
    """exe 所在目录；源码运行时为工程根目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _resource_root() -> str:
    base = getattr(sys, "_MEIPASS", None)
    return base if base else app_dir()


def _is_writable(directory: str) -> bool:
    """实际创建并删除一个临时文件，确认目录可写（只读目录探测才会失败）。"""
    probe = ""
    try:
        handle, probe = tempfile.mkstemp(prefix=".fuxin_write_", dir=directory)
        os.close(handle)
        return True
    except OSError:
        return False
    finally:
        if probe:
            try:
                os.remove(probe)
            except OSError:
                pass


def _user_data_dir() -> str:
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        base = os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(base, "FuxinNet")


def _ensure_dir(directory: str) -> bool:
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError:
        return False
    return _is_writable(directory)


def _migrate_legacy_config(source_dir: str, target_dir: str) -> bool:
    """把 ``source_dir`` 的配置搬到 ``target_dir``，成功返回 True。

    目标已有配置时不覆盖（用户当前的设置优先）。
    """
    source = os.path.join(source_dir, CONFIG_NAME)
    target = os.path.join(target_dir, CONFIG_NAME)
    if not os.path.exists(source) or os.path.exists(target):
        return False
    try:
        shutil.copy2(source, target)
        return True
    except OSError:
        return False


def _fallback_marker(directory: str) -> str:
    return os.path.join(directory, FALLBACK_MARKER_NAME)


def _mark_fallback(directory: str) -> None:
    """记录"本机曾因程序目录不可写而回退"，用于将来迁回时判断。"""
    try:
        with open(_fallback_marker(directory), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(time.strftime("%Y-%m-%d %H:%M:%S") + "\n")
    except OSError:
        pass


def _clear_fallback_marker(directory: str) -> None:
    try:
        os.remove(_fallback_marker(directory))
    except OSError:
        pass


def data_dir() -> str:
    """运行期数据目录：程序目录可写就用它，否则回退用户数据目录。

    结果只计算一次；选定目录会记录到 :func:`data_dir_note`，供启动日志首行输出。

    迁移是双向的：
    - 程序目录不可写 → 回退用户目录，并把旧配置复制过去（升级兼容）；
    - 程序目录重新可写（例如从 Program Files 挪到 D 盘）→ 把用户目录的配置迁回，
      否则用户会以为"账号密码丢了"。
    回退时会在用户目录留一个标记文件，只有见过该标记才执行反向迁移，
    避免用户主动删掉程序目录配置后又被旧副本"复活"。
    """
    global _data_dir, _data_dir_note
    with _DATA_DIR_LOCK:
        if _data_dir is not None:
            return _data_dir

        preferred = app_dir()
        if _is_writable(preferred):
            _data_dir = preferred
            _data_dir_note = f"数据目录：程序目录 {preferred}"
            # 反向迁移：仅当之前确实回退过（有标记）才搬回来
            user_dir = _user_data_dir()
            if os.path.exists(_fallback_marker(user_dir)):
                if _migrate_legacy_config(user_dir, preferred):
                    _data_dir_note += f"（已从用户目录迁回 {CONFIG_NAME}）"
                _clear_fallback_marker(user_dir)
            return _data_dir

        fallback = _user_data_dir()
        if not _ensure_dir(fallback):
            fallback = os.path.join(tempfile.gettempdir(), "FuxinNet")
            _ensure_dir(fallback)

        migrated = _migrate_legacy_config(preferred, fallback)
        _mark_fallback(fallback)
        _data_dir = fallback
        _data_dir_note = f"程序目录不可写，运行期数据已回退到：{fallback}"
        if migrated:
            _data_dir_note += f"（已从旧位置迁移 {CONFIG_NAME}）"
        return _data_dir


def data_dir_note() -> str:
    """启动日志用的说明：本次选定的数据目录及回退/迁移情况。"""
    data_dir()
    return _data_dir_note


def ui_dir() -> str:
    """HTML 资源目录；打包后位于 _MEIPASS。"""
    return os.path.join(_resource_root(), "ui")


def asset_path(name: str) -> str:
    """图标等静态资源。"""
    return os.path.join(_resource_root(), "assets", name)


def config_path() -> str:
    return os.path.join(data_dir(), CONFIG_NAME)


def log_path() -> str:
    return os.path.join(data_dir(), LOG_NAME)


def running_marker_path() -> str:
    return os.path.join(data_dir(), RUNNING_MARKER_NAME)
