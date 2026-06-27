"""Initial schema: all tables, indexes, and constraints.

Revision ID: 001
Revises: None
Create Date: 2026-06-26 00:00:00.000000

Enables pgvector and uuid-ossp extensions, then creates all tables
defined in artifacts/data-model.json.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMBEDDING_DIM = 1536


def upgrade() -> None:
    # ── Extensions ─────────────────────────────────────────────────────────
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";')

    # ── user_accounts ───────────────────────────────────────────────────────
    op.create_table(
        "user_accounts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("username", sa.String(100), nullable=False, unique=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("hashed_password", sa.Text, nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="TRUE"),
        sa.Column("developer_profile_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.CheckConstraint("role IN ('developer', 'manager')", name="chk_user_role"),
    )
    op.create_index("idx_user_accounts_email", "user_accounts", ["email"], unique=True)
    op.create_index("idx_user_accounts_username", "user_accounts", ["username"], unique=True)
    op.create_index("idx_user_accounts_developer_profile_id", "user_accounts", ["developer_profile_id"], unique=True)

    # ── developer_profiles ──────────────────────────────────────────────────
    op.create_table(
        "developer_profiles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("skills", JSONB, nullable=False),
        sa.Column("experience_years", sa.Integer, nullable=False),
        sa.Column("preferred_stack", JSONB, nullable=False),
        sa.Column("work_style_vector", JSONB, nullable=False),
        sa.Column("motivation_vector", JSONB, nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("availability_hours", sa.Integer, nullable=False),
        sa.Column("career_goals", JSONB, nullable=False),
        sa.Column("project_history", JSONB, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("is_behavioral_self_reported", sa.Boolean, nullable=False, server_default="TRUE"),
        sa.Column("embedding_status", sa.String(20), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.CheckConstraint("embedding_status IN ('pending','ready','failed')", name="chk_developer_embedding_status"),
        sa.CheckConstraint("experience_years >= 0 AND experience_years <= 60", name="chk_experience_years"),
        sa.CheckConstraint("availability_hours >= 0 AND availability_hours <= 168", name="chk_availability_hours"),
        sa.ForeignKeyConstraint(["user_id"], ["user_accounts.id"], ondelete="SET NULL"),
    )
    op.create_index("idx_developer_profiles_timezone", "developer_profiles", ["timezone"])
    op.create_index("idx_developer_profiles_embedding_status", "developer_profiles", ["embedding_status"])
    op.create_index("idx_developer_profiles_experience_years", "developer_profiles", ["experience_years"])

    # Add FK from user_accounts to developer_profiles (circular — add after both tables exist)
    op.create_foreign_key(
        "fk_user_accounts_developer_profile_id",
        "user_accounts", "developer_profiles",
        ["developer_profile_id"], ["id"],
        ondelete="SET NULL",
    )

    # ── project_profiles ────────────────────────────────────────────────────
    op.create_table(
        "project_profiles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(255), nullable=False, server_default=sa.text("''")),
        sa.Column("required_skills", JSONB, nullable=False),
        sa.Column("team_structure", JSONB, nullable=False),
        sa.Column("workload_intensity", sa.Double(), nullable=False),
        sa.Column("innovation_level", sa.Double(), nullable=False),
        sa.Column("timezone_overlap_required", sa.String(64), nullable=False),
        sa.Column("duration_weeks", sa.Integer, nullable=False),
        sa.Column("growth_opportunities", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.CheckConstraint("workload_intensity >= 0.0 AND workload_intensity <= 1.0", name="chk_workload_intensity"),
        sa.CheckConstraint("innovation_level >= 0.0 AND innovation_level <= 1.0", name="chk_innovation_level"),
        sa.CheckConstraint("duration_weeks > 0", name="chk_duration_weeks"),
    )
    op.create_index("idx_project_profiles_timezone_overlap", "project_profiles", ["timezone_overlap_required"])
    op.create_index("idx_project_profiles_workload_intensity", "project_profiles", ["workload_intensity"])

    # ── developer_embeddings (pgvector) ─────────────────────────────────────
    op.execute(f"""
        CREATE TABLE developer_embeddings (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            developer_id UUID NOT NULL REFERENCES developer_profiles(id) ON DELETE CASCADE,
            embedding_type VARCHAR(20) NOT NULL CHECK (embedding_type IN ('skill', 'behavioral')),
            vector VECTOR({EMBEDDING_DIM}) NOT NULL,
            model_name VARCHAR(100) NOT NULL,
            model_version VARCHAR(50) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_developer_embeddings_dev_type UNIQUE (developer_id, embedding_type)
        );
    """)
    op.execute("""
        CREATE INDEX idx_developer_embeddings_hnsw_skill
        ON developer_embeddings USING hnsw (vector vector_cosine_ops)
        WITH (m=16, ef_construction=64)
        WHERE embedding_type = 'skill';
    """)
    op.execute("""
        CREATE INDEX idx_developer_embeddings_hnsw_behavioral
        ON developer_embeddings USING hnsw (vector vector_cosine_ops)
        WITH (m=16, ef_construction=64)
        WHERE embedding_type = 'behavioral';
    """)
    op.create_index("idx_developer_embeddings_developer_id", "developer_embeddings", ["developer_id"])

    # ── project_embeddings (pgvector) ───────────────────────────────────────
    op.execute(f"""
        CREATE TABLE project_embeddings (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id UUID NOT NULL REFERENCES project_profiles(id) ON DELETE CASCADE,
            embedding_type VARCHAR(20) NOT NULL CHECK (embedding_type IN ('skill')),
            vector VECTOR({EMBEDDING_DIM}) NOT NULL,
            model_name VARCHAR(100) NOT NULL,
            model_version VARCHAR(50) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_project_embeddings_proj_type UNIQUE (project_id, embedding_type)
        );
    """)
    op.execute("""
        CREATE INDEX idx_project_embeddings_hnsw_cosine
        ON project_embeddings USING hnsw (vector vector_cosine_ops)
        WITH (m=16, ef_construction=64);
    """)

    # ── match_records ───────────────────────────────────────────────────────
    op.create_table(
        "match_records",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("developer_id", UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), nullable=False),
        sa.Column("match_score", sa.Double(), nullable=False),
        sa.Column("skill_score", sa.Double(), nullable=False),
        sa.Column("workstyle_score", sa.Double(), nullable=False),
        sa.Column("motivation_score", sa.Double(), nullable=False),
        sa.Column("timezone_score", sa.Double(), nullable=False),
        sa.Column("growth_score", sa.Double(), nullable=False),
        sa.Column("explanation", sa.Text, nullable=False),
        sa.Column("explanation_source", sa.String(30), nullable=False, server_default=sa.text("'stub_pending'")),
        sa.Column("explanation_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("risks", JSONB, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("growth_potential", JSONB, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("weights_snapshot", JSONB, nullable=False),
        sa.Column("vector_search_degraded", sa.Boolean, nullable=False, server_default="FALSE"),
        sa.Column("behavioral_data_unavailable", sa.Boolean, nullable=False, server_default="FALSE"),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(["developer_id"], ["developer_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["project_profiles.id"], ondelete="CASCADE"),
        sa.CheckConstraint("match_score >= 0.0 AND match_score <= 1.0", name="chk_match_score"),
        sa.CheckConstraint("explanation_source IN ('stub_pending','stub_permanent','claude_cached','claude_async')", name="chk_explanation_source"),
    )
    op.create_index("idx_match_records_developer_id", "match_records", ["developer_id"])
    op.create_index("idx_match_records_developer_score_desc", "match_records", ["developer_id", "match_score"])
    op.create_index("idx_match_records_project_id", "match_records", ["project_id"])
    op.create_index("idx_match_records_timestamp", "match_records", ["timestamp"])
    op.create_index("idx_match_records_explanation_source", "match_records", ["explanation_source"])

    # ── feedback_records ────────────────────────────────────────────────────
    op.create_table(
        "feedback_records",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("developer_id", UUID(as_uuid=True), nullable=False),
        sa.Column("match_id", UUID(as_uuid=True), nullable=False),
        sa.Column("accepted", sa.Boolean, nullable=False),
        sa.Column("comment", sa.Text, nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(["developer_id"], ["developer_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["match_id"], ["match_records.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("developer_id", "match_id", name="uq_feedback_records_dev_match"),
    )
    op.create_index("idx_feedback_records_developer_id_accepted", "feedback_records", ["developer_id", "accepted"])
    op.create_index("idx_feedback_records_match_id", "feedback_records", ["match_id"])

    # ── allocation_records ──────────────────────────────────────────────────
    op.create_table(
        "allocation_records",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("developer_id", UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), nullable=True),
        sa.Column("start_date", sa.Date, nullable=False),
        sa.Column("end_date", sa.Date, nullable=False),
        sa.Column("workload_intensity", sa.Double(), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="TRUE"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(["developer_id"], ["developer_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["project_profiles.id"], ondelete="SET NULL"),
        sa.CheckConstraint("end_date >= start_date", name="chk_alloc_date_order"),
        sa.CheckConstraint("workload_intensity >= 0.0 AND workload_intensity <= 1.0", name="chk_alloc_workload_intensity"),
    )
    op.create_index("idx_allocation_records_developer_id", "allocation_records", ["developer_id"])
    op.create_index("idx_allocation_records_developer_end_date", "allocation_records", ["developer_id", "end_date"])
    op.create_index("idx_allocation_records_developer_active", "allocation_records", ["developer_id", "is_active"])
    op.create_index("idx_allocation_records_workload_intensity", "allocation_records", ["developer_id", "workload_intensity", "start_date"])

    # ── weight_config ───────────────────────────────────────────────────────
    op.create_table(
        "weight_config",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("w1_skill", sa.Double(), nullable=False, server_default="0.30"),
        sa.Column("w2_workstyle", sa.Double(), nullable=False, server_default="0.25"),
        sa.Column("w3_motivation", sa.Double(), nullable=False, server_default="0.20"),
        sa.Column("w4_timezone", sa.Double(), nullable=False, server_default="0.15"),
        sa.Column("w5_growth", sa.Double(), nullable=False, server_default="0.10"),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("updated_by", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.CheckConstraint("id = 1", name="chk_weight_config_singleton"),
        sa.CheckConstraint("ABS(w1_skill + w2_workstyle + w3_motivation + w4_timezone + w5_growth - 1.0) < 0.001", name="chk_weight_config_sum"),
        sa.ForeignKeyConstraint(["updated_by"], ["user_accounts.id"], ondelete="SET NULL"),
    )

    # ── prompt_versions ─────────────────────────────────────────────────────
    op.create_table(
        "prompt_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("prompt_key", sa.String(100), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("template_text", sa.Text, nullable=False),
        sa.Column("system_prompt", sa.Text, nullable=True),
        sa.Column("model_name", sa.String(100), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="FALSE"),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("artifact_ref", sa.String(255), nullable=True),
        sa.Column("created_by", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.CheckConstraint("version >= 1", name="chk_prompt_version_ge1"),
        sa.UniqueConstraint("prompt_key", "version", name="uq_prompt_versions_key_version"),
    )
    op.create_index("idx_prompt_versions_key_active", "prompt_versions", ["prompt_key", "is_active"])

    # ── explanation_cache ───────────────────────────────────────────────────
    op.create_table(
        "explanation_cache",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("cache_key", sa.String(64), nullable=False, unique=True),
        sa.Column("developer_id", UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), nullable=False),
        sa.Column("match_id", UUID(as_uuid=True), nullable=True),
        sa.Column("explanation", sa.Text, nullable=False),
        sa.Column("prompt_version_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["developer_id"], ["developer_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["project_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["match_id"], ["match_records.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["prompt_version_id"], ["prompt_versions.id"], ondelete="SET NULL"),
    )
    op.create_index("idx_explanation_cache_key", "explanation_cache", ["cache_key"], unique=True)
    op.create_index("idx_explanation_cache_developer_id", "explanation_cache", ["developer_id"])
    op.create_index("idx_explanation_cache_expires_at", "explanation_cache", ["expires_at"])

    # ── erasure_audit_log ───────────────────────────────────────────────────
    op.create_table(
        "erasure_audit_log",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("erasure_request_id", UUID(as_uuid=True), nullable=False, unique=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("developer_id", sa.String(36), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("initiating_user_id", sa.String(36), nullable=True),
        sa.Column("error_detail", sa.Text, nullable=True),
        sa.Column("steps_completed", JSONB, nullable=True),
        sa.CheckConstraint("status IN ('pending','completed','failed')", name="chk_erasure_status"),
    )
    op.create_index("idx_erasure_audit_log_developer_id", "erasure_audit_log", ["developer_id"])
    op.create_index("idx_erasure_audit_log_status", "erasure_audit_log", ["status"])
    op.create_index("idx_erasure_audit_log_requested_at", "erasure_audit_log", ["requested_at"])


def downgrade() -> None:
    op.drop_table("erasure_audit_log")
    op.drop_table("explanation_cache")
    op.drop_table("prompt_versions")
    op.drop_table("weight_config")
    op.drop_table("allocation_records")
    op.drop_table("feedback_records")
    op.drop_table("match_records")
    op.execute("DROP TABLE IF EXISTS project_embeddings;")
    op.execute("DROP TABLE IF EXISTS developer_embeddings;")
    op.drop_table("project_profiles")
    op.execute("ALTER TABLE user_accounts DROP CONSTRAINT IF EXISTS fk_user_accounts_developer_profile_id;")
    op.drop_table("developer_profiles")
    op.drop_table("user_accounts")
