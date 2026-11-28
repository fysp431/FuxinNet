"""认证协议、RSA、诊断落盘与配置加密。"""

from __future__ import annotations

import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.asymmetric import rsa
from fakes import FakeLogger

from fuxin import auth, net, paths
from fuxin import config as fconfig
from fuxin.diagnostics import MAX_DUMP_BYTES, AuthDiagnostic


# ---------------------------------------------------------------- 1. 解析
@pytest.mark.parametrize(
    ("body", "want_ok", "want_message"),
    [
        ('{"result": "success"}', True, ""),
        ('{"result":"success"}', True, ""),
        (
            '{"result":"fail","message":"\\u5bc6\\u7801\\u4e0d\\u5339\\u914d"}',
            False,
            "密码不匹配",
        ),
        (
            'garbage "message":"\\u7528\\u6237\\u4e0d\\u5b58\\u5728" tail',
            False,
            "用户不存在",
        ),
    ],
)
def test_parse_login_result(body: str, want_ok: bool, want_message: str) -> None:
    ok, message = auth.parse_login_result(body)
    assert ok is want_ok
    assert message == want_message


def test_decode_unicode_escapes() -> None:
    assert auth.decode_unicode_escapes(r"\u7528\u6237\u4e0d\u5b58\u5728") == "用户不存在"
    assert auth.decode_unicode_escapes(r"a\u7528b") == "a用b"
    plain = "用户不存在,请输入正确的用户名!"
    assert auth.decode_unicode_escapes(plain) == plain
    assert auth.decode_unicode_escapes(r"\ud83d\ude00") == "😀"


# ---------------------------------------------------------------- 2. RSA
def _key_material() -> tuple[rsa.RSAPrivateKey, str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    numbers = key.private_numbers()
    modulus_hex = numbers.public_numbers.n.to_bytes(128, "big").hex()
    exponent_hex = (65537).to_bytes(3, "big").hex()
    return key, exponent_hex, modulus_hex


def test_rsa_encrypt_roundtrip() -> None:
    key, exponent_hex, modulus_hex = _key_material()
    plaintext = "2025015413"
    encrypted = auth.rsa_encrypt(exponent_hex, modulus_hex, plaintext)
    assert encrypted.isdigit()

    numbers = key.private_numbers()
    block = pow(int(encrypted), numbers.d, numbers.public_numbers.n).to_bytes(128, "big")
    assert block[:2] == b"\x00\x02"
    separator = block.index(b"\x00", 2)
    assert separator - 2 >= 8
    assert block[separator + 1 :].decode() == plaintext


@pytest.mark.parametrize(
    "call",
    [
        lambda exp, mod: auth.rsa_encrypt(exp, mod, "x" * 200),
        lambda exp, mod: auth.rsa_encrypt("zz", mod, "x"),
        lambda exp, mod: auth.rsa_encrypt(exp, "abcd", "x"),
    ],
)
def test_rsa_rejects_invalid_input(call) -> None:
    _, exponent_hex, modulus_hex = _key_material()
    with pytest.raises(ValueError):
        call(exponent_hex, modulus_hex)


def test_auth_fatal_on_missing_public_key(monkeypatch) -> None:
    monkeypatch.setattr(
        auth,
        "get_page_info",
        lambda *args, **kwargs: {"encrypt": True, "exponent": "", "modulus": "", "body": ""},
    )
    with pytest.raises(auth.AuthFatal):
        auth.login("127.0.0.1", "index", "user", "pass", None)

    monkeypatch.setattr(
        auth,
        "get_page_info",
        lambda *args, **kwargs: {
            "encrypt": True,
            "exponent": "010001",
            "modulus": "abcd",
            "body": "",
        },
    )
    with pytest.raises(auth.AuthFatal):
        auth.login("127.0.0.1", "index", "user", "pass", None)


# ---------------------------------------------------------------- 3. 诊断
class _PathLogger(FakeLogger):
    def __init__(self, path: str) -> None:
        super().__init__()
        self.path = path


def test_diagnostic_masks_sensitive_fields(tmp_path) -> None:
    log = _PathLogger(str(tmp_path / "fuxin_log.txt"))
    dump = AuthDiagnostic(log, "密码不匹配")
    dump.add(
        "login 响应体",
        '{"password":"secret123","mac":"001122334455","msg":"fail"}',
    )
    dump.add_url("http://portal/eportal/index.jsp?wlanuserip=10.1.2.3")
    path = dump.save()

    assert path and os.path.basename(path).startswith("fuxin_dump_")
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    assert "secret123" not in text
    assert "0011****" in text
    assert "wlanuserip=10.1****" in text
    assert "密码不匹配" in text


def test_diagnostic_skips_without_log_path() -> None:
    dump = AuthDiagnostic(FakeLogger(), "失败")
    dump.add("login 响应体", "x")
    assert dump.save() == ""


def test_diagnostic_caps_file_size(tmp_path) -> None:
    log = _PathLogger(str(tmp_path / "fuxin_log.txt"))
    dump = AuthDiagnostic(log, "失败")
    for index in range(6):
        dump.add(f"响应{index}", "a" * 60000)
    path = dump.save()
    assert path
    size = os.path.getsize(path)
    assert size <= MAX_DUMP_BYTES + 200


# ------------------------------------------------------- 4. 认证后复检
def test_wait_online_probes_before_sleeping(monkeypatch) -> None:
    log = FakeLogger()
    monkeypatch.setattr(net, "check_online", lambda *args, **kwargs: True)

    def fail_sleep(*args, **kwargs):
        raise AssertionError("网络已恢复时不应先等待")

    monkeypatch.setattr(auth, "_sleep", fail_sleep)
    started = time.monotonic()
    auth._wait_online(threading.Event(), log)
    assert time.monotonic() - started < 1.0
    assert any("复检第1次" in line for line in log.lines)


def test_wait_online_times_out(monkeypatch) -> None:
    log = FakeLogger()
    monkeypatch.setattr(net, "check_online", lambda *args, **kwargs: False)
    monkeypatch.setattr(auth, "WAIT_PROBE_MAX", 0)
    with pytest.raises(auth.AuthFailed):
        auth._wait_online(threading.Event(), log)


# ------------------------------------------------------- 5. 完整认证流程
_STATE = {"modulus": "", "login_ok": False}


class MockPortal(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # 静音
        pass

    def _send(self, status: int, body: bytes = b"", headers: dict | None = None) -> None:
        self.send_response(status)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parts = urlsplit(self.path)
        path = parts.path
        query = parts.query

        if path == "/":
            self._send(
                302,
                headers={
                    "Location": (
                        "/eportal/index.jsp?wlanuserip=10a1b2c3d4"
                        "&nasportid=99aabbcc&t=wireless-v2"
                    )
                },
            )
            return
        if path == "/eportal/index.jsp":
            self._send(200, b"<html><body>portal</body></html>")
            return
        if path == "/eportal/InterFace.do" and "method=pageInfo" in query:
            payload = (
                '{"result":"success","passwordEncrypt":"true",'
                f'"publicKeyExponent":"010001","publicKeyModulus":"{_STATE["modulus"]}"}}'
            )
            self._send(200, payload.encode())
            return
        if path == "/eportal/InterFace.do" and "method=login" in query:
            self._send(200, b'{"result":"success"}')
            return
        self._send(404, b"not found")

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode()
        form = {key: value[0] for key, value in parse_qs(raw, keep_blank_values=True).items()}
        if "method=login" not in self.path:
            self._send(404, b"not found")
            return

        expect = {
            "userId": "2025015413",
            "service": "",
            "passwordEncrypt": "true",
            "queryString": "wlanuserip=10a1b2c3d4&nasportid=99aabbcc&t=wireless-v2",
        }
        ok = all(form.get(key) == value for key, value in expect.items())
        # 密码必须是 RSA 加密后的十进制串（而非明文）
        ok = ok and form.get("password", "").isdigit() and form.get("password") != "test123"
        if ok:
            _STATE["login_ok"] = True
            self._send(200, b'{"result":"success"}')
        else:
            body = (
                '{"result":"fail","message":"\\u5bc6\\u7801\\u4e0d\\u5339\\u914d,'
                '\\u8bf7\\u8f93\\u5165\\u6b63\\u786e\\u7684\\u5bc6\\u7801!"}'
            )
            self._send(200, body.encode())


def test_auth_flow_with_mock_portal(monkeypatch) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    _STATE["modulus"] = key.private_numbers().public_numbers.n.to_bytes(128, "big").hex()
    _STATE["login_ok"] = False

    server = ThreadingHTTPServer(("127.0.0.1", 0), MockPortal)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("FUXIN_PORTAL_HOST", f"127.0.0.1:{port}")

    online = {"value": False}
    monkeypatch.setattr(net, "check_online", lambda *args, **kwargs: online["value"])
    monkeypatch.setattr(auth, "WAIT_PROBE_GAP", 0.05)

    log = FakeLogger()
    stop = threading.Event()
    try:
        probe = net.http_request(f"http://{net.portal_host()}/", timeout=3.0)
        assert probe.status == 302

        portal = net.find_portal_url(log)
        assert portal.startswith("http://127.0.0.1") and "index.jsp" in portal

        def flip() -> None:
            time.sleep(0.3)
            online["value"] = True

        threading.Thread(target=flip, daemon=True).start()
        auth.try_authenticate(stop, log, "2025015413", "test123")
        assert _STATE["login_ok"] is True

        # 凭据错误分支
        _STATE["login_ok"] = False
        online["value"] = False
        with pytest.raises(auth.AuthFailed) as excinfo:
            auth.try_authenticate(stop, log, "wrong-user", "bad")
        message = str(excinfo.value)
        assert "密码不匹配" in message
        assert auth.is_account_error(message)
    finally:
        server.shutdown()


# ------------------------------------------------------- 6. 配置加密
def test_config_encrypt_roundtrip() -> None:
    token = fconfig.encrypt_text("2025015413", "user")
    assert token != "2025015413"
    assert fconfig.decrypt_text(token, "user") == "2025015413"

    token2 = fconfig.encrypt_text("p@ssw0rd", "pass")
    assert token2 != fconfig.encrypt_text("p@ssw0rd", "pass")
    with pytest.raises(InvalidTag):
        fconfig.decrypt_text(token, "pass")


def test_config_reads_legacy_file(monkeypatch, tmp_path) -> None:
    sample = (
        f"user={fconfig.encrypt_text('2025015413', 'user')}\n"
        f"pass={fconfig.encrypt_text('pw', 'pass')}\n"
        "freq=300\n"
    )
    target = tmp_path / "fuxin_config.dat"
    target.write_text(sample, encoding="utf-8", newline="\n")
    monkeypatch.setattr(paths, "config_path", lambda: str(target))

    cfg = fconfig.Config(FakeLogger())
    assert cfg.user == "2025015413"
    assert cfg.freq == 300
