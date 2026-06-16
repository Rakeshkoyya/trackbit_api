"""Task schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class AssigneeOut(BaseModel):
    id: uuid.UUID
    name: str


class TaskOut(BaseModel):
    id: uuid.UUID
    board_id: uuid.UUID
    board_name: str
    title: str
    description: str | None = None
    category: str | None = None
    priority: int = 0
    assignee: AssigneeOut | None = None
    due_at: datetime | None = None
    all_day: bool = False
    status: str
    pass_count: int = 0
    is_critical: bool = False
    passed_by: str | None = None  # name of who last passed it (home transparency)
    created_at: datetime


class TaskEventOut(BaseModel):
    id: int
    type: str
    actor_name: str | None = None
    at: datetime
    text: str  # human-rendered ("Passed to Priya")


class TaskDetailOut(TaskOut):
    events: list[TaskEventOut] = []
    assignable: list[AssigneeOut] = []  # pool for the reassign picker
    can_cancel: bool = False


class TaskCreateRequest(BaseModel):
    board_id: uuid.UUID
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    category: str | None = Field(default=None, max_length=60)
    priority: int = Field(default=0, ge=0, le=3)
    assignee_id: uuid.UUID | None = None  # None = leave unassigned (claimable)
    due_at: datetime | None = None
    all_day: bool = False
    is_critical: bool = False


class TaskUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    category: str | None = Field(default=None, max_length=60)
    due_at: datetime | None = None
    all_day: bool | None = None
    is_critical: bool | None = None
    priority: int | None = Field(default=None, ge=0, le=3)
    board_id: uuid.UUID | None = None  # set to move the task to another board


class AssignRequest(BaseModel):
    user_id: uuid.UUID | None = None  # None = unassign


class MakeRecurringRequest(BaseModel):
    days: list[str] = Field(min_length=1)  # weekday codes: mon..sun
    time: str | None = None  # "HH:MM"; None = all-day


class ReassignRequest(BaseModel):
    to_user_id: uuid.UUID


class CompleteResponse(BaseModel):
    status: str
    already_done: bool = False
    completed_by_name: str | None = None


# ---- attachments (S2, P4-BE-02) ---------------------------------------
class NoteCreateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=2000)


class AttachmentOut(BaseModel):
    id: uuid.UUID
    kind: str  # note | photo
    content: str | None = None  # note text
    file_url: str | None = None  # photo URL
    uploaded_by_name: str
    created_at: datetime
