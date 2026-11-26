# 福信校网通 · FJPIT 校园网自动认证助手

> 断网自动重连，告别手动点「登录」。一个常驻托盘的小工具，专为 **福建信息职业技术学院（FJPIT）** 校园网 ePortal 认证打造。

<p>
  <img alt="version" src="https://img.shields.io/badge/version-v2.0.0-2f6fed">
  <img alt="platform" src="https://img.shields.io/badge/platform-Windows%2010%2F11%20x64-0078d4">
  <img alt="python" src="https://img.shields.io/badge/python-3.11-3776ab">
  <img alt="ui" src="https://img.shields.io/badge/UI-pywebview%20%2B%20WebView2-18a058">
</p>

![界面截图](docs/screenshot.png)

---

## 这是什么

连上校园 WiFi 后，认证过期往往不会有任何提示——浏览器打不开、游戏掉线，得手动打开网关页面重新登录。
这个小工具会在后台按你设定的频率探测网络，一旦发现"连着 WiFi 但没认证"，就自动完成 ePortal 网页认证，
网络恢复后继续静默守护。

界面用 **Python + pywebview** 重写（原生 WebView2 渲染），托盘常驻，仅 ~21 MB 单文件、免安装。

## 功能特性

**自动认证**

- 三源连通性探测（`generate_204` / `msftconnecttest` / `captive.apple`），校验响应特征，避免被劫持页骗过
- **显式绕过系统代理**：断网时若系统代理仍可达探测域名，会被误判为"在线"——这里用直连彻底规避
- 自动跟随门户跳转链（302 链 + 200 页面的 JS/meta 跳转，最多 6 跳），提取 `index.jsp?…` 认证参数
- 密码按网关要求做 **RSA PKCS#1 v1.5** 加密（十进制串回传），兼容明文网关
- 会话残留处理：落到 `redirectortosuccess` 且无参数时，用空参数强制刷新认证会话
- 认证提交后每 5 秒复检、最长等 60 秒，确认真的通了才算成功

**守护与重试**

- 失败间隔 30 秒重试，连续 3 次自动停止并复位界面
- 账号/密码错误（"用户不存在""密码不匹配"）**立即停止**，不无意义重试，直接显示网关原文
- 只在状态变化时写日志，不会刷屏
- 检测频率运行中修改即时生效

**界面**

- 卡片式现代 UI：状态徽标、在线/离线、上次检测、失败计数一屏可见
- 运行中锁定账号与频率，防止误改
- 保存按钮两态（有改动才可点）、密码显隐
- 日志实时滚动，支持导出与打开所在目录

**托盘与自启**

- 点 × 隐藏到托盘继续跑；左键恢复窗口；右键「打开主界面 / 退出」
- 托盘提示实时显示「运行中 / 已停止」
- 开机自启（用户级，写入 `HKCU\…\Run`，免管理员），带 `--min` 静默驻留托盘
- 自启作用域下拉预留「系统级（开发中）」接口

**安全**

- 学号密码用 `AES-GCM` 加密后存本地（`SHA256("fuxin-xiaowangtong|" + salt)` 派生密钥），不联网上传
- 日志自动脱敏：`mac=` `wlanuserip=` `snmpagentip=` `nasportid=` 只保留前 4 位
- 单实例：重复双击只会把已有窗口叫出来

## 下载与安装

1. 到 **Releases** 下载 `福信校网通_v2.0.0.exe`（或自行编译，见下文）
2. **建议先解压/放到独立文件夹**，不要直接在压缩包里双击运行
3. 双击启动。首次运行可能出现「Windows 已保护你的电脑」→ 点「更多信息」→「仍要运行」
   （程序没有购买代码签名证书，属正常现象）
4. 需要 **WebView2 运行时**：Win11 与较新的 Win10 自带 Edge 即可；若提示无法初始化界面内核，
   安装 [Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/) 后重试

> 首次运行会在 exe 同目录生成 `fuxin_log.txt`、`fuxin_config.dat`、`webview_data\`，属正常产物。

## 快速上手

1. 填「学号」→ 点右侧 **保存**
2. 填「密码」→ 点 **保存**（保存后输入框提示"已保存，留空则不修改"）
3. 点顶部绿色 **启 动**

运行后按钮变红「停 止」，账号信息锁定。要改设置先点停止。

**断网验证**：连上校园网但处于"需要登录"状态 → 点启动 → 日志应依次出现
`检测到断网，开始认证` → `已获取认证跳转URL` → `已获取认证参数` → `认证成功，网络已恢复`。

## 使用说明

| 项 | 说明 |
|---|---|
| 检测频率 | 单位 **秒/次**，最小 1、必须整数、**不设上限**；保存后立即生效 |
| 开机自启 | 作用域选「用户级」后打开开关；选「系统级（开发中）」时开关置灰 |
| 关闭窗口 | 隐藏到托盘，后台继续运行 |
| 真正退出 | 托盘右键 →「退出」（会先停止检测并写日志） |
| 日志 | exe 同目录 `fuxin_log.txt`，也可在界面「导出日志」 |

## 工作原理（够用版）

```
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
每 5 秒复检，最多 60 秒；成功即恢复守护
```

失败分类处理：**凭据错误**立即停止；**网络类失败** 30 秒后重试，连续 3 次停止。

## 常见问题

<details>
<summary><b>双击没反应 / 白屏</b></summary>

多半被杀毒软件拦截，或 SmartScreen 提示没点「仍要运行」。
白屏通常是 WebView2 未能渲染页面：把 exe 放到不含特殊字符的普通目录（桌面、`D:\某个新文件夹`）再试，
并把 `fuxin_log.txt` 一并反馈。
</details>

<details>
<summary><b>点了 × 程序是不是退出了？</b></summary>

没有，隐藏到托盘了（可能被折叠进任务栏「^」溢出区）。左键点托盘图标即可恢复。
</details>

<details>
<summary><b>开机自启没生效</b></summary>

① 确认作用域是「用户级」且开关是绿色；② 任务管理器 →「启动」看「福信校网通」是否被禁用；
③ 换过 exe 位置的话，把开关关一次再开一次，让注册表值指向新路径。
</details>

<details>
<summary><b>提示"已在运行中"</b></summary>

托盘里已有一个实例，它会自动把窗口叫出来——不需要也不允许再开第二个。
</details>

<details>
<summary><b>认证一直失败怎么排查</b></summary>

看日志最后几行：

| 日志 | 含义 | 处理 |
|---|---|---|
| `用户不存在` / `密码不匹配` | 凭据错误，程序已自动停止 | 核对学号密码 |
| `无法连接认证服务器` | 不在校园网 / 未连 WiFi | 检查网络 |
| `认证已提交，但等待 60 秒后网络仍未连通` | 网关收下了但没放行 | 重试一次，或手动开浏览器对比 |
| `认证服务器可达但未获取到认证参数` | 可能网络已经恢复 | 通常可忽略 |
</details>

## 技术栈

| 层 | 选型 |
|---|---|
| UI | HTML + CSS + 原生 JS（WebView2 渲染） |
| 壳 | pywebview 6 + WinForms |
| 后端 | Python 3.11（`http.client` 直连，无第三方 HTTP 依赖） |
| 加密 | cryptography（AES-GCM） |
| 托盘 | pystray + Pillow |
| 打包 | PyInstaller（单文件） |

## 目录结构

```
.
├─ run.py                 # 入口（写 .NET app.config → 起界面）
├─ fuxin.spec             # 单文件打包
├─ fuxin_portable.spec    # 免解压目录版打包
├─ requirements.txt
├─ assets/                # 图标
├─ docs/                  # 截图
├─ ui/                    # 前端：index.html / style.css / app.js
├─ src/fuxin/
│  ├─ main.py             # DPI、单实例、托盘接线、窗口
│  ├─ paths.py            # 路径与常量（以 exe 目录为准）
│  ├─ logger.py           # 日志：文件 + 内存 + 脱敏
│  ├─ config.py           # 配置：AES-GCM 读写
│  ├─ net.py              # 直连 HTTP、探测、跳转链
│  ├─ auth.py             # pageInfo / RSA / login / 完整认证
│  ├─ worker.py           # 监控线程与重试状态机
│  ├─ tray.py             # 系统托盘
│  ├─ autostart.py        # 开机自启（用户级 / 系统级预留）
│  └─ api.py              # 前端桥（轮询 + 保存 + 启停 + 日志）
└─ tools/selftest.py      # 核心自测（RSA / 转义 / mock 网关 / 配置）
```

## 本地开发

> **必须用 Python 3.11**：pythonnet 3.2+ 只提供 netstandard2.1 程序集，.NET Framework 4.8 加载不了；
> 3.11 + pythonnet 3.0.5 才能走系统自带 .NET Framework，打包后的 exe 也才能"开箱即用"。

```bat
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\pip install pythonnet==3.0.5 pyinstaller
.venv\Scripts\python run.py
```

核心逻辑自测（不需要校园网，会在本地起一个 mock ePortal）：

```bat
.venv\Scripts\python tools\selftest.py
```

## 打包

```bat
python -m PyInstaller --noconfirm --clean fuxin.spec
```

产物：`dist\福信校网通.exe`（单文件）。

已经在 spec/代码里处理掉的三个坑（改配置时别删）：

| 现象 | 原因 | 处理 |
|---|---|---|
| `Could not create temporary directory!` | 某些目录/策略下无法用 `%TEMP%` 解包 | spec 里 `runtime_tmpdir="."`，改在 exe 旁解包（退出自动清理） |
| `Failed to resolve Python.Runtime.Loader.Initialize` | .NET Framework 把路径判为"远程程序集" | `run.py` 导入前自动写 `<exe>.config`，打开 `loadFromRemoteSources` |
| 窗口白屏 | 中文路径 / `file://` 在 WebView2 偶发加载失败 | UI 改由本地回环服务加载（`http_server=True`），`storage_path` 固定数据目录 |

## 运行期文件

| 文件 | 说明 |
|---|---|
| `fuxin_config.dat` | 学号/密码（AES-GCM 密文）+ 检测频率 + 自启作用域 |
| `fuxin_log.txt` | 运行日志（UTF-8 / CRLF / `[时间] 内容`） |
| `fuxin_log_export_*.txt` | 「导出日志」的副本 |
| `webview_data\` | WebView2 缓存，可删 |

## 免责声明

- 本项目为个人学习与自用工具，与学校、网络中心无任何从属关系
- 仅在你**本人已获得授权**的账号与网络环境下使用；请遵守学校网络使用规定
- 凭据只保存在本机（加密），不会上传；但因使用本工具产生的任何后果由使用者自行承担

## 作者

**fysp431** · 邮箱 2872058826@qq.com

如果这个小工具帮到了你，点个 ⭐ 就好。
