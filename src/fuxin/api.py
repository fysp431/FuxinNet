"""前端与后端之间的桥（pywebview js_api）。

所有方法都返回可 JSON 序列化的 dict；前端用轮询 poll() 拉取状态与新增日志，
避免跨线程调用 evaluate_js 带来的线程安全问题。
"""

from __future__ import annotations

import os
import time

from . import autostart, paths

# 学号/密码白名单：ASCII 可打印字符（0x21-0x7E），排除中文、空格与控制字符
_CREDENTIAL_MIN = 0x21
_CREDENTIAL_MAX = 0x7E
_SAVE_FAILED = "保存失败：数据目录不可写，本次修改未生效"


def _invalid_credential(value: str) -> bool:
    return any(
        not (_CREDENTIAL_MIN <= ord(char) <= _CREDENTIAL_MAX) for char in value
    )


class Api:
    def __init__(self, config, logger, monitor) -> None:
        self.config = config
        self.logger = logger
        self.monitor = monitor
        self.tray = None

    # ------------------------------------------------------------ 基础信息
    def bootstrap(self) -> dict:
        return {
            "version": paths.APP_VERSION,
            "subtitle": paths.APP_SUBTITLE,
            "author": paths.AUTHOR,
            "email": paths.AUTHOR_EMAIL,
            "appDir": paths.data_dir(),
            "logPath": paths.log_path(),
            "cursor": self.logger.cursor(),
            "entries": self.logger.snapshot(200),
            "config": self._config_view(),
            "state": self.monitor.snapshot(),
            "loadError": self.config.load_error,
            "autostart": autostart.is_enabled(self.config.autostart_scope),
            "autostartScope": self.config.autostart_scope,
            "autostartSystemSupported": autostart.is_supported(autostart.SCOPE_SYSTEM),
        }

    def _config_view(self) -> dict:
        return {
            "user": self.config.user,
            "hasPass": bool(self.config.password),
            "freq": self.config.freq_value(),
        }

    def _reply(self, ok: bool, message: str = "") -> dict:
        return {
            "ok": ok,
            "message": message,
            "config": self._config_view(),
            "state": self.monitor.snapshot(),
            "cursor": self.logger.cursor(),
        }

    # ------------------------------------------------------------ 轮询
    def poll(self, cursor: int) -> dict:
        try:
            cursor = int(cursor)
        except (TypeError, ValueError):
            cursor = 0
        entries = self.logger.entries_since(cursor)
        return {
            "cursor": self.logger.cursor(),
            "entries": entries,
            "state": self.monitor.snapshot(),
            "config": self._config_view(),
        }

    # ------------------------------------------------------------ 配置
    def _persist(self) -> bool:
        """保存配置；磁盘异常统一转成可读文案，不再把 OSError 抛给 JS 层。"""
        try:
            self.config.save()
        except OSError as exc:
            self.logger.log(f"保存配置失败：{exc}")
            return False
        return True

    def save_user(self, value: str) -> dict:
        if self.monitor.running or self.monitor.stopping:
            return self._reply(False, "正在运行，账号与频率已锁定")
        value = (value or "").strip()
        if not value:
            return self._reply(False, "学号不能为空")
        if _invalid_credential(value):
            return self._reply(
                False, "学号只能包含英文、数字和英文特殊字符（不能含中文或空格）"
            )
        if value == self.config.user:
            return self._reply(False, "学号未发生变化")

        previous = self.config.user
        self.config.set_user(value)
        if not self._persist():
            self.config.set_user(previous)
            return self._reply(False, _SAVE_FAILED)

        message = "学号已保存"
        self.logger.log(message)
        self.monitor.set_message(message)
        return self._reply(True, message)

    def save_pass(self, value: str) -> dict:
        if self.monitor.running or self.monitor.stopping:
            return self._reply(False, "正在运行，账号与频率已锁定")
        value = value or ""
        if not value:
            return self._reply(False, "密码不能为空")
        if _invalid_credential(value):
            return self._reply(
                False, "密码只能包含英文、数字和英文特殊字符（不能含中文或空格）"
            )
        if value == self.config.password:
            return self._reply(False, "密码未发生变化")

        previous = self.config.password
        self.config.set_password(value)
        if not self._persist():
            self.config.set_password(previous)
            return self._reply(False, _SAVE_FAILED)

        message = "密码已保存"
        self.logger.log(message)
        self.monitor.set_message(message)
        return self._reply(True, message)

    def save_freq(self, value) -> dict:
        if self.monitor.running or self.monitor.stopping:
            return self._reply(False, "正在运行，账号与频率已锁定")
        try:
            freq = int(str(value).strip())
        except (TypeError, ValueError):
            return self._reply(False, f"检测频率须为不小于 {paths.MIN_FREQ} 的整数")
        if freq < paths.MIN_FREQ:
            return self._reply(False, f"检测频率须为不小于 {paths.MIN_FREQ} 的整数")
        if freq == self.config.freq_value():
            return self._reply(False, "检测频率未发生变化")

        previous = self.config.freq_value()
        self.config.set_freq(freq)
        if not self._persist():
            self.config.set_freq(previous)
            return self._reply(False, _SAVE_FAILED)

        message = f"检测频率已调整为 {freq} 秒/次"
        self.logger.log(message)
        self.monitor.set_message(message)
        return self._reply(True, message)

    # ------------------------------------------------------------ 启停
    def start(self) -> dict:
        ok, message = self.monitor.start()
        return self._reply(ok, message)

    def stop(self) -> dict:
        state = self.monitor.snapshot()
        if not state.get("running") and not state.get("stopping"):
            return self._reply(False, "当前未在运行")
        self.monitor.stop(by_user=True)
        state = self.monitor.snapshot()
        if state.get("stopping"):
            return self._reply(True, "正在停止…")
        return self._reply(True, "已停止检测")

    # ------------------------------------------------------------ 日志
    def export_log(self) -> dict:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dest = os.path.join(paths.data_dir(), f"fuxin_log_export_{stamp}.txt")
        try:
            self.logger.export(dest)
        except OSError as exc:
            return self._reply(False, f"导出失败：{exc}")
        return self._reply(True, dest)

    def open_log_dir(self) -> dict:
        try:
            os.startfile(paths.data_dir())  # noqa: S606 - 只在用户点击时调用
        except Exception as exc:
            return self._reply(False, f"无法打开目录：{exc}")
        return self._reply(True, paths.data_dir())

    # ------------------------------------------------------------ 开机自启
    def _autostart_reply(self, ok: bool, message: str) -> dict:
        reply = self._reply(ok, message)
        scope = self.config.autostart_scope
        reply["autostart"] = autostart.is_enabled(scope)
        reply["autostartScope"] = scope
        reply["autostartSystemSupported"] = autostart.is_supported(autostart.SCOPE_SYSTEM)
        return reply

    def set_autostart_scope(self, scope: str) -> dict:
        """切换自启作用域（记住选择，不改变开关状态）。"""
        scope = autostart.normalize_scope(scope)
        previous = self.config.autostart_scope
        self.config.autostart_scope = scope
        if not self._persist():
            self.config.autostart_scope = previous
            return self._autostart_reply(False, _SAVE_FAILED)
        label = "用户级" if scope == autostart.SCOPE_USER else "系统级"
        self.logger.log(f"开机自启作用域切换为：{label}")
        if scope == autostart.SCOPE_SYSTEM:
            return self._autostart_reply(True, "系统级自启开发中，暂不可用")
        return self._autostart_reply(True, f"开机自启作用域：{label}")

    def set_autostart(self, enabled: bool, scope: str = "") -> dict:
        """开启/关闭自启；scope 为空时用上次记住的作用域。"""
        target = autostart.normalize_scope(scope or self.config.autostart_scope)
        previous = self.config.autostart_scope
        self.config.autostart_scope = target
        # 作用域落盘失败时不要动注册表：否则界面提示失败但自启已被改掉
        if not self._persist():
            self.config.autostart_scope = previous
            return self._autostart_reply(False, _SAVE_FAILED)

        label = "用户级" if target == autostart.SCOPE_USER else "系统级"
        if not autostart.is_supported(target):
            self.logger.log(f"{label}开机自启暂未实现，已忽略开启请求")
            return self._autostart_reply(False, "系统级自启开发中，敬请期待")

        if enabled:
            ok, detail = autostart.enable(target)
            if ok:
                self.logger.log(f"已启用{label}开机自启：" + detail)
            else:
                self.logger.log(f"启用{label}开机自启失败：" + detail)
                return self._autostart_reply(False, detail)
        else:
            ok, detail = autostart.disable(target)
            if not ok:
                self.logger.log(f"关闭{label}开机自启失败：" + detail)
                return self._autostart_reply(False, detail)
            self.logger.log(f"已关闭{label}开机自启")

        return self._autostart_reply(True, "开机自启已开启" if enabled else "开机自启已关闭")
