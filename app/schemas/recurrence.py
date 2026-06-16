"""Recurring-template schemas."""

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

from app.schemas.task import AssigneeOut


class RecurringTemplateCreate(BaseModel):
    board_id: uuid.UUID
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    category: str | None = Field(default=None, max_length=60)
    priority: int = Field(default=0, ge=0, le=3)
    recurrence: dict  # validated by app.core.recurrence.validate_rule
    default_assignee_id: uuid.UUID | None = None
    is_critical: bool = False
    remind_before_minutes: int | None = None


class RecurringTemplateUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    category: str | None = Field(default=None, max_length=60)
    priority: int | None = Field(default=None, ge=0, le=3)
    recurrence: dict | None = None
    default_assignee_id: uuid.UUID | None = None
    is_critical: bool | None = None
    remind_before_minutes: int | None = None
    board_id: uuid.UUID | None = None  # set to move the recurring task to another board


class RecurringTemplateOut(BaseModel):
    id: uuid.UUID
    board_id: uuid.UUID
    board_name: str
    title: str
    description: str | None = None
    category: str | None = None
    priority: int = 0
    recurrence: dict
    default_assignee: AssigneeOut | None = None
    active: bool
    is_critical: bool
    next_occurrences: list[date] = []
    created_at: datetime


class RecurringDay(BaseModel):
    date: date
    status: str  # done | missed | open | scheduled
    instance_id: uuid.UUID | None = None
    completed_by_name: str | None = None
    due_at: datetime | None = None


class RecurringHistoryOut(BaseModel):
    template: RecurringTemplateOut
    days: list[RecurringDay] = []  # past + today, newest first
    upcoming: list[date] = []      # next few scheduled dates
