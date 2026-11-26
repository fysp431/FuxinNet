/* 福信校网通 - 前端逻辑
 * 与后端通过 window.pywebview.api 通信，状态用轮询拉取（800ms），
 * 避免任何跨线程的 evaluate_js 调用。
 */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);

  const el = {
    subtitle: $("subtitle"),
    pill: $("pill"),
    pillText: $("pillText"),
    mainBtn: $("mainBtn"),
    mainBtnText: $("mainBtnText"),
    netState: $("netState"),
    lastCheck: $("lastCheck"),
    failCount: $("failCount"),
    userInput: $("userInput"),
    passInput: $("passInput"),
    freqInput: $("freqInput"),
    eyeBtn: $("eyeBtn"),
    saveUser: $("saveUser"),
    savePass: $("savePass"),
    saveFreq: $("saveFreq"),
    lockBadge: $("lockBadge"),
    autoStart: $("autoStart"),
    autoScope: $("autoScope"),
    hint: $("hint"),
    log: $("log"),
    exportBtn: $("exportBtn"),
    openDirBtn: $("openDirBtn"),
    ver: $("ver"),
    toast: $("toast"),
  };

  const state = {
    running: false,
    phase: "idle",
    online: null,
    failCount: 0,
    lastCheck: "",
    cursor: 0,
    cfg: { user: "", hasPass: false, freq: 600 },
    autostart: false,
    autostartScope: "user",
    autostartSystemSupported: false,
    ready: false,
  };

  let toastTimer = 0;
  function toast(message) {
    el.toast.textContent = message;
    el.toast.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.toast.classList.remove("show"), 2600);
  }

  function api() {
    return window.pywebview && window.pywebview.api;
  }

  /* ------------------------------------------------------------ 状态渲染 */
  function renderState() {
    const running = state.running;
    const authenticating = running && state.phase === "authenticating";
    const checking = running && state.phase === "checking";

    el.pill.className = "pill";
    if (authenticating) {
      el.pill.classList.add("working");
      el.pillText.textContent = "正在认证";
    } else if (running) {
      el.pill.classList.add("running");
      el.pillText.textContent = "运行中";
    } else if (state.phase === "error") {
      el.pill.classList.add("error");
      el.pillText.textContent = "已停止";
    } else {
      el.pill.classList.add("idle");
      el.pillText.textContent = "未运行";
    }

    el.mainBtn.className = "main-btn " + (running ? "running" : "idle");
    el.mainBtnText.textContent = running ? "停 止" : "启 动";

    if (state.online === true) {
      el.netState.textContent = "在线";
      el.netState.className = "ok";
    } else if (state.online === false) {
      el.netState.textContent = "离线";
      el.netState.className = "bad";
    } else {
      el.netState.textContent = "—";
      el.netState.className = "";
    }

    el.lastCheck.textContent = state.lastCheck || (checking ? "检测中…" : "—");
    el.failCount.textContent = String(state.failCount || 0);
    el.failCount.className = state.failCount > 0 ? "bad" : "";

    el.lockBadge.hidden = !running;
    el.userInput.disabled = running;
    el.passInput.disabled = running;
    el.freqInput.disabled = running;
    el.eyeBtn.disabled = running;

    renderAutoStart();
    refreshSaveButtons();
  }

  function renderAutoStart() {
    const isSystem = state.autostartScope === "system";
    // 系统级未实现：开关置灰但仍反映「用户级」真实状态，避免误以为已关闭
    el.autoStart.classList.toggle("on", !!state.autostart);
    el.autoStart.setAttribute("aria-checked", state.autostart ? "true" : "false");
    el.autoStart.disabled = isSystem;
    if (isSystem) {
      el.autoStart.title = state.autostart
        ? "系统级自启开发中；当前用户级：已开启"
        : "系统级自启开发中；当前用户级：未开启";
    } else {
      el.autoStart.title = state.autostart ? "已启用，点击关闭" : "未启用，点击开启";
    }
    if (el.autoScope.value !== state.autostartScope) {
      el.autoScope.value = state.autostartScope;
    }
  }

  function refreshSaveButtons() {
    const running = state.running;

    const userValue = el.userInput.value.trim();
    const userDirty = !running && userValue !== "" && userValue !== state.cfg.user;
    el.saveUser.classList.toggle("active", userDirty);
    el.saveUser.disabled = !userDirty;

    const passDirty = !running && el.passInput.value.length > 0;
    el.savePass.classList.toggle("active", passDirty);
    el.savePass.disabled = !passDirty;

    const freqValue = Number(el.freqInput.value);
    const freqDirty =
      !running &&
      el.freqInput.value !== "" &&
      Number.isFinite(freqValue) &&
      freqValue !== state.cfg.freq;
    el.saveFreq.classList.toggle("active", freqDirty);
    el.saveFreq.disabled = !freqDirty;
  }

  /* ------------------------------------------------------------ 日志 */
  function appendEntries(entries) {
    if (!entries || !entries.length) return;
    const nearBottom =
      el.log.scrollHeight - el.log.scrollTop - el.log.clientHeight < 60;

    for (const item of entries) {
      const line = document.createElement("div");
      line.className = "line";
      const text = item.text || "";
      if (/失败|错误|异常|无法连接/.test(text)) line.classList.add("err");
      else if (/成功|网络正常|已恢复/.test(text)) line.classList.add("ok");

      const t = document.createElement("span");
      t.className = "t";
      t.textContent = item.time || "";
      const m = document.createElement("span");
      m.className = "m";
      m.textContent = text;

      line.appendChild(t);
      line.appendChild(m);
      el.log.appendChild(line);
    }

    const empty = el.log.querySelector(".empty");
    if (empty) empty.remove();

    while (el.log.childElementCount > 400) {
      el.log.removeChild(el.log.firstElementChild);
    }
    if (nearBottom) el.log.scrollTop = el.log.scrollHeight;
  }

  function showEmptyLog() {
    const div = document.createElement("div");
    div.className = "empty";
    div.textContent = "暂无日志。点击「启动」开始自动检测网络。";
    el.log.appendChild(div);
  }

  /* ------------------------------------------------------------ 配置同步 */
  function hydrateConfig(cfg) {
    if (!cfg) return;
    state.cfg = cfg;
    if (document.activeElement !== el.userInput) {
      el.userInput.value = cfg.user || "";
    }
    el.passInput.placeholder = cfg.hasPass ? "已保存，留空则不修改" : "请输入密码";
    if (document.activeElement !== el.freqInput) {
      el.freqInput.value = String(cfg.freq);
    }
    el.hint.textContent = cfg.hasPass
      ? "提示：学号/密码已加密保存在程序目录；运行中不可修改。"
      : "提示：填写学号与密码并分别「保存」后才能启动；运行中不可修改。";
  }

  /* ------------------------------------------------------------ 后端调用 */
  async function call(method, ...args) {
    const backend = api();
    if (!backend || typeof backend[method] !== "function") {
      toast("后端未就绪");
      return null;
    }
    try {
      return await backend[method](...args);
    } catch (err) {
      toast("调用失败：" + err);
      return null;
    }
  }

  function applyReply(reply) {
    if (!reply) return;
    if (reply.config) hydrateConfig(reply.config);
    if (reply.state) {
      state.running = !!reply.state.running;
      state.phase = reply.state.phase || "idle";
      state.online = reply.state.online;
      state.failCount = reply.state.failCount || 0;
      state.lastCheck = reply.state.lastCheck || "";
      renderState();
    }
    if (typeof reply.cursor === "number") state.cursor = reply.cursor;
    if (reply.message) toast(reply.message);
  }

  /* ------------------------------------------------------------ 轮询 */
  let polling = false;
  async function poll() {
    if (polling || !state.ready) return;
    polling = true;
    try {
      const data = await call("poll", state.cursor);
      if (data) {
        if (data.config) hydrateConfig(data.config);
        if (data.state) {
          const prevRunning = state.running;
          const prevPhase = state.phase;
          state.running = !!data.state.running;
          state.phase = data.state.phase || "idle";
          state.online = data.state.online;
          state.failCount = data.state.failCount || 0;
          state.lastCheck = data.state.lastCheck || "";
          if (prevRunning !== state.running || state.running) renderState();
          if (state.phase === "error" && prevPhase !== "error" && data.state.message) {
            toast(data.state.message);
          }
        }
        if (Array.isArray(data.entries)) appendEntries(data.entries);
        if (typeof data.cursor === "number") state.cursor = data.cursor;
      }
    } finally {
      polling = false;
    }
  }

  /* ------------------------------------------------------------ 初始化 */
  async function bootstrap() {
    const data = await call("bootstrap");
    if (!data) return;
    state.ready = true;
    el.ver.textContent = data.version || "v2.0.0";
    state.autostart = !!data.autostart;
    state.autostartScope = data.autostartScope === "system" ? "system" : "user";
    state.autostartSystemSupported = !!data.autostartSystemSupported;
    if (data.subtitle) el.subtitle.textContent = data.subtitle;
    if (data.cursor) state.cursor = data.cursor;
    hydrateConfig(data.config);
    if (data.state) {
      state.running = !!data.state.running;
      state.phase = data.state.phase || "idle";
      state.online = data.state.online;
      state.failCount = data.state.failCount || 0;
      state.lastCheck = data.state.lastCheck || "";
    }
    renderState();
    if (Array.isArray(data.entries) && data.entries.length) {
      appendEntries(data.entries);
      el.log.scrollTop = el.log.scrollHeight;
    } else {
      showEmptyLog();
    }
    setInterval(poll, 800);
  }

  /* ------------------------------------------------------------ 事件绑定 */
  function bind() {
    el.mainBtn.addEventListener("click", async () => {
      el.mainBtn.disabled = true;
      const reply = state.running ? await call("stop") : await call("start");
      applyReply(reply);
      el.mainBtn.disabled = false;
    });

    el.saveUser.addEventListener("click", async () => {
      const reply = await call("save_user", el.userInput.value);
      applyReply(reply);
    });

    el.savePass.addEventListener("click", async () => {
      const reply = await call("save_pass", el.passInput.value);
      if (reply && reply.ok) el.passInput.value = "";
      applyReply(reply);
    });

    el.saveFreq.addEventListener("click", async () => {
      const reply = await call("save_freq", el.freqInput.value);
      applyReply(reply);
    });

    el.autoScope.addEventListener("change", async () => {
      const scope = el.autoScope.value === "system" ? "system" : "user";
      el.autoScope.disabled = true;
      const reply = await call("set_autostart_scope", scope);
      if (reply) {
        if (reply.autostartScope) state.autostartScope = reply.autostartScope;
        if (typeof reply.autostart === "boolean") state.autostart = reply.autostart;
        toast(reply.message || "已切换");
      }
      state.autostartScope = scope;
      renderAutoStart();
      el.autoScope.disabled = false;
    });

    el.autoStart.addEventListener("click", async () => {
      el.autoStart.disabled = true;
      const want = !state.autostart;
      const reply = await call("set_autostart", want, state.autostartScope);
      if (reply && typeof reply.autostart === "boolean") {
        state.autostart = reply.autostart;
      } else if (reply && reply.ok) {
        state.autostart = want;
      }
      if (reply && reply.autostartScope) state.autostartScope = reply.autostartScope;
      renderAutoStart();
      if (reply) toast(reply.message || (state.autostart ? "开机自启已开启" : "开机自启已关闭"));
      el.autoStart.disabled = false;
    });

    el.eyeBtn.addEventListener("click", () => {
      el.passInput.type = el.passInput.type === "password" ? "text" : "password";
    });

    el.exportBtn.addEventListener("click", async () => {
      const reply = await call("export_log");
      if (reply && reply.ok) toast("日志已导出：" + reply.message);
      else if (reply) toast(reply.message || "导出失败");
    });

    el.openDirBtn.addEventListener("click", async () => {
      const reply = await call("open_log_dir");
      if (reply && !reply.ok) toast(reply.message || "无法打开目录");
    });

    for (const input of [el.userInput, el.passInput, el.freqInput]) {
      input.addEventListener("input", refreshSaveButtons);
      input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          if (input === el.userInput) el.saveUser.click();
          else if (input === el.passInput) el.savePass.click();
          else el.saveFreq.click();
        }
      });
    }
  }

  function start() {
    bind();
    if (window.pywebview && window.pywebview.api) {
      bootstrap();
    } else {
      window.addEventListener("pywebviewready", bootstrap, { once: true });
      setTimeout(() => {
        if (!state.ready && !api()) {
          showEmptyLog();
          toast("等待后端连接…");
        }
      }, 3000);
    }
  }

  document.addEventListener("DOMContentLoaded", start);
})();
