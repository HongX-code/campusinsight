# 高校智讯 CampusInsight

> 高校信息智能服务平台
> 让校园信息，从「看见」到「看懂」，再到「行动」
> 第十一届华为 ICT 大赛 · 中国创新赛 · 赛题1：基于华为云产品打造 AI 创新应用

高校的通知散落在教务处、学工部、团委、各学院等十几个站点，格式各异、期限紧迫。学生每天面对的是"翻十几个网站、读几千字、还可能看错截止日期"的低效信息获取过程。

**高校智讯**把这一过程压缩为三步：**采集 → 结构化解析 → 个性化匹配**。系统把一篇几千字的高校通知转换为带原文依据的结构化字段（类别、报名截止、适用对象、资格条件、报名材料、报名方式），再结合学生的年级、专业和兴趣给出"符合 / 可能符合 / 暂不符合"的结论与判断理由，并支持基于已收录通知的溯源问答。

---

## 一、核心能力

| 模块 | 能力 | 关键设计 |
| --- | --- | --- |
| 通知中心 | 手动录入、链接采集、检索筛选、增删改查 | 双数据入口，采集失败不影响演示；`content_hash` 去重 |
| 采集中心 | 按栏目批量抓取、翻页、预览、勾选入库 | 配置化采集源；抓取与解析解耦；重复条目自动标记 |
| 文体判别 | 自动区分事务通知 / 公示公告 / 新闻资讯 | 新闻稿不抽报名类字段，避免"看起来合理"的假数据；办理信号一票否决 |
| AI 智能解析 | 分类 + 14 类结构化字段 + 截止语义类型 + 原文依据 + 置信度 | 四层保护 + 规则校验：结构校验 / 文体闸门 / 原文接地 / 规则兜底 |
| 结构化字段契约 | 年级、专业、报名起止、活动时间、地点、联系人电话 | 确定性抽取，不交给生成式模型；每字段附原文证据 |
| 规则校验与审核 | 时间倒挂、证据失配、字段泄漏等 9 项校验 + 通过/修正/驳回 | AI 负责理解，程序负责约束，原文负责证明 |
| 个性化匹配 | 三级结论 + 七因子分项 + 逐条判断理由 | 适用对象/专业/兴趣/条件/时效/新鲜度/完整度七因子加权，专业年级学历走结构化硬条件，不靠字符串包含猜 |
| 通知推荐 | 召回 → 排序 → 多样性重排，附「为什么推给你」 | 跳过项按原因计数（未解析/新闻稿/已截止/硬冲突/重复副本），离线规则引擎可用 |
| 智能问答 | 意图识别 + 结构化字段 + 溯源回答 | 只答已收录内容，直接给出截止日期 / 材料清单 / 申请条件等确切值并附原文依据 |
| 时间管理 | 待办、截止提醒、30 天议程 | 与解析结果联动 |
| 数据看板 | 分类分布、解析质量、截止预警、采集渠道、录入趋势 | 支持人工复核闭环 |
| 界面与体验 | 9 视图学生端闭环 + 24 张界面截图 | Arctic Glass Design System / 蓝色=操作、紫色=AI / 三档响应式，详见 `docs/ui_design.md` |

### 为什么做"接地校验"

大模型在通知解析上最典型的问题是**日期幻觉**：把"发布时间"或"活动举办时间"当成"报名截止时间"。高校场景下这个错误的代价是学生直接错过报名。

系统的处理方式不是"相信模型"，而是**每条抽出的日期都必须能在原文中定位到对应表述**，定位不到就置空并标记"待人工复核"。这条规则写在 `app/services/analyzer.py` 的 `date_grounded()` 中，并被 `tests/test_smoke.py` 单独覆盖。

---

## 二、技术架构

```
                     ┌─────────────────────────────┐
   浏览器（原生 HTML/CSS/JS）  │  单页应用，无构建步骤         │
                     └──────────────┬──────────────┘
                                    │ REST / JSON
                     ┌──────────────▼──────────────┐
                     │  FastAPI（app/main.py）      │
                     │  routers: notices / users /  │
                     │           ai / dashboard     │
                     └──────────────┬──────────────┘
                                    │
        ┌───────────────────────────┼───────────────────────────┐
        │                           │                           │
┌───────▼────────┐        ┌─────────▼─────────┐       ┌─────────▼────────┐
│ 业务服务层      │        │ AI 服务层          │       │ 数据访问层        │
│ crawler        │        │ llm_client        │       │ SQLAlchemy ORM   │
│ cleaner        │        │ analyzer          │       │ notices /        │
│ matcher        │        │ classifier        │       │ notice_analysis  │
│ qa             │        │ extractor         │       │ users / tasks    │
└───────┬────────┘        └─────────┬─────────┘       └─────────┬────────┘
        │                           │                           │
        │                  ┌────────▼─────────┐        ┌────────▼────────┐
        │                  │ 华为云 MaaS       │        │ 华为云 RDS      │
        │                  │ (OpenAI 兼容)     │        │ for MySQL       │
        │                  └──────────────────┘        └─────────────────┘
        │                           │
        │                  ┌────────▼─────────┐
        │                  │ 规则引擎（离线兜底）│
        │                  └──────────────────┘
        │
┌───────▼─────────┐
│ 华为云 DCS Redis │  （可选，未配置自动降级为内存缓存）
└─────────────────┘
```

**技术选型原则：七天可交付。**

- 前端用原生 HTML/CSS/JS：单文件即可演示，不需要 Node 环境、不需要打包；
- 后端用 FastAPI：同一进程同时提供静态页面与 JSON 接口，部署时只有一个进程；
- 数据库用 SQLAlchemy 抽象：本地 SQLite 零配置起步，改一个环境变量即切换到华为云 RDS for MySQL，业务代码零改动；
- AI 层做接口隔离：`llm_client.chat()` 是唯一入口，换模型不碰业务代码。

---

## 三、目录结构

```
gaoxiao-zhixun/
├── app/
│   ├── main.py               # 应用入口、路由装配、异常兜底
│   ├── config.py             # 配置（.env 读取、分类枚举）
│   ├── serializers.py        # 请求模型 + 序列化
│   ├── routers/
│   │   ├── notices.py        # 通知 CRUD / 采集 / 解析 / 人工修正
│   │   ├── sources.py        # 采集中心：源列表 / 抓取预览 / 勾选入库
│   │   ├── users.py          # 用户画像 / 匹配 / 待办 / 议程
│   │   ├── ai.py             # 解析预览 / 问答 / 引擎状态
│   │   └── dashboard.py      # 统计看板
│   ├── services/
│   │   ├── collector.py      # 采集引擎（列表发现 / 翻页 / 详情 / 附件 / 去重）
│   │   ├── crawler.py        # 单篇链接采集（复用 collector 的请求与解析）
│   │   ├── cleaner.py        # 文本清洗（噪声剔除、章节识别）
│   │   ├── textutil.py       # 归一化、日期识别、章节切块
│   │   ├── classifier.py     # 分类词典与纠偏
│   │   ├── genre.py          # 文体判别（新闻稿闸门）与截止语义类型
│   │   ├── extractor.py      # 字段抽取（日期打分、对象归一化、材料条目）
│   │   ├── fields.py         # 结构化字段契约（年级/专业/时间/地点/联络 + 证据）
│   │   ├── reltime.py        # 相对时间结合发布时间换算（本周五前 / 三个工作日）
│   │   ├── validator.py      # 规则校验 C1—C11（时间倒挂、接地、置信度…）
│   │   ├── conditions.py     # 资格条件原子化（11 类约束规则表）
│   │   ├── analyzer.py       # 解析编排 + 四层保护 + 统一校验出口（含文体闸门）
│   │   ├── llm_client.py     # 大模型适配（OpenAI 兼容）
│   │   ├── cache.py          # Redis / 内存缓存
│   │   ├── matcher.py        # 个性化匹配与理由生成
│   │   └── qa.py             # 检索 + 溯源问答
│   ├── database/
│   │   ├── models.py         # 四张核心表 + 四张多端治理表
│   │   ├── connection.py     # 引擎、会话、健康检查
│   │   └── init.sql          # RDS for MySQL 建表脚本
│   ├── templates/index.html  # 单页应用（9 视图骨架 + 线性图标 sprite）
│   └── static/
│       ├── css/style.css     # Arctic Glass Design System（设计变量 / 组件 / 三档响应式）
│       └── js/app.js         # 视图切换、数据渲染与三级可信链路交互
├── config/
│   └── sources.json          # 采集源与选择器配置（新增高校只改这里）
├── data/
│   ├── sample_notices.json   # 24 份构造样本（开发集）
│   ├── real_notices.json     # 42 份真实采集快照（含 URL / 字数 / 附件）
│   └── evaluation/
│       ├── test_cases.json     # 开发集人工标注（24 条）
│       ├── holdout_notices.json # 留出集通知（8 份，未参与调参）
│       ├── holdout_cases.json   # 留出集人工标注（8 条）
│       ├── eval_result.json     # 开发集评测明细（脚本生成）
│       └── holdout_result.json  # 留出集评测明细（脚本生成）
├── docs/                     # 需求 / 架构 / 数据库 / 接口 / 数据集 / 关键代码 / 部署 / 测试报告 / 视频脚本 / 界面设计规范 / CodeArts 提示词 / 截图（份数以 docs/metrics.json 现场快照为准）
├── codearts/                 # CodeArts 资产：提示词 11 份 + 模板 5 份 + 填写示范 + 单页作战台 + README
├── scripts/
│   ├── init_db.py            # 建表
│   ├── crawl.py              # 站点批量采集（--list / --all / --save / --import）
│   ├── import_samples.py     # 导入构造样本 + 真实采集快照
│   ├── reset_db.py           # 清空并重建演示数据（改解析逻辑后用）
│   ├── evaluate.py           # 解析效果评测（--set dev|holdout，--llm）
│   ├── real_audit.py         # 真实集解析质量审计（生成 docs/real_audit.md）
│   ├── benchmark.py          # 处理效率实测（生成 docs/benchmark.md）
│   ├── sync_metrics.py       # 把实测指标回灌到 README / 测试报告 / 视频脚本（幂等）
│   ├── capture.py            # 一键重生成 24 张界面截图
│   ├── codearts_tools.py     # doctor / bootstrap / split / index / verify：环境、骨架、索引、口径
│   └── ci_gate.py            # 构建与质量门禁（9 阶段，失败即停，不吞退出码）
├── tests/test_smoke.py       # 端到端自检（292 项，采集用例离线可跑）
├── requirements.txt
├── .env.example
├── run.py                    # 本地启动器（--check 体检 / --open 自动开浏览器）
├── start_server.bat          # Windows 一键启动：选解释器 → 体检 → 打开浏览器
└── start_server.sh           # Linux / macOS / WSL / Termux 一键启动（同一套体检）
```

---

## 四、快速开始

```bash
# 1. 安装依赖（Python 3.10+；本机 Windows 若 python 指向 3.13，请用 py -3.12）
pip install -r requirements.txt

# 2. 配置（可跳过，默认使用本地 SQLite + 规则引擎）
copy .env.example .env

# 3. 建表并导入演示数据
python scripts/import_samples.py

# 4. 启动（四选一）
python run.py                 # 先体检，再启动并打印可点击地址
python run.py --open          # 启动后自动打开浏览器
start_server.bat              # Windows 一键启动：自动选解释器 → 体检 → 打开浏览器
./start_server.sh             # Linux / macOS / WSL / Termux 一键启动

# 浏览器固定打开 http://127.0.0.1:8000/
```

> **不要访问 `http://0.0.0.0:8000/`**。`0.0.0.0` 是「监听所有网卡」的通配地址，
> **不是网址**——uvicorn 日志里出现它只说明服务在所有网卡上监听。
> 本机一律访问 `http://127.0.0.1:8000/` 或 `http://localhost:8000/`；
> 云端部署时浏览器访问 ECS 公网 IP / 域名，同样不是 `0.0.0.0`。
> 项目默认已改为只监听 `127.0.0.1`，需要对外暴露时才显式设置 `APP_HOST=0.0.0.0`。

### 4.1 页面打不开时，先跑一次体检

```bash
python run.py --check
```

它不启动服务，只逐项核对解释器版本、8 个运行依赖、端口占用、数据库文件与通知条数、
页面三件套资源，并打印可直接复制的修复命令。**真实故障优先按这张表定位：**

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 「无法访问此页面 / 0.0.0.0:8000」 | 把监听通配地址当成浏览器网址 | 改用 `http://127.0.0.1:8000/` |
| 连接被拒绝 / ERR_CONNECTION_REFUSED | 服务进程没启动，或已被 Ctrl+C 结束 | 跑 `python run.py`，保持窗口不关 |
| 端口被占用 | 另一个进程（或上一次未退出的服务）占着 8000 | `APP_PORT=8001 python run.py`，或按体检提示结束占用进程 |
| `ModuleNotFoundError: fastapi` | 本机 `python` 指向未装依赖的 3.13 | `py -3.12 run.py`，或 `py -3.12 -m pip install -r requirements.txt` |
| 页面能开但列表为空 | 数据库尚未导入演示数据 | `python scripts/import_samples.py` |
| 页面能开但接口 404 | 直接双击了 HTML 文件，没走后端 | 用 `python run.py` 启动后经 `127.0.0.1:8000` 访问 |

> **自检工具本身也要可信**：`verify` 的口径扫描已从「整文件白名单」收紧为**行级豁免**
> （只有带说明标记的引用行可放过）；`split --check` 会检出 `codearts/prompts/` 下多余的提示词文件；
> `sync_metrics.py` 对同一锚点的所有出现处全量回灌并打印处数；`verify` 硬核对文档端点数与实测值；
> `run.py --check` 用 `/api/health` 的 `app` 标识区分「本服务」与「别的程序占了端口」。
> 五项均做过缺陷注入反向验证（修复前全漏报、修复后全部变红），详见 `docs/test_report.md` T12。

#### 多端访问（Windows / Android / HarmonyOS）

系统按「响应式 Web + PWA」交付：一套 FastAPI 后端同时服务桌面与移动端，不额外打包 APK / HAP。

| 端 | 打开方式 | 安装为应用 |
| --- | --- | --- |
| Windows | Edge / Chrome 访问 `http://127.0.0.1:8000/` | 地址栏右侧「安装」图标，变成独立窗口 |
| Android | 手机浏览器访问电脑局域网地址 | Chrome 菜单 →「添加到主屏幕」 |
| HarmonyOS | 华为浏览器访问同一局域网地址 | 菜单 →「添加到桌面」；HarmonyOS NEXT 浏览器按 PWA 处理 |
| iOS / macOS / Linux | Safari / Chrome | 分享 →「添加到主屏幕」/ 安装 |

一键让手机 / 平板 / 鸿蒙设备访问（自动监听所有网卡并打印可访问地址）：

```bash
# Windows：双击 start_lan.bat，或
py -3.12 run.py --lan

# Linux / macOS / WSL / Android Termux
./start_lan.sh                # 等价于 python3 run.py --lan
```

`--lan` 会自动探测本机所有局域网 IPv4 并打印 `http://192.168.x.x:8000/`，
不必手工查 `ipconfig`；等价的手工方式仍是 `APP_HOST=0.0.0.0 python run.py`。
局域网地址以本行打印结果为准；`--lan` 启动后直接在浏览器打开即可。

- 防火墙需放行 8000 端口；`0.0.0.0` 仍然只是监听地址，不要写进浏览器。
- Service Worker 只在 HTTPS 或 `localhost` 下注册；用局域网 IP 的 http 访问时页面照常可用，
  离线壳不启用。正式部署请配置 HTTPS。
- 离线壳缓存页面与静态资源，接口一律不缓存；离线时 AI 问答与解析会提示联网。
- 移动端底部导航含「更多」，可进入数据总览与采集中心；窄屏顶部有独立搜索入口。
- AI 助手提供两个页签：**智能问答**（原文出处 + 引擎标识）、**AI 解析实验室**（粘贴通知即看
  字段证据、C1—C11 校验与降级原因，不写入数据库）。
- 智能问答的 6 个示例问题全部与演示库中真实通知对齐、可单轮精准回答：问材料就列材料清单、
  问条件就列申请条件、问截止就给确切日期；回答由已复核的结构化字段组装并带 `[n]` 出处，
  底部标注识别到的意图与依据字段数。示例不使用「适合我」「这个」这类无依据指代，
  也不写伪精确匹配度。纯指代问法会明确请你点名通知，不编造答案。
- `backdrop-filter` 全站补齐 `-webkit-` 前缀，安全区、16px 输入字号与触控尺寸按
  Android / HarmonyOS 浏览器约束处理。

> 状态：manifest / `sw.js` / 图标 / 响应式断点 / 触控适配**已就绪**；
> 局域网地址自动探测与一键启动脚本**已就绪**；
> 桌面（1440）与手机（390）两档视口的浏览器巡检已跑通（0 console 错误、0 横向溢出），
> `localhost` 下 Service Worker 注册成功；
> 真机安装与鸿蒙浏览器表现**待真机验证**，不以桌面浏览器的结果替代真机结论。

#### 云上部署（RDS for MySQL + DCS for Redis + ECS）

```bash
# 1) 开通数据服务（需已安装 Terraform，并 export HW_ACCESS_KEY / HW_SECRET_KEY）
cp deploy/cloud/terraform/terraform.tfvars.example deploy/cloud/terraform/terraform.tfvars
bash deploy/cloud/provision.sh

# 2) 部署应用（使用 auto-deploy 技能，复用上一步的 VPC/子网）
python .codeartsdoer/skills/auto-deploy/scripts/auto_deploy.py \
  --repo . --app-name gaoxiao-zhixun --region cn-north-4 \
  --network-mode reuse --existing-vpc-id <vpc_id> --existing-subnet-id <subnet_id> \
  --env-file deploy/cloud/cloud.env --yes

# 3) 验收
curl -s http://<ECS_IP>/api/health    # database.kind 应为 RDS
curl -s http://<ECS_IP>/api/ai/status # cache.kind 应为 DCS
```

- 选型依据、连接池/超时、成本对比：`docs/cloud_services.md`
- 平台矩阵与容器/桌面/移动交付：`docs/platform_delivery.md`
- 完整命令与清理方式：`deploy/README.md`
- 状态：**脚本已就绪，真实开通与公网验证待资源**；AK/SK 只走环境变量，不提交。

### 4.2 质量证据链（一次运行，全部数字现场复算）

「N 项自检全通过」只能说明断言没报错，不能说明断言有效。项目把验证结果做成可复现的证据链：

| 命令 | 作用 |
| --- | --- |
| `python scripts/mutation_audit.py` | 在临时副本注入 11 组真实业务缺陷（日期容错、规则校验、top_k 透传 / 截断、去重指纹、自检框架恒真化等），每组都必须让自检变红；主工程全程只读 |
| `python scripts/evidence.py --collect` | 现场跑完全部校验，写 `docs/metrics.json`、回灌全部对外口径，生成 `docs/quality_evidence.md` 与 `docs/质量证据总览.html` |
| `python scripts/evidence.py --check` | 只读核对文档口径与磁盘实测是否一致 |
| `python scripts/scale_sync.py --check` | 只体检回灌规则的锚点是否仍然命中 |

`docs/质量证据总览.html` 可离线打开、可打印，答辩时直接展示；每个数字都带复现命令与退出码，
边界清单明确列出**尚未验证**的华为云连通、公网部署、CodeArts 实操留痕与人工对照实验，不包装成已完成。

**口径不再手工同步**：源码行数、自检项数、文档份数由 `evidence.py` 现场统计到 `docs/metrics.json`，
再由 `scale_sync.py` 按结构锚点回灌到 README、技术文档、提示词主文档与申报材料脚本，
`codearts_tools.py verify` 现场复核概数与端点数。说明某一轮演进过程的历史叙述刻意保留。

### 4.3 给 CodeArts 的提示词体系（两层，别混用）

| 层 | 文档 | 用途 |
| --- | --- | --- |
| 日常开发（v1.x） | `docs/codearts_prompts.md` + `codearts/prompts/` 下 11 份独立文件 | P0 常驻约束 + P1—P10 任务提示词、Day 1—Day 7 排期、七类留痕方案 |
| 下一阶段实操（v2.0） | `docs/codearts_prompts_advanced.md` | B0 接手上下文注入块、A0 通用执行契约、A1—A10 任务提示词、验收矩阵、已知陷阱与状态边界 |

换人接手，或推进云端资源 / 实操留痕 / 提交前收口时：先把进阶文档 §2 的 B0 整段粘进新会话，
再复制对应 A 编号的提示词正文并附 A0 执行契约，改完按 §5 验收矩阵逐条核对。
`.codeartsdoer/AGENTS.md` 由客户端自动加载，内容一律指向 `docs/metrics.json` 现场快照、
**不写死易变数字**；`verify` 会检查它的锚点并把它纳入旧值行级扫描。
A 系列**不参与 `split`**，11 份独立提示词口径不变；两份文档与 `codearts/` 资产**都不是实操证据**。

自检与评测：

```bash
python scripts/crawl.py --all --limit 6 --save data/real_notices.json   # 抓取真实通知（需联网）
python tests/test_smoke.py                  # 292 项端到端自检（无需联网）
python scripts/evaluate.py                  # 开发集评测（24 份）
python scripts/evaluate.py --set holdout    # 留出集评测（8 份，检验泛化）
python scripts/real_audit.py                # 真实集质量审计（生成 docs/real_audit.md）
python scripts/benchmark.py                 # 处理效率实测（生成 docs/benchmark.md）
python scripts/sync_metrics.py              # 把实测指标回灌到全部 Markdown 材料
python scripts/capture.py                   # 一键重生成 24 张界面截图
python scripts/reset_db.py                  # 改动解析逻辑后重建演示数据

# CodeArts 资产与门禁（详见 docs/codearts_prompts.md 与 docs/codearts_prompts_advanced.md；python 按 doctor 输出替换）
python scripts/codearts_tools.py doctor     # 运行环境体检：给出正确的解释器前缀
python scripts/codearts_tools.py bootstrap  # 首次开工：一键生成 11_CodeArts使用记录/ 骨架
python scripts/codearts_tools.py bootstrap --check
python scripts/codearts_tools.py index      # 代码库索引覆盖自检
python scripts/codearts_tools.py split      # 主文档改动后重新生成独立提示词文件
python scripts/codearts_tools.py split --check
python scripts/codearts_tools.py verify     # 交付件口径体检
python scripts/codearts_tools.py verify --strict  # 门禁口径：任一检查被 SKIP 也判失败
python scripts/codearts_tools.py all        # 一次性跑完上述只读自检
python scripts/ci_gate.py                   # 9 阶段构建与质量门禁（含环境体检）
python scripts/mutation_audit.py            # 11 组业务缺陷变异测试（临时副本，主工程只读）
python scripts/check_a11y.py                # 版式与可访问性静态自检（网格轨道 / 文字对比度 / 移动端字号与触控目标）
python scripts/evidence.py --collect         # 现场采集证据链 + 口径回灌 + 生成单页总览
python scripts/evidence.py --check           # 只读核对口径

# 申报材料重建（脚本以 __file__ 定位资源，可从任意目录执行）
python ../../申报材料/build_ppt.py
python ../../申报材料/fill_form.py
python ../../申报材料/export_pdf.py         # 导出 PDF 并校验页数（防覆盖静默失败）
```

> **完整验收必须用依赖齐全的解释器**。`doctor --strict` 除必需依赖外还会检查
> `python-pptx / pypdf / python-docx`；缺任一项时 `verify` 会把 PPTX/PDF/DOCX 结构检查
> 记为 SKIP，看起来是「失败 0」，实际未验。此时 `verify --strict`、`ci_gate.py` 与
> `evidence.py --check` 会直接判失败。处理方式：按 `doctor` 输出切换解释器前缀，
> 或执行 `python -m pip install -r requirements.txt` 补齐后重跑门禁与证据采集。

文档索引：

| 文档 | 内容 |
| --- | --- |
| `docs/requirements.md` | 需求与功能边界 |
| `docs/architecture.md` | 技术架构与模块划分 |
| `docs/database.md` | 数据表设计 |
| `docs/api.md` | 接口说明 |
| `docs/dataset.md` | 数据集选取、标注口径、清洗流程 |
| `docs/real_audit.md` | 42 份真实站点的解析质量审计（脚本生成，可复现） |
| `docs/benchmark.md` | 处理效率实测（脚本生成，人工侧未实测则不填数） |
| `docs/design_compliance.md` | 三份设计文档的逐条符合度矩阵（实现位置 / 验证方式 / 状态） |
| `docs/key_code.md` | 关键代码片段的解读 |
| `docs/deployment.md` | 部署与华为云接入（含连通性自检脚本） |
| `docs/test_report.md` | 测试报告（自检 / 双集评测 / 缺陷复盘） |
| `docs/video_script.md` | 演示视频分镜脚本（5 分 30 秒，含录制检查清单） |
| `docs/ui_design.md` | 界面设计规范（Arctic Glass Design System）：Design DNA / 设计变量 / 9 视图 / 组件规范 / 三级可信链路 / 24 张截图清单 |
| `docs/codearts_prompts.md` | **CodeArts 全流程智能开发体系**：执行摘要、双闭环·三层证据方法论、就绪度矩阵、阅读导航与术语表；P0 常驻约束 + P1—P10 任务提示词、Day 1—Day 7 逐日排期、短卡片、七类留痕、红线与统一回归清单（§1.0 含运行环境口径） |
| `docs/codearts_prompts_advanced.md` | **v2.0 进阶提示词与执行契约**：契约式提示词三层（上下文 / 执行 / 验收）、B0 接手注入块、A0 通用执行契约、A1—A10 可直接投喂的任务提示词、验收矩阵、真实踩坑清单与状态边界；不写死易变数字，不参与 `split` |
| `codearts/README.md` 与 `CodeArts实操作战台.html` | 资产说明 + 单页执行看板：11 个可复制提示词、5 类留痕模板、1 份填写示范、逐日时间轴与留痕进度（本地持久化）；**醒目标注「模板非实操证据」** |

---

## 五、配置说明

全部配置集中在 `.env`，三项可选能力均为"配置即启用、不配置即降级"，不影响主流程。

### 1. 数据库（华为云 RDS for MySQL）

```env
DATABASE_URL=mysql+pymysql://user:password@<rds-host>:3306/gaoxiao_zhixun?charset=utf8mb4
```

云端首次部署先执行 `app/database/init.sql` 建库建表，或直接启动应用让其自动建表。
未配置时使用 `data/gaoxiao_zhixun.db`（SQLite），页面左上角会显示当前数据库类型。

### 2. 大模型（华为云 MaaS / ModelArts Studio）

```env
LLM_PROVIDER=maas
LLM_BASE_URL=https://api.modelarts-maas.com/v1
LLM_API_KEY=<你的密钥>
LLM_MODEL=DeepSeek-V3
```

接口遵循 OpenAI 兼容协议，因此也支持 DeepSeek、通义等任意兼容服务。
**未配置密钥时自动降级为内置规则引擎**，并在界面与 `/api/ai/status` 中明确标注当前引擎，不会把规则结果伪装成 AI 结果。

### 3. 缓存（华为云 DCS for Redis）

```env
REDIS_URL=redis://:<password>@<dcs-host>:6379/0
```

用于缓存重复解析结果（避免同一通知重复计费）与问答结果。未配置时使用进程内缓存。

### 4. 采集白名单

```env
CRAWL_ALLOWED_DOMAINS=whut.edu.cn,edu.cn,ac.cn
```

采集器只允许访问白名单域名，且显式拒绝内网地址与 localhost（防 SSRF）。

### 5. 数据脱敏（公开仓库前必跑）

`data/real_notices.json` 是真实站点快照，原文里带着工作人员的**个人手机号、个人邮箱、联系人姓名与校内系统内网 IP**。这些内容出现在校内页面上是正常的，但随仓库公开就等于把别人的联系方式再发布一次，所以入库前必须换成占位值。

只替换四类个人信息，业务事实一律不动：

| 类别 | 占位规则 |
| --- | --- |
| 个人手机号 | `1380000XXXX`（保留 11 位手机形态，正则仍可识别，但不指向真人） |
| 个人邮箱（qq/126/163 等） | `exampleN@example.com` |
| 联系人姓名 | 「姓 + 老师」，保留「这是个人在办这件事」的信息 |
| 内网 IP | `host-NNNN.example.edu.cn`，保留端口后缀 |

刻意**不改**三类：**校名与机构名**（作品要解决的问题域，抹掉就看不懂场景）、**机构角色邮箱**（`xgb@` / `yjsy@` 这类是部门对外公开信箱，不是个人信息）、**带区号的办公电话**（`027-87xxxxxx` 是单位总机，公开可查）。

```bash
python scripts/sanitize_data.py --check     # 只检查，有残留退出码 1
python scripts/sanitize_data.py             # 就地脱敏（幂等，重复跑不再变化）
python scripts/sanitize_demo_db.py --check  # 演示库同样检查
```

两点值得说明：

1. **真值不在代码里**。`scripts/sanitize_data.py` 只带「怎么替换」的规则，具体「替换谁」放在工作区根的 `.sanitize_map.json`（已在 `.gitignore` 中）。否则脱敏就变成「先把真号抄进脚本，再用它去替换真号」，泄漏只是换个文件继续存在。映射文件缺失时手机号 / 邮箱 / 内网 IP 仍可靠正则兜底并生成稳定占位值，姓名类会明确提示未启用而不是假装扫过。
2. **演示库也要脱敏**。`data/*.db` 本身被忽略，但它是截图与演示的唯一数据来源——库里留着真号，下一次截图就会把号码印在图上再提交进仓库，等于绕开 `.gitignore`。

脱敏后必须回归验证解析行为不变：`tests/test_smoke.py`、`scripts/evaluate.py`（开发集 + 留出集）、`scripts/real_audit.py` 三项指标要与脱敏前逐字一致。实测四类占位值不改变任何字段抽取结果（`real_audit.py` 报告除生成时间戳外无差异）。

---

## 六、解析效果（可量化指标）

### 6.1 开发集（24 份人工标注）

覆盖 8 个类别、跨年日期、范围日期、表格化排版、无明确截止等边界情形。

| 指标 | 规则引擎（离线基线） |
| --- | --- |
| 分类准确率 | 100.0% |
| 截止日期准确率（含空值判定） | **100.0%** |
| 截止日期召回率（仅非空样本） | **100.0%** |
| 适用对象 F1 | 91.2% |
| 资格条件 F1 | 94.3% |
| 报名材料 F1 | 94.5% |
| 平均置信度 | 0.82 |
| 需人工复核比例 | 16.7% |

### 6.2 留出集（8 份，未参与调参）

为避免"自己写规则、自己验证"造成的高估，另建一组规则开发时未见过的通知，
涵盖学术会议征稿、学生组织换届、实验室开放日、企业实习、教师教学竞赛、无门槛讲座 6 种新体裁。

| 指标 | 首次测量（规则未改） | 修正结构缺陷后 | 接入真实站点后再补规则 |
| --- | --- | --- | --- |
| 分类准确率 | 50.0% | 100.0% | 100.0% |
| 截止日期准确率 | 87.5% | 100.0% | 100.0% |
| 适用对象 F1 | 81.5% | 89.7% | 89.7% |
| 资格条件 F1 | 53.3% | 72.7% | **84.2%** |
| 报名材料 F1 | 72.7% | 72.7% | **92.3%** |

留出集的两轮数字都如实保留。**首轮 50% 的分类准确率暴露的是真实缺陷**：文体强信号会无视"讲座"直接改写类别、
日期上下文按字符数开窗会跨行串味——这两个问题在开发集上因为样本已被规则"喂熟"而看不出来。
修正后条件召回 80%、材料 92.3%，剩余误差集中在极自由的表述上，这是规则引擎在新体裁上的真实边界，
也是系统保留大模型通道的直接原因。

### 6.3 真实站点采集回归（42 份）

从武汉理工大学 7 个公开栏目抓取 42 份真实通知（快照 `data/real_notices.json`）：

| 指标 | 数值 |
| --- | --- |
| 栏目抓取成功率 | 7/7 = 100% |
| 详情页抓取成功率 | 42/42 = 100% |
| 平均正文长度 | 1377 字（最短 218，最长 4419） |
| 带附件的通知 | 15 份 / 36 个附件 |
| 文体构成 | 事务通知 21 / 公示公告 3 / 新闻资讯 18 |
| 事务与公示类办理截止覆盖 | 20/24 = 83.3%（4 份逐条核对，见审计报告） |
| 新闻资讯报名类字段误报 | 0/18 = 100% 不误报 |

**为什么不再用「截止日期抽取率 55%」这个说法**：42 份里有 18 份是新闻稿，本身不存在报名截止，
把"正确地留空"记成"没抽到"既低估了系统，也掩盖了更严重的问题——当时系统反过来在新闻稿上
**硬造**字段（把赛事会期当报名截止、把学科名「材料」当报名材料）。因此改成两把尺子：
事务类看办理截止覆盖，新闻类看不误报率。完整审计报告由 `python scripts/real_audit.py` 一键生成。

这批真实数据累计暴露出 4 类构造样本测不出来的缺陷：正文数字被拆成碎片、条目编号被当章节标题、
材料词表泛义化、**新闻稿不该套报名类字段**。第 4 类同时把开发集截止日期准确率从 91.7% 推到 100%
（根因是「X至Y」范围末端无条件加分，把公示期、会期也推成截止日）。
修复详情见 `docs/test_report.md` 第 6.5—6.8 节与 `docs/key_code.md`。

> 说明：配置大模型后同一脚本可输出大模型成绩：`python scripts/evaluate.py --llm`（留出集加 `--set holdout`）。
> 数据集规模较小，指标存在波动，不作为对外承诺值。

### 6.4 设计符合度与结构化字段契约（v1.3）

按《总控文档》《技术文档 1》《技术文档 2》逐条核对后，把「文档写了、代码没落地」的 6 项补齐，
逐条对照表见 `docs/design_compliance.md`（条款 → 实现文件 → 验证命令 → 状态）。

| 设计条款 | 实现 | 验证 |
| --- | --- | --- |
| 总控 8.2 十四类结构化字段 | `app/services/fields.py` 确定性抽取年级/学历、专业/学院、报名开始、活动举办、地点、联系人/电话/邮箱，每字段附原文证据；不交给生成式模型 | `shot-fields.png`、自检 [13] |
| 技文1 第 26 章 / 技文2 第 29、46 章 规则校验 | `app/services/validator.py` C1—C11（日期格式、时间倒挂、报名起止、证据接地、原文可定位、活动冲突、置信度软提示、文体字段泄漏）；`analyzer._finalize()` 是规则与大模型两条链路的统一出口，结果写入 `checks` | 自检 [13]、`shot-review.png` |
| 技文2 第 21 章 相对时间 | `app/services/reltime.py`：「本周五前」「三个工作日内」「本月底前」结合发布时间换算；无发布时间或非办理语境则置空并标记待复核 | 自检 [13] |
| 技文2 第 32 章 硬条件匹配 | `app/services/matcher.py` 用结构化枚举做专业/年级/学历冲突判定，明确冲突直接判「暂不符合」，不靠短字符串包含猜 | 自检 [6][13] |
| 技文1 第 47 章 人工审核 | 详情抽屉「通过 / 修正字段 / 驳回重解析」三态；驳回回退待复核，重新通过清理旧驳回说明 | `shot-review.png`、自检 [13] |
| 总控 25.4 / 技文2 第 61 章 效率实测 | `scripts/benchmark.py` 现场计时生成 `docs/benchmark.md` | 见 6.5 |

设计原则一句话概括：**AI 负责理解，程序负责约束，原文负责证明**。
四层保护（结构校验 / 文体闸门 / 原文接地 / 规则兜底）+ 规则校验（C1—C11 统一校验出口）；
低置信度只作软提示，硬拦截只留给时间倒挂、证据失配、格式非法这类真异常，避免把全量通知标红。

### 6.5 处理效率实测

`python scripts/benchmark.py` 现场计时（74 份语料、单条重复 3 轮共 222 次采样），报告生成到 `docs/benchmark.md`：

| 项目 | 实测值 |
| --- | --- |
| 单条解析中位数 / 平均 / P95 | 1.86 ms / 2.63 ms / 7.21 ms |
| 100 条连续解析 | 0.202 s，吞吐 495.5 条/秒 |
| API（TestClient 进程内） | `/api/health` 6.72 ms、`/api/notices?size=20` 25.05 ms、`/api/dashboard/stats` 51.35 ms |
| 数据库 `SELECT COUNT(*)` | 0.26 ms |

计时含文本归一化、文体判别、分类、日期打分、字段抽取、证据接地与规则校验，不含数据库写入与网络 I/O。
> **数值会波动**：`benchmark.py` 每次运行重新计时，开发机负载不同会让单条中位数在 1—2 ms、
> 接口响应在 2—30 ms 间浮动。**`docs/benchmark.md` 是唯一数据源**：复跑 `benchmark.py` 后执行
> `python scripts/sync_metrics.py`，即可把本页、测试报告与视频脚本的指标一次性回灌（幂等，可反复运行）；
> 答辩 PPT 与作品信息收集表由生成脚本直接读取该报告填入数字。全链路不手抄，不会与报告脱节。

**人工阅读整理耗时未组织真人实验，因此报告不填人工耗时、不写「效率提升百分比」**；
云端 RDS、公网 API、并发访问与大模型链路耗时均列入未覆盖清单，待资源到位后重跑。

### 6.6 界面设计与体验（Arctic Glass）

本轮完成 **Arctic Glass Design System** 全量重构，前端三份文件整体重写
（行数以 `docs/metrics.json` 现场采集为准，`python scripts/scale_sync.py --check` 可核对）。

- **Design DNA**：冷感极简 + Apple HIG 留白 + Material 3 组件规范 + Linear / Notion 信息组织。浅灰白底 `#F6F8FC`、白卡片、深灰文字；蓝色 `#2563EB` 只用于品牌与核心操作，紫色 `#7C3AED` 只用于 AI 能力，绿 / 橙 / 红分别表达正常 / 临近 / 紧急截止。明确反对「满屏蓝色科技大屏」与传统高校 OA 风格，AI 是能力而不是装饰，不做粒子、霓虹与大面积渐变，全站无 Emoji、统一线性图标。
- **9 视图学生端闭环**：`home` 智能工作台（**新的默认视图**）、`dashboard` 数据总览、`notices` 通知中心、`sources` 采集中心、`match` 我的匹配、`qa` AI 助手、`tasks` 我的任务；248px 侧栏分组导航 + 64px 毛玻璃顶栏 + 1280px 内容区，8pt 间距、12–20px 圆角、150–300ms 动效，三档响应式（≥1200px 完整侧栏 / 768–1199px 72px Icon Rail / <768px 隐藏侧栏并改底部 5 项导航）。
- **首页智能工作台**：AI 洞察 Hero + 4 张 Stats Card + 3 张推荐机会（含匹配度与逐条理由）+ 右侧「即将截止」与「可信链路 5 节点」。匹配分是 5–98 的可解释加权分，界面标注「规则加权匹配度（非录取概率）」，不冒充录取概率。
- **三级可信链路**：详情抽屉把 `checks` 报告渲染为 `.check-panel`，14 类字段与原文依据绑定为一组 `.field-block`，点击「原文依据」在通知原文中黄色高亮并滚动定位，形成「AI 结论 → 结构化信息 → 原文证据」的界面闭环。
- **24 张界面截图**：由 `python scripts/capture.py` 一键重生成，覆盖首页长图、14 字段完整长图、原文依据高亮、审核三态与移动端首屏等。

完整设计变量表、页面清单、组件规范与截图检查要点见 **`docs/ui_design.md`**。

### 6.7 通知推荐算法与问答精准度（v1.4）

**匹配从"一串加法魔数"升级为七因子加权模型**（`app/services/matcher.py`）：

| 因子 | 权重 | 说明 |
| --- | --- | --- |
| 适用对象 | 0.28 | 学历 / 年级 / 身份是否覆盖当前用户，权重最高，因为它决定"能不能报" |
| 兴趣方向 | 0.20 | 命中 1.0、相邻主题 0.6、不相关 0.15、**未填兴趣 0.5 中性** |
| 专业限制 | 0.14 | 词级双向包含判定，避免「信息管理」被「电子信息」假命中 |
| 时效紧迫 | 0.12 | 7 档递减：当天/3 天内 1.0 → 60 天外 0.22，已截止 0.0 |
| 信息可执行度 | 0.10 | 6 个关键字段的填充率 × 0.7 + 置信度 × 0.3 |
| 硬性条件 | 0.08 | 成绩 / 排名 / 四六级 / 无违纪等硬门槛 |
| 内容新鲜 | 0.08 | 按发布时间衰减，**缺失时取 0.5 中性**（缺失不等于陈旧） |

七个权重之和为 1，加权和即拟合度 `fit`，再线性映射到 5—98 分（界面标注"规则加权匹配度（非录取概率）"）。
硬冲突（学历/年级/专业明确不符）不是给个低分，而是**否决 + 压分**：直接判 `暂不符合`，同时把分数压到阈值以下，
避免出现"84 分却暂不符合"的自相矛盾卡片。每条结论都带 `factors[]`（raw / weight / contribution / state / note），
界面上以标签形式展示贡献最大的几个因子，点开可见原因。

**推荐位是独立的三段式编排**（`app/services/recommender.py`，接口 `GET /api/users/{id}/recommendations`）：

```
召回：剔除未解析 / 新闻稿 / 已截止 / 硬冲突 / 同标题重复副本  → 每类跳过都计数
排序：按 fit 降序                                            → 相关性优先
重排：MMR 多样性打散（同类上限 2）                            → 折扣只用来打破近似同分平局
```

返回 `items[]`（含 `why`「为什么推给你」、`factors`、`deadline`）与 `meta`（候选总数、跳过原因分桶、权重、冷启动说明）。
`GET /api/recommendation-model` 输出算法说明面板所需的七因子与三段式流程，界面「算法说明」按钮据此渲染。

**问答检索的四处针对性改进**（`app/services/qa.py`）：

1. **IDF 加权**：`0.5 + ln(1 + (N+1)/(df+1))` 压制「通知」「报名」这类高频虚词，避免它们主导排序；
2. **长度归一**：正文命中分乘 `1/√len`，长通知不再仅因为字多就排前面；
3. **标题去重**：同一通知的多来源副本按归一化标题合并，只占一个引用位；
4. **能报/已截止分口径**：清单类问题先剔除新闻稿，未截止的排前面；一条都没剩时如实说「都已过报名截止」，
   不套用「尚未截止」的模板。引用卡片带 `deadline`，前端直接标出该条是否已截止。

---

## 七、华为云接入清单

| 要求 | 当前状态 | 说明 |
| --- | --- | --- |
| 使用华为云码道 CodeArts 代码智能体 | 提示词体系、逐日排期、独立提示词、留痕模板与填写示范、单页作战台、环境体检 / 一键骨架 / 9 阶段门禁已就绪，提交目录骨架已生成，待团队实际使用留痕 | `docs/codearts_prompts.md`（P0—P10 + Day 1—Day 7）＋ `docs/codearts_prompts_advanced.md`（B0 / A0 / A1—A10）＋ `.codeartsdoer/AGENTS.md` ＋ `codearts/` 资产 ＋ `scripts/codearts_tools.py`、`scripts/ci_gate.py`；投喂前跑 `codearts_tools.py doctor`（确认解释器）与 `index`（索引缺口以现场输出为准），并重建索引，会话 / 生成 / 修复 / 构建 / 部署截图按方案 §5 归档到 `11_CodeArts使用记录/` |
| 关联 RDS 快速开通创建 | 已实现 | 改 `DATABASE_URL` 即切换，`init.sql` 可直接执行 |
| 关联 Redis 快速开通创建 | 已实现 | 改 `REDIS_URL` 即启用，未配置自动降级 |
| 调用 MaaS 大模型 | 已实现 | OpenAI 兼容适配，未配密钥自动降级 |
| 云端部署与在线演示 | 已部署（当前实例被冻结） | ECS + EIP + VPC + 安全组已由 Terraform 真实开通，公网健康检查与首页、REST 接口、浏览器端到端均验证通过；记录见 `docs/华为云ECS部署报告.md`（已脱敏）。当前实例状态为 SHUTOFF 且开机动作返回 `is freezed`，属账号侧限制，需在控制台处理欠费/冻结后恢复 |
| 开源至 Gitee/GitHub | 已完成 GitHub 公开 | `https://github.com/HongX-code/campusinsight`，提交前已跑 `scripts/sanitize_data.py` 与 `sanitize_demo_db.py` 脱敏，报告中的真实 IP/实例 ID/VPC ID 均替换为占位符 |

---

## 八、已知限制

1. 规则引擎在**未见过的通知体裁**上条件召回约 80%（留出集实测），条件表述自由度高、标注本身存在歧义，开放表述需大模型补足；
2. 采集仅支持静态 HTML 页面，不处理 JS 渲染、登录态与需 VPN 的校内门户，遇到反爬会明确报错并引导手动录入；
3. 附件只记录链接，不解析 PDF/Word 内容；图片型通知记为 `content_type=image`，需后续接入 OCR；
4. 问答采用关键词检索而非向量检索，语义泛化能力有限，优点是零依赖、可解释、可离线；
5. 当前为单用户演示模式，尚无账号体系与多租户隔离；
6. 华为云 RDS / Redis / MaaS 的**真实连通性尚未验证**（代码路径就绪，需资源到位后回归），云端跨设备访问同样待验证；
7. 处理效率仅在本地开发机、规则引擎、进程内 TestClient 下实测（`docs/benchmark.md`），**人工阅读耗时、云端 RDS、公网 API、并发与大模型链路耗时均未测**，因此不写「效率提升百分比」。

## 九、开源协议

MIT License，见 `LICENSE`。
