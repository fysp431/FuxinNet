"""监控线程生命周期与异常兜底。"""

from __future__ import annotations

import time

from fakes import CrashConfig, FakeConfig, FakeLogger

from fuxin import auth, net, worker


def _patch_network_ready(monkeypatch) -> None:
    monkeypatch.setattr(net, "http_request", lambda *args, **kwargs: net.Response(status=200))


def _wait_stopped(monitor, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not monitor.running and not monitor.stopping:
            return
        time.sleep(0.05)


def test_monitor_lifecycle(monkeypatch) -> None:
    log = FakeLogger()
    monitor = worker.Monitor(FakeConfig(user="2025015413", password="pw"), log)
    _patch_network_ready(monkeypatch)

    def slow_offline(*args, **kwargs):
        time.sleep(0.8)
        return False

    try:
        monkeypatch.setattr(net, "check_online", lambda *args, **kwargs: True)
        ok, message = monitor.start()
        assert ok, message
        time.sleep(0.2)
        assert monitor.running

        assert not monitor._update(monitor._run_id - 1, phase="hacked")
        assert monitor.phase != "hacked"

        started = time.monotonic()
        monitor.stop()
        assert time.monotonic() - started < 0.5
        assert not monitor.running
        _wait_stopped(monitor)
        assert monitor.phase == worker.PHASE_IDLE, monitor.phase
        assert any("监控线程退出" in line for line in log.lines)

        ok, message = monitor.start()
        assert ok, message
        time.sleep(0.15)
        monitor.stop()
        _wait_stopped(monitor)
        assert not monitor.running
        assert monitor.phase == worker.PHASE_IDLE

        monkeypatch.setattr(net, "check_online", slow_offline)
        log.lines.clear()
        monitor.start()
        time.sleep(0.1)
        started = time.monotonic()
        monitor.stop()
        assert time.monotonic() - started < 0.5
        assert monitor.snapshot().get("stopping") is True
        _wait_stopped(monitor)
        assert not any("开始认证" in line for line in log.lines), log.lines[-3:]
        assert monitor.phase == worker.PHASE_IDLE

        def boom(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(net, "check_online", boom)
        log.lines.clear()
        monitor.start()
        time.sleep(0.5)
        assert monitor.running
        assert any("探测异常" in line for line in log.lines)
        monitor.stop()
        _wait_stopped(monitor)
        assert monitor.phase == worker.PHASE_IDLE

        monkeypatch.setattr(net, "check_online", lambda *args, **kwargs: False)

        def fatal(*args, **kwargs):
            raise auth.AuthFatal("公钥异常")

        monkeypatch.setattr(auth, "try_authenticate", fatal)
        log.lines.clear()
        monitor.start()
        _wait_stopped(monitor)
        assert not monitor.running
        assert monitor.phase == worker.PHASE_ERROR, monitor.phase
        attempts = sum("[认证] 第" in line and "次尝试" in line for line in log.lines)
        assert attempts == 1, log.lines
        assert any("不可恢复" in line for line in log.lines)
    finally:
        monitor.stop(wait=True, timeout=2.0)


def test_monitor_survives_top_level_crash(monkeypatch) -> None:
    log = FakeLogger()
    monitor = worker.Monitor(CrashConfig(), log)
    _patch_network_ready(monkeypatch)
    monkeypatch.setattr(net, "check_online", lambda *args, **kwargs: True)
    try:
        ok, message = monitor.start()
        assert ok, message
        _wait_stopped(monitor)
        assert not monitor.running
        assert monitor.phase == worker.PHASE_ERROR
        assert any("监控线程崩溃" in line for line in log.lines)
    finally:
        monitor.stop(wait=True, timeout=2.0)


def test_set_message_uses_lock() -> None:
    monitor = worker.Monitor(FakeConfig(), FakeLogger())
    monitor.set_message("测试文案")
    assert monitor.snapshot()["message"] == "测试文案"


def test_authenticate_loop_success_is_reported(monkeypatch) -> None:
    log = FakeLogger()
    monitor = worker.Monitor(FakeConfig(user="2025015413", password="pw"), log)
    monkeypatch.setattr(auth, "try_authenticate", lambda *args, **kwargs: None)
    result = monitor._authenticate_loop(monitor._run_id, monitor._stop, "u", "p", 1)
    assert result is True
    assert monitor.snapshot()["online"] is True
