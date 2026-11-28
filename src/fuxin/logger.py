"""运行日志。

约定（沿用旧版行为，便于与历史日志对照）：
- 文件名 fuxin_log.txt，位于程序同目录
- UTF-8 无 BOM，行尾 CRLF，格式 ``[yyyy-MM-dd HH:mm:ss] 内容``
- 追加写文件；同时在内存保留最近若干条供界面展示（界面只读内存，不重复读盘）
- 写入前统一脱敏：mac / wlanuserip / nasportid 等只保留前 4 位；
  密码、token、ticket 类字段整体打码，绝不明文落盘
- 文件超过 2 MB 自动轮转，保留 3 份历史（.1/.2/.3），长期运行不会无限增长
"""

from __future__ import annotations

import os
import re
import threading
import time

# 网络标识字段：支持 mac / macAddress / macaddr
_FIELD_NAMES = (
    r"mac(?:address|addr)?|wlanuserip|snmpagentip|nasportid|nasip|wlanacip|userip|ipv6|apmac"
)
_SECRET_NAMES = r"password|passwd|pass|pwd|token|ticket"

# key=value / key%3Dvalue 形式（前边界避免 compass=abc 误伤）
_KV_FIELD_RE = re.compile(
    rf"(?i)(?<![A-Za-z])({_FIELD_NAMES})(=|%3d)([0-9a-f.:\-]{{4,}})"
)
_KV_SECRET_RE = re.compile(
    rf"(?i)(?<![A-Za-z])({_SECRET_NAMES})(?![A-Za-z])(=|%3d)([^&\s'\"]+)"
)

# JSON 形式：{"mac":"0011...", "password":"..."}
_JSON_FIELD_RE = re.compile(
    rf'(?i)(")({_FIELD_NAMES})\1(\s*:\s*")([0-9a-f.:\-]{{4,}})(")'
)
_JSON_SECRET_RE = re.compile(
    rf'(?i)(")({_SECRET_NAMES})\1(\s*:\s*")([^"]*)(")'
)

MAX_FILE_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 3
_STAT_EVERY = 50


def _mask_field(match: re.Match) -> str:
    key, sep, value = match.group(1), match.group(2), match.group(3)
    if len(value) <= 4:
        return match.group(0)
    return f"{key}{sep}{value[:4]}****"


def _mask_json_field(match: re.Match) -> str:
    value = match.group(4)
    if len(value) <= 4:
        return match.group(0)
    return (
        f'{match.group(1)}{match.group(2)}"'
        f"{match.group(3)}{value[:4]}****{match.group(5)}"
    )


def _mask_json_secret(match: re.Match) -> str:
    return f'{match.group(1)}{match.group(2)}"{match.group(3)}****{match.group(5)}'


def mask_sensitive(text: str) -> str:
    """把 mac=...、wlanuserip=...、JSON 里的 mac/password 等打码。"""
    text = _JSON_SECRET_RE.sub(_mask_json_secret, text)
    text = _KV_SECRET_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}****", text)
    text = _JSON_FIELD_RE.sub(_mask_json_field, text)
    return _KV_FIELD_RE.sub(_mask_field, text)


def sanitize(value: object, limit: int = 800) -> str:
    """日志落盘前的清理：去换行、脱敏、截断。"""
    text = "" if value is None else str(value)
    for old in ("\r\n", "\r", "\n", "\t"):
        text = text.replace(old, " ")
    text = mask_sensitive(text)
    if len(text) > limit:
        text = text[:limit] + "…"
    return text


class Logger:
    """线程安全的日志器（文件 + 内存环形缓冲）。"""

    def __init__(self, path: str, max_entries: int = 1000) -> None:
        self.path = path
        self.max_entries = max_entries
        self._entries: list[dict] = []
        self._seq = 0
        self._lock = threading.Lock()
        self._file_error_logged = False
        self._writes = 0

    # ---------- 写入 ----------
    def _rotate_if_needed(self) -> None:
        """按大小轮转：fuxin_log.txt -> .1 -> .2 -> .3（最旧的丢弃）。"""
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return
        if size < MAX_FILE_BYTES:
            return
        for index in range(BACKUP_COUNT, 0, -1):
            src = self.path if index == 1 else f"{self.path}.{index - 1}"
            dst = f"{self.path}.{index}"
            try:
                if index == BACKUP_COUNT and os.path.exists(dst):
                    os.remove(dst)
                if os.path.exists(src):
                    os.replace(src, dst)
            except OSError:
                # 轮转失败不能影响正常写日志
                pass

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

            self._writes += 1
            if self._writes % _STAT_EVERY == 1:
                self._rotate_if_needed()

            try:
                with open(self.path, "a", encoding="utf-8", newline="") as fh:
                    fh.write(line + "\r\n")
            except OSError as exc:  # 磁盘不可写时不能让主流程崩掉
                if not self._file_error_logged:
                    self._file_error_logged = True
                    self._seq += 1
                    self._entries.append(
                        {
                            "seq": self._seq,
                            "time": short,
                            "text": f"日志文件写入失败：{exc}",
                        }
                    )

    def log_block(self, message: object, limit: int = 4000) -> None:
        """多行日志（异常堆栈等）：逐行落盘，避免被换行清理压成一行。"""
        text = "" if message is None else str(message)
        for line in text.splitlines() or [""]:
            self.log(line, limit=limit)

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
        """导出落盘日志（含 .1~.3 轮转历史），UTF-8 + 原样行尾。

        直接拼接文件而不是内存快照：落盘行本身带 ``[yyyy-MM-dd HH:mm:ss]``
        完整时间戳，跨天导出不会丢失日期，也能导出内存缓冲之外的更早记录。
        日志文件尚未产生（例如只读目录且回退失败）时退回内存快照。
        """
        parts: list[str] = []
        for name in (f"{self.path}.3", f"{self.path}.2", f"{self.path}.1", self.path):
            try:
                with open(
                    name, encoding="utf-8", errors="replace", newline=""
                ) as fh:
                    parts.append(fh.read())
            except FileNotFoundError:
                continue
        if not parts:
            rows = self.snapshot(limit=self.max_entries)
            parts = [f"[{row['time']}] {row['text']}\r\n" for row in rows]
        with open(dest_path, "w", encoding="utf-8", newline="") as fh:
            fh.write("".join(parts))
        return dest_path
