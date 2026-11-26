"""运行日志。

约定（沿用旧版行为，便于与历史日志对照）：
- 文件名 fuxin_log.txt，位于程序同目录
- UTF-8 无 BOM，行尾 CRLF，格式 ``[yyyy-MM-dd HH:mm:ss] 内容``
- 追加写文件；同时在内存保留最近若干条供界面展示（界面只读内存，不重复读盘）
- 写入前统一脱敏：mac/wlanuserip/snmpagentip/nasportid 只保留前 4 位
"""

from __future__ import annotations

import os
import re
import threading
import time

_MASK_RE = re.compile(r"(?i)(mac|wlanuserip|snmpagentip|nasportid)=([0-9a-f]{8,})")


def mask_sensitive(text: str) -> str:
    """把 mac=001122334455 这类值截成 0011****。"""

    def _sub(match: re.Match) -> str:
        key, value = match.group(1), match.group(2)
        if len(value) > 4:
            value = value[:4] + "****"
        return f"{key}={value}"

    return _MASK_RE.sub(_sub, text)


def sanitize(value: object, limit: int = 800) -> str:
    """日志落盘前的清理：去换行、脱敏、截断。"""
    text = "" if value is None else str(value)
    text = text.replace("\r", " ").replace("\n", " ")
    text = mask_sensitive(text)
    if len(text) > limit:
        text = text[:limit] + "…"
    return text


class Logger:
    """线程安全的日志器。"""

    def __init__(self, path: str, max_entries: int = 1000) -> None:
        self.path = path
        self.max_entries = max_entries
        self._entries: list[dict] = []
        self._seq = 0
        self._lock = threading.Lock()
        self._file_error_logged = False

    # ---------- 写入 ----------
    def log(self, message: object, limit: int = 800) -> None:
        text = sanitize(message, limit=limit)
        now = time.localtime()
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", now)
        short = time.strftime("%H:%M:%S", now)
        line = f"[{stamp}] {text}"

        with self._lock:
            self._seq += 1
            self._entries.append({"seq": self._seq, "time": short, "text": text})
            if len(self._entries) > self.max_entries:
                del self._entries[: len(self._entries) - self.max_entries]

        try:
            with open(self.path, "a", encoding="utf-8", newline="") as fh:
                fh.write(line + "\r\n")
        except OSError as exc:  # 磁盘不可写时不能让主流程崩掉
            if not self._file_error_logged:
                self._file_error_logged = True
                with self._lock:
                    self._seq += 1
                    self._entries.append(
                        {
                            "seq": self._seq,
                            "time": short,
                            "text": f"日志文件写入失败：{exc}",
                        }
                    )

    # ---------- 读取 ----------
    def entries_since(self, cursor: int, limit: int = 300) -> list[dict]:
        with self._lock:
            return [e for e in self._entries if e["seq"] > cursor][:limit]

    def cursor(self) -> int:
        with self._lock:
            return self._seq

    def snapshot(self, limit: int = 500) -> list[dict]:
        with self._lock:
            return list(self._entries[-limit:])

    # ---------- 导出 ----------
    def export(self, dest_path: str) -> str:
        """导出内存中的完整日志到指定文件（UTF-8 + CRLF）。"""
        rows = self.snapshot(limit=self.max_entries)
        with open(dest_path, "w", encoding="utf-8", newline="") as fh:
            for row in rows:
                fh.write(f"{row['time']}  {row['text']}\r\n")
        return dest_path

    def log_exists(self) -> bool:
        try:
            return os.path.getsize(self.path) > 0
        except OSError:
            return False
