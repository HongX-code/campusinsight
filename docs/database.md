# 数据库设计

## 1. 设计原则

1. **保留原文**：`notices.content` 存清洗后的全文，任何解析结果都能回溯核对；
2. **解析结果结构化**：`notice_analysis` 用独立表 + JSON 字段，不把自然语言塞进一个 text 列；
3. **一个通知一条解析**：`notice_id` 唯一约束，人工修正覆盖同一条记录，避免版本混乱；
4. **去重前置**：`content_hash` 唯一索引，从数据库层杜绝重复入库；
5. **跨库兼容**：字段类型只用 MySQL / SQLite 都支持的类型，日期统一以 `VARCHAR(32)` 存 ISO 字符串——因为通知里的时间是"文本"，强行转 DATE 会在清洗阶段丢失"暂未确定"这类状态。

## 2. 实体关系

```
users 1 ──── n user_tasks n ──── 1 notices 1 ──── 1 notice_analysis
  │                                   │                    │
  │                                   ├──── n notice_checklists
  │                                   └──── n notice_conflicts（notice_a / notice_b 各一条）
  ├──── n wechat_bindings（微信登录票据）
  └──── n push_subscriptions（一次性订阅授权计数）
```

四张核心表（`notices` / `notice_analysis` / `users` / `user_tasks`）承载解析与匹配主链路；
后三张是新能力表：`notice_conflicts` 存跨来源冲突，`notice_checklists` 存办理清单，
`wechat_bindings` + `push_subscriptions` 支撑微信小程序登录与截止提醒。

## 3. 表结构

### 3.1 notices（原始通知）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | INT PK | 自增主键 |
| title | VARCHAR(255) | 通知标题 |
| content | LONGTEXT | 清洗后正文 |
| category | VARCHAR(32) | 分类（与 notice_analysis.category 同步） |
| source_url | VARCHAR(512) | 来源链接，手动录入可为空 |
| source_name | VARCHAR(128) | 来源单位或域名 |
| publish_time | VARCHAR(32) | 发布时间 YYYY-MM-DD，允许空 |
| content_hash | VARCHAR(64) UK | SHA256(标题+正文归一化)，去重键 |
| ingest_mode | VARCHAR(16) | manual / url / import |
| attachments | TEXT | JSON 数组：正文内 / 附件区抽取到的附件清单（名称 + URL） |
| created_at / updated_at | DATETIME | 创建与更新时间 |

索引：`uk_notices_hash(content_hash)`、`idx_notices_category`、`idx_notices_publish`。

### 3.2 notice_analysis（解析结果）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| notice_id | INT UK FK | 一对一关联 notices，级联删除 |
| category | VARCHAR(32) | 9 类之一 |
| genre | VARCHAR(16) | 文体：`notice` / `publicity` / `news` |
| actionable | TINYINT(1) | 是否有可办理事项；新闻资讯为 0，不进匹配与待办 |
| deadline | VARCHAR(32) NULL | 办理截止日期 |
| deadline_kind | VARCHAR(16) | 截止语义：报名 / 申报 / 提交 / 报送 / 征集 / 异议 / 办理截止 |
| start_time | VARCHAR(32) NULL | 报名开始时间（与 `registration_start` 同义的兼容字段，按总控 8.2 语义落库） |
| grade_requirement | TEXT | JSON 数组：年级 / 学历限制，默认 `[]`（`fields.py` 确定性抽取） |
| major_requirement | TEXT | JSON 数组：专业 / 学院限制，默认 `[]` |
| registration_start | VARCHAR(32) NULL | 报名开始日期 YYYY-MM-DD，默认 NULL |
| event_time | VARCHAR(32) NULL | 活动 / 讲座举办时间 YYYY-MM-DD，默认 NULL |
| location | VARCHAR(200) | 报名或活动地点，默认 `''` |
| contact | TEXT | JSON 对象：联系人 / 电话 / 邮箱，默认 `{}` |
| deadline_source | VARCHAR(16) | 截止日期来源 `absolute` / `relative` / 空，默认 `''` |
| checks | TEXT | JSON 对象：规则校验报告，默认 `{}` |
| target | TEXT | JSON 数组，归一化身份词 |
| conditions | TEXT | JSON 数组，资格条件 |
| materials | TEXT | JSON 数组，报名材料 |
| apply_method | TEXT | 报名方式 |
| evidence | TEXT | JSON 对象：字段 → 原文片段 |
| timeline | TEXT | JSON 数组：多轨道截止节点，默认 `[]` |
| confidence | FLOAT | 0—1 综合置信度 |
| needs_review | TINYINT(1) | 是否需人工复核 |
| engine | VARCHAR(32) | llm / rule（引擎来源对用户可见） |
| model_name | VARCHAR(64) | 模型标识，便于区分批次 |
| reviewed | TINYINT(1) | 是否已完成人工复核 |
| review_note | TEXT | 系统提示或人工备注 |

索引：`uq_analysis_notice(notice_id)` 唯一约束，以及本轮新增的三个查询索引
`idx_analysis_category(category)`、`idx_analysis_deadline(deadline)`、`idx_analysis_genre(genre)`，
分别服务于分类筛选、临近截止排序与文体构成统计（`app/database/init.sql` 与 ORM 定义一一对应）。

解析结果示例：

```json
{
  "category": "学科竞赛",
  "genre": "notice",
  "actionable": true,
  "deadline": "2026-11-20",
  "deadline_kind": "报名截止",
  "deadline_source": "absolute",
  "checks": { "passed": ["C1 日期格式与真实性"], "issues": [], "warnings": [], "ok": true },
  "target": ["本科生"],
  "conditions": ["以3人团队形式报名", "每人限报一队", "参赛队员须无违纪记录"],
  "materials": ["报名表", "个人简历"],
  "evidence": {
    "deadline": "二、报名时间 2026年11月3日至2026年11月20日",
    "target": "一、参赛对象 全日制本科生，以3人团队形式报名"
  },
  "confidence": 0.88,
  "needs_review": false,
  "engine": "rule"
}
```

`checks` 由 `services/validator.py` 的 `audit(result, text, publish)` 生成，`analyzer._finalize()`
作为规则与大模型两条链路的统一出口写入本列，结构固定为：

```json
{
  "passed": ["C1 日期格式与真实性", "C2 截止 ≥ 发布", "C5 证据原文接地", "C9 文体闸门无字段泄漏"],
  "issues": ["截止日期 2026-01-01 早于发布时间 2026-11-06"],
  "warnings": ["解析置信度 0.62 偏低，建议人工核对关键字段"],
  "ok": false
}
```

| 键 | 类型 | 含义 |
| --- | --- | --- |
| `passed` | `list[str]` | 通过的校验项（C1—C11，逐项含义见 `docs/architecture.md` 第 5 节） |
| `issues` | `list[str]` | 硬异常：置 `needs_review=true`，并把说明追加到 `review_note` |
| `warnings` | `list[str]` | 软提示：只写复核说明，不改审核状态 |
| `ok` | `bool` | `issues` 为空即为 `true` |

#### `timeline` 列：为什么单值 `deadline` 之后还要存一份节点数组

`deadline` 保留作兼容字段与排序兜底，但它表达不了真实通知的时间结构。以《关于2026年
秋冬季博士、硕士学位授予及相关工作安排的通知》为例，原文 6 行「X月X日（秋季）和
Y月Y日（冬季）」并行推进，共 14 个节点；单列只能存其中一个，且实测存下来的
`2026-10-08` 是博士预答辩，**对硕士生来说是错的日期**（硕士要赶的是 10-10 提交答辩
意向、10-12 答辩前检测），秋季整条轨道的 7 个 7 月节点则全部丢失。

这不是抽取不准，是**列类型装不下**：事实是「轨道 × 身份 × 事项」的三维网格。所以
新增 `timeline` 存 JSON 数组，每个节点：

```json
{ "seq": 3, "date": "2026-10-10", "track": "冬季", "role": "博士研究生",
  "scope": "student",
  "action": "博士学位论文与实践成果总结报告\"答辩前检测\"",
  "evidence": "10月10日（冬季），博士学位论文…\"答辩前检测\"截止" }
```

- `track`：`秋季 / 冬季 / 春季 / 夏季`，原文无批次标记时为空串。
- `role`：`博士研究生 / 硕士研究生 / 本科生 / 专科生 / 学院`。
- `scope`：`student` 或 `admin`。**学院审议、报送名单这类节点是 `admin`，永不进入
  学生个人时间轴** —— 把管理节点推给学生只会制造不属于他的紧迫感。
- `evidence`：原句片段，`validator.py` 的 C10 会逐节点回原文核对日期与事项，
  对不上就剔除并重排 `seq`、回写 `stats.total`。

选择 JSON 列而非独立 `notice_timeline` 表，理由是节点随解析结果整体重算、没有独立
生命周期，也不被其他表外键引用；一旦将来要按节点跨通知聚合（如「全校本周截止」），
再抽表不迟。列的新增已登记在 `app/database/connection.py` 的 `_ensure_columns()`，

#### 表格为什么不存在数据库里

「表格结构复原」这个能力**没有新增任何列**，这是刻意的。列边界的信息在
`notices.content` 里就以 `\x1f` 分隔符的形式存着——采集期序列化出来的正文本身就是
带结构的，表格是**从正文里读出来的视图**，不是另一份事实。

如果单独存一张 `notice_tables(headers, rows, ...)`，就会出现正文与表格两份数据，
而它们必然有一天不同步：重新采集一次正文变了，表格表却还留着旧的。解析结果
（`notice_analysis`）里的 `table_stats` / `table_detail` 也不落库，只随响应返回，
理由同上——它们是对正文的**派生物**，重算成本为零（纯字符串切分），没有保存价值。

判断标准很简单：**这份数据是不是原文里没有、需要独立维护的？** 时间轴节点不是
（每条都要回原文核对，落在正文之外），所以存 JSON 列；表格不是（正文里就有
分隔符），所以不存。
旧库启动时自动补列，不需要手工迁移。

### 3.3 users（用户画像）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | INT PK | |
| name | VARCHAR(64) | 姓名 |
| grade | VARCHAR(32) | 大一 / 大二 / 大三 / 大四 / 研一… |
| major | VARCHAR(128) | 专业 |
| interests | TEXT | JSON 数组，兴趣方向 |
| student_no | VARCHAR(64) | 学号（演示数据脱敏） |

### 3.4 user_tasks（个人待办）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| user_id + notice_id | 联合唯一 | 同一通知不重复加入待办 |
| status | VARCHAR(16) | todo / doing / done |
| remind_time | VARCHAR(32) | 提醒时间（预留） |
| note | TEXT | 备注 |

## 4. 多端与治理相关的四张表

这四张表不在解析主链路上，缺了它们系统照样跑，但它们分别承载「谁在用」「怎么提醒」
「同一件事两处说法不一致」「这份通知到底要我做什么」四个问题。

### 4.1 wechat_bindings（微信登录绑定）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| openid | VARCHAR(64) UK | 微信 openid；未配置 AppID 时为 `mock_` 前缀的稳定派生值 |
| unionid | VARCHAR(64) | 同一开放平台下的跨应用用户标识，可为空 |
| user_id | INT FK | 关联 `users`；**本系统 users 是唯一身份源，微信只是登录方式** |
| nickname / avatar | VARCHAR | 微信侧昵称与头像，仅展示用 |
| token | VARCHAR(64) UK | 服务端自签票据 |
| token_expire_at | DATETIME | 票据过期时间（默认 30 天） |
| last_login_at | DATETIME | 最近一次登录 |
| created_at | DATETIME | 首次绑定时间 |

设计取舍：**不自签 `session_key`**。微信的 `session_key` 只有 2 小时有效，
拿它当会话票据会让用户在校内弱网里频繁莫名掉登录，且失败原因无法向用户解释。
改为登录时换取一次身份、随后用本地票据（可配 `WECHAT_TOKEN_TTL_DAYS`），
Web 与小程序因此共用同一份画像与待办。

### 4.2 push_subscriptions（订阅消息授权计数）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| user_id + template_id + notice_id | 联合唯一 | 同一用户对同一模板同一条通知只记一行 |
| openid | VARCHAR(64) | 冗余存储，推送时免去再查绑定表 |
| remain_count | INT | 剩余可下发次数（微信一次性订阅**一次授权一次下发**） |
| status | VARCHAR(16) | granted / revoked / exhausted / failed |
| last_error | VARCHAR(255) | 最近一次失败原因（如 `43101` 用户未授权） |
| last_sent_at | DATETIME | 最近一次成功下发 |

用计数而不是布尔：微信订阅消息是「用户点一次授权，服务端才能发一条」，
把它当长期订阅必然在下发时被 `43101` 打回。计数让界面能如实显示「还剩 N 次提醒」。

### 4.3 notice_conflicts（跨来源冲突）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| notice_a_id / notice_b_id | INT FK | 被判为同一事项的两条通知 |
| field | VARCHAR(32) | 冲突字段：`deadline` / `location` / `contact.phone` / `contact.email` / `apply_method` / `grade_requirement` / `major_requirement` |
| value_a / value_b | TEXT | 两侧取值（数组已压成中文顿号串） |
| severity | VARCHAR(16) | blocker / warn / info |
| reason | TEXT | 面向人的说明，如「同一事项出现两个截止日期」 |
| resolved | TINYINT(1) | 是否已人工裁决 |
| resolution | VARCHAR(16) | use_a / use_b / both_wrong / ignored |
| created_at | DATETIME | 检出的时间 |

**这张表存在的理由**：单条通知各自抽取可以条条都「正确」，只有横向比较才暴露
「同一件事两个截止日期」。按较早的判会误报已过期，按较晚的判会错过材料提交，
而单来源抽取器在结构上不可能发现这个矛盾。

### 4.4 notice_checklists（办理清单）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| notice_id | INT FK | 归属通知 |
| seq | INT | 步骤顺序 |
| title | VARCHAR(200) | 动词短语，如「提交签字盖章的报名表」 |
| detail | TEXT | 展开后的补充说明 |
| offline | TINYINT(1) | 是否需线下办理（盖章 / 签字 / 纸质递交） |
| depends_on | INT | 前置步骤 seq，0 表示无依赖 |
| evidence | TEXT | 该步骤在原文中的依据句 |
| source | VARCHAR(16) | `text`（正文抽取）/ `material`（材料名词化）/ `deadline`（时间锚点） |

三个不显然的设计：
- `offline` 必须单列。学生真正的卡点常常不是「不知道要交什么」，而是
  「这个要盖章，得跑一趟」——线上流程和线下跑腿的提醒策略完全不同；
- `depends_on` 让「先填表再盖章」有顺序，前端可以做「前一步没勾，后一步灰着」；
- `source` 区分来源，因为 `deadline` 类型的那一步（「在 X 前完成提交」）
  **是系统补的时间锚点、原文里并没有这句话**，界面必须能标出来，不能冒充原文。

## 5. 为什么不用外键约束外的强 schema

解析结果天然不稳定（字段可能为空、可能人工修正、可能换模型重跑）。把 `target/conditions/materials/evidence` 放 JSON 而不是拆成多张子表，换来的是：

- 一次查询拿到完整语义单元，前端无需 join；
- 调整字段结构不需要迁移表；
- 换模型重解析时整条覆盖，不存在孤儿行。

代价是无法用 SQL 对"条件"做精确查询——当前业务没有这个需求，若将来需要按条件检索，可加一张 `notice_condition` 明细表做物化。

## 6. 建表方式

- 本地：`python scripts/init_db.py`（SQLAlchemy `metadata.create_all`，幂等）
- 云端：执行 `app/database/init.sql`（含库、表、索引、外键、中文注释，与 ORM 定义一一对应）
- 旧库轻量迁移：`create_all` 只建新表，不会给已存在的表补列。`app/database/connection.py` 的
  `_ensure_columns()` 在 `init_db()` 里紧随 `create_all` 执行：用 `inspect` 比对现有列，
  对缺失列逐条 `ALTER TABLE ... ADD COLUMN`（本轮 8 个结构化字段 / 校验字段都在补列清单里），
  只做"缺什么补什么"的加列，不改名、不删列，避免误伤历史数据。本地已有 SQLite 库与云端 RDS
  因此都能平滑升级到 v1.3 表结构。
