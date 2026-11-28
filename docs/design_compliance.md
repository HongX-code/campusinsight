# 设计符合度矩阵（总控文档 / 技术文档 1 / 技术文档 2）

> 版本 v1.2　|　核对日期 2026-11-07　|　核对对象：`作品文件/gaoxiao-zhixun/`
>
> 这份表用于回答答辩时最容易被追问的问题：**「你们文档里写的设计，代码里到底有没有？」**
> 每一条都给出可打开的实现文件与可复跑的验证命令，不做口头声明。

## 0. 状态定义与核对口径

| 标记 | 含义 |
| --- | --- |
| ✅ | 已实现，且有自检 / 评测 / 截图可复验 |
| ⚠️ | 部分实现或有明确取舍，原因写在备注 |
| ⏳ | 依赖云资源、密钥、真人实验或团队信息，尚未闭环 |
| 📌 | 设计文档明确列为后续迭代（V2+），不是当前缺口 |

核对口径：以 `python tests/test_smoke.py`、`scripts/evaluate.py`、`scripts/real_audit.py`、
`scripts/benchmark.py`、`scripts/capture.py` 的实际输出为准；界面项以 `docs/shot-*.png` 为准。
当前规模：**51 个路由端点**（含页面 `/` 与健康检查，其中 JSON API 28 个）、约 **22200 行源码**（Python 17400 + 前端 4859，不含文档）、**297 项端到端自检全通过**、**24 张界面截图**、Markdown 文档 **22 份**（`docs/` 下 21 份 + 根目录 README；新增 `docs/codearts_prompts_advanced.md`）。另有 `codearts/` 下的 1 份资产说明、11 份独立提示词、5 份留痕模板、1 份填写示范与 1 个单页实操作战台，属**方案资产**，不计入交付文档数。

---

## 1. 总控文档《项目实施方案与开发文档》

| 条款 | 要求 | 实现位置 | 验证方式 | 状态 |
| --- | --- | --- | --- | --- |
| 五章 · 16 项目标 | 建库、采集、清洗、分类、抽取、匹配、搜索、问答、截止管理、统计、云、CodeArts、部署、测试集、量化评估 | `app/` 全链路 + `docs/` | 自检 + 双集评测 + 真实集审计 | ✅（云端部署除外） |
| 6.1 五大模块 | 通知中心 / AI 解析 / 个性化匹配 / AI 助手 / 数据管理 | `app/templates/index.html` **七个视图（含智能工作台首页）** | `docs/ui_design.md`、`shot-home.png`、`shot-*.png` | ✅ |
| 总控 6.1 / 界面与交互 | Arctic Glass Design System：冷感极简、蓝色=操作 / 紫色=AI、9 视图学生端闭环、三档响应式、三级可信链路界面落点 | `app/static/css/style.css` + `docs/ui_design.md` + `app/static/js/app.js` | 24 张界面截图（`shot-home.png` / `shot-home-full.png` / `shot-fields-full.png` / `shot-evidence.png` / `shot-mobile.png` 等）、`docs/test_report.md` T5 界面检查 | ✅ |
| 7.1 列表字段 | 标题、分类、发布、截止、来源、状态、是否匹配 | `app/static/js/app.js` 通知卡片 | `shot-notices.png` | ✅ |
| **8.2 十四字段** | 类别、对象、报名起止、活动时间、条件、专业/年级限制、材料、方式、地点、联系人/电话、原文依据 | `services/extractor.py` + **`services/fields.py`** + `database/models.py` | `shot-fields.png`、自检 [13] | ✅（本轮补齐后六项） |
| 九章 可信度 | AI 结果 + 原文依据 + 置信状态；无法确定给「待人工确认」 | `analyzer.py` 接地校验 + `validator.py` + 详情抽屉 | 自检 [4][13] | ✅ |
| 十章 用户画像 | 年级、专业、学院、兴趣、经历 | `users` 表 / `UserIn` | `shot-match.png` | ✅ |
| 10.2 匹配逻辑 | 规则匹配 + AI 辅助理解，不完全交给模型 | `services/matcher.py` | 自检 [6] | ✅ |
| 十一章 理由可解释 | 不能只给「推荐参加」，要逐条依据 | `matcher.match_one` 的 `reasons` | `shot-match.png` | ✅ |
| 十二章 问答 | 只针对已收录通知回答，附来源；意图识别 + 结构化字段给出确切值 | `services/qa.py` + `routers/ai.py`（语料含 `analysis` 结构化字段） | `shot-qa.png`、自检 [5] 与 T17「6 个示例逐条精准回答」 | ✅ |
| 十三章 原文溯源 | 每个字段对应原文片段 | `evidence` JSON 列 + 抽屉 `evi()` | 自检 [4] | ✅ |
| 十四章 采集合规 | 只采公开网页，不碰登录/验证码/统一认证 | `services/collector.check_url` 白名单 + SSRF 防护 | 自检 [8] | ✅ |
| 十五章 数据链路 | 网页→解析→清洗→去重→分类→抽取→校验→审核→入库→展示 | `collector.py` → `analyzer.py` → `routers/notices.py` | `docs/architecture.md` 2.1/2.3 | ✅ |
| 十六章 四张核心表 | users / notices / notice_analysis / user_tasks | `database/models.py`、`init.sql` | `docs/database.md` | ✅ |
| 十七/十八 单体架构 | 不上微服务/K8s/消息队列 | 单 FastAPI 进程 + SQLite/MySQL | `app/main.py` | ✅ |
| 十九章 技术栈 | FastAPI + 原生 HTML/CSS/JS + MySQL + 统一 AIService 封装 | `app/`，`services/llm_client.py` | `requirements.txt` | ✅ |
| 二十一/二十二 CodeArts | 需求→生成→构建→调试→修复→部署的双闭环，三层证据可核验，留痕不夸大 | `.codeartsdoer/AGENTS.md` 常驻约束 + `docs/codearts_prompts.md`（P0—P10、**Day 1—Day 7 逐日排期**、短卡片、红线）+ `codearts/prompts`、`codearts/templates` + `scripts/codearts_tools.py`、`scripts/ci_gate.py` | `codearts_tools.py doctor / bootstrap --check / index / verify`、`ci_gate.py --dry-run` | ⏳ 体系、资产、环境体检 / 一键骨架 / 9 阶段门禁已就绪，实操截图与会话导出待团队补齐 |
| 二十三 五个创新点 | 信息理解 / 机会匹配 / 结果可验证 / 截止时间是多节点网格 / 表格保留列边界 | 答辩 PPT 第 6—11 页 | `build_ppt.py` | ✅ |
| **25.4 处理效率** | 人工阅读耗时 vs 系统解析耗时 | **`scripts/benchmark.py` → `docs/benchmark.md`** | 单条中位数、100 条吞吐、API/DB 响应 | ⚠️ 系统侧已实测；人工侧需真人计时实验，未编造 |
| 二十五 50—100 份测试集 | 人工标注类别/截止/对象/条件/材料 | 32 份逐字段标注（24 dev + 8 留出）+ 42 份真实快照 = 74 份通知 | `data/evaluation/`、`real_audit.md` | ⚠️ 语料总量达标，逐字段标注为 32 份，口径已如实区分 |
| 二十六 Test 01—08 | 普通/多日期/专业/年级/无截止/格式混乱/重复/JSON 异常 | `tests/test_smoke.py` + 双集评测 | 自检 + `evaluate.py` | ✅ |
| 二十七 安全 | Key 不进前端、最少必要个人信息、robots 与访问限速 | `.env.example`、`config/sources.json` 0.8s 限速 | 自检 [8] | ✅ |
| 三十三 Demo 流程 | 围绕一个真实用户故事，不逐菜单点击 | `docs/video_script.md` 12 个镜头（Arctic Glass 学生端闭环，5 分 30 秒） | — | ⏳ 视频待录制 |
| 三十四 PPT 10—15 页 | 初赛建议页数 | 当前 22 页（封面 + 20 页正文 + 尾页，总决赛模板） | `build_ppt.py` | ⚠️ 超出建议页数，按总决赛模板扩展，见备注 |
| 三十八 原则四 | 所有量化指标必须真实测试 | `evaluate.py` / `real_audit.py` / `benchmark.py` 均现场计算 | 报告头部标注生成时间 | ✅ |

---

## 2. 技术文档 1《技术实现层梳理》

| 条款 | 要求 | 实现位置 | 验证方式 | 状态 |
| --- | --- | --- | --- | --- |
| 第二章 架构 | 大模型放在 AI 处理层，不放在系统最底层 | `analyzer.py` 编排，程序负责采集/存储/规则 | `architecture.md` | ✅ |
| 第五章 HTTP | 超时、UA、编码探测、状态码处理 | `collector.fetch_page` | 自检 [8] | ✅ |
| **第七章 正文识别** | 不能 `max(elements, key=len)` | `collector._pick_content` 优先级 + 包含关系/体量比 1.3 双判 | 离线 HTML 用例 | ✅ |
| 八/九章 清洗 | 去导航、页脚、浏览量、上/下一篇 | `collector.NOISE_LINE_RE`、`_merge_fragments` | 42 份真实集回归 | ✅ |
| 九章 数据标准化 | 日期统一 YYYY-MM-DD | `textutil.find_dates` / `infer_year` | 跨年用例 | ✅ |
| 十一章 Hash 去重 | 标题 + 正文 SHA-256 | `collector` + `notices.content_hash` 唯一索引 | 自检 [2][9] | ✅ |
| 十七/十八 evidence | AI 结果与原文分开存，可追溯 | `notice_analysis` 独立表 + `evidence` 列 | `docs/database.md` | ✅ |
| 二十二 结构化输出 | 强制 JSON Schema，不留自然语言 | `analyzer.SYSTEM_PROMPT` + `_validate_llm` | 自检 [4] | ✅ |
| 二十五 幻觉 | 原文约束 + 结构化 + 证据 + 置信度 + 人工审核 | 四层保护 + 文体闸门 | `architecture.md` 第 5 节 | ✅ |
| **二十六 规则校验** | `deadline >= publish_time`，异常标待审核 | **`services/validator.py` C1—C11** | 自检 [13]、`shot-review.png` | ✅（本轮补齐） |
| 二十八 匹配三层 | 硬规则 / 软匹配 / AI 解释 | `matcher.py` 结构化硬条件 + 兴趣软匹配 + reasons | 自检 [6][13] | ✅ |
| 二十九 评分权重 | 40/20/20/10/10 仅作设计参数，不宣称为客观最优 | `matcher.py` 采用加分制而非伪精确百分比 | `docs/architecture.md` | ✅ 见备注 |
| 三十 问答 | 先查库拿事实，再让模型组织语言 | `services/qa.py` | `shot-qa.png` | ✅ |
| 三十一/三十九 RAG | 第一版不强制向量库 | — | — | 📌 文档明确列为 V2 |
| 三十四 API | 前后端分离，接口可复用 | 51 个路由端点，OpenAPI 自动生成 | `/docs` | ✅ |
| 四十二 日志 | 爬虫 / API / AI / 异常 / 任务日志 | `logging` 分模块 logger | `app/main.py` 中间件 | ⚠️ 应用日志已覆盖，未落库成 `crawl_logs`/`ai_logs` 表 |
| 四十三 异常 | 单页失败不拖垮整批 | `crawl_all` 单源 try/except、`analyze` 模型兜底 | 自检 [9] | ✅ |
| 四十四/四十五 | JSON 容错 + Pydantic 校验 | `llm_client.chat_json` + `serializers.py` | 自检 [4] | ✅ |
| 四十六 数据质量 | 空标题拒收、异常时间标记、重复去重、AI 无法判断转 pending | `NoticeIn` 校验 + `validator.py` + 唯一索引 | 自检 [2][13] | ✅ |
| **四十七 人工审核** | 管理员 `[通过][修改][驳回]` | 详情抽屉「规则校验与人工审核」区 + `PUT /api/notices/{id}/analysis` 三态 | `shot-review.png`、自检 [13] | ✅（本轮补齐） |
| 四十八/四十九 安全 | `.env` 保护 Key、ORM 参数化查询 | `.gitignore`、SQLAlchemy | 代码审查 | ✅ |
| 五十 权限 | 普通用户 / 管理员 | — | — | ⚠️ 演示原型不设登录，见备注 |

---

## 3. 技术文档 2《技术设计与开发文档》

| 条款 | 要求 | 实现位置 | 验证方式 | 状态 |
| --- | --- | --- | --- | --- |
| 三章 七阶段路线 | 采集→治理→理解→结构化→匹配→检索→行动 | 全链路 | `architecture.md` | ✅ |
| 四章 六层架构 | 用户/应用/三大模块/数据/云设施 | 单页前端 + FastAPI + services + RDS | `docs/architecture.md` | ✅（云设施待实测） |
| 五章 七个模块 | 用户、采集、治理、解析、匹配、助手、待办 | `routers/` + `services/` | 截图与自检 | ✅ |
| 六章 采集合规 | 仅公开信息，不做登录绕过 | `collector.check_url` | 自检 [8] | ✅ |
| 八章 正文提取 | 综合长度/密度/标签/标题位置，不取最长 | `collector.parse_list` 打分 + `_pick_content` | 42 份真实集 | ✅ |
| 十章 去重 | SHA-256(标题+正文) | `content_hash` | 自检 [2] | ✅ |
| 十四—十六 表结构 | users / notices / notice_analysis / user_tasks | `models.py` | `docs/database.md` | ✅ |
| 十七 索引 | `publish_time` / `category` / `content_hash` / `deadline` / `user_id` 建索引，hash 唯一 | `init.sql` 11 个 KEY / UNIQUE + `models.py` 的 `index=True` | 读建表脚本 | ✅ |
| 二十 时间抽取 | 报名起止、活动起止、材料提交时间，统一格式 | `extractor` + `fields.extract_times` | 自检 [13] | ✅ |
| **二十一 相对时间** | 「本周五前」结合发布时间换算，不能确定置空 + pending_review | **`services/reltime.py`** | 自检 [13] | ✅（本轮补齐） |
| 二十四 evidence | 每个重要字段对应原文证据 | `fields.py` 每字段带 evidence + 接地校验 | `shot-fields.png` | ✅ |
| 二十五 置信度 | 只作辅助参考，不冒充统计概率 | `analyzer._rule_result` 按字段完整度折算 | `docs/architecture.md` | ✅ |
| 二十六 审核阈值 | confidence < 0.7 可设 pending_review | `validator.C8` 记为**软提示**而非硬拦截 | `shot-review.png` | ⚠️ 有意取舍，见备注 |
| 二十七 结构化流程 | Prompt→JSON→Pydantic→字段检查→规则校验→证据校验→落库 | `analyzer.analyze` + `_finalize` | 自检 [4][13] | ✅ |
| 三十二 硬条件 | 专业/年级/学历/学院走规则，冲突直接不匹配 | `matcher._check_grade_hard` / `_check_major_hard` | 自检 [13] | ✅（本轮补齐） |
| 三十四 匹配评分 | 权重是设计参数，需说明非客观最优 | `matcher.py` 加分制 | `docs/architecture.md` | ✅ |
| 三十六 问答 | 检索 + 事实 + 生成，不让模型自由回答 | `qa.py` | `shot-qa.png` | ✅ |
| 四十二 API | 用户/通知/匹配/问答/待办接口 | 51 个路由端点 | `docs/api.md` | ✅ |
| 四十九—五十二 华为云 | CodeArts / RDS / DCS(Redis) / AI 模型服务 / 环境变量 | `config.py`、`llm_client.py`、`cache.py`、`connection.py`、`deploy/cloud/terraform/`、`scripts/cloud_bootstrap.py` | `docs/cloud_services.md`、`deploy/README.md` | ⏳ 代码与脚本就绪，真实开通/连通待验证 |
| 五十三 日志 | 爬虫 / API / AI / 异常 / 任务 | 分模块 logger | — | ⚠️ 未落库 |
| 五十六 数据生命周期 | 原始数据与 AI 数据分离 | `notices` / `notice_analysis` 两表 | `docs/database.md` | ✅ |
| 五十七 可信机制 | AI 负责理解，程序负责约束，原文负责证明 | `validator.py` + 文体闸门 + 接地校验 | `architecture.md` 第 5 节 | ✅ |
| 五十八 三类测试 | 功能 / 数据 / AI 效果 | `tests/test_smoke.py`、双集评测、真实集审计 | `test_report.md` | ✅ |
| **六十—六十二 不能编造 / 性能 / 测试数据** | 所有数字来自真实测试；测单条、100 条、API、DB、并发；`data/evaluation/` | `benchmark.py` + `docs/benchmark.md` + `data/evaluation/` | 报告现场生成 | ⚠️ 并发与云端项未测，已列入未覆盖清单 |
| 七十 云端运行 | 公网或比赛要求方式可访问 | `deploy/app/`、`deploy/docker/`、auto-deploy 技能 | `deploy/README.md` | ⏳ 部署脚本就绪，待公网验证 |
| 扩展 多端运行 | 一套后端同时服务 Windows / Android / HarmonyOS 浏览器，可安装为 PWA | `/manifest.webmanifest`、`/sw.js`、`run.py --lan` 局域网地址探测、三档响应式 + 底部导航 + 安全区 + `-webkit-` 前缀、AI 助手「多端接入」诊断页签 | `docs/platform_delivery.md`、`docs/deployment.md` 第 7 节（含真机验证清单） | ✅ 代码与桌面/手机浏览器巡检就绪；真机安装待验证 |

---

## 4. 有意识的取舍（不是漏做，写在这里避免答辩被当成缺陷）

1. **置信度阈值不硬拦截**：规则引擎置信度按字段完整度折算，典型值 0.6—0.8。若把 `< 0.7` 一律转 `pending_review`，几乎全部通知会被标红，人工复核反而失去意义。因此 C8 记为软提示，硬拦截只留给真正的异常（时间倒挂、证据不接地、格式非法）。
2. **匹配采用加分制而非 40/20/20/10/10 的伪精确百分比**：文档本身要求「不能把人为设定的权重包装成科学测得」，所以实现为可解释的分档加分 + 三级结论（符合 / 可能符合 / 暂不符合）。
3. **演示原型不设登录与管理员权限**：技术文档 2 第 55 章的权限设计针对多用户生产场景；当前为单机演示库，所有画像均为公开展示用的虚构学生，不收集真实个人信息。审核接口因此未加鉴权，部署到公网时必须补上。
4. **PPT 20 页而非建议的 10—15 页**：初赛建议页数针对「模板 1 + 初赛」，当前交付使用总决赛模板 2，并把量化验证、采集链路、可信性补强、设计闭环各展开一页。若初赛另有页数限制，可按需合并。
5. **UI 以学生端为演示主视觉、管理看板仅作辅助证明**：方案明确要求「管理后台不要成为比赛主视觉」，因此新增智能工作台首页为默认视图，数据总览 / 采集中心退到侧栏第二组「信息治理」，仅用于证明解析与采集能力；匹配分采用可解释的规则加权分（5–98），界面明确标注「规则加权匹配度（非录取概率）」并与三档结论分开表述，**不写成伪精确百分比概率**。

## 5. 仍未闭环（需要团队 / 云资源，AI 无法代做）

| 项 | 对应条款 | 需要什么 | 产出物 |
| --- | --- | --- | --- |
| 华为云 RDS / DCS(Redis) / MaaS 连通实测 | 总控 21、技文2 49—52 | 账号、Terraform、密钥、白名单 | 连接成功截图 + `cloud.env`（脱敏）；Terraform 与对接代码已就绪 |
| CodeArts 使用留痕 | 总控 22、技文2 52 | 从需求到测试的真实操作记录（提示词见 `codearts_prompts.md`） | `11_CodeArts使用记录/` 下七类截图 + 会话导出 + 采纳说明 + 排错记录 + 开发日志 |
| 人工阅读耗时对照实验 | 总控 25.4 | 3 名计时员 × 10 份通知 | 计时表，再与 `benchmark.md` 比较 |
| 公网部署与并发压测 | 技文2 61、70 | 云端实例 + 压测工具 | 云端性能报告 |
| 演示视频 | 总控 33 | 按 `video_script.md` 录制 | MP4 |
| 队名 / 队员 / 指导教师 / 联系方式 | 申报模板 | 团队信息 | 替换 PPT 与表单中的占位符 |

## 6. 本轮（v1.3）补齐的条款

按本表逐条核对后，本轮从「⚠️/未实现」推进到「✅」的共 6 项：

| 条款 | 之前 | 现在 |
| --- | --- | --- |
| 总控 8.2 十四字段 | 只有 8 个字段，专业/年级混在自由文本 | 新增 `services/fields.py`，14 字段全部落地并带证据 |
| 技文1 第 26 章 / 技文2 第 29、46 章 规则校验 | 无 | `services/validator.py` C1—C11 |
| 技文2 第 21 章 相对时间 | 只认绝对日期 | `services/reltime.py`，含「无法换算则置空待复核」 |
| 技文2 第 32 章 硬条件匹配 | 字符串包含模糊猜 | `matcher` 用归一化枚举做冲突判定 |
| 技文1 第 47 章 人工审核 | 只有 API，无界面 | 详情抽屉三态操作区 + `shot-review.png` |
| 总控 25.4 / 技文2 第 61 章 性能实测 | 无 | `scripts/benchmark.py` + `docs/benchmark.md` |

## 7. 本轮（v1.4）UI 重构

本轮按 Arctic Glass Design System 做前端全量重写，界面口径以 `docs/ui_design.md` 为准。

| 变更 | 实现位置 | 验证方式 | 状态 |
| --- | --- | --- | --- |
| 新增智能工作台首页为默认视图（AI 洞察 Hero / 4 张 Stats Card / 推荐机会 / 即将截止 / 可信链路 5 节点） | `app/templates/index.html` `#view-home`、`app/static/js/app.js` `renderHome()` | `shot-home.png`、`shot-home-ai.png`、`shot-home-opps.png`、`shot-home-full.png` | ✅ |
| Arctic Glass Design System：`:root` 设计变量、冷感极简配色（蓝色=操作 / 紫色=AI）、卡片与按钮五态 | `app/static/css/style.css`、`docs/ui_design.md` | `docs/ui_design.md` 第 3 节、`docs/test_report.md` T5 变量与组件五态检查 | ✅ |
| 9 视图与三档响应式（≥1200px 完整侧栏 / 768–1199px 72px Icon Rail / <768px 隐藏侧栏 + 底部 5 项导航） | `app/static/css/style.css` 第 12 节、`app/static/js/app.js` `switchView()` | `shot-mobile.png`、`shot-mobile-full.png`、T5 三档响应式检查 | ✅ |
| 字段-证据绑定：14 类字段双列网格，字段与原文依据绑定为一组 `.field-block`，证据块不占整行 | `app/static/js/app.js` `fieldBlock()`、`app/static/css/style.css` `.field-block` | `shot-fields.png`、`shot-fields-full.png` | ✅ |
| 原文依据点击 → 通知原文黄色高亮并滚动定位（AI 结论 → 结构化信息 → 原文证据） | `app/static/js/app.js` `jumpToEvidence()`、`.content-box mark` | `shot-evidence.png`、T5 原文依据高亮检查 | ✅ |
| 截止时间状态系统（正常绿 / 临近橙 / 紧急红 / 已截止灰 / 未识别）与规则校验审核三态 `.check-panel` | `app/static/js/app.js` `deadlineInfo()`、`app/static/css/style.css` `.dl-*` / `.check-panel` | `shot-notices.png`、`shot-review.png` | ✅ |
| 24 张界面截图一键重生成 | `scripts/capture.py`、`docs/shot-*.png` | `python scripts/capture.py`、`docs/test_report.md` 第 7 节 | ✅ |
