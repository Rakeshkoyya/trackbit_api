"""board task_scope (per-board task privacy: 'all' | 'assigned')

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-06-27 10:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "c9d0e1f2a3b4"
down_revision: str | None = "b8c9d0e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "boards",
        sa.Column("task_scope", sa.Text(), nullable=False, server_default="all"),
    )
    op.create_check_constraint(
        "task_scope_valid", "boards", "task_scope IN ('all', 'assigned')"
    )


def downgrade() -> None:
    op.drop_constraint("task_scope_valid", "boards", type_="check")
    op.drop_column("boards", "task_scope")
