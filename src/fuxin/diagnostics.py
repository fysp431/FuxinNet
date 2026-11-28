"""认证失败诊断文件。

设计取舍：
- 只在认证**失败**时落盘，正常成功不写，避免长期运行撑大磁盘；
- 内容与主日志分开保存，主日志仍只保留摘要，排障时再取现场；
- 落盘前统一经过 :func:`fuxin.logger.mask_sensitive` 打码，mac / wlanuserip /
  password / token 等敏感字段不会明文出现；
- 单份上限 256 KB，超出截断并注明原因。
"""

from __future__ import annotations

import os
import time

from .logger import mask_sensitive

MAX_DUMP_BYTES = 256 * 1024
_MAX_SECTION_CHARS = 64 * 1024


class AuthDiagnostic:
    """收集一次认证过程的关键原始响应，失败时一次性落盘。"""

    def __init__(self, logger, reason: str = "") -> None:
        self.logger = logger
        self.reason = reason
        self.sections: list[tuple[str, str]] = []
        self.urls: list[str] = []

    # ---------- 收集 ----------
    def add(self, title: str, body: object) -> None:
        if body is None:
            return
        text = str(body)
        if len(text) > _MAX_SECTION_CHARS:
            text = text[:_MAX_SECTION_CHARS] + "\n…（本节内容超长，已截断）"
        self.sections.append((str(title), text))

    def add_url(self, url: str) -> None:
        if url and url not in self.urls:
            self.urls.append(url)

    # ---------- 落盘 ----------
    def _render(self) -> str:
        lines = [
            "福信校网通 认证失败诊断",
            f"生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"失败原因：{self.reason or '未提供'}",
            "",
            "==== 跳转链 URL ====",
        ]
        if self.urls:
            lines.extend(f"{index}. {url}" for index, url in enumerate(self.urls, 1))
        else:
            lines.append("(未记录到跳转 URL)")
        for title, body in self.sections:
            lines.extend(["", f"==== {title} ====", body])
        lines.append("")
        return "\n".join(lines)

    def _directory(self) -> str:
        """与主日志同目录；logger 未绑定文件路径时返回空串（工具/测试场景）。"""
        path = getattr(self.logger, "path", "") or ""
        return os.path.dirname(os.path.abspath(path)) if path else ""

    def save(self) -> str:
        """写入诊断文件，返回路径；失败返回空串且不影响认证主流程。"""
        directory = self._directory()
        if not directory:
            return ""
        content = mask_sensitive(self._render())
        encoded = content.encode("utf-8", "replace")
        if len(encoded) > MAX_DUMP_BYTES:
            trailer = "\n…（诊断内容超过 256 KB，已截断）\n"
            keep = max(0, MAX_DUMP_BYTES - len(trailer.encode("utf-8")))
            content = encoded[:keep].decode("utf-8", "ignore") + trailer
        name = f"fuxin_dump_{time.strftime('%Y%m%d-%H%M%S')}.txt"
        target = os.path.join(directory, name)
        if os.path.exists(target):
            target = os.path.join(
                directory,
                f"fuxin_dump_{time.strftime('%Y%m%d-%H%M%S')}_{os.getpid()}.txt",
            )
        try:
            with open(target, "w", encoding="utf-8", newline="") as fh:
                fh.write(content)
        except OSError as exc:
            self.logger.log(f"[诊断] 认证失败现场写入失败：{exc}")
            return ""
        return target
