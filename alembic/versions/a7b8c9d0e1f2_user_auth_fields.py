"""user auth fields: username + must_set_password

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-06-17 10:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import CITEXT


revision: str = "a7b8c9d0e1f2"
down_revision: str | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("username", CITEXT(), nullable=True))
    op.add_column(
        "users",
        sa.Column("must_set_password", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_unique_constraint("uq_users_username", "users", ["username"])
    # A user must have at least one identifier. Phone is included because the old
    # invite flow created phone-only staff (no email/username) that still exist.
    op.create_check_constraint(
        "users_contact_present",
        "users",
        "email IS NOT NULL OR username IS NOT NULL OR phone IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint("users_contact_present", "users", type_="check")
    op.drop_constraint("uq_users_username", "users", type_="unique")
    op.drop_column("users", "must_set_password")
    op.drop_column("users", "username")
