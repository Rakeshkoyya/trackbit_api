"""Report schemas (S6 board report, S7 org dashboard).

All numbers derive from the append-only event projection (plan §7.1): cancelled
tasks are excluded everywhere; net state (after any reopen) is what counts.
"""

import uuid

from pydantic import BaseModel


class TrendPoint(BaseModel):
    date: str  # org-local YYYY-MM-DD
    done: int


class MemberBar(BaseModel):
    user_id: uuid.UUID
    name: str
    done: int
    total: int
    on_time: int = 0


class BoardReportResponse(BaseModel):
    board_id: uuid.UUID
    board_name: str
    range: str  # today | week
    total: int
    done: int
    completion_pct: int  # 0..100, rounded
    on_time: int
    on_time_pct: int  # of done, 0..100
    overdue: int
    members: list[MemberBar] = []
    trend: list[TrendPoint] = []  # last 14 org-local days, oldest -> newest


class BoardSummary(BaseModel):
    board_id: uuid.UUID
    name: str
    total: int
    done: int
    completion_pct: int


class HotspotMember(BaseModel):
    user_id: uuid.UUID
    name: str
    passes_received: int


class HotTask(BaseModel):
    id: uuid.UUID
    title: str
    board_name: str
    pass_count: int


class OrgDashboardResponse(BaseModel):
    range: str  # today | week
    total: int
    done: int
    completion_pct: int
    on_time: int
    on_time_pct: int
    overdue: int
    members: list[MemberBar] = []
    boards: list[BoardSummary] = []
    hotspot_members: list[HotspotMember] = []
    hotspot_tasks: list[HotTask] = []
    orphaned_count: int = 0  # F9: open+unassigned tasks orphaned by removal/private flip


class NudgeResponse(BaseModel):
    sent: bool
    overdue_count: int
    reason: str | None = None  # e.g. "recently_nudged", "nothing_overdue"


# ---- history / trophy room (S10, §3.3) --------------------------------
class DayDot(BaseModel):
    date: str  # org-local YYYY-MM-DD
    state: str  # all | partial | none
    done: int
    total: int


class WeekCount(BaseModel):
    week_start: str  # org-local Monday, YYYY-MM-DD
    count: int


class CompletedItem(BaseModel):
    id: uuid.UUID
    title: str
    board_name: str
    completed_at: str  # ISO UTC


class HistoryResponse(BaseModel):
    dots: list[DayDot] = []  # ~70 days, oldest -> newest
    weekly: list[WeekCount] = []
    this_week_count: int = 0
    personal_best: int = 0  # best week in the window
    current_run: int = 0  # active all-clear run (>=2 for §3.2 line)
    total_completed: int = 0  # completions within the window
    completions: list[CompletedItem] = []  # day-grouped list source, newest first
