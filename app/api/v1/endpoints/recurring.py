"""Recurring task template endpoints."""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.context import CurrentMember
from app.core.database import get_db
from app.core.dependencies import get_current_member
from app.schemas.common import MessageResponse
from app.schemas.recurrence import (
    RecurringHistoryOut,
    RecurringTemplateCreate,
    RecurringTemplateOut,
    RecurringTemplateUpdate,
)
from app.services.recurrence import RecurringService

router = APIRouter()


@router.get("", response_model=list[RecurringTemplateOut])
def list_templates(
    board_id: uuid.UUID,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> list[RecurringTemplateOut]:
    return RecurringService(db).list_for_board(member, board_id)


@router.post("", response_model=RecurringTemplateOut)
def create_template(
    body: RecurringTemplateCreate,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> RecurringTemplateOut:
    return RecurringService(db).create(member, body)


@router.get("/{template_id}/history", response_model=RecurringHistoryOut)
def template_history(
    template_id: uuid.UUID,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> RecurringHistoryOut:
    return RecurringService(db).history(member, template_id)


@router.patch("/{template_id}", response_model=RecurringTemplateOut)
def update_template(
    template_id: uuid.UUID,
    body: RecurringTemplateUpdate,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> RecurringTemplateOut:
    return RecurringService(db).update(member, template_id, body)


@router.post("/{template_id}/toggle", response_model=RecurringTemplateOut)
def toggle_template(
    template_id: uuid.UUID,
    active: bool,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> RecurringTemplateOut:
    return RecurringService(db).set_active(member, template_id, active)


@router.delete("/{template_id}", response_model=MessageResponse)
def delete_template(
    template_id: uuid.UUID,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> MessageResponse:
    RecurringService(db).delete(member, template_id)
    return MessageResponse(message="Recurring task removed.")
