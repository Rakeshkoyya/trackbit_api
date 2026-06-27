"""Task service — all task operations flow through the event writer (plan §7.1)."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core import plans
from app.core.context import CurrentMember
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.core.recurrence import occurs_on
from app.core.timeutil import org_day_bounds
from app.core.visibility import (
    assignable_pool,
    can_view_all_tasks,
    can_view_board,
    can_view_task,
    is_assignable,
)
from app.models import Board, BoardCategory, TaskEvent, TaskInstance, TaskTemplate
from app.schemas.board import BoardGroup, BoardRow, BoardTableResponse
from app.schemas.task import (
    AssigneeOut,
    CompleteResponse,
    TaskCreateRequest,
    TaskDetailOut,
    TaskEventOut,
    TaskOut,
    TaskUpdateRequest,
)
from app.services import analytics, events, notifications


def _now() -> datetime:
    return datetime.now(UTC)


# Hidden from default Home/board views; visible only under explicit filters.
_HIDDEN_STATUSES = ("cancelled",)

# Fallback palette for category groups that exist only on tasks (not yet saved
# as a BoardCategory with a picked color). Mirrors the web `groupColor` palette.
_GROUP_PALETTE = (
    "#2f8f5b", "#185fa5", "#7f77dd", "#b5791f",
    "#d4537e", "#1d9e75", "#d85a30", "#534ab7",
)


def _auto_color(name: str) -> str:
    h = 0
    for ch in name:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return _GROUP_PALETTE[h % len(_GROUP_PALETTE)]


class TaskService:
    def __init__(self, db: Session):
        self.db = db

    # ---- loading / authorization --------------------------------------
    def _get_board(self, board_id: uuid.UUID) -> Board:
        board = self.db.get(Board, board_id)
        if board is None:
            raise NotFoundError("Board", str(board_id))
        return board

    def _get_instance(self, instance_id: uuid.UUID) -> TaskInstance:
        inst = self.db.get(TaskInstance, instance_id)
        if inst is None:
            raise NotFoundError("Task", str(instance_id))
        return inst

    def _require_viewable(self, member: CurrentMember, board: Board) -> None:
        if not can_view_board(self.db, board=board, user_id=member.user_id):
            # Don't reveal existence of private boards the user can't see.
            raise NotFoundError("Board")

    def _sees_all_tasks(self, member: CurrentMember, board: Board) -> bool:
        """False only for a regular member on a privacy board — they're limited
        to their own tasks (see core/visibility.py)."""
        return can_view_all_tasks(
            board=board, user_id=member.user_id, is_admin=member.is_admin
        )

    # ---- serialization -------------------------------------------------
    def _serialize_many(self, member: CurrentMember, instances: list[TaskInstance]) -> list[TaskOut]:
        if not instances:
            return []
        board_ids = {i.board_id for i in instances}
        boards = {
            b.id: b
            for b in self.db.scalars(select(Board).where(Board.id.in_(board_ids)))
        }
        assignee_ids = {i.assignee_id for i in instances if i.assignee_id}
        names = events.resolve_user_names(self.db, assignee_ids)

        # passed_by: actor name of the latest 'passed' event, for passed tasks.
        passed_by: dict[uuid.UUID, str] = {}
        passed_ids = [i.id for i in instances if i.pass_count > 0]
        if passed_ids:
            rows = self.db.execute(
                select(TaskEvent.instance_id, TaskEvent.actor_id)
                .where(TaskEvent.instance_id.in_(passed_ids), TaskEvent.event_type == "passed")
                .order_by(TaskEvent.instance_id, TaskEvent.id.desc())
            ).all()
            seen: set[uuid.UUID] = set()
            actor_ids = set()
            latest: dict[uuid.UUID, uuid.UUID] = {}
            for inst_id, actor_id in rows:
                if inst_id not in seen:
                    seen.add(inst_id)
                    if actor_id:
                        latest[inst_id] = actor_id
                        actor_ids.add(actor_id)
            actor_names = events.resolve_user_names(self.db, actor_ids)
            passed_by = {iid: actor_names.get(aid, "someone") for iid, aid in latest.items()}

        out = []
        for i in instances:
            board = boards.get(i.board_id)
            out.append(
                TaskOut(
                    id=i.id,
                    board_id=i.board_id,
                    board_name=board.name if board else "",
                    title=i.title,
                    description=i.description,
                    category=i.category,
                    priority=i.priority,
                    assignee=(
                        AssigneeOut(id=i.assignee_id, name=names.get(i.assignee_id, "—"))
                        if i.assignee_id
                        else None
                    ),
                    due_at=i.due_at,
                    all_day=i.all_day,
                    status=i.status,
                    pass_count=i.pass_count,
                    is_critical=i.is_critical,
                    passed_by=passed_by.get(i.id),
                    created_at=i.created_at,
                )
            )
        return out

    def _serialize_one(self, member: CurrentMember, inst: TaskInstance) -> TaskOut:
        return self._serialize_many(member, [inst])[0]

    def _render_chain(self, inst: TaskInstance) -> list[TaskEventOut]:
        evs = list(
            self.db.scalars(
                select(TaskEvent).where(TaskEvent.instance_id == inst.id).order_by(TaskEvent.id)
            )
        )
        # Collect every user id referenced by actor or payload.
        uids: set[uuid.UUID] = set()
        for e in evs:
            if e.actor_id:
                uids.add(e.actor_id)
            for key in ("to", "from"):
                val = (e.payload or {}).get(key)
                if val:
                    try:
                        uids.add(uuid.UUID(val))
                    except (ValueError, TypeError):
                        pass
        names = events.resolve_user_names(self.db, uids)

        def name_of(val):
            try:
                return names.get(uuid.UUID(val), "someone")
            except (ValueError, TypeError):
                return "someone"

        rendered = []
        for e in evs:
            actor = names.get(e.actor_id) if e.actor_id else None
            p = e.payload or {}
            to_name = name_of(p.get("to")) if p.get("to") else "someone"
            text = {
                "created": f"Created by {actor or 'someone'}",
                "assigned": f"Assigned to {to_name}",
                "claimed": f"Claimed by {actor or 'someone'}",
                "passed": f"Passed to {to_name}",
                "completed": f"Completed by {actor or 'someone'}",
                "reopened": f"Reopened by {actor or 'someone'}",
                "missed": "Missed",
                "edited": f"Edited by {actor or 'someone'}",
                "cancelled": f"Cancelled by {actor or 'someone'}",
                "commented": f"Note by {actor or 'someone'}",
                "attached": f"Attachment by {actor or 'someone'}",
            }.get(e.event_type, e.event_type)
            rendered.append(
                TaskEventOut(id=e.id, type=e.event_type, actor_name=actor, at=e.created_at, text=text)
            )
        return rendered

    def detail(self, member: CurrentMember, instance_id: uuid.UUID) -> TaskDetailOut:
        inst = self._get_instance(instance_id)
        board = self._get_board(inst.board_id)
        self._require_viewable(member, board)
        # Privacy board: a member may only open their own task (don't leak others').
        if not can_view_task(
            board=board, assignee_id=inst.assignee_id,
            user_id=member.user_id, is_admin=member.is_admin,
        ):
            raise NotFoundError("Task")
        base = self._serialize_one(member, inst)
        pool_ids = assignable_pool(self.db, board=board)
        pool_names = events.resolve_user_names(self.db, pool_ids)
        assignable = [AssigneeOut(id=uid, name=pool_names.get(uid, "—")) for uid in pool_ids]
        assignable.sort(key=lambda a: a.name.lower())
        return TaskDetailOut(
            **base.model_dump(),
            events=self._render_chain(inst),
            assignable=assignable,
            can_cancel=(member.is_admin or inst.created_by == member.user_id),
        )

    # ---- queries -------------------------------------------------------
    def list_board_tasks(self, member: CurrentMember, board_id: uuid.UUID,
                         include_done: bool = True) -> list[TaskOut]:
        board = self._get_board(board_id)
        self._require_viewable(member, board)
        stmt = select(TaskInstance).where(
            TaskInstance.board_id == board_id,
            TaskInstance.status.notin_(_HIDDEN_STATUSES),
        )
        if not include_done:
            stmt = stmt.where(TaskInstance.status != "done")
        if not self._sees_all_tasks(member, board):
            stmt = stmt.where(TaskInstance.assignee_id == member.user_id)
        stmt = stmt.order_by(TaskInstance.status, TaskInstance.due_at.nulls_last(),
                             TaskInstance.created_at)
        return self._serialize_many(member, list(self.db.scalars(stmt)))

    def board_categories(self, member: CurrentMember, board_id: uuid.UUID) -> list[str]:
        """Distinct, non-empty category tags used on a board (for the dropdown)."""
        board = self._get_board(board_id)
        self._require_viewable(member, board)
        inst_cats = self.db.scalars(
            select(TaskInstance.category)
            .where(TaskInstance.board_id == board_id, TaskInstance.category.isnot(None))
            .distinct()
        )
        tmpl_cats = self.db.scalars(
            select(TaskTemplate.category)
            .where(TaskTemplate.board_id == board_id, TaskTemplate.category.isnot(None))
            .distinct()
        )
        seen = {c.strip() for c in list(inst_cats) + list(tmpl_cats) if c and c.strip()}
        return sorted(seen, key=str.lower)

    def board_table(self, member: CurrentMember, board_id: uuid.UUID) -> BoardTableResponse:
        """Monday-style rows: one-time instances + one row per active recurring
        template (folding the per-day occurrences into a single logical task)."""
        board = self._get_board(board_id)
        self._require_viewable(member, board)
        _, _, now_local = org_day_bounds(member.org.timezone)
        today = now_local.date()
        # Privacy board: a regular member only sees rows that are theirs — their
        # one-time tasks and recurring templates defaulting to them.
        sees_all = self._sees_all_tasks(member, board)

        one_time_q = select(TaskInstance).where(
            TaskInstance.board_id == board_id,
            TaskInstance.template_id.is_(None),
            TaskInstance.status.notin_(_HIDDEN_STATUSES),
        )
        templates_q = select(TaskTemplate).where(
            TaskTemplate.board_id == board_id, TaskTemplate.active.is_(True)
        )
        if not sees_all:
            one_time_q = one_time_q.where(TaskInstance.assignee_id == member.user_id)
            templates_q = templates_q.where(
                TaskTemplate.default_assignee_id == member.user_id
            )
        one_time = list(self.db.scalars(one_time_q.order_by(TaskInstance.created_at)))
        templates = list(self.db.scalars(templates_q.order_by(TaskTemplate.title)))
        # Today's materialized instance per template (for the check/claim action).
        today_by_tmpl: dict[uuid.UUID, TaskInstance] = {}
        if templates:
            for inst in self.db.scalars(
                select(TaskInstance).where(
                    TaskInstance.template_id.in_([t.id for t in templates]),
                    TaskInstance.occurrence_date == today,
                )
            ):
                today_by_tmpl[inst.template_id] = inst

        # Resolve every assignee name in one pass.
        uids: set[uuid.UUID] = {i.assignee_id for i in one_time if i.assignee_id}
        uids |= {t.default_assignee_id for t in templates if t.default_assignee_id}
        uids |= {i.assignee_id for i in today_by_tmpl.values() if i.assignee_id}
        names = events.resolve_user_names(self.db, uids)

        def assignee_of(uid: uuid.UUID | None) -> AssigneeOut | None:
            return AssigneeOut(id=uid, name=names.get(uid, "—")) if uid else None

        rows: list[BoardRow] = []
        for i in one_time:
            rows.append(
                BoardRow(
                    kind="task", id=i.id, title=i.title, description=i.description,
                    category=i.category, priority=i.priority, assignee=assignee_of(i.assignee_id),
                    due_at=i.due_at, all_day=i.all_day, status=i.status,
                    pass_count=i.pass_count, is_critical=i.is_critical,
                    created_at=i.created_at,
                )
            )
        for t in templates:
            inst = today_by_tmpl.get(t.id)
            occurs = occurs_on(t.recurrence_rule, today)
            if inst is not None:
                status = inst.status
                assignee = assignee_of(inst.assignee_id)
                due_at, all_day = inst.due_at, inst.all_day
                today_instance_id = inst.id
            else:
                status = "open" if occurs else "scheduled"
                assignee = assignee_of(t.default_assignee_id)
                due_at, all_day = None, False
                today_instance_id = None
            rows.append(
                BoardRow(
                    kind="recurring", id=t.id, title=t.title, description=t.description,
                    category=t.category, priority=t.priority, assignee=assignee,
                    due_at=due_at, all_day=all_day,
                    status=status, recurrence=t.recurrence_rule,
                    today_instance_id=today_instance_id, occurs_today=occurs,
                    is_critical=t.is_critical, created_at=t.created_at,
                )
            )
        # Category groups: saved (color + order, incl. empty) ∪ any still on tasks.
        saved = list(
            self.db.scalars(
                select(BoardCategory)
                .where(BoardCategory.board_id == board_id)
                .order_by(BoardCategory.position, BoardCategory.name)
            )
        )
        groups = [BoardGroup(name=c.name, color=c.color) for c in saved]
        saved_names = {c.name for c in saved}
        extra = {r.category for r in rows if r.category and r.category not in saved_names}
        for name in sorted(extra, key=str.lower):
            groups.append(BoardGroup(name=name, color=_auto_color(name)))

        return BoardTableResponse(
            rows=rows, categories=self.board_categories(member, board_id), groups=groups
        )

    # ---- category groups (Monday-style, first-class) ------------------
    def _next_category_pos(self, board_id: uuid.UUID) -> int:
        cur = self.db.scalar(
            select(func.coalesce(func.max(BoardCategory.position), -1)).where(
                BoardCategory.board_id == board_id
            )
        )
        return int(cur) + 1

    def create_category(self, member: CurrentMember, board_id: uuid.UUID,
                        name: str, color: str | None = None) -> None:
        board = self._get_board(board_id)
        self._require_viewable(member, board)
        name = name.strip()
        existing = self.db.scalar(
            select(BoardCategory).where(
                BoardCategory.board_id == board_id, BoardCategory.name == name
            )
        )
        if existing is not None:
            if color:
                existing.color = color
                self.db.flush()
            return
        self.db.add(BoardCategory(
            org_id=member.org_id, board_id=board_id, name=name,
            color=color or _auto_color(name), position=self._next_category_pos(board_id),
        ))
        self.db.flush()

    def update_category(self, member: CurrentMember, board_id: uuid.UUID, name: str,
                        new_name: str | None = None, color: str | None = None) -> None:
        board = self._get_board(board_id)
        self._require_viewable(member, board)
        name = name.strip()
        row = self.db.scalar(
            select(BoardCategory).where(
                BoardCategory.board_id == board_id, BoardCategory.name == name
            )
        )
        # A task-derived category may not be saved yet — materialize it first.
        if row is None:
            row = BoardCategory(
                org_id=member.org_id, board_id=board_id, name=name,
                color=_auto_color(name), position=self._next_category_pos(board_id),
            )
            self.db.add(row)
            self.db.flush()
        if color:
            row.color = color
        new = (new_name or "").strip()
        if new and new != name:
            other = self.db.scalar(
                select(BoardCategory).where(
                    BoardCategory.board_id == board_id, BoardCategory.name == new
                )
            )
            if other is not None and other.id != row.id:
                self.db.delete(row)  # merge into the existing target group
            else:
                row.name = new
            for model in (TaskInstance, TaskTemplate):
                self.db.execute(
                    update(model).where(model.board_id == board_id, model.category == name)
                    .values(category=new)
                )
        self.db.flush()

    def delete_category(self, member: CurrentMember, board_id: uuid.UUID, name: str) -> None:
        board = self._get_board(board_id)
        self._require_viewable(member, board)
        name = name.strip()
        self.db.execute(
            sa_delete(BoardCategory).where(
                BoardCategory.board_id == board_id, BoardCategory.name == name
            )
        )
        for model in (TaskInstance, TaskTemplate):
            self.db.execute(
                update(model).where(model.board_id == board_id, model.category == name)
                .values(category=None)
            )
        self.db.flush()

    # ---- mutations -----------------------------------------------------
    def create(self, member: CurrentMember, req: TaskCreateRequest) -> TaskDetailOut:
        board = self._get_board(req.board_id)
        self._require_viewable(member, board)  # open model: any viewer can add

        if req.is_critical:
            plans.enforce_critical_allowed(member.org)

        # Privacy board: a regular member can only put a task on themselves
        # (they can't see anyone else's work, so they can't hand it off either).
        assignee_id = req.assignee_id
        if not self._sees_all_tasks(member, board):
            if assignee_id is not None and assignee_id != member.user_id:
                raise ForbiddenError(
                    "On this board you can only assign tasks to yourself.",
                    code="self_assign_only",
                )
            assignee_id = member.user_id

        if assignee_id is not None and not is_assignable(
            self.db, board=board, user_id=assignee_id
        ):
            raise ValidationError("That person can't be assigned on this board.",
                                  code="not_assignable")

        inst = TaskInstance(
            org_id=member.org_id,
            board_id=board.id,
            title=req.title,
            description=req.description,
            category=req.category,
            priority=req.priority,
            assignee_id=assignee_id,
            due_at=req.due_at,
            all_day=req.all_day,
            is_critical=req.is_critical,
            status="open",
            created_by=member.user_id,
        )
        self.db.add(inst)
        self.db.flush()

        events.append_event(self.db, org_id=member.org_id, instance_id=inst.id,
                            event_type="created", actor_id=member.user_id)
        analytics.track(self.db, event=analytics.TASK_CREATED, org_id=member.org_id,
                        user_id=member.user_id, props={"board_id": str(board.id)})
        if assignee_id is not None:
            events.append_event(self.db, org_id=member.org_id, instance_id=inst.id,
                                event_type="assigned", actor_id=member.user_id,
                                payload={"to": str(assignee_id)})
            analytics.track(self.db, event=analytics.TASK_ASSIGNED, org_id=member.org_id,
                            user_id=member.user_id)
            if assignee_id != member.user_id:
                notifications.enqueue_instant(
                    self.db, org_id=member.org_id, user_id=assignee_id,
                    instance_id=inst.id, notif_type="assigned", actor_name=member.user.name,
                )
        notifications.enqueue_reminder(self.db, inst)
        return self.detail(member, inst.id)

    def make_recurring(self, member: CurrentMember, instance_id: uuid.UUID,
                       days: list[str], time: str | None):
        """Convert a one-time task into a weekly recurring template (reuses the
        engine), then cancel the original instance so it isn't duplicated."""
        from app.schemas.recurrence import RecurringTemplateCreate
        from app.services.recurrence import RecurringService

        inst = self._get_instance(instance_id)
        board = self._get_board(inst.board_id)
        self._require_viewable(member, board)
        if inst.template_id is not None:
            raise ConflictError("This task is already recurring.", code="already_recurring")
        if inst.status in ("done", "cancelled"):
            raise ConflictError("This task is closed.", code="task_closed")

        rule: dict = {"freq": "weekly", "days": days}
        if time:
            rule["time"] = time
        out = RecurringService(self.db).create(
            member,
            RecurringTemplateCreate(
                board_id=board.id, title=inst.title, description=inst.description,
                category=inst.category, priority=inst.priority, recurrence=rule,
                default_assignee_id=inst.assignee_id, is_critical=inst.is_critical,
            ),
        )
        # Retire the one-time instance (audit trail kept).
        inst.status = "cancelled"
        self.db.flush()
        events.append_event(self.db, org_id=member.org_id, instance_id=inst.id,
                            event_type="cancelled", actor_id=member.user_id,
                            payload={"reason": "converted_to_recurring"})
        return out

    def claim(self, member: CurrentMember, instance_id: uuid.UUID) -> TaskOut:
        inst = self._get_instance(instance_id)
        board = self._get_board(inst.board_id)
        self._require_viewable(member, board)
        # Privacy board: no open claim pool — tasks are assigned directly.
        if board.task_scope == "assigned":
            raise ForbiddenError(
                "Claiming is off for this board — tasks are assigned directly.",
                code="claim_disabled",
            )
        if not is_assignable(self.db, board=board, user_id=member.user_id):
            raise ForbiddenError("You can't claim tasks on this board.", code="not_assignable")

        # Atomic: only the first claimer wins.
        res = self.db.execute(
            update(TaskInstance)
            .where(TaskInstance.id == instance_id, TaskInstance.assignee_id.is_(None),
                   TaskInstance.status == "open")
            .values(assignee_id=member.user_id)
        )
        if res.rowcount == 0:
            self.db.refresh(inst)
            holder = (
                events.resolve_user_names(self.db, {inst.assignee_id}).get(inst.assignee_id)
                if inst.assignee_id else None
            )
            raise ConflictError(
                f"Already taken{f' by {holder}' if holder else ''}.", code="already_claimed"
            )
        self.db.refresh(inst)
        events.append_event(self.db, org_id=member.org_id, instance_id=inst.id,
                            event_type="claimed", actor_id=member.user_id)
        analytics.track(self.db, event=analytics.TASK_CLAIMED, org_id=member.org_id,
                        user_id=member.user_id)
        return self._serialize_one(member, inst)

    def reassign(self, member: CurrentMember, instance_id: uuid.UUID,
                 to_user_id: uuid.UUID) -> TaskOut:
        inst = self._get_instance(instance_id)
        board = self._get_board(inst.board_id)
        self._require_viewable(member, board)
        if not self._sees_all_tasks(member, board):
            raise ForbiddenError(
                "Only the board owner can change who a task is assigned to here.",
                code="assign_restricted",
            )
        if not is_assignable(self.db, board=board, user_id=to_user_id):
            raise ValidationError("That person can't be assigned on this board.",
                                  code="not_assignable")
        if inst.status in ("done", "cancelled"):
            raise ConflictError("This task is closed.", code="task_closed")

        from_id = inst.assignee_id
        inst.assignee_id = to_user_id
        inst.pass_count = inst.pass_count + 1
        self.db.flush()
        events.append_event(self.db, org_id=member.org_id, instance_id=inst.id,
                            event_type="passed", actor_id=member.user_id,
                            payload={"from": str(from_id) if from_id else None,
                                     "to": str(to_user_id)})
        analytics.track(self.db, event=analytics.TASK_PASSED, org_id=member.org_id,
                        user_id=member.user_id)
        if to_user_id != member.user_id:
            notifications.enqueue_instant(
                self.db, org_id=member.org_id, user_id=to_user_id,
                instance_id=inst.id, notif_type="passed", actor_name=member.user.name,
            )
        notifications.reset_reminder(self.db, inst)  # reminder follows the new assignee
        return self._serialize_one(member, inst)

    def assign(self, member: CurrentMember, instance_id: uuid.UUID,
               to_user_id: uuid.UUID | None) -> TaskOut:
        """Set a task's assignee from the person picker. Unassigned → assign
        (no pass); already-owned → pass (keeps the ↩ accountability count);
        None → unassign."""
        inst = self._get_instance(instance_id)
        board = self._get_board(inst.board_id)
        self._require_viewable(member, board)
        if not self._sees_all_tasks(member, board):
            raise ForbiddenError(
                "Only the board owner can change who a task is assigned to here.",
                code="assign_restricted",
            )
        if inst.status in ("done", "cancelled"):
            raise ConflictError("This task is closed.", code="task_closed")
        from_id = inst.assignee_id

        if to_user_id is None:
            if from_id is None:
                return self._serialize_one(member, inst)
            inst.assignee_id = None
            self.db.flush()
            events.append_event(self.db, org_id=member.org_id, instance_id=inst.id,
                                event_type="edited", actor_id=member.user_id,
                                payload={"assignee_id": [str(from_id), None]})
            return self._serialize_one(member, inst)

        if not is_assignable(self.db, board=board, user_id=to_user_id):
            raise ValidationError("That person can't be assigned on this board.",
                                  code="not_assignable")
        if from_id == to_user_id:
            return self._serialize_one(member, inst)

        inst.assignee_id = to_user_id
        if from_id is not None:
            inst.pass_count = inst.pass_count + 1
            self.db.flush()
            events.append_event(self.db, org_id=member.org_id, instance_id=inst.id,
                                event_type="passed", actor_id=member.user_id,
                                payload={"from": str(from_id), "to": str(to_user_id)})
            analytics.track(self.db, event=analytics.TASK_PASSED, org_id=member.org_id,
                            user_id=member.user_id)
            notif_type = "passed"
        else:
            self.db.flush()
            events.append_event(self.db, org_id=member.org_id, instance_id=inst.id,
                                event_type="assigned", actor_id=member.user_id,
                                payload={"to": str(to_user_id)})
            analytics.track(self.db, event=analytics.TASK_ASSIGNED, org_id=member.org_id,
                            user_id=member.user_id)
            notif_type = "assigned"
        if to_user_id != member.user_id:
            notifications.enqueue_instant(
                self.db, org_id=member.org_id, user_id=to_user_id,
                instance_id=inst.id, notif_type=notif_type, actor_name=member.user.name,
            )
        notifications.reset_reminder(self.db, inst)
        return self._serialize_one(member, inst)

    def complete(self, member: CurrentMember, instance_id: uuid.UUID) -> CompleteResponse:
        inst = self._get_instance(instance_id)
        board = self._get_board(inst.board_id)
        self._require_viewable(member, board)

        res = self.db.execute(
            update(TaskInstance)
            .where(TaskInstance.id == instance_id, TaskInstance.status.in_(("open", "missed")))
            .values(status="done", completed_at=_now(), completed_by=member.user_id)
        )
        if res.rowcount == 0:
            self.db.refresh(inst)
            if inst.status == "done":
                who = (
                    events.resolve_user_names(self.db, {inst.completed_by}).get(inst.completed_by)
                    if inst.completed_by else None
                )
                return CompleteResponse(status="done", already_done=True, completed_by_name=who)
            raise ConflictError("This task can't be completed.", code="not_completable")
        self.db.refresh(inst)
        events.append_event(self.db, org_id=member.org_id, instance_id=inst.id,
                            event_type="completed", actor_id=member.user_id)
        analytics.track(self.db, event=analytics.TASK_COMPLETED, org_id=member.org_id,
                        user_id=member.user_id)
        return CompleteResponse(status="done", already_done=False)

    def reopen(self, member: CurrentMember, instance_id: uuid.UUID) -> TaskOut:
        inst = self._get_instance(instance_id)
        board = self._get_board(inst.board_id)
        self._require_viewable(member, board)
        if inst.status != "done":
            raise ConflictError("Only completed tasks can be reopened.", code="not_reopenable")
        inst.status = "open"
        inst.completed_at = None
        inst.completed_by = None
        self.db.flush()
        events.append_event(self.db, org_id=member.org_id, instance_id=inst.id,
                            event_type="reopened", actor_id=member.user_id)
        return self._serialize_one(member, inst)

    def edit(self, member: CurrentMember, instance_id: uuid.UUID,
             req: TaskUpdateRequest) -> TaskDetailOut:
        inst = self._get_instance(instance_id)
        board = self._get_board(inst.board_id)
        self._require_viewable(member, board)  # open model: any viewer can edit

        # Move to another board (validated separately from the field diff).
        if ("board_id" in req.model_fields_set and req.board_id is not None
                and req.board_id != inst.board_id):
            target = self._get_board(req.board_id)
            self._require_viewable(member, target)
            old_board = inst.board_id
            inst.board_id = req.board_id
            if inst.assignee_id and not is_assignable(
                self.db, board=target, user_id=inst.assignee_id
            ):
                inst.assignee_id = None
            self.db.flush()
            events.append_event(self.db, org_id=member.org_id, instance_id=inst.id,
                                event_type="edited", actor_id=member.user_id,
                                payload={"board_id": [str(old_board), str(req.board_id)],
                                         "reason": "moved_board"})

        if req.is_critical and "is_critical" in req.model_fields_set:
            plans.enforce_critical_allowed(member.org)
        diff: dict[str, list] = {}
        for field in ("title", "description", "category", "due_at", "all_day", "is_critical",
                      "priority"):
            new = getattr(req, field)
            if new is None and field not in req.model_fields_set:
                continue
            old = getattr(inst, field)
            if old != new:
                diff[field] = [
                    old.isoformat() if isinstance(old, datetime) else old,
                    new.isoformat() if isinstance(new, datetime) else new,
                ]
                setattr(inst, field, new)
        if diff:
            self.db.flush()
            events.append_event(self.db, org_id=member.org_id, instance_id=inst.id,
                                event_type="edited", actor_id=member.user_id, payload=diff)
            if "due_at" in diff:
                notifications.reset_reminder(self.db, inst)
        return self.detail(member, inst.id)

    def cancel(self, member: CurrentMember, instance_id: uuid.UUID) -> None:
        inst = self._get_instance(instance_id)
        board = self._get_board(inst.board_id)
        self._require_viewable(member, board)
        if not (member.is_admin or inst.created_by == member.user_id):
            raise ForbiddenError("Only the creator or an admin can cancel this.",
                                 code="cancel_forbidden")
        if inst.status == "cancelled":
            return
        inst.status = "cancelled"
        self.db.flush()
        events.append_event(self.db, org_id=member.org_id, instance_id=inst.id,
                            event_type="cancelled", actor_id=member.user_id)
