"""Recurring templates + the materializer (the engine that spawns instances).

Templates are the ONLY recurring definition (PRD §4.2). Instances are
materialized in advance (idempotently, keyed on template_id + occurrence_date)
so 'missed' is a real, reportable state — never lazily.
"""

import uuid
from datetime import date, timedelta

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import plans
from app.core.context import CurrentMember
from app.core.exceptions import ForbiddenError, NotFoundError
from app.core.recurrence import due_time, next_occurrences, occurs_on, validate_rule
from app.core.timeutil import org_day_bounds, org_due_at
from app.core.visibility import can_view_all_tasks, can_view_board, is_assignable
from app.models import Board, Organization, TaskInstance, TaskTemplate
from app.schemas.recurrence import (
    RecurringDay,
    RecurringHistoryOut,
    RecurringTemplateCreate,
    RecurringTemplateOut,
    RecurringTemplateUpdate,
)
from app.schemas.task import AssigneeOut
from app.services import events, notifications


class RecurringService:
    def __init__(self, db: Session):
        self.db = db

    # ---- authorization / loading --------------------------------------
    def _board(self, board_id: uuid.UUID) -> Board:
        b = self.db.get(Board, board_id)
        if b is None:
            raise NotFoundError("Board")
        return b

    def _require_viewable(self, member: CurrentMember, board: Board) -> None:
        if not can_view_board(self.db, board=board, user_id=member.user_id):
            raise NotFoundError("Board")

    def _sees_all(self, member: CurrentMember, board: Board) -> bool:
        """False only for a regular member on a privacy board — they're limited to
        recurring tasks that default to them (mirrors TaskService)."""
        return can_view_all_tasks(
            board=board, user_id=member.user_id, is_admin=member.is_admin
        )

    def _get(self, template_id: uuid.UUID) -> TaskTemplate:
        t = self.db.get(TaskTemplate, template_id)
        if t is None:
            raise NotFoundError("Recurring task")
        return t

    # ---- serialization -------------------------------------------------
    def _serialize(self, t: TaskTemplate) -> RecurringTemplateOut:
        board = self.db.get(Board, t.board_id)
        assignee = None
        if t.default_assignee_id:
            names = events.resolve_user_names(self.db, {t.default_assignee_id})
            assignee = AssigneeOut(id=t.default_assignee_id,
                                   name=names.get(t.default_assignee_id, "—"))
        upcoming = next_occurrences(t.recurrence_rule, after=date.today(), count=3) if t.active else []
        return RecurringTemplateOut(
            id=t.id, board_id=t.board_id, board_name=board.name if board else "",
            title=t.title, description=t.description, category=t.category,
            priority=t.priority,
            recurrence=t.recurrence_rule, default_assignee=assignee, active=t.active,
            is_critical=t.is_critical, next_occurrences=upcoming, created_at=t.created_at,
        )

    # ---- CRUD ----------------------------------------------------------
    def list_for_board(self, member: CurrentMember, board_id: uuid.UUID) -> list[RecurringTemplateOut]:
        board = self._board(board_id)
        self._require_viewable(member, board)
        stmt = select(TaskTemplate).where(TaskTemplate.board_id == board_id)
        # Privacy board: a member only sees recurring tasks that default to them.
        if not self._sees_all(member, board):
            stmt = stmt.where(TaskTemplate.default_assignee_id == member.user_id)
        rows = self.db.scalars(stmt.order_by(TaskTemplate.title))
        return [self._serialize(t) for t in rows]

    def history(self, member: CurrentMember, template_id: uuid.UUID) -> RecurringHistoryOut:
        """Per-day record of a recurring task: past occurrences with done/missed
        state, plus the next few scheduled dates."""
        t = self._get(template_id)
        board = self._board(t.board_id)
        self._require_viewable(member, board)
        # Privacy board: members may only open a recurring task that's theirs.
        if not self._sees_all(member, board) and t.default_assignee_id != member.user_id:
            raise NotFoundError("Recurring task")
        _, _, now_local = org_day_bounds(member.org.timezone)
        today = now_local.date()
        insts = list(
            self.db.scalars(
                select(TaskInstance)
                .where(
                    TaskInstance.template_id == t.id,
                    TaskInstance.occurrence_date.isnot(None),
                    TaskInstance.occurrence_date <= today,
                )
                .order_by(TaskInstance.occurrence_date.desc())
                .limit(90)
            )
        )
        cnames = events.resolve_user_names(
            self.db, {i.completed_by for i in insts if i.completed_by}
        )
        days = [
            RecurringDay(
                date=i.occurrence_date,
                status=i.status,
                instance_id=i.id,
                completed_by_name=cnames.get(i.completed_by) if i.completed_by else None,
                due_at=i.due_at,
            )
            for i in insts
        ]
        upcoming = (
            next_occurrences(t.recurrence_rule, after=today, count=5) if t.active else []
        )
        return RecurringHistoryOut(template=self._serialize(t), days=days, upcoming=upcoming)

    def create(self, member: CurrentMember, req: RecurringTemplateCreate) -> RecurringTemplateOut:
        board = self._board(req.board_id)
        self._require_viewable(member, board)
        rule = validate_rule(req.recurrence)
        if req.is_critical:
            plans.enforce_critical_allowed(member.org)

        # Privacy board: a regular member can only schedule recurring work for
        # themselves (same rule as one-time task creation).
        default_assignee_id = req.default_assignee_id
        if not self._sees_all(member, board):
            if default_assignee_id is not None and default_assignee_id != member.user_id:
                raise ForbiddenError(
                    "On this board you can only assign tasks to yourself.",
                    code="self_assign_only",
                )
            default_assignee_id = member.user_id

        if default_assignee_id is not None and not is_assignable(
            self.db, board=board, user_id=default_assignee_id
        ):
            raise ForbiddenError("That person can't be assigned on this board.",
                                 code="not_assignable")

        t = TaskTemplate(
            org_id=member.org_id, board_id=board.id, title=req.title,
            description=req.description, category=req.category, priority=req.priority,
            recurrence_rule=rule,
            default_assignee_id=default_assignee_id, active=True,
            is_critical=req.is_critical, remind_before_minutes=req.remind_before_minutes,
            created_by=member.user_id,
        )
        self.db.add(t)
        self.db.flush()

        # Materialize today + tomorrow immediately so it appears right away.
        org = self.db.get(Organization, member.org_id)
        self._materialize_template(t, org, _today_and_tomorrow(org))
        return self._serialize(t)

    def update(self, member: CurrentMember, template_id: uuid.UUID,
               req: RecurringTemplateUpdate) -> RecurringTemplateOut:
        t = self._get(template_id)
        board = self._board(t.board_id)
        self._require_viewable(member, board)
        if not self._sees_all(member, board):
            raise ForbiddenError(
                "Only the board owner can change recurring tasks here.",
                code="recurring_restricted",
            )
        data = req.model_dump(exclude_unset=True)
        if data.get("board_id") and data["board_id"] != t.board_id:
            target = self._board(data["board_id"])
            self._require_viewable(member, target)
            t.board_id = data["board_id"]
        if "recurrence" in data and data["recurrence"] is not None:
            t.recurrence_rule = validate_rule(data["recurrence"])
        for field in ("title", "description", "category", "priority", "default_assignee_id",
                      "is_critical", "remind_before_minutes"):
            if field in data:
                setattr(t, field, data[field])
        self.db.flush()
        # Edits apply to FUTURE instances only — existing ones are untouched.
        return self._serialize(t)

    def set_active(self, member: CurrentMember, template_id: uuid.UUID,
                   active: bool) -> RecurringTemplateOut:
        t = self._get(template_id)
        board = self._board(t.board_id)
        self._require_viewable(member, board)
        if not self._sees_all(member, board):
            raise ForbiddenError(
                "Only the board owner can change recurring tasks here.",
                code="recurring_restricted",
            )
        t.active = active
        self.db.flush()
        return self._serialize(t)

    def delete(self, member: CurrentMember, template_id: uuid.UUID) -> None:
        t = self._get(template_id)
        board = self._board(t.board_id)
        self._require_viewable(member, board)
        if not self._sees_all(member, board):
            raise ForbiddenError(
                "Only the board owner can change recurring tasks here.",
                code="recurring_restricted",
            )
        # Hard delete: existing instances keep their history (template_id -> NULL
        # via FK), future instances stop (PRD F9).
        self.db.execute(sa_delete(TaskTemplate).where(TaskTemplate.id == template_id))
        self.db.flush()

    # ---- materialization (used by create + the nightly job) -----------
    def _materialize_template(self, t: TaskTemplate, org: Organization,
                              dates: list[date]) -> int:
        if not t.active:
            return 0
        created = 0
        for d in dates:
            if not occurs_on(t.recurrence_rule, d):
                continue
            # Idempotent: skip if this occurrence already exists.
            exists = self.db.scalar(
                select(TaskInstance.id).where(
                    TaskInstance.template_id == t.id, TaskInstance.occurrence_date == d
                )
            )
            if exists:
                continue
            due_at, all_day = org_due_at(org.timezone, d, due_time(t.recurrence_rule))
            remind = t.remind_before_minutes if t.remind_before_minutes is not None else 30
            inst = TaskInstance(
                org_id=org.id, board_id=t.board_id, template_id=t.id, occurrence_date=d,
                title=t.title, description=t.description, category=t.category,
                priority=t.priority,
                assignee_id=t.default_assignee_id, due_at=due_at, all_day=all_day,
                status="open", is_critical=t.is_critical, remind_before_minutes=remind,
                created_by=t.created_by,
            )
            self.db.add(inst)
            self.db.flush()
            events.append_event(self.db, org_id=org.id, instance_id=inst.id,
                                event_type="created", actor_id=None)
            if t.default_assignee_id:
                events.append_event(self.db, org_id=org.id, instance_id=inst.id,
                                    event_type="assigned", actor_id=None,
                                    payload={"to": str(t.default_assignee_id)})
            notifications.enqueue_reminder(self.db, inst)
            created += 1
        return created

    def materialize_org(self, org: Organization, dates: list[date]) -> int:
        templates = self.db.scalars(
            select(TaskTemplate).where(
                TaskTemplate.org_id == org.id, TaskTemplate.active.is_(True)
            )
        )
        return sum(self._materialize_template(t, org, dates) for t in templates)


def _today_and_tomorrow(org: Organization) -> list[date]:
    _, _, now_local = org_day_bounds(org.timezone)
    today = now_local.date()
    return [today, today + timedelta(days=1)]
