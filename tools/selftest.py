"""核心逻辑自测（不需要真实校园网）。

覆盖：
1. RSA PKCS#1 v1.5：用真实密钥对加密后再私钥解密，必须还原
2. unicode 转义解码（含代理对）
3. 完整认证流程：本地 mock eportal（302 → 认证页 → pageInfo → login → 等联网）
4. 凭据错误识别
5. 配置加密往返 + 兼容旧文件格式

用法： .venv\\Scripts\\python.exe tools\\selftest.py
"""

from __future__ import annotations

import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "src"))

from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

from fuxin import auth, config as fconfig, net, paths  # noqa: E402

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name} {detail}")
        FAILURES.append(name)


class _Logger:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def log(self, message, limit: int = 800) -> None:
        self.lines.append(str(message))


# ------------------------------------------------------------------ 1. RSA
def test_rsa() -> None:
    print("\n[1] RSA PKCS#1 v1.5")
    key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    numbers = key.private_numbers()
    modulus = numbers.public_numbers.n.to_bytes(128, "big")
    exponent = (65537).to_bytes(3, "big")
    mod_hex = modulus.hex()
    exp_hex = exponent.hex()

    plaintext = "2025015413"
    encrypted = auth.rsa_encrypt(exp_hex, mod_hex, plaintext)
    check("输出为十进制数字串", encrypted.isdigit(), encrypted[:20])

    cipher_value = int(encrypted)
    recovered = pow(cipher_value, numbers.d, numbers.public_numbers.n)
    block = recovered.to_bytes(128, "big")
    check("填充以 00 02 开头", block[:2] == b"\x00\x02", block[:4].hex())
    separator = block.index(b"\x00", 2)
    check("填充长度 >= 8", separator - 2 >= 8, str(separator))
    check("还原出原文", block[separator + 1:].decode() == plaintext)

    too_long = "x" * 200
    try:
        auth.rsa_encrypt(exp_hex, mod_hex, too_long)
        check("超长密码拒绝加密（不回退明文）", False, "仍然返回了明文")
    except ValueError:
        check("超长密码拒绝加密（不回退明文）", True)


# --------------------------------------------------- 2. unicode 转义解码
def test_unicode() -> None:
    print("\n[2] unicode 转义解码")
    check("中文转义", auth.decode_unicode_escapes(r"\u7528\u6237\u4e0d\u5b58\u5728") == "用户不存在")
    check("混合文本", auth.decode_unicode_escapes(r"a\u7528b") == "a用b")
    plain = "用户不存在,请输入正确的用户名!"
    check("无转义原样返回", auth.decode_unicode_escapes(plain) == plain)
    pair = auth.decode_unicode_escapes(r"\ud83d\ude00")
    check("代理对", pair == "😀", repr(pair))


# ------------------------------------------------------- 3. 完整认证流程
MODULUS_HEX = ""
EXPONENT_HEX = "010001"
LOGIN_OK = {"value": False}


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
                headers={"Location": "/eportal/index.jsp?wlanuserip=10a1b2c3d4&nasportid=99aabbcc&t=wireless-v2"},
            )
            return
        if path == "/eportal/index.jsp":
            self._send(200, "<html><body>portal</body></html>".encode())
            return
        if path == "/eportal/InterFace.do" and "method=pageInfo" in query:
            payload = (
                '{"result":"success","passwordEncrypt":"true","publicKeyExponent":"%s",'
                '"publicKeyModulus":"%s"}' % (EXPONENT_HEX, MODULUS_HEX)
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
        form = {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}
        if "method=login" not in self.path:
            self._send(404, b"not found")
            return
        expect = {
            "userId": "2025015413",
            "service": "",
            "passwordEncrypt": "true",
            "queryString": "wlanuserip=10a1b2c3d4&nasportid=99aabbcc&t=wireless-v2",
        }
        ok = all(form.get(k) == v for k, v in expect.items())
        # 密码必须是 RSA 加密后的十进制串（而非明文）
        ok = ok and form.get("password", "").isdigit() and form.get("password") != "test123"
        if ok:
            LOGIN_OK["value"] = True
            self._send(200, b'{"result":"success"}')
        else:
            body = (
                '{"result":"fail","message":"\\u5bc6\\u7801\\u4e0d\\u5339\\u914d,'
                '\\u8bf7\\u8f93\\u5165\\u6b63\\u786e\\u7684\\u5bc6\\u7801!"}'
            )
            self._send(200, body.encode())


def test_auth_flow() -> bool:
    global MODULUS_HEX
    print("\n[3] 完整认证流程（mock eportal）")

    key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    MODULUS_HEX = key.private_numbers().public_numbers.n.to_bytes(128, "big").hex()

    server = ThreadingHTTPServer(("127.0.0.1", 0), MockPortal)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    os.environ["FUXIN_PORTAL_HOST"] = f"127.0.0.1:{port}"

    online = {"value": False}
    original_check = net.check_online
    net.check_online = lambda *args, **kwargs: online["value"]
    try:
        logger = _Logger()
        stop = threading.Event()

        probe = net.http_request(f"http://{net.portal_host()}/", timeout=3.0)
        check("mock 根路径返回 302", probe.status == 302, str(probe.status))
        portal = net.find_portal_url(logger)
        check("解析出认证页", portal.startswith("http://127.0.0.1") and "index.jsp" in portal, portal)

        # login 成功后让网络"恢复"
        def _flip() -> None:
            time.sleep(0.6)
            online["value"] = True

        threading.Thread(target=_flip, daemon=True).start()
        auth.try_authenticate(stop, logger, "2025015413", "test123")
        check("认证流程成功", LOGIN_OK["value"] is True)

        # 凭据错误分支
        LOGIN_OK["value"] = False
        online["value"] = False
        try:
            auth.try_authenticate(stop, logger, "wrong-user", "bad")
            check("错误凭据抛异常", False, "没有抛异常")
        except auth.AuthFailed as exc:
            message = str(exc)
            check("错误凭据提示已解码", "密码不匹配" in message, message)
            check("识别为账号类错误", auth.is_account_error(message))
    finally:
        net.check_online = original_check
        server.shutdown()
    return True


# ------------------------------------------------------- 4. 配置加密往返
def test_config() -> None:
    print("\n[4] 配置加密存储")
    token = fconfig.encrypt_text("2025015413", "user")
    check("密文不是明文", token != "2025015413")
    check("往返一致", fconfig.decrypt_text(token, "user") == "2025015413")
    token2 = fconfig.encrypt_text("p@ssw0rd", "pass")
    check("两次加密结果不同（随机 nonce）", token2 != fconfig.encrypt_text("p@ssw0rd", "pass"))

    try:
        fconfig.decrypt_text(token, "pass")
        check("错误 salt 必须失败", False)
    except Exception:
        check("错误 salt 解密失败", True)

    # 与旧版文件格式兼容
    sample = f"user={fconfig.encrypt_text('2025015413', 'user')}\npass={fconfig.encrypt_text('pw', 'pass')}\nfreq=300\n"
    tmp = os.path.join(_ROOT, "_selftest_config.dat")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(sample)
    old = paths.config_path
    paths.config_path = lambda: tmp  # type: ignore[assignment]
    try:
        cfg = fconfig.Config(_Logger())
        check("读取旧格式学号", cfg.user == "2025015413", cfg.user)
        check("读取旧格式频率", cfg.freq == 300, str(cfg.freq))
    finally:
        paths.config_path = old  # type: ignore[assignment]
        os.remove(tmp)


def main() -> int:
    print("=" * 60)
    print("福信校网通 - 核心自测")
    print("=" * 60)
    test_rsa()
    test_unicode()
    test_auth_flow()
    test_config()
    print("\n" + "=" * 60)
    if FAILURES:
        print(f"失败 {len(FAILURES)} 项：{FAILURES}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
