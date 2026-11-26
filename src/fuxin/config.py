"""配置读写。

文件格式与旧版一致，便于直接兼容历史 fuxin_config.dat::

    user=<base64(AES-GCM 密文)>
    pass=<base64(AES-GCM 密文)>
    freq=600

密钥 = SHA256("fuxin-xiaowangtong|" + salt)，user/pass 使用不同 salt；
AES-GCM，12 字节随机 nonce 拼在密文前面，整体 Base64。
定位是"防随手查看"，不是强加密。
"""

from __future__ import annotations

import base64
import hashlib
import os
import threading
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import paths

_KEY_PREFIX = "fuxin-xiaowangtong|"
_SALT_USER = "user"
_SALT_PASS = "pass"
_NONCE_LEN = 12


def derive_key(salt: str) -> bytes:
    return hashlib.sha256((_KEY_PREFIX + salt).encode("utf-8")).digest()


def encrypt_text(plain: str, salt: str) -> str:
    nonce = os.urandom(_NONCE_LEN)
    cipher = AESGCM(derive_key(salt))
    blob = nonce + cipher.encrypt(nonce, plain.encode("utf-8"), None)
    return base64.b64encode(blob).decode("ascii")


def decrypt_text(token: str, salt: str) -> str:
    blob = base64.b64decode(token, validate=False)
    if len(blob) <= _NONCE_LEN:
        raise ValueError("配置密文过短")
    nonce, body = blob[:_NONCE_LEN], blob[_NONCE_LEN:]
    cipher = AESGCM(derive_key(salt))
    return cipher.decrypt(nonce, body, None).decode("utf-8")


class Config:
    """内存中的配置；pass 明文只存在内存里，落盘前加密。"""

    def __init__(self, logger) -> None:
        self._logger = logger
        self._lock = threading.RLock()
        self.user = ""
        self.password = ""
        self.freq = paths.DEFAULT_FREQ
        self.autostart_scope = "user"
        self.load_error = ""
        self.load()

    # ---------- 读写 ----------
    def load(self) -> None:
        path = paths.config_path()
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                raw = fh.read()
        except FileNotFoundError:
            return
        except OSError as exc:
            self.load_error = f"配置文件读取失败：{exc}"
            self._logger.log(self.load_error)
            return

        for line in raw.replace("\r\n", "\n").split("\n"):
            line = line.strip()
            if not line or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip().lower()
            try:
                if key == "user":
                    self.user = decrypt_text(value, _SALT_USER)
                elif key == "pass":
                    self.password = decrypt_text(value, _SALT_PASS)
                elif key == "autostart_scope":
                    scope = value.strip().lower()
                    self.autostart_scope = scope if scope in ("user", "system") else "user"
                elif key == "freq":
                    number = int(value.strip())
                    if number < paths.MIN_FREQ:
                        number = paths.MIN_FREQ
                        self._logger.log(
                            f"检测频率低于下限，已调整为 {paths.MIN_FREQ} 秒/次"
                        )
                    self.freq = number
            except Exception as exc:  # 单行损坏不影响其它字段
                self.load_error = f"配置项 {key} 解析失败：{exc}"
                self._logger.log(self.load_error)

    def save(self) -> None:
        with self._lock:
            lines: list[str] = []
            if self.user:
                lines.append("user=" + encrypt_text(self.user, _SALT_USER))
            if self.password:
                lines.append("pass=" + encrypt_text(self.password, _SALT_PASS))
            lines.append(f"freq={self.freq}")
            lines.append(f"autostart_scope={self.autostart_scope}")
            payload = "\n".join(lines) + "\n"
            target = paths.config_path()
            tmp = target + ".tmp"
            try:
                with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(payload)
                os.replace(tmp, target)
            except OSError as exc:
                self._logger.log(f"配置保存失败：{exc}")
                raise

    # ---------- 便捷访问 ----------
    def has_credentials(self) -> bool:
        with self._lock:
            return bool(self.user) and bool(self.password)

    def credentials(self) -> tuple[str, str]:
        with self._lock:
            return self.user, self.password

    def set_user(self, user: str) -> None:
        with self._lock:
            self.user = user

    def set_password(self, password: str) -> None:
        with self._lock:
            self.password = password

    def set_freq(self, freq: int) -> None:
        with self._lock:
            self.freq = max(paths.MIN_FREQ, int(freq))

    def freq_value(self) -> int:
        with self._lock:
            return self.freq
