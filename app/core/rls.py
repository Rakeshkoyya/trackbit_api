"""Row-Level Security policies — the org-isolation safety net (plan §4, B-law).

App-layer query scoping (org_id from auth context) is the PRIMARY guard. RLS is
defence in depth: if a query ever forgets its org filter, the database still
refuses to leak across tenants — but ONLY once the connection has opted in by
setting `app.current_org_id`.

Policy semantics per table with an org_id column:
    - GUC unset/empty  -> full access (today's default; app scoping is the guard)
    - GUC set          -> rows are visible only when org_id matches

This lets us roll RLS out without breaking unscoped/admin paths, and engage it
per-request once auth resolves org context (wired in P0-BE-03).

`FORCE ROW LEVEL SECURITY` makes the policy apply to the table owner too (the
migration role), so the safety net is real and testable.
"""

# Tables that carry a non-null org_id and represent tenant business data.
ORG_SCOPED_TABLES = (
    "memberships",
    "boards",
    "task_templates",
    "task_instances",
    "task_events",
    "notifications",
)

# NULLIF guards the ::uuid cast: Postgres does NOT guarantee OR short-circuit
# evaluation, so the cast can run even when an earlier branch is true. NULLIF
# turns '' into NULL, and NULL::uuid is valid (NULL) — so the cast never errors,
# whatever order the planner picks. When the GUC is unset/empty the row passes
# via the first two branches; app-layer scoping remains the primary guard.
_USING = (
    "current_setting('app.current_org_id', true) IS NULL "
    "OR current_setting('app.current_org_id', true) = '' "
    "OR org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid"
)


def create_policies_sql() -> list[str]:
    """(Re)create the org_isolation policy on each table (idempotent via DROP IF EXISTS)."""
    stmts: list[str] = []
    for table in ORG_SCOPED_TABLES:
        stmts.append(f"DROP POLICY IF EXISTS org_isolation ON {table};")
        stmts.append(
            f"CREATE POLICY org_isolation ON {table} "
            f"USING ({_USING}) WITH CHECK ({_USING});"
        )
    return stmts


def upgrade_sql() -> list[str]:
    stmts: list[str] = []
    for table in ORG_SCOPED_TABLES:
        stmts.append(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        stmts.append(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
    stmts.extend(create_policies_sql())
    return stmts


def downgrade_sql() -> list[str]:
    stmts: list[str] = []
    for table in ORG_SCOPED_TABLES:
        stmts.append(f"DROP POLICY IF EXISTS org_isolation ON {table};")
        stmts.append(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;")
        stmts.append(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")
    return stmts
