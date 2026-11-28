"""测试用的轻量替身，避免触碰真实配置/日志文件。"""

from __future__ import annotations


class FakeConfig:
    def __init__(self, user: str = "", password: str = "", freq: int = 1) -> None:
        self.user = user
        self.password = password
        self.freq = freq
        self.load_error = ""
        self.autostart_scope = "user"
        self.auto_run = False
        self.saved: list[str] = []
        self.save_error: str | None = None

    # ---------- 读取 ----------
    def credentials(self) -> tuple[str, str]:
        return self.user, self.password

    def has_credentials(self) -> bool:
        return bool(self.user) and bool(self.password)

    def freq_value(self) -> int:
        return self.freq

    def auto_run_value(self) -> bool:
        return self.auto_run

    # ---------- 写入 ----------
    def set_user(self, value: str) -> None:
        self.user = value
        self.saved.append("user")

    def set_password(self, value: str) -> None:
        self.password = value
        self.saved.append("password")

    def set_freq(self, value: int) -> None:
        self.freq = value
        self.saved.append(f"freq={value}")

    def set_auto_run(self, value: bool) -> None:
        self.auto_run = bool(value)
        self.saved.append("auto_run")

    def save(self) -> None:
        if self.save_error:
            raise OSError(self.save_error)
        self.saved.append("save")


class CrashConfig(FakeConfig):
    """第二次读取频率时崩溃，用于验证工作线程顶层异常兜底。"""

    def __init__(self) -> None:
        super().__init__(user="2025015413", password="pw")
        self.calls = 0

    def freq_value(self) -> int:
        self.calls += 1
        if self.calls >= 2:
            raise RuntimeError("config boom")
        return 1


class FakeLogger:
    """只记录到内存；没有 path，诊断文件写入会被安全跳过。"""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.entries: list[dict] = []

    def log(self, message: object, limit: int = 800) -> None:
        self.lines.append(str(message))

    def log_block(self, message: object, limit: int = 4000) -> None:
        self.lines.append(str(message))

    def cursor(self) -> int:
        return len(self.entries)

    def snapshot(self, limit: int = 500) -> list[dict]:
        return list(self.entries[-limit:])

    def entries_since(self, cursor: int, limit: int = 300) -> list[dict]:
        return [entry for entry in self.entries if entry["seq"] > cursor][:limit]
