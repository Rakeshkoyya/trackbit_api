"""Board service: listing (with today's completion), settings, member management."""

import uuid

from sqlalchemy import and_, func, select
from sqlalchemy import delete as sa_delete
from sqlalchemy.orm import Session

from app.core import plans
from app.core.context import CurrentMember
from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.core.timeutil import org_day_bounds
from app.core.visibility import can_view_board
from app.models import Board, BoardCategory, BoardMember, Membership, TaskInstance
from app.schemas.board import (
    BoardCreateRequest,
    BoardListItem,
    BoardMemberOut,
    BoardOut,
    BoardsListResponse,
    BoardUpdateRequest,
)
from app.services import events, notifications


class BoardService:
    def __init__(self, db: Session):
        self.db = db

    def _can_manage(self, member: CurrentMember, board: Board) -> bool:
        return member.is_admin or board.owner_id == member.user_id

    def _completion(self, member: CurrentMember, board_ids: list[uuid.UUID]) -> dict:
        """Per-board counts: overall (done/total of all non-cancelled instances)
        plus today's slice (due within the org-local day)."""
        if not board_ids:
            return {}
        start, end, _ = org_day_bounds(member.org.timezone)
        in_today = and_(TaskInstance.due_at >= start, TaskInstance.due_at < end)
        rows = self.db.execute(
            select(
                TaskInstance.board_id,
                func.count().label("total"),
                func.count().filter(TaskInstance.status == "done").label("done"),
                func.count().filter(in_today).label("total_today"),
                func.count().filter(TaskInstance.status == "done", in_today).label("done_today"),
            )
            .where(
                TaskInstance.board_id.in_(board_ids),
                TaskInstance.status != "cancelled",
            )
            .group_by(TaskInstance.board_id)
        ).all()
        return {r.board_id: r for r in rows}

    def list_boards(self, member: CurrentMember) -> BoardsListResponse:
        boards = list(
            self.db.scalars(
                select(Board).where(Board.archived_at.is_(None)).order_by(Board.name)
            )
        )
        my_membership = set(
            self.db.scalars(
                select(BoardMember.board_id).where(BoardMember.user_id == member.user_id)
            )
        )

        my, other = [], []
        for b in boards:
            if not can_view_board(self.db, board=b, user_id=member.user_id):
                continue
            mine = b.owner_id == member.user_id or b.id in my_membership
            (my if mine else other).append(b)

        comp = self._completion(member, [b.id for b in my + other])

        def to_item(b: Board) -> BoardListItem:
            r = comp.get(b.id)
            return BoardListItem(
                id=b.id, name=b.name, visibility=b.visibility,
                task_scope=b.task_scope, category=b.category,
                total_today=r.total_today if r else 0, done_today=r.done_today if r else 0,
                total=r.total if r else 0, done=r.done if r else 0,
                is_owner=(b.owner_id == member.user_id),
            )

        return BoardsListResponse(
            my_boards=[to_item(b) for b in my],
            other_public=[to_item(b) for b in other],
        )

    def get(self, member: CurrentMember, board_id: uuid.UUID) -> BoardOut:
        board = self.db.get(Board, board_id)
        if board is None or not can_view_board(self.db, board=board, user_id=member.user_id):
            raise NotFoundError("Board")
        bm = list(self.db.scalars(select(BoardMember.user_id).where(BoardMember.board_id == board_id)))
        names = events.resolve_user_names(self.db, set(bm))
        return BoardOut(
            id=board.id, name=board.name, visibility=board.visibility,
            task_scope=board.task_scope, category=board.category,
            owner_id=board.owner_id, archived=board.archived_at is not None,
            can_manage=self._can_manage(member, board),
            members=[BoardMemberOut(user_id=u, name=names.get(u, "—")) for u in bm],
            member_count=len(bm),
        )

    def create(self, member: CurrentMember, req: BoardCreateRequest) -> BoardOut:
        plans.enforce_board_quota(self.db, member.org)
        board = Board(
            org_id=member.org_id, name=req.name, visibility=req.visibility,
            task_scope=req.task_scope, category=req.category,
            created_by=member.user_id, owner_id=member.user_id,
        )
        self.db.add(board)
        self.db.flush()
        # Creator is a board member (matters when the board is/becomes private).
        self.db.add(BoardMember(board_id=board.id, user_id=member.user_id))
        self.db.flush()

        # Starter content so a new board opens to something, not a blank slate.
        self.db.add(BoardCategory(
            org_id=member.org_id, board_id=board.id, name="Group 1",
            color="#2f8f5b", position=0,
        ))
        for title in ("task1", "task2"):
            inst = TaskInstance(
                org_id=member.org_id, board_id=board.id, title=title,
                category="Group 1", status="open", created_by=member.user_id,
            )
            self.db.add(inst)
            self.db.flush()
            events.append_event(self.db, org_id=member.org_id, instance_id=inst.id,
                                event_type="created", actor_id=member.user_id)
        self.db.flush()
        return self.get(member, board.id)

    def update(self, member: CurrentMember, board_id: uuid.UUID,
               req: BoardUpdateRequest) -> BoardOut:
        board = self.db.get(Board, board_id)
        if board is None or not can_view_board(self.db, board=board, user_id=member.user_id):
            raise NotFoundError("Board")
        if not self._can_manage(member, board):
            raise ForbiddenError("Only the board owner or an admin can change settings.")

        if req.name is not None:
            board.name = req.name
        if req.task_scope is not None:
            board.task_scope = req.task_scope
        if req.category is not None:
            board.category = req.category
        if req.archived is not None:
            board.archived_at = func.now() if req.archived else None
        if req.visibility is not None and req.visibility != board.visibility:
            self._flip_visibility(member, board, req.visibility)
        self.db.flush()
        return self.get(member, board.id)

    def _flip_visibility(self, member: CurrentMember, board: Board, new: str) -> None:
        """F9: public->private unassigns open tasks held by non-members."""
        if new == "private" and board.visibility == "public":
            board.visibility = "private"
            self.db.flush()
            member_ids = set(
                self.db.scalars(
                    select(BoardMember.user_id).where(BoardMember.board_id == board.id)
                )
            )
            orphaned = list(
                self.db.scalars(
                    select(TaskInstance).where(
                        TaskInstance.board_id == board.id,
                        TaskInstance.status == "open",
                        TaskInstance.assignee_id.isnot(None),
                        TaskInstance.assignee_id.notin_(member_ids or {uuid.uuid4()}),
                    )
                )
            )
            for inst in orphaned:
                prev = inst.assignee_id
                inst.assignee_id = None
                events.append_event(self.db, org_id=member.org_id, instance_id=inst.id,
                                    event_type="edited", actor_id=member.user_id,
                                    payload={"assignee_id": [str(prev), None],
                                             "reason": "board_went_private"})
                if prev is not None:  # F9: tell them their task moved off (calmly)
                    notifications.enqueue_unassigned(
                        self.db, org_id=member.org_id, user_id=prev,
                        instance_id=inst.id, reason="board_went_private",
                    )
        elif new == "public":
            board.visibility = "public"

    # ---- private-board membership -------------------------------------
    def add_member(self, member: CurrentMember, board_id: uuid.UUID,
                   user_id: uuid.UUID) -> BoardOut:
        board = self.db.get(Board, board_id)
        if board is None or not can_view_board(self.db, board=board, user_id=member.user_id):
            raise NotFoundError("Board")
        if not self._can_manage(member, board):
            raise ForbiddenError("Only the board owner or an admin can add members.")
        is_org_member = self.db.scalar(
            select(Membership.id).where(
                Membership.org_id == member.org_id, Membership.user_id == user_id,
                Membership.status == "active",
            )
        )
        if not is_org_member:
            raise ValidationError("That person isn't an active member of this org.")
        exists = self.db.scalar(
            select(BoardMember.id).where(
                BoardMember.board_id == board_id, BoardMember.user_id == user_id
            )
        )
        if not exists:
            self.db.add(BoardMember(board_id=board_id, user_id=user_id))
            self.db.flush()
        return self.get(member, board_id)

    def remove_member(self, member: CurrentMember, board_id: uuid.UUID,
                      user_id: uuid.UUID) -> BoardOut:
        board = self.db.get(Board, board_id)
        if board is None or not can_view_board(self.db, board=board, user_id=member.user_id):
            raise NotFoundError("Board")
        if not self._can_manage(member, board):
            raise ForbiddenError("Only the board owner or an admin can remove members.")
        # Unassign their open tasks on this board (F9, private scope) — with an
        # audit event + a calm heads-up, same as a public→private flip.
        orphaned = list(
            self.db.scalars(
                select(TaskInstance).where(
                    TaskInstance.board_id == board_id,
                    TaskInstance.assignee_id == user_id,
                    TaskInstance.status == "open",
                )
            )
        )
        for inst in orphaned:
            inst.assignee_id = None
            events.append_event(self.db, org_id=member.org_id, instance_id=inst.id,
                                event_type="edited", actor_id=member.user_id,
                                payload={"assignee_id": [str(user_id), None],
                                         "reason": "removed_from_board"})
            notifications.enqueue_unassigned(
                self.db, org_id=member.org_id, user_id=user_id,
                instance_id=inst.id, reason="removed_from_board",
            )
        self.db.execute(
            BoardMember.__table__.delete().where(
                (BoardMember.board_id == board_id) & (BoardMember.user_id == user_id)
            )
        )
        self.db.flush()
        return self.get(member, board_id)

    def delete(self, member: CurrentMember, board_id: uuid.UUID) -> None:
        """Hard-delete a board and everything under it. DB-level ON DELETE
        CASCADE removes board_members, task_templates, and task_instances
        (→ task_events, attachments, notifications)."""
        board = self.db.get(Board, board_id)
        if board is None or not can_view_board(self.db, board=board, user_id=member.user_id):
            raise NotFoundError("Board")
        if not self._can_manage(member, board):
            raise ForbiddenError("Only the board owner or an admin can delete this board.")
        self.db.execute(sa_delete(Board).where(Board.id == board_id))
        self.db.flush()
