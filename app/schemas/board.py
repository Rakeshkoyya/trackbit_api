"""Board schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.task import AssigneeOut


class BoardListItem(BaseModel):
    id: uuid.UUID
    name: str
    visibility: str
    task_scope: str = "all"
    category: str
    done_today: int = 0
    total_today: int = 0
    done: int = 0  # overall completed (all non-cancelled instances)
    total: int = 0  # overall task count
    is_owner: bool = False


class BoardsListResponse(BaseModel):
    my_boards: list[BoardListItem] = []
    other_public: list[BoardListItem] = []


class BoardMemberOut(BaseModel):
    user_id: uuid.UUID
    name: str


class BoardOut(BaseModel):
    id: uuid.UUID
    name: str
    visibility: str
    task_scope: str = "all"
    category: str
    owner_id: uuid.UUID
    archived: bool = False
    can_manage: bool = False
    members: list[BoardMemberOut] = []
    member_count: int = 0


class BoardCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    visibility: str = Field(default="public", pattern="^(public|private)$")
    task_scope: str = Field(default="all", pattern="^(all|assigned)$")
    category: str = Field(default="tasks", pattern="^(tasks|checklist)$")


class BoardUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    visibility: str | None = Field(default=None, pattern="^(public|private)$")
    task_scope: str | None = Field(default=None, pattern="^(all|assigned)$")
    category: str | None = Field(default=None, pattern="^(tasks|checklist)$")
    archived: bool | None = None


class BoardMemberAddRequest(BaseModel):
    user_id: uuid.UUID


# ---- Monday-style board table -----------------------------------------
class BoardRow(BaseModel):
    """A single row in the board table.

    kind="task"      → id is a TaskInstance id; act on it directly.
    kind="recurring" → id is a TaskTemplate id; today's actionable instance (if
                       any) is `today_instance_id`. The detail page is the
                       template's per-day history.
    """

    kind: str  # "task" | "recurring"
    id: uuid.UUID
    title: str
    description: str | None = None
    category: str | None = None
    priority: int = 0
    assignee: AssigneeOut | None = None
    due_at: datetime | None = None
    all_day: bool = False
    status: str  # task: instance status; recurring: today's status or "scheduled"
    recurrence: dict | None = None
    today_instance_id: uuid.UUID | None = None
    occurs_today: bool = True
    pass_count: int = 0
    is_critical: bool = False
    passed_by: str | None = None
    created_at: datetime


class BoardGroup(BaseModel):
    name: str
    color: str


class BoardTableResponse(BaseModel):
    rows: list[BoardRow] = []
    categories: list[str] = []  # distinct tags on this board (for the dropdown)
    groups: list[BoardGroup] = []  # ordered category groups w/ colors (incl. empty)


class CategoryCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    color: str | None = None


class CategoryUpdateRequest(BaseModel):
    name: str  # current name (the identifier)
    new_name: str | None = Field(default=None, min_length=1, max_length=60)
    color: str | None = None


class MyTaskRow(BoardRow):
    """A board-table row carrying its board identity, for the cross-board
    'My tasks' view on Home (grouped by board)."""

    board_id: uuid.UUID
    board_name: str


class MyTasksResponse(BaseModel):
    rows: list[MyTaskRow] = []
