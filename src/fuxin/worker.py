"""后台监控线程：检测 → 断网则认证 → 失败重试 → 状态机。

行为契约：
- 每 30 秒重试一次，连续失败 3 次自动停止；凭据错误立即停止
- 不可恢复错误（AuthFatal，如网关公钥异常）立即停止，不做无意义重试
- 只在状态变化时记事件日志；同时每次检测写一行运行数据（耗时 / 结果）
- 停止是幂等的；`stop()` 只发信号并立即返回，复位与广播交给线程 finally

并发约定：
- 每次启动分配 run_id，只有当前 run_id 才能写状态；旧线程无法污染新会话
- 线程退出统一在 finally 中清空 _thread；旧线程未退出前拒绝再次启动
- 工作线程顶层 try/except：未预期异常写入堆栈并置 error，而不是静默死掉
- 连通性探测自身异常只跳过本轮，不终止整个监控（网络抖动可恢复）
"""

from __future__ import annotations

import threading
import time
import traceback

from . import auth, net, paths

PHASE_IDLE = "idle"
PHASE_CHECKING = "checking"
PHASE_AUTHING = "authenticating"
PHASE_RUNNING = "running"
PHASE_ERROR = "error"
PHASE_WAITING = "waiting"

RETRY_GAP = 30
MAX_FAIL = 3
WAIT_NET_READY_MAX = 180
WAIT_NET_READY_GAP = 5


def _now_text() -> str:
    return time.strftime("%H:%M:%S")


class Monitor:
    def __init__(self, config, logger) -> None:
        self.config = config
        self.logger = logger
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._run_id = 0
        self._stop_reason = ""
        self._stop_error = False
        self.phase = PHASE_IDLE
        self.online: bool | None = None
        self.fail_count = 0
        self.last_check = ""
        self.last_message = "点击「启动」开始自动检测网络"
        self.on_state = None  # 运行状态变化回调（供托盘刷新 tip）

    # ------------------------------------------------------------ 内部工具
    def _log_exc(self, prefix: str) -> None:
        text = prefix + "\n" + traceback.format_exc(limit=6)
        writer = getattr(self.logger, "log_block", None)
        if writer is not None:
            writer(text)
        else:
            self.logger.log(" ".join(text.split()), limit=4000)

    def _emit_state(self) -> None:
        callback = self.on_state
        if callback is None:
            return
        try:
            callback(self.snapshot())
        except Exception:
            pass

    def _alive(self, run_id: int, stop: threading.Event) -> bool:
        return not stop.is_set() and run_id == self._run_id

    def _update(self, run_id: int, **fields) -> bool:
        """仅当前 run 可以写状态，防止旧线程回写新会话。"""
        with self._lock:
            if run_id != self._run_id:
                return False
            for key, value in fields.items():
                setattr(self, key, value)
            return True

    # ------------------------------------------------------------ 状态查询
    @property
    def running(self) -> bool:
        with self._lock:
            thread = self._thread
            return thread is not None and thread.is_alive() and not self._stop.is_set()

    @property
    def stopping(self) -> bool:
        """已发出停止请求，但工作线程还在收尾。"""
        with self._lock:
            thread = self._thread
            return thread is not None and thread.is_alive() and self._stop.is_set()

    def snapshot(self) -> dict:
        with self._lock:
            thread = self._thread
            alive = thread is not None and thread.is_alive()
            return {
                "running": bool(alive and not self._stop.is_set()),
                "stopping": bool(alive and self._stop.is_set()),
                "phase": self.phase,
                "online": self.online,
                "failCount": self.fail_count,
                "lastCheck": self.last_check,
                "message": self.last_message,
            }

    def set_message(self, message: str) -> None:
        """供 api 层使用的持锁写法，避免直接赋值与工作线程竞态。"""
        with self._lock:
            self.last_message = message

    # ------------------------------------------------------------ 启停
    def start(self) -> tuple[bool, str]:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                if self._stop.is_set():
                    return False, "正在停止，请稍候再试"
                return True, ""

        user, password = self.config.credentials()
        if not user or not password:
            return False, "请先输入并保存学号和密码"

        interval = max(paths.MIN_FREQ, self.config.freq_value())
        with self._lock:
            self._run_id += 1
            run_id = self._run_id
            self._stop = threading.Event()
            self._stop_reason = ""
            self._stop_error = False
            self.fail_count = 0
            self.online = None
            self.phase = PHASE_CHECKING
            self.last_message = "正在运行，账号与频率已锁定"
            thread = threading.Thread(
                target=self._run,
                args=(run_id, self._stop, user, password),
                name=f"fuxin-monitor-{run_id}",
                daemon=True,
            )
            self._thread = thread

        self.logger.log(
            f"========== 启动监控 run={run_id} 频率={interval}秒/次 "
            f"门户={net.portal_host()} =========="
        )
        thread.start()
        try:
            self.config.set_auto_run(True)
            self.config.save()
        except Exception as exc:
            self.logger.log(f"记录自动运行意图失败：{exc}")
        self._emit_state()
        return True, ""

    def stop(
        self,
        by_user: bool = True,
        reason: str = "",
        error: bool = False,
        wait: bool = False,
        timeout: float = 2.0,
    ) -> None:
        """发出停止请求。

        默认立即返回，避免阻塞 UI 线程；复位与界面广播由 _run 的 finally 完成。
        wait=True 仅供退出程序等允许等待的场景使用。
        """
        with self._lock:
            thread = self._thread
            if thread is None:
                return
            already = self._stop.is_set()
            self._stop.set()
            if reason:
                self._stop_reason = reason
            elif by_user and not self._stop_reason:
                self._stop_reason = "已停止检测"
            if error:
                self._stop_error = True
            run_id = self._run_id

        if not already:
            label = "用户手动停止" if by_user else (reason or "停止监控")
            self.logger.log(f"[运行] {label} run={run_id}")
            if by_user:
                try:
                    self.config.set_auto_run(False)
                    self.config.save()
                except Exception as exc:
                    self.logger.log(f"清除自动运行意图失败：{exc}")
        self._emit_state()

        # 工作线程内部请求停止（如账号错误）：不做等待
        if not wait or thread is threading.current_thread():
            return
        thread.join(timeout)
        if thread.is_alive():
            self.logger.log(f"[运行] 等待线程退出超时 run={run_id}（后台仍在收尾）")

    # ------------------------------------------------------------ 主循环
    def _run(
        self, run_id: int, stop: threading.Event, user: str, password: str
    ) -> None:
        started = time.monotonic()
        exit_error = False
        reason = ""
        try:
            self._loop(run_id, stop, user, password)
        except Exception:
            exit_error = True
            reason = "监控线程异常退出"
            self._log_exc(f"[异常] 监控线程崩溃 run={run_id}")
        finally:
            with self._lock:
                is_current = (
                    run_id == self._run_id
                    and self._thread is threading.current_thread()
                )
                if is_current:
                    self._thread = None
                    self.phase = (
                        PHASE_ERROR if (exit_error or self._stop_error) else PHASE_IDLE
                    )
                    self.fail_count = 0
                    self.online = None
                    self.last_message = reason or self._stop_reason or "已停止检测"
                    final_phase = self.phase
            if is_current:
                self.logger.log(
                    f"[运行] 监控线程退出 run={run_id} phase={final_phase} "
                    f"运行时长={int(time.monotonic() - started)}s"
                )
                self._emit_state()

    def _loop(
        self, run_id: int, stop: threading.Event, user: str, password: str
    ) -> None:
        last_online: bool | None = None
        if not self._wait_network_ready(run_id, stop):
            return
        interval = max(paths.MIN_FREQ, self.config.freq_value())
        check_no = 0
        while self._alive(run_id, stop):
            check_no += 1
            last_online = self._do_check(
                run_id, stop, user, password, last_online, check_no
            )
            if not self._alive(run_id, stop):
                return
            interval = self._tick_wait(run_id, stop, interval)

    def _do_check(
        self,
        run_id: int,
        stop: threading.Event,
        user: str,
        password: str,
        last_online: bool | None,
        check_no: int,
    ) -> bool | None:
        if not self._alive(run_id, stop):
            return last_online
        self._update(run_id, phase=PHASE_CHECKING)

        started = time.monotonic()
        try:
            online = net.check_online(self.logger, stop=stop)
        except Exception:
            # 探测自身异常（代码/环境问题）不应终止整个监控，跳过本轮等待恢复
            self._log_exc(f"[异常] 连通性探测异常 run={run_id}")
            self.logger.log("[数据] 探测异常，本轮跳过（不触发认证）")
            self._update(run_id, online=None, last_message="连通性探测异常，本轮跳过")
            return last_online
        cost = int((time.monotonic() - started) * 1000)

        # 探测途中被停止：不要把"中断"误当成"离线"进入认证
        if not self._alive(run_id, stop):
            return last_online

        self._update(run_id, online=online, last_check=_now_text())
        self.logger.log(
            f"[数据] 第{check_no}次检测 结果={'在线' if online else '离线'} 总用时={cost}ms"
        )

        if online:
            if last_online is not True:
                self.logger.log("网络正常")
                self._update(run_id, last_message="网络正常，正在后台守护")
            self._update(run_id, phase=PHASE_RUNNING)
            return True

        self.logger.log(f"检测到断网（第{check_no}次检测），开始认证")
        return self._authenticate_loop(run_id, stop, user, password, check_no)

    def _authenticate_loop(
        self,
        run_id: int,
        stop: threading.Event,
        user: str,
        password: str,
        check_no: int,
    ) -> bool:
        """认证直到成功、不可恢复失败或次数用尽；返回是否认证成功。"""
        fail = 0
        while self._alive(run_id, stop):
            self._update(run_id, phase=PHASE_AUTHING, fail_count=fail)
            attempt = fail + 1
            self.logger.log(
                f"[认证] 第{attempt}次尝试 run={run_id}（第{check_no}轮检测）"
            )
            started = time.monotonic()
            try:
                auth.try_authenticate(stop, self.logger, user, password)
            except auth.AuthStopped:
                self.logger.log(f"[认证] 收到停止请求，认证中止 run={run_id}")
                return False
            except auth.AuthFatal as exc:
                message = str(exc)
                self.logger.log(f"认证失败（不可恢复）：{message}")
                self._update(run_id, last_message=message)
                self.stop(by_user=False, reason=message, error=True)
                return False
            except auth.AuthFailed as exc:
                message = str(exc)
                cost = int((time.monotonic() - started) * 1000)
                if auth.is_account_error(message):
                    self.logger.log(f"认证失败（账号错误）：{message}")
                    self._update(run_id, last_message=message)
                    self.stop(by_user=False, reason=message, error=True)
                    return False
                fail += 1
                self._update(run_id, fail_count=fail, last_message=message)
                self.logger.log(f"[认证] 第{fail}次失败 用时={cost}ms：{message}")
                if fail >= MAX_FAIL:
                    self.logger.log(f"已认证失败 {MAX_FAIL} 次，不再重新尝试")
                    self._update(run_id, last_message="已认证失败 3 次，不再重新尝试")
                    self.stop(by_user=False, reason="已认证失败 3 次，不再重新尝试", error=True)
                    return False
                self.logger.log(f"已认证失败 {fail} 次，{RETRY_GAP} 秒后重新尝试")
                if stop.wait(RETRY_GAP):
                    return False
                continue
            except Exception:
                fail += 1
                self._update(run_id, fail_count=fail)
                self._log_exc(f"[异常] 认证异常 run={run_id} 第{fail}次")
                if fail >= MAX_FAIL:
                    self.stop(by_user=False, reason="认证异常次数过多", error=True)
                    return False
                if stop.wait(RETRY_GAP):
                    return False
                continue

            if not self._alive(run_id, stop):
                return False
            cost = int((time.monotonic() - started) * 1000)
            self._update(
                run_id,
                fail_count=0,
                online=True,
                phase=PHASE_RUNNING,
                last_message="认证成功，网络已恢复",
            )
            self.logger.log(f"[认证] 认证成功，网络已恢复 用时={cost}ms")
            return True
        return False

    def _wait_network_ready(self, run_id: int, stop: threading.Event) -> bool:
        """等待门户可达；期间不计入认证失败次数。

        返回 True 表示可以继续检测循环，False 表示已被停止。
        超时不算失败，照常继续（门户地址可能变化）。
        """
        host = net.portal_host()
        self._update(
            run_id,
            phase=PHASE_WAITING,
            last_message=f"等待网络就绪（门户 {host}）",
        )
        started = time.monotonic()
        deadline = started + WAIT_NET_READY_MAX
        last_note = 0.0
        while self._alive(run_id, stop):
            try:
                resp = net.http_request(f"http://{host}/", timeout=3.0)
                ready = not resp.error
            except Exception:
                ready = False
            if ready:
                self.logger.log(
                    f"网络已就绪，开始检测（等待 {int(time.monotonic() - started)}s）"
                )
                return True
            now = time.monotonic()
            if now - last_note >= 15:
                last_note = now
                self.logger.log(
                    f"[数据] 等待网络就绪 已等待 {int(now - started)}s"
                    f"/{WAIT_NET_READY_MAX}s 门户={host} 暂不可达"
                )
            remain = deadline - now
            if remain <= 0:
                self.logger.log(
                    f"等待网络就绪超时（{WAIT_NET_READY_MAX}s），"
                    "继续进入检测循环"
                )
                return True
            if stop.wait(min(WAIT_NET_READY_GAP, remain)):
                return False
        return False

    def _tick_wait(self, run_id: int, stop: threading.Event, interval: int) -> int:
        """按当前频率等待；期间频率被改动则立即生效。"""
        deadline = time.monotonic() + interval
        current = interval
        while self._alive(run_id, stop):
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
