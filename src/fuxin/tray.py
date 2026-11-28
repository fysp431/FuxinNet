"""系统托盘（pystray）。

行为契约：
- 点窗口关闭按钮 → 隐藏到托盘，后台继续检测（不是退出）
- 托盘左键单击 / 菜单「打开主界面」→ 恢复并前置窗口
- 托盘右键 → 打开主界面 / 退出
- tip 显示运行状态：福信校网通 - 运行中（vX）/ 已停止（vX）
- 「退出」必须先停监控、写日志，再销毁窗口和图标
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable

from . import paths


class Tray:
    def __init__(
        self,
        logger,
        on_show: Callable[[], None],
        on_quit: Callable[[], None],
    ) -> None:
        self.logger = logger
        self.on_show = on_show
        self.on_quit = on_quit
        self._icon = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._available = True

    # ------------------------------------------------------------ 生命周期
    def _load_image(self):
        try:
            from PIL import Image

            for name in ("icon.png", "icon.ico"):
                candidate = paths.asset_path(name)
                if os.path.exists(candidate):
                    return Image.open(candidate)
        except Exception as exc:  # 图标加载失败不应影响主程序
            self.logger.log(f"托盘图标加载失败：{exc}")
        return None

    def start(self) -> bool:
        if self._icon is not None:
            return True
        try:
            import pystray
        except Exception as exc:
            self._available = False
            self.logger.log(f"托盘不可用（缺少 pystray）：{exc}")
            return False

        image = self._load_image()
        if image is None:
            self._available = False
            self.logger.log("托盘不可用：找不到图标资源 assets/icon.png")
            return False

        menu = pystray.Menu(
            pystray.MenuItem("打开主界面", self._handle_show, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", self._handle_quit),
        )
        try:
            self._icon = pystray.Icon("fuxin", image, self._tip_text(False), menu)
            self._thread = threading.Thread(target=self._icon.run, name="fuxin-tray", daemon=True)
            self._thread.start()
            self.logger.log("[托盘] 图标已创建")
            return True
        except Exception as exc:
            self._available = False
            self._icon = None
            self.logger.log(f"托盘创建失败：{exc}")
            return False

    def stop(self) -> None:
        icon = self._icon
        self._icon = None
        if icon is not None:
            try:
                icon.stop()
                self.logger.log("[托盘] 图标已移除")
            except Exception as exc:
                self.logger.log(f"托盘移除异常：{exc}")

    @property
    def available(self) -> bool:
        return self._available and self._icon is not None

    # ------------------------------------------------------------ 状态
    def _tip_text(self, running: bool) -> str:
        state = "运行中" if running else "已停止"
        return f"{paths.APP_NAME} - {state}（{paths.APP_VERSION}）"

    def set_running(self, running: bool) -> None:
        if running == self._running:
            return
        self._running = running
        self._refresh_tip()

    def _refresh_tip(self) -> None:
        icon = self._icon
        if icon is None:
            return
        try:
            icon.title = self._tip_text(self._running)
        except Exception:
            pass

    def notify(self, message: str, title: str = "") -> None:
        """托盘气泡提示（失败静默，仅作锦上添花）。"""
        icon = self._icon
        if icon is None:
            return
        try:
            icon.notify(message, title or paths.APP_NAME)
        except Exception:
            pass

    # ------------------------------------------------------------ 回调
    def _handle_show(self, *_args) -> None:
        try:
            self.on_show()
        except Exception as exc:
            self.logger.log(f"托盘恢复窗口失败：{exc}")

    def _handle_quit(self, *_args) -> None:
        try:
            self.on_quit()
        except Exception as exc:
            self.logger.log(f"托盘退出异常：{exc}")
