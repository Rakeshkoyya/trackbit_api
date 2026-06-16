"""rls: null-safe org_id cast in org_isolation policy

Replaces the org_isolation policies so the ::uuid cast is guarded by NULLIF.
Postgres does not guarantee OR short-circuit evaluation, so the original
expression could evaluate ''::uuid (e.g. on a pooled connection where the GUC
was reset to '') and raise. NULLIF makes the cast input-safe.

Revision ID: a1b2c3d4e5f6
Revises: fcfa2fd7c22c
Create Date: 2026-06-12

"""
from collections.abc import Sequence

from alembic import op

from app.core.rls import create_policies_sql

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "fcfa2fd7c22c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for stmt in create_policies_sql():
        op.execute(stmt)


def downgrade() -> None:
    # No-op: the previous policy form is superseded; recreating policies is safe
    # and downgrading the cast guard would only reintroduce the bug.
    pass
