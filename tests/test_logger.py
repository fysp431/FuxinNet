"""日志脱敏与导出。"""

from __future__ import annotations

import re

import pytest

from fuxin import logger


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("mac=001122334455", "mac=0011****"),
        ("macAddress=001122334455", "macAddress=0011****"),
        ("wlanuserip=10.1.2.3&x=1", "wlanuserip=10.1****&x=1"),
        (
            "indexParams=wlanuserip%3D10.1.2.3%26mac%3D001122334455",
            "indexParams=wlanuserip%3D10.1****%26mac%3D0011****",
        ),
        ("password=secret123", "password=****"),
        ("ticket=abc%2Fdef", "ticket=****"),
        (
            'pageInfo={"mac":"001122334455","wlanuserip":"10.1.2.3"}',
            'pageInfo={"mac":"0011****","wlanuserip":"10.1****"}',
        ),
        ('{"password":"secret123"}', '{"password":"****"}'),
        ("compass=abc", "compass=abc"),
        ("普通日志没有敏感字段", "普通日志没有敏感字段"),
    ],
)
def test_mask_sensitive(raw: str, expected: str) -> None:
    assert logger.mask_sensitive(raw) == expected


def test_export_includes_rotated_history_and_full_timestamp(tmp_path) -> None:
    path = tmp_path / "fuxin_log.txt"
    log = logger.Logger(str(path))
    log.log("第一条")
    log.log("第二条")

    # 模拟已轮转的更早日志（带完整日期时间戳）
    (tmp_path / "fuxin_log.txt.1").write_text(
        "[2026-01-01 00:00:00] 昨天的记录\r\n", encoding="utf-8", newline=""
    )

    dest = tmp_path / "export.txt"
    log.export(str(dest))

    with open(dest, encoding="utf-8", newline="") as fh:
        text = fh.read()

    assert "昨天的记录" in text
    assert "第一条" in text and "第二条" in text
    assert re.search(r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]", text)
