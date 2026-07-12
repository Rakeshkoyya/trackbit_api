"""Visibility & authorization — the single source of truth (PRD §8.3).

Every board-access decision flows through here; endpoints must not inline these
checks (plan §7, the architectural law).

The deliberate, load-bearing rule: **admins do NOT see private boards they are
not members of.** Admin = billing/people/rollup, not omniscience. The org rollup
excludes private boards anyway (D7), so nothing leaks.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Board, BoardMember, Membership


def is_active_org_member(db: Session, *, org_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    return db.scalar(
        select(Membership.id).where(
            Membership.org_id == org_id,
            Membership.user_id == user_id,
            Membership.status == "active",
        )
    ) is not None


def _is_board_member(db: Session, *, board_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    return db.scalar(
        select(BoardMember.id).where(
            BoardMember.board_id == board_id, BoardMember.user_id == user_id
        )
    ) is not None


def can_view_board(db: Session, *, board: Board, user_id: uuid.UUID) -> bool:
    """public -> any active org member; private -> a board_members row (no admin bypass)."""
    if board.visibility == "public":
        return is_active_org_member(db, org_id=board.org_id, user_id=user_id)
    return _is_board_member(db, board_id=board.id, user_id=user_id)


def assignable_pool(db: Session, *, board: Board) -> set[uuid.UUID]:
    """Who a task on this board may be assigned to (enforced on assign & reassign).

    public  -> all active org members
    private -> the board's members
    """
    if board.visibility == "public":
        rows = db.scalars(
            select(Membership.user_id).where(
                Membership.org_id == board.org_id, Membership.status == "active"
            )
        )
    else:
        rows = db.scalars(
            select(BoardMember.user_id).where(BoardMember.board_id == board.id)
        )
    return set(rows)


def is_assignable(db: Session, *, board: Board, user_id: uuid.UUID) -> bool:
    return user_id in assignable_pool(db, board=board)


def can_view_all_tasks(*, board: Board, user_id: uuid.UUID, is_admin: bool) -> bool:
    """Within a board the user can already open, may they see *every* task?

    On a privacy board (``task_scope == 'assigned'``) only the owner and org
    admins do; regular members are limited to tasks assigned to them. On an
    ``'all'`` board everyone who can view the board sees everything (legacy open
    model). This is the row-level companion to ``can_view_board``.
    """
    if board.task_scope != "assigned":
        return True
    return is_admin or board.owner_id == user_id


def can_view_task(
    *, board: Board, assignee_id: uuid.UUID | None, user_id: uuid.UUID, is_admin: bool
) -> bool:
    """Row-level check: caller must be able to see the board first."""
    if can_view_all_tasks(board=board, user_id=user_id, is_admin=is_admin):
        return True
    return assignee_id is not None and assignee_id == user_id


def board_report_scope(
    db: Session, *, board: Board, user_id: uuid.UUID, is_admin: bool
) -> bool:
    """Board reports are visible to whoever can see the board — except on a
    privacy board, where only the owner/admins get them (regular members see
    only their own tasks, so a board-wide report isn't theirs to view)."""
    if not can_view_board(db, board=board, user_id=user_id):
        return False
    return can_view_all_tasks(board=board, user_id=user_id, is_admin=is_admin)
