"""启动入口（源码运行与 PyInstaller 打包共用）。

注意：必须在 import fuxin / webview 之前写好 .NET 的 app.config。
部分机器/目录（如从网络位置、某些虚拟盘或带来源标记的目录）会让
.NET Framework 以"远程程序集"为由拒绝加载 pythonnet 的 Python.Runtime.dll，
需要 loadFromRemoteSources 开关。这里在启动时补一个 <exe>.config 解决。
"""

from __future__ import annotations

import os
import sys

_APP_CONFIG = """<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <startup useLegacyV2RuntimeActivationPolicy="true">
    <supportedRuntime version="v4.0" sku=".NETFramework,Version=v4.8"/>
  </startup>
  <runtime>
    <loadFromRemoteSources enabled="true"/>
  </runtime>
</configuration>
"""


def _ensure_dotnet_config() -> None:
    if not getattr(sys, "frozen", False):
        return
    exe_path = sys.executable
    config_path = exe_path + ".config"
    if os.path.exists(config_path):
        return
    try:
        with open(config_path, "w", encoding="utf-8") as fh:
            fh.write(_APP_CONFIG)
    except OSError:
        pass  # 目录不可写时只能作罢，正常路径通常可写


_ensure_dotnet_config()

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from fuxin.main import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
