"""网络层：URL 解析与并发探测。"""

from __future__ import annotations

import threading
import time

from fuxin import net

BASE = "http://172.16.9.11/eportal/index.jsp?x=1"


def test_resolve_url() -> None:
    assert net.resolve_url("//portal.local/login?x=1", BASE) == "http://portal.local/login?x=1"
    assert net.resolve_url("//portal.local/x", "https://10.0.0.1/a") == "https://portal.local/x"
    assert net.resolve_url("/a/b", BASE) == "http://172.16.9.11/a/b"
    assert net.resolve_url("http://h/x", BASE) == "http://h/x"


def test_extract_jump() -> None:
    jump = net.extract_jump(
        "<script>window.location.href='/eportal/index.jsp?wlanuserip=1&mac=2'</script>",
        BASE,
    )
    assert jump == "http://172.16.9.11/eportal/index.jsp?wlanuserip=1&mac=2"

    meta = net.extract_jump(
        '<meta http-equiv="refresh" content="0;url=/eportal/index.jsp?x=12345">', BASE
    )
    assert meta == "http://172.16.9.11/eportal/index.jsp?x=12345"


def test_check_online_returns_as_soon_as_one_source_succeeds(monkeypatch) -> None:
    def fake_probe(url: str, want_status: int, want_body: str = "") -> tuple[bool, str]:
        if "gstatic" in url:
            return True, "匹配"
        time.sleep(0.5)
        return False, "超时"

    monkeypatch.setattr(net, "_probe", fake_probe)
    started = time.monotonic()
    assert net.check_online() is True
    assert time.monotonic() - started < 0.4


def test_check_online_offline_when_all_sources_fail(monkeypatch) -> None:
    monkeypatch.setattr(net, "_probe", lambda *args, **kwargs: (False, "错误=timeout"))
    assert net.check_online() is False


def test_check_online_stop_before_probe(monkeypatch) -> None:
    monkeypatch.setattr(net, "_probe", lambda *args, **kwargs: (True, "匹配"))
    stop = threading.Event()
    stop.set()
    assert net.check_online(stop=stop) is False
