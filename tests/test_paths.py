"""运行期数据目录：可写性探测、回退与旧配置迁移。"""

from __future__ import annotations

import os

from fuxin import paths


def _reset(monkeypatch, app_dir: str, writable) -> None:
    monkeypatch.setattr(paths, "app_dir", lambda: app_dir)
    monkeypatch.setattr(paths, "_is_writable", writable)
    monkeypatch.setattr(paths, "_data_dir", None)
    monkeypatch.setattr(paths, "_data_dir_note", "")


def test_uses_app_dir_when_writable(monkeypatch, tmp_path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    _reset(monkeypatch, str(app), lambda directory: True)

    assert paths.data_dir() == str(app)
    assert "程序目录" in paths.data_dir_note()
    assert paths.config_path() == str(app / paths.CONFIG_NAME)
    assert paths.log_path() == str(app / paths.LOG_NAME)
    assert paths.running_marker_path() == str(app / paths.RUNNING_MARKER_NAME)


def test_falls_back_and_migrates_legacy_config(monkeypatch, tmp_path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    local = tmp_path / "local"
    (app / paths.CONFIG_NAME).write_text("freq=600\n", encoding="utf-8")

    def writable(directory: str) -> bool:
        return os.path.normcase(str(directory)) != os.path.normcase(str(app))

    _reset(monkeypatch, str(app), writable)
    monkeypatch.setattr(paths, "_user_data_dir", lambda: str(local))

    assert paths.data_dir() == str(local)
    note = paths.data_dir_note()
    assert "回退" in note
    assert "迁移" in note
    assert (local / paths.CONFIG_NAME).read_text(encoding="utf-8") == "freq=600\n"

    # 不覆盖新位置已有配置
    (local / paths.CONFIG_NAME).write_text("freq=120\n", encoding="utf-8")
    monkeypatch.setattr(paths, "_data_dir", None)
    monkeypatch.setattr(paths, "_data_dir_note", "")
    assert paths.data_dir() == str(local)
    assert (local / paths.CONFIG_NAME).read_text(encoding="utf-8") == "freq=120\n"


def test_migrates_back_when_app_dir_becomes_writable(monkeypatch, tmp_path) -> None:
    """曾在受控目录用过（配置在用户目录），后来 exe 挪到可写目录 → 配置迁回。"""
    app = tmp_path / "app"
    app.mkdir()
    local = tmp_path / "local"
    local.mkdir()
    (local / paths.CONFIG_NAME).write_text("freq=300\n", encoding="utf-8")
    (local / paths.FALLBACK_MARKER_NAME).write_text("2026-01-01 00:00:00\n", encoding="utf-8")

    _reset(monkeypatch, str(app), lambda directory: True)
    monkeypatch.setattr(paths, "_user_data_dir", lambda: str(local))

    assert paths.data_dir() == str(app)
    assert "迁回" in paths.data_dir_note()
    assert (app / paths.CONFIG_NAME).read_text(encoding="utf-8") == "freq=300\n"
    # 迁移完成后标记应清除，避免下次又搬一遍
    assert not (local / paths.FALLBACK_MARKER_NAME).exists()


def test_no_back_migration_without_fallback_marker(monkeypatch, tmp_path) -> None:
    """用户主动删掉程序目录配置想重置时，不能被用户目录的旧副本"复活"。"""
    app = tmp_path / "app"
    app.mkdir()
    local = tmp_path / "local"
    local.mkdir()
    (local / paths.CONFIG_NAME).write_text("freq=300\n", encoding="utf-8")
    # 没有回退标记

    _reset(monkeypatch, str(app), lambda directory: True)
    monkeypatch.setattr(paths, "_user_data_dir", lambda: str(local))

    assert paths.data_dir() == str(app)
    assert not (app / paths.CONFIG_NAME).exists()
    assert "迁回" not in paths.data_dir_note()


def test_back_migration_keeps_existing_app_config(monkeypatch, tmp_path) -> None:
    """程序目录已有配置时，反向迁移不能覆盖。"""
    app = tmp_path / "app"
    app.mkdir()
    local = tmp_path / "local"
    local.mkdir()
    (app / paths.CONFIG_NAME).write_text("freq=600\n", encoding="utf-8")
    (local / paths.CONFIG_NAME).write_text("freq=300\n", encoding="utf-8")
    (local / paths.FALLBACK_MARKER_NAME).write_text("2026-01-01 00:00:00\n", encoding="utf-8")

    _reset(monkeypatch, str(app), lambda directory: True)
    monkeypatch.setattr(paths, "_user_data_dir", lambda: str(local))

    assert paths.data_dir() == str(app)
    assert (app / paths.CONFIG_NAME).read_text(encoding="utf-8") == "freq=600\n"
