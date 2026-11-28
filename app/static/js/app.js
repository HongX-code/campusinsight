/* 高校智讯 CampusInsight · 前端逻辑（原生 JS，无构建步骤）
 * UI v2.0：Arctic Glass Design System —— 学生端智能工作台 + 信息治理后台
 * 数据全部来自真实 REST 接口，界面不写死演示数字。
 */
const API_BASE = "";

const state = {
  view: "home",
  categories: [],
  users: [],
  currentUser: null,
  profileEditing: false,
  matchLevel: "",
  page: 1,
  size: 12,
  keyword: "",
  category: "",
  genre: "",
  review: "",
  quick: "all",
  total: 0,
  currentNotice: null,
  dashboardCache: null,
  /* 每个视图最后一次取数的时刻，用于抑制「连点同一导航项」引发的重复请求与骨架闪烁 */
  renderedAt: {},
  asking: false,
  lastQuestion: "",
  qaTab: "chat",
  pwa: {},
  /* 多端接入与冲突治理的视图状态 */
  mobileStatus: null,
  conflictSeverity: "",
  conflictShowResolved: false,
};

/* ---------------- 角色账号登录态 ----------------
 * 登录角色决定"我的匹配"用谁的画像计算；登录态存 localStorage，刷新后保持。
 * 这与旧的"匹配页下拉框切换当前用户"是两种范式：这里先登录后匹配，匹配页只读展示登录身份。
 */
const LOGIN_KEY = "campusinsight.login.userId";
function roleLabel(u) {
  const grade = u.grade ? u.grade + " · " : "";
  const major = u.major || "";
  return `${grade}${major}`;
}
function loadLogin(users) {
  if (!users || !users.length) return null;
  const saved = Number(localStorage.getItem(LOGIN_KEY));
  const found = users.find((u) => u.id === saved);
  if (found) { state.currentUser = found; return found; }
  /* 无有效登录态：不默认替用户登录，保持未登录，等用户在头像处选择角色 */
  state.currentUser = null;
  return null;
}
function setLogin(userId) {
  const u = state.users.find((x) => x.id === userId) || null;
  if (u) {
    state.currentUser = u;
    localStorage.setItem(LOGIN_KEY, String(u.id));
  } else {
    state.currentUser = null;
    localStorage.removeItem(LOGIN_KEY);
  }
  refreshIdentityUI();
  renderLoginRoles();
  /* 登录角色改变后，所有按当前用户取数的视图都要重算 */
  renderMatch();
  if (state.view === "home") renderHome();
  if (state.view === "tasks") renderTasks();
  return u;
}
function refreshIdentityUI() {
  const u = state.currentUser;
  const chip = $("#userChip");
  const nameEl = $("#topUserName");
  const avatarEl = $("#topAvatar");
  if (nameEl) nameEl.textContent = u ? u.name : "登录";
  if (avatarEl) avatarEl.textContent = u ? u.name.slice(0, 1) : "·";
  if (chip) {
    chip.classList.toggle("logged-in", !!u);
    chip.title = u ? `已登录：${u.name}（点击切换角色账号）` : "登录 / 切换角色账号";
  }
  const idEl = $("#matchIdentityName");
  const idBox = $("#matchIdentity");
  if (idEl) idEl.textContent = u ? `当前登录：${u.name} · ${roleLabel(u)}` : "未登录 · 请在右上角选择角色账号";
  if (idBox) idBox.classList.toggle("anonymous", !u);
}
function openLogin() {
  renderLoginRoles();
  $("#loginMask")?.classList.add("open");
  // 弹窗打开后把焦点移进对话框，键盘用户才能继续用 Tab 选择角色
  setTimeout(() => { $("#loginRoles .role-card")?.focus(); }, 30);
}
function closeLogin() {
  const mask = $("#loginMask");
  if (!mask || !mask.classList.contains("open")) return;
  mask.classList.remove("open");
  // 关闭后把焦点还给触发按钮，避免焦点落在 body 上丢失键盘上下文
  $("#userChip")?.focus();
}
function renderLoginRoles() {
  const box = $("#loginRoles");
  if (!box) return;
  const users = state.users || [];
  if (!users.length) { box.innerHTML = '<div class="hint">暂无可登录角色，请稍后重试。</div>'; return; }
  box.innerHTML = users.map((u) => {
    const active = state.currentUser && state.currentUser.id === u.id;
    return `<button type="button" class="role-card ${active ? "active" : ""}" data-login-id="${u.id}">
      <span class="role-avatar">${esc(u.name.slice(0, 1))}</span>
      <span class="role-meta"><strong>${esc(u.name)}</strong><span>${esc(roleLabel(u))} · 关注 ${esc((u.interests || []).join("、") || "未设置")}</span></span>
      <svg aria-hidden="true" focusable="false" class="role-check"><use href="#i-check"/></svg>
    </button>`;
  }).join("");
  $$(".role-card", box).forEach((c) => c.addEventListener("click", () => {
    setLogin(Number(c.dataset.loginId));
    closeLogin();
    toast(`已以「${state.currentUser.name}」身份登录，匹配已更新`);
  }));
}

/* ---------------- 基础工具 ---------------- */
/* 短 TTL 内存缓存：首页与数据总览都会拉 dashboard/stats，来回切换视图时
   没有必要每次都打一遍网络。只缓存 GET，写请求成功后整体失效，
   保证「改完立刻看到新数据」。 */
const MEM_TTL = 12000;
const memCache = new Map();
function cacheGet(key) {
  const hit = memCache.get(key);
  if (!hit) return null;
  if (Date.now() - hit.at > hit.ttl) { memCache.delete(key); return null; }
  return hit.data;
}
function cachePut(key, data, ttl) { memCache.set(key, { at: Date.now(), data, ttl }); }
function cacheFlush() { memCache.clear(); }

async function api(path, options = {}) {
  const opt = { headers: { "Content-Type": "application/json" }, ...options };
  const method = String(opt.method || "GET").toUpperCase();
  /* cache: 传毫秒数可覆盖默认 TTL，传 0 表示这次不走缓存 */
  const ttl = opt.cache === undefined ? (method === "GET" ? MEM_TTL : 0) : opt.cache;
  delete opt.cache;
  if (ttl > 0) {
    const cached = cacheGet(path);
    if (cached !== null) return cached;
  }
  if (opt.body && typeof opt.body !== "string") opt.body = JSON.stringify(opt.body);
  /* 移动网络下请求可能长时间悬挂；统一加超时，避免按钮一直转圈 */
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), opt.timeout || 60000);
  delete opt.timeout;
  if (!opt.signal) opt.signal = ctrl.signal;
  let res;
  try {
    res = await fetch(API_BASE + path, opt);
  } catch (err) {
    if (err && err.name === "AbortError") throw new Error("请求超时，请检查网络后重试");
    throw new Error(navigator.onLine === false ? "当前处于离线状态，请联网后重试" : "网络请求失败，请稍后重试");
  } finally {
    clearTimeout(timer);
  }
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
  if (!res.ok) {
    const msg = (data && (data.detail || data.error)) || `请求失败（${res.status}）`;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  /* 写操作成功后清空读缓存：新增/删除/解析都会改变列表与统计，
     留着旧快照会让用户以为「点了没反应」。 */
  if (method !== "GET") cacheFlush();
  /* 返回的是同一份对象引用，调用方不要就地改写缓存内容 */
  if (ttl > 0 && method === "GET") cachePut(path, data, ttl);
  return data;
}

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

let toastTimer = null;
function toast(msg, isError = false) {
  const el = $("#toast");
  el.textContent = msg;
  el.className = "toast show" + (isError ? " error" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = "toast"; }, 2800);
}

function daysLeft(deadline) {
  if (!deadline) return null;
  const d = new Date(deadline + "T00:00:00");
  // 历史脏日期（例如人工录入的 "2026-11-1"）会构造出 Invalid Date，
  // 若直接参与运算会得到 NaN，界面上就出现「还有 NaN 天」。
  if (isNaN(d.getTime())) return null;
  const today = new Date(); today.setHours(0, 0, 0, 0);
  return Math.round((d - today) / 86400000);
}

/* 画像里的年级写法五花八门（博二 / 研三 / 大三 / 2024级硕士），而时间轴节点上
   的身份词只有四个（博士研究生 / 硕士研究生 / 本科生 / 专科生）。这里做同一套
   归一，保证「本人需要办」的判定不会因为写法不同而全部落空。 */
function roleFromGrade(grade) {
  const g = String(grade || "");
  if (!g) return "";
  if (g.includes("博士") || g.includes("博一") || g.includes("博二")
      || g.includes("博三") || g.includes("博四") || g.includes("博五")) return "博士研究生";
  if (g.includes("硕士") || /研[一二三]/.test(g) || g.includes("研究生")) return "硕士研究生";
  if (g.includes("专科") || g.includes("高职")) return "专科生";
  if (g.includes("本科") || /大[一二三四五]/.test(g)) return "本科生";
  return "";
}

/* 截止时间视觉系统：正常绿 / 临近橙 / 紧急红 / 已截止灰 */
function deadlineInfo(deadline) {
  if (!deadline) return { cls: "dl-none", text: "未识别截止", date: "" };
  const n = daysLeft(deadline);
  if (n === null) return { cls: "dl-none", text: "日期待核对", date: deadline };
  if (n < 0) return { cls: "dl-over", text: "已截止", date: deadline };
  if (n === 0) return { cls: "dl-urgent", text: "今天截止", date: deadline };
  if (n === 1) return { cls: "dl-urgent", text: "明天截止", date: deadline };
  if (n <= 3) return { cls: "dl-urgent", text: `还有 ${n} 天`, date: deadline };
  if (n <= 7) return { cls: "dl-near", text: `还有 ${n} 天`, date: deadline };
  return { cls: "dl-normal", text: `还有 ${n} 天`, date: deadline };
}

function dueChip(deadline) {
  const d = deadlineInfo(deadline);
  return `<span class="due-chip ${d.cls}">${esc(d.text)}</span>`;
}

/* 兼容数据总览里的紧凑标签 */
function deadlineTag(deadline) {
  const d = deadlineInfo(deadline);
  const map = { "dl-urgent": "bad", "dl-near": "warn", "dl-normal": "ok", "dl-over": "", "dl-none": "" };
  return `<span class="tag ${map[d.cls] || ""}">${esc(d.text)}</span>`;
}

function engineTag(a) {
  if (!a) return '<span class="tag">未解析</span>';
  const label = a.engine === "llm" ? "大模型解析" : "规则引擎";
  return `<span class="tag">${label}</span>`;
}

const icon = (name) => `<svg aria-hidden="true" focusable="false"><use href="#i-${name}"/></svg>`;

/* ---------------- 统一失败态 ----------------
   只渲染一句「加载失败：xxx」等于把用户堵在死路上：同一视图 15 秒内不会重新取数，
   刷新整页又太重。这里统一成「原因 + 重试按钮」，并把该视图的取数时刻清零，
   让下一次点击导航就能真正重来。 */
const retryHooks = new Map();
function failBlock(target, e, key, retry) {
  if (retry) retryHooks.set(key, retry);
  if (key) state.renderedAt[key] = 0;
  const targets = Array.isArray(target) ? target : [target];
  const msg = e && e.message ? e.message : "未知错误";
  targets.forEach((t) => {
    const el = typeof t === "string" ? $(t) : t;
    if (!el) return;
    el.innerHTML = `<div class="fail-box">
      <div class="fail-title">${icon("alert")}这页数据暂时没取到</div>
      <div class="fail-msg">${esc(msg)}</div>
      <button class="btn sm" onclick="retryLoad('${key || ""}')">重试</button>
    </div>`;
  });
}
function retryLoad(key) {
  const fn = retryHooks.get(key);
  if (!fn) return;
  toast("正在重新读取…");
  fn();
}

/* 统一的占位骨架：同一形状的灰块比「加载中…」文字更稳——
   内容到位时不会整块跳一下，读屏也不会读到一串临时文案。
   lines 为文字块条数，avatar/cards 控制块形状。 */
function skeletonRows(lines = 3, cls = "") {
  return `<div class="sk-block ${cls}" aria-hidden="true">${
    Array.from({ length: lines }, (_, i) =>
      `<div class="skeleton sk-line" style="width:${i === 0 ? 62 : i === lines - 1 ? 45 : 88}%"></div>`).join("")
  }</div>`;
}
function skeletonCards(n = 3, height = 96, cls = "sk-grid") {
  return Array.from({ length: n }, () => `<div class="skeleton" style="height:${height}px"></div>`).join("");
}

/* 写操作的统一「提交中」锁定：同一颗按钮在请求返回前不再接受点击，
   既避免重复提交，也让用户看到「已在处理」，不再连点。
   失败时把按钮文案还原，成功则交给调用方决定是否重绘。 */
async function withBusy(btn, text, fn) {
  if (!btn) return fn();
  if (btn.dataset.busy === "1") return undefined;
  const oldHTML = btn.innerHTML;
  const wasDisabled = btn.disabled;
  btn.dataset.busy = "1";
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span>${text}`;
  try {
    return await fn();
  } finally {
    delete btn.dataset.busy;
    /* 按钮可能已被重新渲染替换掉（例如列表刷新）；此时只跳过还原，
       绝不能在这里 return——finally 里的 return 会吞掉 fn 抛出的异常，
       让调用方以为操作成功。 */
    if (btn.isConnected) {
      btn.disabled = wasDisabled;
      btn.innerHTML = oldHTML;
    }
  }
}

/* ---------------- 视图切换 ---------------- */
const VIEW_META = {
  home: ["智能工作台", "让校园信息，从「看见」到「看懂」，再到「行动」"],
  notices: ["通知中心", "检索、筛选与查看每一条通知的解析状态"],
  sources: ["采集中心", "按栏目批量抓取公开通知，预览确认后再入库"],
  match: ["我的匹配", "结合你的专业、年级与兴趣，给出三档结论与逐条理由"],
  qa: ["AI 助手", "只基于已收录通知作答，每个结论都能回溯原文"],
  tasks: ["我的任务", "把关键通知加入时间管理，按本人身份看自己的截止时间轴"],
  dashboard: ["数据总览", "已收录通知的结构化解析与处理质量总览"],
  mobile: ["多端接入", "Web、PWA、微信小程序、系统日历四个入口共享同一套后端与画像"],
  conflicts: ["冲突治理", "横向比对同源通知，找出单条解析看不见的矛盾字段"],
};

function switchView(name, push = true) {
  if (!VIEW_META[name]) return;
  /* 再次点击当前导航项不应该重新打一遍接口：切视图是有代价的（每个视图都要取数），
     而用户连点的用意通常是「回到这一页」，不是「刷新」。刚渲染过就不重复渲染，
     既省请求也避免「骨架→内容」再闪一次；超过刷新窗口后再点才会真正重新取数。 */
  const VIEW_REFRESH_MS = 15000;
  const same = state.view === name && Date.now() - (state.renderedAt[name] || 0) < VIEW_REFRESH_MS;
  state.view = name;
  /* aria-current 让读屏能报出「当前是哪个视图」，
     纯视觉的 .active 类对辅助技术不可见。 */
  $$(".nav-item").forEach((b) => {
    const on = b.dataset.view === name;
    b.classList.toggle("active", on);
    if (on) b.setAttribute("aria-current", "page");
    else b.removeAttribute("aria-current");
  });
  $$("#mobileNav button").forEach((b) => {
    const on = b.dataset.view === name;
    b.classList.toggle("active", on);
    if (on) b.setAttribute("aria-current", "page");
    else b.removeAttribute("aria-current");
  });
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === "view-" + name));
  const scrollEl = document.querySelector(".scroll");
  if (scrollEl) scrollEl.classList.toggle("scroll--qa", name === "qa");
  const [title, desc] = VIEW_META[name] || ["", ""];
  $("#viewTitle").textContent = title;
  $("#viewDesc").textContent = desc;
  $("#btnBatchAnalyze").style.display = (name === "notices" || name === "dashboard") ? "" : "none";
  $(".scroll").scrollTop = 0;
  window.scrollTo(0, 0);
  /* 视图写进 hash：PWA 快捷方式、刷新、跨端分享都能直达同一页；
     用 pushState 让 Android / HarmonyOS 的返回手势按页面层级回退。 */
  if (location.hash !== "#" + name) {
    if (push) history.pushState({ view: name }, "", "#" + name);
    else history.replaceState({ view: name }, "", "#" + name);
  }

  /* 同一视图的重复点击到此为止：滚动复位已经做了，再往下就是重复取数 */
  if (same) return;

  /* 先记下「这一页已经开始取数」的时刻，成功失败都以它为准；
     取数失败时由 failBlock 把它清零，于是「连点抑制」自然失效，
     用户再点一次导航就能真正重来，不必刷新整页。 */
  state.renderedAt[name] = Date.now();

  if (name === "home") renderHome();
  if (name === "dashboard") renderDashboard();
  if (name === "notices") renderNotices();
  if (name === "sources") renderSources();
  if (name === "match") renderMatch();
  if (name === "tasks") renderTasks();
  if (name === "mobile") renderMobile();
  if (name === "conflicts") renderConflicts();
  if (name === "qa" && state.qaTab === "chat" && window.innerWidth >= 768) {
    setTimeout(() => $("#qaInput").focus(), 60);
  }
}

function viewFromHash() {
  const name = (location.hash || "").replace(/^#/, "");
  return VIEW_META[name] ? name : "home";
}

/* ---------------- 首页 · 智能工作台 ---------------- */
async function renderHome() {
  const uid = state.currentUser && state.currentUser.id;
  const name = state.currentUser ? state.currentUser.name : "同学";
  const hour = new Date().getHours();
  const hi = hour < 11 ? "早上好" : hour < 18 ? "下午好" : "晚上好";
  $("#homeGreeting").textContent = `${hi}，${name}`;

  $("#homeHero").innerHTML = '<div class="hero skeleton" style="height:190px"></div>';
  $("#homeStats").innerHTML = '<div class="stat-card skeleton" style="height:112px"></div>'.repeat(4);
  $("#homeOpps").innerHTML = '<div class="opp-card skeleton" style="height:150px"></div>'.repeat(2);

  try {
    /* 各区块独立取数：某个接口临时不可用时只让对应区块降级，
       不要因为一处失败就让整个工作台只剩一句「加载失败」。 */
    const settled = await Promise.allSettled([
      api("/api/dashboard/stats"),
      uid ? api(`/api/users/${uid}/matches?level=match&only_open=true`) : Promise.resolve({ items: [], counts: {} }),
      uid ? api(`/api/users/${uid}/agenda?days=7`) : Promise.resolve([]),
      uid ? api(`/api/users/${uid}/tasks`) : Promise.resolve([]),
    ]);
    const pick = (i, fallback) => settled[i].status === "fulfilled" ? settled[i].value : fallback;
    const emptyOverview = {
      total_notices: 0, analyzed: 0, unanalyzed: 0, need_review: 0, reviewed: 0,
      upcoming_30d: 0, avg_confidence: 0, users: 0, tasks: 0,
    };
    const stats = pick(0, { overview: emptyOverview });
    const matches = pick(1, { items: [], counts: {} });
    const agenda = pick(2, []);
    const tasks = pick(3, []);
    const rejected = settled.filter((r) => r.status === "rejected").length;
    if (rejected === settled.length) {
      /* 四路全挂就别硬撑成「0 条通知」：那样用户会以为系统里真的没数据。
         交给统一失败态，给原因和重试。 */
      failBlock("#homeHero", new Error("接口暂时不可用，请稍后重试"), "home", renderHome);
      $("#homeStats").innerHTML = "";
      $("#homeOpps").innerHTML = "";
      $("#homeDue").innerHTML = '<div class="empty">暂时无法加载近期截止事项</div>';
      $("#bellBadge").style.display = "none";
      return;
    }
    if (rejected > 0) {
      toast("部分数据暂时不可用，已显示其余可用内容", true);
    }
    state.dashboardCache = stats;
    const o = stats.overview;
    const top = (matches.items || []).slice(0, 3);

    /* 用本次真实取数的时刻替代写死的「刚刚」：
       「刚刚」是静态假象，与“数据全部来自真实 REST 接口”的承诺相矛盾。 */
    const refreshed = new Date();
    const refreshedAt = `${String(refreshed.getHours()).padStart(2, "0")}:${String(refreshed.getMinutes()).padStart(2, "0")}`;

    /* Hero：只陈述系统里真实存在的量 */
    $("#homeHero").innerHTML = `
      <div class="hero">
        <div class="hero-head">
          <span class="hero-tag">${icon("spark")} AI 智能洞察</span>
          <span class="hero-updated">数据读取于 ${refreshedAt} · 基于 ${o.total_notices} 条已收录通知</span>
        </div>
        <div class="hero-title">已为你结构化解析 <em>${o.analyzed}</em> 条校园通知</div>
        <div class="hero-sub">从分散公告到可执行信息：识别文体、抽取 14 类字段、绑定原文依据，再与你的画像匹配。</div>
        <div class="hero-metrics">
          <div class="hero-metric hl"><b>${(matches.items || []).length}</b><span>条符合你的机会</span></div>
          <div class="hero-metric warn"><b>${agenda.length}</b><span>项 7 天内截止</span></div>
          <div class="hero-metric"><b>${o.need_review}</b><span>条待人工复核</span></div>
        </div>
        <div class="hero-cta">
          <button class="btn primary" onclick="switchView('match')">查看我的推荐 ${icon("arrow")}</button>
          <button class="btn" onclick="switchView('notices')">浏览全部通知</button>
        </div>
      </div>`;

    /* 4 个 Stats Card */
    const todoN = tasks.filter((t) => t.status !== "done").length;
    const doingN = tasks.filter((t) => t.status === "doing").length;
    const statsCards = [
      { label: "新通知", value: o.total_notices, sub: `已解析 ${o.analyzed} 条 · 待解析 ${o.unanalyzed} 条`, ico: "notice", cls: "" },
      { label: "我的匹配", value: (matches.items || []).length, sub: `其中 85 分以上 ${(matches.items || []).filter((m) => m.score >= 85).length} 条`, ico: "match", cls: "ai" },
      { label: "即将截止", value: agenda.length, sub: `未来 30 天共 ${o.upcoming_30d} 项`, ico: "clock", cls: "warn" },
      { label: "待完成任务", value: todoN, sub: `${doingN} 个进行中 · 共 ${tasks.length} 条`, ico: "task", cls: "ok" },
    ];
    $("#homeStats").innerHTML = statsCards.map((c) => `
      <div class="stat-card">
        <div class="stat-top">
          <span class="stat-label">${esc(c.label)}</span>
          <span class="stat-icon ${c.cls}">${icon(c.ico)}</span>
        </div>
        <div class="stat-value">${c.value}</div>
        <div class="stat-sub">${esc(c.sub)}</div>
      </div>`).join("");

    /* 推荐机会：分数 + 逐条理由，不做无解释的数字 */
    $("#homeOpps").innerHTML = top.length
      ? top.map((m) => {
          const score = Math.max(5, Math.min(98, m.score));
          const lv = score >= 85 ? "" : score >= 72 ? "mid" : "low";
          const good = (m.reasons || []).filter((r) => r.type === "good").slice(0, 4);
          return `<div class="opp-card ${lv}" onclick="openNotice(${m.notice.id})">
            <div class="opp-top">
              <span class="tag" data-cat="${esc(m.notice.category)}">${esc(m.notice.category)}</span>
              <span class="tag ai">AI 匹配</span>
              <span class="opp-score ${lv}"><b>${score} 分</b><span>匹配度</span></span>
            </div>
            <div class="opp-title">${esc(m.notice.title)}</div>
            <div class="opp-meta"><span>${esc((m.notice.summary || "").slice(0, 40))}</span><span>规则加权匹配度 · 非录取概率</span></div>
            <div class="match-bar"><i style="width:${score}%"></i></div>
            <div class="opp-reasons">
              ${good.length
                ? good.map((r) => `<span class="opp-reason">${icon("check")}${esc(r.text)}</span>`).join("")
                : `<span class="opp-reason">${icon("check")}${esc((m.reasons[0] || {}).text || "依据结构化字段判定")}</span>`}
            </div>
            <div class="opp-foot">
              <span class="hint">点击查看详情与原文依据</span>
              <button class="btn sm" onclick="event.stopPropagation();addTaskFrom(${m.notice.id}, this)">加入任务</button>
            </div>
          </div>`;
        }).join("")
      : `<div class="card empty">当前没有高匹配机会。可以先到「我的匹配」调整筛选，或在「采集中心」补充新通知。</div>`;

    /* 即将截止 */
    $("#homeDue").innerHTML = agenda.length
      ? agenda.slice(0, 5).map((x) => `
        <div class="due-item" onclick="openNotice(${x.notice_id})">
          <div class="due-main">
            <div class="due-title">${esc(x.title)}</div>
            <div class="due-meta">${esc(x.category)} · ${esc(x.deadline)} 截止</div>
          </div>
          ${dueChip(x.deadline)}
        </div>`).join("")
      : '<div class="empty">未来 7 天没有截止事项</div>';

    /* 铃铛角标 */
    const bell = $("#bellBadge");
    if (agenda.length) { bell.style.display = ""; bell.textContent = agenda.length; }
    else bell.style.display = "none";
  } catch (e) {
    /* 首页四块是独立取数的，能走到这里说明连汇总都没成功；
       统一失败态带重试，别让用户只能刷新整页。 */
    failBlock("#homeHero", e, "home", renderHome);
    $("#homeStats").innerHTML = "";
    $("#homeOpps").innerHTML = "";
    $("#homeDue").innerHTML = '<div class="empty">暂时无法加载近期截止事项</div>';
    $("#bellBadge").style.display = "none";
  }
}

async function addTaskFrom(noticeId, btn) {
  const uid = state.currentUser && state.currentUser.id;
  if (!uid) { toast("请先点击右上角头像登录角色账号", true); return; }
  /* 锁定发起点击的按钮：连点时只会有一次请求落到后端 */
  await withBusy(btn, "加入中…", async () => {
    try {
      await api(`/api/users/${uid}/tasks`, { method: "POST", body: { notice_id: noticeId } });
      toast("已加入我的任务");
      renderHome();
    } catch (e) { toast(e.message, true); }
  });
}

/* ---------------- 数据总览（信息治理） ---------------- */
async function renderDashboard() {
  /* 与首页同一套骨架：先摆出四张 KPI 的灰块，再原地换成数字，
     避免「一行加载中」被替换成整块图表时的跳动。 */
  $("#kpiGrid").innerHTML = `<div class="kpi-row">${skeletonCards(4, 96, "")}</div>`;
  try {
    const d = await api("/api/dashboard/stats");
    state.dashboardCache = d;
    const o = d.overview;

    $("#kpiGrid").innerHTML = [
      kpi("已收录通知", o.total_notices, `已解析 ${o.analyzed} 条 · 待解析 ${o.unanalyzed} 条`, "accent"),
      kpi("7 天内截止", o.upcoming_7d, `未来 30 天共 ${o.upcoming_30d} 项`, o.upcoming_7d > 0 ? "warn" : ""),
      kpi("待人工复核", o.need_review, `已复核 ${o.reviewed} 条`, o.need_review > 0 ? "bad" : ""),
      kpi("平均置信度", (o.avg_confidence * 100).toFixed(1) + "%", `覆盖 ${o.users} 位用户 · ${o.tasks} 条待办`, ""),
    ].join("");

    const maxCat = Math.max(1, ...d.categories.map((c) => c.value));
    $("#catBars").innerHTML = d.categories
      .filter((c) => c.value > 0 || c.name !== "其他")
      .map((c) => `
        <div class="bar-row">
          <div class="name">${esc(c.name)}</div>
          <div class="bar-track"><div class="bar-fill" style="width:${(c.value / maxCat) * 100}%"></div></div>
          <div class="num">${c.value}</div>
        </div>`).join("") || '<div class="empty">暂无数据</div>';

    const maxTrend = Math.max(1, ...d.trend.map((t) => t.count));
    const trendTotal = d.trend.reduce((s, t) => s + t.count, 0);
    $("#trendChart").innerHTML = trendTotal === 0
      ? '<div class="trend-empty">近 7 天暂无新增记录，录入或采集通知后这里会显示录入趋势</div>'
      : d.trend.map((t) => `
      <div class="trend-col" title="${esc(t.date)}：新增 ${t.count} 条">
        <div class="trend-val">${t.count || ""}</div>
        <div class="trend-bar" style="height:${Math.max(4, (t.count / maxTrend) * 100)}%"></div>
        <div class="trend-label">${esc(t.date)}</div>
      </div>`).join("");

    const modes = d.modes || [];
    const maxMode = Math.max(1, ...modes.map((m) => m.value));
    const modeTotal = modes.reduce((s, m) => s + m.value, 0) || 1;
    $("#modeBars").innerHTML = modes.map((m) => `
      <div class="bar-row">
        <div class="name">${esc(m.name)}</div>
        <div class="bar-track"><div class="bar-fill" style="width:${(m.value / maxMode) * 100}%"></div></div>
        <div class="num">${m.value} · ${Math.round((m.value / modeTotal) * 100)}%</div>
      </div>`).join("") || '<div class="empty">暂无数据</div>';

    const genres = d.genres || [];
    const maxGenre = Math.max(1, ...genres.map((g) => g.value));
    $("#genreBars").innerHTML = genres.map((g) => `
      <div class="bar-row">
        <div class="name">${esc(g.name)}</div>
        <div class="bar-track"><div class="bar-fill" style="width:${(g.value / maxGenre) * 100}%"></div></div>
        <div class="num">${g.value}</div>
      </div>`).join("") || '<div class="empty">暂无解析结果</div>';

    $("#deadlineList").innerHTML = d.deadlines.length
      ? d.deadlines.slice(0, 6).map((x) => `
        <div class="list-item" onclick="openNotice(${x.notice_id})">
          <div class="li-main">
            <div class="li-title">${esc(x.title)}</div>
            <div class="li-meta"><span>${esc(x.category)}</span><span>${esc(x.deadline)}</span></div>
          </div>
          ${deadlineTag(x.deadline)}
        </div>`).join("")
      : '<div class="empty">近 7 天没有即将截止的事项</div>';

    const conf = Math.round(o.avg_confidence * 100);
    const analyzedRate = o.total_notices ? Math.round((o.analyzed / o.total_notices) * 100) : 0;
    const reviewedRate = o.analyzed ? Math.round((o.reviewed / o.analyzed) * 100) : 0;
    $("#qualityBox").innerHTML = `
      <div>
        <div class="q-row"><span>解析覆盖率</span><b>${analyzedRate}%</b></div>
        <div class="q-bar"><i style="width:${analyzedRate}%"></i></div>
      </div>
      <div>
        <div class="q-row"><span>平均置信度</span><b>${conf}%</b></div>
        <div class="q-bar"><i style="width:${conf}%"></i></div>
      </div>
      <div>
        <div class="q-row"><span>人工复核完成率</span><b>${reviewedRate}%</b></div>
        <div class="q-bar"><i style="width:${reviewedRate}%"></i></div>
      </div>
      <div class="li-meta" style="margin-top:4px">
        ${d.engines.map((e) => `<span>${e.engine === "llm" ? "大模型" : "规则引擎"}：${e.count} 条</span>`).join("")}
        <span>已过截止：${o.overdue} 条</span>
      </div>`;
  } catch (e) {
    failBlock("#kpiGrid", e, "dashboard", renderDashboard);
  }
}

function kpi(label, value, sub, cls) {
  return `<div class="kpi ${cls}">
    <div class="label">${esc(label)}</div>
    <div class="value">${esc(value)}</div>
    <div class="sub">${esc(sub)}</div>
  </div>`;
}

/* ---------------- 通知中心 ---------------- */
function renderCategoryChips() {
  const box = $("#categoryChips");
  if (!box || !state.categories.length) return;
  box.innerHTML = `<button class="chip ${state.category === "" ? "active" : ""}" data-cat="">全部分类</button>` +
    state.categories.map((c) =>
      `<button class="chip ${state.category === c ? "active" : ""}" data-cat="${esc(c)}">${esc(c)}</button>`
    ).join("");
  $$("#categoryChips .chip").forEach((b) => b.addEventListener("click", () => {
    state.category = b.dataset.cat;
    state.page = 1;
    $("#noticeCategory").value = state.category;
    renderCategoryChips();
    renderNotices();
  }));
}

async function renderNotices() {
  const qs = new URLSearchParams({
    page: state.page, size: state.size,
    keyword: state.keyword, category: state.category,
    genre: state.genre, review: state.review,
  });
  $("#noticeList").innerHTML = `${skeletonCards(2, 88, "sk-list")}${skeletonRows(2, "sk-card")}`;
  try {
    const d = await api("/api/notices?" + qs);
    state.total = d.total;
    state.categories = d.categories;
    const catBox = $("#noticeCategory");
    if (catBox.options.length <= 1) {
      d.categories.forEach((c) => {
        const op = document.createElement("option"); op.value = c; op.textContent = c; catBox.appendChild(op);
      });
    }
    renderCategoryChips();

    if (!d.items.length) {
      $("#noticeList").innerHTML = '<div class="card empty">没有符合条件的通知，试试调整筛选条件或录入新通知</div>';
    } else {
      $("#noticeList").innerHTML = d.items.map((n) => {
        const a = n.analysis;
        const news = a && a.actionable === false;
        return `<div class="notice-card" onclick="openNotice(${n.id})">
          <div class="nc-top">
            <span class="tag" data-cat="${esc(n.category)}">${esc(n.category)}</span>
            ${a && a.genre === "news" ? '<span class="tag genre-news">新闻资讯</span>' : ""}
            ${a && a.genre === "publicity" ? '<span class="tag genre-pub">公示公告</span>' : ""}
            <span class="spacer"></span>
            ${a ? (a.needs_review ? '<span class="tag warn">待复核</span>' : '<span class="tag ok">已解析</span>') : '<span class="tag">未解析</span>'}
          </div>
          <div class="nc-title">${esc(n.title)}</div>
          <div class="nc-sum">${esc(n.summary)}</div>
          <div class="nc-foot">
            ${news ? '<span class="due-chip dl-none">无办理事项</span>'
                   : dueChip(a && a.deadline)}
            ${a && a.deadline_kind && !news ? `<span class="tag kind">${esc(a.deadline_kind)}</span>` : ""}
            <span>${esc(n.publish_time || "发布时间未填")}</span>
            <span>${esc(n.source_name || n.ingest_mode)}</span>
          </div>
        </div>`;
      }).join("");
    }

    const pages = Math.max(1, Math.ceil(d.total / state.size));
    $("#noticePager").innerHTML = `
      <button class="btn sm" ${state.page <= 1 ? "disabled" : ""} onclick="gotoPage(${state.page - 1})">上一页</button>
      <span class="hint">第 ${state.page} / ${pages} 页 · 共 ${d.total} 条</span>
      <button class="btn sm" ${state.page >= pages ? "disabled" : ""} onclick="gotoPage(${state.page + 1})">下一页</button>`;
  } catch (e) {
    failBlock("#noticeList", e, "notices", renderNotices);
    $("#noticePager").innerHTML = "";
  }
}

function gotoPage(p) {
  state.page = p;
  renderNotices();
}

function applyQuick(q) {
  state.quick = q;
  $$("#noticeQuick .chip").forEach((x) => x.classList.toggle("active", x.dataset.quick === q));
  state.genre = (q === "notice" || q === "publicity" || q === "news") ? q : "";
  state.review = q === "needs_review" ? "needs_review" : "";
  $("#noticeGenre").value = state.genre;
  $("#noticeReview").value = state.review;
  state.page = 1;
  renderNotices();
}

/* ---------------- 通知详情 · AI Structured Insight ---------------- */
async function openNotice(id) {
  $("#drawer").classList.add("open");
  $("#drawerMask").classList.add("open");
  $("#drawerTitle").textContent = "正在读取…";
  $("#drawerMeta").textContent = "";
  $("#drawerBody").innerHTML = `${skeletonRows(2, "sk-card")}${skeletonCards(2, 72, "sk-grid")}`;
  try {
    const n = await api("/api/notices/" + id);
    state.currentNotice = n;
    renderDrawer(n);
  } catch (e) {
    /* 抽屉是单条取数，重试要按这条通知重开，而不是刷新整个列表 */
    $("#drawerTitle").textContent = "读取失败";
    failBlock("#drawerBody", e, "drawer", () => openNotice(id));
  }
}

function closeDrawer() {
  $("#drawer").classList.remove("open");
  $("#drawerMask").classList.remove("open");
  state.currentNotice = null;
}

/* 点击「原文依据」→ 在正文中定位并高亮，形成「AI 结论 → 字段 → 原文」闭环 */
/* 正文渲染专用：把采集期用来保留列边界的 \x1f 显示成一个可见竖线。
   这个字符是控制符（Unit Separator），浏览器不会为它排版，
   直接塞进 innerHTML 会让「学院名称\x1f专业代码」在界面上粘成一串，
   看起来像解析出错了——其实是我们的分隔符露出来了。 */
function pretty(text) {
  return esc(String(text == null ? "" : text)).replace(/\u001f/g, '<span class="cell-sep">|</span>');
}

function jumpToEvidence(fragment) {
  const box = $(".content-box");
  if (!box || !fragment) return;
  const full = box.textContent || "";
  // 正文里的列分隔符已经渲染成竖线，而证据片段里还是原始的 \x1f，
  // 两边都归一化成竖线才能对上，否则表格字段永远定位不到。
  const anchor = String(fragment).replace(/\u001f/g, "|").replace(/\s/g, "").slice(0, 12);
  if (!anchor) return;
  const flat = [];
  for (let i = 0; i < full.length; i++) {
    if (!/\s/.test(full[i])) flat.push({ ch: full[i], at: i });
  }
  const flatStr = flat.map((x) => x.ch).join("");
  const hit = flatStr.indexOf(anchor);
  if (hit < 0) { toast("该依据未在原文中定位到连续片段，请核对字段", true); return; }
  const start = flat[hit].at;
  const end = flat[Math.min(hit + anchor.length - 1, flat.length - 1)].at + 1;
  box.innerHTML = esc(full.slice(0, start)) +
    `<mark id="eviMark">${esc(full.slice(start, end))}</mark>` +
    esc(full.slice(end));
  const mark = $("#eviMark");
  if (mark) mark.scrollIntoView({ block: "center", behavior: "smooth" });
  toast("已在原文中定位该字段的依据");
}

function renderDrawer(n) {
  const a = n.analysis;
  const isNews = !!(a && a.actionable === false);
  $("#drawerTitle").textContent = n.title;
  $("#drawerMeta").innerHTML = [
    esc(n.category),
    a && a.genre_label ? esc(a.genre_label) : "",
    n.publish_time ? "发布 " + esc(n.publish_time) : "发布时间未填",
    esc(n.source_name || "来源未填"),
    n.ingest_mode === "url" ? "网页采集" : n.ingest_mode === "import" ? "批量导入" : "手动录入",
  ].filter(Boolean).join(" · ");

  /* 一个字段 + 它的原文依据 = 一个网格单元，避免证据块打乱双列排布 */
  const fieldBlock = (k, v, eviKey, wide) => `
    <div class="field-block ${wide ? "wide" : ""}">
      <div class="field-cell">
        <div class="k">${esc(k)}</div>
        <div class="v">${v && String(v).length ? v : '<span style="color:var(--text-tertiary);font-weight:400">未识别</span>'}</div>
      </div>
      ${evi(eviKey)}
    </div>`;

  const tagList = (arr) => (arr && arr.length)
    ? `<div class="tag-list">${arr.map((x) => `<span class="tag">${esc(x)}</span>`).join("")}</div>`
    : '<span style="color:var(--text-tertiary);font-weight:400">未识别</span>';

  /* 证据片段放进 data-*，用事件委托定位，避免内联字符串转义问题 */
  const evi = (key) => (a && a.evidence && a.evidence[key])
    ? `<div class="evi" data-evi="${esc(a.evidence[key])}" title="点击在原文中定位">${pretty(a.evidence[key])}</div>`
    : "";

  const contactText = (c) => {
    if (!c || typeof c !== "object") return "";
    const parts = [c.person, (c.phone || []).join(" / "), (c.email || []).join(" / ")].filter(Boolean);
    return parts.length ? esc(parts.join(" · ")) : "";
  };

  /* 多轨道截止时间轴：一条通知里可能并行推进秋/冬两套时间表，或者博士生与
     硕士生各有各的节点。单值「报名截止」只能显示其中一个日期，所以这里按
     轨道分组把全部节点摊开，并在每组标出「本人需要办」的节点。 */
  const timelineHTML = (() => {
    const nodes = (a && a.timeline) || [];
    if (!nodes.length) return "";
    const tracks = (a.timeline_tracks && a.timeline_tracks.length)
      ? a.timeline_tracks
      : [""];
    const myRole = (state.currentUser && roleFromGrade(state.currentUser.grade)) || "";

    const groups = tracks.map((t) => ({
      track: t,
      list: nodes.filter((n) => (n.track || "") === t),
    })).filter((g) => g.list.length);

    const trackTag = (t) => (t ? `<span class="tag ai">${esc(t)}轨道</span>` : "");
    const roleTag = (n) => (n.scope === "admin"
      ? '<span class="tag kind">学院办理</span>'
      : `<span class="tag kind">${esc(n.role || "学生")}</span>`);

    return `
      <div class="sec-title">截止时间轴 <span class="line"></span></div>
      <div class="tl-panel">
        <div class="tl-head">
          <span class="tag ai">${icon("calendar")} 共 ${nodes.length} 个节点 · ${groups.length} 条轨道</span>
          <span class="hint">同一事项在秋季与冬季各有一组日期，按自己所在批次看对应的那一行。</span>
        </div>
        ${groups.map((g) => `
          <div class="tl-track">
            <div class="tl-track-head">${trackTag(g.track)}
              <span class="hint">${g.list.length} 个节点</span></div>
            <ol class="tl-list">
              ${g.list.map((n) => {
                const dl = daysLeft(n.date);
                const mine = n.scope !== "admin" && myRole
                  && (String(n.role).includes(myRole) || myRole.includes(String(n.role)));
                return `<li class="tl-node ${n.scope === "admin" ? "is-admin" : ""} ${mine ? "is-mine" : ""}">
                  <span class="tl-date">${esc(n.date || "")}</span>
                  <span class="tl-act">${esc(n.action || "")}</span>
                  ${roleTag(n)}
                  ${dl >= 0 ? `<span class="tl-left">剩 ${dl} 天</span>` : '<span class="tl-left is-due">已过期</span>'}
                </li>`;
              }).join("")}
            </ol>
          </div>`).join("")}
        <div class="hint" style="margin-top:8px">
          每个节点都逐条回原文核对过日期与事项；对不上原文的节点会被剔除，不会出现在这里。
        </div>
      </div>`;
  })();

  /* 规则校验报告 + 人工审核操作区 */
  const ck = (a && a.checks) || {};
  const passN = (ck.passed || []).length;
  /* 表格派生说明：这条通知里的学院/专业/电话是从表格的哪几列读出来的。
     不给这层说明，用户在详情里看到一个凭空多出来的学院名，只能选择信或不信。
     注意这里只讲结构不讲整表原文——原文就在上面，再贴一遍既冗余又容易不同步。 */
  const tableHTML = (() => {
    const st = (a && a.table_stats) || {};
    const detail = (a && a.table_detail) || [];
    // 存量正文：旧版采集器把表格压平入库，列边界已丢且不可复原。
    // 这时必须显式说"要重新采集"，不能让它表现为"这条通知本来就没有表格"——
    // 沉默会让用户以为信息本来就不存在，而实际上是我们早期把结构扔掉了。
    if (a && a.table_needs_recollect && !detail.length) {
      return `
      <div class="sec-title">表格结构 <span class="line"></span></div>
      <div class="tl-panel">
        <div class="tl-head">
          <span class="tag">需重新采集</span>
          <span class="hint">这条通知的正文是旧版采集器入库的，表格的列边界已经丢失</span>
        </div>
        <div class="hint" style="margin-top:8px">
          压平之后的正文里没有"列"这个概念（085600材料与化工1027-87164677 既可切三段也可切两段），
          复原只能靠猜，猜错会造出原文没有的事实，所以不做复原。
          在"来源管理"里重新采集这条通知，表格结构就能完整还原。
        </div>
      </div>`;
    }
    if (!st.total || !detail.length) return "";
    const kindName = { major_plan: "招生计划表", schedule: "日程安排表", contact: "联系表", list: "清单表" };
    const colName = {
      college: "学院", major_code: "专业代码", major_name: "专业名称", plan: "计划",
      phone: "电话", email: "邮箱", person: "联系人", date: "日期", seq: "序号",
      detail: "事项", link: "链接", "": "未标注",
    };
    const rows = detail.map((t) => {
      const cols = (t.kinds || []).map((k) => esc(colName[k] || k)).join(" · ");
      return `<li><b>${esc(kindName[t.kind] || "表格")}</b>　${t.columns} 列 × ${t.rows} 行
        <span class="hint">（${cols}）</span></li>`;
    }).join("");
    const derived = [];
    if (st.colleges) derived.push(`${st.colleges} 个学院`);
    if (st.majors) derived.push(`${st.majors} 个专业`);
    if (st.phones) derived.push(`${st.phones} 个电话`);
    return `
      <div class="sec-title">表格结构 <span class="line"></span></div>
      <div class="tl-panel">
        <div class="tl-head">
          <span class="tag">${st.total} 张表</span>
          <span class="tag">${st.rows} 行数据</span>
          <span class="hint">${derived.length ? "据此补全：" + esc(derived.join("、")) : ""}</span>
        </div>
        <ul class="tl-list" style="padding-left:16px;margin:8px 0 0">${rows}</ul>
        <div class="hint" style="margin-top:8px">
          表格里的列边界在采集时就被保留下来了；学院与电话只在正文没写时才由表格补位，
          正文明确写出的值优先。列数对不上的行不采信——值串列记错学院比空着更糟。
        </div>
        ${a.table_note ? `<div class="evi static" style="margin-top:8px">${esc(a.table_note)}</div>` : ""}
      </div>`;
  })();

  const issueN = (ck.issues || []).length;
  const warnN = (ck.warnings || []).length;
  const checksHTML = (a && (ck.passed || ck.issues)) ? `
    <div class="sec-title">规则校验与人工审核 <span class="line"></span></div>
    <div class="check-panel">
      <div class="check-summary">
        <span class="tag ok">通过 ${passN} 项</span>
        ${issueN ? `<span class="tag warn">拦截 ${issueN} 项</span>` : ""}
        ${warnN ? `<span class="tag">提示 ${warnN} 项</span>` : ""}
        <span class="hint">${esc((ck.issues || [])[0] || "未发现硬异常")}</span>
      </div>
      <div class="actions review-actions">
        <button class="btn primary sm" onclick="reviewNotice(${n.id},'approve')">通过</button>
        <button class="btn sm" onclick="toggleCorrect(${n.id})">修正字段</button>
        <button class="btn sm" onclick="reviewNotice(${n.id},'reject')">驳回重解析</button>
      </div>
      <input class="input" id="reviewReason" placeholder="驳回原因（驳回时必填，会写入复核说明）" />
    </div>` : "";

  let analysisHTML = "";
  if (a) {
    const conf = Math.round((a.confidence || 0) * 100);
    const confColor = conf >= 75 ? "var(--success)" : conf >= 55 ? "var(--warning)" : "var(--danger)";
    const genreNote = isNews ? `
      <div class="genre-note">
        <b>${esc(a.genre_label || "新闻资讯")}</b>：这是报道/资讯类内容，没有报名或办理事项。
        系统不抽取截止日期、对象、条件、材料等字段，也不把它加入待办与个性化匹配，
        避免产出「看起来很合理」的假数据。
        <div class="hint">${esc(a.review_note || "")}</div>
      </div>` : "";
    analysisHTML = `
      ${genreNote}
      <div class="insight-card">
        <div class="insight-head">
          <span class="ai-label">${icon("spark")} AI 已完成结构化解析</span>
          <span class="tag ai">14 类字段契约</span>
          <span class="insight-conf">${engineTag(a)}
            ${a.reviewed ? '<span class="tag ok">已人工复核</span>' : (a.needs_review ? '<span class="tag warn">待复核</span>' : "")}
          </span>
        </div>
        <div class="conf-bar">
          <span>信息可信度</span>
          <div class="conf-track"><i style="width:${conf}%"></i></div>
          <b style="color:${confColor}">${conf}%</b>
        </div>
        <div class="hint" style="margin-top:8px">字面事实由程序从原文确定性抽取，逐字段做证据接地；模型不生成这些值。</div>
      </div>

      <div class="sec-title">结构化字段 <span class="line"></span></div>
      <div class="field-grid">
        ${fieldBlock(a.deadline_kind || "报名截止", a.deadline ? esc(a.deadline) + "（" + (daysLeft(a.deadline) >= 0 ? "剩 " + daysLeft(a.deadline) + " 天" : "已截止") + "）" + (a.deadline_source === "relative" ? '<span class="tag ai">相对时间换算</span>' : "") : "", "", false)}
        ${fieldBlock("报名开始时间", a.start_time ? esc(a.start_time) : "", "", false)}
        ${fieldBlock("年级 / 学历限制", tagList(a.grade_requirement), "grade_requirement", true)}
        ${fieldBlock("专业 / 学院限制", tagList(a.major_requirement), "major_requirement", true)}
        ${fieldBlock("活动举办时间", a.event_time ? esc(a.event_time) : "", "", false)}
        ${fieldBlock("地点", a.location ? esc(a.location) : "", "", false)}
        ${fieldBlock("联系方式", contactText(a.contact), "contact", true)}
        ${fieldBlock("适用对象", tagList(a.target), "target", true)}
        ${fieldBlock("资格条件", tagList(a.conditions), "conditions", true)}
        ${fieldBlock("报名材料", tagList(a.materials), "materials", true)}
        ${fieldBlock("报名方式", a.apply_method ? esc(a.apply_method) : "", "", true)}
      </div>
      ${a.review_note && !isNews ? `<div class="evi static" style="margin-top:12px">${esc(a.review_note)}</div>` : ""}
      ${timelineHTML}
      ${tableHTML}
      ${checksHTML}`;
  } else {
    analysisHTML = `<div class="sec-title">AI 解析结果 <span class="line"></span></div><div class="empty">该通知尚未解析</div>`;
  }

  $("#drawerBody").innerHTML = `
    ${analysisHTML}
    <div class="actions">
      <button class="btn ai sm" onclick="analyzeNotice(${n.id}, this)">${icon("spark")}重新解析</button>
      <button class="btn sm" onclick="addTask(${n.id}, this)" ${isNews ? 'disabled title="新闻资讯无办理事项"' : ""}>加入待办</button>
      <button class="btn sm" onclick="toggleCorrect(${n.id})">人工修正</button>
      ${n.source_url ? `<a class="btn sm" href="${esc(n.source_url)}" target="_blank" rel="noopener">查看原文链接</a>` : ""}
      <button class="btn sm" onclick="removeNotice(${n.id}, this)" style="color:var(--danger);margin-left:auto">删除</button>
    </div>
    <div id="correctForm"></div>

    <div class="sec-title">通知原文 <span class="line"></span></div>
    <div class="hint" style="margin-bottom:8px">点击上方任意「原文依据」可在此处定位并高亮对应句子。</div>
    <div class="content-box">${pretty(n.content)}</div>`;
}

async function analyzeNotice(id, btn) {
  /* 解析是重活（可能调模型），连点会导致同一通知被并行解析多次 */
  await withBusy(btn, "解析中…", async () => {
    try {
      const n = await api(`/api/notices/${id}/analyze`, { method: "POST" });
      renderDrawer(n);
      toast("解析完成");
      memCache.delete("/api/dashboard/stats");
      if (state.view === "notices") renderNotices();
      if (state.view === "dashboard") renderDashboard();
    } catch (e) {
      toast("解析失败：" + e.message, true);
    }
  });
}

function toggleCorrect(id) {
  const n = state.currentNotice;
  if (!n) return;
  const a = n.analysis || { target: [], conditions: [], materials: [] };
  const box = $("#correctForm");
  if (box.dataset.open === "1") { box.innerHTML = ""; box.dataset.open = "0"; return; }
  box.dataset.open = "1";
  box.innerHTML = `
    <div class="sec-title">人工修正 <span class="line"></span></div>
    <div class="correct-form">
      <div class="row">
        <label class="field"><span>分类</span>
          <select class="input" id="cfCat">${state.categories.map((c) => `<option ${c === n.category ? "selected" : ""}>${esc(c)}</option>`).join("")}</select>
        </label>
        <label class="field"><span>报名截止</span>
          <input class="input" id="cfDeadline" type="date" value="${esc(a.deadline || "")}" />
        </label>
      </div>
      <label class="field"><span>适用对象（用逗号分隔）</span>
        <input class="input" id="cfTarget" value="${esc((a.target || []).join("，"))}" />
      </label>
      <label class="field"><span>资格条件（用逗号分隔）</span>
        <input class="input" id="cfCond" value="${esc((a.conditions || []).join("，"))}" />
      </label>
      <label class="field"><span>报名材料（用逗号分隔）</span>
        <input class="input" id="cfMat" value="${esc((a.materials || []).join("，"))}" />
      </label>
      <label class="field"><span>报名方式</span>
        <textarea class="input area" id="cfMethod" rows="2">${esc(a.apply_method || "")}</textarea>
      </label>
      <div class="row">
        <label class="field"><span>年级 / 学历限制（逗号分隔）</span>
          <input class="input" id="cfGrade" value="${esc((a.grade_requirement || []).join("，"))}" />
        </label>
        <label class="field"><span>专业 / 学院限制（逗号分隔）</span>
          <input class="input" id="cfMajor" value="${esc((a.major_requirement || []).join("，"))}" />
        </label>
      </div>
      <label class="field"><span>地点</span>
        <input class="input" id="cfLocation" value="${esc(a.location || "")}" />
      </label>
      <div style="display:flex;gap:8px">
        <button class="btn primary sm" onclick="submitCorrect(${id})">保存并标记已复核</button>
        <button class="btn sm" onclick="toggleCorrect(${id})">取消</button>
      </div>
    </div>`;
}

function splitList(v) {
  return v.split(/[,，、;；]/).map((s) => s.trim()).filter(Boolean);
}

async function submitCorrect(id) {
  const body = {
    category: $("#cfCat").value,
    deadline: $("#cfDeadline").value,
    target: splitList($("#cfTarget").value),
    conditions: splitList($("#cfCond").value),
    materials: splitList($("#cfMat").value),
    apply_method: $("#cfMethod").value,
    grade_requirement: splitList($("#cfGrade").value),
    major_requirement: splitList($("#cfMajor").value),
    location: $("#cfLocation").value,
  };
  try {
    const n = await api(`/api/notices/${id}/analysis`, { method: "PUT", body });
    state.currentNotice = n;
    renderDrawer(n);
    toast("已保存人工修正结果");
    if (state.view === "notices") renderNotices();
  } catch (e) {
    toast("保存失败：" + e.message, true);
  }
}

async function reviewNotice(id, action) {
  const el = $("#reviewReason");
  const reason = el ? el.value.trim() : "";
  if (action === "reject" && !reason) { toast("驳回需填写原因", true); return; }
  try {
    const n = await api(`/api/notices/${id}/analysis`, {
      method: "PUT", body: { action, reject_reason: reason },
    });
    state.currentNotice = n;
    renderDrawer(n);
    toast(action === "approve" ? "已通过人工审核" : "已驳回，等待重新解析");
    if (state.view === "notices") renderNotices();
    if (state.view === "dashboard") renderDashboard();
  } catch (e) {
    toast("操作失败：" + e.message, true);
  }
}

async function removeNotice(id, btn) {
  if (!confirm("确定删除这条通知？相关解析结果与待办会一并删除。")) return;
  await withBusy(btn, "删除中…", async () => {
    try {
      await api(`/api/notices/${id}`, { method: "DELETE" });
      closeDrawer();
      toast("已删除");
      // 详情抽屉可从多个视图打开，删除后刷新当前视图，避免统计/列表停留在旧数据。
      if (state.view === "home") renderHome();
      if (state.view === "notices") renderNotices();
      if (state.view === "tasks") renderTasks();
      if (state.view === "dashboard") renderDashboard();
      if (state.view === "match") renderMatch();
    } catch (e) {
      toast("删除失败：" + e.message, true);
    }
  });
}

async function addTask(noticeId, btn) {
  const uid = state.currentUser && state.currentUser.id;
  if (!uid) { toast("请先点击右上角头像登录角色账号", true); return; }
  /* 后端对重复待办会回 409，但用户连点三次仍会看到三条请求与一条报错；
     这里先锁按钮，把「已经加过了」交给第一次请求的结果说明。 */
  await withBusy(btn, "加入中…", async () => {
    try {
      await api(`/api/users/${uid}/tasks`, { method: "POST", body: { notice_id: noticeId } });
      toast("已加入待办");
      // 详情抽屉可从多个视图打开：加入待办后同步刷新当前视图里依赖待办的内容，
      // 避免首页待办数、我的任务列表停留在旧数据（与首页「加入任务」入口保持一致）。
      if (state.view === "home") renderHome();
      if (state.view === "tasks") renderTasks();
    } catch (e) {
      toast(e.message, true);
    }
  });
}

/* ---------------- 我的匹配 ---------------- */
/* 个人信息板块：画像既展示也可编辑，保存后立即重算匹配，
   让"个人信息"真正成为匹配依据，而不是一块静态展示。 */
const PROFILE_INTEREST_OPTIONS = ["竞赛", "科研", "讲座", "志愿服务", "校园活动", "实习", "奖学金", "实践"];

function renderProfileCard(u) {
  const box = $("#profileCard");
  if (!box) return;
  /* 登录后要把未登录时被整体隐藏的容器恢复回来 */
  box.hidden = false;
  if (!state.profileEditing) {
    box.innerHTML = `
      <div class="avatar">${esc(u.name.slice(0, 1))}</div>
      <div class="profile-info">
        <strong>${esc(u.name)}</strong>
        <p>${esc(u.grade || "—")} · ${esc(u.major || "—")} · 学号 ${esc(u.student_no || "—")}</p>
        <p class="profile-role">个人画像用于个性化匹配，可随时修改</p>
      </div>
      <div class="chips">${(u.interests || []).map((i) => `<span class="tag ai">${esc(i)}</span>`).join("") || '<span class="profile-none">未设置兴趣方向</span>'}</div>
      <button class="btn ghost sm profile-edit-btn" onclick="toggleProfileEdit()">编辑个人信息</button>`;
    return;
  }
  const chosen = new Set(u.interests || []);
  box.innerHTML = `
    <div class="profile-edit">
      <div class="profile-edit-title"><svg aria-hidden="true" focusable="false"><use href="#i-user"/></svg>编辑个人信息 · 匹配会据此实时更新</div>
      <div class="profile-grid">
        <label class="field"><span>姓名</span><input class="input" id="pfName" value="${esc(u.name)}" maxlength="20"/></label>
        <label class="field"><span>年级</span><input class="input" id="pfGrade" value="${esc(u.grade || "")}" maxlength="12" placeholder="如 大三 / 研二"/></label>
        <label class="field"><span>专业</span><input class="input" id="pfMajor" value="${esc(u.major || "")}" maxlength="40"/></label>
        <label class="field"><span>学号</span><input class="input" id="pfNo" value="${esc(u.student_no || "")}" maxlength="20"/></label>
      </div>
      <div class="profile-interests">
        <span class="profile-sub">关注方向（可多选，直接决定匹配结果）</span>
        <div class="profile-chips">${PROFILE_INTEREST_OPTIONS.map((i) =>
          `<button type="button" class="interest-chip ${chosen.has(i) ? "on" : ""}" data-v="${esc(i)}">${esc(i)}</button>`).join("")}</div>
      </div>
      <div class="profile-actions">
        <button class="btn ghost sm" onclick="toggleProfileEdit()">取消</button>
        <button class="btn primary sm" id="pfSave" onclick="saveProfile(${u.id})">保存并重新匹配</button>
      </div>
    </div>`;
  $$(".interest-chip").forEach((c) => c.addEventListener("click", () => c.classList.toggle("on")));
}

function toggleProfileEdit() {
  state.profileEditing = !state.profileEditing;
  if (state.currentUser) renderProfileCard(state.currentUser);
}

async function saveProfile(userId) {
  const name = ($("#pfName")?.value || "").trim();
  if (!name) { toast("请填写姓名", true); return; }
  const payload = {
    name,
    grade: ($("#pfGrade")?.value || "").trim(),
    major: ($("#pfMajor")?.value || "").trim(),
    student_no: ($("#pfNo")?.value || "").trim(),
    interests: $$(".interest-chip.on").map((c) => c.dataset.v),
  };
  const btn = $("#pfSave");
  /* 保存会连带重算整张匹配结果，重复提交会让结果与画像错位；
     统一走 withBusy，按钮文案也与其他写操作保持同一种「…中」写法。 */
  return withBusy(btn, "保存中…", async () => {
    try {
      const updated = await api(`/api/users/${userId}`, { method: "PUT", body: payload });
      const idx = state.users.findIndex((x) => x.id === userId);
      if (idx >= 0) state.users[idx] = updated;
      state.currentUser = updated;
      state.profileEditing = false;
      refreshIdentityUI();
      renderLoginRoles();
      toast("个人信息已保存，匹配已更新");
      renderMatch();
      if (state.view === "home") renderHome();
    } catch (e) {
      toast(e.message, true);
    }
  });
}

async function renderMatch() {
  refreshIdentityUI();
  if (!state.users.length) {
    $("#matchList").innerHTML = '<div class="card empty">暂无可登录的角色账号</div>';
    return;
  }
  const u = state.currentUser;
  /* 未登录时不展示任何角色的匹配结果，引导先在头像处选择登录角色 */
  if (!u) {
    $("#matchSummary").textContent = "";
    /* 只清空 innerHTML 会留下带内边距的空卡片（一条 40px 白条），
       连同容器一起隐藏，未登录时页面只呈现下方那张引导卡。 */
    const pc = $("#profileCard");
    pc.innerHTML = "";
    pc.hidden = true;
    const rp = $("#recoPanel");
    if (rp) rp.hidden = true;
    $("#matchList").innerHTML = '<div class="card empty">请先点击右上角头像，选择一个角色账号登录。<br />登录后这里会按该角色的专业、年级与兴趣，给出三档匹配结论与逐条理由。</div>';
    return;
  }

  renderProfileCard(u);

  /* 匹配结论要等服务端算完七因子，列表先摆骨架，结论出来再原地替换 */
  $("#matchSummary").textContent = "正在计算匹配…";
  $("#matchList").innerHTML = skeletonCards(3, 118, "sk-list");

  const qs = new URLSearchParams({ level: state.matchLevel, only_open: "true" });
  try {
    const d = await api(`/api/users/${u.id}/matches?${qs}`);
    $("#matchSummary").textContent =
      `符合 ${d.counts.match} 条 · 可能符合 ${d.counts.maybe} 条 · 暂不符合 ${d.counts.no} 条`;

    if (!d.items.length) {
      $("#matchList").innerHTML = '<div class="card empty">没有匹配结果，可能是通知尚未解析或筛选条件过窄</div>';
      return;
    }
    const lvName = { match: "符合", maybe: "可能符合", no: "暂不符合" };
    $("#matchList").innerHTML = d.items.map((m) => {
      const score = Math.max(5, Math.min(98, m.score));
      return `<div class="match-card lv-${m.level}" onclick="openNotice(${m.notice.id})">
        <div class="match-head">
          <span class="tag" data-cat="${esc(m.notice.category)}">${esc(m.notice.category)}</span>
          <div class="t">${esc(m.notice.title)}</div>
          <span class="score">${score} 分</span>
        </div>
        <div class="match-meter">
          <span class="lv">${lvName[m.level] || ""}</span>
          <div class="track"><i style="width:${score}%"></i></div>
        </div>
        ${factorChips(m.factors)}
        <div class="reasons">
          ${m.reasons.map((r) => `<div class="reason ${r.type}"><span class="dot"></span><span>${esc(r.text)}</span></div>`).join("")}
        </div>
      </div>`;
    }).join("");
    loadRecommendations(u);
  } catch (e) {
    $("#matchSummary").textContent = "";
    failBlock("#matchList", e, "match", renderMatch);
  }
}

/* 匹配卡片里的分项依据：只标出真正影响结论的几项，避免七个标签堆成噪音。
 * state=ok 且贡献高的给「正向」，conflict/bad 给「否决」，其余中性省略。 */
const FACTOR_SHORT = {
  target: "适用对象", major: "专业限制", interest: "兴趣方向",
  condition: "硬性条件", urgency: "时效紧迫", freshness: "内容新鲜", quality: "信息完整",
};
function factorChips(factors) {
  if (!Array.isArray(factors) || !factors.length) return "";
  const keep = factors.filter((f) => f.state === "bad" || f.contribution >= 0.06);
  if (!keep.length) return "";
  const chips = keep.map((f) => {
    const cls = f.state === "bad" ? "bad" : (f.contribution >= 0.12 ? "good" : "");
    const label = FACTOR_SHORT[f.key] || f.label || f.key;
    return `<span class="fchip ${cls}" title="${esc(f.note || "")}">${esc(label)}</span>`;
  });
  return `<div class="match-factors">${chips.join("")}</div>`;
}

/* 推荐位：召回→画像→融合→探索→重排五段式接口，返回「为什么推给你」而不是只给一个分数 */
async function loadRecommendations(u) {
  const panel = $("#recoPanel");
  if (!panel) return;
  if (!u) { panel.hidden = true; return; }
  try {
    const d = await api(`/api/users/${u.id}/recommendations?limit=6`);
    const items = d.items || [];
    if (!items.length) { panel.hidden = true; return; }
    panel.hidden = false;
    const meta = d.meta || {};
    const sig = meta.signals || {};
    /* 把「用了哪几路信号」说出来，用户才知道推荐位不是随机排的 */
    const used = [];
    if (sig.engaged) used.push(`${sig.engaged} 条待办做画像`);
    if (sig.semantic_centroid_terms) used.push("语义兴趣向量");
    if (sig.peer_users) used.push(`${sig.peer_users} 位同学的行为`);
    $("#recoMeta").textContent =
      `从 ${meta.candidate_total || 0} 条里筛出 ${meta.recalled || 0} 条候选`
      + (used.length ? `，融合${used.join("、")}` : "，按内容与时效排序")
      + `，取前 ${items.length} 条`;
    $("#recoList").innerHTML = items.map((r, i) => {
      const score = Math.max(5, Math.min(98, r.score));
      const cat = esc(r.category || "");
      const sg = r.signals || {};
      /* 只展示真正有贡献的信号，避免一排 0% 的噪声 */
      const chips = [
        ["内容", sg.content],
        ["语义", sg.semantic],
        ["偏好", sg.behavior],
        ["同学", sg.peer],
        ["热度", sg.popular],
      ].filter(([, v]) => Number(v) >= 0.05)
        .map(([k, v]) => `<span class="rsig">${k} ${Math.round(Number(v) * 100)}%</span>`)
        .join("");
      return `<div class="reco-card" onclick="openNotice(${r.notice_id})">
        <div class="reco-rank">${i + 1}</div>
        <div class="reco-body">
          <div class="reco-line">
            <span class="tag" data-cat="${cat}">${cat}</span>
            <span class="reco-t">${esc(r.notice.title)}</span>
            <span class="score">${score} 分</span>
          </div>
          <div class="reco-why">${esc(r.why || "")}</div>
          ${chips ? `<div class="reco-sigs">${chips}</div>` : ""}
        </div>
      </div>`;
    }).join("");
  } catch (e) {
    panel.hidden = true;
  }
}

async function toggleRecoExplain() {
  const box = $("#recoExplain");
  if (!box) return;
  if (!box.hidden) { box.hidden = true; return; }
  if (!box.dataset.loaded) {
    try {
      const d = await api("/api/recommendation-model");
      const bar = (f) => {
        const pct = Math.round((f.weight || 0) * 100);
        return `<div class="rx-row">
          <span class="rx-name">${esc(f.label)}</span>
          <span class="rx-bar"><i style="width:${pct}%"></i></span>
          <span class="rx-pct">${pct}%</span>
          <span class="rx-desc">${esc(f.desc || "")}</span>
        </div>`;
      };
      const rows = (d.factors || []).map(bar).join("");
      const fusion = (d.fusion || []).map(bar).join("");
      const pipe = (d.pipeline || []).map((s) => `<li>${esc(s)}</li>`).join("");
      box.innerHTML = `<div class="rx-title">五段式推荐流程</div>
        <ol class="rx-pipe">${pipe}</ol>
        <div class="rx-title">第一步：内容七因子加权（满分 98，${d.threshold} 分以上判为符合）</div>
        ${rows}
        <div class="rx-title">第二步：五路信号融合（最终排序得分）</div>
        ${fusion}
        <div class="rx-note">${esc(d.cold_start || "")}</div>`;
      box.dataset.loaded = "1";
    } catch (e) {
      box.innerHTML = `<div class="hint">说明加载失败：${esc(e.message)}</div>`;
      /* 没拿到模型说明就不要记成已加载，否则再点也不会重试 */
      delete box.dataset.loaded;
    }
  }
  box.hidden = false;
}

/* ---------------- AI 助手 ---------------- */
async function loadAiStatus(silent = true) {
  try {
    const d = await api("/api/ai/status", { timeout: 15000 });
    const info = d.llm || {};
    const dot = $("#chatDot");
    const status = $("#chatStatus");
    if (!status) return;

    let cls = "rule";
    let head = "规则引擎（离线）· 结果可回原文核对";
    if (info.ready && info.last_latency_ms > 0) {
      cls = "llm";
      head = `大模型已就绪 · ${info.model}`;
    } else if (info.ready) {
      cls = "llm";
      head = `大模型已配置 · ${info.model}`;
    } else if (info.available && info.cooldown_remaining > 0) {
      cls = "cool";
      head = `大模型冷却中 · 规则引擎兜底`;
    }
    if (dot) dot.className = "ai-status-dot " + cls;
    status.textContent = head;
    const side = $("#engineName");
    if (side) side.textContent = info.ready ? info.model : "规则引擎（离线）";
  } catch (e) {
    if (!silent) toast("AI 状态读取失败：" + e.message, true);
  }
}

async function probeAi() {
  const btn = $("#btnAiProbe");
  if (!btn) return;
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = "探测中…";
  try {
    const d = await api("/api/ai/health", { method: "POST", timeout: 30000 });
    if (d.ok) toast(`模型连通正常（${d.latency_ms} ms）`);
    else if (d.error_kind === "not_configured") toast("未配置密钥，内置规则引擎可用");
    else toast(`模型不可用：${d.reason}`, true);
  } catch (e) {
    toast("探测失败：" + e.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = original || "测试连接";
    loadAiStatus(true);
  }
}

/* ---- 结构化要点 + 追问建议 渲染（让对话窗口展示更完整） ---- */
let WELCOME_HTML = "";
const FIELD_DEFS = [
  { k: "deadline", l: "截止时间", t: "text" },
  { k: "registration_start", l: "报名开始", t: "text" },
  { k: "materials", l: "材料清单", t: "list" },
  { k: "conditions", l: "申请条件", t: "list" },
  { k: "target", l: "面向对象", t: "list" },
  { k: "apply_method", l: "报名方式", t: "text" },
  { k: "contact", l: "联系方式", t: "text" },
  { k: "location", l: "地点", t: "text" },
  { k: "excerpt", l: "原文摘要", t: "excerpt" },
];

function renderAnswerFields(rows, citations) {
  if (!rows || !rows.length) return "";
  const byIdx = {};
  (citations || []).forEach((c) => { byIdx[c.index] = c; });
  const blocks = rows.slice(0, 4).map((r) => {
    const f = r.fields || {};
    const fr = FIELD_DEFS.filter((d) => {
      const v = f[d.k];
      if (v == null) return false;
      if (Array.isArray(v)) return v.length > 0;
      const s = String(v).trim();
      return s.length > 0 && s !== "未写明";
    });
    if (!fr.length) return "";
    const rowsHtml = fr.map((d) => {
      const v = f[d.k];
      if (d.t === "list") {
        return `<div class="frow"><span class="flabel">${d.l}</span><div class="fvals">${v.map((x) => `<span class="fchip">${esc(x)}</span>`).join("")}</div></div>`;
      }
      if (d.t === "excerpt") {
        return `<div class="frow"><span class="flabel">${d.l}</span><div class="fvals ftext"><span>${v.map((x) => esc(x)).join("；")}</span></div></div>`;
      }
      return `<div class="frow"><span class="flabel">${d.l}</span><div class="fvals ftext"><span>${esc(v)}</span></div></div>`;
    }).join("");
    const cit = byIdx[r.index] || {};
    const title = cit.title ? esc(cit.title) : "";
    const openBtn = cit.notice_id ? `<button class="afb-open" onclick="openNotice(${cit.notice_id})">查看原文</button>` : "";
    return `<div class="afb"><div class="afb-head"><span class="afb-idx">[${r.index}]</span>${title ? `<span class="afb-title">${title}</span>` : ""}${openBtn}</div>${rowsHtml}</div>`;
  }).filter(Boolean).join("");
  if (!blocks) return "";
  return `<div class="ans-fields-block"><div class="afb-cap">结构化要点（编号对应下方来源，点「查看原文」核对）</div>${blocks}</div>`;
}

function renderFollowups(asked) {
  const qs = $$("#quickQuestions .quick-card").map((c) => c.dataset.q).filter((q) => q && q !== asked);
  const picks = qs.slice(0, 3);
  if (!picks.length) return "";
  /* 用 data-q 承载问题文本，点击交由 #qaChat 上的事件委托处理。
     之前用 onclick="askWith(${JSON.stringify(q)})" 会在中文问题含双引号时
     提前闭合 HTML 属性，导致点击事件根本不触发（「你可能还想问」点了没反应）。 */
  return `<div class="followups"><span class="fu-label">你可能还想问</span>${picks.map((q) => `<button class="fu-chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>`;
}

function askWith(q) {
  const i = $("#qaInput");
  if (i) { i.value = q; askQuestion(); }
}

function clearChat() {
  const chat = $("#qaChat");
  if (!chat) return;
  chat.innerHTML = WELCOME_HTML || "";
  const wrap = chat.closest(".qa-wrap");
  if (wrap) wrap.classList.remove("chat-active");
  chat.scrollTop = 0;
}

async function askQuestion() {
  const input = $("#qaInput");
  const q = input.value.trim();
  if (!q || state.asking) return;
  state.asking = true;
  state.lastQuestion = q;
  input.value = "";
  $("#qaSend").disabled = true;

  const chat = $("#qaChat");
  /* 重试时先清掉上一次遗留的报错/占位节点，避免页面出现两个 #pending
     导致应答写进旧节点、新占位一直转圈。 */
  const stale = chat.querySelector("#pending");
  if (stale) stale.remove();
  chat.insertAdjacentHTML("beforeend",
    `<div class="msg user"><div class="bubble">${esc(q)}</div></div>
     <div class="msg bot" id="pending"><div class="bubble pending-bubble"><span class="spinner dark"></span>正在检索已收录通知并核对原文…</div></div>`);
  chat.scrollTop = chat.scrollHeight;
  /* 开始对话后收起欢迎语与示例区，把整块纵向空间让给对话窗口 */
  const qaWrap = document.querySelector("#qaPaneChat").closest(".qa-wrap");
  if (qaWrap) qaWrap.classList.add("chat-active");

  try {
    const d = await api("/api/qa/ask", { method: "POST", body: { query: q }, timeout: 75000 });
    const cites = (d.citations || []).map((c) => `
      <div class="cite" onclick="openNotice(${c.notice_id})">
        <b>[${c.index}]</b>
        <div class="cite-body">
          <span class="cite-title">${esc(c.title)}</span>
          ${c.snippet ? `<span class="cite-snip">${esc(c.snippet)}</span>` : ""}
        </div>
        <span class="cite-score">相关度 ${c.score}</span>
      </div>`).join("");
    const engineLabel = d.engine === "llm" ? "大模型生成"
      : d.mode === "no-hit" ? "未命中"
      : d.mode === "clarify" ? "需要补充信息" : "结构化抽取回答";
    const warn = d.fallback_reason
      ? `<div class="ans-warn">${esc(d.fallback_reason)}</div>` : "";
    const citeWarn = d.engine === "llm" && d.citation_ok === false && !d.fallback_reason
      ? `<div class="ans-warn">回答未标注来源编号，请以下方引用列表为准核对原文。</div>` : "";
    const foot = `<div class="ans-foot">
        <span class="ans-engine ${esc(d.engine || "rule")}">${engineLabel}</span>
        ${d.intent_label ? `<span class="ans-intent">${esc(d.intent_label)}</span>` : ""}
      </div>`;
    const fieldsHtml = renderAnswerFields(d.answer_fields, d.citations);
    const fuHtml = renderFollowups(q);
    const answerHtml = esc(d.answer).replace(/\n/g, "<br/>");
    $("#pending").innerHTML = `<div class="bubble answer">${answerHtml}${warn}${citeWarn}${fieldsHtml}${cites ? `<div class="cites">${cites}</div>` : ""}${foot}${fuHtml}</div>`;
    loadAiStatus(true);
  } catch (e) {
    $("#pending").innerHTML = `<div class="bubble error-bubble">抱歉，问答服务暂时不可用：${esc(e.message)}
      <button class="btn ghost sm" onclick="askQuestionRetry()">重试</button></div>`;
  } finally {
    state.asking = false;
    $("#qaSend").disabled = false;
    chat.scrollTop = chat.scrollHeight;
  }
}

function askQuestionRetry() {
  if (!state.lastQuestion) return;
  $("#qaInput").value = state.lastQuestion;
  askQuestion();
}

/* ---------------- 我的任务 ---------------- */
/* 个人时间轴：直接打 /api/timeline，服务端已按用户画像里的年级归一身份，
   这里只负责渲染。回看窗口给 400 天，是因为校内通知的节点日期可能已经过去，
   学生仍需要看到「我错过了哪几步」，而不是一片空白。 */
async function renderMyTimeline() {
  const box = $("#myTimeline");
  if (!box) return;
  /* 只认当前登录角色：以前这里会 fallback 到 users[0]，
     等于未登录时把张明的待办当成「你的」，与登录设计相矛盾。 */
  const u = state.currentUser;
  if (!u) {
    box.innerHTML = '<div class="empty">请先点击右上角头像登录角色账号</div>';
    return;
  }
  const grade = (state.currentUser && state.currentUser.grade) || "";
  if (!grade) {
    box.innerHTML = `<div class="empty">
      个人时间轴需要知道你的年级。<br/>
      <span class="hint">在「我的匹配」里填写年级（如 博二 / 研三 / 大三）后，这里会自动列出你要办的节点。</span>
    </div>`;
    return;
  }
  box.innerHTML = skeletonRows(4, "sk-timeline");
  try {
    /* Web 端没有小程序那套票据，登录只是本地选了一个角色，
       所以按 user_id 取数；小程序端仍走 x-token。 */
    const d = await api(`/api/timeline?user_id=${u.id}&back=400`);
    const hint = $("#myTimelineHint");
    if (hint) {
      hint.textContent = d.role
        ? `身份归一为「${d.role}」，共 ${d.count} 个节点`
        : "年级未识别为具体身份，请在「我的匹配」中改成 博二 / 研三 / 大三 这类写法";
    }
    if (!d.items || !d.items.length) {
      box.innerHTML = '<div class="empty">当前没有与你身份相关的截止节点</div>';
      return;
    }
    // 按轨道分组，与通知详情里的时间轴保持同一套视觉语言
    const tracks = [];
    d.items.forEach((it) => { if (!tracks.includes(it.track)) tracks.push(it.track); });
    box.innerHTML = tracks.map((t) => {
      const list = d.items.filter((it) => it.track === t);
      return `<div class="tl-track">
        <div class="tl-track-head">
          ${t ? `<span class="tag ai">${esc(t)}轨道</span>` : '<span class="tag kind">未标注轨道</span>'}
          <span class="hint">${list.length} 个节点</span>
        </div>
        <ol class="tl-list">
          ${list.map((it) => `
            <li class="tl-node is-mine">
              <span class="tl-date">${esc(it.date)}</span>
              <span class="tl-act">${esc(it.action)}
                <button class="tl-open" data-open="${it.notice_id}">${esc(it.notice_title)}</button>
              </span>
              <span class="tag kind">${esc(it.role || "")}</span>
              ${it.days_left >= 0
                ? `<span class="tl-left">剩 ${it.days_left} 天</span>`
                : '<span class="tl-left is-due">已过期</span>'}
            </li>`).join("")}
        </ol>
      </div>`;
    }).join("");
  } catch (e) {
    /* 时间轴嵌在任务视图里，重试只重取这一块，不动上面的统计 */
    failBlock(box, e, "tasks", renderMyTimeline);
  }
}

async function renderTasks() {
  /* 时间轴独立取数：即使待办接口失败，时间轴仍要能显示出来 */
  renderMyTimeline();
  /* 只认当前登录角色：未登录就给一句明确引导，
     不要再借 users[0] 的待办冒充「当前用户」的。 */
  const u = state.currentUser;
  if (!u) {
    $("#taskStats").innerHTML = "";
    $("#agendaList").innerHTML = "";
    $("#taskList").innerHTML =
      '<div class="card empty">请先点击右上角头像登录角色账号，登录后这里会列出你的待办与截止日程。</div>';
    return;
  }
  /* 与其它视图同一套骨架：统计与两张列表先摆灰块，取到数据再原地替换，
     避免「空列表 → 满列表」时整片区域高度突变。 */
  $("#taskStats").innerHTML = `<div class="sk-block sk-stats" aria-hidden="true">
    ${Array.from({ length: 4 }, () => '<div class="skeleton sk-pill"></div>').join("")}</div>`;
  $("#agendaList").innerHTML = skeletonRows(2, "sk-list");
  $("#taskList").innerHTML = skeletonRows(3, "sk-list");
  try {
    const [agenda, tasks] = await Promise.all([
      api(`/api/users/${u.id}/agenda?days=30`),
      api(`/api/users/${u.id}/tasks`),
    ]);

    const todoN = tasks.filter((t) => t.status === "todo").length;
    const doingN = tasks.filter((t) => t.status === "doing").length;
    const doneN = tasks.filter((t) => t.status === "done").length;
    $("#taskStats").innerHTML = `
      <div class="task-stat"><b>${todoN}</b><span>待开始</span></div>
      <div class="task-stat"><b>${doingN}</b><span>进行中</span></div>
      <div class="task-stat"><b>${doneN}</b><span>已完成</span></div>
      <div class="task-stat hl"><b>${agenda.length}</b><span>30 天内截止</span></div>`;

    $("#agendaList").innerHTML = agenda.length
      ? agenda.map((x) => `
        <div class="list-item" onclick="openNotice(${x.notice_id})">
          <div class="li-main">
            <div class="li-title">${esc(x.title)}</div>
            <div class="li-meta"><span>${esc(x.category)}</span><span>${esc(x.deadline)}</span></div>
          </div>
          ${dueChip(x.deadline)}
        </div>`).join("")
      : '<div class="empty">未来 30 天没有截止事项</div>';

    const statusMap = { todo: "待开始", doing: "进行中", done: "已完成" };
    $("#taskList").innerHTML = tasks.length
      ? tasks.map((t) => `
        <div class="list-item">
          <div class="li-main">
            <div class="li-title" style="${t.status === "done" ? "text-decoration:line-through;color:var(--text-tertiary)" : ""}">${esc(t.notice_title)}</div>
            <div class="li-meta"><span>${statusMap[t.status] || t.status}</span>${t.deadline ? `<span>${esc(t.deadline)}</span>` : ""}</div>
          </div>
          <button class="btn sm" onclick="cycleTask(${t.id},'${t.status}')">${t.status === "done" ? "重置" : "推进"}</button>
        </div>`).join("")
      : '<div class="empty">还没有待办，可在通知详情中点击「加入待办」</div>';
  } catch (e) {
    /* 统计与日程是同一次取数里的两块，失败时必须一起收掉骨架，
       否则页面上会一直挂着两排灰块，看起来像还在加载。 */
    $("#taskStats").innerHTML = "";
    $("#agendaList").innerHTML = "";
    failBlock("#taskList", e, "tasks", renderTasks);
  }
}

async function cycleTask(id, cur) {
  const next = cur === "todo" ? "doing" : cur === "doing" ? "done" : "todo";
  try {
    await api(`/api/tasks/${id}`, { method: "PATCH", body: { status: next } });
    renderTasks();
  } catch (e) {
    toast(e.message, true);
  }
}

/* ---------------- 采集中心 ---------------- */
const crawlState = { sources: [], selected: new Set(), result: null, picked: new Set(), loaded: false };

async function renderSources() {
  if (crawlState.loaded) return;
  const box = $("#srcList");
  box.innerHTML = skeletonRows(3, "sk-sources");
  try {
    const d = await api("/api/sources");
    crawlState.sources = d.sources || [];
    crawlState.loaded = true;
    crawlState.sources.filter((s) => s.enabled).slice(0, 3).forEach((s) => crawlState.selected.add(s.id));
    renderSourceList();
  } catch (e) {
    /* crawlState.loaded 是「只取一次」的闸门，失败时不能置位，
       否则重试会直接被 return 掉。 */
    crawlState.loaded = false;
    failBlock(box, e, "sources", renderSources);
  }
}

function renderSourceList() {
  const box = $("#srcList");
  if (!crawlState.sources.length) {
    box.innerHTML = '<div class="empty">没有可用的采集源</div>';
    return;
  }
  box.innerHTML = crawlState.sources.map((s) => `
    <label class="src-item${s.enabled ? "" : " off"}">
      <input type="checkbox" data-src="${esc(s.id)}" ${crawlState.selected.has(s.id) ? "checked" : ""} ${s.enabled ? "" : "disabled"} />
      <div class="src-main">
        <b>${esc(s.name)}</b>
        <span class="src-url">${esc(s.url)}</span>
      </div>
    </label>`).join("");
  $$("#srcList input[data-src]").forEach((cb) => cb.addEventListener("change", () => {
    if (cb.checked) crawlState.selected.add(cb.dataset.src);
    else crawlState.selected.delete(cb.dataset.src);
  }));
}

function renderCrawlTable() {
  const box = $("#crawlTable");
  const blocks = (crawlState.result && crawlState.result.sources) || [];
  const rows = [];
  blocks.forEach((b) => (b.items || []).forEach((it, i) => rows.push({ b, it, key: b.source.id + "#" + i })));

  if (!rows.length) {
    const inc = $("#crawlIncremental") && $("#crawlIncremental").checked;
    const skipped = (crawlState.result && crawlState.result.sources || [])
      .reduce((n, b) => n + ((b.stats && b.stats.skipped_known) || 0), 0);
    const msg = inc && skipped
      ? `这些栏目的最新 ${skipped} 条都已收录，暂无新内容。可关闭「只看新内容」查看全部，或稍后再抓。`
      : "没有抓取到内容，可能是站点改版或网络不可达";
    box.innerHTML = `<div class="empty">${esc(msg)}</div>`;
    $("#crawlCount").textContent = "0 条";
    return;
  }

  const stat = crawlState.result.summary || {};
  const skipped = blocks.reduce((n, b) => n + ((b.stats && b.stats.skipped_known) || 0), 0);
  $("#crawlCount").textContent = `共 ${rows.length} 条新内容${skipped ? ` · 跳过已收录 ${skipped} 条` : ""} · 成功源 ${stat.ok}/${stat.sources} · 耗时 ${stat.elapsed}s`;

  box.innerHTML = `<table class="crawl-grid">
    <thead><tr><th></th><th>标题</th><th>栏目</th><th>发布时间</th><th>字数</th><th>状态</th></tr></thead>
    <tbody>${rows.map(({ b, it, key }) => `
      <tr class="${it.duplicate ? "dup" : ""}">
        <td><input type="checkbox" data-key="${esc(key)}" ${crawlState.picked.has(key) ? "checked" : ""} ${it.duplicate ? "disabled" : ""} /></td>
        <td>
          <a href="${esc(it.url)}" target="_blank" rel="noopener">${esc(it.title)}</a>
          <div class="crawl-snip">${esc(it.preview || "")}</div>
        </td>
        <td class="muted">${esc(b.source.name.replace("武汉理工大学-", ""))}</td>
        <td class="muted">${esc(it.publish_date || "—")}</td>
        <td class="muted">${it.chars || 0}</td>
        <td>${it.duplicate ? '<span class="tag">已收录</span>'
          : it.error ? '<span class="tag bad">详情失败</span>'
          : '<span class="tag ok">新</span>'}</td>
      </tr>`).join("")}</tbody></table>`;

  $$("#crawlTable input[data-key]").forEach((cb) => cb.addEventListener("change", () => {
    if (cb.checked) crawlState.picked.add(cb.dataset.key);
    else crawlState.picked.delete(cb.dataset.key);
    $("#btnCrawlImport").disabled = crawlState.picked.size === 0;
  }));
  $("#btnCrawlImport").disabled = crawlState.picked.size === 0;
}

async function doCrawl() {
  const ids = Array.from(crawlState.selected);
  if (!ids.length) return toast("请至少选择一个采集栏目", true);
  const btn = $("#btnCrawl");
  /* 抓取要挨个访问校方站点，耗时以秒计；不锁按钮时连点会同时跑多轮爬取 */
  await withBusy(btn, "抓取中…", async () => {
    $("#crawlCount").textContent = "抓取中…";
    try {
      const d = await api("/api/sources/preview", {
        method: "POST",
        body: {
          source_ids: ids,
          limit: Number($("#crawlLimit").value) || 6,
          max_pages: Number($("#crawlPages").value) || 1,
          incremental: $("#crawlIncremental") ? $("#crawlIncremental").checked : true,
        },
      });
      crawlState.result = d;
      crawlState.picked = new Set();
      (d.sources || []).forEach((b) => (b.items || []).forEach((it, i) => {
        if (!it.duplicate) crawlState.picked.add(b.source.id + "#" + i);
      }));
      renderCrawlTable();
      const failed = (d.summary && d.summary.failed) || [];
      toast(failed.length ? `抓取完成，${failed.length} 个源失败` : `抓取完成：${d.total} 条`, failed.length > 0);
    } catch (e) {
      toast(e.message, true);
      $("#crawlCount").textContent = "抓取失败";
    }
  });
}

async function importCrawl() {
  const items = [];
  ((crawlState.result && crawlState.result.sources) || []).forEach((b) => (b.items || []).forEach((it, i) => {
    if (!crawlState.picked.has(b.source.id + "#" + i)) return;
    items.push({
      title: it.title,
      content: it.content || "",
      url: it.url,
      publish_date: it.publish_date || "",
      unit: b.source.name,
      attachments: it.attachments || [],
    });
  }));
  if (!items.length) return toast("没有选中的条目", true);

  const btn = $("#btnCrawlImport");
  /* 导入会写入数据库并可能触发逐条解析，重复提交会重复建条目 */
  await withBusy(btn, "导入中…", async () => {
    try {
      const d = await api("/api/sources/import", {
        method: "POST",
        body: { items, auto_analyze: $("#crawlAnalyze").checked },
      });
      toast(`导入完成：新增 ${d.created} 条，跳过重复 ${d.skipped} 条${d.analyzed ? `，已解析 ${d.analyzed}` : ""}`);
      crawlState.result = null;
      crawlState.picked = new Set();
      $("#crawlTable").innerHTML = '<div class="empty">已导入，可再次抓取以查看新内容</div>';
      $("#crawlCount").textContent = "尚未抓取";
    } catch (e) {
      toast(e.message, true);
    }
  });
}

/* ---------------- 录入弹窗 ---------------- */
let modalTab = "manual";

function openModal() {
  $("#modalMask").classList.add("open");
  $("#inPublish").value = new Date().toISOString().slice(0, 10);
}

function closeModal() { $("#modalMask").classList.remove("open"); }

async function submitModal() {
  const btn = $("#modalSubmit");
  /* 录入是「建通知 + 立刻解析」两步写操作，连点会重复建条目 */
  await withBusy(btn, "处理中…", async () => {
    try {
      if (modalTab === "manual") {
        const body = {
          title: $("#inTitle").value.trim(),
          content: $("#inContent").value.trim(),
          publish_time: $("#inPublish").value,
          source_name: $("#inSource").value.trim(),
        };
        if (body.title.length < 2) throw new Error("请填写通知标题");
        if (body.content.length < 10) throw new Error("请粘贴通知正文");
        const n = await api("/api/notices", { method: "POST", body });
        await api(`/api/notices/${n.id}/analyze`, { method: "POST" });
        toast("录入并解析完成");
        closeModal();
        $("#inTitle").value = ""; $("#inContent").value = ""; $("#inSource").value = "";
        if (state.view === "notices") renderNotices(); else switchView("notices");
        openNotice(n.id);
      } else {
        const url = $("#inUrl").value.trim();
        if (!url) throw new Error("请填写网页链接");
        const n = await api("/api/notices/ingest/url", { method: "POST", body: { url, auto_analyze: true } });
        toast("采集并解析完成");
        closeModal();
        if (state.view === "notices") renderNotices(); else switchView("notices");
        openNotice(n.id);
      }
    } catch (e) {
      toast(e.message, true);
    }
  });
}

async function batchAnalyze(btn) {
  /* 批量解析可能在服务端跑一段时间，未锁定时连点会并发触发多次 */
  await withBusy(btn, "批量解析中…", async () => {
    try {
      const d = await api("/api/notices/batch-analyze?only_unparsed=true", { method: "POST" });
      toast(`批量解析完成：成功 ${d.success} / ${d.total}`);
      if (state.view === "notices") renderNotices(); else renderDashboard();
    } catch (e) {
      toast(e.message, true);
    }
  });
}

/* ---------------- AI 解析实验室（不入库，展示四层保护） ---------------- */
const LAB_SAMPLES = {
  contest: {
    title: "关于举办2026年数学建模竞赛校内选拔的通知",
    publish: "2026-11-01",
    content: "各学院：为选拔优秀队伍参加全国大学生数学建模竞赛，学校决定举办2026年校内选拔赛。参赛对象为全校2024级、2025级本科生，专业不限。请各班于2026年11月20日前将报名表、个人简历各一份提交至教务处教务科，逾期不予受理。报名开始时间为2026年11月5日。联系人：张老师，联系电话 027-87654321，地点：南湖校区教务处 302 室。",
  },
  news: {
    title: "我校学子在全国大学生数学建模竞赛中喜获佳绩",
    publish: "2026-11-01",
    content: "近日，2026年全国大学生数学建模竞赛结果公布，我校代表队获得全国一等奖两项、二等奖三项。比赛中，同学们展现了扎实的数学功底与团队协作能力。校领导对获奖同学表示祝贺，并鼓励更多同学参与学科竞赛。据悉，本次赛事共吸引来自全国千余所高校的队伍参加。",
  },
  scholar: {
    title: "关于开展2026年度国家奖学金评审工作的通知",
    publish: "2026-10-15",
    content: "各学院：根据学校安排，现启动2026年度国家奖学金评审工作。申请对象为我校全日制本科二年级及以上学生，要求学业成绩排名位于专业前10%，且无违纪记录。请符合条件的学生于2026年10月30日前填写《国家奖学金申请审批表》并提交至所在学院学生工作办公室，同时上传成绩单与获奖证明材料。咨询电话：027-87650000。",
  },
};

const LAB_FIELDS = [
  ["category", "通知分类"], ["deadline", "办理截止"], ["start_time", "报名开始"],
  ["target", "适用对象"], ["conditions", "资格条件"], ["materials", "报名材料"],
  ["apply_method", "办理方式"], ["grade_requirement", "年级限制"],
  ["major_requirement", "专业限制"], ["registration_start", "报名起始"],
  ["event_time", "活动时间"], ["location", "地点"], ["contact", "联系方式"],
];

function switchQaTab(name) {
  const tabs = ["chat", "lab"];
  const tab = tabs.includes(name) ? name : "chat";
  state.qaTab = tab;
  $$("#qaTabs .seg-btn").forEach((b) => {
    const active = b.dataset.tab === tab;
    b.classList.toggle("active", active);
    b.setAttribute("aria-selected", active ? "true" : "false");
    // 漫游 tabindex：只有当前页签留在 Tab 序列里，方向键切换由 keydown 处理
    b.setAttribute("tabindex", active ? "0" : "-1");
  });
  tabs.forEach((t) => {
    const pane = $("#qaPane" + t.charAt(0).toUpperCase() + t.slice(1));
    if (pane) pane.hidden = t !== tab;
  });
  if (tab === "lab") loadAiStatus(true);
}

function fillLabSample(key) {
  const item = LAB_SAMPLES[key];
  if (!item) return;
  $("#labTitle").value = item.title;
  $("#labPublish").value = item.publish;
  $("#labContent").value = item.content;
  $("#labResult").innerHTML = "";
  $("#labHint").textContent = "已填入示例，点「开始解析」查看字段证据与规则校验。置信度不是录取概率或正确率。";
}

function labCell(label, value, evidence) {
  const has = value !== null && value !== undefined && value !== "" &&
    !(Array.isArray(value) && value.length === 0);
  const shown = Array.isArray(value) ? value.join("、") : String(value == null ? "" : value);
  return `<div class="lab-field">
    <span class="lab-field-label">${esc(label)}</span>
    <div class="lab-field-value ${has ? "" : "muted"}">${has ? esc(shown) : "—（原文未给出）"}</div>
    ${has && evidence ? `<div class="lab-field-ev">原文依据：${esc(evidence)}</div>` : ""}
  </div>`;
}

function renderLabResult(d) {
  const ev = d.evidence || {};
  const checks = d.checks || {};
  const passed = checks.passed || [];
  const issues = checks.issues || [];
  const warnings = checks.warnings || [];
  const fields = LAB_FIELDS.map(([k, label]) => labCell(label, d[k], ev[k])).join("");
  const degrade = d.degraded
    ? `<div class="ans-warn">本次由模型降级到规则引擎（${esc(d.fallback_kind || "unknown")}）：${esc(d.fallback_reason || "模型不可用")}</div>`
    : "";
  const engine = d.engine === "llm" ? "大模型 + 规则校验" : "内置规则引擎（离线兜底）";
  const review = d.needs_review
    ? '<span class="lab-badge warn">建议人工复核</span>'
    : '<span class="lab-badge ok">校验通过</span>';
  const checkRows = [
    ...issues.map((x) => `<div class="check-row bad"><b>硬异常</b><span>${esc(x)}</span></div>`),
    ...warnings.map((x) => `<div class="check-row warn"><b>软提示</b><span>${esc(x)}</span></div>`),
    ...passed.map((x) => `<div class="check-row ok"><b>通过</b><span>${esc(x)}</span></div>`),
  ].join("");
  $("#labResult").innerHTML = `
    <div class="card lab-result">
      <div class="card-head"><h2>解析结果</h2>${review}</div>
      <div class="lab-kpis">
        <div class="lab-kpi"><span>解析引擎</span><strong>${esc(engine)}</strong></div>
        <div class="lab-kpi"><span>文体判别</span><strong>${esc(d.genre_label || "—")}</strong></div>
        <div class="lab-kpi"><span>办理截止</span><strong>${esc(d.deadline || "未识别")}</strong></div>
        <div class="lab-kpi"><span>加权置信度</span><strong>${Number(d.confidence || 0).toFixed(2)}</strong></div>
      </div>
      ${degrade}
      ${d.review_note ? `<p class="hint">复核说明：${esc(d.review_note)}</p>` : ""}
      <p class="hint">模型：${esc(d.model_name || "—")}；置信度为规则加权值，非录取概率。</p>
    </div>
    <div class="card lab-result">
      <div class="card-head"><h2>结构化字段与原文依据</h2><span class="hint">每个非空字段都要能在原文定位</span></div>
      <div class="lab-fields">${fields}</div>
    </div>
    <div class="card lab-result">
      <div class="card-head"><h2>规则校验 C1—C9</h2><span class="hint">通过 ${passed.length} 项 · 硬异常 ${issues.length} 项 · 软提示 ${warnings.length} 项</span></div>
      <div class="lab-checks">${checkRows || '<div class="check-row"><span>本次无校验记录</span></div>'}</div>
    </div>`;
}

async function runLab() {
  const title = $("#labTitle").value.trim();
  const content = $("#labContent").value.trim();
  if (content.length < 10) { toast("请粘贴至少 10 个字的通知原文", true); return; }
  const btn = $("#btnLabRun");
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = "解析中…";
  $("#labResult").innerHTML = '<div class="card empty">正在解析并核对原文…</div>';
  try {
    const d = await api("/api/ai/preview", {
      method: "POST", timeout: 80000,
      body: {
        title, content,
        publish_time: $("#labPublish").value || "",
        use_llm: $("#labRuleOnly").checked ? false : null,
      },
    });
    renderLabResult(d);
    loadAiStatus(true);
  } catch (e) {
    $("#labResult").innerHTML = `<div class="card empty">解析失败：${esc(e.message)}</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = original || "开始解析";
  }
}

/* ---------------- 启动 ---------------- */
function registerServiceWorker() {
  /* 注册结果写进 state：离线壳状态如实记录，不再把失败静默吞掉 */
  state.pwa = { supported: "serviceWorker" in navigator, secure: !!window.isSecureContext, reason: "" };
  if (!state.pwa.supported) {
    state.pwa.reason = "当前浏览器不支持 Service Worker";
    return;
  }
  const secureHost = location.protocol === "https:"
    || ["localhost", "127.0.0.1"].includes(location.hostname);
  if (!secureHost) {
    state.pwa.reason = "局域网 http 非安全上下文，离线壳不启用（页面与接口照常可用）";
    return;
  }
  navigator.serviceWorker.register("/sw.js")
    .then(() => { state.pwa.registered = true; })
    .catch((err) => {
      state.pwa.reason = "离线壳注册失败：" + ((err && err.message) || "未知原因");
    });
  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    state.pwa.installable = true;
    window.__pwaInstallEvent = e;
  });
}

function bindNetworkStatus() {
  const bar = $("#offlineBar");
  const sync = () => { if (bar) bar.hidden = navigator.onLine !== false; };
  window.addEventListener("online", () => { sync(); toast("网络已恢复"); });
  window.addEventListener("offline", () => { sync(); toast("已进入离线模式，AI 问答需联网", true); });
  sync();
}

function openSheet() {
  $("#sheetMask").classList.add("show");
  $("#moreSheet").classList.add("show");
  document.body.classList.add("sheet-open");
}

function closeSheet() {
  $("#sheetMask").classList.remove("show");
  $("#moreSheet").classList.remove("show");
  document.body.classList.remove("sheet-open");
}

function applyGlobalSearch(value) {
  state.keyword = String(value || "").trim();
  state.page = 1;
  $("#noticeSearch").value = state.keyword;
  $("#globalSearch").value = state.keyword;
  if (!state.keyword) return;
  if (state.view !== "notices") switchView("notices");
  else renderNotices();
}

/* ---------------- 多端接入 ---------------- */
const PLATFORM_META = {
  web: { name: "Web 单页", desc: "原生 HTML/CSS/JS，无构建依赖", icon: "i-home" },
  pwa: { name: "PWA", desc: "可安装到桌面，离线可读已解析内容", icon: "i-cloud" },
  miniprogram: { name: "微信小程序", desc: "原生工程，tabBar 四页 + 详情页", icon: "i-mobile" },
  calendar: { name: "日历订阅", desc: "ICS 推送截止日期，不依赖推送权限", icon: "i-calendar" },
};

async function renderMobile() {
  $("#mobileKpi").innerHTML = '<div class="kpi skeleton"></div>'.repeat(4);
  $("#platformList").innerHTML = skeletonCards(2, 84, "sk-grid");
  $("#pushBody").innerHTML = skeletonRows(3, "sk-push");

  try {
    const s = await api("/api/wechat/status");
    state.mobileStatus = s;

    const platforms = s.platforms || {};
    const keys = Object.keys(platforms);
    const online = keys.filter((k) => platforms[k].available).length;

    $("#mobileKpi").innerHTML = [
      kpi("接入端", online, `共 ${keys.length} 个入口共享同一后端`, "accent"),
      kpi("登录模式", s.enabled ? "微信" : "游客", s.login || "", ""),
      kpi("订阅消息", s.push_ready ? "可用" : "未配置",
        s.template_configured ? "模板已配置" : "缺少模板 ID，降级为站内提醒",
        s.push_ready ? "" : "warn"),
      kpi("票据有效期", s.token_ttl_days + " 天",
        "过期由前端静默重登，用户无感", ""),
    ].join("");

    $("#mobileModeHint").textContent = s.mode || "";

    $("#platformList").innerHTML = keys.map((k) => {
      const p = platforms[k] || {};
      const meta = PLATFORM_META[k] || { name: k, desc: "", icon: "i-file" };
      const entry = p.entry || p.manifest || p.subscription || "";
      return `<div class="platform-row">
        <span class="pf-ico"><svg aria-hidden="true" focusable="false"><use href="#${meta.icon}"/></svg></span>
        <div class="pf-main">
          <div class="pf-name">${esc(meta.name)}
            <span class="tag ${p.available ? "tag-success" : "tag-gray"}">${p.available ? "可用" : "关闭"}</span>
          </div>
          <div class="pf-desc">${esc(meta.desc)}</div>
          ${entry ? `<div class="pf-entry">${esc(entry)}</div>` : ""}
        </div>
      </div>`;
    }).join("") || '<div class="empty">没有可用入口</div>';

    $("#mpConfig").innerHTML = [
      ["AppID / Secret", s.enabled ? "已配置" : "未配置（游客演示模式）"],
      ["订阅消息模板", s.template_configured ? "已配置" : "未配置"],
      ["登录方式", s.login || "-"],
      ["基础地址", location.origin],
    ].map(([k, v]) => `<div class="cfg-row"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join("");

    $("#pushState").textContent = s.push_ready ? "可用" : "未配置";
    $("#pushBody").innerHTML = s.push_ready
      ? `<p class="hint">用户在小程序详情页点「开启微信截止提醒」后，后端按「模板 + 通知」累计下发额度。
           微信一次性订阅的语义是「授权一次只能下发一条」，所以这里用计数而非布尔开关。</p>
         <div class="cfg-row"><span>下发时机</span><b>截止前 3 天 / 当天</b></div>
         <div class="cfg-row"><span>降级策略</span><b>无额度时转站内待办</b></div>`
      : `<p class="hint">未配置 <code>WECHAT_TEMPLATE_DEADLINE</code>，推送通道关闭。
           此时截止提醒走两条不依赖权限的路径：站内待办 + 日历订阅。</p>
         <div class="cfg-row"><span>影响范围</span><b>仅关闭微信下发，其它端不受影响</b></div>`;

    const uid = state.currentUser && state.currentUser.id;
    const calUrl = uid ? `${location.origin}/api/users/${uid}/calendar.ics` : `${location.origin}/api/calendar.ics`;
    $("#calUrl").value = calUrl;
    $("#calDownload").href = calUrl.replace(location.origin, "");

    updateConflictBadge();
  } catch (e) {
    failBlock(["#mobileKpi", "#platformList", "#pushBody"], e, "mobile", renderMobile);
  }
}

/* ---------------- 冲突治理 ---------------- */
const SEVERITY_META = {
  blocker: { text: "高", cls: "tag-danger" },
  warn: { text: "中", cls: "tag-warn" },
  info: { text: "低", cls: "tag-gray" },
};
const RESOLUTION_TEXT = {
  use_a: "采用前者",
  use_b: "采用后者",
  both_wrong: "两者均错",
  ignored: "忽略此项",
};

async function renderConflicts() {
  $("#conflictList").innerHTML = `${skeletonCards(3, 92, "sk-list")}`;
  try {
    const q = [];
    if (state.conflictSeverity) q.push(`severity=${state.conflictSeverity}`);
    if (state.conflictShowResolved) q.push("resolved=true");
    const res = await api("/api/conflicts" + (q.length ? "?" + q.join("&") : ""));
    const items = res.items || [];

    const sum = res.summary || {};
    const sev = sum.by_severity || {};
    const unresolved = sum.unresolved || 0;

    $("#conflictKpi").innerHTML = [
      kpi("冲突总数", sum.total || 0, unresolved ? `待裁决 ${unresolved} 项` : "全部已裁决",
        unresolved > 0 ? "warn" : ""),
      kpi("高优先级", sev.blocker || 0, "截止日期类冲突，影响可否报名", (sev.blocker || 0) > 0 ? "bad" : ""),
      kpi("中优先级", sev.warn || 0, "地点 / 联系方式 / 年级专业要求", ""),
      kpi("低优先级", sev.info || 0, "办理方式差异", ""),
    ].join("");

    $("#conflictSummary").textContent = items.length
      ? `当前显示 ${items.length} 条`
      : "未发现冲突";

    if (!items.length) {
      $("#conflictList").innerHTML = `<div class="card empty">
        未发现冲突。<br/>
        <span class="hint">点右上角「重新扫描」可按最新入库内容重新比对；
        周期性通知（不同年份/学期）会自动按限定词排除，不会误报。</span>
      </div>`;
      updateConflictBadge();
      return;
    }

    $("#conflictList").innerHTML = items.map((c) => {
      const sev = SEVERITY_META[c.severity] || SEVERITY_META.info;
      const done = c.resolved;
      const body = done
        ? `<div class="cf-resolved">已裁决：${esc(RESOLUTION_TEXT[c.resolution] || c.resolution || "-")}</div>`
        : `<div class="cf-actions">
             <button class="btn ghost sm" data-act="use_a" data-id="${c.id}">采用前者</button>
             <button class="btn ghost sm" data-act="use_b" data-id="${c.id}">采用后者</button>
             <button class="btn ghost sm" data-act="both_wrong" data-id="${c.id}">两者均错</button>
             <button class="btn ghost sm" data-act="ignored" data-id="${c.id}">忽略</button>
           </div>`;
      return `<div class="card conflict-card ${done ? "is-resolved" : ""}">
        <div class="cf-head">
          <span class="tag ${sev.cls}">${sev.text}</span>
          <span class="tag tag-gray">${esc(c.field_label || c.field)}</span>
          ${done ? '<span class="tag tag-success">已裁决</span>' : ""}
        </div>
        <div class="cf-compare">
          <div class="cf-side">
            <div class="cf-side-title" title="${esc(c.title_a || "")}">${esc(c.title_a || "")}</div>
            <div class="cf-side-value">${esc(c.value_a || "（空）")}</div>
            <button class="cf-open" data-open="${c.notice_a_id}">查看原文</button>
          </div>
          <div class="cf-vs">≠</div>
          <div class="cf-side">
            <div class="cf-side-title" title="${esc(c.title_b || "")}">${esc(c.title_b || "")}</div>
            <div class="cf-side-value">${esc(c.value_b || "（空）")}</div>
            <button class="cf-open" data-open="${c.notice_b_id}">查看原文</button>
          </div>
        </div>
        ${c.reason ? `<div class="cf-reason">${esc(c.reason)}</div>` : ""}
        <div class="cf-foot">
          <span class="hint">字段：${esc(c.field_label || c.field)}</span>
          ${body}
        </div>
      </div>`;
    }).join("");

    updateConflictBadge();
  } catch (e) {
    /* 冲突页顶上还有四个 KPI 卡片，失败时一并收掉，避免只留下空壳数字 */
    $("#conflictKpi").innerHTML = "";
    $("#conflictSummary").textContent = "";
    failBlock("#conflictList", e, "conflicts", renderConflicts);
  }
}

async function updateConflictBadge() {
  const badge = $("#conflictBadge");
  if (!badge) return;
  try {
    const res = await api("/api/conflicts");
    const n = (res.summary && res.summary.unresolved) || 0;
    badge.textContent = n;
    badge.hidden = n === 0;
  } catch (e) {
    badge.hidden = true;
  }
}

async function scanConflicts() {
  const btn = $("#btnScanConflicts");
  btn.disabled = true;
  const old = btn.innerHTML;
  btn.textContent = "扫描中…";
  try {
    const res = await api("/api/conflicts/scan", { method: "POST" });
    toast(`扫描 ${res.scanned} 条通知，发现 ${res.found} 处冲突`);
    renderConflicts();
  } catch (e) {
    toast("扫描失败：" + e.message, true);
  } finally {
    btn.disabled = false;
    btn.innerHTML = old;
  }
}

async function resolveConflict(id, resolution) {
  try {
    await api(`/api/conflicts/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ resolution }),
    });
    toast("已记录裁决");
    renderConflicts();
  } catch (e) {
    toast("裁决失败：" + e.message, true);
  }
}

async function boot() {
  registerServiceWorker();
  bindNetworkStatus();
  bindEvents();
  try {
    const [info, users] = await Promise.all([api("/api/system/info"), api("/api/users")]);
    $("#engineName").textContent = info.llm_available ? info.llm_model : "规则引擎（离线）";
    state.users = users;
    loadLogin(users);   /* 恢复上次登录的角色；无登录态则保持未登录 */
    refreshIdentityUI();
    renderLoginRoles();
  } catch (e) {
    toast("初始化失败：" + e.message, true);
  }
  switchView(viewFromHash(), false);
  loadAiStatus(true);
}

function bindEvents() {
  $$(".nav-item").forEach((b) => b.addEventListener("click", () => switchView(b.dataset.view)));
  $$("#mobileNav button").forEach((b) => b.addEventListener("click", () => {
    if (b.dataset.view) switchView(b.dataset.view);
  }));
  $("#mobileMore").addEventListener("click", openSheet);
  $("#sheetMask").addEventListener("click", closeSheet);
  $$("#moreSheet .sheet-item[data-view]").forEach((b) => b.addEventListener("click", () => {
    closeSheet();
    switchView(b.dataset.view);
  }));
  $("#sheetAddNotice").addEventListener("click", () => { closeSheet(); openModal(); });
  $("#sheetBatchAnalyze").addEventListener("click", (e) => { closeSheet(); batchAnalyze(e.currentTarget); });
  $("#btnAiProbe").addEventListener("click", probeAi);
  const qaChat = $("#qaChat");
  if (qaChat) WELCOME_HTML = qaChat.innerHTML;
  const btnClear = $("#btnChatClear");
  if (btnClear) btnClear.addEventListener("click", clearChat);
  $("#btnMobileSearch").addEventListener("click", () => {
    const bar = $("#mobileSearchBar");
    bar.hidden = !bar.hidden;
    if (!bar.hidden) setTimeout(() => $("#mobileSearchInput").focus(), 60);
  });
  $("#mobileSearchClose").addEventListener("click", () => { $("#mobileSearchBar").hidden = true; });
  let mobileTimer = null;
  $("#mobileSearchInput").addEventListener("input", (e) => {
    clearTimeout(mobileTimer);
    mobileTimer = setTimeout(() => {
      applyGlobalSearch(e.target.value);
      $("#mobileSearchBar").hidden = true;
    }, 380);
  });
  $("#mobileSearchInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      clearTimeout(mobileTimer);
      applyGlobalSearch(e.target.value);
      $("#mobileSearchBar").hidden = true;
    }
  });
  const syncViewFromUrl = () => {
    const name = viewFromHash();
    if (name !== state.view) switchView(name, false);
  };
  window.addEventListener("hashchange", syncViewFromUrl);
  /* 移动端返回手势：pushState 产生的历史由 popstate 驱动 */
  window.addEventListener("popstate", syncViewFromUrl);
  $("#drawerClose").addEventListener("click", closeDrawer);
  $("#drawerMask").addEventListener("click", closeDrawer);
  $("#btnAddNotice").addEventListener("click", openModal);
  $("#btnBatchAnalyze").addEventListener("click", (e) => batchAnalyze(e.currentTarget));
  $("#btnCrawl").addEventListener("click", doCrawl);
  $("#btnCrawlImport").addEventListener("click", importCrawl);
  $("#btnBell").addEventListener("click", () => switchView("tasks"));
  $("#btnHelp").addEventListener("click", () => { switchView("qa"); switchQaTab("lab"); });
  $("#qaTabs").addEventListener("click", (e) => {
    const b = e.target.closest("[data-tab]");
    if (b) switchQaTab(b.dataset.tab);
  });
  // 页签是 tablist：左右方向键在页签间移动，Home/End 跳到首尾，这是读屏与键盘用户的默认预期
  $("#qaTabs").addEventListener("keydown", (e) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
    const btns = $$("#qaTabs .seg-btn");
    const idx = btns.findIndex((b) => b.classList.contains("active"));
    if (idx < 0) return;
    e.preventDefault();
    const last = btns.length - 1;
    const next = e.key === "Home" ? 0
      : e.key === "End" ? last
        : e.key === "ArrowLeft" ? (idx === 0 ? last : idx - 1)
          : (idx === last ? 0 : idx + 1);
    switchQaTab(btns[next].dataset.tab);
    btns[next].focus();
  });
  $("#labSamples").addEventListener("click", (e) => {
    const b = e.target.closest("[data-sample]");
    if (b) fillLabSample(b.dataset.sample);
  });
  $("#btnLabRun").addEventListener("click", runLab);
  $("#labContent").addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") runLab();
  });
  $("#crawlAll").addEventListener("change", (e) => {
    crawlState.picked = new Set();
    if (e.target.checked) {
      ((crawlState.result && crawlState.result.sources) || []).forEach((b) => (b.items || []).forEach((it, i) => {
        if (!it.duplicate) crawlState.picked.add(b.source.id + "#" + i);
      }));
    }
    renderCrawlTable();
  });
  $("#modalClose").addEventListener("click", closeModal);
  $("#modalCancel").addEventListener("click", closeModal);
  $("#modalSubmit").addEventListener("click", submitModal);
  $("#modalMask").addEventListener("click", (e) => { if (e.target.id === "modalMask") closeModal(); });

  $$("#ingestTabs .tab").forEach((t) => t.addEventListener("click", () => {
    modalTab = t.dataset.tab;
    $$("#ingestTabs .tab").forEach((x) => x.classList.toggle("active", x === t));
    $$(".tab-pane").forEach((p) => p.classList.toggle("active", p.id === "pane-" + modalTab));
  }));

  /* 全局搜索：跳到通知中心并带入关键词 */
  let globalTimer = null;
  $("#globalSearch").addEventListener("input", (e) => {
    clearTimeout(globalTimer);
    globalTimer = setTimeout(() => applyGlobalSearch(e.target.value), 320);
  });
  $("#globalSearch").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { clearTimeout(globalTimer); $("#globalSearch").dispatchEvent(new Event("input")); }
  });

  let searchTimer = null;
  $("#noticeSearch").addEventListener("input", (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.keyword = e.target.value.trim();
      state.page = 1;
      $("#globalSearch").value = e.target.value;
      renderNotices();
    }, 320);
  });
  $("#noticeCategory").addEventListener("change", (e) => {
    state.category = e.target.value; state.page = 1; renderCategoryChips(); renderNotices();
  });
  $("#noticeGenre").addEventListener("change", (e) => {
    state.genre = e.target.value; state.page = 1; renderNotices();
  });
  $("#noticeReview").addEventListener("change", (e) => {
    state.review = e.target.value; state.page = 1; renderNotices();
  });
  $$("#noticeQuick .chip").forEach((b) => b.addEventListener("click", () => applyQuick(b.dataset.quick)));

  /* 原文依据 → 正文高亮定位（事件委托，抽屉内容会被多次重绘） */
  document.addEventListener("click", (e) => {
    const t = e.target.closest(".evi[data-evi]");
    if (t) jumpToEvidence(t.dataset.evi);
  });

  /* 角色账号：头像处登录/切换，弹窗内角色卡片由 renderLoginRoles 绑定 */
  $("#userChip").addEventListener("click", openLogin);
  $("#loginClose").addEventListener("click", closeLogin);
  $("#loginCancel").addEventListener("click", closeLogin);
  $("#loginMask").addEventListener("click", (e) => { if (e.target.id === "loginMask") closeLogin(); });
  $$("#matchFilter .seg-btn").forEach((b) => b.addEventListener("click", () => {
    $$("#matchFilter .seg-btn").forEach((x) => x.classList.toggle("active", x === b));
    state.matchLevel = b.dataset.level;
    renderMatch();
  }));

  /* 推荐算法说明面板：按需拉取 /api/recommendation-model，避免首屏多一次请求 */
  $("#recoExplainBtn")?.addEventListener("click", toggleRecoExplain);

  /* ---- 多端接入 ---- */
  $("#btnRefreshMobile").addEventListener("click", renderMobile);
  $("#btnCopyCal").addEventListener("click", async () => {
    const val = $("#calUrl").value;
    try {
      await navigator.clipboard.writeText(val);
      toast("日历订阅链接已复制");
    } catch (e) {
      $("#calUrl").select();
      toast("请手动复制选中的链接", true);
    }
  });

  /* ---- 冲突治理 ---- */
  $("#btnScanConflicts").addEventListener("click", scanConflicts);
  $$("#conflictFilter .seg-btn").forEach((b) => b.addEventListener("click", () => {
    $$("#conflictFilter .seg-btn").forEach((x) => x.classList.toggle("active", x === b));
    state.conflictSeverity = b.dataset.sev;
    renderConflicts();
  }));
  $("#conflictResolved").addEventListener("change", (e) => {
    state.conflictShowResolved = e.target.checked;
    renderConflicts();
  });
  /* 冲突卡片里的按钮是动态渲染的，统一用委托绑定 */
  $("#conflictList").addEventListener("click", (e) => {
    const act = e.target.closest("[data-act]");
    if (act) {
      resolveConflict(act.dataset.id, act.dataset.act);
      return;
    }
    const open = e.target.closest("[data-open]");
    if (open) openNotice(open.dataset.open);
  });

  /* 个人时间轴里的通知标题也是入口，跟冲突卡片用同一种事件委托 */
  const tlBox = $("#myTimeline");
  if (tlBox) {
    tlBox.addEventListener("click", (e) => {
      const open = e.target.closest("[data-open]");
      if (open) openNotice(open.dataset.open);
    });
  }

  $("#qaSend").addEventListener("click", askQuestion);
  /* 「你可能还想问」是回答后动态插入的追问按钮，用事件委托绑定，
     避免内联 onclick 的引号转义问题，动态节点也能正常点击发问。 */
  $("#qaChat").addEventListener("click", (e) => {
    const chip = e.target.closest(".fu-chip");
    if (chip && chip.dataset.q) { askWith(chip.dataset.q); }
  });
  $("#qaInput").addEventListener("keydown", (e) => {
    /* 中文输入法组词时的 Enter 是「选字」，不是「发送」——移动端最常见的一类误操作 */
    if (e.key === "Enter" && !e.isComposing && e.keyCode !== 229) {
      e.preventDefault();
      askQuestion();
    }
  });
  $$("#quickQuestions .quick-card").forEach((b) => b.addEventListener("click", () => {
    $("#qaInput").value = b.dataset.q;
    askQuestion();
  }));

  document.addEventListener("keydown", (e) => {
    // 登录弹窗是模态的，Esc 只关它，不要顺手把抽屉和底部面板也关掉
    if (e.key === "Escape") {
      if ($("#loginMask")?.classList.contains("open")) { closeLogin(); return; }
      closeDrawer(); closeModal(); closeSheet();
    }
  });
}

document.addEventListener("DOMContentLoaded", boot);
