# 福信校网通 · FJPIT 校园网自动认证助手

> 断网自动重连，告别手动点「登录」。一个常驻托盘的小工具，专为 **福建信息职业技术学院平潭校区（FJPIT）** 校园网 ePortal 认证打造。

<p>  
  <img alt="version" src="https://img.shields.io/badge/version-v2.0.1-2f6fed">  
  <img alt="platform" src="https://img.shields.io/badge/platform-Windows%2010%2F11%20x64-0078d4">  
  <img alt="python" src="https://img.shields.io/badge/python-3.11-3776ab">  
  <img alt="ui" src="https://img.shields.io/badge/UI-pywebview%20%2B%20WebView2-18a058">  
</p>

<p align="center">
  <a href="docs/screenshot.png"><img src="docs/screenshot.png" alt="界面截图" width="520"></a>
</p>

---

[这是什么](#这是什么) · [功能特性](#功能特性) · [下载与安装](#下载与安装) · [快速上手](#快速上手) · [使用说明](#使用说明) · [工作原理](#工作原理) · [常见问题](#常见问题) · [技术栈](#技术栈) · [目录结构](#目录结构) · [本地开发](#本地开发) · [打包](#打包) · [运行期文件](#运行期文件) · [免责声明](#免责声明) · [许可证](#许可证) · [作者](#作者)

---

## 这是什么

连上校园 WiFi 后，认证过期往往不会有任何提示——浏览器打不开、游戏掉线，得手动打开网关页面重新登录。  
这个小工具会在后台按设定的频率探测网络，一旦发现「连着 WiFi 但没认证」，就自动完成 ePortal 网页认证，  
网络恢复后继续静默守护。

界面用 **Python + pywebview** 重写（原生 WebView2 渲染），托盘常驻，约 21 MB 单文件、免安装。

## 功能特性

**自动认证**

- 三源并发探测（gstatic `generate_204` / msftconnecttest / captive.apple，Apple 页按 200+Success 校验），显式直连绕过系统代理，避免被劫持页误判在线
- 自动跟随门户跳转链获取 `index.jsp?…` 认证参数，并按网关要求做 **RSA PKCS#1 v1.5** 密码加密

**守护与诊断**

- 检测到断网自动认证，失败自动重试或停止（策略见[使用说明](#使用说明)）
- 状态变化与每次检测都记运行数据；认证失败时额外落盘脱敏诊断文件（`fuxin_dump_*.txt`），便于对照网关行为

**开机自启**

- 支持用户级开机自启，开机后静默驻留托盘

**界面**

- 卡片式状态面板：状态徽标、在线/离线、上次检测、失败次数一屏可见
- 运行中锁定账号、密码与频率；保存按钮两态、密码显隐
- 学号/密码输入即时过滤中文、空格与不可见字符
- 配置文件损坏时页面顶部提示；保存失败给出可读原因并回滚本次修改

**安全**

- 学号密码用 `AES-GCM` 本地加密，日志自动脱敏，不联网上传
- 单实例运行：重复双击只会唤醒已有窗口

## 下载与安装

1. 到 [Releases](https://github.com/fysp431/FuxinNet/releases/latest) 下载 `FuxinNet_v2.0.1.exe`（或自行编译，见[打包](#打包)）
2. **建议放到独立文件夹**，不要直接在压缩包里双击运行
3. 双击启动。首次运行可能出现「Windows 已保护你的电脑」→ 点「更多信息」→「仍要运行」  
   （程序没有购买代码签名证书，属正常现象）
4. 需要 **WebView2 运行时**：Win11 与较新的 Win10 自带 Edge 即可；若提示无法初始化界面内核，  
   安装 [Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/) 后重试

> 运行期会生成哪些文件、存放在哪里，见[运行期文件](#运行期文件)。

## 快速上手

1. 填「学号」→ 点右侧 **保存**
2. 填「密码」→ 点 **保存**（保存后输入框提示「已保存，留空则不修改」）
3. 点顶部绿色 **启 动**

运行后按钮变红「停 止」，账号信息锁定。要改设置先点停止；若此时正在认证，界面会显示「正在停止」，后台收尾完成后自动恢复可编辑状态。

**断网验证**：连上校园网但处于「需要登录」状态 → 点启动 → 日志应依次出现  
`检测到断网（第N次检测），开始认证` → `已获取认证跳转URL: …` → `[认证] 认证成功，网络已恢复 用时=…ms`。

## 使用说明

| 项    | 说明                                    |
| ---- | ------------------------------------- |
| 检测频率 | 单位 **秒/次**，最小 **10**（默认 600）、必须整数、**不设上限**；停止状态下修改，下次启动生效；旧配置低于 10 时按 10 读取 |
| 自动运行 | 启动时若上次退出前监控在运行且已保存账号密码，自动开始检测；手动点「停止」后下次启动保持停止 |
| 等待网络 | 开机/启动时门户不可达会先等待，最长 **180 秒**（每 5 秒重试），不计入失败次数 |
| 认证失败 | 间隔 **30 秒**重试，连续 **3 次**自动停止；不可恢复错误立即停止，错误分类见[常见问题](#常见问题) |
| 开机自启 | 作用域选「用户级」后打开开关；系统级（开发中）置灰；换过 exe 路径需关一次再开 |
| 关闭窗口 | 隐藏到托盘，后台继续运行                          |
| 真正退出 | 托盘右键 →「退出」（会先停止检测并写日志）                |
| 关机/注销 | 记录「系统关机或注销，非用户操作」后放行，不阻塞关机；被强杀下次启动补记异常关闭 |
| 查看版本 | 右键 exe →「属性」→「详细信息」看「文件版本 / 产品版本」；界面底部页脚、托盘悬停提示也显示 `v2.0.1` |
| 日志   | 位置与轮转见[运行期文件](#运行期文件)；界面可「导出日志」      |

## 工作原理

```
等待门户可达（超时照常继续，参数见「使用说明」）
    ↓
探测在线？ ──是──> 安静等待下一轮
    │
    否
    ↓
跟随跳转链（≤6 跳）拿到 index.jsp?wlanuserip=…&mac=…    ← 参数原样回传，不解码再编码
    ↓
GET  /eportal/InterFace.do?method=pageInfo&indexParams=…  ← 取 passwordEncrypt / 公钥
    ↓
RSA PKCS#1 v1.5 填充 + 裸模幂 → 十进制字符串                ← 网关要求时才加密
    ↓
POST /eportal/InterFace.do?method=login
     userId / password / service="" / passwordEncrypt / queryString
    ↓
解析 {"result":"success"} 或 {"result":"fail","message":"…"}（含 \uXXXX 解码）
    ↓
立即复检，未通则每 5 秒重试，最多 60 秒；成功即恢复守护
```

补充：落到 `redirectortosuccess` 且无参数时视为会话残留，用空参数强制刷新认证会话；  
认证失败的分类处理见[常见问题](#常见问题)。

## 常见问题

<details>

<summary><b>双击没反应 / 白屏</b></summary>

多半被杀毒软件拦截，或 SmartScreen 提示没点「仍要运行」。  
白屏通常是 WebView2 未能渲染页面：把 exe 放到不含特殊字符的普通目录（`D:\某个新文件夹`）再试，  
并把 `fuxin_log.txt` 一并反馈。

</details>

<details>

<summary><b>开机自启没生效 / 开机后没有自动检测</b></summary>

① 确认作用域是「用户级」且开关是绿色；② 任务管理器 →「启动」看「福信校网通」是否被禁用；  
③ 换过 exe 位置的话，把开关关一次再开一次；④ 自动检测还要求上次退出时监控在运行且已保存账号密码。

</details>

<details>

<summary><b>认证一直失败怎么排查</b></summary>

看日志最后几行：

| 日志                      | 含义              | 处理             |
| ----------------------- | --------------- | -------------- |
| `用户不存在` / `密码不匹配`       | 凭据错误，程序已自动停止    | 核对学号密码         |
| `密码加密失败` / `未返回公钥`        | 网关公钥异常，不可恢复，已自动停止 | 稍后重试；持续出现请反馈日志 |
| `无法连接认证服务器`             | 不在校园网 / 未连 WiFi | 检查网络           |
| `认证已提交，但等待 60 秒后网络仍未连通` | 网关收下了但没放行       | 重试一次，或手动开浏览器对比 |
| `认证服务器可达但未获取到认证参数`      | 可能网络已经恢复        | 通常可忽略          |

</details>

## 技术栈

| 层  | 选型                                         |
| -- | ------------------------------------------ |
| UI | HTML + CSS + 原生 JS（WebView2 渲染）            |
| 壳  | pywebview 6 + WinForms                     |
| 后端 | Python 3.11（`http.client` 直连，无第三方 HTTP 依赖） |
| 加密 | cryptography（AES-GCM）                      |
| 托盘 | pystray + Pillow                           |
| 打包 | PyInstaller（单文件 / 免解压目录版；`version_info.py` 自动写入版本资源） |
| 关闭判定 | WinForms `FormClosing` + `CloseReason`（区分系统关机 / 点 X / 主动退出） |

## 目录结构

```
.
├─ run.py                 # 入口（写 .NET app.config → 起界面）
├─ fuxin.spec             # 单文件打包
├─ fuxin_portable.spec    # 免解压目录版打包
├─ requirements.txt       # 运行期依赖
├─ requirements-dev.txt   # 构建 / 测试期依赖
├─ pyproject.toml         # ruff / pytest 配置
├─ .github/workflows/
│  └─ ci.yml              # push / PR 自动跑 ruff + pytest
├─ version_info.py        # 生成 exe 的 Windows 版本资源（属性面板可看版本）
├─ assets/                # 图标
├─ docs/                  # 截图
├─ ui/                    # 前端：index.html / style.css / app.js
├─ src/fuxin/
│  ├─ main.py             # DPI、单实例、托盘、关闭策略、窗口
│  ├─ paths.py            # 路径与常量（数据目录探测 / 回退 / 迁移）
│  ├─ logger.py           # 日志：文件 + 内存 + 脱敏 + 导出
│  ├─ config.py           # 配置：AES-GCM 读写
│  ├─ diagnostics.py      # 认证失败诊断落盘（脱敏、限 256 KB）
│  ├─ net.py              # 直连 HTTP、并发探测、跳转链
│  ├─ auth.py             # pageInfo / RSA / login / 完整认证
│  ├─ worker.py           # 监控线程与重试状态机
│  ├─ tray.py             # 系统托盘
│  ├─ autostart.py        # 开机自启（用户级 / 系统级预留）
│  └─ api.py              # 前端桥（轮询 + 保存 + 启停 + 日志）
└─ tests/                 # pytest 用例（脱敏 / URL / RSA / 线程 / api / 路径回退）
```

## 本地开发

> **必须用 Python 3.11**：pythonnet 3.2+ 只提供 netstandard2.1 程序集，.NET Framework 4.8 加载不了；  
> 只有 3.11 + pythonnet 3.0.5 才能走系统自带 .NET Framework，打包后的 exe 也才能「开箱即用」。  
> 3.12 / 3.13 下的兼容性**没有验证过**，不要用它们打包或跑测试。

```bat
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python run.py
```

运行测试与静态检查（不需要校园网；认证流程用本地 mock ePortal）：

```bat
.venv\Scripts\python -m pytest
.venv\Scripts\python -m ruff check .
```

`tests/` 覆盖脱敏、URL 解析、RSA、完整认证流程、配置、线程生命周期、
api 保存/校验、数据目录回退与迁移；CI（`.github/workflows/ci.yml`）在
Python 3.11 上跑同样的两条命令。

## 打包

```bat
python -m PyInstaller --noconfirm --clean fuxin.spec
```

产物：`dist\FuxinNet_v2.0.1.exe`（单文件）。

目录版（免解压，行为一致）：

```bat
python -m PyInstaller --noconfirm --clean fuxin_portable.spec
```

产物：`dist\FuxinNet_v2.0.1\`。

产物名与「右键 → 属性 → 详细信息」中的文件版本 / 产品版本都来自
`src/fuxin/paths.py` 的 `APP_VERSION`（打包时由 `version_info.py` 生成版本资源），
发版时只改该常量即可，无需再动 spec。

已经在 spec/代码里处理掉的四个坑（改配置时别删）：

| 现象                                                   | 原因                                 | 处理                                                        |
| ---------------------------------------------------- | ---------------------------------- | --------------------------------------------------------- |
| `Could not create temporary directory!`              | `runtime_tmpdir` 用了相对路径 `.`，解析的是进程当前工作目录；开机自启时 CWD 为 `C:\Windows`，普通用户不可写 | spec 里改为 `runtime_tmpdir=r"%LOCALAPPDATA%\FuxinNet\tmp"`，固定为用户可写绝对路径，与 CWD 无关 |
| `Failed to resolve Python.Runtime.Loader.Initialize` | .NET Framework 把路径判为「远程程序集」        | `run.py` 导入前自动写 `<exe>.config`，打开 `loadFromRemoteSources` |
| 窗口白屏                                                 | 中文路径 / `file://` 在 WebView2 偶发加载失败 | UI 改由本地回环服务加载（`http_server=True`），`storage_path` 固定数据目录   |
| 关机被阻塞 / 日志误记为后台运行                                   | pywebview closing 在关机时返回 False 会否决关机 | 改用 WinForms FormClosing + CloseReason 单一决策，关机分支不隐藏窗口 |

## 运行期文件

默认都在程序（exe）同目录。若该目录不可写（如 `C:\Program Files`、只读介质），
程序启动时会自动回退到 `%LOCALAPPDATA%\FuxinNet\`，并把旧位置的
`fuxin_config.dat` 复制过去；选定的目录会写进日志开头，方便排障。

| 文件                       | 说明                              |
| ------------------------ | ------------------------------- |
| `fuxin_config.dat`       | 学号/密码（AES-GCM 密文）+ 检测频率 + 自启作用域 + `auto_run`（上次退出时监控是否在运行） |
| `fuxin_running.marker`   | 运行标记，用于识别上次是否异常关闭；正常退出时自动删除 |
| `fuxin_log.txt`          | 运行日志（UTF-8 / CRLF / `[时间] 内容`），超 2 MB 自动轮转为 `.1`~`.3` |
| `fuxin_dump_*.txt`       | 认证失败诊断现场（pageInfo / login 原始响应 + 跳转链，已脱敏，单份 ≤ 256 KB；仅在失败时生成） |
| `fuxin_log_export_*.txt` | 「导出日志」的副本，包含 `.1`~`.3` 轮转历史与完整日期时间戳 |
| `webview_data\`          | WebView2 缓存，可删                  |
| `<exe>.config`           | 首次运行由 `run.py` 自动生成，为 .NET 打开 `loadFromRemoteSources`；删除后下次启动会重建 |

单文件版启动时还会把运行库解压到 `%LOCALAPPDATA%\FuxinNet\tmp\_MEI*`，退出后自动清理，一般无需手动处理。

## 免责声明

- 本项目为个人学习与自用工具，与学校、网络中心无任何从属关系
- 仅在你**本人已获得授权**的账号与网络环境下使用；请遵守学校网络使用规定
- 凭据只保存在本机（加密），不会上传；但因使用本工具产生的任何后果由使用者自行承担

## 许可证

MIT

## 作者

**fysp431** · 邮箱 <2872058826@qq.com>

如果这个小工具帮到了你，点个 ⭐ 就好。
