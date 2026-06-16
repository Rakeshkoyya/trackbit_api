"""task category tag (per-task, board-scoped distinct values)

Revision ID: d4e5f6a7b8c9
Revises: c3f1a2b4d5e6
Create Date: 2026-06-13 16:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "c3f1a2b4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("task_templates", sa.Column("category", sa.Text(), nullable=True))
    op.add_column("task_instances", sa.Column("category", sa.Text(), nullable=True))
    # Speeds up the distinct-categories lookup per board.
    op.create_index(
        "ix_task_instances_board_category", "task_instances", ["board_id", "category"]
    )


def downgrade() -> None:
    op.drop_index("ix_task_instances_board_category", table_name="task_instances")
    op.drop_column("task_instances", "category")
    op.drop_column("task_templates", "category")
