"""ePortal 认证实现（协议与旧版逐字保持）。

流程：
    check_online → find_portal_url（≤6 跳）→ pageInfo → RSA → login → 等联网(5s×12)
关键约定：
- indexParams 必须原样回传（不解码再编码）
- 密码加密走 PKCS#1 v1.5 填充 + 裸模幂，输出十进制字符串
- redirectortosuccess 且无参数 = 会话残留，用空参数强制 login 刷新
- 用户不存在 / 密码不匹配 = 凭据错误，上层应立即停止监控
"""

from __future__ import annotations

import binascii
import os
import re
import threading
import time
import urllib.parse
from typing import Optional

from . import net

WAIT_PROBE_GAP = 5
WAIT_PROBE_MAX = 60

_RE_EXP = re.compile(r'"publicKeyExponent":"?([0-9a-fA-F]+)"?')
_RE_MOD = re.compile(r'"publicKeyModulus":"?([0-9a-fA-F]+)"?')
_RE_MSG = re.compile(r'"message"\s*:\s*"((?:[^"\\]|\\.)*)"')
_RE_UNICODE = re.compile(r"\\u([0-9a-fA-F]{4})")


class AuthStopped(Exception):
    """用户点了停止，静默退出。"""


class AuthFailed(Exception):
    """认证失败，message 用于界面提示。"""


# ---------------------------------------------------------------- 工具函数
def decode_unicode_escapes(text: str) -> str:
    """把 ``\\u7528`` 之类的转义解成中文（正确处理代理对）。"""

    def _sub(match: re.Match) -> str:
        code = int(match.group(1), 16)
        return chr(code)

    decoded = _RE_UNICODE.sub(_sub, text)
    if any("\ud800" <= ch <= "\udfff" for ch in decoded):
        try:
            decoded = decoded.encode("utf-16", "surrogatepass").decode("utf-16")
        except UnicodeDecodeError:
            pass
    return decoded


def mask_sensitive(text: str) -> str:
    return net_mask(text)


def net_mask(text: str) -> str:
    from .logger import mask_sensitive as _mask

    return _mask(text)


def is_account_error(message: str) -> bool:
    return "用户不存在" in message or "密码不匹配" in message


def _stopped(stop: threading.Event) -> bool:
    return stop.is_set()


def _sleep(stop: threading.Event, seconds: float) -> bool:
    """可被停止打断的等待；返回 True 表示被停止。"""
    return stop.wait(seconds)


def _urlencode(params: dict) -> str:
    return urllib.parse.urlencode(params, quote_via=urllib.parse.quote)


# ---------------------------------------------------------------- 协议步骤
def get_page_info(host: str, index_params: str) -> dict:
    url = f"http://{host}/eportal/InterFace.do?method=pageInfo&indexParams={index_params}"
    resp = net.http_request(url, timeout=8.0)
    info = {"encrypt": False, "exponent": "", "modulus": ""}
    if resp.error:
        return info
    body = resp.text("utf-8")
    if '"passwordEncrypt":"true"' in body or '"passwordEncrypt":true' in body:
        info["encrypt"] = True
    match = _RE_EXP.search(body)
    if match:
        info["exponent"] = match.group(1)
    match = _RE_MOD.search(body)
    if match:
        info["modulus"] = match.group(1)
    return info


def rsa_encrypt(exponent_hex: str, modulus_hex: str, plaintext: str) -> str:
    """PKCS#1 v1.5 填充 + 裸模幂，输出十进制字符串。失败时回退明文。"""
    try:
        exponent_bytes = binascii.unhexlify(exponent_hex)
        modulus_bytes = binascii.unhexlify(modulus_hex)
    except (binascii.Error, ValueError):
        return plaintext
    if not modulus_bytes:
        return plaintext

    data = plaintext.encode("utf-8")
    size = len(modulus_bytes)
    if len(data) > size - 11:
        return plaintext

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


def login(host: str, index_params: str, username: str, password: str) -> tuple[bool, str]:
    """提交登录，返回 (是否成功, 网页原始提示)。"""
    info = get_page_info(host, index_params)
    pwd = password
    if info["encrypt"] and info["exponent"] and info["modulus"]:
        pwd = rsa_encrypt(info["exponent"], info["modulus"], password)

    form = {
        "userId": username,
        "password": pwd,
        "service": "",
        "passwordEncrypt": "true" if info["encrypt"] else "false",
        "queryString": index_params,
    }
    payload = _urlencode(form).encode("utf-8")
    url = f"http://{host}/eportal/InterFace.do?method=login"
    resp = net.http_request(
        url,
        method="POST",
        body=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=8.0,
    )
    if resp.error:
        return False, "无法连接认证服务器"

    body = resp.text("utf-8")
    if '"result":"success"' in body:
        return True, ""

    message = ""
    match = _RE_MSG.search(body)
    if match:
        message = decode_unicode_escapes(match.group(1))
    if not message:
        message = "认证失败（服务器未返回原因）"
    return False, message


# ---------------------------------------------------------------- 完整流程
def try_authenticate(
    stop: threading.Event,
    logger,
    username: str,
    password: str,
) -> None:
    """成功返回 None；失败抛 AuthFailed；被停止抛 AuthStopped。"""
    host = net.portal_host()

    if _stopped(stop):
        raise AuthStopped()

    # 无感认证可能刚好恢复，先快速复检一次
    if net.check_online():
        return

    portal_url = net.find_portal_url(logger)
    if not portal_url:
        # 区分诊断：服务器能不能直连
        probe = net.http_request(f"http://{host}/eportal/InterFace.do?method=login", timeout=5.0)
        if not probe.error and probe.status:
            raise AuthFailed("认证服务器可达但未获取到认证参数（可能网络已恢复）")
        raise AuthFailed(f"无法连接认证服务器 {host}")

    if _stopped(stop):
        raise AuthStopped()

    index_params = ""
    if "?" in portal_url:
        index_params = portal_url.split("?", 1)[1]

    # 会话残留：eportal 认为已认证但没有参数 → 空参数强制刷新
    if not index_params and "redirectortosuccess" in portal_url:
        logger.log("eportal 显示已认证但网络不通，强制刷新认证会话")
        ok, message = login(host, "", username, password)
        if not ok:
            raise AuthFailed(message)
        _wait_online(stop, logger)
        return

    if not index_params:
        raise AuthFailed(f"认证页面参数解析失败（跳转URL: {portal_url[:60]}）")

    logger.log("已获取认证跳转URL: " + portal_url)
    logger.log("已获取认证参数: " + index_params)

    ok, message = login(host, index_params, username, password)
    if not ok:
        raise AuthFailed(message)

    _wait_online(stop, logger)


def _wait_online(stop: threading.Event, logger) -> None:
    deadline = time.monotonic() + WAIT_PROBE_MAX
    while time.monotonic() < deadline:
        if _sleep(stop, WAIT_PROBE_GAP):
            raise AuthStopped()
        if net.check_online():
            return
    raise AuthFailed(f"认证已提交，但等待 {WAIT_PROBE_MAX} 秒后网络仍未连通")
