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
```

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
| `passed` | `list[str]` | 通过的校验项（C1—C9，逐项含义见 `docs/architecture.md` 第 5 节） |
| `issues` | `list[str]` | 硬异常：置 `needs_review=true`，并把说明追加到 `review_note` |
| `warnings` | `list[str]` | 软提示：只写复核说明，不改审核状态 |
| `ok` | `bool` | `issues` 为空即为 `true` |

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

## 4. 为什么不用外键约束外的强 schema

解析结果天然不稳定（字段可能为空、可能人工修正、可能换模型重跑）。把 `target/conditions/materials/evidence` 放 JSON 而不是拆成多张子表，换来的是：

- 一次查询拿到完整语义单元，前端无需 join；
- 调整字段结构不需要迁移表；
- 换模型重解析时整条覆盖，不存在孤儿行。

代价是无法用 SQL 对"条件"做精确查询——当前业务没有这个需求，若将来需要按条件检索，可加一张 `notice_condition` 明细表做物化。

## 5. 建表方式

- 本地：`python scripts/init_db.py`（SQLAlchemy `metadata.create_all`，幂等）
- 云端：执行 `app/database/init.sql`（含库、表、索引、外键、中文注释，与 ORM 定义一一对应）
- 旧库轻量迁移：`create_all` 只建新表，不会给已存在的表补列。`app/database/connection.py` 的
  `_ensure_columns()` 在 `init_db()` 里紧随 `create_all` 执行：用 `inspect` 比对现有列，
  对缺失列逐条 `ALTER TABLE ... ADD COLUMN`（本轮 8 个结构化字段 / 校验字段都在补列清单里），
  只做"缺什么补什么"的加列，不改名、不删列，避免误伤历史数据。本地已有 SQLite 库与云端 RDS
  因此都能平滑升级到 v1.3 表结构。
