"""Home ('Today') schema (S1)."""

from pydantic import BaseModel

from app.schemas.task import TaskOut


class HomeResponse(BaseModel):
    greeting_name: str
    date_label: str          # e.g. "Tuesday, June 10"
    done_today: int
    total_today: int
    overdue: list[TaskOut] = []
    older_overdue_count: int = 0   # collapsed "N older tasks" (plan G6)
    due_today: list[TaskOut] = []
    anytime: list[TaskOut] = []
    claimable: list[TaskOut] = []
