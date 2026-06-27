"""Seed data: ≥ 50 developer profiles, ≥ 20 project profiles, default WeightConfig,
prompt_versions entry, and pre-computed deterministic random embeddings.

Revision ID: 002
Revises: 001
Create Date: 2026-06-26 00:00:01.000000
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMBEDDING_DIM = 1536


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _unit_vec(seed_str: str, dim: int = EMBEDDING_DIM) -> str:
    """Deterministic normalised random vector in pgvector '[x,y,…]' literal format."""
    seed = int(hashlib.md5(seed_str.encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)
    v = [rng.gauss(0, 1) for _ in range(dim)]
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return "[" + ",".join(f"{x / norm:.6f}" for x in v) + "]"


def _work_style(i: int) -> list:
    rng = random.Random(i * 7 + 13)
    return [round(rng.uniform(0.1, 0.9), 4) for _ in range(8)]


def _motivation(i: int) -> list:
    rng = random.Random(i * 11 + 17)
    return [round(rng.uniform(0.1, 0.9), 4) for _ in range(8)]


# ─────────────────────────────────────────────────────────────────────────────
# Static project data (20 rows)
# ─────────────────────────────────────────────────────────────────────────────

_PROJECTS = [
    {
        "id": "10000000-0000-0000-0000-000000000001",
        "name": "Distributed ML Platform",
        "required_skills": ["Python", "Kubernetes", "Kafka", "MLflow", "PostgreSQL"],
        "team_structure": "Cross-functional squad of 8, async-first, high-feedback",
        "workload_intensity": 0.85, "innovation_level": 0.95,
        "timezone_overlap_required": "UTC+0 to UTC+3", "duration_weeks": 52,
        "growth_opportunities": ["distributed systems", "ML pipeline design", "technical leadership"],
    },
    {
        "id": "10000000-0000-0000-0000-000000000002",
        "name": "Real-Time Analytics Engine",
        "required_skills": ["Python", "Apache Spark", "Kafka", "Redis", "Go"],
        "team_structure": "Agile squad of 6, hybrid, sprint-based",
        "workload_intensity": 0.80, "innovation_level": 0.88,
        "timezone_overlap_required": "UTC-5 to UTC+2", "duration_weeks": 36,
        "growth_opportunities": ["real-time systems", "Spark optimization"],
    },
    {
        "id": "10000000-0000-0000-0000-000000000003",
        "name": "AI-Powered Code Review System",
        "required_skills": ["Python", "FastAPI", "LLM APIs", "Docker", "PostgreSQL"],
        "team_structure": "Small research team of 4, async-first, R&D culture",
        "workload_intensity": 0.70, "innovation_level": 0.92,
        "timezone_overlap_required": "UTC+0 to UTC+5", "duration_weeks": 24,
        "growth_opportunities": ["LLM integration", "developer tooling"],
    },
    {
        "id": "10000000-0000-0000-0000-000000000004",
        "name": "Blockchain Data Indexer",
        "required_skills": ["Python", "Node.js", "PostgreSQL", "Redis", "Web3"],
        "team_structure": "Cross-functional squad of 7, async-first",
        "workload_intensity": 0.75, "innovation_level": 0.80,
        "timezone_overlap_required": "UTC-8 to UTC+0", "duration_weeks": 40,
        "growth_opportunities": ["blockchain technology", "distributed indexing"],
    },
    {
        "id": "10000000-0000-0000-0000-000000000005",
        "name": "Multi-Cloud Orchestration Platform",
        "required_skills": ["Kubernetes", "Terraform", "Python", "Go", "AWS"],
        "team_structure": "Platform team of 10, hybrid, agile",
        "workload_intensity": 0.90, "innovation_level": 0.85,
        "timezone_overlap_required": "UTC-5 to UTC+5", "duration_weeks": 64,
        "growth_opportunities": ["cloud architecture", "platform engineering", "technical leadership"],
    },
    {
        "id": "10000000-0000-0000-0000-000000000006",
        "name": "Customer Data Platform",
        "required_skills": ["Python", "dbt", "Snowflake", "Airflow", "SQL"],
        "team_structure": "Data team of 5, agile, weekly syncs",
        "workload_intensity": 0.65, "innovation_level": 0.55,
        "timezone_overlap_required": "UTC-6 to UTC+2", "duration_weeks": 30,
        "growth_opportunities": ["data modeling", "ELT pipelines", "analytics engineering"],
    },
    {
        "id": "10000000-0000-0000-0000-000000000007",
        "name": "Predictive Churn Model",
        "required_skills": ["Python", "scikit-learn", "MLflow", "PostgreSQL", "Pandas"],
        "team_structure": "Data science team of 4, agile, research-oriented",
        "workload_intensity": 0.60, "innovation_level": 0.75,
        "timezone_overlap_required": "UTC-5 to UTC+2", "duration_weeks": 20,
        "growth_opportunities": ["ML model development", "feature engineering"],
    },
    {
        "id": "10000000-0000-0000-0000-000000000008",
        "name": "Real-Time BI Dashboard",
        "required_skills": ["Python", "React", "GraphQL", "PostgreSQL", "Redis"],
        "team_structure": "Product team of 6, scrum, 2-week sprints",
        "workload_intensity": 0.70, "innovation_level": 0.60,
        "timezone_overlap_required": "UTC+0 to UTC+3", "duration_weeks": 24,
        "growth_opportunities": ["data visualization", "GraphQL APIs"],
    },
    {
        "id": "10000000-0000-0000-0000-000000000009",
        "name": "Event-Driven ETL Pipeline",
        "required_skills": ["Python", "Kafka", "Airflow", "Spark", "S3"],
        "team_structure": "Platform squad of 5, kanban, async",
        "workload_intensity": 0.75, "innovation_level": 0.65,
        "timezone_overlap_required": "UTC-3 to UTC+5", "duration_weeks": 28,
        "growth_opportunities": ["event sourcing", "pipeline optimization"],
    },
    {
        "id": "10000000-0000-0000-0000-000000000010",
        "name": "Data Quality Automation",
        "required_skills": ["Python", "Great Expectations", "dbt", "SQL", "Airflow"],
        "team_structure": "Data platform team of 4, agile",
        "workload_intensity": 0.55, "innovation_level": 0.50,
        "timezone_overlap_required": "UTC+0 to UTC+4", "duration_weeks": 16,
        "growth_opportunities": ["data quality frameworks", "data governance"],
    },
    {
        "id": "10000000-0000-0000-0000-000000000011",
        "name": "Developer Portal Redesign",
        "required_skills": ["React", "TypeScript", "Node.js", "GraphQL", "CSS"],
        "team_structure": "Product squad of 5, scrum, 2-week sprints",
        "workload_intensity": 0.65, "innovation_level": 0.60,
        "timezone_overlap_required": "UTC-5 to UTC+2", "duration_weeks": 20,
        "growth_opportunities": ["design systems", "accessibility", "developer experience"],
    },
    {
        "id": "10000000-0000-0000-0000-000000000012",
        "name": "Mobile-First E-Commerce Platform",
        "required_skills": ["React Native", "TypeScript", "Node.js", "PostgreSQL", "Stripe"],
        "team_structure": "Product team of 8, scrum, weekly releases",
        "workload_intensity": 0.80, "innovation_level": 0.65,
        "timezone_overlap_required": "UTC-5 to UTC+0", "duration_weeks": 40,
        "growth_opportunities": ["mobile development", "payment systems"],
    },
    {
        "id": "10000000-0000-0000-0000-000000000013",
        "name": "Internal Admin Dashboard",
        "required_skills": ["React", "Python", "FastAPI", "PostgreSQL", "Docker"],
        "team_structure": "Small team of 3, kanban",
        "workload_intensity": 0.50, "innovation_level": 0.40,
        "timezone_overlap_required": "UTC+0 to UTC+3", "duration_weeks": 12,
        "growth_opportunities": ["full-stack development", "RBAC implementation"],
    },
    {
        "id": "10000000-0000-0000-0000-000000000014",
        "name": "API Gateway Modernization",
        "required_skills": ["Python", "FastAPI", "Docker", "Kubernetes", "PostgreSQL"],
        "team_structure": "Platform team of 6, agile, async-first",
        "workload_intensity": 0.75, "innovation_level": 0.70,
        "timezone_overlap_required": "UTC+0 to UTC+5", "duration_weeks": 32,
        "growth_opportunities": ["API design", "microservices", "platform engineering"],
    },
    {
        "id": "10000000-0000-0000-0000-000000000015",
        "name": "Notification Service Overhaul",
        "required_skills": ["Python", "Redis", "Kafka", "PostgreSQL", "Docker"],
        "team_structure": "Backend team of 4, scrum",
        "workload_intensity": 0.60, "innovation_level": 0.55,
        "timezone_overlap_required": "UTC-3 to UTC+3", "duration_weeks": 18,
        "growth_opportunities": ["event-driven architecture", "reliability engineering"],
    },
    {
        "id": "10000000-0000-0000-0000-000000000016",
        "name": "Zero-Trust Security Platform",
        "required_skills": ["Python", "Terraform", "AWS", "Vault", "Kubernetes"],
        "team_structure": "Security platform team of 6, agile",
        "workload_intensity": 0.85, "innovation_level": 0.80,
        "timezone_overlap_required": "UTC-5 to UTC+3", "duration_weeks": 48,
        "growth_opportunities": ["cloud security", "zero-trust architecture"],
    },
    {
        "id": "10000000-0000-0000-0000-000000000017",
        "name": "CI/CD Pipeline Automation",
        "required_skills": ["Python", "Jenkins", "GitLab CI", "Docker", "Kubernetes"],
        "team_structure": "DevOps team of 5, kanban, async-first",
        "workload_intensity": 0.70, "innovation_level": 0.60,
        "timezone_overlap_required": "UTC+0 to UTC+5", "duration_weeks": 24,
        "growth_opportunities": ["CI/CD optimization", "developer productivity"],
    },
    {
        "id": "10000000-0000-0000-0000-000000000018",
        "name": "Observability Stack Migration",
        "required_skills": ["Python", "Prometheus", "Grafana", "OpenTelemetry", "Kubernetes"],
        "team_structure": "SRE team of 4, agile",
        "workload_intensity": 0.75, "innovation_level": 0.70,
        "timezone_overlap_required": "UTC-5 to UTC+5", "duration_weeks": 20,
        "growth_opportunities": ["observability engineering", "distributed tracing"],
    },
    {
        "id": "10000000-0000-0000-0000-000000000019",
        "name": "Database Reliability Engineering",
        "required_skills": ["PostgreSQL", "Python", "pgvector", "Redis", "Terraform"],
        "team_structure": "DRE team of 3, kanban",
        "workload_intensity": 0.80, "innovation_level": 0.65,
        "timezone_overlap_required": "UTC+0 to UTC+3", "duration_weeks": 36,
        "growth_opportunities": ["database administration", "disaster recovery"],
    },
    {
        "id": "10000000-0000-0000-0000-000000000020",
        "name": "Serverless Compute Migration",
        "required_skills": ["Python", "AWS Lambda", "Terraform", "Docker", "PostgreSQL"],
        "team_structure": "Cloud team of 5, agile, async",
        "workload_intensity": 0.72, "innovation_level": 0.75,
        "timezone_overlap_required": "Americas (UTC-8 to UTC-3)", "duration_weeks": 28,
        "growth_opportunities": ["serverless architecture", "cost optimization"],
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# Developer data (50 rows)
# ─────────────────────────────────────────────────────────────────────────────

_TIMEZONES = [
    "America/New_York", "America/Los_Angeles", "America/Chicago",
    "Europe/London", "Europe/Warsaw", "Europe/Berlin",
    "Asia/Singapore", "Asia/Tokyo", "Asia/Kolkata", "Australia/Sydney",
]

_SKILL_SETS = {
    "backend":   ["Python", "FastAPI", "PostgreSQL", "Docker", "Redis", "SQLAlchemy"],
    "frontend":  ["React", "TypeScript", "JavaScript", "CSS", "GraphQL", "Node.js"],
    "fullstack": ["Python", "React", "TypeScript", "PostgreSQL", "FastAPI", "Docker"],
    "data":      ["Python", "Pandas", "scikit-learn", "SQL", "Spark", "Airflow"],
    "devops":    ["Kubernetes", "Terraform", "Docker", "AWS", "Linux", "Ansible"],
}

_GOAL_POOLS = [
    ["technical leadership", "distributed systems", "system design"],
    ["machine learning", "AI/ML research", "data science"],
    ["frontend architecture", "design systems", "user experience"],
    ["platform engineering", "developer productivity", "infrastructure"],
    ["open source contribution", "community building", "mentoring"],
    ["startup founding", "product development", "entrepreneurship"],
    ["cloud architecture", "serverless computing", "cost optimization"],
    ["security engineering", "zero-trust networks", "compliance"],
]


def _make_devs() -> list:
    devs = []
    domain_order = ["backend", "frontend", "fullstack", "data", "devops"]
    idx = 1
    for domain in domain_order:
        skills = _SKILL_SETS[domain]
        for j in range(1, 11):   # 10 devs per domain = 50 total
            dev_id = f"20000000-0000-0000-0000-{idx:012d}"
            tz = _TIMEZONES[idx % len(_TIMEZONES)]
            exp = 1 + (idx % 14)
            goals = _GOAL_POOLS[idx % len(_GOAL_POOLS)]
            extra = f"{domain.capitalize()}Skill{j}"
            devs.append({
                "id": dev_id,
                "skills": skills + [extra],
                "experience_years": exp,
                "preferred_stack": skills[:3],
                "work_style_vector": _work_style(idx),
                "motivation_vector": _motivation(idx),
                "timezone": tz,
                "availability_hours": 30 + (idx % 11),
                "career_goals": goals,
                "project_history": [],
                "is_behavioral_self_reported": (idx % 3 != 0),
                "embedding_status": "ready",
            })
            idx += 1
    return devs


_DEVELOPERS = _make_devs()


# ─────────────────────────────────────────────────────────────────────────────
# Upgrade / Downgrade
# ─────────────────────────────────────────────────────────────────────────────

def upgrade() -> None:
    bind = op.get_bind()

    # 1. Default WeightConfig (id = 1) ──────────────────────────────────────
    bind.execute(sa.text("""
        INSERT INTO weight_config
            (id, w1_skill, w2_workstyle, w3_motivation, w4_timezone, w5_growth,
             version, created_at, updated_at)
        VALUES
            (1, 0.30, 0.25, 0.20, 0.15, 0.10, 1, NOW(), NOW())
        ON CONFLICT (id) DO NOTHING
    """))

    # 2. Prompt version record ───────────────────────────────────────────────
    bind.execute(sa.text("""
        INSERT INTO prompt_versions
            (id, prompt_key, version, template_text, system_prompt,
             model_name, is_active, description, artifact_ref,
             created_by, created_at)
        VALUES
            ('30000000-0000-0000-0000-000000000001',
             'match_explanation', 1,
             'See artifact_ref for full template text.',
             'See artifact_ref for system prompt content.',
             'claude-3-5-haiku-20241022',
             TRUE,
             'Seeded from artifacts/prompts/match_explanation_v1.json',
             'artifacts/prompts/match_explanation_v1.json',
             'developer-agent',
             NOW())
        ON CONFLICT (prompt_key, version) DO NOTHING
    """))

    # 3. Project profiles (20 rows) ──────────────────────────────────────────
    for proj in _PROJECTS:
        bind.execute(sa.text("""
            INSERT INTO project_profiles
                (id, name, required_skills, team_structure, workload_intensity,
                 innovation_level, timezone_overlap_required, duration_weeks,
                 growth_opportunities, created_at, updated_at)
            VALUES
                (:id, :name, :skills, :ts, :wi, :il, :tz, :dw, :go, NOW(), NOW())
            ON CONFLICT (id) DO NOTHING
        """), {
            "id": proj["id"],
            "name": proj["name"],
            "skills": json.dumps(proj["required_skills"]),
            "ts": json.dumps(proj["team_structure"]),
            "wi": proj["workload_intensity"],
            "il": proj["innovation_level"],
            "tz": proj["timezone_overlap_required"],
            "dw": proj["duration_weeks"],
            "go": json.dumps(proj["growth_opportunities"]),
        })

    # 4. Project embeddings ──────────────────────────────────────────────────
    for proj in _PROJECTS:
        vec = _unit_vec(f"project-skill-{proj['id']}")
        emb_id = str(uuid.uuid4())
        # Use f-string for vector literal (pgvector type not parameterisable)
        bind.execute(sa.text(
            f"INSERT INTO project_embeddings"
            f" (id, project_id, embedding_type, vector, model_name, model_version, created_at, updated_at)"
            f" VALUES ('{emb_id}', '{proj['id']}', 'skill', '{vec}',"
            f" 'random-unit-vector-dev', 'dev-1.0', NOW(), NOW())"
            f" ON CONFLICT (project_id, embedding_type) DO NOTHING"
        ))

    # 5. Developer profiles (50 rows) ────────────────────────────────────────
    for dev in _DEVELOPERS:
        bind.execute(sa.text("""
            INSERT INTO developer_profiles
                (id, skills, experience_years, preferred_stack,
                 work_style_vector, motivation_vector, timezone,
                 availability_hours, career_goals, project_history,
                 is_behavioral_self_reported, embedding_status,
                 created_at, updated_at)
            VALUES
                (:id, :skills, :exp, :stack, :wsv, :mv, :tz,
                 :avh, :goals, :hist, :selfr, :estat, NOW(), NOW())
            ON CONFLICT (id) DO NOTHING
        """), {
            "id": dev["id"],
            "skills": json.dumps(dev["skills"]),
            "exp": dev["experience_years"],
            "stack": json.dumps(dev["preferred_stack"]),
            "wsv": json.dumps(dev["work_style_vector"]),
            "mv": json.dumps(dev["motivation_vector"]),
            "tz": dev["timezone"],
            "avh": dev["availability_hours"],
            "goals": json.dumps(dev["career_goals"]),
            "hist": json.dumps(dev["project_history"]),
            "selfr": dev["is_behavioral_self_reported"],
            "estat": dev["embedding_status"],
        })

    # 6. Developer embeddings (skill + behavioral, 100 rows total) ───────────
    for dev in _DEVELOPERS:
        for emb_type in ("skill", "behavioral"):
            vec = _unit_vec(f"developer-{emb_type}-{dev['id']}")
            emb_id = str(uuid.uuid4())
            bind.execute(sa.text(
                f"INSERT INTO developer_embeddings"
                f" (id, developer_id, embedding_type, vector, model_name, model_version, created_at, updated_at)"
                f" VALUES ('{emb_id}', '{dev['id']}', '{emb_type}', '{vec}',"
                f" 'random-unit-vector-dev', 'dev-1.0', NOW(), NOW())"
                f" ON CONFLICT (developer_id, embedding_type) DO NOTHING"
            ))

    # 7. Seed user accounts ──────────────────────────────────────────────────
    try:
        import bcrypt
        mgr_hash = bcrypt.hashpw(b"Manager@1234!"[:72], bcrypt.gensalt()).decode("utf-8")
        dev_hash = bcrypt.hashpw(b"Dev@1234!"[:72], bcrypt.gensalt()).decode("utf-8")
    except Exception:
        # bcrypt unavailable at migration time — store sentinel so auth always fails
        # until password is reset via the admin CLI.
        mgr_hash = "$2b$12$PLACEHOLDER_MGR_HASH_MUST_RESET_xxxxxxxxxxxxxxxx"
        dev_hash = "$2b$12$PLACEHOLDER_DEV_HASH_MUST_RESET_xxxxxxxxxxxxxxxx"

    bind.execute(sa.text("""
        INSERT INTO user_accounts
            (id, username, email, hashed_password, role, is_active, created_at, updated_at)
        VALUES
            ('40000000-0000-0000-0000-000000000001',
             'admin', 'admin@neural-sync.example.com',
             :pw, 'manager', TRUE, NOW(), NOW())
        ON CONFLICT (username) DO NOTHING
    """), {"pw": mgr_hash})

    bind.execute(sa.text("""
        INSERT INTO user_accounts
            (id, username, email, hashed_password, role, is_active,
             developer_profile_id, created_at, updated_at)
        VALUES
            ('40000000-0000-0000-0000-000000000002',
             'developer1', 'dev1@neural-sync.example.com',
             :pw, 'developer', TRUE, :dpid, NOW(), NOW())
        ON CONFLICT (username) DO NOTHING
    """), {"pw": dev_hash, "dpid": _DEVELOPERS[0]["id"]})


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text(
        "DELETE FROM developer_embeddings WHERE developer_id LIKE '20000000-%'"
    ))
    bind.execute(sa.text(
        "DELETE FROM developer_profiles WHERE id LIKE '20000000-%'"
    ))
    bind.execute(sa.text(
        "DELETE FROM project_embeddings WHERE project_id LIKE '10000000-%'"
    ))
    bind.execute(sa.text(
        "DELETE FROM project_profiles WHERE id LIKE '10000000-%'"
    ))
    bind.execute(sa.text("DELETE FROM weight_config WHERE id = 1"))
    bind.execute(sa.text(
        "DELETE FROM prompt_versions"
        " WHERE prompt_key = 'match_explanation' AND version = 1"
    ))
    bind.execute(sa.text(
        "DELETE FROM user_accounts WHERE username IN ('admin', 'developer1')"
    ))
