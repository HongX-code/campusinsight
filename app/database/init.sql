-- =====================================================================
-- 高校智讯 · 数据库初始化脚本（华为云 RDS for MySQL 8.0）
-- 说明：本地开发环境由 SQLAlchemy 自动建表；云端执行本脚本可获得
--       与 ORM 完全一致的表结构，便于运维与评审复现。
-- =====================================================================

CREATE DATABASE IF NOT EXISTS `gaoxiao_zhixun`
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;

USE `gaoxiao_zhixun`;

-- 1. 用户画像
CREATE TABLE IF NOT EXISTS `users` (
  `id`          INT AUTO_INCREMENT PRIMARY KEY,
  `name`        VARCHAR(64)  NOT NULL COMMENT '姓名',
  `grade`       VARCHAR(32)  DEFAULT '' COMMENT '年级：大一/大二/大三/大四/研一',
  `major`       VARCHAR(128) DEFAULT '' COMMENT '专业',
  `interests`   TEXT         COMMENT '兴趣方向，JSON 数组',
  `student_no`  VARCHAR(64)  DEFAULT '' COMMENT '学号（演示数据脱敏）',
  `created_at`  DATETIME     DEFAULT CURRENT_TIMESTAMP,
  KEY `idx_users_grade` (`grade`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='学生用户画像';

-- 2. 原始通知
CREATE TABLE IF NOT EXISTS `notices` (
  `id`            INT AUTO_INCREMENT PRIMARY KEY,
  `title`         VARCHAR(255) NOT NULL COMMENT '通知标题',
  `content`       LONGTEXT     NOT NULL COMMENT '通知正文（清洗后）',
  `category`      VARCHAR(32)  DEFAULT '其他' COMMENT '分类',
  `source_url`    VARCHAR(512) DEFAULT '' COMMENT '来源链接',
  `source_name`   VARCHAR(128) DEFAULT '' COMMENT '来源单位',
  `publish_time`  VARCHAR(32)  DEFAULT '' COMMENT '发布时间 YYYY-MM-DD',
  `content_hash`  VARCHAR(64)  NOT NULL COMMENT '正文指纹，用于去重',
  `ingest_mode`   VARCHAR(16)  DEFAULT 'manual' COMMENT 'manual/url/import/crawl',
  `attachments`   TEXT         COMMENT '附件清单，JSON 数组',
  `created_at`    DATETIME     DEFAULT CURRENT_TIMESTAMP,
  `updated_at`    DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY `uk_notices_hash` (`content_hash`),
  KEY `idx_notices_category` (`category`),
  KEY `idx_notices_publish` (`publish_time`),
  KEY `idx_notices_ingest_mode` (`ingest_mode`),
  KEY `idx_notices_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='原始通知';

-- 3. AI 解析结果（一个通知对应一条，人工修正覆盖同一条记录）
CREATE TABLE IF NOT EXISTS `notice_analysis` (
  `id`            INT AUTO_INCREMENT PRIMARY KEY,
  `notice_id`     INT NOT NULL,
  `category`      VARCHAR(32)  DEFAULT '其他',
  `genre`         VARCHAR(16)  DEFAULT 'notice' COMMENT '文体：notice / publicity / news',
  `actionable`    TINYINT(1)   DEFAULT 1 COMMENT '是否有可办理事项（新闻资讯为 0）',
  `deadline`      VARCHAR(32)  NULL COMMENT '办理截止日期 YYYY-MM-DD',
  `deadline_kind` VARCHAR(16)  DEFAULT '' COMMENT '报名/申报/提交/报送/征集/异议截止等',
  `start_time`    VARCHAR(32)  NULL COMMENT '活动开始时间',
  `grade_requirement` TEXT COMMENT '年级/学历限制，JSON 数组',
  `major_requirement` TEXT COMMENT '专业/学院限制，JSON 数组',
  `registration_start` VARCHAR(32) NULL COMMENT '报名开始日期',
  `event_time`    VARCHAR(32)  NULL COMMENT '活动/讲座举办时间',
  `location`      VARCHAR(200) DEFAULT '' COMMENT '报名或活动地点',
  `contact`       TEXT COMMENT '联系人/电话/邮箱，JSON 对象',
  `deadline_source` VARCHAR(16) DEFAULT '' COMMENT 'absolute / relative',
  `checks`        TEXT COMMENT '规则校验报告，JSON 对象',
  `target`        TEXT COMMENT '适用对象，JSON 数组',
  `conditions`    TEXT COMMENT '资格条件，JSON 数组',
  `materials`     TEXT COMMENT '报名材料，JSON 数组',
  `apply_method`  TEXT COMMENT '报名方式',
  `evidence`      TEXT COMMENT '字段依据，JSON 对象',
  `confidence`    FLOAT DEFAULT 0 COMMENT '综合置信度 0~1',
  `needs_review`  TINYINT(1) DEFAULT 1 COMMENT '是否需人工复核',
  `engine`        VARCHAR(32) DEFAULT 'rule' COMMENT 'llm / rule',
  `model_name`    VARCHAR(64) DEFAULT '',
  `review_note`   TEXT,
  `reviewed`      TINYINT(1) DEFAULT 0,
  `updated_at`    DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY `uq_analysis_notice` (`notice_id`),
  KEY `idx_analysis_category` (`category`),
  KEY `idx_analysis_deadline` (`deadline`),
  KEY `idx_analysis_genre` (`genre`),
  KEY `idx_analysis_needs_review` (`needs_review`),
  KEY `idx_analysis_reviewed` (`reviewed`),
  KEY `idx_analysis_engine` (`engine`),
  CONSTRAINT `fk_analysis_notice` FOREIGN KEY (`notice_id`) REFERENCES `notices`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='AI 解析结果';

-- 4. 个人待办
CREATE TABLE IF NOT EXISTS `user_tasks` (
  `id`          INT AUTO_INCREMENT PRIMARY KEY,
  `user_id`     INT NOT NULL,
  `notice_id`   INT NOT NULL,
  `status`      VARCHAR(16) DEFAULT 'todo' COMMENT 'todo/doing/done',
  `remind_time` VARCHAR(32) DEFAULT '',
  `note`        TEXT,
  `created_at`  DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY `uq_user_notice_task` (`user_id`, `notice_id`),
  KEY `idx_task_user` (`user_id`),
  CONSTRAINT `fk_task_user`   FOREIGN KEY (`user_id`)   REFERENCES `users`(`id`)   ON DELETE CASCADE,
  CONSTRAINT `fk_task_notice` FOREIGN KEY (`notice_id`) REFERENCES `notices`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='个人待办';
