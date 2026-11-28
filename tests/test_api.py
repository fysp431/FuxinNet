"""前端桥接层：启停语义、配置保存与校验。"""

from __future__ import annotations

import time

from fakes import FakeConfig, FakeLogger

from fuxin import net, worker
from fuxin.api import Api


def _patch_network_ready(monkeypatch) -> None:
    monkeypatch.setattr(net, "http_request", lambda *args, **kwargs: net.Response(status=200))


def _wait_stopped(monitor, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not monitor.running and not monitor.stopping:
            return
        time.sleep(0.05)


def _make_api(config: FakeConfig) -> tuple[Api, FakeLogger, worker.Monitor]:
    log = FakeLogger()
    monitor = worker.Monitor(config, log)
    return Api(config, log, monitor), log, monitor


def test_stop_semantics_and_locks(monkeypatch) -> None:
    _patch_network_ready(monkeypatch)

    def slow_offline(*args, **kwargs):
        time.sleep(1.2)
        return False

    monkeypatch.setattr(net, "check_online", slow_offline)
    config = FakeConfig(user="2025015413", password="pw", freq=10)
    api, _log, monitor = _make_api(config)
    try:
        monitor.start()
        time.sleep(0.1)
        started = time.monotonic()
        reply = api.stop()
        assert time.monotonic() - started < 0.5
        assert reply.get("ok") is True
        assert reply.get("message") == "正在停止…"
        assert reply.get("state", {}).get("stopping") is True

        for method, argument in (
            ("save_user", "9999999999"),
            ("save_pass", "newpass"),
            ("save_freq", 5),
        ):
            save_reply = getattr(api, method)(argument)
            assert save_reply.get("ok") is False
            assert "正在运行" in save_reply.get("message", ""), save_reply.get("message")
        assert not any(
            item in ("user", "password") or item.startswith("freq=") for item in config.saved
        ), config.saved

        _wait_stopped(monitor)
        reply2 = api.stop()
        assert reply2.get("ok") is False, reply2.get("message")
    finally:
        monitor.stop(wait=True, timeout=2.0)


def test_save_freq_limits() -> None:
    config = FakeConfig(freq=10)
    api, _log, _monitor = _make_api(config)

    reply = api.save_freq(9)
    assert reply.get("ok") is False
    assert "10" in reply.get("message", "")
    reply = api.save_freq(10)
    assert reply.get("ok") is False  # 未变化
    reply = api.save_freq(30)
    assert reply.get("ok") is True, reply.get("message")
    assert "freq=30" in config.saved


def test_save_rejects_non_ascii_credentials() -> None:
    config = FakeConfig()
    api, _log, _monitor = _make_api(config)

    reply = api.save_user("张三")
    assert reply.get("ok") is False
    assert "中文" in reply.get("message", "") or "特殊字符" in reply.get("message", "")

    reply = api.save_user("2025 01")
    assert reply.get("ok") is False

    reply = api.save_pass("pass word")
    assert reply.get("ok") is False
    assert config.user == "" and config.password == ""

    reply = api.save_user("2025015413")
    assert reply.get("ok") is True, reply.get("message")
    reply = api.save_pass("p@ssw0rd!")
    assert reply.get("ok") is True, reply.get("message")


def test_save_failure_rolls_back_memory(monkeypatch) -> None:
    config = FakeConfig(user="2025000000", password="oldpass", freq=10)
    config.save_error = "只读目录"
    api, log, _monitor = _make_api(config)

    reply = api.save_user("2025000001")
    assert reply.get("ok") is False
    assert "保存失败" in reply.get("message", "")
    assert config.user == "2025000000"

    reply = api.save_pass("newpass")
    assert reply.get("ok") is False
    assert config.password == "oldpass"

    reply = api.save_freq(30)
    assert reply.get("ok") is False
    assert config.freq == 10
    assert any("保存配置失败" in line for line in log.lines)


def test_bootstrap_reports_load_error() -> None:
    config = FakeConfig()
    config.load_error = "配置项 freq 解析失败：bad value"
    api, _log, _monitor = _make_api(config)
    data = api.bootstrap()
    assert data["loadError"] == config.load_error


def test_autostart_save_failure_returns_readable_message() -> None:
    """只读目录下切换自启作用域/开关，不能把 OSError 抛给前端。"""
    config = FakeConfig()
    config.save_error = "只读目录"
    api, log, _monitor = _make_api(config)

    reply = api.set_autostart_scope("system")
    assert reply.get("ok") is False
    assert "保存失败" in reply.get("message", ""), reply.get("message")
    # 内存值应回滚，不能让界面显示已切换
    assert config.autostart_scope == "user"

    reply = api.set_autostart(True, "user")
    assert reply.get("ok") is False
    assert "保存失败" in reply.get("message", ""), reply.get("message")
    assert config.autostart_scope == "user"
    assert any("保存配置失败" in line for line in log.lines)
