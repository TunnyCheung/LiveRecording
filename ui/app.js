const QUALITIES = ["原画", "蓝光", "高清", "标清", "流畅"];
const FORMATS = [
  { value: "ts", label: "录制TS" },
  { value: "mp4", label: "录制MP4" },
  { value: "flv", label: "录制FLV" },
];

const state = {
  config: {},
  users: [],
  running: false,
  selected: null,
  pendingSettings: null,
  transcodeFile: null,
  timer: null,
};

const $ = (id) => document.getElementById(id);
/* 事件绑定一律走这里：元素不在就安静跳过。
   index.html 由 ui-shell 单独改，少一个 id 也只该是「那个功能没装」，不能让整页脚本挂掉。 */
function on(idOrEl, event, handler) {
  const el = typeof idOrEl === "string" ? $(idOrEl) : idOrEl;
  if (!el || !el.addEventListener) return null;
  el.addEventListener(event, handler);
  return el;
}
const api = () => window.pywebview.api;

function toast(text, ms = 2200) {
  const el = $("toast");
  el.textContent = text;
  el.classList.remove("hidden");
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.add("hidden"), ms);
}

/* ---------------- 主题（深色 / 浅色） ---------------- */
/* 两套样式令牌在 style.css 的 :root[data-theme="dark"] / :root[data-theme="light"] 里，
   这里只负责把 <html data-theme> 设对，并给 #btnTheme 画上对应的月亮 / 太阳图标。 */
const THEMES = ["dark", "light"];
let currentTheme = null;   // 当前已应用到 DOM 的主题；相同就不重复写，省掉每秒一次的无谓 DOM 变动

/* 内联 SVG，不依赖任何外部资源；图标自己插进按钮里 */
const ICON_SVG = {
  moon: '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>',
  sun: '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4.2"/><path d="M12 2v2.6M12 19.4V22M2 12h2.6M19.4 12H22M4.9 4.9l1.85 1.85M17.25 17.25l1.85 1.85M19.1 4.9l-1.85 1.85M6.75 17.25L4.9 19.1"/></svg>',
  /* 最大化 = 一个方框；还原 = 两个错开的方框（图标画的是「按下会发生什么」） */
  maximize: '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="4" width="16" height="16" rx="2"/></svg>',
  restore: '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="7" width="13" height="13" rx="2"/><path d="M8 7V5.6A1.6 1.6 0 0 1 9.6 4H19a1 1 0 0 1 1 1v9.4A1.6 1.6 0 0 1 18.4 16H17"/></svg>',
  min: '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12.5h14"/></svg>',
  close: '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>',
};

function renderIcon(button, kind) {
  if (!button) return;
  const html = ICON_SVG[kind];
  if (html && button.innerHTML !== html) button.innerHTML = html;
}

/* 把主题落到 DOM 上；force 用于「刚被用户手动设置过」，跳过相同判断直接重画 */
function applyTheme(theme, force) {
  const wanted = THEMES.indexOf(theme) >= 0 ? theme : "dark";
  if (!force && wanted === currentTheme) return wanted;
  currentTheme = wanted;
  document.documentElement.dataset.theme = wanted;
  const button = $("btnTheme");
  if (button) {
    // 按钮上的图标表示「点一下切到哪边」：现在深色就画太阳，现在浅色就画月亮
    renderIcon(button, wanted === "dark" ? "sun" : "moon");
    button.title = wanted === "dark" ? "切换到浅色" : "切换到深色";
    button.setAttribute("aria-label", button.title);
  }
  return wanted;
}

/* 点 #btnTheme：先立刻改 DOM（界面即时响应），再让后端落盘；失败就回滚 + 提示 */
async function toggleTheme(event) {
  if (event) {
    event.stopPropagation();
    event.preventDefault();
  }
  const next = currentTheme === "dark" ? "light" : "dark";
  const previous = currentTheme || "dark";
  applyTheme(next, true);
  const saved = state.config && state.config.theme;
  if (state.config) state.config.theme = next;   // 乐观更新：下一秒 refresh 的快照还没回来也不回跳
  try {
    await api().apply_settings({ theme: next });
  } catch (err) {
    applyTheme(previous, true);
    if (state.config) state.config.theme = saved || previous;
    toast("主题没切换成功，请重试");
  }
}

let themeBound = false;

function setupTheme() {
  const saved = (state.config && state.config.theme) || "dark";
  applyTheme(THEMES.indexOf(saved) >= 0 ? saved : "dark");   // 先按上次的选择上色，省掉启动时的一次闪白 / 闪黑
  if (themeBound) return;                                    // 只绑一次，boot() 里再调也不会重复绑
  themeBound = !!on("btnTheme", "click", toggleTheme);
}

/* 自己画的确认框：系统自带的 confirm 是块深色方框，和界面完全不是一个风格 */
function askConfirm(text, okText = "确定", title = "确认") {
  return new Promise((resolve) => {
    $("confirmTitle").textContent = title;
    $("confirmText").textContent = text;
    $("confirmOk").textContent = okText;
    const modal = $("confirmModal");
    const finish = (answer) => {
      modal.classList.add("hidden");
      $("confirmOk").onclick = null;
      $("confirmCancel").onclick = null;
      resolve(answer);
    };
    $("confirmOk").onclick = () => finish(true);
    $("confirmCancel").onclick = () => finish(false);
    modal.classList.remove("hidden");
  });
}

function fmtSize(bytes) {
  if (bytes >= 1024 ** 3) return (bytes / 1024 ** 3).toFixed(2) + "GB";
  if (bytes >= 1024 ** 2) return (bytes / 1024 ** 2).toFixed(1) + "MB";
  if (bytes >= 1024) return Math.round(bytes / 1024) + "KB";
  return bytes + "B";
}

function fmtCount(value) {
  // 后端解析出来的可能是字符串或脏值，先转数字，转不出来就当没有人数
  const num = Number(value);
  if (!isFinite(num) || num <= 0) return "";
  if (num >= 10000) return "在线 " + (num / 10000).toFixed(1) + "万";
  return "在线 " + Math.round(num);
}

/* 每行的「上次录制」：有记录只显示时间，没记录显示弱化的「未录制」。
   不再带文件大小 —— 录制中的那一行已经有「大小 xx」了，重复。
   （last_record_size 后端继续给，界面不用） */
function lastRecordText(user) {
  const at = String(user.last_record_at || "").trim();
  if (!at) return { text: "未录制", muted: true };
  return { text: "上次录制 " + at, muted: false };
}

/* ---------------- 名字显示 / 列表排序 ---------------- */
/* NICK-TEST-BEGIN */
const NICK_LIMIT = 6;   // 列表里最多显示 6 个字，多出来的用 ** 代替

/* 把名字切成「字」：两位数字算一个字（「10月」「11日」各两个字位），其余每个字符各算一个。
   这样「狗蛋传奇10月11日生日庆典」的前 6 个字正好是「狗蛋传奇10月」。
   三位以上的数字串按单个数字算：否则「5102833285」这种拿快手号当昵称的名字会被当成一个字，
   永远触发不了截断。 */
function nickUnits(text) {
  const units = [];
  (String(text).match(/\d+|[^\d]/gu) || []).forEach((piece) => {
    if (piece.length > 2) {
      for (const ch of piece) units.push(ch);
    } else {
      units.push(piece);
    }
  });
  return units;
}

function truncateNick(text, limit = NICK_LIMIT) {
  const raw = String(text == null ? "" : text);
  const units = nickUnits(raw);
  if (units.length <= limit) return raw;
  return units.slice(0, limit).join("") + "**";
}

/* 列表显示优先级：有备注就显示备注（用户自己写的，不参与截断），没有才显示截断后的昵称 */
function displayName(user) {
  const remark = typeof user.remark === "string" ? user.remark.trim() : "";
  if (remark) return remark;
  const real = String(user.nickname == null ? "" : user.nickname).trim();
  return truncateNick(real) || "未命名主播";
}

/* 把 wanted 里的行按顺序摆进 list：位置没变的行一个节点都不碰。
   原来的写法每轮刷新都无条件 appendChild —— 即使顺序没变，也会把每个 li 从文档里摘下来再插回去。
   鼠标底下的那一行被换掉，:hover 立刻断掉，.pills 的 opacity 过渡每秒重新开始，
   看上去就是「移除」按钮一闪一闪。现在只对位置不对的行 insertBefore，
   顺序不变时 DOM 完全没有变动，悬停态和选中态都不会再被打断。 */
function syncListOrder(list, wanted) {
  // 先摘掉这一轮已经没有的行：这样删一位主播不会让其他行的位置变化
  const keep = new Set(wanted);
  Array.from(list.children).forEach((li) => {
    if (!keep.has(li)) li.remove();
  });
  // 再按顺序摆位：位置本来就对的行不碰
  wanted.forEach((li, index) => {
    if (list.children[index] !== li) list.insertBefore(li, list.children[index] || null);
  });
}
/* NICK-TEST-END */

/* ---------------- 渲染 ---------------- */

function ensureRow(list, user) {
  let li = list.querySelector(`li[data-uid="${user.userid}"]`);
  if (li) return li;
  li = document.createElement("li");
  li.className = "row";
  li.dataset.uid = user.userid;
  li.innerHTML = `
    <img class="avatar" alt="" />
    <div class="meta"><div class="nick"></div><div class="sub"></div></div>
    <div class="row-right">
      <div class="pills"></div>
      <div class="auto-ctl" data-role="auto-ctl">
        <span class="auto-label"></span>
        <button class="switch" data-role="auto"></button>
      </div>
    </div>`;
  const avatar = li.querySelector(".avatar");
  // 加载失败就藏起来，并把 url 清掉，下一轮刷新会再试一次
  avatar.addEventListener("error", () => {
    avatar.style.visibility = "hidden";
    avatar.dataset.url = "";
  });
  li.querySelector(".pills").append(dangerPill("移除", () => removeUser(user.userid)));
  // 开关同时充当「开始录制 / 暂停录制」：关掉之后点一下就能恢复自动录制
  li.querySelector('[data-role="auto-ctl"]').addEventListener("click", (event) => {
    event.stopPropagation();
    const current = state.users.find((item) => item.userid === user.userid);
    setAuto(user.userid, !!(current && current.disable));
  });
  li.addEventListener("click", () => selectUser(user.userid));
  li.addEventListener("contextmenu", (event) => {
    event.preventDefault();
    selectUser(user.userid);
    openRowMenu(event.clientX, event.clientY);
  });
  return li;
}

/* 行内容签名：这一行显示出来的所有东西都由这些字段决定。
   签名没变 = 这一轮不用碰 DOM，fillRow 整段跳过；空闲行每秒刷新的 DOM 写入次数是 0。 */
function rowSignature(user) {
  const cfg = state.config || {};
  return [
    user.avatar || "",
    user.nickname || "",
    typeof user.remark === "string" ? user.remark : "",
    user.title || "",
    user.status || "",
    user.recording ? "1" : "0",
    user.live ? "1" : "0",
    user.duration == null ? "" : user.duration,
    user.size == null ? "" : user.size,
    user.user_count == null ? "" : user.user_count,
    user.error || "",
    user.last_record_at || "",
    user.last_record_file || "",
    user.disable ? "1" : "0",
    user.type || "",
    state.selected === user.userid ? "1" : "0",
    cfg.hideTime ? "1" : "0",
    cfg.hideFileSize ? "1" : "0",
    cfg.showUserCount ? "1" : "0",
  ].join("\u0001");
}

/* 头像单独处理：只在地址变了才重新赋 src（每秒重建 <img> 会让图反复中断，有的头像就再也不出来）。
   它跑在签名判断之前，所以加载失败清空 url 之后，下一轮刷新还能再试一次。 */
function syncAvatar(li, user) {
  const avatar = li.querySelector(".avatar");
  const wanted = user.avatar || "";
  if (avatar.dataset.url !== wanted) {
    avatar.dataset.url = wanted;
    avatar.style.visibility = "";
    avatar.referrerPolicy = "no-referrer";
    avatar.src = wanted;
  }
}

function fillRow(li, user) {
  syncAvatar(li, user);

  // 内容没变：昵称、title、.sub 里的 tag、选中态、自动录制开关全都不用重写，
  // 整行的 hover/过渡状态在两次刷新之间完全连续 —— 闪的是整行信息，不只是「移除」两个字。
  const sig = rowSignature(user);
  if (li._rowSig === sig) return;
  li._rowSig = sig;                 // 存在 JS 属性上，不写 DOM 属性

  const nick = li.querySelector(".nick");
  const realName = String(user.nickname || "").trim() || "未命名主播";
  // 有备注显示备注，没有就显示截断到 6 个字的名字（备注不截断）
  nick.textContent = displayName(user);
  // title 里始终放完整真名，截断后的名字悬停就能看到全名；正在播的标题另起一行接在后面
  nick.title = user.title ? realName + "\n" + "正在播：" + user.title : realName;

  const sub = li.querySelector(".sub");
  sub.textContent = "";
  const status = document.createElement("span");
  status.className = "status" + (user.recording ? " rec" : user.live ? " live" : "");
  status.textContent = user.status;
  sub.appendChild(status);
  const platform = document.createElement("span");
  const badge = {
    douyin: ["d", "抖音"],
    kuaishou: ["ks", "快手"],
    xiaohongshu: ["xhs", "小红书"],
    bilibili: ["b", "B站"],
    huya: ["hy", "虎牙"],
    douyu: ["dy", "斗鱼"],
    acfun: ["ac", "Acfun"],
    zhanqi: ["zq", "战旗"],
  }[user.type] || ["d", "抖音"];
  platform.className = "platform" + (badge ? " " + badge[0] : "");
  platform.textContent = badge[1];
  platform.title = platform.textContent;
  platform.dataset.platform = user.type || "douyin";
  sub.appendChild(platform);
  if (user.recording) {
    if (!state.config.hideTime) sub.appendChild(tag("时长 " + user.duration));
    if (!state.config.hideFileSize) sub.appendChild(tag("大小 " + user.size));
  }
  if (state.config.showUserCount && user.user_count) sub.appendChild(tag(fmtCount(user.user_count)));
  const last = lastRecordText(user);
  const lastTag = tag(last.text);
  lastTag.classList.add("last-rec");
  if (last.muted) lastTag.classList.add("muted");
  if (user.last_record_file) lastTag.title = user.last_record_file;
  sub.appendChild(lastTag);
  if (user.error) sub.appendChild(tag(user.error));

  li.classList.toggle("selected", state.selected === user.userid);
  const ctl = li.querySelector(".auto-ctl");
  const auto = li.querySelector('[data-role="auto"]');
  auto.classList.toggle("on", !user.disable);
  ctl.classList.toggle("on", !user.disable);
  ctl.classList.toggle("start", !!user.disable);
  ctl.querySelector(".auto-label").textContent = user.disable ? "开始录制" : "自动录制";
  ctl.title = user.disable
    ? "点一下恢复：开播后自动录制"
    : "点一下暂停：不再自动录制这位主播";
}

function renderUsers() {
  const list = $("userList");
  $("emptyHint").classList.toggle("hidden", state.users.length > 0);
  $("tabCount").textContent = state.users.length;

  // 先准备好每一行：ensureRow 复用原来那个 li；fillRow 内容没变就整段跳过
  const wanted = state.users.map((user) => {
    const li = ensureRow(list, user);
    fillRow(li, user);
    return li;
  });
  // 只有位置真的变了才动 DOM，顺序不变时一个节点都不碰（见 syncListOrder 的说明）
  syncListOrder(list, wanted);
}

function tag(text) {
  const span = document.createElement("span");
  span.className = "tag";
  span.textContent = text;
  return span;
}

function dangerPill(text, handler) {
  const button = document.createElement("button");
  button.className = "pill-danger";
  button.textContent = text;
  button.onclick = (event) => {
    event.stopPropagation();
    handler();
  };
  return button;
}

/* ---------------- 数据刷新 ---------------- */

let refreshing = false;
let lastRefreshError = 0;

async function refresh() {
  if (refreshing || !window.pywebview || !window.pywebview.api) return;   // 上一轮没回来就不再排新的
  refreshing = true;
  try {
    const data = await api().get_state();
    applyTheme((data.config && data.config.theme) || "dark");   // 每秒都对齐一次：后端改了主题界面也跟得上
    state.config = data.config;
    state.users = data.users;
    state.running = data.running;
    $("btnRun").textContent = data.running ? "停止检测" : "检测录制";
    $("btnQuality").textContent = data.config.definition || "蓝光";
    const format = FORMATS.find((item) => item.value === data.config.outFormat) || FORMATS[0];
    $("btnFormat").textContent = format.label;
    $("versionText").textContent = "当前版本: " + data.version;
    $("settingsVersion").textContent = "Version: " + data.version;
    renderUsers();
    if (!$("logsModal").classList.contains("hidden")) {
      $("logBox").textContent = (data.logs || []).join("\n");
      $("logBox").scrollTop = $("logBox").scrollHeight;
    }
  } catch (err) {
    // 出错要说出来，否则界面会一直空着像卡死一样
    console.error("refresh failed", err);
    if (Date.now() - lastRefreshError > 10000) {
      lastRefreshError = Date.now();
      toast("读取状态失败：" + (err && err.message ? err.message : err), 5000);
    }
  } finally {
    refreshing = false;
  }
}

function selectUser(userid) {
  state.selected = userid;
  $("rowActions").classList.toggle("hidden", !userid);
  renderUsers();
}

async function setAuto(userid, enabled) {
  await api().set_auto(userid, enabled);
  await refresh();
}

async function removeUser(userid) {
  const user = state.users.find((item) => item.userid === userid);
  const name = (user && (user.nickname || user.unique_id)) || "这位主播";
  const ok = await askConfirm(`把「${name}」从主播列表里移除？`, "移除");
  if (!ok) return;
  await api().remove_user(userid);
  state.selected = null;
  $("rowActions").classList.add("hidden");
  await refresh();
}

/* ---------------- 菜单 ---------------- */

function hideMenus() {
  ["mainMenu", "rowMenu", "qualityMenu", "formatMenu"].forEach((id) =>
    $(id).classList.add("hidden"));
}

/* 菜单一律贴着窗口内沿摆，绝不允许跑出窗口外。
   顶栏只有 40 多像素高，原来的「往上弹」写法在顶栏按钮上会把菜单推出上边界，
   现在正上方放不下就自动改成往下弹。 */
function placeMenu(menu, x, y, prefer) {
  const width = menu.offsetWidth || 160;
  const height = menu.offsetHeight || 0;
  const margin = 8;
  const viewportWidth = window.innerWidth;
  const viewportHeight = window.innerHeight;
  let top = y;
  if (prefer === "above" && y - height < margin) prefer = "below";
  if (prefer === "above") top = y - height;
  if (prefer === "below" && top + height > viewportHeight - margin
      && y - height >= margin) top = y - height;   // 下面放不下就翻到上面
  top = Math.max(margin, Math.min(top, Math.max(margin, viewportHeight - height - margin)));
  const left = Math.max(margin, Math.min(x, Math.max(margin, viewportWidth - width - margin)));
  menu.style.right = "auto";     // 只认算好的 left / top，避免残留的 right 把宽度算歪
  menu.style.bottom = "auto";
  menu.style.left = Math.round(left) + "px";
  menu.style.top = Math.round(top) + "px";
}

function openMenu(id, x, y, prefer) {
  hideMenus();
  const menu = $(id);
  if (!menu) return;             // 结构还没跟上时不报错
  menu.classList.remove("hidden");
  placeMenu(menu, x, y, prefer);
  // 窗口一被拖动 / 缩放，菜单跟着挪到新的坐标上，绝不留在窗口外面
  requestAnimationFrame(() => {
    if (!menu.classList.contains("hidden")) placeMenu(menu, x, y, prefer);
  });
}

/* 菜单挂在哪个按钮上：右边缘和按钮对齐（按钮靠窗口右边才不会被推出界），纵向按 prefer 弹 */
function openMenuAtButton(id, button, prefer) {
  if (!button) return;
  const rect = button.getBoundingClientRect();
  openMenu(id,
    rect.right - 150,
    prefer === "above" ? rect.top - 4 : rect.bottom + 4,
    prefer);
}

function openRowMenu(x, y) {
  // 这一项在「停止录制 / 开始录制」之间切换，没在播也没在录时置灰
  const user = state.users.find((item) => item.userid === state.selected);
  const button = $("rowMenu").querySelector('[data-row="stoprec"]');
  const recording = !!(user && user.recording);
  button.textContent = recording ? "停止录制" : "开始录制";
  button.disabled = !user || (!recording && !user.live);
  openMenu("rowMenu", x, y);
}

function buildChoiceMenu(id, options, current, onPick) {
  const menu = $(id);
  menu.innerHTML = "";
  options.forEach((option) => {
    const button = document.createElement("button");
    button.textContent = option.label + (option.value === current ? "  ✓" : "");
    button.onclick = () => onPick(option.value);
    menu.appendChild(button);
  });
}

document.addEventListener("click", (event) => {
  if (!event.target.closest(".menu") && !event.target.closest("#btnMenu")
      && !event.target.closest("#btnQuality") && !event.target.closest("#btnFormat")) {
    hideMenus();
  }
});

/* 顶栏的 ⚙：菜单从齿轮下方贴着窗口右边线展开。
   currentTarget 而不是 target —— 点在里面的 <svg> 上也能拿到那个按钮的坐标。 */
on("btnMenu", "click", (event) => openMenuAtButton("mainMenu", event.currentTarget, "below"));

on("btnQuality", "click", (event) => {
  buildChoiceMenu("qualityMenu",
    QUALITIES.map((q) => ({ value: q, label: q })),
    state.config.definition,
    async (value) => {
      hideMenus();
      await api().apply_settings({ definition: value });
      toast("清晰度：" + value);
      refresh();
    });
  openMenuAtButton("qualityMenu", event.currentTarget, "below");
});

on("btnFormat", "click", (event) => {
  buildChoiceMenu("formatMenu", FORMATS.map((f) => ({ value: f.value, label: f.label })),
    state.config.outFormat,
    async (value) => {
      hideMenus();
      await api().apply_settings({ outFormat: value });
      toast("输出格式：" + value.toUpperCase());
      refresh();
    });
  openMenuAtButton("formatMenu", event.currentTarget, "below");
});

on("mainMenu", "click", async (event) => {
  const key = event.target.dataset.menu;
  if (!key) return;
  hideMenus();
  if (key === "settings") openSettings();
  if (key === "transcode") openTranscode();
  if (key === "savedir") await api().open_save_dir();
  if (key === "readme") $("readmeModal").classList.remove("hidden");
  if (key === "logs") {
    $("logsModal").classList.remove("hidden");
    const data = await api().get_state();
    $("logBox").textContent = (data.logs || []).join("\n");
  }
});

/* ---------------- 无边框窗口：三键 + 拖动区双击最大化 ----------------
   窗口是 frameless（无系统标题栏），三个按钮直接调后端 js_api：
   win_minimize / win_toggle_max / win_close。每个按钮都 stopPropagation，
   否则事件冒泡到顶栏会被当成拖动窗口，点按钮就变成拖窗口了。 */

/* 最大化 / 还原后换按钮图标与提示：maximized → 还原图标，其余 → 最大化图标 */
function applyWinState(state) {
  const button = $("btnWinMax");
  if (!button) return;
  const maximized = state === "maximized";
  // 已经最大化了，按钮当然显示「还原」那个双框图标
  renderIcon(button, maximized ? "restore" : "maximize");
  button.title = maximized ? "还原" : "最大化";
  button.setAttribute("aria-label", button.title);
}

function setMaxIcon() {
  let result;
  try {
    result = api().win_state();
  } catch (err) {
    return;                      // 后端还没接上时安静跳过，不影响别的按钮
  }
  // 后端可能返回字符串，也可能是 Promise（pywebview 的 js_api 两种都见过）
  if (result && typeof result.then === "function") {
    result.then(applyWinState).catch(() => {});
  } else if (typeof result === "string") {
    applyWinState(result);
  }
}

/* 最小化 / 最大化 / 关闭：都先停掉冒泡，再让后端去做真正的事
   （关闭按钮走的是后端原有的逻辑：设了「关闭到托盘」就收进托盘） */
function setupWindowControls() {
  on("btnWinMin", "click", (event) => {
    event.stopPropagation();
    try { api().win_minimize(); } catch (err) { /* 后端没接上就什么都不做 */ }
  });

  on("btnWinMax", "click", async (event) => {
    event.stopPropagation();
    try {
      const state = await api().win_toggle_max();
      if (typeof state === "string") applyWinState(state);
    } catch (err) {
      /* 切换失败就保持原图标 */
    }
  });

  on("btnWinClose", "click", (event) => {
    event.stopPropagation();
    try { api().win_close(); } catch (err) { /* 同上 */ }
  });

  renderIcon($("btnWinMin"), "min");
  renderIcon($("btnWinClose"), "close");
  applyWinState("normal");
  setMaxIcon();
}

/* 双击拖动区 = 最大化 / 还原。
   只有带 .pywebview-drag-region 的两个元素能当拖动区（这是 pywebview 约定的类名），
   所以这里用事件委托认这个类名；按钮、输入框、菜单一律不参与，避免点按钮被当成双击。 */
function setupDragGestures() {
  document.addEventListener("dblclick", (event) => {
    const target = event.target;
    if (!target || !target.closest) return;
    if (target.closest("button, input, textarea, select, .menu, .modal")) return;
    const region = target.closest(".pywebview-drag-region");
    if (!region || !api()) return;
    event.preventDefault();
    try {
      const result = api().win_toggle_max();
      if (result && typeof result.then === "function") {
        result.then((state) => { if (typeof state === "string") applyWinState(state); }).catch(() => {});
      }
    } catch (err) {
      /* 后端没接上时忽略这次双击 */
    }
  });
}

/* 右键菜单里补一项「备注该主播…」：index.html 不归本文件管，所以在这里插进去 */
(function addRemarkMenuItem() {
  const menu = $("rowMenu");
  if (!menu || menu.querySelector('[data-row="remark"]')) return;
  const button = document.createElement("button");
  button.dataset.row = "remark";
  button.textContent = "备注该主播…";
  const removeButton = menu.querySelector('[data-row="remove"]');
  const divider = removeButton && removeButton.previousElementSibling;
  menu.insertBefore(button, divider && divider.classList.contains("sep") ? divider : removeButton);
})();

/* ---------------- 备注 ---------------- */

/* 输入弹窗用 JS 动态创建（index.html 不归本文件管），样式沿用 .modal/.dialog，补充规则在 style.css */
function askRemark(userid) {
  const user = state.users.find((item) => item.userid === userid);
  const real = String((user && (user.nickname || user.unique_id)) || "这位主播");
  return new Promise((resolve) => {
    const modal = document.createElement("div");
    modal.className = "modal";
    modal.innerHTML = [
      '<div class="dialog small remark-dialog">',
      '  <div class="dialog-head"><span class="brand-dot"></span><span>备注该主播</span></div>',
      '  <div class="dialog-body column">',
      '    <p class="doc-text remark-tip"></p>',
      '    <input class="remark-input" type="text" maxlength="40" spellcheck="false"',
      '      placeholder="给这位主播写个备注，列表里就显示它" />',
      '  </div>',
      '  <div class="dialog-foot">',
      '    <span class="muted">留空 = 恢复显示原名</span>',
      '    <div class="dialog-buttons">',
      '      <button class="ghost" data-remark="clear">清空</button>',
      '      <button class="ghost" data-remark="cancel">取消</button>',
      '      <button class="primary" data-remark="ok">确定</button>',
      '    </div>',
      '  </div>',
      '</div>',
    ].join("");
    document.body.appendChild(modal);

    const input = modal.querySelector(".remark-input");
    modal.querySelector(".remark-tip").textContent = "原名：" + real;
    input.value = user && typeof user.remark === "string" ? user.remark : "";

    let done = false;
    const finish = (value) => {
      if (done) return;
      done = true;
      document.removeEventListener("keydown", onKey, true);
      modal.remove();
      resolve(value);
    };
    const onKey = (event) => {
      if (event.key === "Escape") { event.preventDefault(); finish(null); }
      else if (event.key === "Enter") { event.preventDefault(); finish(input.value); }
    };
    modal.querySelector('[data-remark="ok"]').onclick = () => finish(input.value);
    modal.querySelector('[data-remark="cancel"]').onclick = () => finish(null);
    modal.querySelector('[data-remark="clear"]').onclick = () => finish("");   // 清空 = 恢复显示原名
    modal.addEventListener("mousedown", (event) => { if (event.target === modal) finish(null); });
    document.addEventListener("keydown", onKey, true);
    input.focus();
    input.select();
  });
}

async function editRemark(userid) {
  const text = await askRemark(userid);
  if (text === null) return;                 // 取消：什么都不改
  const value = text.trim();
  let save = null;
  try { save = api().set_remark; } catch (err) { save = null; }   // 后端还没更新时别把界面卡住
  if (typeof save !== "function") { toast("当前版本还不支持备注"); return; }
  let ok = false;
  try { ok = await save(userid, value); } catch (err) { ok = false; }
  if (!ok) { toast("备注没保存上，请重试"); return; }
  // 先就地改内存里这一行，列表立刻显示；再 refresh() 跟后端快照对齐
  const user = state.users.find((item) => item.userid === userid);
  if (user) user.remark = value;
  renderUsers();
  toast(value ? "备注已保存" : "已恢复显示原名");
  refresh();
}

$("rowMenu").onclick = async (event) => {
  const key = event.target.dataset.row;
  const userid = state.selected;
  if (!key || !userid) return;
  hideMenus();
  if (key === "check") {
    const status = await api().check_now(userid);
    toast("检测结果：" + status);
  }
  if (key === "audio" || key === "video" || key === "vr") {
    await api().set_mode(userid, key);
    toast({ audio: "仅录制音频", video: "默认录制视频", vr: "录制VR直播" }[key]);
  }
  if (key === "home") await api().open_home(userid);
  if (key === "dir") await api().open_dir(userid);
  if (key === "remark") await editRemark(userid);
  if (key === "remove") await removeUser(userid);
  if (key === "stoprec") {
    const user = state.users.find((item) => item.userid === userid);
    const message = user && user.recording
      ? await api().stop_recording(userid)
      : await api().resume_recording(userid);
    toast(message);
  }
  if (key === "copy") {
    const result = await api().copy_stream(userid);
    toast(result.url ? "直播源已复制到剪贴板" : (result.error || "没有拿到直播源"));
  }
  refresh();
};

$("rowActions").onclick = async (event) => {
  const key = event.target.dataset.act;
  if (!key || !state.selected) return;
  if (key === "home") await api().open_home(state.selected);
  if (key === "dir") await api().open_dir(state.selected);
  if (key === "copy") {
    const result = await api().copy_stream(state.selected);
    toast(result.url ? "直播源已复制到剪贴板" : (result.error || "没有拿到直播源"));
  }
};

/* ---------------- 检测录制 ---------------- */

on("btnRun", "click", async () => {
  const result = await api().toggle_run();
  if (result.error) toast(result.error);
  refresh();
});

/* ---------------- 添加主播 ---------------- */

/* 添加主播的全部过程提示（正在解析 / 已添加 / 已经在列表里 / 失败）都走顶部 toast，
   说完就消失，示例列表下面不再留任何常驻文字。 */
function showAddResult(result) {
  const ok = [];
  if (result.added.length) ok.push("已添加：" + result.added.join("、"));
  if ((result.updated || []).length) ok.push("已补上分享链接：" + result.updated.join("、"));
  const notes = [];
  (result.failed || []).forEach((item) => {
    // dup=true 是「已经在列表里」，属于正常情况，不加「失败：」前缀
    notes.push(item.dup ? item.reason : item.line + "：" + item.reason);
  });
  const parts = ok.concat(notes);
  if (parts.length) toast(parts.join("；"), parts.length > 1 ? 4200 : 3000);
}

$("btnAdd").onclick = async () => {
  const text = $("addText").value;
  if (!text.trim()) {
    toast("先粘贴主播主页链接");
    return;
  }
  toast("正在解析…", 1200);
  let result;
  try {
    result = await api().add_users(text);
  } catch (err) {
    toast("解析失败：" + ((err && err.message) || err), 4000);
    return;
  }
  showAddResult(result);
  if (result.added.length || (result.updated || []).length) $("addText").value = "";
  refresh();
};

/* ---------------- 设置 ---------------- */

function readSettings() {
  return {
    intervalTime: Number($("setInterval").value) || 15,
    save: $("setSave").value,
    streamType: $("setStream").value,
    fileTime: Number($("setFileTime").value),
    fileSize: Number($("setFileSize").value),
    recTime: Number($("setRecTime").value),
    recFileSize: Number($("setRecSize").value),
    checkPK: $("setCheckPK").classList.contains("on"),
    hideTime: $("setHideTime").classList.contains("on"),
    hideFileSize: $("setHideSize").classList.contains("on"),
    showUserCount: $("setShowCount").classList.contains("on"),
    mp3play: $("setMp3").classList.contains("on"),
    closeToTray: $("setCloseTray").classList.contains("on"),
    autoCheck: $("setAutoCheck").classList.contains("on"),
    autoStart: $("setAutoStart").classList.contains("on"),
    silentStart: $("setSilentStart").classList.contains("on"),
    // 主题是顶栏 #btnTheme 管的，设置面板里没有它的控件；这里带上当前值，
    // 万一后端按「整份替换」写配置，也不会把用户刚选的主题覆盖回默认值。
    theme: (state.config && state.config.theme) || currentTheme || "dark",
  };
}

function openSettings() {
  const cfg = state.config;
  showSettingsPane("basic");
  $("setInterval").value = cfg.intervalTime;
  $("setSave").value = cfg.save;
  $("setStream").value = cfg.streamType;
  $("setFileTime").value = cfg.fileTime;
  $("setFileSize").value = cfg.fileSize;
  $("setRecTime").value = cfg.recTime;
  $("setRecSize").value = cfg.recFileSize;
  setSwitch("setCheckPK", cfg.checkPK);
  setSwitch("setHideTime", cfg.hideTime);
  setSwitch("setHideSize", cfg.hideFileSize);
  setSwitch("setShowCount", cfg.showUserCount);
  setSwitch("setMp3", cfg.mp3play);
  setSwitch("setCloseTray", cfg.closeToTray);
  setSwitch("setAutoCheck", cfg.autoCheck);
  setSwitch("setAutoStart", cfg.autoStart);
  setSwitch("setSilentStart", cfg.silentStart);
  updateSliderLabels();
  $("settingsModal").classList.remove("hidden");
}

function showSettingsPane(name) {
  document.querySelectorAll(".side-item").forEach((item) =>
    item.classList.toggle("active", item.dataset.pane === name));
  $("paneBasic").classList.toggle("hidden", name !== "basic");
  $("paneAbout").classList.toggle("hidden", name !== "about");
}

document.querySelectorAll(".side-item").forEach((item) => {
  item.onclick = () => showSettingsPane(item.dataset.pane);
});

function setSwitch(id, value) {
  $(id).classList.toggle("on", !!value);
}

function updateSliderLabels() {
  const fileTime = Number($("setFileTime").value);
  const fileSize = Number($("setFileSize").value);
  const recTime = Number($("setRecTime").value);
  const recSize = Number($("setRecSize").value);
  $("valFileTime").textContent = fileTime ? fileTime + "分钟" : "不限制";
  $("valFileSize").textContent = fileSize ? fileSize + "MB" : "不限制";
  $("valRecTime").textContent = recTime ? recTime + "分钟" : "不限制";
  $("valRecSize").textContent = recSize ? recSize + "MB" : "不分段";
}

["setFileTime", "setFileSize", "setRecTime", "setRecSize"].forEach((id) => {
  $(id).oninput = updateSliderLabels;
});

["setCheckPK", "setHideTime", "setHideSize", "setShowCount", "setMp3",
 "setCloseTray", "setAutoCheck", "setAutoStart", "setSilentStart"].forEach((id) => {
  $(id).onclick = () => $(id).classList.toggle("on");
});

$("btnPickFolder").onclick = async () => {
  const folder = await api().pick_folder($("setSave").value);
  if (folder) $("setSave").value = folder;
};

$("btnApplySettings").onclick = async () => {
  await api().apply_settings(readSettings());
  $("settingsModal").classList.add("hidden");
  toast("设置已应用");
  refresh();
};

document.querySelectorAll("[data-close]").forEach((button) => {
  button.onclick = () => $(button.dataset.close).classList.add("hidden");
});

/* ---------------- 视频转码 ---------------- */

async function loadFiles() {
  const files = await api().list_recordings();
  const list = $("fileList");
  list.innerHTML = "";
  state.transcodeFile = null;
  $("btnDoTranscode").disabled = true;
  if (!files.length) {
    const li = document.createElement("li");
    li.className = "muted";
    li.textContent = "保存目录里还没有 ts / flv 录像";
    list.appendChild(li);
    return;
  }
  files.forEach((file) => {
    const li = document.createElement("li");
    const name = document.createElement("span");
    name.className = "fname";
    name.textContent = file.name;
    const meta = document.createElement("span");
    meta.className = "fmeta";
    meta.textContent = file.folder + " · " + fmtSize(file.size);
    li.appendChild(name);
    li.appendChild(meta);
    li.onclick = () => {
      list.querySelectorAll("li").forEach((item) => item.classList.remove("selected"));
      li.classList.add("selected");
      state.transcodeFile = file.path;
      $("btnDoTranscode").disabled = false;
      $("transcodeHint").textContent = "已选择：" + file.name;
    };
    list.appendChild(li);
  });
}

function openTranscode() {
  $("transcodeModal").classList.remove("hidden");
  loadFiles();
}

$("btnRefreshFiles").onclick = loadFiles;

$("btnDoTranscode").onclick = async () => {
  if (!state.transcodeFile) return;
  $("btnDoTranscode").disabled = true;
  $("transcodeHint").textContent = "转码中…";
  const result = await api().transcode(state.transcodeFile);
  $("transcodeHint").textContent = result.ok ? "完成：" + result.target : "失败：" + result.error;
  $("btnDoTranscode").disabled = false;
};

on("btnTranscode", "click", openTranscode);

/* ---------------- 启动 ---------------- */

let booted = false;

function boot() {
  // pywebviewready 有极小概率早于脚本注册监听，没等到就自己轮询，别让列表一直空着
  if (booted || !window.pywebview || !window.pywebview.api) return false;
  booted = true;
  setupTheme();
  setupWindowControls();
  setupDragGestures();
  refresh();
  state.timer = setInterval(refresh, 1000);
  return true;
}

window.addEventListener("pywebviewready", boot);

document.addEventListener("DOMContentLoaded", () => {
  if (boot()) return;
  const wait = setInterval(() => {
    if (boot()) clearInterval(wait);
  }, 250);
  setTimeout(() => clearInterval(wait), 30000);
});
