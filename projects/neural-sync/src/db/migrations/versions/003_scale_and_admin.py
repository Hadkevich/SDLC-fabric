"""Roster-scale columns + indexes, the 'admin' role, and an admin user (WS-B2 / WS-E1).

Adds to developer_profiles: display_name and a denormalized risk cache
(burnout/bench badge + score + computed_at) so the manager roster can filter and
aggregate risk for 10k+ developers without an O(N) per-request recompute. Adds the
matching indexes (GIN on skills, btree on display_name + risk badges). Extends the
user role CHECK to include 'admin' and seeds an admin account (Task04 §6 Admin View).

Revision ID: 003
Revises: 002
Create Date: 2026-06-28 00:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Manager-facing display labels (DeveloperProfile stores no personal name — behavioral
# matching is anonymized). Cycled by stable id order so each developer keeps the same name.
_DISPLAY_NAMES = [
    "Aisha Rahman", "Marcus Lindqvist", "Sofia Marchetti", "Daniel Okonkwo",
    "Priya Nair", "Liam Doyle", "Yuki Sato", "Elena Kuznetsova", "Omar Haddad",
    "Grace Kim", "Noah Bennett", "Mei Lin", "Tomás Ferreira", "Hannah Schmidt",
    "Arjun Mehta", "Clara Nielsen", "Kwame Owusu", "Isabella Rossi", "Lucas Moreau",
    "Fatima Zahra", "Ethan Walker", "Nadia Petrova", "Samuel Adeyemi", "Olivia Brooks",
]


def upgrade() -> None:
    # ── 1. New columns on developer_profiles ──────────────────────────────────
    op.add_column("developer_profiles", sa.Column("display_name", sa.String(120), nullable=True))
    op.add_column("developer_profiles", sa.Column("burnout_risk_badge", sa.String(10), nullable=True))
    op.add_column("developer_profiles", sa.Column("bench_risk_badge", sa.String(10), nullable=True))
    op.add_column("developer_profiles", sa.Column("burnout_risk_score", sa.Double(), nullable=True))
    op.add_column("developer_profiles", sa.Column("bench_risk_score", sa.Double(), nullable=True))
    op.add_column("developer_profiles", sa.Column("risk_computed_at", sa.DateTime(timezone=True), nullable=True))

    # ── 2. Roster filter/search indexes (10k scale) ───────────────────────────
    op.create_index("idx_developer_profiles_display_name", "developer_profiles", ["display_name"])
    op.create_index("idx_developer_profiles_burnout_badge", "developer_profiles", ["burnout_risk_badge"])
    op.create_index("idx_developer_profiles_bench_badge", "developer_profiles", ["bench_risk_badge"])
    # GIN index on the JSONB skills array for containment-based skill filtering.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_developer_profiles_skills_gin "
        "ON developer_profiles USING gin (skills jsonb_path_ops)"
    )

    # ── 3. Backfill display_name for existing rows (demo-friendly roster) ──────
    names_sql = "ARRAY[" + ",".join("'" + n.replace("'", "''") + "'" for n in _DISPLAY_NAMES) + "]"
    op.execute(
        f"""
        WITH ranked AS (
            SELECT id, row_number() OVER (ORDER BY id) AS rn FROM developer_profiles
        )
        UPDATE developer_profiles d
        SET display_name = ({names_sql})[((r.rn - 1) % {len(_DISPLAY_NAMES)}) + 1]
        FROM ranked r
        WHERE d.id = r.id AND d.display_name IS NULL
        """
    )

    # ── 4. Extend the user role CHECK to include 'admin' ──────────────────────
    op.drop_constraint("chk_user_role", "user_accounts", type_="check")
    op.create_check_constraint(
        "chk_user_role", "user_accounts", "role IN ('developer', 'manager', 'admin')"
    )

    # ── 5. Seed an admin user (Task04 §6 Admin View) ──────────────────────────
    try:
        import bcrypt
        admin_hash = bcrypt.hashpw(b"Admin@1234!", bcrypt.gensalt()).decode("utf-8")
    except Exception:  # bcrypt unavailable at migration time — sentinel (reset to use)
        admin_hash = "$2b$12$PLACEHOLDER_ADMIN_HASH_MUST_RESET_xxxxxxxxxxxxxxxx"
    op.execute(
        sa.text(
            """
            INSERT INTO user_accounts
                (id, username, email, hashed_password, role, is_active, created_at, updated_at)
            VALUES
                ('40000000-0000-0000-0000-000000000003',
                 'sysadmin', 'sysadmin@neural-sync.example.com',
                 :pw, 'admin', TRUE, NOW(), NOW())
            ON CONFLICT (username) DO NOTHING
            """
        ).bindparams(pw=admin_hash)
    )


def downgrade() -> None:
    op.execute("DELETE FROM user_accounts WHERE username = 'sysadmin'")
    op.drop_constraint("chk_user_role", "user_accounts", type_="check")
    op.create_check_constraint(
        "chk_user_role", "user_accounts", "role IN ('developer', 'manager')"
    )
    op.execute("DROP INDEX IF EXISTS idx_developer_profiles_skills_gin")
    op.drop_index("idx_developer_profiles_bench_badge", table_name="developer_profiles")
    op.drop_index("idx_developer_profiles_burnout_badge", table_name="developer_profiles")
    op.drop_index("idx_developer_profiles_display_name", table_name="developer_profiles")
    for col in ("risk_computed_at", "bench_risk_score", "burnout_risk_score",
                "bench_risk_badge", "burnout_risk_badge", "display_name"):
        op.drop_column("developer_profiles", col)
