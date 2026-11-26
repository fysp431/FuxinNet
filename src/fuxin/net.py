"""网络层：连通性探测 + 门户跳转链解析。

两个关键点（历史 bug 的经验，必须保持）：
1. 完全不走系统代理。这里用 ``http.client`` 直连，天然绕过 IE/WinINET 代理设置，
   避免"断网了但代理还能访问探测域名"导致误判在线。
2. 认证页参数在 302 链 + 200 HTML(JS/meta 跳转) 里，必须手工跟随，最多 6 跳。
"""

from __future__ import annotations

import http.client
import os
import re
import ssl
import urllib.parse
from dataclasses import dataclass, field
from typing import Optional

DEFAULT_PORTAL_HOST = "172.16.9.11"
MAX_HOPS = 6
PROBE_BODY_LIMIT = 512
HTML_LIMIT = 128 * 1024

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

_RE_JS_JUMP = re.compile(
    r"""(?:location(?:\.href)?|window\.location|top\.location)\s*=\s*['"(]([^'")\s]+)"""
)
_RE_META_JUMP = re.compile(r"""(?i)http-equiv=["']?refresh["']?[^>]*url=([^'"\s>]+)""")


def portal_host() -> str:
    """认证服务器地址（允许通过环境变量覆盖，方便本地自测）。"""
    return os.environ.get("FUXIN_PORTAL_HOST", DEFAULT_PORTAL_HOST)


@dataclass
class Response:
    status: int = 0
    headers: dict = field(default_factory=dict)
    body: bytes = b""
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.status >= 200 and self.status < 400

    @property
    def location(self) -> str:
        return self.headers.get("location", "")

    def text(self, encoding: str = "utf-8") -> str:
        return self.body.decode(encoding, "replace")


def http_request(
    url: str,
    method: str = "GET",
    body: Optional[bytes] = None,
    headers: Optional[dict] = None,
    timeout: float = 5.0,
    max_bytes: int = HTML_LIMIT,
) -> Response:
    """不跟随重定向、不使用代理的单次请求。"""
    parts = urllib.parse.urlsplit(url)
    if not parts.scheme or not parts.hostname:
        return Response(error="URL 无效")
    scheme = parts.scheme.lower()
    host = parts.hostname
    port = parts.port
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query

    request_headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Connection": "close",
    }
    if headers:
        request_headers.update(headers)

    kwargs = {"timeout": timeout}
    if scheme == "https":
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        conn = http.client.HTTPSConnection(host, port, context=context, **kwargs)
    else:
        conn = http.client.HTTPConnection(host, port, **kwargs)

    try:
        conn.request(method, path, body=body, headers=request_headers)
        raw = conn.getresponse()
        data = b"" if method == "HEAD" else raw.read(max_bytes)
        head = {k.lower(): v for k, v in raw.getheaders()}
        return Response(status=raw.status, headers=head, body=data)
    except Exception as exc:  # 网络异常统一降级为 error 字段
        return Response(error=f"{type(exc).__name__}: {exc}")
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------- 连通性探测
def _probe(url: str, want_status: int, want_body: str = "") -> bool:
    resp = http_request(url, timeout=5.0, max_bytes=PROBE_BODY_LIMIT)
    if resp.error or resp.status == 0:
        return False
    location = resp.location
    if location and (portal_host() in location or "eportal" in location.lower()):
        return False  # 被重定向到认证页 = 需要认证
    if resp.status != want_status:
        return False
    if want_body:
        if want_body not in resp.text("utf-8"):
            return False
    return True


def check_online() -> bool:
    """三源探测，任一口径通过才算在线；全失败才判离线。"""
    if _probe("http://generate_204", 204):
        return True
    if _probe(
        "http://www.msftconnecttest.com/connecttest.txt",
        200,
        "Microsoft Connect Test",
    ):
        return True
    if _probe("http://captive.apple.com", 204):
        return True
    return False


# ---------------------------------------------------------------- 跳转链解析
def url_host(url: str) -> str:
    if "//" in url:
        rest = url.split("//", 1)[1]
        return rest.split("/", 1)[0].split("?", 1)[0]
    return portal_host()


def resolve_url(url: str, base: str) -> str:
    url = url.strip().strip("'\"")
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if url.startswith("/"):
        return f"http://{url_host(base)}{url}"
    if url.startswith("//"):
        return "http:" + url
    idx = base.rfind("/")
    if idx > 7:
        return base[: idx + 1] + url
    return f"http://{url_host(base)}/{url}"


def is_auth_page(url: str) -> bool:
    lowered = url.lower()
    if "index.jsp" not in lowered and "eportal" not in lowered:
        return False
    if "?" not in url:
        return False
    query = url.split("?", 1)[1]
    return len(query) > 8


def extract_jump(html: str, base: str) -> str:
    match = _RE_JS_JUMP.search(html)
    if match:
        return resolve_url(match.group(1), base)
    match = _RE_META_JUMP.search(html)
    if match:
        return resolve_url(match.group(1), base)
    return ""


def follow_chain(url: str, logger, depth: int = 0) -> str:
    """跟随 302 链与 200 页面的 JS/meta 跳转，返回认证页 URL（找不到返回空串）。"""
    if depth > MAX_HOPS or not url:
        return ""
    resp = http_request(url, timeout=5.0, max_bytes=HTML_LIMIT)
    if resp.error or resp.status == 0:
        logger.log(f"[诊断] 跳{depth} err: {str(resp.error)[:80]}")
        return ""

    if resp.status == 200:
        html = resp.text("utf-8")
        logger.log(f"[诊断] 跳{depth} {url[:60]} → 200 长度{len(html)}")
        logger.log("[诊断] 200页内容前300字符: " + html[:300])
        target = extract_jump(html, url)
        if not target:
            return ""
        logger.log("[诊断] HTML跳转 → " + target)
        if is_auth_page(target):
            return target
        return follow_chain(target, logger, depth + 1)

    location = resp.location
    logger.log(f"[诊断] 跳{depth} {url[:60]} → {resp.status} {location[:120]}")
    if not location or resp.status < 300 or resp.status >= 400:
        return ""
    if location.startswith("/"):
        location = f"http://{url_host(url)}{location}"
    if not location.startswith("http"):
        return ""
    if is_auth_page(location):
        return location
    return follow_chain(location, logger, depth + 1)


def find_portal_url(logger) -> str:
    """依次尝试三个入口，返回带参数的认证页 URL。"""
    host = portal_host()
    entries = [
        "http://123.123.123.123",
        f"http://{host}/",
        "http://www.msftconnecttest.com/redirect",
    ]
    for entry in entries:
        found = follow_chain(entry, logger, 0)
        if found:
            return found
    return ""
