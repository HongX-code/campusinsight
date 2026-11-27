"""数据模型：核心表 users / notices / notice_analysis / user_tasks
外加多端支撑表 wechat_bindings / push_subscriptions / notice_conflicts / notice_checklists。
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _now() -> dt.datetime:
    return dt.datetime.now()


class User(Base):
    """学生用户画像：年级、专业、兴趣，用于个性化匹配。"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    grade: Mapped[str] = mapped_column(String(32), default="", comment="大一/大二/大三/大四/研一...")
    major: Mapped[str] = mapped_column(String(128), default="")
    interests: Mapped[str] = mapped_column(Text, default="[]", comment="JSON 数组字符串")
    student_no: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)

    tasks: Mapped[list["UserTask"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Notice(Base):
    """原始通知：保留全文、来源与发布时间，content_hash 用于去重。"""

    __tablename__ = "notices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(32), default="其他")
    source_url: Mapped[str] = mapped_column(String(512), default="")
    source_name: Mapped[str] = mapped_column(String(128), default="")
    publish_time: Mapped[str] = mapped_column(String(32), default="", comment="ISO 日期字符串，允许为空")
    content_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    ingest_mode: Mapped[str] = mapped_column(String(16), default="manual", comment="manual/url/import/crawl")
    attachments: Mapped[str] = mapped_column(Text, default="[]", comment="JSON 数组：公告附件清单")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    analysis: Mapped["NoticeAnalysis | None"] = relationship(
        back_populates="notice", cascade="all, delete-orphan", uselist=False
    )


class NoticeAnalysis(Base):
    """AI 解析结果：结构化字段 + 原文依据 + 置信度 + 人工复核状态。"""

    __tablename__ = "notice_analysis"
    __table_args__ = (UniqueConstraint("notice_id", name="uq_analysis_notice"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    notice_id: Mapped[int] = mapped_column(ForeignKey("notices.id", ondelete="CASCADE"), index=True)

    category: Mapped[str] = mapped_column(String(32), default="其他")
    genre: Mapped[str] = mapped_column(String(16), default="notice",
                                       comment="文体 notice/publicity/news；news 不抽报名类字段")
    actionable: Mapped[bool] = mapped_column(Boolean, default=True,
                                             comment="是否存在可办理事项，决定能否进入匹配与待办")
    deadline: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True,
                                                 comment="办理/报名截止日期 YYYY-MM-DD")
    deadline_kind: Mapped[str] = mapped_column(String(16), default="",
                                               comment="报名/申报/提交/报送/征集/异议截止等语义类型")
    start_time: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="活动/讲座开始时间")
    # 设计文档 8.2 的约束类 / 时间类 / 联络类字段（由 services/fields.py 确定性抽取）
    grade_requirement: Mapped[str] = mapped_column(Text, default="[]", comment="JSON 数组：年级与学历限制")
    major_requirement: Mapped[str] = mapped_column(Text, default="[]", comment="JSON 数组：专业与学院限制")
    registration_start: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="报名开始日期")
    event_time: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="活动/讲座举办时间")
    location: Mapped[str] = mapped_column(String(200), default="", comment="报名或活动地点")
    contact: Mapped[str] = mapped_column(Text, default="{}", comment="JSON 对象：联系人/电话/邮箱")
    deadline_source: Mapped[str] = mapped_column(String(16), default="", comment="absolute / relative")
    checks: Mapped[str] = mapped_column(Text, default="{}", comment="JSON 对象：规则校验报告")
    target: Mapped[str] = mapped_column(Text, default="[]", comment="JSON 数组：适用对象")
    conditions: Mapped[str] = mapped_column(Text, default="[]", comment="JSON 数组：资格条件")
    materials: Mapped[str] = mapped_column(Text, default="[]", comment="JSON 数组：报名材料")
    apply_method: Mapped[str] = mapped_column(Text, default="", comment="报名方式")
    evidence: Mapped[str] = mapped_column(Text, default="{}", comment="JSON 对象：字段 -> 原文片段")
    # 多轨道截止时间轴。单值 deadline 只能回答「最晚什么时候」，回答不了
    # 「我是谁、我该在哪天做什么」；学位授予类通知里秋季/冬季两条轴并行，
    # 十几个节点各绑不同身份，压成一个日期必然有一条轴消失。节点以 JSON 存，
    # 与单值 deadline 并存：单值服务粗粒度提醒，节点服务个人时间轴。
    timeline: Mapped[str] = mapped_column(Text, default="[]", comment="JSON 数组：多轨道截止节点")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=True)
    engine: Mapped[str] = mapped_column(String(32), default="rule", comment="llm / rule")
    model_name: Mapped[str] = mapped_column(String(64), default="")
    review_note: Mapped[str] = mapped_column(Text, default="")
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    notice: Mapped[Notice] = relationship(back_populates="analysis")


class UserTask(Base):
    """个人待办：把通知加入时间管理。"""

    __tablename__ = "user_tasks"
    __table_args__ = (UniqueConstraint("user_id", "notice_id", name="uq_user_notice_task"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    notice_id: Mapped[int] = mapped_column(ForeignKey("notices.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="todo", comment="todo/doing/done")
    remind_time: Mapped[str] = mapped_column(String(32), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)

    user: Mapped[User] = relationship(back_populates="tasks")
    notice: Mapped[Notice] = relationship()


class WechatBinding(Base):
    """微信小程序账号绑定：openid 一一对应到本系统的 users.id。

    设计上「本系统用户」仍是唯一身份源，微信只是登录方式之一。
    这样同一个人可以在 Web 与小程序之间共享画像与待办，而不是两套数据。
    """

    __tablename__ = "wechat_bindings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    openid: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    unionid: Mapped[str] = mapped_column(String(64), default="")
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    nickname: Mapped[str] = mapped_column(String(64), default="")
    avatar: Mapped[str] = mapped_column(String(512), default="")
    # 本系统签发的本地票据（不依赖微信 session_key，避免 2 小时过期导致掉登录）
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    token_expire_at: Mapped[dt.datetime] = mapped_column(DateTime)
    last_login_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class PushSubscription(Base):
    """订阅消息授权记录：用户在小程序里点了「允许」才有资格推送。

    微信一次性订阅的规则是「一次授权一次下发」，所以 remain_count 会随着
    每次成功推送递减，减到 0 就不再尝试，避免接口报 43101 刷错误日志。
    """

    __tablename__ = "push_subscriptions"
    __table_args__ = (
        UniqueConstraint("user_id", "template_id", "notice_id", name="uq_push_user_tpl_notice"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    openid: Mapped[str] = mapped_column(String(64), default="", index=True)
    template_id: Mapped[str] = mapped_column(String(128), default="")
    notice_id: Mapped[int] = mapped_column(Integer, default=0, comment="0 表示与具体通知无关的通用授权")
    remain_count: Mapped[int] = mapped_column(Integer, default=0, comment="剩余可下发次数")
    status: Mapped[str] = mapped_column(String(16), default="granted", comment="granted/revoked/failed")
    last_error: Mapped[str] = mapped_column(String(200), default="")
    last_sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class NoticeConflict(Base):
    """跨来源冲突：同一件事被多个学院/多个页面发布，关键字段却不一致。

    这是「单来源抽取器永远发现不了」的失效点：每条通知单独看都是对的，
    放在一起才暴露矛盾。冲突只提示、不自动裁决，最终由人工确认哪条为准。
    """

    __tablename__ = "notice_conflicts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    notice_a_id: Mapped[int] = mapped_column(ForeignKey("notices.id", ondelete="CASCADE"), index=True)
    notice_b_id: Mapped[int] = mapped_column(ForeignKey("notices.id", ondelete="CASCADE"), index=True)
    field: Mapped[str] = mapped_column(String(32), default="", comment="deadline/location/contact/...")
    value_a: Mapped[str] = mapped_column(String(200), default="")
    value_b: Mapped[str] = mapped_column(String(200), default="")
    severity: Mapped[str] = mapped_column(String(16), default="warn", comment="blocker/warn/info")
    reason: Mapped[str] = mapped_column(String(300), default="")
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    resolution: Mapped[str] = mapped_column(String(32), default="", comment="use_a/use_b/both_wrong/ignored")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class NoticeChecklist(Base):
    """办理清单拆解：把「报名要准备什么」拆成可勾选的原子步骤。

    materials 只是名词清单（如「成绩单」），学生仍不知道先做什么、去哪做。
    checklist 把名词变成带顺序的动词短语，并区分「可线上完成」与「需线下跑」。
    """

    __tablename__ = "notice_checklists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    notice_id: Mapped[int] = mapped_column(ForeignKey("notices.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0, comment="执行顺序，从 1 开始")
    title: Mapped[str] = mapped_column(String(120), default="")
    detail: Mapped[str] = mapped_column(String(300), default="")
    offline: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否必须线下办理")
    depends_on: Mapped[int] = mapped_column(Integer, default=0, comment="前置步骤 seq，0 表示无依赖")
    evidence: Mapped[str] = mapped_column(String(300), default="", comment="对应的原文片段")
    source: Mapped[str] = mapped_column(String(16), default="rule", comment="rule/llm")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
