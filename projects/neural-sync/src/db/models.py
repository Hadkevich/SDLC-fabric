"""SQLAlchemy ORM models derived from artifacts/data-model.json.

All entities are defined here. The pgvector VECTOR type is used for
embedding columns. CASCADE FK constraints implement the GDPR erasure
cascade documented in the data model.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Double,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# pgvector support — import at module level so Alembic can introspect
try:
    from pgvector.sqlalchemy import Vector  # type: ignore
except ImportError:  # pragma: no cover
    # Fallback for environments without pgvector installed (type checking only)
    from sqlalchemy import Text as Vector  # type: ignore  # noqa: F811

from src.core.settings import settings

# Single source of truth for the vector column dimension (see settings.embedding_dim).
EMBEDDING_DIM: int = settings.embedding_dim


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# ─────────────────────────────────────────────────────────────────────────────
# UserAccount
# ─────────────────────────────────────────────────────────────────────────────
class UserAccount(Base):
    __tablename__ = "user_accounts"
    __table_args__ = (
        CheckConstraint("role IN ('developer', 'manager')", name="chk_user_role"),
        Index("idx_user_accounts_email", "email", unique=True),
        Index("idx_user_accounts_username", "username", unique=True),
        Index("idx_user_accounts_developer_profile_id", "developer_profile_id", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    username: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    hashed_password: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    developer_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("developer_profiles.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    developer_profile: Mapped["DeveloperProfile | None"] = relationship(
        "DeveloperProfile", foreign_keys=[developer_profile_id], back_populates="user_account"
    )


# ─────────────────────────────────────────────────────────────────────────────
# DeveloperProfile  (GDPR cascade root, step 1)
# ─────────────────────────────────────────────────────────────────────────────
class DeveloperProfile(Base):
    __tablename__ = "developer_profiles"
    __table_args__ = (
        CheckConstraint(
            "embedding_status IN ('pending','ready','failed')",
            name="chk_developer_embedding_status",
        ),
        CheckConstraint("experience_years >= 0 AND experience_years <= 60", name="chk_experience_years"),
        CheckConstraint("availability_hours >= 0 AND availability_hours <= 168", name="chk_availability_hours"),
        Index("idx_developer_profiles_timezone", "timezone"),
        Index("idx_developer_profiles_embedding_status", "embedding_status"),
        Index("idx_developer_profiles_experience_years", "experience_years"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )
    skills: Mapped[list] = mapped_column(JSONB, nullable=False)
    experience_years: Mapped[int] = mapped_column(Integer, nullable=False)
    preferred_stack: Mapped[list] = mapped_column(JSONB, nullable=False)
    # NOTE: work_style_vector and motivation_vector are stored in the DB
    # but MUST NEVER be exposed in API responses or passed to Claude.
    work_style_vector: Mapped[list] = mapped_column(JSONB, nullable=False)
    motivation_vector: Mapped[list] = mapped_column(JSONB, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    availability_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    career_goals: Mapped[list] = mapped_column(JSONB, nullable=False)
    project_history: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    is_behavioral_self_reported: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    embedding_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    # Relationships
    user_account: Mapped["UserAccount | None"] = relationship(
        "UserAccount", foreign_keys="[UserAccount.developer_profile_id]",
        back_populates="developer_profile", uselist=False
    )
    embeddings: Mapped[list["DeveloperEmbedding"]] = relationship(
        "DeveloperEmbedding", back_populates="developer", cascade="all, delete-orphan"
    )
    match_records: Mapped[list["MatchRecord"]] = relationship(
        "MatchRecord", back_populates="developer", cascade="all, delete-orphan"
    )
    feedback_records: Mapped[list["FeedbackRecord"]] = relationship(
        "FeedbackRecord", back_populates="developer", cascade="all, delete-orphan"
    )
    allocation_records: Mapped[list["AllocationRecord"]] = relationship(
        "AllocationRecord", back_populates="developer", cascade="all, delete-orphan"
    )
    explanation_cache_entries: Mapped[list["ExplanationCache"]] = relationship(
        "ExplanationCache", back_populates="developer", cascade="all, delete-orphan"
    )


# ─────────────────────────────────────────────────────────────────────────────
# ProjectProfile
# ─────────────────────────────────────────────────────────────────────────────
class ProjectProfile(Base):
    __tablename__ = "project_profiles"
    __table_args__ = (
        CheckConstraint(
            "workload_intensity >= 0.0 AND workload_intensity <= 1.0",
            name="chk_workload_intensity",
        ),
        CheckConstraint(
            "innovation_level >= 0.0 AND innovation_level <= 1.0",
            name="chk_innovation_level",
        ),
        CheckConstraint("duration_weeks > 0", name="chk_duration_weeks"),
        Index("idx_project_profiles_timezone_overlap", "timezone_overlap_required"),
        Index("idx_project_profiles_workload_intensity", "workload_intensity"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    required_skills: Mapped[list] = mapped_column(JSONB, nullable=False)
    team_structure: Mapped[dict | str] = mapped_column(JSONB, nullable=False)
    workload_intensity: Mapped[float] = mapped_column(Double, nullable=False)
    innovation_level: Mapped[float] = mapped_column(Double, nullable=False)
    timezone_overlap_required: Mapped[str] = mapped_column(String(64), nullable=False)
    duration_weeks: Mapped[int] = mapped_column(Integer, nullable=False)
    growth_opportunities: Mapped[list] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    embeddings: Mapped[list["ProjectEmbedding"]] = relationship(
        "ProjectEmbedding", back_populates="project", cascade="all, delete-orphan"
    )
    match_records: Mapped[list["MatchRecord"]] = relationship(
        "MatchRecord", back_populates="project", cascade="all, delete-orphan"
    )
    allocation_records: Mapped[list["AllocationRecord"]] = relationship(
        "AllocationRecord", back_populates="project"
    )
    explanation_cache_entries: Mapped[list["ExplanationCache"]] = relationship(
        "ExplanationCache", back_populates="project", cascade="all, delete-orphan"
    )


# ─────────────────────────────────────────────────────────────────────────────
# DeveloperEmbedding  (pgvector — GDPR step 2 via CASCADE)
# ─────────────────────────────────────────────────────────────────────────────
class DeveloperEmbedding(Base):
    __tablename__ = "developer_embeddings"
    __table_args__ = (
        CheckConstraint(
            "embedding_type IN ('skill','behavioral')",
            name="chk_dev_embedding_type",
        ),
        UniqueConstraint("developer_id", "embedding_type", name="uq_developer_embeddings_dev_type"),
        Index("idx_developer_embeddings_developer_id", "developer_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    developer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("developer_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    embedding_type: Mapped[str] = mapped_column(String(20), nullable=False)
    vector: Mapped[list] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    model_version: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    developer: Mapped["DeveloperProfile"] = relationship(
        "DeveloperProfile", back_populates="embeddings"
    )


# ─────────────────────────────────────────────────────────────────────────────
# ProjectEmbedding  (pgvector)
# ─────────────────────────────────────────────────────────────────────────────
class ProjectEmbedding(Base):
    __tablename__ = "project_embeddings"
    __table_args__ = (
        CheckConstraint("embedding_type IN ('skill')", name="chk_proj_embedding_type"),
        UniqueConstraint("project_id", "embedding_type", name="uq_project_embeddings_proj_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("project_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    embedding_type: Mapped[str] = mapped_column(String(20), nullable=False)
    vector: Mapped[list] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    model_version: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    project: Mapped["ProjectProfile"] = relationship(
        "ProjectProfile", back_populates="embeddings"
    )


# ─────────────────────────────────────────────────────────────────────────────
# MatchRecord  (GDPR step 3 via CASCADE; FeedbackRecord cascades from here)
# ─────────────────────────────────────────────────────────────────────────────
class MatchRecord(Base):
    __tablename__ = "match_records"
    __table_args__ = (
        CheckConstraint("match_score >= 0.0 AND match_score <= 1.0", name="chk_match_score"),
        CheckConstraint("skill_score >= 0.0 AND skill_score <= 1.0", name="chk_skill_score"),
        CheckConstraint("workstyle_score >= 0.0 AND workstyle_score <= 1.0", name="chk_workstyle_score"),
        CheckConstraint("motivation_score >= 0.0 AND motivation_score <= 1.0", name="chk_motivation_score"),
        CheckConstraint("timezone_score >= 0.0 AND timezone_score <= 1.0", name="chk_timezone_score"),
        CheckConstraint("growth_score >= 0.0 AND growth_score <= 1.0", name="chk_growth_score"),
        CheckConstraint(
            "explanation_source IN ('stub_pending','stub_permanent','claude_cached','claude_async')",
            name="chk_explanation_source",
        ),
        Index("idx_match_records_developer_id", "developer_id"),
        Index("idx_match_records_developer_score_desc", "developer_id", "match_score"),
        Index("idx_match_records_project_id", "project_id"),
        Index("idx_match_records_timestamp", "timestamp"),
        Index("idx_match_records_explanation_source", "explanation_source"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    developer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("developer_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("project_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    match_score: Mapped[float] = mapped_column(Double, nullable=False)
    skill_score: Mapped[float] = mapped_column(Double, nullable=False)
    workstyle_score: Mapped[float] = mapped_column(Double, nullable=False)
    motivation_score: Mapped[float] = mapped_column(Double, nullable=False)
    timezone_score: Mapped[float] = mapped_column(Double, nullable=False)
    growth_score: Mapped[float] = mapped_column(Double, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    explanation_source: Mapped[str] = mapped_column(String(30), nullable=False, default="stub_pending")
    explanation_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    risks: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    growth_potential: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    weights_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    vector_search_degraded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    behavioral_data_unavailable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    developer: Mapped["DeveloperProfile"] = relationship(
        "DeveloperProfile", back_populates="match_records"
    )
    project: Mapped["ProjectProfile"] = relationship(
        "ProjectProfile", back_populates="match_records"
    )
    feedback_records: Mapped[list["FeedbackRecord"]] = relationship(
        "FeedbackRecord", back_populates="match", cascade="all, delete-orphan"
    )


# ─────────────────────────────────────────────────────────────────────────────
# FeedbackRecord  (GDPR step 4 — two independent CASCADE paths)
# ─────────────────────────────────────────────────────────────────────────────
class FeedbackRecord(Base):
    __tablename__ = "feedback_records"
    __table_args__ = (
        UniqueConstraint("developer_id", "match_id", name="uq_feedback_records_dev_match"),
        Index("idx_feedback_records_developer_id_accepted", "developer_id", "accepted"),
        Index("idx_feedback_records_match_id", "match_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    developer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("developer_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    match_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("match_records.id", ondelete="CASCADE"),
        nullable=False,
    )
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    developer: Mapped["DeveloperProfile"] = relationship(
        "DeveloperProfile", back_populates="feedback_records"
    )
    match: Mapped["MatchRecord"] = relationship(
        "MatchRecord", back_populates="feedback_records"
    )


# ─────────────────────────────────────────────────────────────────────────────
# AllocationRecord  (GDPR step 5 via CASCADE)
# ─────────────────────────────────────────────────────────────────────────────
class AllocationRecord(Base):
    __tablename__ = "allocation_records"
    __table_args__ = (
        CheckConstraint("end_date >= start_date", name="chk_alloc_date_order"),
        CheckConstraint(
            "workload_intensity >= 0.0 AND workload_intensity <= 1.0",
            name="chk_alloc_workload_intensity",
        ),
        Index("idx_allocation_records_developer_id", "developer_id"),
        Index("idx_allocation_records_developer_end_date", "developer_id", "end_date"),
        Index("idx_allocation_records_developer_active", "developer_id", "is_active"),
        Index(
            "idx_allocation_records_workload_intensity",
            "developer_id", "workload_intensity", "start_date",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    developer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("developer_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("project_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    workload_intensity: Mapped[float] = mapped_column(Double, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    developer: Mapped["DeveloperProfile"] = relationship(
        "DeveloperProfile", back_populates="allocation_records"
    )
    project: Mapped["ProjectProfile | None"] = relationship(
        "ProjectProfile", back_populates="allocation_records"
    )


# ─────────────────────────────────────────────────────────────────────────────
# WeightConfig  (singleton row, id=1)
# ─────────────────────────────────────────────────────────────────────────────
class WeightConfig(Base):
    __tablename__ = "weight_config"
    __table_args__ = (
        CheckConstraint("id = 1", name="chk_weight_config_singleton"),
        CheckConstraint("w1_skill >= 0.0", name="chk_w1"),
        CheckConstraint("w2_workstyle >= 0.0", name="chk_w2"),
        CheckConstraint("w3_motivation >= 0.0", name="chk_w3"),
        CheckConstraint("w4_timezone >= 0.0", name="chk_w4"),
        CheckConstraint("w5_growth >= 0.0", name="chk_w5"),
        CheckConstraint(
            "ABS(w1_skill + w2_workstyle + w3_motivation + w4_timezone + w5_growth - 1.0) < 0.001",
            name="chk_weight_config_sum",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    w1_skill: Mapped[float] = mapped_column(Double, nullable=False, default=0.30)
    w2_workstyle: Mapped[float] = mapped_column(Double, nullable=False, default=0.25)
    w3_motivation: Mapped[float] = mapped_column(Double, nullable=False, default=0.20)
    w4_timezone: Mapped[float] = mapped_column(Double, nullable=False, default=0.15)
    w5_growth: Mapped[float] = mapped_column(Double, nullable=False, default=0.10)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )


# ─────────────────────────────────────────────────────────────────────────────
# PromptVersion  (immutable audit trail for Claude prompts — AC12)
# ─────────────────────────────────────────────────────────────────────────────
class PromptVersion(Base):
    __tablename__ = "prompt_versions"
    __table_args__ = (
        CheckConstraint("version >= 1", name="chk_prompt_version_ge1"),
        UniqueConstraint("prompt_key", "version", name="uq_prompt_versions_key_version"),
        Index("idx_prompt_versions_key_active", "prompt_key", "is_active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    prompt_key: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    template_text: Mapped[str] = mapped_column(Text, nullable=False)
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    explanation_cache_entries: Mapped[list["ExplanationCache"]] = relationship(
        "ExplanationCache", back_populates="prompt_version"
    )


# ─────────────────────────────────────────────────────────────────────────────
# ExplanationCache  (GDPR step 6 via CASCADE on developer_id)
# ─────────────────────────────────────────────────────────────────────────────
class ExplanationCache(Base):
    __tablename__ = "explanation_cache"
    __table_args__ = (
        Index("idx_explanation_cache_key", "cache_key", unique=True),
        Index("idx_explanation_cache_developer_id", "developer_id"),
        Index("idx_explanation_cache_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    cache_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    developer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("developer_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("project_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    match_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("match_records.id", ondelete="SET NULL"),
        nullable=True,
    )
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("prompt_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    developer: Mapped["DeveloperProfile"] = relationship(
        "DeveloperProfile", back_populates="explanation_cache_entries"
    )
    project: Mapped["ProjectProfile"] = relationship(
        "ProjectProfile", back_populates="explanation_cache_entries"
    )
    prompt_version: Mapped["PromptVersion | None"] = relationship(
        "PromptVersion", back_populates="explanation_cache_entries"
    )


# ─────────────────────────────────────────────────────────────────────────────
# ErasureAuditLog  (RETAINED post-erasure — compliance record)
# developer_id stored as VARCHAR (no FK) so record survives cascade
# ─────────────────────────────────────────────────────────────────────────────
class ErasureAuditLog(Base):
    __tablename__ = "erasure_audit_log"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','completed','failed')",
            name="chk_erasure_status",
        ),
        Index("idx_erasure_audit_log_developer_id", "developer_id"),
        Index("idx_erasure_audit_log_status", "status"),
        Index("idx_erasure_audit_log_requested_at", "requested_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    erasure_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True, default=uuid.uuid4
    )
    developer_id: Mapped[str] = mapped_column(String(36), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    initiating_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    steps_completed: Mapped[list | None] = mapped_column(JSONB, nullable=True)
