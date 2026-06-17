"""widen auth_tokens.purpose to allow password_reset

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-06-17 10:05:00.000000

"""
from collections.abc import Sequence

from alembic import op


revision: str = "b8c9d0e1f2a3"
down_revision: str | None = "a7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = "purpose IN ('magic_link', 'otp', 'invite', 'refresh')"
_NEW = "purpose IN ('magic_link', 'otp', 'invite', 'refresh', 'password_reset')"


def upgrade() -> None:
    op.drop_constraint("purpose_valid", "auth_tokens", type_="check")
    op.create_check_constraint("purpose_valid", "auth_tokens", _NEW)


def downgrade() -> None:
    op.drop_constraint("purpose_valid", "auth_tokens", type_="check")
    op.create_check_constraint("purpose_valid", "auth_tokens", _OLD)
