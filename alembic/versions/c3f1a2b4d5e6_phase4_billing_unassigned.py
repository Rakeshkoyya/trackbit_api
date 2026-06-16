"""phase 4: org billing columns, invoices, unassigned notif type

Revision ID: c3f1a2b4d5e6
Revises: 68d07710c0bf
Create Date: 2026-06-13 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "c3f1a2b4d5e6"
down_revision: str | None = "68d07710c0bf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_NOTIF = ("assigned", "passed", "reminder", "overdue", "digest", "report_card", "nudge")
_NEW_NOTIF = (*_OLD_NOTIF, "unassigned")


def _notif_check(values) -> str:
    inner = ", ".join(f"'{v}'" for v in values)
    return f"notif_type IN ({inner})"


def upgrade() -> None:
    # Organizations: subscription lifecycle columns.
    op.add_column("organizations", sa.Column("plan_status", sa.Text(), server_default="none", nullable=False))
    op.add_column("organizations", sa.Column("plan_renews_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("organizations", sa.Column("grace_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("organizations", sa.Column("razorpay_customer_id", sa.Text(), nullable=True))
    op.add_column("organizations", sa.Column("razorpay_subscription_id", sa.Text(), nullable=True))
    op.create_check_constraint(
        "plan_status_valid", "organizations", "plan_status IN ('none', 'active', 'grace')"
    )

    # Invoices (local mirror of Razorpay charges).
    op.create_table(
        "invoices",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("org_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_id", sa.Text(), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.Text(), server_default="INR", nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_id"),
    )
    op.create_index("ix_invoices_org_created", "invoices", ["org_id", sa.text("created_at DESC")])

    # Extend the notifications notif_type check to include 'unassigned' (F9).
    op.drop_constraint("notif_type_valid", "notifications", type_="check")
    op.create_check_constraint("notif_type_valid", "notifications", _notif_check(_NEW_NOTIF))


def downgrade() -> None:
    op.drop_constraint("notif_type_valid", "notifications", type_="check")
    op.create_check_constraint("notif_type_valid", "notifications", _notif_check(_OLD_NOTIF))

    op.drop_index("ix_invoices_org_created", table_name="invoices")
    op.drop_table("invoices")

    op.drop_constraint("plan_status_valid", "organizations", type_="check")
    for col in (
        "razorpay_subscription_id",
        "razorpay_customer_id",
        "grace_until",
        "plan_renews_at",
        "plan_status",
    ):
        op.drop_column("organizations", col)
