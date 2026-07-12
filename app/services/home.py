"""Home ('Today') service (S1). Buckets computed in org-local time (plan G5/G6)."""

from datetime import timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.core.context import CurrentMember
from app.core.timeutil import org_day_bounds
from app.core.visibility import can_view_board
from app.models import Board, TaskInstance
from app.schemas.board import MyTaskRow, MyTasksResponse
from app.schemas.home import HomeResponse
from app.schemas.task import AssigneeOut
from app.services.task import TaskService

_OVERDUE_WINDOW_DAYS = 7  # older items collapse into a single calm row (G6)


class HomeService:
    def __init__(self, db: Session):
        self.db = db
        self.tasks = TaskService(db)

    def today(self, member: CurrentMember) -> HomeResponse:
        start, end, now_local = org_day_bounds(member.org.timezone)
        cutoff = start - timedelta(days=_OVERDUE_WINDOW_DAYS)

        mine = list(
            self.db.scalars(
                select(TaskInstance).where(
                    TaskInstance.assignee_id == member.user_id,
                    TaskInstance.status.in_(("open", "missed")),
                )
            )
        )

        overdue, older_count, due_today, anytime = [], 0, [], []
        for t in mine:
            # Recurring misses expire quietly — tomorrow brings a fresh instance
            # (PRD O2/G6). They never pile up in Overdue.
            if t.status == "missed" and t.template_id is not None:
                continue
            if t.due_at is None:
                anytime.append(t)
            elif t.due_at < start:
                if t.due_at >= cutoff:
                    overdue.append(t)
                else:
                    older_count += 1
            elif t.due_at < end:
                due_today.append(t)
            else:
                # due later than today; not surfaced on Home yet
                pass

        overdue.sort(key=lambda t: t.due_at)
        due_today.sort(key=lambda t: t.due_at)
        anytime.sort(key=lambda t: t.created_at)

        # Claimable: unassigned open tasks on boards the user can see.
        unassigned = list(
            self.db.scalars(
                select(TaskInstance).where(
                    TaskInstance.assignee_id.is_(None),
                    TaskInstance.status == "open",
                )
            )
        )
        board_cache: dict = {}

        def claimable_board(board_id) -> bool:
            # Privacy boards ('assigned') have no open claim pool — skip them.
            if board_id not in board_cache:
                b = self.db.get(Board, board_id)
                board_cache[board_id] = (
                    b is not None
                    and b.task_scope == "all"
                    and can_view_board(self.db, board=b, user_id=member.user_id)
                )
            return board_cache[board_id]

        claimable = [t for t in unassigned if claimable_board(t.board_id)]
        claimable.sort(key=lambda t: (t.due_at is None, t.due_at or t.created_at))

        # Day progress: tasks I completed today.
        done_count = len(
            list(
                self.db.scalars(
                    select(TaskInstance.id).where(
                        TaskInstance.completed_by == member.user_id,
                        TaskInstance.status == "done",
                        TaskInstance.completed_at >= start,
                        TaskInstance.completed_at < end,
                    )
                )
            )
        )
        remaining = len(overdue) + len(due_today) + len(anytime)
        total = done_count + remaining

        first_name = member.user.name.split()[0] if member.user.name else "there"
        date_label = now_local.strftime("%A, %B ") + str(now_local.day)

        return HomeResponse(
            greeting_name=first_name,
            date_label=date_label,
            done_today=done_count,
            total_today=total,
            overdue=self.tasks._serialize_many(member, overdue),
            older_overdue_count=older_count,
            due_today=self.tasks._serialize_many(member, due_today),
            anytime=self.tasks._serialize_many(member, anytime),
            claimable=self.tasks._serialize_many(member, claimable),
        )

    def my_tasks(self, member: CurrentMember) -> MyTasksResponse:
        """Every task assigned to the caller across all boards, for the Home
        'My tasks' table (grouped by board). Open/missed plus today's completed
        (so done rows show struck-through and feed the day-progress count)."""
        start, end, now_local = org_day_bounds(member.org.timezone)
        today = now_local.date()

        insts = list(
            self.db.scalars(
                select(TaskInstance).where(
                    TaskInstance.assignee_id == member.user_id,
                    or_(
                        TaskInstance.status.in_(("open", "missed")),
                        and_(
                            TaskInstance.status == "done",
                            TaskInstance.completed_at >= start,
                            TaskInstance.completed_at < end,
                        ),
                    ),
                )
            )
        )
        # Recurring tasks: only TODAY's occurrence belongs on "My tasks". The
        # materializer pre-creates tomorrow's instance (which shouldn't show yet),
        # and recurring misses expire quietly (PRD O2/G6).
        insts = [
            t for t in insts
            if t.template_id is None or (t.status != "missed" and t.occurrence_date == today)
        ]

        board_names: dict = {}
        if insts:
            board_names = {
                bid: name
                for bid, name in self.db.execute(
                    select(Board.id, Board.name).where(
                        Board.id.in_({t.board_id for t in insts})
                    )
                ).all()
            }

        me = AssigneeOut(id=member.user_id, name=member.user.name)
        rows = [
            MyTaskRow(
                kind="task", id=t.id, title=t.title, description=t.description,
                category=t.category, priority=t.priority, assignee=me,
                due_at=t.due_at, all_day=t.all_day, status=t.status,
                pass_count=t.pass_count, is_critical=t.is_critical,
                created_at=t.created_at,
                board_id=t.board_id, board_name=board_names.get(t.board_id, ""),
            )
            for t in insts
        ]
        # Not-done first, then done; within, overdue → soonest due → undated.
        rows.sort(key=lambda r: (r.status == "done", r.due_at is None, r.due_at or r.created_at))
        return MyTasksResponse(rows=rows)
