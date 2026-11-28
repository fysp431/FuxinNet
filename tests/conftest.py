"""pytest 公共配置：把 ``src`` 与 ``tests`` 加入导入路径。"""

from __future__ import annotations

import os
import sys

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS_DIR)
SRC = os.path.join(ROOT, "src")

for _path in (SRC, TESTS_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)
