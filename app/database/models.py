"""数据模型：四张核心表 users / notices / notice_analysis / user_tasks。"""
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
