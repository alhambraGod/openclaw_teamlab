"""
OpenClaw TeamLab — Read-Only ORM Models for CognAlign-CoEvo Prod DB
Maps cognalign_coevo_prod tables. These models are NEVER written to from TeamLab.
All classes use a separate declarative base (CoevoBase) so they never mix with
the openclaw_teamlab models in migration/table-creation operations.
"""
from datetime import datetime
from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, Enum, ForeignKey,
    Integer, JSON, String, Text, TIMESTAMP,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class CoevoBase(DeclarativeBase):
    """Separate base for read-only coevo models — keeps them isolated from openclaw tables."""
    pass


class CoevoUser(CoevoBase):
    """cognalign_coevo_prod.users — platform user accounts."""
    __tablename__ = "users"

    id = Column(BigInteger, primary_key=True)
    email = Column(String(128))
    username = Column(String(128))
    role = Column(Enum("student", "teacher", "researcher", "pm"))
    is_global_admin = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    avatar_url = Column(String(512))
    bio = Column(Text)
    last_login_at = Column(TIMESTAMP)
    created_at = Column(TIMESTAMP)
    updated_at = Column(TIMESTAMP)

    # Relationships (read-only)
    project_memberships = relationship("CoevoProjectMember", back_populates="user")


class CoevoProject(CoevoBase):
    """cognalign_coevo_prod.projects — research projects / teams."""
    __tablename__ = "projects"

    id = Column(BigInteger, primary_key=True)
    project_name = Column(String(255))
    project_code = Column(String(64))
    description = Column(Text)
    creator_user_id = Column(BigInteger, ForeignKey("users.id"))
    avatar_url = Column(String(512))
    max_members = Column(Integer)
    member_count = Column(Integer)
    is_active = Column(Boolean, default=True)
    settings = Column(JSON)
    created_at = Column(TIMESTAMP)
    updated_at = Column(TIMESTAMP)

    members = relationship("CoevoProjectMember", back_populates="project")
    meetings = relationship("CoevoMeeting", back_populates="project")
    creator = relationship("CoevoUser", foreign_keys=[creator_user_id])


class CoevoProjectMember(CoevoBase):
    """cognalign_coevo_prod.project_members — role + permissions in a project."""
    __tablename__ = "project_members"

    id = Column(BigInteger, primary_key=True)
    project_id = Column(BigInteger, ForeignKey("projects.id"))
    user_id = Column(BigInteger, ForeignKey("users.id"))
    project_role = Column(Enum("teacher", "researcher", "pm", "student"))
    project_auth = Column(Enum("admin", "normal"))
    display_name = Column(String(128))
    is_muted = Column(Boolean, default=False)
    quarterly_goal = Column(Text)
    short_term_goal = Column(Text)
    joined_at = Column(TIMESTAMP)
    created_at = Column(TIMESTAMP)
    updated_at = Column(TIMESTAMP)

    project = relationship("CoevoProject", back_populates="members")
    user = relationship("CoevoUser", back_populates="project_memberships")


class CoevoMeeting(CoevoBase):
    """cognalign_coevo_prod.meetings — meeting scheduling and workflow."""
    __tablename__ = "meetings"

    id = Column(BigInteger, primary_key=True)
    project_id = Column(BigInteger, ForeignKey("projects.id"))
    meeting_name = Column(String(255))
    meeting_time = Column(TIMESTAMP)
    creator_user_id = Column(BigInteger, ForeignKey("users.id"))
    phase = Column(Enum("pre", "in", "post"))
    status = Column(Enum("draft", "notified", "in_progress", "completed"))
    settings = Column(JSON)
    overall_summary = Column(Text)
    overall_summary_generated_at = Column(TIMESTAMP)
    is_active = Column(Boolean, default=True)
    created_at = Column(TIMESTAMP)
    updated_at = Column(TIMESTAMP)

    project = relationship("CoevoProject", back_populates="meetings")
    creator = relationship("CoevoUser", foreign_keys=[creator_user_id])
    reports = relationship("CoevoMeetingReport", back_populates="meeting")
    attendees = relationship("CoevoMeetingAttendee", back_populates="meeting")


class CoevoMeetingAttendee(CoevoBase):
    """cognalign_coevo_prod.meeting_attendees — who attended a meeting."""
    __tablename__ = "meeting_attendees"

    id = Column(BigInteger, primary_key=True)
    meeting_id = Column(BigInteger, ForeignKey("meetings.id"))
    user_id = Column(BigInteger, ForeignKey("users.id"))
    attended = Column(Boolean, default=True)
    created_at = Column(TIMESTAMP)

    meeting = relationship("CoevoMeeting", back_populates="attendees")
    user = relationship("CoevoUser")


class CoevoMeetingReport(CoevoBase):
    """cognalign_coevo_prod.meeting_reports — pre/post student reports & AI summaries."""
    __tablename__ = "meeting_reports"

    id = Column(BigInteger, primary_key=True)
    meeting_id = Column(BigInteger, ForeignKey("meetings.id"))
    user_id = Column(BigInteger, ForeignKey("users.id"))
    phase = Column(Enum("pre", "post"))
    # Pre-meeting fields
    task_items = Column(Text)
    key_blockers = Column(Text)
    next_week_plan = Column(Text)
    remarks = Column(Text)
    # Post-meeting AI-generated fields
    dialogue_detail = Column(Text)
    core_viewpoints = Column(Text)
    issues_recorded = Column(Text)
    teacher_suggestions = Column(Text)
    teacher_comments = Column(Text)
    student_summary = Column(Text)
    pm_notes = Column(Text)
    status = Column(Enum("pending", "submitted", "summarized"))
    submitted_at = Column(TIMESTAMP)
    created_at = Column(TIMESTAMP)
    updated_at = Column(TIMESTAMP)

    meeting = relationship("CoevoMeeting", back_populates="reports")
    user = relationship("CoevoUser")


class CoevoCollabRecommendation(CoevoBase):
    """cognalign_coevo_prod.collaboration_recommendations — AI collaboration analysis."""
    __tablename__ = "collaboration_recommendations"

    id = Column(BigInteger, primary_key=True)
    project_id = Column(BigInteger, ForeignKey("projects.id"))
    requester_user_id = Column(BigInteger, ForeignKey("users.id"))
    target_user_ids = Column(JSON)
    mode = Column(Enum("selected", "auto_best", "third_party"))
    collaboration_direction = Column(Text)
    collaboration_suggestion = Column(Text)
    expected_output = Column(Text)
    best_partner_analysis = Column(JSON)
    raw_llm_response = Column(Text)
    status = Column(Enum("pending", "generating", "completed", "failed"))
    created_at = Column(TIMESTAMP)
    updated_at = Column(TIMESTAMP)

    project = relationship("CoevoProject")
    requester = relationship("CoevoUser", foreign_keys=[requester_user_id])


class CoevoResearchPlan(CoevoBase):
    """cognalign_coevo_prod.research_plans — long-term AI-generated research roadmaps."""
    __tablename__ = "research_plans"

    id = Column(BigInteger, primary_key=True)
    project_id = Column(BigInteger, ForeignKey("projects.id"))
    creator_user_id = Column(BigInteger, ForeignKey("users.id"))
    plan_name = Column(String(255))
    total_cycles = Column(Integer)
    selected_meeting_ids = Column(JSON)
    selected_count = Column(Integer)
    generated_count = Column(Integer)
    nodes = Column(JSON)
    final_goal = Column(Text)
    final_expected_effect = Column(Text)
    status = Column(Enum("pending", "generating", "completed", "failed"))
    created_at = Column(TIMESTAMP)
    updated_at = Column(TIMESTAMP)

    project = relationship("CoevoProject")
    creator = relationship("CoevoUser", foreign_keys=[creator_user_id])


class CoevoAgentMemory(CoevoBase):
    """cognalign_coevo_prod.agent_memories — CAMA memory module entries."""
    __tablename__ = "agent_memories"

    id = Column(BigInteger, primary_key=True)
    project_id = Column(BigInteger, ForeignKey("projects.id"))
    user_id = Column(BigInteger, ForeignKey("users.id"))
    meeting_id = Column(BigInteger, ForeignKey("meetings.id"))
    memory_type = Column(Enum(
        "meeting_summary", "pre_report", "post_report",
        "todo_tracking", "teacher_feedback", "project_context", "per_person_summary"
    ))
    content = Column(Text)
    extra_metadata = Column("metadata", JSON)
    relevance_score = Column(Integer)
    reference_count = Column(Integer)
    last_referenced_at = Column(TIMESTAMP)
    cycle_id = Column(String(128))
    created_at = Column(TIMESTAMP)
    updated_at = Column(TIMESTAMP)
