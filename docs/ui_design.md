# 界面设计规范（Arctic Glass Design System）

> 版本：v1.0（2026-11-07）
> 对应实现：`app/static/css/style.css`（1179 行，头部标注 Arctic Glass Design System v2.0）、`app/templates/index.html`（388 行）、`app/static/js/app.js`（1222 行）
> 验证：`python scripts/capture.py` 一键重生成 **24 张**界面截图，逐张按第 10 节清单核对；检查项见 `docs/test_report.md` 第 7 节，符合度见 `docs/design_compliance.md`。
> 关联：`docs/video_script.md`（演示分镜）、`docs/architecture.md`（界面层可信性落点）。

本规范是 Arctic Glass UI 全量重构后的**唯一界面口径**。所有色值、圆角、阴影、动效时长、布局尺寸、类名与视图名均逐项抄自 CSS/JS 源码，不写示意值。改样式先改 `:root` 变量，不在组件里散落十六进制常量。

## 1. 设计定位与命名

| 项 | 内容 |
| --- | --- |
| 产品名 | 高校智讯 / CampusInsight |
| 定位 | 高校信息智能服务平台 |
| 副标题 | 让校园信息，从「看见」到「看懂」，再到「行动」 |
| 主视觉 | 学生端**智能工作台**（默认视图 `home`） |
| 辅助 | 信息治理类看板（数据总览 / 采集中心），仅作能力证明 |

命名刻意避开「智慧校园综合管理平台」「校园 OA」「一站式服务大厅」等传统政务后台命名。要解决的是「学生看不完、看不懂、看错截止日期」，不是给管理员做后台；管理看板保留但退到侧栏第二组「信息治理」，比赛主视觉是学生端「看见 → 看懂 → 行动」闭环。

### 1.1 反面清单：不要做成什么

| 不要 | 原因 | 替代做法 |
| --- | --- | --- |
| 满屏蓝色科技大屏、深蓝渐变底、霓虹描边 | 与「信息可信」的冷静气质相反 | 浅灰白底 `#F6F8FC` + 白卡片，蓝色 `#2563EB` 只用于品牌与核心操作 |
| 传统 OA：深蓝顶栏、九宫格入口、密集表格 | 信息密度压过可读性 | 卡片列表 + 大留白 + chips 筛选 |
| 粒子、发光、大面积渐变、光晕铺满 | 装饰抢注意力 | 仅 Hero 一条淡渐变与一处淡光晕；`.brand-mark` 源码明确「不做发光、不做渐变」 |
| 把 AI 当装饰（紫色到处贴） | AI 是能力不是装饰 | 紫色 `--accent` 只出现在 AI 能力区与 AI 结论处 |
| 伪精确百分比 | 匹配分是加权分不是概率 | 标注「规则加权匹配度（非录取概率）」，分数与三档结论分开表述 |
| Emoji | 跨平台字形不一致 | 统一内联 SVG 线性图标（`#i-*` sprite），无 Emoji |

## 2. Design DNA（一句话总约束）

> **冷感极简 + Apple HIG 留白 + Material 3 组件规范 + Linear / Notion 信息组织。**

1. **底色与层次**：浅灰白底、白卡片、深灰文字，靠 1px 边框与克制阴影分层，不靠色块堆叠；
2. **颜色分工**：蓝色只用于品牌与核心操作，紫色只用于 AI 能力，绿 / 橙 / 红只表达「正常 / 临近 / 紧急」截止状态，其余中性灰；
3. **信息组织**：Linear 式分组导航、Notion 式卡片与标题层级，用 `.section-head` / `.sec-title` 分段；
4. **动效克制**：150–300ms 标准缓动，只做「进入、悬停、按压、加载」四类反馈。

## 3. 设计变量（逐项抄自 `style.css` 的 `:root`）

### 3.1 色板

| 分组 | 变量 | 值 | 用途 |
| --- | --- | --- | --- |
| 品牌 | `--primary` / `--primary-hover` | `#2563EB` / `#1D4ED8` | 主按钮、激活态、品牌标记 / hover |
| 品牌 | `--primary-soft` / `--primary-light` | `#EFF6FF` / `#DBEAFE` | 激活导航底、Hero 渐变起点 / 聚焦描边 |
| AI | `--accent` / `--accent-hover` | `#7C3AED` / `#6D28D9` | AI 图标、可信度条、AI 标签文字（**仅 AI**） |
| AI | `--accent-soft` / `--accent-light` | `#F5F3FF` / `#EDE9FE` | AI 区域底色、Hero 渐变终点 / 证据竖线 |
| 状态 | `--success` / `--success-soft` | `#16A34A` / `#F0FDF4` | 正常 / 已解析 / 符合（填充、圆点、进度条） |
| 状态 | `--warning` / `--warning-soft` | `#D97706` / `#FFFBEB` | 临近 / 待复核 / 可能符合（填充、圆点、进度条） |
| 状态 | `--danger` / `--danger-soft` | `#DC2626` / `#FEF2F2` | 紧急 / 驳回 / 删除（填充、圆点、进度条） |
| 状态文字 | `--success-text` / `--warning-text` / `--danger-text` | `#15803D` / `#B45309` / `#B91C1C` | 浅底上的绿色 / 橙色 / 红色文字（**文字一律用这组**） |
| 表面 | `--bg` / `--surface` / `--surface-soft` | `#F6F8FC` / `#FFFFFF` / `#F8FAFC` | 页面底 / 卡片 / 输入框与字段单元 |
| 边框 | `--border` / `--border-light` | `#E5E7EB` / `#EEF2F7` | 常规边框 / 轻分隔线 |
| 文字 | `--text-primary` / `--text-secondary` / `--text-tertiary` | `#111827` / `#4B5563` / `#5F6B7A` | 标题 / 次要说明 / 提示与元信息 |

> 三级文字与三个状态色都按 WCAG AA（对比度 ≥ 4.5:1）反算过：原先的 `--text-tertiary #9CA3AF` 在白底只有 2.54、`--success #16A34A` 在 `#F0FDF4` 上只有 3.15，均不达标，故加深为上述取值（在站点全部 12 种浅色底上最低 4.57）。填充与描边不受影响，继续使用 `--success/--warning/--danger`（图形对比度按 3:1 判定，原值合规）。

> 类别标签另有 9 组 `[data-cat]` 配色（竞赛 `#EFF6FF`/`#1D4ED8`、奖学金 `#F5F3FF`/`#6D28D9`、科研项目、实习实践、就业信息、讲座报告、教学事务、校园活动、志愿服务），均为低饱和浅底深字，只作辅助识别。

### 3.2 圆角、阴影、动效、布局

| 类别 | 变量 | 值 |
| --- | --- | --- |
| 圆角 | `--radius-sm` / `--radius-md` / `--radius-lg` / `--radius-xl` / `--radius-pill` | `8px` / `12px` / `16px` / `20px` / `999px` |
| 阴影 | `--shadow-sm` / `--shadow-md` / `--shadow-lg` | `0 1px 2px rgba(15,23,42,.04)` / `0 8px 24px rgba(15,23,42,.06)` / `0 16px 40px rgba(15,23,42,.08)` |
| 动效 | `--ease-standard` | `cubic-bezier(.2, 0, 0, 1)` |
| 动效 | `--duration-fast` / `--duration-normal` / `--duration-slow` | `150ms` / `200ms` / `300ms` |
| 布局 | `--sidebar-w` / `--topbar-h` / `--content-max` | `248px` / `64px` / `1280px` |

字体栈（`html, body`）：`Inter` / `Noto Sans SC` / `PingFang SC` / `Microsoft YaHei` / `Hiragino Sans GB` / `system-ui` / `-apple-system` / `sans-serif`，基准 `14px`、行高 `1.6`。

## 4. 布局骨架

```
┌──────────────┬───────────────────────────────────────────────────────┐
│              │  topbar 64px 毛玻璃 rgba(255,255,255,.82)              │
│  sidebar     │  [标题/副标题] [全局搜索] [通知·帮助·用户·录入]          │
│  248px       ├───────────────────────────────────────────────────────┤
│              │  .scroll  padding: 28px 32px 56px                      │
│  智能工作台   │   ┌─────────────────────────────────────────────┐     │
│   首页       │   │ .container  width: min(100%, 1280px)        │     │
│   通知中心   │   │            margin-inline: auto               │     │
│   我的匹配   │   │  7 个 .view，仅 .view.active 可见             │     │
│   AI 助手    │   └─────────────────────────────────────────────┘     │
│   我的任务   │                                                       │
│  信息治理     │                                                       │
│   数据总览   │                                                       │
│   采集中心   │                                                       │
├──────────────┴───────────────────────────────────────────────────────┤
│  <768px：.mobile-nav 固定底部 62px，5 项（首页/通知/匹配/AI/任务）      │
└───────────────────────────────────────────────────────────────────────┘
```

- **侧栏** `.sidebar`：`248px`、白底、右侧 1px `--border-light`；`.nav-group-title` 分两组（智能工作台 / 信息治理）；`.nav-item.active` 用 `--primary-soft` 底 + 左侧 3px `--primary` 竖条。
- **顶栏** `.topbar`：`64px`，`rgba(255,255,255,.82)` + `backdrop-filter: saturate(180%) blur(16px)`，含全局搜索、通知铃铛、用户头像、录入按钮。
- **内容区**：`.container { width: min(100%, var(--content-max)); margin-inline: auto; }` 最大宽 `1280px`；`.scroll` 提供 `28px 32px 56px` 内边距。
- **间距**：8pt 节奏为主（8 / 16 / 24 / 32），局部 4pt 微调（10 / 12 / 14 / 20）；卡片内边距 18–20px，栅格 gap 12–20px。
- **动效**：页面进入 `viewIn`（opacity 0→1 + `translateY(8px)`→0，`300ms`）；卡片 hover `translateY(-3px)`；按钮 `:active` `scale(.97)`；Skeleton `shimmer 1.4s`。

### 4.1 三档响应式

| 断点 | 侧栏 | 顶栏 | 栅格 | 底部导航 |
| --- | --- | --- | --- | --- |
| ≥1200px（默认） | 248px 完整 Sidebar | 64px | `stat-grid`/`kpi-grid` 4 列，`notice-list` 2 列，`home-grid` 1.55fr:1fr | 隐藏 |
| 768–1199px（`@media (max-width: 1199px)`） | 72px Icon Rail，隐藏文字/分组标题/侧栏脚 | 64px，隐藏副标题 | `kpi-grid`/`stat-grid` 2 列，`grid-2`/`notice-list`/`home-grid` 单列 | 隐藏 |
| <768px（`@media (max-width: 767px)`） | `display: none` | 56px sticky，隐藏搜索与用户名 | 卡片单列，`field-grid` 单列 | 显示 fixed 62px、5 项 |

另有 `@media (prefers-reduced-motion: reduce)` 把动画/过渡压到 `.01ms`。

## 5. 页面清单（9 个视图）

视图由 `.nav-item[data-view]` 与 `#mobileNav` 的 `data-view` 驱动 `switchView()`，`VIEW_META` 定义顶栏标题与副标题，`state.view` 默认 `"home"`。

| 视图 | 顶栏标题 | 导航分组 | 核心区块 | 对应截图 |
| --- | --- | --- | --- | --- |
| `home` | 智能工作台 | 智能工作台 | greeting、AI 洞察 Hero、4 张 Stats Card、推荐机会、即将截止、可信链路 5 节点 | `shot-home.png` / `shot-home-ai.png` / `shot-home-opps.png` / `shot-home-full.png` |
| `dashboard` | 数据总览 | 信息治理 | 4 张 KPI、分类分布、近 7 日趋势、7 天截止、解析质量、渠道/文体构成 | `shot-dashboard.png` / `shot-genres.png` |
| `notices` | 通知中心 | 智能工作台 | 快捷 chips、分类 chips、搜索/文体/状态工具栏、双列卡片列表、分页 | `shot-notices.png` / `shot-genre.png` |
| `sources` | 采集中心 | 信息治理 | 采集源多选、每源条数/翻页/入库后解析、抓取结果表与重复标记 | `shot-sources.png` / `shot-ingest.png`（录入弹窗） |
| `match` | 我的匹配 | 智能工作台 | 画像卡、三档 seg 筛选、匹配度进度条、三档结论、逐条理由 | `shot-match.png` |
| `qa` | AI 助手 | 智能工作台 | 4 个快捷问题卡、对话气泡、溯源引用、输入框 | `shot-qa.png` |
| `tasks` | 我的任务 | 智能工作台 | 4 项统计条（待开始/进行中/已完成/30 天内截止）、**我的时间轴（按本人身份归一的轨道节点）**、即将截止、待办、状态推进 | `shot-tasks.png` |
| `mobile` | 多端接入 | 信息治理 | 接入端统计（Web/PWA/小程序/日历）、游客演示模式说明、微信票据有效期、订阅消息配额 | `shot-mobile-platform.png` |
| `conflicts` | 冲突治理 | 信息治理 | 冲突总数/高优先级/中优先级/低优先级 KPI、逐条跨来源比对卡片（截止/地点/联系方式/报名方式）、四态裁决按钮 | `shot-conflicts.png` |

详情抽屉（`.drawer`）与录入弹窗（`.modal`）是跨视图浮层，对应 `shot-detail.png` / `shot-fields.png` / `shot-fields-full.png` / `shot-evidence.png` / `shot-review.png` / `shot-table.png` / `shot-table-full.png` / `shot-ingest.png`。

## 6. 组件规范

### 6.1 卡片

`.card`：白底、1px `--border-light`、`--radius-lg`(16px)、20px 内边距、`--shadow-sm`；hover `translateY(-3px)` + 描边转 `--primary-light`。变体 `.stat-card` / `.opp-card` / `.notice-card` / `.trust-card` 共享同一套反馈。标题用 `.card-head h2`（14.5px + 左侧 3px 蓝竖条），段落标题用 `.section-head` / `.section-title`。

### 6.2 按钮五态（`.btn`，`--duration-fast` 150ms 过渡）

| 态 | 表现 |
| --- | --- |
| 默认 | 白底、1px `--border`、10px 圆角、40px 高 |
| hover | `--surface-soft` 底 + `#CBD5E1` 描边；`.primary` 转 `--primary-hover` 并 `translateY(-1px)` |
| active | `scale(.97)` |
| disabled | `opacity: .45`、`cursor: not-allowed`、取消位移 |
| focus-visible | 全局 `outline: 2px solid var(--primary)` + `outline-offset: 2px` |

变体：`.primary`（蓝，核心操作）/ `.ghost` / `.ai`（`--accent-soft` 底，AI 操作）/ `.sm`（32px）/ `.wide`。蓝色只给「录入通知」「查看推荐」等核心操作，不把整页按钮染蓝。

### 6.3 标签系统

`.tag` 为基础（pill 圆角、11.5px、浅底深字），颜色只作辅助识别：`.cat` / `.ok` / `.warn` / `.bad` / `.ai` / `.kind` / `.genre-news` / `.genre-pub` 及 9 组 `[data-cat]` 类别配色。筛选用 `.chip` / `.chip.active`（`--primary-soft` 底 + `--primary-hover` 字），分段用 `.seg` / `.seg-btn.active`。**标签不竖排、不重叠**，窄容器 `flex-wrap` 换行。

### 6.4 截止时间状态点

`.deadline-dot` + `.dl-*` 五态，由 `app.js` 的 `deadlineInfo()` 按剩余天数计算：

| 类 | 颜色 | 触发条件 |
| --- | --- | --- |
| `.dl-normal` | 绿 `--success` | 剩余 > 7 天 |
| `.dl-near` | 橙 `--warning` | 剩余 4–7 天 |
| `.dl-urgent` | 红 `--danger` | 今天 / 明天 / 剩余 ≤ 3 天 |
| `.dl-over` | 灰 `--text-tertiary` + `#CBD5E1` 点 | 已截止 |
| `.dl-none` | 浅灰 `#E2E8F0` | 未识别截止 |

卡片内用 `.due-chip.dl-*`，看板紧凑列表用 `deadlineTag()` 映射到 `.tag.ok/.warn/.bad`。绿 / 橙 / 红只表达「正常 / 临近 / 紧急」。

### 6.5 AI 区域紫色语义（克制）

`--accent` 家族**只**出现在 AI 能力直接相关处：`.hero-tag`、`.hero::before` 淡光晕、`.insight-card`（AI Structured Insight 头卡，渐变 `linear-gradient(135deg, var(--accent-soft) 0%, #FFFFFF 72%)`）、`.ai-label`、`.tag.ai`、`.btn.ai`、`.stat-icon.ai`、`.evi`（证据竖线与「原文依据 · 点击定位」）、`.genre-note`、`.pipe-node.ai`、`.quick-card:hover`、`.qa-hero .ai-mark`。**AI 是能力而不是装饰**，不因「有 AI」把整页刷紫。

### 6.6 字段-证据块（本轮修复）

抽屉用 `.field-grid`（`repeat(2, 1fr)` 双列）展示 14 类结构化字段。**一个字段 + 它的原文依据绑定为一个网格单元 `.field-block`**，由 `app.js` 的 `fieldBlock(k, v, eviKey, wide)` 生成，长字段用 `.field-block.wide` 跨两列；证据块 `.evi` 不再独占整行打乱双列网格。`.field-cell` 内 `.k` 为字段名、`.v` 为值；点击 `.evi[data-evi]` 由事件委托调用 `jumpToEvidence()`，在 `.content-box` 把对应句子包成 `<mark>`（`#FEF08A` 黄底）并 `scrollIntoView` 平滑定位。规则校验报告与人工审核三态渲染在 `.check-panel`（通过 / 修正字段 / 驳回重解析 + 驳回原因输入）。

### 6.7 Skeleton

`.skeleton` 用 `linear-gradient(90deg, #F1F5F9 25%, #E9EEF6 50%, #F1F5F9 75%)` + `shimmer 1.4s infinite linear`。`renderHome()` 等 4 个接口时先渲染 Hero、4 张 Stats Card 与推荐机会卡的骨架占位，数据到位再替换，避免空白闪烁。

## 7. 「AI 结论 → 结构化信息 → 原文证据」三级可信链路的界面落点

| 层级 | 界面落点 | 说明 |
| --- | --- | --- |
| AI 结论 | 首页 `.hero`（AI 智能洞察 + 三项指标）、抽屉 `.insight-card`（AI 已完成结构化解析 + **信息可信度** `.conf-bar`）、`.tag.ai` | 只陈述系统里真实存在的量；字面事实注明「由程序从原文确定性抽取，模型不生成这些值」 |
| 结构化信息 | 抽屉 `.field-grid` 的 14 类字段 `.field-block`、首页「可信链路」`.pipeline` 5 节点 | 字段与证据同组展示、长字段跨两列；5 节点为：公开站点采集 → AI 结构化解析 → 规则校验 C1—C11 → 原文证据绑定 → 个性化匹配与行动 |
| 原文证据 | `.evi[data-evi]` → `.content-box mark` 黄色高亮定位；AI 助手 `.cites` 可点开对应通知 | 点证据即回到原文句子；`.check-panel` 把 `checks` 报告与人工审核三态摊开给审核人 |

一句话：**AI 负责理解，程序负责约束，原文负责证明**——三级链路在界面上必须都能点得到、看得见。

## 8. 首页智能工作台详解（`home`，新的默认视图）

`boot()` 结束时调用 `switchView("home")`，`#view-home` 带 `active` 类。

1. **greeting**：`.greeting h2` 按时段显示「早上好 / 下午好 / 晚上好，{姓名}」，副标题即产品副标题；
2. **AI 洞察 Hero** `.hero`：渐变 `linear-gradient(135deg, #EFF6FF 0%, #FFFFFF 55%, #F5F3FF 100%)`、`--radius-xl`(20px)，含 `.hero-tag`、更新时间、标题「已为你结构化解析 N 条校园通知」、三个 `.hero-metric`（符合你的机会 / 7 天内截止 / 待人工复核）与两个 CTA；
3. **4 张 Stats Card** `.stat-grid`：新通知、我的匹配、即将截止、待完成任务，每张含 `.stat-icon`（「我的匹配」用 `.ai` 紫图标）与 `.stat-sub`；
4. **推荐给你的机会**（左栏）：3 张 `.opp-card`，含类别标签、AI 匹配标签、`.opp-score`、`.match-bar` 进度条、`.opp-reasons` 逐条理由与「加入任务」。分数取 5–98 加权分，**界面明确标注「规则加权匹配度 · 非录取概率」，不冒充百分比概率**，结论分档与分数分开表述；
5. **右侧两卡**：「即将截止」（未来 7 天，`.due-item` + `.due-chip` 四态状态点）与「为什么可以相信这些结果」`.trust-card`（`.pipeline` 可信链路 5 节点，AI 节点紫色）。

设计意图：学生第一屏就拿到「与我有关、能行动、可追溯」的信息，而不是先看一张管理向的统计大屏。

## 9. 移动端适配与多端安装

- `<768px`：`.sidebar { display: none }`，`.layout` 改块级，`.topbar` 变 `56px + safe-area-inset-top` 的 sticky 栏；`.mobile-nav` 为 `position: fixed; bottom: 0` 的 62px 毛玻璃栏，6 项（首页 / 通知 / 匹配 / AI / 任务 / 更多），激活项 `--primary` 蓝；
- 「更多」打开底部动作面板（`.sheet`），承载移动端放不下的数据总览、采集中心、录入通知与批量解析，避免为管理功能再挤一条导航；
- 窄屏隐藏 topbar 内的桌面搜索框，改为独立的 `.mobile-search` 条（图标按钮展开），保留全局搜索能力；
- `.scroll` 底部留 96px 内边距，避免内容被固定底栏遮住；底部导航与弹层均带 `env(safe-area-inset-bottom)`；
- 触控目标：`.icon-btn` 38px、底部导航项 ≥ 48px、`.sheet-item` 50px；统一 `touch-action: manipulation` 消除双击缩放；
- 移动端表单控件字号固定 16px，避免 iOS / HarmonyOS 浏览器聚焦时自动放大页面；
- `.stat-grid` / `.kpi-grid` 2 列、`.quick-grid` / `.field-grid` / `.row` 1 列，`.field-cell.wide` / `.field-block.wide` 回落不跨列，卡片单列；`.drawer` 全宽，`.qa-wrap` 高度自适应；切换视图时只滚动内容区，不再把移动端停在页面中部；
- AI 助手在移动端不自动聚焦输入框（避免一进页面就弹键盘），Enter 发送会跳过中文输入法的组词确认（`isComposing` / `keyCode 229`）；
- 断网时固定显示 `.offline-bar`，明确「已缓存页面可浏览，AI 问答需联网」；
- PWA：`/manifest.webmanifest`（`standalone` + 三个快捷方式）、`/sw.js`（导航网络优先、静态资源缓存优先、`/api/` 不缓存）、192 / 512 / maskable 图标；Windows、Android、HarmonyOS 浏览器都从同一份清单识别安装入口；`backdrop-filter` 全站补齐 `-webkit-` 前缀兼容移动 WebView；
- AI 助手为两个页签（`.qa-tabs` + `.qa-pane`）：智能问答 / AI 解析实验室。解析实验室用 `.lab-field` 把每个结构化字段与它的原文证据绑在同一张卡里，`.lab-checks` 按硬异常 / 软提示 / 通过三档展示 C1—C11；降级原因复用既有 `.ans-warn`，不新增视觉语言。`.qa-pane[hidden]` 必须显式 `display:none`，否则会被 `display:flex` 顶掉导致两个页签同时显示；
- 可访问性基线：三级文字与三个状态文字色均按 WCAG AA（4.5:1）取值，状态色的填充 / 描边与文字色分开（`--success` vs `--success-text`），改动色值必须同步本节 3.1 色板表；移动端正文最小 11.5px，仅 `.pipe-dot`、`.icon-btn .badge` 这类纯装饰数字允许更小；所有可点元素最短边 ≥ 32px（`.cf-open` 用上下 9px 内边距撑高、`.seg-btn` 移动端 `min-height:32px`），用 `python scripts/check_a11y.py` 复核；
- 对话框语义：详情抽屉、录入弹窗、登录弹窗都是 `role="dialog"` + `aria-modal="true"` + `aria-labelledby` 指向各自标题；纯图形关闭按钮一律带 `aria-label`。登录弹窗打开时把焦点交给第一张角色卡片，关闭后还给触发它的顶栏头像按钮，Esc 也只关当前这一层，避免键盘用户焦点落到 body 上丢失上下文；
- 页签语义：`.qa-tabs` 是 `role="tablist"`，两个 `.seg-btn` 是 `role="tab"` 并用 `aria-controls` 绑定 `.qa-pane` 的 `role="tabpanel"` + `aria-labelledby`；采用漫游 tabindex（只有激活页签是 `tabindex="0"`），支持左右方向键循环、`Home`/`End` 跳到首尾；
- 操作反馈：`#toast` 是 `role="status"` + `aria-live="polite"` + `aria-atomic="true"` 的 live region，读屏会在「已加入待办」「保存失败」这类操作后直接播报，不必再去找视觉提示；
- 加载态：所有取数视图在等待期间渲染 `.skeleton` 灰块（`skeletonRows()` / `skeletonCards()`），不用「加载中…」这类会跳动的文字占位；灰块尺寸与最终内容同形同高，替换时不改变布局。`.sk-block` / `.sk-grid` / `.sk-list` / `.sk-stats` 只在 `@keyframes shimmer` 之后定义，未新增颜色变量；
- 忙碌态：八个写操作入口统一走 `withBusy(btn, text, fn)`——进入即 `disabled` + `dataset.busy="1"` + `spinner` 文案，退出（含异常）在 `finally` 恢复原 HTML 与 `disabled` 状态，重复点击被 `dataset.busy` 直接短路；
- 动效降级：骨架的 `shimmer` 与视图切换动画都受 `prefers-reduced-motion: reduce` 约束（时长压到 .01ms、阶梯动画置 `none`），但灰块占位本身保留，因为它承担的是布局稳定性而非装饰；
- 导航当前项：左侧 `.nav-item` 与移动端 `#mobileNav` 的当前视图除了 `.active` 视觉类，同时用 `aria-current="page"` 标记（`switchView()` 里成对设置与清除），`.active` 对辅助技术不可见，光有它读屏无法判断当前在哪一页；
- 名称可用：页面上有主导航、移动端底部导航、更多功能三个 `<nav>`，各自带 `aria-label` 区分；搜索框、筛选下拉、AI 提问框、日历订阅地址都补了 `aria-label`——只用 `placeholder` 不算无障碍名称，输入一个字符后它就消失了，读屏随即失去控件语义；
- 截图脚本用 `390×844` 视口：`shot-mobile.png` 用**视口截图**（非 full_page）才能拍到 fixed 底部导航，`shot-mobile-full.png` 用 `full_page` 拍完整长页。**24 张截图已按当前界面重生成；真机截图仍待验证阶段补齐。**
- 智能问答的 6 张 `.quick-card` 示例全部对应真实通知，措辞不含「适合我 / 这个」等无依据指代；
  回答底栏新增 `.ans-intent`（识别到的意图）与 `.ans-fields`（依据结构化字段条数）两个克制的胶囊标签，
  沿用 `--surface-soft` / `--success-soft` 底色，不新增强调色、不发光；`.qaMeta` 同步展示意图与含结构化字段的语料数。

## 10. 截图清单（24 张，`python scripts/capture.py` 一键重生成）

| 文件名 | 内容 | 检查要点 |
| --- | --- | --- |
| `shot-home.png` | 首页智能工作台（默认视图） | greeting、Hero、4 Stats、推荐机会、即将截止、可信链路 5 节点齐全，无横向滚动 |
| `shot-home-ai.png` | `.hero` 元素截图：AI 洞察卡 | 淡渐变三段色、`.hero-tag` 紫、三个指标、两个 CTA 完整 |
| `shot-home-opps.png` | `#homeOpps` 元素截图：3 张推荐机会卡 | 匹配度 + 进度条 + 逐条理由标签 +「规则加权匹配度 · 非录取概率」文案 |
| `shot-home-full.png` | 首页完整长图（临时加高视口 1440×1800） | 整页视口截图，避免 `.scroll` 内部滚动容器元素截图的错位，页面不截断 |
| `shot-dashboard.png` | 数据总览（信息治理） | 4 KPI、分类分布、7 日趋势、截止事项、解析质量、渠道/文体构成 |
| `shot-notices.png` | 通知中心 | 快捷 chips + 分类 chips + 搜索工具栏 + 双列卡片列表，**非密集表格** |
| `shot-ingest.png` | `.modal` 录入弹窗 | 手动录入 / 粘贴链接双 Tab、字段、底部按钮 |
| `shot-detail.png` | 新闻稿详情抽屉（文体闸门） | `.genre-note` 紫调说明、加入待办置灰、无办理事项标记 |
| `shot-fields.png` | `.drawer` 元素截图：AI Structured Insight + 字段 | `.insight-card` 紫调、信息可信度、14 类字段双列网格 |
| `shot-fields-full.png` | 14 字段完整长图（1440×2100） | `.field-block` 字段-证据绑定成组，不占整行打乱网格，审核区完整 |
| `shot-evidence.png` | `.content-box` 元素截图：原文依据高亮 | 黄色 `<mark>` `#FEF08A`、滚动定位、已排除 Toast 浮层 |
| `shot-review.png` | `.check-panel` 元素截图 | 通过 / 拦截 / 提示计数 + 三态按钮 + 驳回原因输入 |
| `shot-match.png` | 我的匹配 | 画像卡、三档 seg、`score` 显示「X 分」、三档结论、逐条理由 |
| `shot-qa.png` | AI 助手 | 4 个快捷问题卡、引擎状态卡（含测试连接）、对话气泡、引用片段与耗时、紫色语义克制 |
| `shot-tasks.png` | 我的任务 | 4 项统计条、即将截止、我的待办、状态推进按钮 |
| `shot-sources.png` | 采集中心 | 采集源列表、抓取参数、结果表、重复标记 |
| `shot-genre.png` | 通知中心按「新闻资讯」筛选 | 文体闸门后的空字段列表、卡片文体标签、无办理事项标记 |
| `shot-genres.png` | 看板「文体构成」卡片元素截图 | 3 类文体统计完整，元素截图避开内部滚动区 |
| `shot-mobile.png` | 移动端首屏（390×844 视口截图） | fixed 6 项底部导航可见、topbar sticky、卡片单列（截图待重拍） |
| `shot-mobile-full.png` | 移动端长图（full_page） | 长页内容完整、底栏不重复遮挡内容（截图待重拍） |

## 11. 明确不做的事（收束清单）

1. **不做重蓝色**：不出现深蓝底大屏、蓝色渐变导航、通栏蓝色 hero；蓝色只用于品牌与核心操作。
2. **不做密集表格**：通知中心、首页、匹配页用卡片列表；表格只留在采集中心的抓取结果预览。
3. **不做粒子 / 发光 / 大面积渐变**：不引入粒子背景、霓虹描边、发光阴影、全屏渐变；唯一渐变是 Hero 与 AI 洞察头卡的淡色三段过渡。
4. **不写伪精确百分比**：匹配分是 `matcher.py` 的 5–98 加权分，界面统一标注「规则加权匹配度（非录取概率）」，分数与三档结论分开表述，**不写「96% 匹配」之类把加权分当录取概率的说法**。
5. **不用 Emoji**：统一内联 SVG 线性图标（`#i-*` sprite），几何字符仅 `◈`、`✕`。
6. **不把 AI 当装饰**：紫色只在 AI 能力区出现，不做「机器人吉祥物」「AI 光效」。
7. **不造界面假数据**：数字全部来自真实 REST 接口（`/api/dashboard/stats`、`/api/users/{id}/matches` 等），前端不写死演示数字。
