"""P0 修复回归测试（不依赖 cryptography，也不需要校园网）。

覆盖：
1. 日志脱敏：IPv4 / MAC / URL 编码 / JSON 形式 / 口令字段，避免误伤普通单词
2. resolve_url：协议相对 URL、JS/meta 跳转
3. login 返回值解析：JSON 字段带空格、JSON unicode 转义、非 JSON 文本
4. RSA：失败不再回退明文（纯 Python 生成密钥对验证 PKCS#1 填充）
5. 不可恢复错误：公钥异常抛 AuthFatal
6. Monitor 生命周期：stop 不阻塞、stopping 状态、中断不误认证、探测异常可恢复
7. 顶层异常：写堆栈并置 error
8. api.stop：立即返回且文案反映真实状态
9. 检测频率：最低 10 秒/次

用法： python tools/test_p0.py
"""

from __future__ import annotations

import os
import random
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))

from fuxin import api as fuxin_api  # noqa: E402
from fuxin import auth, logger, net, worker  # noqa: E402

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name} {detail}")
        FAILURES.append(name)


# ------------------------------------------------------------------ 1. 脱敏
def test_mask() -> None:
    print("\n[1] 日志脱敏")
    cases = [
        ("mac=001122334455", "mac=0011****"),
        ("macAddress=001122334455", "macAddress=0011****"),
        ("wlanuserip=10.1.2.3&x=1", "wlanuserip=10.1****&x=1"),
        (
            "indexParams=wlanuserip%3D10.1.2.3%26mac%3D001122334455",
            "indexParams=wlanuserip%3D10.1****%26mac%3D0011****",
        ),
        ("password=secret123", "password=****"),
        ("ticket=abc%2Fdef", "ticket=****"),
        (
            'pageInfo={"mac":"001122334455","wlanuserip":"10.1.2.3"}',
            'pageInfo={"mac":"0011****","wlanuserip":"10.1****"}',
        ),
        ('{"password":"secret123"}', '{"password":"****"}'),
        ("compass=abc", "compass=abc"),
        ("普通日志没有敏感字段", "普通日志没有敏感字段"),
    ]
    for raw, expected in cases:
        actual = logger.mask_sensitive(raw)
        check(f"脱敏 {raw[:32]}", actual == expected, f"得到 {actual!r}")


# ------------------------------------------------------------ 2. URL 解析
def test_resolve_url() -> None:
    print("\n[2] 跳转 URL 解析")
    base = "http://172.16.9.11/eportal/index.jsp?x=1"
    check(
        "协议相对 URL",
        net.resolve_url("//portal.local/login?x=1", base)
        == "http://portal.local/login?x=1",
        net.resolve_url("//portal.local/login?x=1", base),
    )
    check(
        "https 基址保持协议",
        net.resolve_url("//portal.local/x", "https://10.0.0.1/a")
        == "https://portal.local/x",
    )
    check("绝对路径", net.resolve_url("/a/b", base) == "http://172.16.9.11/a/b")
    check("绝对 URL 原样返回", net.resolve_url("http://h/x", base) == "http://h/x")
    jump = net.extract_jump(
        "<script>window.location.href='/eportal/index.jsp?wlanuserip=1&mac=2'</script>",
        base,
    )
    check(
        "JS 跳转提取",
        jump == "http://172.16.9.11/eportal/index.jsp?wlanuserip=1&mac=2",
        jump,
    )
    meta = net.extract_jump(
        '<meta http-equiv="refresh" content="0;url=/eportal/index.jsp?x=12345">', base
    )
    check("meta 跳转提取", meta == "http://172.16.9.11/eportal/index.jsp?x=12345", meta)


# ---------------------------------------------------------- 3. login 解析
def test_parse_login_result() -> None:
    print("\n[3] login 返回值解析")
    cases = [
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
    ]
    for body, want_ok, want_message in cases:
        ok, message = auth.parse_login_result(body)
        check(f"解析 {body[:40]}", ok is want_ok and message == want_message, f"{ok}/{message!r}")


# ------------------------------------------------------------------ 4. RSA
def _is_probable_prime(number: int, rounds: int = 8) -> bool:
    if number < 2:
        return False
    for prime in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if number % prime == 0:
            return number == prime
    odd = number - 1
    exponent = 0
    while odd % 2 == 0:
        odd //= 2
        exponent += 1
    for _ in range(rounds):
        base = random.randrange(2, number - 1)
        value = pow(base, odd, number)
        if value in (1, number - 1):
            continue
        for _ in range(exponent - 1):
            value = value * value % number
            if value == number - 1:
                break
        else:
            return False
    return True


def _gen_prime(bits: int, rng: random.Random) -> int:
    while True:
        candidate = rng.getrandbits(bits) | (1 << (bits - 1)) | 1
        if _is_probable_prime(candidate):
            return candidate


def test_rsa() -> None:
    print("\n[4] RSA 加密（不回退明文）")
    rng = random.Random(20261126)
    p = _gen_prime(512, rng)
    q = _gen_prime(512, rng)
    modulus = p * q
    exponent = 65537
    private = pow(exponent, -1, (p - 1) * (q - 1))
    modulus_hex = modulus.to_bytes(128, "big").hex()

    plaintext = "2025015413"
    encrypted = auth.rsa_encrypt("010001", modulus_hex, plaintext)
    check("输出为十进制数字串", encrypted.isdigit())
    block = pow(int(encrypted), private, modulus).to_bytes(128, "big")
    check("PKCS#1 填充头 00 02", block[:2] == bytes([0, 2]), block[:2].hex())
    separator = block.index(bytes([0]), 2)
    check("填充长度 >= 8", separator - 2 >= 8, str(separator))
    check("还原出原文", block[separator + 1 :].decode() == plaintext)

    failures = [
        ("超长密码", lambda: auth.rsa_encrypt("010001", modulus_hex, "x" * 200)),
        ("非法公钥", lambda: auth.rsa_encrypt("zz", modulus_hex, "x")),
        ("公钥过短", lambda: auth.rsa_encrypt("010001", "abcd", "x")),
    ]
    for name, call in failures:
        try:
            call()
            check(f"{name} 应抛 ValueError", False, "没有抛异常")
        except ValueError:
            check(f"{name} 应抛 ValueError", True)


# --------------------------------------------------------- 5. 不可恢复错误
def test_auth_fatal() -> None:
    print("\n[5] 不可恢复认证错误")
    original = auth.get_page_info
    try:
        auth.get_page_info = lambda *args, **kwargs: {
            "encrypt": True,
            "exponent": "",
            "modulus": "",
        }
        try:
            auth.login("127.0.0.1", "index", "user", "pass", None)
            check("公钥缺失抛 AuthFatal", False, "没有抛异常")
        except auth.AuthFatal as exc:
            check("公钥缺失抛 AuthFatal", True)
            check("AuthFatal 是 AuthFailed 子类", isinstance(exc, auth.AuthFailed))

        auth.get_page_info = lambda *args, **kwargs: {
            "encrypt": True,
            "exponent": "010001",
            "modulus": "abcd",
        }
        try:
            auth.login("127.0.0.1", "index", "user", "pass", None)
            check("公钥异常抛 AuthFatal", False, "没有抛异常")
        except auth.AuthFatal:
            check("公钥异常抛 AuthFatal", True)
    finally:
        auth.get_page_info = original


# --------------------------------------------------------------- 6. 线程
class _FakeConfig:
    user = ""
    password = ""

    def __init__(self) -> None:
        self.saved: list[str] = []

    def credentials(self) -> tuple[str, str]:
        return ("2025015413", "pw")

    def freq_value(self) -> int:
        return 1

    def set_user(self, value: str) -> None:
        self.user = value
        self.saved.append("user")

    def set_password(self, value: str) -> None:
        self.password = value
        self.saved.append("password")

    def set_freq(self, value: int) -> None:
        self.saved.append(f"freq={value}")

    def save(self) -> None:
        self.saved.append("save")


class _CrashConfig(_FakeConfig):
    def __init__(self) -> None:
        self.calls = 0

    def freq_value(self) -> int:
        self.calls += 1
        if self.calls >= 2:
            raise RuntimeError("config boom")
        return 1


class _FakeLogger:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def log(self, message, limit: int = 800) -> None:
        self.lines.append(str(message))

    def log_block(self, message, limit: int = 4000) -> None:
        self.lines.append(str(message))

    def cursor(self) -> int:
        return len(self.lines)


def _wait_stopped(monitor, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not monitor.running and not monitor.stopping:
            return
        time.sleep(0.05)


def test_worker_lifecycle() -> None:
    print("\n[6] Monitor 生命周期")
    original_check = net.check_online
    original_auth = auth.try_authenticate
    fake_logger = _FakeLogger()
    monitor = worker.Monitor(_FakeConfig(), fake_logger)
    try:
        net.check_online = lambda *args, **kwargs: True
        ok, message = monitor.start()
        check("启动成功", ok, message)
        time.sleep(0.2)
        check("运行中 running=True", monitor.running)
        check(
            "旧 run_id 不能写状态",
            not monitor._update(monitor._run_id - 1, phase="hacked")
            and monitor.phase != "hacked",
        )

        started = time.monotonic()
        monitor.stop()
        elapsed = time.monotonic() - started
        check("stop 立即返回（不阻塞 UI）", elapsed < 0.5, f"{elapsed:.3f}s")
        check("停止后 running=False", not monitor.running)
        _wait_stopped(monitor)
        check("线程退出后 phase=idle", monitor.phase == worker.PHASE_IDLE, monitor.phase)
        check(
            "线程退出有运行数据",
            any("监控线程退出" in line for line in fake_logger.lines),
        )

        ok, message = monitor.start()
        check("旧线程退出后可再次启动", ok, message)
        time.sleep(0.15)
        monitor.stop()
        _wait_stopped(monitor)
        check("第二次停止同样复位", not monitor.running and monitor.phase == worker.PHASE_IDLE)

        def slow_offline(*args, **kwargs):
            time.sleep(0.8)
            return False

        net.check_online = slow_offline
        fake_logger.lines.clear()
        monitor.start()
        time.sleep(0.1)
        started = time.monotonic()
        monitor.stop()
        elapsed = time.monotonic() - started
        check("长探测期间 stop 仍立即返回", elapsed < 0.5, f"{elapsed:.3f}s")
        check("停止后 snapshot.stopping=True", monitor.snapshot().get("stopping") is True)
        _wait_stopped(monitor)
        check(
            "探测途中停止不会误进入认证",
            not any("开始认证" in line for line in fake_logger.lines),
            str(fake_logger.lines[-3:]),
        )
        check("中断后 phase=idle", monitor.phase == worker.PHASE_IDLE, monitor.phase)

        def boom(*args, **kwargs):
            raise RuntimeError("boom")

        net.check_online = boom
        fake_logger.lines.clear()
        monitor.start()
        time.sleep(0.5)
        check("探测异常后监控仍在运行", monitor.running)
        check("探测异常已记录", any("探测异常" in line for line in fake_logger.lines))
        monitor.stop()
        _wait_stopped(monitor)
        check("探测异常后仍可正常停止", monitor.phase == worker.PHASE_IDLE)

        net.check_online = lambda *args, **kwargs: False

        def fatal(*args, **kwargs):
            raise auth.AuthFatal("公钥异常")

        auth.try_authenticate = fatal
        fake_logger.lines.clear()
        monitor.start()
        _wait_stopped(monitor)
        check("AuthFatal 后线程退出", not monitor.running)
        check("AuthFatal 后 phase=error", monitor.phase == worker.PHASE_ERROR, monitor.phase)
        attempts = sum(
            "[认证] 第" in line and "次尝试" in line for line in fake_logger.lines
        )
        check("AuthFatal 只尝试一次", attempts == 1, str(fake_logger.lines))
        check("AuthFatal 文案可见", any("不可恢复" in line for line in fake_logger.lines))
    finally:
        net.check_online = original_check
        auth.try_authenticate = original_auth
        try:
            monitor.stop(wait=True, timeout=2.0)
        except Exception:
            pass


# ------------------------------------------------------- 7. 顶层异常兜底
def test_worker_crash() -> None:
    print("\n[7] 顶层异常兜底")
    original_check = net.check_online
    fake_logger = _FakeLogger()
    monitor = worker.Monitor(_CrashConfig(), fake_logger)
    try:
        net.check_online = lambda *args, **kwargs: True
        ok, message = monitor.start()
        check("崩溃场景启动成功", ok, message)
        _wait_stopped(monitor)
        check("顶层异常后线程退出", not monitor.running)
        check("顶层异常后 phase=error", monitor.phase == worker.PHASE_ERROR, monitor.phase)
        check(
            "异常堆栈已写入日志",
            any("监控线程崩溃" in line for line in fake_logger.lines),
        )
    finally:
        net.check_online = original_check
        try:
            monitor.stop(wait=True, timeout=2.0)
        except Exception:
            pass


# ------------------------------------------------------ 8. api.stop 语义
def test_api_stop() -> None:
    print("\n[8] api.stop 状态与文案")
    original_check = net.check_online

    def slow_offline(*args, **kwargs):
        time.sleep(1.2)
        return False

    net.check_online = slow_offline
    fake_logger = _FakeLogger()
    monitor = worker.Monitor(_FakeConfig(), fake_logger)
    api_config = _FakeConfig()
    api = fuxin_api.Api(api_config, fake_logger, monitor)
    try:
        monitor.start()
        time.sleep(0.1)
        started = time.monotonic()
        reply = api.stop()
        elapsed = time.monotonic() - started
        check("api.stop 立即返回", elapsed < 0.5, f"{elapsed:.3f}s")
        check(
            "api.stop 返回正在停止",
            reply.get("ok") is True and reply.get("message") == "正在停止…",
            reply.get("message"),
        )
        check(
            "回包 state.stopping=True",
            reply.get("state", {}).get("stopping") is True,
        )
        for method, argument in (
            ("save_user", "9999999999"),
            ("save_pass", "newpass"),
            ("save_freq", 5),
        ):
            reply_save = getattr(api, method)(argument)
            check(
                f"stopping 期间 {method} 被拒绝",
                reply_save.get("ok") is False
                and "正在运行" in reply_save.get("message", ""),
                reply_save.get("message"),
            )
        check("stopping 期间未写入配置", not api_config.saved, str(api_config.saved))
        _wait_stopped(monitor)
        reply2 = api.stop()
        check("完全停止后返回未运行", reply2.get("ok") is False, reply2.get("message"))
    finally:
        net.check_online = original_check
        try:
            monitor.stop(wait=True, timeout=2.0)
        except Exception:
            pass


def test_freq_limit() -> None:
    print("\n[9] 检测频率下限")
    fake_logger = _FakeLogger()
    config = _FakeConfig()
    monitor = worker.Monitor(config, fake_logger)
    api = fuxin_api.Api(config, fake_logger, monitor)

    reply = api.save_freq(9)
    check(
        "9 秒被拒绝且提示下限 10",
        reply.get("ok") is False and "10" in reply.get("message", ""),
        reply.get("message"),
    )
    reply = api.save_freq(10)
    check("10 秒可以保存", reply.get("ok") is True, reply.get("message"))
    check("频率已写入配置", "freq=10" in config.saved, str(config.saved))


def main() -> int:
    print("=" * 60)
    print("P0 修复回归测试")
    print("=" * 60)
    test_mask()
    test_resolve_url()
    test_parse_login_result()
    test_rsa()
    test_auth_fatal()
    test_worker_lifecycle()
    test_worker_crash()
    test_api_stop()
    test_freq_limit()
    print("\n" + "=" * 60)
    if FAILURES:
        print(f"失败 {len(FAILURES)} 项：{FAILURES}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
