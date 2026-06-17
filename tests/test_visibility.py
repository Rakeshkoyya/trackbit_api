"""P0-BE-05 done-when: 8-case visibility matrix + assignable_pool.

Covers admin/member x public/private x member/non-member, including the
load-bearing rule that admins cannot see private boards they're not on.
"""

import uuid

import pytest

from app.core.visibility import assignable_pool, can_view_board
from app.models import Board, BoardMember, Membership, Organization, User
from tests.conftest import AdminSession


@pytest.fixture
def world():
    """Build an isolated org with: admin, member-on-private, member-off-private,
    plus an outside user in another org. Returns ids + board objects."""
    db = AdminSession()
    created_orgs, created_users = [], []
    try:
        org = Organization(name=f"vis-{uuid.uuid4()}")
        db.add(org)
        db.flush()
        created_orgs.append(org.id)

        def mk_user(name):
            u = User(name=name, email=f"vis-{uuid.uuid4().hex[:8]}@example.com")
            db.add(u)
            db.flush()
            created_users.append(u.id)
            return u

        admin = mk_user("admin")
        insider = mk_user("insider")   # member, on the private board
        outsider = mk_user("outsider")  # member, NOT on the private board
        stranger = mk_user("stranger")  # not in this org at all

        for role, u in [("admin", admin), ("member", insider), ("member", outsider)]:
            db.add(Membership(org_id=org.id, user_id=u.id, org_role=role, status="active"))

        # stranger belongs to a different org
        other_org = Organization(name=f"vis-other-{uuid.uuid4()}")
        db.add(other_org)
        db.flush()
        created_orgs.append(other_org.id)
        db.add(Membership(org_id=other_org.id, user_id=stranger.id, org_role="member",
                          status="active"))

        public = Board(org_id=org.id, name="Public", visibility="public",
                       created_by=admin.id, owner_id=admin.id)
        private = Board(org_id=org.id, name="Private", visibility="private",
                        created_by=admin.id, owner_id=admin.id)
        db.add_all([public, private])
        db.flush()

        # Private board members: admin is NOT added; only insider is.
        db.add(BoardMember(board_id=private.id, user_id=insider.id))
        db.commit()

        yield {
            "db": db, "org": org, "public": public, "private": private,
            "admin": admin.id, "insider": insider.id,
            "outsider": outsider.id, "stranger": stranger.id,
        }
    finally:
        from sqlalchemy import delete
        for oid in created_orgs:
            db.execute(delete(Organization).where(Organization.id == oid))
        for uid in created_users:
            db.execute(delete(User).where(User.id == uid))
        db.commit()
        db.close()


def test_public_board_visible_to_all_org_members(world):
    db, pub = world["db"], world["public"]
    assert can_view_board(db, board=pub, user_id=world["admin"]) is True
    assert can_view_board(db, board=pub, user_id=world["insider"]) is True
    assert can_view_board(db, board=pub, user_id=world["outsider"]) is True


def test_public_board_hidden_from_non_org_user(world):
    db, pub = world["db"], world["public"]
    assert can_view_board(db, board=pub, user_id=world["stranger"]) is False


def test_private_board_visible_only_to_its_members(world):
    db, priv = world["db"], world["private"]
    assert can_view_board(db, board=priv, user_id=world["insider"]) is True
    assert can_view_board(db, board=priv, user_id=world["outsider"]) is False
    assert can_view_board(db, board=priv, user_id=world["stranger"]) is False


def test_admin_cannot_see_private_board_they_are_not_on(world):
    # The load-bearing rule: admin is NOT a member of the private board.
    db, priv = world["db"], world["private"]
    assert can_view_board(db, board=priv, user_id=world["admin"]) is False


def test_assignable_pool_public_is_all_active_members(world):
    db, pub = world["db"], world["public"]
    pool = assignable_pool(db, board=pub)
    assert pool == {world["admin"], world["insider"], world["outsider"]}
    assert world["stranger"] not in pool


def test_assignable_pool_private_is_board_members_only(world):
    db, priv = world["db"], world["private"]
    assert assignable_pool(db, board=priv) == {world["insider"]}
