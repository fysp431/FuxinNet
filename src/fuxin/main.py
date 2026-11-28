"""福信校网通 —— Python + pywebview 重构版。

入口职责：DPI 感知、单实例、初始化配置/日志/监控、托盘、启动 WebView 窗口。
"""

from __future__ import annotations

import ctypes
import os
import platform
import sys
import time
import traceback
from ctypes import wintypes

from . import autostart, net, paths
from .api import Api
from .config import Config
from .logger import Logger
from .tray import Tray
from .worker import Monitor

try:
    import webview
except Exception:  # pragma: no cover - 仅打包环境异常时提示
    webview = None

_ERROR_ALREADY_EXISTS = 183
_single_instance_handle = None

# 单一 kernel32 实例：ctypes.WinDLL 每次调用返回的是同一对象，但 ctypes.windll.kernel32
# 是另一个包装，两者 argtypes 不共享。acquire 与 release 必须用同一个句柄对象，
# 否则 release 时 HANDLE 会按默认 c_int 传参，64 位下可能被截断。
_KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
_KERNEL32.CreateMutexW.restype = wintypes.HANDLE
_KERNEL32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
_KERNEL32.CloseHandle.restype = wintypes.BOOL
_KERNEL32.CloseHandle.argtypes = [wintypes.HANDLE]


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
    """返回 True 表示本进程持有实例锁；False 表示已有实例在跑。

    显式声明 ctypes 签名：64 位下 HANDLE 是 64 位指针，默认按 c_int 解析会截断。
    """
    global _single_instance_handle
    handle = _KERNEL32.CreateMutexW(None, False, paths.MUTEX_NAME)
    if not handle:
        return True
    if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
        _KERNEL32.CloseHandle(handle)
        return False
    _single_instance_handle = handle  # 保持引用，进程存活期间不释放
    return True


def release_single_instance() -> None:
    """进程退出前显式关闭互斥量句柄（系统虽会回收，显式关闭更规范）。"""
    global _single_instance_handle
    handle, _single_instance_handle = _single_instance_handle, None
    if handle is None:
        return
    try:
        _KERNEL32.CloseHandle(handle)
    except Exception:
        pass


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

    try:
        return _run_app(start_minimized)
    finally:
        release_single_instance()


def _run_app(start_minimized: bool) -> int:
    if webview is None:
        _show_message("缺少 pywebview 依赖，无法启动界面", paths.APP_NAME)
        return 1

    logger = Logger(paths.log_path())
    logger.log(f"========== {paths.APP_NAME} {paths.APP_VERSION} 启动 ==========")
    logger.log(paths.data_dir_note())
    logger.log(f"程序目录：{paths.app_dir()}")
    logger.log(
        f"[环境] Python {platform.python_version()} / {platform.platform()} / "
        f"frozen={bool(getattr(sys, 'frozen', False))}"
    )
    logger.log(f"[环境] 门户={net.portal_host()} 日志={paths.log_path()}")

    # 异常关闭检测：上次若未正常退出，标记文件会残留
    marker = paths.running_marker_path()
    if os.path.exists(marker):
        try:
            with open(marker, encoding="utf-8", errors="replace") as fh:
                detail = fh.read().strip()
        except OSError:
            detail = ""
        logger.log(
            "上次运行未正常结束：疑似异常关闭（崩溃 / 断电 / 进程被强制结束）"
            + (f"｜上次启动信息：{detail}" if detail else "")
        )
        try:
            os.remove(marker)
        except OSError as exc:
            logger.log(f"清理运行标记失败：{exc}")
    try:
        with open(marker, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(
                f"start={time.strftime('%Y-%m-%d %H:%M:%S')} pid={os.getpid()}\n"
            )
    except OSError as exc:
        logger.log(f"写入运行标记失败：{exc}")

    config = Config(logger)
    if config.load_error:
        logger.log(f"配置读取异常的提示已通过界面展示：{config.load_error}")
    logger.log(
        f"[环境] 检测频率={config.freq_value()}秒/次 自启作用域={config.autostart_scope}"
    )
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
        height=760,
        min_size=(560, 720),
        background_color="#eef2f8",
        text_select=False,
        easy_drag=False,
        hidden=start_minimized,
    )

    state = {"quitting": False, "close_logged": False}

    def _clear_marker() -> None:
        try:
            os.remove(paths.running_marker_path())
        except OSError:
            pass

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
        if monitor.running or monitor.stopping:
            monitor.stop(by_user=False, reason="托盘退出", wait=True, timeout=3.0)
            logger.log("托盘退出：已请求停止检测")
        logger.log("软件关闭：用户手动退出（托盘菜单）")
        state["close_logged"] = True
        _clear_marker()
        tray.stop()
        try:
            window.destroy()
        except Exception as exc:
            logger.log(f"销毁窗口异常：{exc}")

    tray = Tray(logger, on_show=show_window, on_quit=quit_app)
    api.tray = tray

    def on_closed() -> None:
        if state["quitting"]:
            return
        state["quitting"] = True
        try:
            if monitor.running or monitor.stopping:
                monitor.stop(by_user=False, reason="软件关闭", wait=True, timeout=2.0)
            if not state["close_logged"]:
                # 兜底：FormClosing 钩子未覆盖到的路径
                logger.log("软件关闭：用户手动退出（关闭窗口）")
                state["close_logged"] = True
        except Exception:
            logger.log("关闭时发生异常：" + traceback.format_exc(limit=3))
        _clear_marker()
        try:
            tray.stop()
        except Exception:
            pass

    window.events.closed += on_closed

    # 监控状态变化 → 托盘 tip
    monitor.on_state = lambda snapshot: tray.set_running(bool(snapshot.get("running")))

    def _install_close_policy() -> None:
        """安装唯一的关闭决策钩子。

        这是「关还是藏」的唯一决策点：
          - 系统关机/注销  -> 放行，不隐藏，不阻塞
          - 任务管理器结束 -> 放行，不拦截
          - 程序主动退出   -> 放行（日志已在 quit_app 写好）
          - 用户点 X       -> 托盘可用则隐藏，否则放行
        """
        try:
            from System.Windows.Forms import CloseReason
        except Exception as exc:
            logger.log(f"无法加载 WinForms 关闭原因枚举，关闭策略退化为默认：{exc}")
            return

        form = getattr(window, "native", None)
        if form is None:
            logger.log(
                "警告：未能获取主窗口 WinForms 句柄，"
                "关闭原因将无法精确区分（将退化为通用文案）"
            )
            return

        def _on_form_closing(sender, args):
            reason = args.CloseReason

            # 程序已主动退出：直接放行，日志已在 quit_app 记录
            if state["quitting"]:
                args.Cancel = False
                return

            # 系统关机 / 注销：放行，绝不隐藏、绝不阻塞
            if reason == CloseReason.WindowsShutDown:
                args.Cancel = False
                if not state["close_logged"]:
                    logger.log(
                        "软件关闭：系统关机或注销，非用户操作（Windows 会话结束）"
                    )
                    state["close_logged"] = True
                _clear_marker()
                return

            # 任务管理器结束：放行，不拦截（拦截无意义且不友好）
            if reason == CloseReason.TaskManagerClosing:
                args.Cancel = False
                return

            # 用户点窗口 X
            if reason == CloseReason.UserClosing:
                if not tray.available:
                    args.Cancel = False
                    if not state["close_logged"]:
                        logger.log(
                            "软件关闭：用户关闭窗口（托盘不可用，程序随之退出）"
                        )
                        state["close_logged"] = True
                    _clear_marker()
                    return
                # 托盘可用：隐藏到托盘，取消关闭
                args.Cancel = True
                try:
                    window.hide()
                    logger.log("窗口已隐藏到托盘（未退出，后台继续运行）")
                except Exception as exc:
                    logger.log(f"隐藏窗口失败：{exc}")
                    args.Cancel = False
                return

            # 其他原因（ApplicationExitCall 等）：放行
            args.Cancel = False

        try:
            form.FormClosing += _on_form_closing
            logger.log("已安装关闭策略钩子（CloseReason 驱动）")
        except Exception as exc:
            logger.log(f"安装关闭策略钩子失败：{exc}")

    def start_services() -> None:
        _install_close_policy()

        if not tray.start():
            logger.log("托盘不可用：关闭窗口将直接退出程序")
        else:
            tray.set_running(monitor.running)
            if start_minimized:
                logger.log("以托盘方式启动（--min）")
            else:
                _foreground_window_by_title(paths.APP_NAME)

        # 自动运行：记住上次退出时的状态（与是否开机自启无关）
        if not config.auto_run_value():
            return
        if not config.has_credentials():
            logger.log("上次退出时处于运行状态，但未保存账号或密码，未自动启动")
            return
        ok, message = monitor.start()
        if ok:
            logger.log("上次退出时处于运行状态，已自动开始检测")
        else:
            logger.log(f"自动开始检测失败：{message}")

    # 托盘必须等窗口创建完成后再起（回调里要操作窗口）
    window.events.loaded += start_services

    try:
        webview.start(
            debug=False,
            private_mode=False,
            http_server=True,
            storage_path=os.path.join(paths.data_dir(), "webview_data"),
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
