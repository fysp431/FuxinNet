"""ePortal 认证实现（协议与旧版逐字保持）。

流程：
    check_online → find_portal_url（≤6 跳）→ pageInfo → RSA → login → 等联网(5s×12)
关键约定：
- indexParams 必须原样回传（不解码再编码）
- 密码加密走 PKCS#1 v1.5 填充 + 裸模幂，输出十进制字符串
- 公钥缺失/长度异常时直接报错，绝不回退明文密码（否则网关只会回"密码不匹配"）
- redirectortosuccess 且无参数 = 会话残留，用空参数强制 login 刷新
- 用户不存在 / 密码不匹配 = 凭据错误，上层应立即停止监控

日志：每个协议步骤都输出一行 [数据] 运行数据（状态码 / 耗时 / 公钥位数 /
结果），不记录密码、公钥原文和完整页面内容，便于事后定位网关行为差异。
"""

from __future__ import annotations

import binascii
import json
import os
import re
import threading
import time
import urllib.parse

from . import net
from .diagnostics import AuthDiagnostic

WAIT_PROBE_GAP = 5
WAIT_PROBE_MAX = 60

_RE_EXP = re.compile(r'"publicKeyExponent"\s*:\s*"?([0-9a-fA-F]+)"?')
_RE_MOD = re.compile(r'"publicKeyModulus"\s*:\s*"?([0-9a-fA-F]+)"?')
_RE_MSG = re.compile(r'"message"\s*:\s*"((?:[^"\\]|\\.)*)"')
_RE_UNICODE = re.compile(r"\\u([0-9a-fA-F]{4})")


class AuthStopped(Exception):
    """用户点了停止，静默退出。"""


class AuthFailed(Exception):
    """认证失败，message 用于界面提示。"""


class AuthFatal(AuthFailed):
    """不可恢复错误（如网关公钥异常），上层应立即停止而不是重试。"""


# ---------------------------------------------------------------- 工具函数
def decode_unicode_escapes(text: str) -> str:
    """把 ``\\u7528`` 之类的转义解成中文（正确处理代理对）。"""

    def _sub(match: re.Match) -> str:
        return chr(int(match.group(1), 16))

    decoded = _RE_UNICODE.sub(_sub, text)
    if any(0xD800 <= ord(ch) <= 0xDFFF for ch in decoded):
        try:
            decoded = decoded.encode("utf-16", "surrogatepass").decode("utf-16")
        except UnicodeDecodeError:
            pass
    return decoded


def _mask_user(username: str) -> str:
    """日志里只保留学号前 3 位。"""
    if len(username) <= 3:
        return "***"
    return username[:3] + "***"


def is_account_error(message: str) -> bool:
    return "用户不存在" in message or "密码不匹配" in message


def _stopped(stop: threading.Event) -> bool:
    return stop.is_set()


def _sleep(stop: threading.Event, seconds: float) -> bool:
    """可被停止打断的等待；返回 True 表示被停止。"""
    return stop.wait(seconds)


def _urlencode(params: dict) -> str:
    return urllib.parse.urlencode(params, quote_via=urllib.parse.quote)


def _json_load(body: str) -> dict | None:
    try:
        value = json.loads(body)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def parse_login_result(body: str) -> tuple[bool, str]:
    """解析 login 返回；兼容字段间空格、以及非 JSON 的 \\uXXXX 文本。"""
    payload = _json_load(body)
    if payload is not None:
        result = str(payload.get("result", "")).strip().lower()
        if result == "success":
            return True, ""
        message = payload.get("message") or payload.get("msg") or ""
        return False, str(message)

    compact = body.replace(" ", "").replace("\n", "").replace("\r", "").lower()
    if '"result":"success"' in compact or body.strip().lower() == "success":
        return True, ""
    match = _RE_MSG.search(body)
    if match:
        return False, decode_unicode_escapes(match.group(1))
    return False, ""


# ---------------------------------------------------------------- 协议步骤
def get_page_info(host: str, index_params: str, logger=None) -> dict:
    url = f"http://{host}/eportal/InterFace.do?method=pageInfo&indexParams={index_params}"
    started = time.monotonic()
    resp = net.http_request(url, timeout=8.0)
    cost = int((time.monotonic() - started) * 1000)
    info = {"encrypt": False, "exponent": "", "modulus": "", "body": "", "error": ""}
    if resp.error:
        info["error"] = resp.error
        if logger is not None:
            logger.log(f"[数据] pageInfo 请求失败 用时={cost}ms err={resp.error[:100]}")
        return info

    body = resp.text("utf-8")
    info["body"] = body
    payload = _json_load(body)
    if payload is not None:
        flag = payload.get("passwordEncrypt", False)
        info["encrypt"] = flag is True or str(flag).strip().lower() == "true"
        info["exponent"] = str(payload.get("publicKeyExponent") or "")
        info["modulus"] = str(payload.get("publicKeyModulus") or "")
    else:
        if '"passwordEncrypt":"true"' in body or '"passwordEncrypt":true' in body:
            info["encrypt"] = True
        match = _RE_EXP.search(body)
        if match:
            info["exponent"] = match.group(1)
        match = _RE_MOD.search(body)
        if match:
            info["modulus"] = match.group(1)

    if logger is not None:
        bits = len(info["modulus"]) * 4 if info["modulus"] else 0
        logger.log(
            f"[数据] pageInfo 状态={resp.status} 用时={cost}ms "
            f"加密={info['encrypt']} 公钥位数={bits}"
        )
    return info


def rsa_encrypt(exponent_hex: str, modulus_hex: str, plaintext: str) -> str:
    """PKCS#1 v1.5 填充 + 裸模幂，输出十进制字符串。

    公钥格式异常、长度不足或密码过长时抛 ValueError —— 绝不回退明文，
    否则上层会误报"密码不匹配"，掩盖真正原因。
    """
    try:
        exponent_bytes = binascii.unhexlify(exponent_hex)
        modulus_bytes = binascii.unhexlify(modulus_hex)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("公钥格式无效") from exc
    if not exponent_bytes or not modulus_bytes:
        raise ValueError("公钥为空")

    size = len(modulus_bytes)
    if size < 64:
        raise ValueError(f"公钥长度异常（{size} 字节）")

    data = plaintext.encode("utf-8")
    if len(data) > size - 11:
        raise ValueError("密码过长，超出网关公钥可加密长度")

    pad_len = size - len(data) - 3
    padding = bytearray(os.urandom(pad_len))
    for i, byte in enumerate(padding):
        if byte == 0:
            padding[i] = 0x01
    block = b"\x00\x02" + bytes(padding) + b"\x00" + data

    exponent = int.from_bytes(exponent_bytes, "big")
    modulus = int.from_bytes(modulus_bytes, "big")
    value = int.from_bytes(block, "big")
    encrypted = pow(value, exponent, modulus)
    return str(encrypted)


def login(
    host: str,
    index_params: str,
    username: str,
    password: str,
    logger=None,
    stop=None,
    dump: AuthDiagnostic | None = None,
) -> tuple[bool, str]:
    """提交登录，返回 (是否成功, 网页原始提示)。

    ``dump`` 非空时收集 pageInfo / login 原始响应，认证失败时由上层落盘。
    """
    if stop is not None and stop.is_set():
        raise AuthStopped()

    info = get_page_info(host, index_params, logger)
    if dump is not None:
        dump.add("pageInfo 响应体", info.get("body", "") or info.get("error", ""))
    if stop is not None and stop.is_set():
        raise AuthStopped()

    pwd = password
    if info["encrypt"]:
        if not (info["exponent"] and info["modulus"]):
            raise AuthFatal("网关要求加密密码，但 pageInfo 未返回公钥")
        try:
            pwd = rsa_encrypt(info["exponent"], info["modulus"], password)
        except ValueError as exc:
            raise AuthFatal(f"密码加密失败：{exc}") from exc

    if stop is not None and stop.is_set():
        raise AuthStopped()

    form = {
        "userId": username,
        "password": pwd,
        "service": "",
        "passwordEncrypt": "true" if info["encrypt"] else "false",
        "queryString": index_params,
    }
    payload = _urlencode(form).encode("utf-8")
    url = f"http://{host}/eportal/InterFace.do?method=login"
    started = time.monotonic()
    resp = net.http_request(
        url,
        method="POST",
        body=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=8.0,
    )
    cost = int((time.monotonic() - started) * 1000)
    if resp.error:
        if dump is not None:
            dump.add("login 请求错误", resp.error)
        if logger is not None:
            logger.log(f"[数据] login 请求失败 用时={cost}ms err={resp.error[:100]}")
        return False, "无法连接认证服务器"

    body = resp.text("utf-8")
    if dump is not None:
        dump.add("login 响应体", body)
    ok, message = parse_login_result(body)
    if not ok and not message:
        message = "认证失败（服务器未返回原因）"
    if logger is not None:
        logger.log(
            f"[数据] login 状态={resp.status} 用时={cost}ms "
            f"结果={'success' if ok else 'fail'} 提示={message[:120]}"
        )
    return ok, message


# ---------------------------------------------------------------- 完整流程
def try_authenticate(
    stop: threading.Event,
    logger,
    username: str,
    password: str,
) -> None:
    """成功返回 None；失败抛 AuthFailed；被停止抛 AuthStopped。

    失败时把本次的原始响应落盘为诊断文件（已脱敏），主日志只记路径摘要。
    """
    dump = AuthDiagnostic(logger)
    dump.add("账号", _mask_user(username))
    try:
        _authenticate_flow(stop, logger, username, password, dump)
    except AuthStopped:
        raise
    except AuthFailed as exc:
        dump.reason = str(exc)
        path = dump.save()
        if path:
            logger.log(f"[诊断] 认证失败现场已保存：{path}")
        raise


def _authenticate_flow(
    stop: threading.Event,
    logger,
    username: str,
    password: str,
    dump: AuthDiagnostic,
) -> None:
    """认证主流程；各失败分支抛 AuthFailed，由 try_authenticate 统一落盘。"""
    host = net.portal_host()
    started = time.monotonic()

    if _stopped(stop):
        raise AuthStopped()

    # 无感认证可能刚好恢复，先快速复检一次
    if net.check_online(logger, stop=stop):
        if _stopped(stop):
            raise AuthStopped()
        cost = int((time.monotonic() - started) * 1000)
        logger.log(f"[数据] 认证前复检：网络已在线，跳过认证 用时={cost}ms")
        return
    if _stopped(stop):
        raise AuthStopped()

    logger.log(f"[认证] 开始认证 门户={host} 账号={_mask_user(username)}")
    portal_url = net.find_portal_url(logger, stop, on_url=dump.add_url)
    if _stopped(stop):
        raise AuthStopped()

    if not portal_url:
        # 区分诊断：服务器能不能直连
        probe = net.http_request(f"http://{host}/eportal/InterFace.do?method=login", timeout=5.0)
        dump.add("门户可达性检查", f"状态={probe.status} err={probe.error}")
        logger.log(
            f"[数据] 门户可达性检查 状态={probe.status} err={probe.error[:100]}"
        )
        if not probe.error and probe.status:
            raise AuthFailed("认证服务器可达但未获取到认证参数（可能网络已恢复）")
        raise AuthFailed(f"无法连接认证服务器 {host}")

    index_params = ""
    if "?" in portal_url:
        index_params = portal_url.split("?", 1)[1]

    # 会话残留：eportal 认为已认证但没有参数 → 空参数强制刷新
    if not index_params and "redirectortosuccess" in portal_url:
        logger.log("eportal 显示已认证但网络不通，强制刷新认证会话")
        ok, message = login(host, "", username, password, logger, stop, dump)
        if not ok:
            raise AuthFailed(message)
        _wait_online(stop, logger)
        return

    if not index_params:
        raise AuthFailed(f"认证页面参数解析失败（跳转URL: {portal_url[:60]}）")

    logger.log("已获取认证跳转URL: " + portal_url)
    keys = [key for key, _ in urllib.parse.parse_qsl(index_params, keep_blank_values=True)]
    logger.log(f"[数据] 认证参数键={keys} 长度={len(index_params)}")

    ok, message = login(host, index_params, username, password, logger, stop, dump)
    if not ok:
        raise AuthFailed(message)

    logger.log(f"[数据] 认证提交成功，开始复检 总用时={int((time.monotonic() - started) * 1000)}ms")
    _wait_online(stop, logger)


def _wait_online(stop: threading.Event, logger) -> None:
    """认证提交后复检：先探一次，未通再等待，避免"网关秒放行也要空等 5 秒"。"""
    deadline = time.monotonic() + WAIT_PROBE_MAX
    attempt = 0
    while True:
        attempt += 1
        started = time.monotonic()
        online = net.check_online(logger, stop=stop)
        cost = int((time.monotonic() - started) * 1000)
        if _stopped(stop):
            raise AuthStopped()
        logger.log(
            f"[数据] 认证后复检第{attempt}次：{'在线' if online else '仍离线'} 用时={cost}ms"
        )
        if online:
            return
        if time.monotonic() >= deadline:
            break
        if _sleep(stop, WAIT_PROBE_GAP):
            raise AuthStopped()
    raise AuthFailed(f"认证已提交，但等待 {WAIT_PROBE_MAX} 秒后网络仍未连通")
