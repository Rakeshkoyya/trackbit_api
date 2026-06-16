"""activation_funnel view (plan G7)

Per-org activation derived from the event log (the reporting backbone). The
activation signal per F1: a task assigned to *someone other than the actor*,
which later gets completed.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-06-12

"""
from collections.abc import Sequence

from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VIEW = """
CREATE VIEW activation_funnel AS
WITH assign_to_other AS (
    SELECT e.org_id,
           e.instance_id,
           MIN(e.created_at) AS assigned_at
    FROM task_events e
    WHERE e.event_type IN ('assigned', 'passed')
      AND e.payload ? 'to'
      AND (e.actor_id IS NULL OR (e.payload ->> 'to') <> e.actor_id::text)
    GROUP BY e.org_id, e.instance_id
),
completed AS (
    SELECT e.instance_id,
           MIN(e.created_at) AS completed_at
    FROM task_events e
    WHERE e.event_type = 'completed'
    GROUP BY e.instance_id
),
activations AS (
    SELECT a.org_id,
           a.instance_id,
           a.assigned_at,
           c.completed_at
    FROM assign_to_other a
    LEFT JOIN completed c ON c.instance_id = a.instance_id
)
SELECT o.id                                        AS org_id,
       o.name                                      AS org_name,
       o.created_at                                AS org_created_at,
       MIN(act.assigned_at)                        AS first_assign_to_other_at,
       MIN(act.completed_at)                       AS first_activation_at,
       COALESCE(bool_or(act.completed_at IS NOT NULL), false) AS activated
FROM organizations o
LEFT JOIN activations act ON act.org_id = o.id
GROUP BY o.id, o.name, o.created_at;
"""


def upgrade() -> None:
    op.execute(_VIEW)
    # The app role reads the funnel for the admin dashboard / ops queries.
    # Guarded so a fresh environment without the role still migrates.
    op.execute(
        "DO $$ BEGIN "
        "IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='trackbit_app') THEN "
        "GRANT SELECT ON activation_funnel TO trackbit_app; "
        "END IF; END $$;"
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS activation_funnel")
