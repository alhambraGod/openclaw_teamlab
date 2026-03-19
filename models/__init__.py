"""
OpenClaw TeamLab — SQLAlchemy ORM Models
"""
from datetime import datetime, date
from typing import Optional

from sqlalchemy import (
    Column, Integer, String, Text, Date, DateTime, Boolean, Enum, JSON,
    ForeignKey, DECIMAL, TIMESTAMP, func, Index
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class Student(Base):
    __tablename__ = "claw_students"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    email = Column(String(200))
    feishu_open_id = Column(String(100), index=True)
    avatar_url = Column(String(500))
    research_area = Column(Text)
    bio = Column(Text)
    enrollment_date = Column(Date)
    degree_type = Column(Enum("phd", "master", "postdoc", "undergrad"), default="phd")
    advisor_notes = Column(Text)
    tags = Column(JSON)
    status = Column(Enum("active", "graduated", "on_leave"), default="active", index=True)
    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())

    scores = relationship("CapabilityScore", back_populates="student", cascade="all, delete-orphan")
    events = relationship("ProgressEvent", back_populates="student", cascade="all, delete-orphan")


class CapabilityDimension(Base):
    __tablename__ = "claw_capability_dimensions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True)
    label = Column(String(100), nullable=False)
    description = Column(Text)
    category = Column(String(50))
    sort_order = Column(Integer, default=0)

    scores = relationship("CapabilityScore", back_populates="dimension")


class CapabilityScore(Base):
    __tablename__ = "claw_capability_scores"

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_id = Column(Integer, ForeignKey("claw_students.id", ondelete="CASCADE"), nullable=False)
    dimension_id = Column(Integer, ForeignKey("claw_capability_dimensions.id", ondelete="CASCADE"), nullable=False)
    score = Column(DECIMAL(3, 1), nullable=False)
    assessed_at = Column(Date, nullable=False)
    assessed_by = Column(String(50), default="system")
    evidence = Column(Text)

    student = relationship("Student", back_populates="scores")
    dimension = relationship("CapabilityDimension", back_populates="scores")

    __table_args__ = (Index("idx_student_time", "student_id", "assessed_at"),)


class ProgressEvent(Base):
    __tablename__ = "claw_progress_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_id = Column(Integer, ForeignKey("claw_students.id", ondelete="CASCADE"), nullable=False)
    event_type = Column(String(50), nullable=False)
    title = Column(String(500), nullable=False)
    description = Column(Text)
    extra_data = Column("metadata", JSON)
    event_date = Column(Date, nullable=False)
    created_at = Column(TIMESTAMP, server_default=func.now())

    student = relationship("Student", back_populates="events")


class Meeting(Base):
    __tablename__ = "claw_meetings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(500))
    meeting_type = Column(Enum("group", "individual", "seminar", "external"), nullable=False)
    meeting_date = Column(DateTime, nullable=False)
    duration_min = Column(Integer)
    attendees = Column(JSON)
    raw_notes = Column(Text)
    summary = Column(Text)
    topics = Column(JSON)
    action_items = Column(JSON)
    created_at = Column(TIMESTAMP, server_default=func.now())


class ResearchDirection(Base):
    __tablename__ = "claw_research_directions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(500), nullable=False)
    description = Column(Text)
    source = Column(Enum("pi_defined", "ai_suggested", "meeting_derived"), nullable=False)
    status = Column(Enum("active", "exploring", "paused", "completed"), default="exploring")
    related_students = Column(JSON)
    related_meetings = Column(JSON)
    evidence = Column(Text)
    priority = Column(Integer, default=5)
    parent_id = Column(Integer, ForeignKey("claw_research_directions.id", ondelete="SET NULL"))
    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())

    children = relationship("ResearchDirection", backref="parent", remote_side="ResearchDirection.id")


class TaskLog(Base):
    __tablename__ = "claw_task_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(64), nullable=False, unique=True)
    user_id = Column(String(200), index=True)
    user_name = Column(String(100))
    source = Column(Enum("feishu", "web", "api", "scheduler"), nullable=False)
    skill_used = Column(String(100), index=True)
    input_text = Column(Text)
    result_summary = Column(Text)
    result_data = Column(JSON)
    status = Column(
        Enum("queued", "running", "completed", "failed", "timeout"),
        nullable=False,
    )
    worker_id = Column(String(50))
    duration_ms = Column(Integer)
    error_message = Column(Text)
    # 异步回调地址（OpenClaw 提供；超时/完成时 POST 结果给调用方，用于飞书回复等）
    callback_url = Column(String(500))
    created_at = Column(TIMESTAMP, server_default=func.now())
    completed_at = Column(TIMESTAMP)
    timeout_at = Column(TIMESTAMP)   # 任务超时时间戳


class CollaborationRecommendation(Base):
    __tablename__ = "claw_collaboration_recommendations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_a_id = Column(Integer, ForeignKey("claw_students.id", ondelete="CASCADE"), nullable=False)
    student_b_id = Column(Integer, ForeignKey("claw_students.id", ondelete="CASCADE"), nullable=False)
    complementarity_score = Column(DECIMAL(4, 2))
    overlap_score = Column(DECIMAL(4, 2))
    research_idea = Column(Text)
    rationale = Column(Text)
    status = Column(Enum("suggested", "accepted", "in_progress", "completed", "dismissed"), default="suggested")
    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())

    student_a = relationship("Student", foreign_keys=[student_a_id])
    student_b = relationship("Student", foreign_keys=[student_b_id])


class ResearchTrend(Base):
    __tablename__ = "claw_research_trends"

    id = Column(Integer, primary_key=True, autoincrement=True)
    domain = Column(String(200), nullable=False)
    trend_title = Column(String(500))
    summary = Column(Text)
    source_urls = Column(JSON)
    relevance_score = Column(DECIMAL(3, 2))
    matched_students = Column(JSON)
    matched_directions = Column(JSON)
    discovered_at = Column(TIMESTAMP, server_default=func.now())
    notified = Column(Boolean, default=False)


class EmailDigest(Base):
    __tablename__ = "claw_email_digests"

    id = Column(Integer, primary_key=True, autoincrement=True)
    recipient_email = Column(String(200), nullable=False)
    digest_type = Column(Enum("daily", "weekly"), nullable=False)
    subject_line = Column(String(500))
    content_hash = Column(String(64))
    trend_ids = Column(JSON)
    sent_at = Column(TIMESTAMP, server_default=func.now())


class PiConfig(Base):
    __tablename__ = "claw_pi_config"

    id = Column(Integer, primary_key=True, autoincrement=True)
    config_key = Column(String(100), nullable=False, unique=True)
    config_value = Column(JSON, nullable=False)
    description = Column(String(500))
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())


class Conversation(Base):
    __tablename__ = "claw_conversations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(200), nullable=False, index=True)
    role = Column(Enum("user", "assistant", "system"), nullable=False)
    content = Column(Text, nullable=False)
    skill_used = Column(String(100))
    extra_data = Column("metadata", JSON)
    created_at = Column(TIMESTAMP, server_default=func.now())


# ── CoEvo Integration Tables ──────────────────────────────────────────────────
# These tables store data generated by openclaw_teamlab based on coevo prod data.
# They live in the openclaw_teamlab database (NOT in cognalign_coevo_prod).

class CoevoStudentLink(Base):
    """
    Maps a coevo user (student) to an openclaw Student row.
    Created automatically by the sync service; preserves both IDs for cross-reference.
    """
    __tablename__ = "claw_coevo_student_links"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # openclaw side
    openclaw_student_id = Column(Integer, ForeignKey("claw_students.id", ondelete="CASCADE"), nullable=True, index=True)
    # coevo side (no FK — different database)
    coevo_user_id = Column(Integer, nullable=False, unique=True, index=True)
    coevo_email = Column(String(200), index=True)
    coevo_username = Column(String(128))
    coevo_role = Column(String(50))
    # Project memberships from coevo (JSON array of project_ids + names)
    coevo_projects = Column(JSON)
    # Sync metadata
    last_synced_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())
    created_at = Column(TIMESTAMP, server_default=func.now())

    openclaw_student = relationship("Student")


class CoevoSyncLog(Base):
    """
    Audit log for every coevo → openclaw sync operation.
    Records what was synced, how many records, and any errors.
    """
    __tablename__ = "claw_coevo_sync_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sync_type = Column(String(50), nullable=False, index=True)  # students, meetings, projects
    triggered_by = Column(String(100))  # scheduler, api, manual
    coevo_records_read = Column(Integer, default=0)
    openclaw_records_created = Column(Integer, default=0)
    openclaw_records_updated = Column(Integer, default=0)
    status = Column(Enum("running", "completed", "failed"), nullable=False, default="running")
    error_message = Column(Text)
    started_at = Column(TIMESTAMP, server_default=func.now())
    completed_at = Column(TIMESTAMP)


class MeetingInsight(Base):
    """
    AI-generated insights derived from coevo meeting_reports and summaries.
    Stores openclaw's enriched analysis on top of coevo meeting data.
    Written by openclaw skills; references coevo meeting IDs (cross-DB, no FK).
    """
    __tablename__ = "claw_meeting_insights"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # References coevo meeting (no FK — different DB)
    coevo_meeting_id = Column(Integer, nullable=False, index=True)
    coevo_project_id = Column(Integer, nullable=True, index=True)
    # Insight content generated by openclaw
    insight_type = Column(String(50), nullable=False, index=True)
    # e.g.: progress_summary, capability_signal, blocker_pattern, team_health
    title = Column(String(500))
    content = Column(Text)
    signals = Column(JSON)          # structured key-value signals extracted
    affected_students = Column(JSON)  # list of coevo_user_ids involved
    confidence_score = Column(DECIMAL(3, 2))
    generated_by = Column(String(100), default="openclaw_ai")
    # Link to openclaw skill task that generated this
    task_id = Column(String(64), ForeignKey("claw_task_log.task_id", ondelete="SET NULL"), nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_insight_meeting_type", "coevo_meeting_id", "insight_type"),
    )


# ── Competition Enhancement Tables ───────────────────────────────────────────

class StudentRiskScore(Base):
    """AI-computed risk scores for students, updated daily by the risk engine.
    Each row represents one point-in-time assessment."""
    __tablename__ = "claw_student_risk_scores"

    id = Column(Integer, primary_key=True, autoincrement=True)
    coevo_user_id = Column(Integer, nullable=False, index=True)
    student_name = Column(String(128))
    # Composite score (0-100)
    overall_score = Column(DECIMAL(5, 2), nullable=False)
    # Sub-signal scores (0-100 each)
    blocker_persistence = Column(DECIMAL(5, 2), default=0)
    goal_completion_gap = Column(DECIMAL(5, 2), default=0)
    engagement_decline = Column(DECIMAL(5, 2), default=0)
    sentiment_score = Column(DECIMAL(5, 2), default=0)
    teacher_signal = Column(DECIMAL(5, 2), default=0)
    # Derived level
    risk_level = Column(Enum("green", "yellow", "red"), nullable=False, index=True)
    # LLM-generated explanation
    explanation = Column(Text)
    # Raw signal data for transparency
    signals_detail = Column(JSON)
    computed_at = Column(TIMESTAMP, server_default=func.now())


class StudentNarrative(Base):
    """Cached AI-generated growth narratives for students."""
    __tablename__ = "claw_student_narratives"

    id = Column(Integer, primary_key=True, autoincrement=True)
    coevo_user_id = Column(Integer, nullable=False, index=True)
    student_name = Column(String(128))
    months_covered = Column(Integer, default=3)
    narrative_text = Column(Text)
    key_milestones = Column(JSON)
    current_assessment = Column(Text)
    recommendations = Column(JSON)
    generated_at = Column(TIMESTAMP, server_default=func.now())


class ActionItemTracker(Base):
    """Cross-meeting action item lifecycle tracker."""
    __tablename__ = "claw_action_item_tracker"

    id = Column(Integer, primary_key=True, autoincrement=True)
    coevo_user_id = Column(Integer, nullable=False, index=True)
    source_meeting_id = Column(Integer, nullable=False, index=True)
    action_text = Column(Text, nullable=False)
    assignee_name = Column(String(128))
    deadline = Column(String(50))
    priority = Column(Enum("high", "medium", "low"), default="medium")
    status = Column(Enum("open", "in_progress", "done", "stale"), default="open", index=True)
    resolved_meeting_id = Column(Integer)
    resolution_evidence = Column(Text)
    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())
    stale_since = Column(TIMESTAMP)


# ── Research Direction Analysis Tables ───────────────────────────────────────

class ResearchDirectionCluster(Base):
    """AI-归纳的整体研究方向聚类，定期由 scheduler 更新。
    每个 cluster 代表团队层面的一个主要研究方向，可映射多个项目和成员。"""
    __tablename__ = "claw_research_direction_clusters"

    id = Column(Integer, primary_key=True, autoincrement=True)
    topic = Column(String(300), nullable=False)          # 方向主题名称
    description = Column(Text)                           # 详细描述
    keywords = Column(JSON)                              # ["关键词1", ...]
    similarity_group = Column(String(100), index=True)  # 聚类分组标识（方便按组过滤）
    related_projects = Column(JSON)                      # [{"id":1,"name":"..."}]
    related_students = Column(JSON)                      # [{"id":1,"name":"...","role":"..."}]
    confidence = Column(DECIMAL(3, 2), default=0.80)
    source_evidence = Column(Text)                       # 引用的具体会议/报告片段
    generated_at = Column(TIMESTAMP, server_default=func.now())
    is_active = Column(Boolean, default=True, index=True)

    ideas = relationship("ResearchDirectionIdea", back_populates="cluster",
                         cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_cluster_group_active", "similarity_group", "is_active"),
    )


class ResearchDirectionIdea(Base):
    """待激活的研究方向 idea，来源于国际前沿追踪或 gap 分析。"""
    __tablename__ = "claw_research_direction_ideas"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(300), nullable=False)
    description = Column(Text)
    inspiration_source = Column(String(200))             # "international_trend" / "gap_analysis"
    related_cluster_id = Column(Integer, ForeignKey("claw_research_direction_clusters.id",
                                                     ondelete="SET NULL"), nullable=True)
    international_refs = Column(JSON)                    # [{"team":"...","paper":"...","url":"..."}]
    status = Column(Enum("pending", "activated", "dismissed"), default="pending", index=True)
    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())

    cluster = relationship("ResearchDirectionCluster", back_populates="ideas")
