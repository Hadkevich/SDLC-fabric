"""Demo credentials: align usernames to roles and set a uniform demo password.

Removes the confusing legacy naming where username 'admin' was a *manager* and the real
admin was 'sysadmin'. After this migration the three demo accounts are:
    manager / 123   (role manager)
    admin   / 123   (role admin)
    user1   / 123   (role developer, linked to the first developer profile)

Idempotent across both a fresh build (002 → admin/developer1/…, 003 → sysadmin) and the
already-running demo DB (admin/user1/sysadmin): the renames converge to the same end state.
Order matters — 'admin' (manager) is renamed to 'manager' first so the name is free for the
real admin account.

Revision ID: 004
Revises: 003
Create Date: 2026-06-28 00:00:02.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _hash(password: str) -> str:
    try:
        import bcrypt
        return bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt()).decode("utf-8")
    except Exception:  # bcrypt unavailable at migration time → sentinel (reset to use)
        return "$2b$12$PLACEHOLDER_DEMO_HASH_MUST_RESET_xxxxxxxxxxxxxxxx"


def upgrade() -> None:
    pw = _hash("123")
    # 1. manager account: 'admin' (role manager) → 'manager'
    op.execute(sa.text(
        "UPDATE user_accounts SET username='manager', hashed_password=:pw "
        "WHERE username='admin' AND role='manager'"
    ).bindparams(pw=pw))
    # 2. developer account: 'developer1' or already-renamed 'user1' → 'user1'
    op.execute(sa.text(
        "UPDATE user_accounts SET username='user1', hashed_password=:pw "
        "WHERE username IN ('developer1', 'user1') AND role='developer'"
    ).bindparams(pw=pw))
    # 3. admin account: 'sysadmin' (role admin) → 'admin' (name now free after step 1)
    op.execute(sa.text(
        "UPDATE user_accounts SET username='admin', hashed_password=:pw "
        "WHERE username='sysadmin' AND role='admin'"
    ).bindparams(pw=pw))


def downgrade() -> None:
    # Best-effort restore of the pre-004 usernames (passwords are not restored).
    op.execute("UPDATE user_accounts SET username='sysadmin' WHERE username='admin' AND role='admin'")
    op.execute("UPDATE user_accounts SET username='admin' WHERE username='manager' AND role='manager'")
    op.execute("UPDATE user_accounts SET username='developer1' WHERE username='user1' AND role='developer'")
