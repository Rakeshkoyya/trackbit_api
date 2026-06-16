"""Reporting service (S6 board report, S7 org dashboard).

Everything here reads the *net projection* the append-only event writer
maintains (plan §7.1): a task's done/cancelled state is folded from its event
chain, never trusted from a single mutable flag. This makes reopen and cancel
reconcile correctly — a reopened task is not 'done', a cancelled task is
excluded from every metric.

The org rollup (S7) covers PUBLIC boards only (PRD D7): private-board work never
leaks into org-wide numbers, even for admins.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.timeutil import org_day_bounds, org_day_span, org_local_date
from app.models import Board, TaskEvent, TaskInstance
from app.schemas.report import (
    BoardReportResponse,
    BoardSummary,
    HotspotMember,
    HotTask,
    MemberBar,
    OrgDashboardResponse,
    TrendPoint,
)
from app.services import events

_TREND_DAYS = 14
_HOTSPOT_DAYS = 14
_HOTSPOT_MIN_PASSES = 2  # pass_count >= 2 == reassignment hotspot (plan P3-BE-02)


@dataclass
class Folded:
    """Net state of one instance, folded from its append-only event chain."""

    inst: TaskInstance
    done: bool
    completed_at: datetime | None
    completed_by: uuid.UUID | None
    cancelled: bool

    @property
    def due_at(self) -> datetime | None:
        return self.inst.due_at

    def on_time(self) -> bool:
        if not self.done:
            return False
        if self.due_at is None:
            return True  # dateless work is never "late"
        return self.completed_at is not None and self.completed_at <= self.due_at


def _load_chains(db: Session, instances: list[TaskInstance]) -> dict[uuid.UUID, list[TaskEvent]]:
    """Batch-load each instance's append-only event chain, ordered."""
    ids = [i.id for i in instances]
    chains: dict[uuid.UUID, list[TaskEvent]] = {i: [] for i in ids}
    if not ids:
        return chains
    rows = db.scalars(
        select(TaskEvent)
        .where(TaskEvent.instance_id.in_(ids))
        .order_by(TaskEvent.instance_id, TaskEvent.id)
    )
    for e in rows:
        chains[e.instance_id].append(e)
    return chains


def _fold(inst: TaskInstance, evs: list[TaskEvent]) -> Folded:
    done = False
    completed_at: datetime | None = None
    completed_by: uuid.UUID | None = None
    cancelled = False
    for e in evs:
        if e.event_type == "completed":
            done, completed_at, completed_by = True, e.created_at, e.actor_id
        elif e.event_type == "reopened":
            done, completed_at, completed_by = False, None, None
        elif e.event_type == "cancelled":
            cancelled = True
    return Folded(inst, done, completed_at, completed_by, cancelled)


def _pct(num: int, den: int) -> int:
    return round(100 * num / den) if den else 0


class ReportService:
    def __init__(self, db: Session):
        self.db = db

    # ---- shared loading + folding -------------------------------------
    def _load_folded(self, instances: list[TaskInstance]) -> list[Folded]:
        chains = _load_chains(self.db, instances)
        return [_fold(i, chains[i.id]) for i in instances]

    def _window(self, tz: str, range_: str) -> tuple[datetime, datetime]:
        """(start_utc, end_utc) for the requested range, org-local."""
        today_start, today_end, _ = org_day_bounds(tz)
        if range_ == "week":
            return today_start - timedelta(days=6), today_end  # last 7 org-local days
        return today_start, today_end  # today

    def _trend(self, tz: str, folded: list[Folded]) -> list[TrendPoint]:
        """Net completions per org-local day over the last 14 days."""
        _, _, dates = org_day_span(tz, _TREND_DAYS)
        counts = {d: 0 for d in dates}
        for f in folded:
            if f.cancelled or not f.done or f.completed_at is None:
                continue
            d = org_local_date(tz, f.completed_at)
            if d in counts:
                counts[d] += 1
        return [TrendPoint(date=d.isoformat(), done=counts[d]) for d in dates]

    def _aggregate(
        self, folded: list[Folded], start: datetime, end: datetime, names: dict
    ) -> tuple[dict, list[MemberBar]]:
        """Stat totals + per-member bars over tasks due within [start, end)."""
        now = datetime.now(UTC)
        total = done = on_time = overdue = 0
        per_member: dict[uuid.UUID | None, dict] = {}
        for f in folded:
            if f.cancelled or f.due_at is None or not (start <= f.due_at < end):
                continue
            total += 1
            is_done = f.done
            is_ot = f.on_time()
            done += is_done
            on_time += is_ot
            if not is_done and f.due_at < now:
                overdue += 1
            aid = f.inst.assignee_id
            m = per_member.setdefault(aid, {"done": 0, "total": 0, "on_time": 0})
            m["total"] += 1
            m["done"] += is_done
            m["on_time"] += is_ot

        bars = [
            MemberBar(
                user_id=aid,
                name=names.get(aid, "Unassigned"),
                done=v["done"],
                total=v["total"],
                on_time=v["on_time"],
            )
            for aid, v in per_member.items()
            if aid is not None
        ]
        bars.sort(key=lambda b: (-b.total, b.name.lower()))
        stats = {
            "total": total,
            "done": done,
            "completion_pct": _pct(done, total),
            "on_time": on_time,
            "on_time_pct": _pct(on_time, done),
            "overdue": overdue,
        }
        return stats, bars

    # ---- S6 board report ----------------------------------------------
    def board_report(self, board: Board, tz: str, range_: str) -> BoardReportResponse:
        start, end = self._window(tz, range_)
        trend_start, _, _ = org_day_span(tz, _TREND_DAYS)
        # Superset: tasks due in the 14d window OR completed in it (covers scope
        # for both ranges and the trend). Cancelled instances are folded out.
        instances = list(
            self.db.scalars(
                select(TaskInstance).where(
                    TaskInstance.board_id == board.id,
                    (TaskInstance.due_at >= trend_start)
                    | (TaskInstance.completed_at >= trend_start),
                )
            )
        )
        folded = self._load_folded(instances)
        names = events.resolve_user_names(
            self.db, {f.inst.assignee_id for f in folded if f.inst.assignee_id}
        )
        stats, bars = self._aggregate(folded, start, end, names)
        return BoardReportResponse(
            board_id=board.id,
            board_name=board.name,
            range=range_,
            members=bars,
            trend=self._trend(tz, folded),
            **stats,
        )

    # ---- S7 org dashboard (public boards only) ------------------------
    def org_dashboard(self, org_id: uuid.UUID, tz: str, range_: str) -> OrgDashboardResponse:
        start, end = self._window(tz, range_)
        trend_start, _, _ = org_day_span(tz, _TREND_DAYS)
        public_boards = list(
            self.db.scalars(
                select(Board).where(
                    Board.org_id == org_id,
                    Board.visibility == "public",
                    Board.archived_at.is_(None),
                )
            )
        )
        board_by_id = {b.id: b for b in public_boards}
        instances = (
            list(
                self.db.scalars(
                    select(TaskInstance).where(
                        TaskInstance.board_id.in_(list(board_by_id)),
                        (TaskInstance.due_at >= trend_start)
                        | (TaskInstance.completed_at >= trend_start),
                    )
                )
            )
            if board_by_id
            else []
        )
        folded = self._load_folded(instances)
        names = events.resolve_user_names(
            self.db, {f.inst.assignee_id for f in folded if f.inst.assignee_id}
        )
        stats, bars = self._aggregate(folded, start, end, names)

        # Per-board summary (over the range window).
        per_board: dict[uuid.UUID, dict] = {}
        for f in folded:
            if f.cancelled or f.due_at is None or not (start <= f.due_at < end):
                continue
            s = per_board.setdefault(f.inst.board_id, {"done": 0, "total": 0})
            s["total"] += 1
            s["done"] += f.done
        boards = [
            BoardSummary(
                board_id=bid,
                name=board_by_id[bid].name,
                total=v["total"],
                done=v["done"],
                completion_pct=_pct(v["done"], v["total"]),
            )
            for bid, v in per_board.items()
        ]
        boards.sort(key=lambda b: (-b.total, b.name.lower()))

        hm, ht = self._hotspots(tz, list(board_by_id), folded)
        return OrgDashboardResponse(
            range=range_,
            members=bars,
            boards=boards,
            hotspot_members=hm,
            hotspot_tasks=ht,
            orphaned_count=self._orphaned_count(list(board_by_id)),
            **stats,
        )

    def _orphaned_count(self, board_ids: list[uuid.UUID]) -> int:
        """F9 flag: tasks left open + unassigned by a removal or private flip in
        the last 14 days — so orphaned work is visible, never silently gone."""
        if not board_ids:
            return 0
        since = datetime.now(UTC) - timedelta(days=_HOTSPOT_DAYS)
        reasons = ("member_removed", "removed_from_board", "board_went_private")
        inst_ids = set(
            self.db.scalars(
                select(TaskEvent.instance_id)
                .join(TaskInstance, TaskInstance.id == TaskEvent.instance_id)
                .where(
                    TaskInstance.board_id.in_(board_ids),
                    TaskEvent.event_type == "edited",
                    TaskEvent.created_at >= since,
                    TaskEvent.payload["reason"].astext.in_(reasons),
                )
            )
        )
        if not inst_ids:
            return 0
        return self.db.scalar(
            select(func.count()).select_from(TaskInstance).where(
                TaskInstance.id.in_(inst_ids),
                TaskInstance.status == "open",
                TaskInstance.assignee_id.is_(None),
            )
        ) or 0

    def _hotspots(
        self, tz: str, board_ids: list[uuid.UUID], folded: list[Folded]
    ) -> tuple[list[HotspotMember], list[HotTask]]:
        """Reassignment signal (plan P3-BE-02): members ranked by passes-received
        in the last 14 days; tasks bounced >= 2 times."""
        if not board_ids:
            return [], []
        since = datetime.now(UTC) - timedelta(days=_HOTSPOT_DAYS)
        # passes-received: 'passed' events in window, counted by payload.to.
        passed = self.db.scalars(
            select(TaskEvent)
            .join(TaskInstance, TaskInstance.id == TaskEvent.instance_id)
            .where(
                TaskInstance.board_id.in_(board_ids),
                TaskEvent.event_type == "passed",
                TaskEvent.created_at >= since,
            )
        )
        received: dict[uuid.UUID, int] = {}
        for e in passed:
            to = (e.payload or {}).get("to")
            if not to:
                continue
            try:
                uid = uuid.UUID(to)
            except (ValueError, TypeError):
                continue
            received[uid] = received.get(uid, 0) + 1
        names = events.resolve_user_names(self.db, set(received))
        members = [
            HotspotMember(user_id=uid, name=names.get(uid, "—"), passes_received=c)
            for uid, c in received.items()
        ]
        members.sort(key=lambda m: (-m.passes_received, m.name.lower()))

        board_names = {f.inst.board_id for f in folded}
        bnames = {
            b.id: b.name
            for b in self.db.scalars(select(Board).where(Board.id.in_(board_names)))
        }
        hot = [
            HotTask(
                id=f.inst.id,
                title=f.inst.title,
                board_name=bnames.get(f.inst.board_id, ""),
                pass_count=f.inst.pass_count,
            )
            for f in folded
            if not f.cancelled and f.inst.pass_count >= _HOTSPOT_MIN_PASSES
        ]
        hot.sort(key=lambda t: -t.pass_count)
        return members[:10], hot[:10]
