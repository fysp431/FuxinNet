"""路径与常量：所有运行期文件都放在程序（exe）同目录，不依赖工作目录。"""

from __future__ import annotations

import os
import sys

APP_NAME = "福信校网通"
APP_VERSION = "v2.0.1-alpha2"
APP_SUBTITLE = "校园网自动认证助手"
AUTHOR = "fysp431"
AUTHOR_EMAIL = "2872058826@qq.com"

CONFIG_NAME = "fuxin_config.dat"
LOG_NAME = "fuxin_log.txt"
RUNNING_MARKER_NAME = "fuxin_running.marker"
MUTEX_NAME = "FUXIN_XiaoWangTong_Mutex_Py"
RUN_VALUE_NAME = "福信校网通"

DEFAULT_FREQ = 600
# 检测频率：最低 10 秒/次，不设上限（整数）
MIN_FREQ = 10

# 开机自启时的静默参数：启动后直接驻留托盘，不弹主窗口
MINIMIZED_FLAG = "--min"


def app_dir() -> str:
    """exe 所在目录；源码运行时为工程根目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _resource_root() -> str:
    base = getattr(sys, "_MEIPASS", None)
    return base if base else app_dir()


def ui_dir() -> str:
    """HTML 资源目录；打包后位于 _MEIPASS。"""
    return os.path.join(_resource_root(), "ui")


def asset_path(name: str) -> str:
    """图标等静态资源。"""
    return os.path.join(_resource_root(), "assets", name)


def config_path() -> str:
    return os.path.join(app_dir(), CONFIG_NAME)


def log_path() -> str:
    return os.path.join(app_dir(), LOG_NAME)


def running_marker_path() -> str:
    return os.path.join(app_dir(), RUNNING_MARKER_NAME)
