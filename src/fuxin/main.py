"""福信校网通 —— Python + pywebview 重构版。

入口职责：DPI 感知、单实例、初始化配置/日志/监控、托盘、启动 WebView 窗口。
"""

from __future__ import annotations

import ctypes
import os
import sys
import traceback

from . import autostart, paths
from .api import Api
from .config import Config
from .logger import Logger
from .tray import Tray
from .worker import Monitor

try:
    import webview
except Exception:  # pragma: no cover - 仅打包环境异常时提示
    webview = None


def enable_dpi_awareness() -> None:
    """Per-Monitor V2，失败逐级回退；不声明会导致 WebView 内容发虚。"""
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def acquire_single_instance() -> bool:
    """返回 True 表示本进程持有实例锁；False 表示已有实例在跑。"""
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, paths.MUTEX_NAME)
    if not handle:
        return True
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        return False
    globals()["_MUTEX_HANDLE"] = handle  # 保持引用，进程存活期间不释放
    return True


def _show_message(text: str, title: str) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x40)
    except Exception:
        print(text)


def _foreground_window_by_title(title: str) -> bool:
    """把已存在的窗口恢复并前置（用于第二次启动时唤醒托盘里的实例）。"""
    user32 = ctypes.windll.user32
    hwnd = user32.FindWindowW(None, title)
    if not hwnd:
        return False
    SW_SHOWNORMAL = 1
    user32.ShowWindow(hwnd, SW_SHOWNORMAL)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    return True


def witness_existing_instance() -> None:
    """已有实例：优先唤醒它的窗口，找不到再提示。"""
    if not _foreground_window_by_title(paths.APP_NAME):
        _show_message(f"{paths.APP_NAME}已在运行中（请在托盘区查看）", "提示")


def main() -> int:
    enable_dpi_awareness()

    start_minimized = paths.MINIMIZED_FLAG in sys.argv[1:]

    if not acquire_single_instance():
        witness_existing_instance()
        return 0

    if webview is None:
        _show_message("缺少 pywebview 依赖，无法启动界面", paths.APP_NAME)
        return 1

    logger = Logger(paths.log_path())
    logger.log(f"========== {paths.APP_NAME} {paths.APP_VERSION} 启动 ==========")
    logger.log(f"程序目录：{paths.app_dir()}")

    config = Config(logger)
    scope_label = "用户级" if config.autostart_scope == autostart.SCOPE_USER else "系统级"
    logger.log(
        f"开机自启（{scope_label}）："
        + ("已启用" if autostart.is_enabled(config.autostart_scope) else "未启用")
    )
    monitor = Monitor(config, logger)
    api = Api(config, logger, monitor)

    html_path = os.path.join(paths.ui_dir(), "index.html")
    if not os.path.exists(html_path):
        logger.log(f"界面资源缺失：{html_path}")
        _show_message(f"界面资源缺失：{html_path}", paths.APP_NAME)
        return 1

    window = webview.create_window(
        paths.APP_NAME,
        url=html_path,
        js_api=api,
        width=680,
        height=880,
        min_size=(560, 720),
        background_color="#eef2f8",
        text_select=False,
        easy_drag=False,
        hidden=start_minimized,
    )

    state = {"quitting": False}

    def show_window() -> None:
        try:
            window.show()
            window.restore()
        except Exception as exc:
            logger.log(f"恢复窗口失败：{exc}")
            return
        _foreground_window_by_title(paths.APP_NAME)

    def quit_app() -> None:
        if state["quitting"]:
            return
        state["quitting"] = True
        if monitor.running:
            monitor.stop(by_user=False, reason="托盘退出")
            logger.log("托盘退出：已停止检测")
        logger.log("用户通过托盘菜单退出软件")
        tray.stop()
        try:
            window.destroy()
        except Exception as exc:
            logger.log(f"销毁窗口异常：{exc}")

    tray = Tray(logger, on_show=show_window, on_quit=quit_app)
    api.tray = tray

    def on_closing() -> bool:
        """返回 False 阻止关闭：关窗口 = 隐藏到托盘（真正退出走托盘菜单）。"""
        if state["quitting"]:
            return True
        if not tray.available:
            return True  # 托盘没起来就老老实实退出，避免"窗口没了也退不掉"
        try:
            window.hide()
            logger.log("窗口已隐藏到托盘（后台继续运行）")
        except Exception as exc:
            logger.log(f"隐藏窗口失败：{exc}")
            return True
        return False

    def on_closed() -> None:
        if state["quitting"]:
            return
        state["quitting"] = True
        try:
            if monitor.running:
                monitor.stop(by_user=False, reason="软件关闭")
            logger.log("软件关闭（窗口退出）")
        except Exception:
            logger.log("关闭时发生异常：" + traceback.format_exc(limit=3))
        try:
            tray.stop()
        except Exception:
            pass

    window.events.closing += on_closing
    window.events.closed += on_closed

    # 监控状态变化 → 托盘 tip
    monitor.on_state = lambda snapshot: tray.set_running(bool(snapshot.get("running")))

    def start_services() -> None:
        if not tray.start():
            logger.log("托盘不可用：关闭窗口将直接退出程序")
        else:
            tray.set_running(monitor.running)
            if start_minimized:
                logger.log("以托盘方式启动（--min）")
            else:
                _foreground_window_by_title(paths.APP_NAME)

    # 托盘必须等窗口创建完成后再起（回调里要操作窗口）
    window.events.loaded += start_services

    try:
        webview.start(
            debug=False,
            private_mode=False,
            http_server=True,
            storage_path=os.path.join(paths.app_dir(), "webview_data"),
        )
    except Exception:
        logger.log("主界面启动失败：" + traceback.format_exc(limit=5))
        try:
            webview.start(gui="mshtml", debug=False, http_server=True)
        except Exception:
            logger.log("回退内核同样失败：" + traceback.format_exc(limit=5))
            _show_message(
                "无法初始化界面内核。\n请安装 Microsoft Edge WebView2 运行时后重试。",
                paths.APP_NAME,
            )
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
