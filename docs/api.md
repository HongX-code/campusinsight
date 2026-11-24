# 接口文档

服务启动后可在 `/docs` 打开 FastAPI 自动生成的交互式接口页（Swagger UI），本文档说明各接口的用途、
关键参数与返回结构，便于前端对接和复核。

基础地址：`http://<host>:8000`

## 1. 通用约定

- 请求与响应均为 `application/json`，编码 UTF-8。
- 列表接口统一返回 `{ items: [...], total: n, page: 1, size: 20, pages: n }`（`size` 默认 20，可用查询参数覆盖）。
- 错误统一返回 `{ detail: "说明" }`，配合 HTTP 状态码：
  - `400` 参数非法（如采集地址不在白名单）
  - `404` 资源不存在
  - `409` 重复数据（同一通知重复录入）
  - `502` 上游模型调用失败（系统会自动降级为规则引擎，不中断业务）

## 2. 系统与 AI 状态

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查，返回数据库连通性、存储类型与多端资源路径 |
| GET | `/api/ai/status` | 当前解析引擎（规则 / 大模型）、模型名、冷却剩余秒数、能力清单 |
| POST | `/api/ai/health` | 主动探测大模型连通性（极短请求）；无密钥时返回规则引擎可用 |
| GET | `/api/system/info` | 版本、云服务接入状态、`network` 多端接入信息，用于演示页底部状态栏与「多端接入」页签 |

`/api/health` 返回示例：

```json
{
  "status": "ok",
  "database": { "ok": true, "kind": "SQLite（本地）" },
  "llm_available": false,
  "version": "1.0.0",
  "platforms": {
    "web": true,
    "pwa": true,
    "manifest": "/manifest.webmanifest",
    "service_worker": "/sw.js",
    "clients": ["Windows", "Android", "HarmonyOS", "iOS", "macOS", "Linux"]
  }
}
```

`/api/ai/status` 额外字段：`llm.ready`（可立即调用）、`llm.cooldown_remaining`（连续失败后的冷却秒数）、
`llm.last_error_kind`（`auth` / `rate_limit` / `timeout` / `network` / `server` / `empty` / `protocol`）、
`llm.last_probe_ok` / `last_probe_ms` / `last_probe_error`（最近一次连通性探测结果）、
`capabilities`（解析、问答、匹配、校验四项能力及是否支持离线）。

`/api/system/info` 的 `network` 字段（「多端接入」页签数据源）：

```json
{
  "listen_host": "127.0.0.1",
  "port": 8000,
  "local_url": "http://127.0.0.1:8000/",
  "lan_addresses": ["192.168.43.41"],
  "lan_urls": ["http://192.168.43.41:8000/"],
  "lan_exposed": false,
  "pwa": {
    "manifest": "/manifest.webmanifest",
    "service_worker": "/sw.js",
    "requires_secure_context": true,
    "note": "离线壳与「添加到桌面」仅在 HTTPS 或 localhost 下可用"
  },
  "clients": ["Windows", "Android", "HarmonyOS", "iOS", "macOS", "Linux"]
}
```

`lan_exposed` 为 `false` 时表示服务只监听回环地址，手机暂时连不上；
用 `python run.py --lan` 启动后即为 `true`。地址探测只读取本机网卡信息，不发起外部请求。

`/api/ai/health` 返回示例（未配置密钥时仍为 200）：

```json
{
  "ok": false,
  "engine": "rule",
  "active_engine": "rule",
  "latency_ms": 0,
  "model": "DeepSeek-V3",
  "reason": "未配置大模型密钥，内置规则引擎可用（离线兜底）",
  "error_kind": "not_configured"
}
```

连通性探测是**只读诊断**：无论成功或失败，都不改变业务调用的连续失败计数与冷却状态，
避免「测试连接」把真实故障提前解除、或让本来正常的解析白白降级。

## 2.1 多端资源（PWA）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/manifest.webmanifest` | PWA 清单，`display=standalone`，含三个快捷方式 |
| GET | `/sw.js` | Service Worker（根作用域，响应头带 `Service-Worker-Allowed: /`） |
| GET | `/static/icons/icon-192.png` | 192 图标 |
| GET | `/static/icons/icon-512.png` | 512 图标 |
| GET | `/static/icons/icon-maskable-512.png` | 自适应图标（Android / HarmonyOS 圆形遮罩） |
| GET | `/favicon.ico` | 站点图标，复用 192 图标，避免浏览器控制台出现 404 |

`/sw.js` 的缓存策略：页面导航网络优先并回退缓存首页；静态资源缓存优先 + 后台更新；
`/api/` 一律网络直连，离线时返回 `503` 并提示"AI 与服务端功能暂不可用"。

## 3. 通知

前缀：`/api/notices`

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/` | 列表，支持 `keyword`（关键词）、`category`、`genre`、`review`、`page`、`size` |
| GET | `/{id}` | 详情，含原文、解析结果与原文依据 |
| POST | `/` | 手动录入（标题 + 正文 + 来源 + 发布时间） |
| PUT | `/{id}` | 修改标题/正文/分类等可编辑字段 |
| DELETE | `/{id}` | 删除 |
| POST | `/ingest/url` | 粘贴公开网页链接采集（白名单 + 内网地址拦截） |
| POST | `/{id}/analyze` | 对单条通知重新解析 |
| POST | `/batch-analyze` | 批量解析未解析或待复核的通知 |
| PUT | `/{id}/analysis` | **人工修正**解析结果，修正后标记 `manual` 来源 |

录入请求体：

```json
{
  "title": "关于举办2026年数学建模竞赛的通知",
  "content": "各学院：……",
  "source": "教务处",
  "source_url": "https://jwc.example.edu.cn/notice/123",
  "publish_time": "2026-11-02"
}
```

解析结果结构（`analysis` 字段）：

```json
{
  "category": "学科竞赛",
  "genre": "notice",
  "genre_label": "事务通知",
  "actionable": true,
  "deadline": "2026-11-20",
  "deadline_kind": "报名截止",
  "deadline_source": "absolute",
  "start_time": "2026-11-03",
  "grade_requirement": ["本科生"],
  "major_requirement": [],
  "registration_start": "2026-11-03",
  "event_time": "2026-12-06",
  "location": "南湖校区教学楼A301",
  "contact": { "person": "张老师", "phone": ["027-87654321"], "email": ["abc@whut.edu.cn"] },
  "checks": { "passed": [], "issues": [], "warnings": [], "ok": true },
  "target": ["本科生"],
  "conditions": ["3人团队", "每人限报一队", "无违纪记录"],
  "materials": ["报名表", "个人简历"],
  "apply_method": "请登录教务处学科竞赛管理系统提交报名信息",
  "evidence": {
    "deadline": "报名时间：2026年11月3日至2026年11月20日",
    "target": "一、参赛对象 全日制本科生",
    "conditions": "参赛队员须无违纪记录",
    "materials": "报名表、个人简历各一份"
  },
  "confidence": 0.88,
  "needs_review": false,
  "engine": "rule"
}
```

> `evidence` 的每个值都是**原文片段**，前端据此实现"点字段看原文"。日期若在原文中找不到对应表述，
> 会被置空并置 `needs_review=true`，不允许模型自行补全。

**本轮新增的结构化字段**（总控文档 8.2 的 14 类字段中补齐的部分）由 `services/fields.py`
从原文确定性抽取，逐字段附 `evidence`，不交给大模型生成：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `grade_requirement` | `list[str]` | 年级 / 学历限制，归一化身份词枚举 |
| `major_requirement` | `list[str]` | 专业 / 学院限制，只在约束语境里采信 |
| `registration_start` | `string \| null` | 报名开始日期（ISO） |
| `event_time` | `string \| null` | 活动 / 讲座举办时间（ISO） |
| `location` | `string` | 报名或活动地点 |
| `contact` | `object` | `{person, phone: [], email: []}` 联络方式 |
| `deadline_source` | `string` | `absolute` / `relative` / 空；`relative` 表示截止日期由相对时间结合发布时间换算 |
| `checks` | `object` | 规则校验报告，结构见下 |

精简示例（只列新增字段）：

```json
{
  "grade_requirement": ["本科生", "大三"],
  "major_requirement": ["计算机", "电子信息"],
  "registration_start": "2026-11-03",
  "event_time": "2026-12-06",
  "location": "南湖校区教学楼A301",
  "contact": { "person": "张老师", "phone": ["027-87654321"], "email": [] },
  "deadline_source": "absolute",
  "checks": {
    "passed": ["C1 日期格式与真实性", "C2 截止 ≥ 发布", "C5 证据原文接地"],
    "issues": [],
    "warnings": ["解析置信度 0.62 偏低，建议人工核对关键字段"],
    "ok": true
  }
}
```

`checks` 的结构固定为 `{passed: [], issues: [], warnings: [], ok: bool}`：
`passed` 是通过的校验项（C1—C9），`issues` 是硬异常（置 `needs_review=true`），
`warnings` 是软提示（只写复核说明、不改状态），`ok = (issues 为空)`。
它由 `validator.audit()` 生成、`analyzer._finalize()` 写入，规则与大模型两条链路共用同一出口。

**文体闸门**：`genre` 取 `notice` / `publicity` / `news`。判定为新闻资讯时，
`actionable=false`，`deadline / target / conditions / materials / apply_method / evidence` 一律留空，
`grade_requirement / major_requirement / registration_start / event_time / location / contact` 等
结构化字段由 `empty_fields()` 一并置空，`needs_review=false`，`review_note` 写明判定依据。此时：

- `GET /api/notices?genre=news` 可筛出全部新闻稿，可与 `review` 叠加；
- `GET /api/users/{id}/matches` 不会返回新闻稿；
- `POST /api/users/{id}/tasks` 对新闻稿返回 `400`（"新闻资讯类内容无办理事项"）。

`deadline_kind` 标注同一日期字段的语义：报名 / 申报 / 提交 / 报送 / 征集 / 异议 / 办理截止。
例如公示公告的"公示期结束"标为**异议截止**，界面不再笼统显示成"报名截止"。

### 3.1 `PUT /api/notices/{id}/analysis`：人工审核三态

该接口既用于**人工修正字段**，也用于**审核动作**（`AnalysisUpdate` 请求模型）：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `action` | `string` | `approve`（通过）/ `reject`（驳回重解析）/ 空字符串（仅修正字段） |
| `reject_reason` | `string` | 驳回原因；驳回时写入复核说明，界面要求必填 |
| `review_note` | `string` | 复核备注 |

可修正的结构化字段：`category`、`deadline`、`start_time`、`target`、`conditions`、`materials`、
`apply_method`、`grade_requirement`、`major_requirement`、`registration_start`、`event_time`、
`location`、`contact`、`review_note`。字段按"覆盖"处理，列表与对象字段序列化为 JSON 存储。
`action` 的取值为 `approve`、`reject` 或空字符串（不传），分别对应通过、驳回与仅修正字段：

三态行为：

| `action` | 请求示例 | 结果 |
| --- | --- | --- |
| `approve` | `{"action": "approve"}` | `reviewed=true`、`needs_review=false`，复核说明追加「｜人工通过」 |
| 空（修正） | `{"major_requirement": ["信息管理"], "location": "南湖校区"}` | 覆盖指定字段，`reviewed=true`、`needs_review=false`，追加「｜已人工复核」 |
| `reject` | `{"action": "reject", "reject_reason": "截止日期与原文不符"}` | `reviewed=false`、`needs_review=true`，说明写「人工驳回：截止日期与原文不符（需重新解析）」 |

驳回不把错误结果洗成"已审核"；重新解析并再次通过时，接口会清理旧的「人工驳回：…」说明，
避免复核说明里同时出现"需重新解析"与"已通过"。界面上对应详情抽屉「规则校验与人工审核」区的
「通过 / 修正字段 / 驳回重解析」三个按钮与驳回原因输入框，截图见 `docs/shot-review.png`；
闭环用例见 `tests/test_smoke.py` 第 [13] 组。

## 4. 站点采集

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/sources` | 列出全部采集源（含选择器配置与停用项） |
| POST | `/api/sources/preview` | 按栏目抓取但**不落库**，返回结果并标注 `duplicate` |
| POST | `/api/sources/import` | 把预览中勾选的条目入库；`items` 为空则按 `source_ids` 现场抓取后直接入库 |

抓取预览请求：

```json
{ "source_ids": ["whut_gd_yjsgl", "whut_news_xsdt"], "limit": 6, "max_pages": 1, "fetch_detail": true }
```

响应（节选）：

```json
{
  "total": 12,
  "summary": { "sources": 2, "ok": 2, "failed": [], "elapsed": 9.8 },
  "sources": [{
    "source": { "id": "whut_gd_yjsgl", "name": "武汉理工大学-研究生管理通知公告", "url": "…" },
    "stats": { "pages": 1, "candidates": 15, "detail_ok": 6, "detail_fail": 0,
               "items": 6, "avg_chars": 1418.7, "elapsed": 5.2 },
    "items": [{
      "title": "关于举办第五届武汉理工大学MPAcc案例大赛的通知",
      "url": "http://gd.whut.edu.cn/yjsgl/tzgg/202501/t20250121_604425.shtml",
      "publish_date": "2025-01-21",
      "content": "各研究生培养单位：……",
      "content_type": "text",
      "attachments": [{ "name": "附件：1-4", "url": "…/P020250121590042728507.rar" }],
      "chars": 1314,
      "duplicate": false,
      "preview": "各研究生培养单位： 为鼓励研究生亲历企业实践……"
    }],
    "errors": []
  }]
}
```

入库请求：

```json
{
  "items": [{ "title": "...", "content": "...", "url": "...", "publish_date": "2025-01-21",
              "unit": "武汉理工大学-研究生管理通知公告", "attachments": [] }],
  "auto_analyze": true
}
```

入库响应：`{ "created": 5, "skipped": 1, "analyzed": 5, "failed": [], "total": 6 }`

> `skipped` 是去重命中数：内容指纹（标题+正文 SHA-256）或 `source_url` 任一命中即跳过。
> `failed` 里的每条都带原因（正文过短、正文补抓失败、解析异常），不会静默丢弃。

## 5. 解析预览（不落库）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/ai/preview` | 传入标题与正文，直接返回解析结果，用于录入前预览 |

请求体：`title`、`content`（≥10 字）、`publish_time`、`source_url`（可选，作文体判别弱先验）、
`use_llm`（`null` 按配置自动选择，`false` 强制规则引擎，便于对照演示）。
响应在通用解析结果之外补充 `degraded` / `fallback_reason` / `fallback_kind`：
模型返回非法 JSON、超时或鉴权失败时会降级到规则引擎，并把原因结构化返回，
界面不再只显示「大模型已就绪」。前端「AI 解析实验室」用这组字段展示字段证据与
C1—C9 校验结果，全程不写入数据库。

## 6. 用户与个性化匹配

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/users` | 用户列表 |
| POST | `/api/users` | 新建用户（姓名、年级、专业、兴趣） |
| GET | `/api/users/{id}` | 用户详情 |
| PUT | `/api/users/{id}` | 修改资料 |
| DELETE | `/api/users/{id}` | 删除 |
| GET | `/api/users/{id}/matches` | 个性化匹配，参数 `only_open=true` 只返回未截止 |
| GET | `/api/users/{id}/agenda` | 时间管理，参数 `days` 默认 30 |

匹配返回结构：

```json
{
  "user": { "id": 1, "name": "张同学", "grade": "大三", "major": "信息管理与信息系统" },
  "counts": { "match": 6, "maybe": 5, "no": 13 },
  "items": [
    {
      "notice": { "id": 1, "title": "…", "category": "学科竞赛" },
      "level": "match",
      "level_text": "符合",
      "score": 82,
      "reasons": [
        { "type": "good", "text": "你是本科生，满足「本科生」的适用对象要求" },
        { "type": "good", "text": "与你关注的「学科竞赛」方向一致" },
        { "type": "warn", "text": "距离报名截止还有 15 天" }
      ]
    }
  ]
}
```

三级结论：`match`（符合）、`maybe`（可能符合，存在未明确的资格项）、`no`（暂不符合，有硬性冲突）。
`reasons` 每条都指向具体判断依据，前端不可省略。

## 7. 待办

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/users/{id}/tasks` | 待办列表（含关联通知标题与截止时间） |
| POST | `/api/users/{id}/tasks` | 加入待办 |
| PATCH | `/api/tasks/{task_id}` | 更新状态：`todo` / `doing` / `done` |
| DELETE | `/api/tasks/{task_id}` | 移除待办 |

## 8. 智能问答

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/qa/ask` | 基于已收录通知回答问题 |

请求：

```json
{ "query": "数学建模竞赛需要提交哪些材料？", "top_k": 3 }
```

响应：

```json
{
  "answer": "问的是提交材料，已收录通知中的答案如下：\n[1]《关于举办2026年…数学建模竞赛…的通知》：需提交：报名表、个人简历、作品说明；截止 2026-11-20。\n以上信息来自通知原文的结构化抽取，编号对应下方来源，可点开核对依据。",
  "citations": [
    {
      "index": 1,
      "notice_id": 1,
      "title": "关于举办2026年武汉理工大学数学建模竞赛…",
      "snippet": "报名表、个人简历各一份，团队作品说明于截止日期前一并上传系统",
      "source_url": "https://jwc.whut.edu.cn/tzgg/20261028-mcm.html",
      "score": 42.5
    }
  ],
  "mode": "structured",
  "engine": "rule",
  "hit": true,
  "citation_ok": true,
  "fallback_reason": "",
  "latency_ms": 4,
  "query_terms": ["数学", "建模", "竞赛", "提交", "材料"],
  "intent": "materials",
  "intent_label": "查提交材料",
  "answer_fields": [
    {"index": 1, "notice_id": 1,
     "fields": {"materials": ["报名表", "个人简历", "作品说明"], "deadline": "2026-11-20"}}
  ],
  "corpus_size": 66,
  "structured_corpus": 44
}
```

`hit=false` 表示语料中没有相关内容，此时 `answer` 明确告知"未收录"，不生成推测性回答。
`mode` 取值：`llm`（大模型生成，带引用）、`structured`（结构化字段组装，默认）、
`extractive`（无结构化字段时的原文摘取兜底）、`clarify`（纯指代、需点名通知）、`no-hit`（拒答）。
`engine` 取值：`llm` / `rule`，用于前端显示当前回答引擎。

问答语料不只是 `title / content`：路由会把已复核的结构化字段（`deadline` / `materials` /
`conditions` / `target` / `apply_method` / `contact` / `evidence` 等）一并交给 `qa.answer()`，
因此问「材料」「条件」「截止」时直接回答确切字段值，而不是回一串标题。`structured_corpus`
是语料中带结构化字段的通知数，为 0 说明知识库尚未解析，答案只能退回原文摘取。
`intent` 由规则识别（材料 / 截止 / 条件 / 联系方式 / 地点 / 报名方式 / 列名单 / 综合），
`answer_fields` 记录每条结论实际用到的字段值，便于逐条核对。

- `citation_ok=false` 且 `fallback_reason` 为空：模型回答未标注编号，答案保留但提示以来源列表为准；
- 模型引用了不存在的编号（如只有 3 条却写 `[5]`）：视为幻觉信号，自动回退抽取式回答，
  并在 `fallback_reason` 说明原因；
- 模型超时 / 鉴权失败 / 冷却中：同样回退抽取式，`fallback_reason` 带可读原因；
- `snippet` 优先取该意图对应的字段原文依据，便于逐字核对；
- 检索使用「查询原词覆盖率 ≥ 0.5」做门槛，同义词只以 0.5 权重加分；查询会先去掉
  「最近 / 有哪些 / 这个 / 适合我」等口语与指代词，避免无意义二元词把覆盖率拖垮；
- 点名了具体通知时按标题最长匹配片段精确聚焦，不把同名近亲通知混进答案；
- 问「近期截止」直接查 `deadline` 字段，排除已过期并按由近到远排，不靠全文检索碰运气；
- 首页 6 个示例问题逐条纳入端到端自检（含预期字段值与误召回黑名单），示例改动会驱动断言变红。

## 9. 看板统计

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/dashboard/stats` | 概览数字、分类分布、采集渠道构成、文体构成、近 7 天趋势、即将截止列表、解析质量分布 |

返回额外字段：`modes`（按 `ingest_mode` 的渠道构成）、`genres`（按 `genre` 的文体构成），
均为 `[{name, value}]`，前端各渲染成一张条形图。

返回字段：

```json
{
  "overview": { "total": 24, "analyzed": 24, "pending_review": 3, "open": 18 },
  "categories": [{ "name": "学科竞赛", "value": 6 }],
  "trend": [{ "date": "11-04", "count": 3 }],
  "deadlines": [{ "id": 1, "title": "…", "deadline": "2026-11-20", "days_left": 15 }],
  "quality": { "high": 15, "medium": 6, "low": 3 }
}
```
