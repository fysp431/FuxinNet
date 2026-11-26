"""后台监控线程：检测 → 断网则认证 → 失败重试 → 状态机。

与旧版一致的行为契约：
- 每 30 秒重试一次，连续失败 3 次自动停止
- 凭据错误（用户不存在 / 密码不匹配）立即停止，不重试
- 只在状态变化时记日志（在线↔离线），避免日志刷屏
- 运行中修改检测频率即时生效
- 停止是幂等的；停止后复位界面状态
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from . import auth, net, paths

PHASE_IDLE = "idle"
PHASE_CHECKING = "checking"
PHASE_AUTHING = "authenticating"
PHASE_OFFLINE = "offline"

RETRY_GAP = 30
MAX_FAIL = 3


def _now_text() -> str:
    return time.strftime("%H:%M:%S")


class Monitor:
    def __init__(self, config, logger) -> None:
        self.config = config
        self.logger = logger
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self.phase = PHASE_IDLE
        self.online: Optional[bool] = None
        self.fail_count = 0
        self.last_check = ""
        self.last_message = "点击「启动」开始自动检测网络"
        self.on_state = None  # 运行状态变化回调（供托盘刷新 tip）

    def _emit_state(self) -> None:
        callback = self.on_state
        if callback is None:
            return
        try:
            callback(self.snapshot())
        except Exception:
            pass

    # ------------------------------------------------------------ 状态查询
    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive() and not self._stop.is_set()

    def snapshot(self) -> dict:
        return {
            "running": self.running,
            "phase": self.phase,
            "online": self.online,
            "failCount": self.fail_count,
            "lastCheck": self.last_check,
            "message": self.last_message,
        }

    # ------------------------------------------------------------ 启停
    def start(self) -> tuple[bool, str]:
        if self.running:
            return True, ""
        user, password = self.config.credentials()
        if not user or not password:
            return False, "请先输入并保存学号和密码"

        self._stop = threading.Event()
        self.fail_count = 0
        self.online = None
        self.phase = PHASE_CHECKING
        self.last_message = "正在运行中，禁止更改学号、密码"
        self.logger.log("========== 启动监控 ==========")

        self._thread = threading.Thread(
            target=self._run, args=(self._stop, user, password), name="fuxin-monitor", daemon=True
        )
        self._thread.start()
        self._emit_state()
        return True, ""

    def stop(self, by_user: bool = True, reason: str = "", error: bool = False) -> None:
        thread = self._thread
        if thread is None:
            return
        self._stop.set()
        # 先落日志，界面能立刻看到；再等线程收尾
        if by_user:
            self.logger.log("用户手动停止")
        if thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._thread = None
        self.phase = "error" if error else PHASE_IDLE
        self.fail_count = 0
        self.online = None
        if by_user:
            self.last_message = "已停止检测"
        elif reason:
            self.last_message = reason
        self._emit_state()

    # ------------------------------------------------------------ 主循环
    def _run(self, stop: threading.Event, user: str, password: str) -> None:
        last_online: Optional[bool] = None
        interval = max(paths.MIN_FREQ, self.config.freq_value())

        while not stop.is_set():
            last_online = self._do_check(stop, user, password, last_online)
            if stop.is_set():
                break
            interval = self._tick_wait(stop, interval)

    def _do_check(
        self, stop: threading.Event, user: str, password: str, last_online: Optional[bool]
    ) -> Optional[bool]:
        if stop.is_set():
            return last_online
        self.phase = PHASE_CHECKING
        online = net.check_online()
        self.online = online
        self.last_check = _now_text()

        if online:
            if last_online is not True:
                self.logger.log("网络正常")
                self.last_message = "网络正常，正在后台守护"
            self.phase = "running"
            return True

        self.logger.log("检测到断网，开始认证")
        self._authenticate_loop(stop, user, password)
        return False

    def _authenticate_loop(self, stop: threading.Event, user: str, password: str) -> None:
        fail = 0
        while not stop.is_set():
            self.phase = PHASE_AUTHING
            self.fail_count = fail
            try:
                auth.try_authenticate(stop, self.logger, user, password)
            except auth.AuthStopped:
                return
            except auth.AuthFailed as exc:
                message = str(exc)
                if auth.is_account_error(message):
                    self.logger.log("认证失败（账号错误）：" + message)
                    self.last_message = message
                    self.stop(by_user=False, reason=message, error=True)
                    return
                fail += 1
                self.fail_count = fail
                self.logger.log(f"认证失败 {fail} 次：{message}")
                self.last_message = message
                if fail >= MAX_FAIL:
                    self.logger.log("已认证失败 3 次，不再重新尝试")
                    self.last_message = "已认证失败 3 次，不再重新尝试"
                    self.stop(by_user=False, reason=self.last_message, error=True)
                    return
                self.logger.log(f"已认证失败 {fail} 次，{RETRY_GAP} 秒后重新尝试")
                if stop.wait(RETRY_GAP):
                    return
                continue
            except Exception as exc:  # 兜底，避免线程静默死掉
                fail += 1
                self.fail_count = fail
                self.logger.log(f"认证异常：{type(exc).__name__}: {exc}")
                if fail >= MAX_FAIL:
                    self.stop(by_user=False, reason="认证异常次数过多", error=True)
                    return
                if stop.wait(RETRY_GAP):
                    return
                continue

            # 成功
            self.fail_count = 0
            self.online = True
            self.phase = "running"
            self.last_message = "认证成功，网络已恢复"
            self.logger.log("认证成功，网络已恢复")
            return

    def _tick_wait(self, stop: threading.Event, interval: int) -> int:
        """按当前频率等待；期间频率被改动则立即生效。"""
        deadline = time.monotonic() + interval
        current = interval
        while not stop.is_set():
            remain = deadline - time.monotonic()
            if remain <= 0:
                break
            if stop.wait(min(1.0, remain)):
                return current
            latest = max(paths.MIN_FREQ, self.config.freq_value())
            if latest != current:
                current = latest
                deadline = time.monotonic() + current
                self.logger.log(f"检测频率已调整为 {current} 秒/次，即时生效")
        return current
