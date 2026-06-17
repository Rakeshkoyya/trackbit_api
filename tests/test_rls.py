"""RLS safety-net test (P0-BE-02 done-when): cross-org reads are denied.

When `app.current_org_id` is set on the connection, org-scoped tables must only
expose rows for that org — even though app-layer query scoping is the primary
guard. Requires the migrated dev database to be reachable.
"""

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from app.core.database import SessionLocal
from app.models import Board, Organization, TaskInstance, User


def _make_org(db, label: str) -> tuple[Organization, TaskInstance]:
    org = Organization(name=f"rls-{label}-{uuid.uuid4()}")
    db.add(org)
    db.flush()
    owner = User(name="owner", email=f"owner-{uuid.uuid4().hex[:8]}@example.com")
    db.add(owner)
    db.flush()
    board = Board(org_id=org.id, name="B", created_by=owner.id, owner_id=owner.id)
    db.add(board)
    db.flush()
    inst = TaskInstance(org_id=org.id, board_id=board.id, title="t", created_by=owner.id)
    db.add(inst)
    db.flush()
    return org, inst


def test_rls_denies_cross_org_reads():
    db = SessionLocal()
    try:
        try:
            org_a, task_a = _make_org(db, "A")
            _, task_b = _make_org(db, "B")
            db.flush()
        except OperationalError as exc:  # pragma: no cover
            pytest.skip(f"dev database not reachable: {exc}")

        # Scope this transaction to org A (transaction-local GUC).
        db.execute(
            text("SELECT set_config('app.current_org_id', :oid, true)"),
            {"oid": str(org_a.id)},
        )

        visible = set(db.execute(select(TaskInstance.id)).scalars().all())
        assert task_a.id in visible, "own-org rows must remain visible"
        assert task_b.id not in visible, "RLS must hide other orgs' rows"
    finally:
        db.rollback()  # never persist test fixtures
        db.close()
