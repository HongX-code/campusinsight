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
| GET | `/api/health` | 健康检查，数据库 / 缓存 / 引擎连通性与多端资源，**华为云三件套 configured/active 单点可见** |
| GET | `/api/ai/status` | 当前解析引擎（规则 / 大模型）、模型名、冷却剩余秒数、能力清单 |
| POST | `/api/ai/health` | 主动探测大模型连通性（极短请求）；无密钥时返回规则引擎可用 |
| GET | `/api/system/info` | 版本、云服务接入状态、`network` 多端接入信息，用于演示页底部状态栏与「多端接入」页签 |

`/api/health` 返回示例：

```json
{
  "status": "ok",
  "database": { "ok": true, "kind": "SQLite（本地）" },
  "cache": { "kind": "内存缓存（单进程）", "keys": 0 },
  "active_engine": "rule",
  "llm_available": false,
  "version": "1.0.0",
  "platforms": {
    "web": true,
    "pwa": true,
    "miniprogram": true,
    "manifest": "/manifest.webmanifest",
    "service_worker": "/sw.js",
    "calendar": "/api/calendar.ics",
    "clients": ["Windows", "Android", "HarmonyOS", "iOS", "macOS", "Linux", "微信小程序"]
  },
  "wechat": { "enabled": false, "push_ready": false },
  "huawei": {
    "rds":  { "configured": false, "active": false, "kind": "SQLite（本地）" },
    "dcs":  { "configured": false, "active": false, "kind": "内存缓存（单进程）" },
    "maas": { "configured": false, "active": false, "provider": "none", "model": "DeepSeek-V3" },
    "fallback": {
      "database": "SQLite（本地）",
      "cache": "内存缓存（单进程）",
      "engine": "内置规则引擎（离线兜底）"
    }
  }
}
```

> **读法**：`configured` 只看环境变量是否填了连接串 / 密钥，`active` 才看实际是否连上，
> 两者分开杜绝「配了就算连通」。上例是**离线自检环境**的真实返回：三件套 `active` 均为 false，
> `huawei.fallback` 如实标出降级目标。ECS/EIP/VPC 已真实部署（见 `.auto-deploy/华为云ECS部署报告.real.md`），
> 但当时运行的是 SQLite + 规则引擎，RDS/DCS/MaaS 尚未开通，故这里不写成 true。

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
  "lan_addresses": ["192.168.1.20"],
  "lan_urls": ["http://192.168.1.20:8000/"],
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
`passed` 是通过的校验项（C1—C11），`issues` 是硬异常（置 `needs_review=true`），
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
C1—C11 校验结果，全程不写入数据库。

## 6. 用户与个性化匹配

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/users` | 用户列表 |
| POST | `/api/users` | 新建用户（姓名、年级、专业、兴趣） |
| GET | `/api/users/{id}` | 用户详情 |
| PUT | `/api/users/{id}` | 修改资料 |
| DELETE | `/api/users/{id}` | 删除 |
| GET | `/api/users/{id}/matches` | 个性化匹配，参数 `only_open=true` 只返回未截止 |
| GET | `/api/users/{id}/recommendations` | 通知推荐位，参数 `limit`（默认 6，1—20）、`include_expired`（默认 false） |
| GET | `/api/recommendation-model` | 推荐算法说明：七因子权重、五路融合权重、五段式流程、阈值、分类上限、探索加成、冷启动说明 |
| GET | `/api/users/{id}/agenda` | 时间管理，参数 `days` 默认 30 |

匹配返回结构（`factors` 为七因子的分项贡献，`score` 由加权和映射而来）：

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
      "fit": 0.8213,
      "category": "学科竞赛",
      "factors": [
        { "key": "target", "label": "适用对象", "raw": 1.0, "weight": 0.28,
          "contribution": 0.28, "state": "good", "note": "你是本科生，满足「本科生」" },
        { "key": "interest", "label": "兴趣方向", "raw": 1.0, "weight": 0.20,
          "contribution": 0.2, "state": "good", "note": "与你关注的「学科竞赛」一致" }
      ],
      "reasons": [
        { "type": "good", "text": "你是本科生，满足「本科生」的适用对象要求" },
        { "type": "good", "text": "与你关注的「学科竞赛」方向一致" },
        { "type": "warn", "text": "距离报名截止还有 15 天" }
      ]
    }
  ]
}
```

三级结论：`match`（符合）、`maybe`（可能符合，存在未明确的资格项或兴趣不相关）、`no`（暂不符合，有硬性冲突）。
硬冲突时分数会被一并压到阈值以下，不会出现"高分但不符合"的矛盾展示。`reasons` 每条都指向具体判断依据，前端不可省略。

推荐位是**五段式**编排（召回 → 画像 → 五路融合 → 探索 → MMR 重排）。`signals` 把五路信号逐项拆开，
`fusion` 是加权结果、`explore` 是探索加成、`value` 是重排前得分；`why` 是「为什么推给你」的一句话，
`meta.skipped` 给出每条没推的原因计数，`meta.signals` 是画像摘要：

```json
{
  "user": { "id": 1, "name": "张同学" },
  "items": [
    { "notice_id": 42, "score": 94, "fit": 0.9569, "category": "科研项目",
      "fusion": 0.7312, "explore": 0.0, "value": 0.7312,
      "signals": { "content": 0.95, "semantic": 0.46, "cosine": 0.139,
                   "behavior": 0.97, "peer": 0.0, "popular": 0.0 },
      "why": "你关注「科研项目」，且 3 天内截止",
      "notice": { "id": 42, "title": "…", "category": "科研项目" },
      "deadline": "2026-12-01",
      "factors": [ "…" ], "reasons": [ "…" ] }
  ],
  "meta": {
    "candidate_total": 78, "recalled": 13, "returned": 6, "limit": 6,
    "max_per_category": 2, "diversity_lambda": 0.72,
    "skipped": { "no_analysis": 0, "not_actionable": 23, "expired": 35,
                 "vetoed": 6, "duplicate": 2 },
    "cold_start": "…", "weights": { "target": 0.28, "…": "…" },
    "algorithm": "fusion-v2", "explore_bonus": 0.035,
    "fusion_weights": { "content": 0.54, "semantic": 0.22, "behavior": 0.12,
                        "peer": 0.06, "popular": 0.06 },
    "signals": { "engaged": 4, "co_engaged": 0, "popular": 0,
                 "semantic_centroid_terms": ["科研项目", "信息管理与信息系统"],
                 "explored": ["志愿服务"] }
  },
  "signals_meta": { "engaged": 4, "co_engaged": 0, "popular": 0,
                    "peer_users": 0, "half_life_days": 90.0 }
}

五路信号的技术实现与降级方式：

| 信号 | 技术 | 说明 |
| --- | --- | --- |
| `content` | 七因子加权 `fit` | 适用对象与硬条件决定主导权重（0.54） |
| `semantic` | 纯标准库 TF-IDF + 稀疏余弦 | 余弦除以参照值 0.30 截断到 1.0；无 numpy、无 embedding 服务，离线可用 |
| `behavior` | 待办行为 90 天半衰期衰减 | 用户真正点过的比自填兴趣更可信 |
| `peer` | 其他用户待办集合交集计数 | 同侪协同，冷启动补充 |
| `popular` | 加入待办人数对数平滑 | 线性归一会在「只有 1 人加入」时给满分热度 |
```

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

## 10. 多端接入 / 治理 / 日历

这一章对应小程序端与两个新治理能力。Web 端与小程序端调用的是同一套接口，
唯一的差别是身份来源：Web 用 `localStorage` 里的 `campusinsight.login.userId`，
小程序用 `/api/wechat/login` 换来的本地票据。

### 10.1 身份票据

票据可以放在三个位置，优先级从高到低：`x-token` 请求头、`Authorization: Bearer <token>`、
`?token=` 查询参数。查询参数这条通道不是补丁——小程序 `wx.downloadFile` 无法携带自定义头，
下载 ICS 只能走查询参数。票据错误与过期统一返回 `401`，`detail` 为可读中文原因。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/wechat/login` | 微信登录或游客登录，返回票据与用户画像 |
| GET | `/api/wechat/status` | 多端接入状态：配置是否齐备、当前登录模式、能力清单 |
| GET | `/api/wechat/me` | 当前票据对应的用户 |

登录请求：

```json
{ "code": "wx.login 拿到的 code", "device_key": "本机生成的设备标识", "nickname": "张同学" }
```

登录响应：

```json
{
  "token": "……",
  "expire_at": "2026-12-24T21:30:00",
  "openid_mode": "guest",
  "user": { "id": 7, "name": "张同学", "grade": "", "major": "", "interests": [], "student_no": "" },
  "mode": "guest",
  "mode_label": "游客演示模式（未配置 AppID/Secret）"
}
```

- 配了 `WECHAT_APPID` + `WECHAT_SECRET` 时走 `jscode2session`，`openid_mode` 为 `wechat`；
  未配置时用 `device_key` 派生稳定账号，`openid_mode` 为 `guest`，
  **openid 带 `mock_` 前缀**，演示数据一眼可辨，不会与真实账号混淆。
- 同一 `device_key` 重复登录复用同一个 `users.id`，但**每次登录都会换新票据，旧票据当场失效**。
  这条是刻意的：校园场景里学生换手机、清缓存很常见，保证任何时刻只有一台设备持有有效票据。
- 票据是本地自签的 30 天令牌（`WECHAT_TOKEN_TTL_DAYS`），**不是 `session_key`**。
  微信 `session_key` 只有 2 小时有效期，用完必须重新 `wx.login`，
  在校内弱网下表现为「莫名掉登录且无法解释」。

### 10.2 订阅消息

微信一次性订阅的语义是「用户点一次允许，只下发一条」，所以授权按次数累计，不是一个布尔开关。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/wechat/subscribe` | 记录一次订阅授权 |
| GET | `/api/wechat/subscriptions` | 授权列表与剩余次数 |
| POST | `/api/wechat/push/{task_id}` | 对某条待办立即下发一次截止提醒 |

订阅响应：

```json
{ "ok": true, "code": "granted", "message": "已记录 1 次提醒授权", "remain_count": 1, "push_ready": false }
```

失败与降级路径（都不抛异常，`code` 区分原因）：

| code | 含义 | 处理 |
| --- | --- | --- |
| `no_template` | 未配置订阅消息模板 | 降级为站内提醒，界面照常可用 |
| `no_quota` | 用户未授权或次数已用尽（微信 `43101`） | 提示重新授权，记录置为 `revoked` |
| `token_invalid` | `access_token` 失效（`40001`/`42001`） | 清缓存后下次重取 |
| `not_configured` | 未配置 AppID/Secret | 游客演示模式，不下发 |

`push` 无论成功失败都会把 `last_error` / `last_sent_at` 写回 `push_subscriptions`，
「为什么没收到提醒」能在页面上查到，而不是只能翻服务器日志。
响应里带 `payload` 字段，返回实际组装的模板字段（`thing1` 标题 / `time2` 日期 /
`thing3` 分类 / `thing4` 剩余天数），便于和微信后台模板逐字段核对。

微信 `thing` 类型字段有 20 字符上限，超长会报 `47003`，所以标题在服务端统一截断。

### 10.3 首屏聚合

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/mobile/bootstrap` | 小程序首屏一次拿全：身份、统计、近期截止、推荐机会 |

```json
{
  "user": { "id": 7, "name": "张同学", "grade": "大三", "major": "计算机科学与技术", "interests": ["竞赛"] },
  "stats": { "open_notices": 18, "todo_tasks": 3, "matched": 5, "overdue_soon": 1 },
  "agenda": [
    { "notice_id": 1, "title": "…", "category": "学科竞赛",
      "deadline": "2026-11-20", "days_left": 15, "deadline_kind": "报名截止" }
  ],
  "matched": [ { "notice": { "id": 1, "title": "…" }, "level": "match", "score": 82, "reasons": [] } ],
  "push_ready": false,
  "server_time": "2026-11-24T21:30:00"
}
```

设计理由是**弱网**：串行五六次请求很容易把首屏拖过 3 秒，
打包成一个响应后列表上限仍然是 5 条，体积不会因为「聚合」而变大。

### 10.4 办理清单

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/notices/{notice_id}/checklist` | 把材料名词升级为可勾选的顺序步骤 |

```json
{
  "steps": [
    { "seq": 1, "title": "登录智慧理工大提交申请", "detail": "", "offline": false,
      "depends_on": 0, "evidence": "申请人登录…", "source": "text" },
    { "seq": 2, "title": "准备申请表", "detail": "报名材料", "offline": true,
      "depends_on": 0, "evidence": "…", "source": "material" },
    { "seq": 3, "title": "在 2024-12-22 前完成提交", "detail": "截止类型：报名截止",
      "offline": false, "depends_on": 2, "evidence": "…", "source": "deadline" }
  ],
  "stats": { "total": 3, "offline": 1, "with_dependency": 1 },
  "degraded": false,
  "reason": ""
}
```

三个字段值得注意：

- `offline`：必须线下办理（盖章、签字、递交纸质件）。学生真正的卡点常常是「要跑一趟」，
  线上提醒和线下提醒的策略完全不同，混在一起就会漏掉。
- `depends_on`：前置步骤的 `seq`，让「先填表再盖章」有顺序，界面可以把前一步没完成的下一步置灰。
- `source`：取值 `text`（原文里的动作句）/ `material`（从材料清单名词化而来）/
  `deadline`（系统补的时间锚点）。**`deadline` 那一句原文里并没有这句话**，
  是系统为了让清单有终点而补的，界面必须能标出来，不能冒充原文。

`degraded=true` 时 `steps` 为空，`reason` 说明原因（未解析 / 新闻资讯类无可办理事项 /
表述过于笼统）。

**多轨道通知的截止锚点不唯一。** 当通知里存在秋季/冬季这类并行批次时，清单会按轨道
各给一条 `source=deadline` 的锚点，把轨道与适用身份写进标题与 `detail`：

```json
{ "seq": 8, "title": "在 2026-07-07 前完成提交（秋季 · 硕士研究生）：提交答辩意向申请",
  "detail": "截止类型：办理截止 · 轨道：秋季 · 适用身份：硕士研究生",
  "offline": false, "depends_on": 7, "evidence": "7月7日（秋季）…硕士学位申请人须在系统提交答辩意向申请",
  "source": "deadline" }
```

只挂一个日期的后果在 10.5 节里说明：学生会照着别人批次的日期办事。

### 10.5 截止时间轴

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/notices/{notice_id}/timeline` | 单条通知的全部截止节点，参数 `role`（身份）与 `track`（轨道） |
| GET | `/api/timeline` | 当前登录用户的个人时间轴，需票据；参数 `track`、`days`、`back` |

**为什么单值 `deadline` 不够。** 以真实的《关于2026年秋冬季博士、硕士学位授予及相关
工作安排的通知》为例：原文里「7月2日（秋季）和10月8日（冬季）前，博士学位申请人预答辩
截止」这类句子有 6 行，横跨秋季与冬季两套批次，共 14 个真实节点。旧的单值字段只能存
一个日期，结果是：

1. **抽中的日期对多数人是错的**。系统给出 `2026-10-08`（博士预答辩），而硕士生真正要
   赶的是 10-10 提交答辩意向、10-12 答辩前检测。
2. **整条秋季轨道消失**。7 月的 7 个节点全部没有落地，秋季批次的学生看不到任何提醒。

这不是抽取算法不准，是**数据结构装不下**：事实本身是「轨道 × 身份 × 事项」的三维网格，
一个字符串表达不了。所以新增节点数组，单值字段保留作兼容与排序兜底。

节点结构：

```json
{
  "seq": 3, "date": "2026-10-10", "track": "冬季", "role": "博士研究生",
  "scope": "student", "action": "博士学位论文与实践成果总结报告\"答辩前检测\"",
  "evidence": "10月10日（冬季），博士学位论文…\"答辩前检测\"截止"
}
```

- `track`：`秋季 / 冬季 / 春季 / 夏季`，原文没有批次标记时为空串。
- `role`：`博士研究生 / 硕士研究生 / 本科生 / 专科生 / 学院`。
- `scope`：`student` 或 `admin`。**学院审议、报送名单这类节点标记为 `admin`，永不进入
  学生的个人时间轴** —— 把管理节点推给学生只会制造虚假紧迫感。

`GET /api/notices/{id}/timeline` 响应：

```json
{ "notice_id": 12, "title": "…", "deadline": "2026-10-08", "role": "硕士研究生",
  "track": "秋季", "tracks": ["秋季", "冬季"],
  "nodes": [ … ], "stats": { "total": 22, "earliest": "2026-07-02", "latest": "2026-12-02" } }
```

`role` 传空时返回全部节点；传身份时走与个人时间轴同一套过滤逻辑，保证两处口径一致。

`GET /api/timeline` 响应：

```json
{ "role": "博士研究生", "grade": "博二", "track": "", "count": 8, "back": 400,
  "items": [ { "notice_id": 12, "notice_title": "…", "category": "学位",
               "track": "冬季", "role": "博士研究生", "action": "预答辩",
               "date": "2026-10-08", "days_left": -48, "evidence": "…" } ] }
```

两个参数的理由：

- `back`（默认 30，上限 730）：**刚过期的节点也要出现**，`days_left` 为负。学生一旦错过
  就再也看不见，反而不知道自己漏了什么。
- `grade` 到 `role` 的归一在服务端完成（`role_from_user`）。画像里写的是「博二 / 研三 /
  大三」，节点里写的是「博士研究生 / 硕士研究生 / 本科生」，不归一就永远匹配不上，
  学生会以为没有自己的事。

### 10.6 表格结构还原

通知详情与解析结果里会多带两组只读字段，说明「这条通知里的表格被读成了什么」。
它们**不落库**（正文本身已带列边界，见 `docs/database.md`），每次解析即时计算。

`GET /api/notices/{notice_id}` 的 `analysis` 对象新增：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `table_stats` | object | `{total, kinds, rows, cells, colleges, majors, phones}`；`total=0` 表示正文没有可识别的表格 |
| `table_detail` | array | 每张表的 `{columns, kinds, kind, rows, headers}`，`kinds` 是逐列语义标签 |
| `table_note` | string | 表格被降级（超过张数/行数上限）或存量正文列边界已丢时的说明，正常为空 |
| `table_needs_recollect` | bool | `true` 表示正文是旧版采集器压平的，列边界不可复原，需重新采集 |

```json
{
  "table_stats": {"total": 1, "kinds": ["major_plan"], "rows": 5, "cells": 25,
                  "colleges": 5, "majors": 5, "phones": 5},
  "table_detail": [{
    "columns": 5,
    "kinds": ["college", "major_code", "major_name", "plan", "phone"],
    "kind": "major_plan",
    "rows": 5,
    "headers": ["学院名称", "专业代码", "专业名称", "拟招生计划", "咨询电话"]
  }],
  "table_note": ""
}
```

`kinds` 的取值：`college`（学院/培养单位）、`major_code`（专业代码）、
`major_name`（专业名称）、`plan`（拟招生计划）、`phone`、`email`、`person`（联系人/导师）、
`date`、`seq`、`detail`（备注/事项）、`link`；空串表示表头无法判定语义。

**为什么要这个字段**：表格里的学院与电话会被补进 `major_requirement` 与 `contact.phone`，
但界面上必须能说清「这个学院名是从第几列来的」。没有这组统计，用户看到一个凭空多出来
的学院名只能选择相信或不相信；有了它，可以点开原文比对表头。

**为什么只补不覆盖**：正文里显式写「面向材料科学与工程学院」比表格里一列学院名更可信，
所以表格只在正文没给或给得更少时补位。

**为什么列数不一致就不采信**：列数对不上会让整行值串列——把「拟招生计划」的 1 当成
专业代码、把专业名当成学院名。这类错误比字段为空危险得多，故由 C11 直接拦下。

**存量正文为什么不给复原**：本能力上线前采集的正文已经被压平，`\x1f` 一个都没有。
此时 `table_stats.total` 为 0、`table_needs_recollect` 为 `true`、`table_note` 说明原因。
不提供「猜回来」的复原，是因为一行 `085600材料与化工1027-87164677` 既可切成三段
（代码 / 名称 / 电话）也可切成两段（代码 / 名称 1027 / 电话），没有外部信息能判定哪种对；
硬切会造出原文里不存在的事实，违反 C11。正确做法是重新采集这条通知。

### 10.7 跨来源冲突

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/conflicts` | 冲突列表，参数 `severity`（blocker/warn/info）与 `resolved`（true/false） |
| POST | `/api/conflicts/scan` | 全量重扫，返回 `scanned / found / blocker / warn / info` |
| PATCH | `/api/conflicts/{id}` | 人工裁决，`resolution` 取 `use_a` / `use_b` / `both_wrong` / `ignored` |

```json
{
  "items": [
    { "id": 1, "notice_a_id": 25, "notice_b_id": 26,
      "title_a": "关于组织2026年…申报的通知", "title_b": "关于组织2026年…申报的通知",
      "field": "deadline", "field_label": "报名截止日期",
      "value_a": "2026-12-12", "value_b": "2026-12-19",
      "severity": "blocker", "severity_label": "高",
      "reason": "同一事项出现两个截止日期，按较早的会误判已过期，按较晚的会错过材料提交",
      "resolved": false, "resolution": "" }
  ],
  "summary": { "total": 1, "by_severity": { "blocker": 1, "warn": 0, "info": 0 },
               "by_field": { "deadline": 1 }, "unresolved": 1 }
}
```

严重度分级：`deadline` 为 `blocker`（判错会直接错过机会）；
`location` / `contact.phone` / `contact.email` / `grade_requirement` / `major_requirement`
为 `warn`；`apply_method` 为 `info`。

**为什么单条通知自己抽不对**：每一份通知单独看，字段都能从自己的原文里找到依据、条条「正确」。
只有把两条同期、同事项的通知横向比对，才会暴露「同一件事写了两个截止日期」——
这是单来源抽取器在结构上不可能发现的问题。

扫描前会先删掉 `resolved=false` 的旧结论再重算，**已裁决的记录保留**，避免把人工判断冲掉。
`severity` 与 `field_label` 都给中文字段名，前端不再维护一份映射表。

### 10.8 日历订阅

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/calendar.ics` | 全站截止日历，参数 `days` 默认 120（1–730） |
| GET | `/api/users/{user_id}/calendar.ics` | 只含该用户未完成待办的个人日历 |

返回 `text/calendar; charset=utf-8`，可直接粘进 iOS 日历、Outlook、Google Calendar。

实现上有三处不显然但必须做对的地方：

- `DTEND` 取截止日 **+1 天**。iCalendar 的 `DTEND` 不含当天，写成等于 `DTSTART`
  会让事件长度为 0，**整条事件在日历里直接消失**（不报错）。
- 每条事件带两个 `VALARM`：截止前 3 天与当天上午 9 点。这是它相对推送的价值——
  **不需要任何推送权限**，粘一个链接就能收到系统级提醒，评委现场可以当场订阅验证。
- `UID` 用 `notice-{id}@{CALENDAR_UID_DOMAIN}` 而非随机值，重复订阅只会更新条目不会产生重复。
- 折行按 RFC 5545 的 75 字节上限，且必须按 UTF-8 边界切，否则一个汉字被截成半个字节，
  整行在部分客户端里被静默丢弃。
