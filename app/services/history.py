"""Personal history / trophy room (S10, §3.3) — server-computed so the client
stays dumb (plan P3-BE-04).

Dot calendar reads as a mosaic of effort, never a broken chain: each day is
all-clear / partial / nothing-due. The all-clear *run* (§3.2) skips quiet days
(nothing due never breaks it) and is only surfaced by the UI when >= 2.
"""

from datetime import date, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.context import CurrentMember
from app.core.timeutil import org_day_span, org_local_date
from app.models import Board, TaskInstance
from app.schemas.report import (
    CompletedItem,
    DayDot,
    HistoryResponse,
    WeekCount,
)
from app.services.reports import _fold, _load_chains

_WINDOW_DAYS = 70  # ~10 weeks
_MAX_COMPLETIONS = 60  # cap the day-grouped list payload


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())  # Monday


class HistoryService:
    def __init__(self, db: Session):
        self.db = db

    def history(self, member: CurrentMember) -> HistoryResponse:
        tz = member.org.timezone
        uid = member.user_id
        start, end, dates = org_day_span(tz, _WINDOW_DAYS)

        # Tasks that touch this user in the window: due-to-them OR completed-by-them.
        instances = list(
            self.db.scalars(
                select(TaskInstance).where(
                    TaskInstance.org_id == member.org_id,
                    or_(
                        (TaskInstance.assignee_id == uid) & (TaskInstance.due_at >= start),
                        (TaskInstance.completed_by == uid) & (TaskInstance.completed_at >= start),
                    ),
                )
            )
        )
        chains = _load_chains(self.db, instances)
        folded = [_fold(i, chains.get(i.id, [])) for i in instances]

        # ---- dot calendar: tasks due to the user, per org-local day ----
        day_total = {d: 0 for d in dates}
        day_done = {d: 0 for d in dates}
        for f in folded:
            if f.cancelled or f.due_at is None or f.inst.assignee_id != uid:
                continue
            d = org_local_date(tz, f.due_at)
            if d in day_total:
                day_total[d] += 1
                day_done[d] += 1 if f.done else 0

        dots: list[DayDot] = []
        for d in dates:
            tot, dn = day_total[d], day_done[d]
            state = "none" if tot == 0 else ("all" if dn == tot else "partial")
            dots.append(DayDot(date=d.isoformat(), state=state, done=dn, total=tot))

        # current all-clear run: walk back, quiet days don't break it (§3.2).
        run = 0
        for dot in reversed(dots):
            if dot.state == "none":
                continue
            if dot.state == "all":
                run += 1
            else:
                break

        # ---- completions: net-done by this user, in the window --------
        completions = [
            f
            for f in folded
            if not f.cancelled and f.done and f.completed_by == uid and f.completed_at is not None
        ]
        completions.sort(key=lambda f: f.completed_at, reverse=True)

        week_counts: dict[date, int] = {_week_start(d): 0 for d in dates}
        for f in completions:
            ws = _week_start(org_local_date(tz, f.completed_at))
            if ws in week_counts:
                week_counts[ws] += 1
        weekly = [WeekCount(week_start=ws.isoformat(), count=c) for ws, c in sorted(week_counts.items())]
        this_week = week_counts.get(_week_start(dates[-1]), 0)
        personal_best = max(week_counts.values(), default=0)

        board_ids = {f.inst.board_id for f in completions[:_MAX_COMPLETIONS]}
        bnames = {
            b.id: b.name for b in self.db.scalars(select(Board).where(Board.id.in_(board_ids)))
        } if board_ids else {}
        recent = [
            CompletedItem(
                id=f.inst.id,
                title=f.inst.title,
                board_name=bnames.get(f.inst.board_id, ""),
                completed_at=f.completed_at.isoformat(),
            )
            for f in completions[:_MAX_COMPLETIONS]
        ]

        return HistoryResponse(
            dots=dots,
            weekly=weekly,
            this_week_count=this_week,
            personal_best=personal_best,
            current_run=run,
            total_completed=len(completions),
            completions=recent,
        )
