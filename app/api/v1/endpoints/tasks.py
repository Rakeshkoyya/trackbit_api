"""Task endpoints."""

import uuid

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.orm import Session

from app.core.context import CurrentMember
from app.core.database import get_db
from app.core.dependencies import get_current_member
from app.schemas.common import MessageResponse
from app.schemas.recurrence import RecurringTemplateOut
from app.schemas.task import (
    AssignRequest,
    AttachmentOut,
    CompleteResponse,
    MakeRecurringRequest,
    NoteCreateRequest,
    ReassignRequest,
    TaskCreateRequest,
    TaskDetailOut,
    TaskOut,
    TaskUpdateRequest,
)
from app.services.attachment import AttachmentService
from app.services.task import TaskService

router = APIRouter()


@router.post("", response_model=TaskDetailOut)
def create_task(
    body: TaskCreateRequest,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> TaskDetailOut:
    return TaskService(db).create(member, body)


@router.get("/{task_id}", response_model=TaskDetailOut)
def get_task(
    task_id: uuid.UUID,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> TaskDetailOut:
    return TaskService(db).detail(member, task_id)


@router.patch("/{task_id}", response_model=TaskDetailOut)
def edit_task(
    task_id: uuid.UUID,
    body: TaskUpdateRequest,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> TaskDetailOut:
    return TaskService(db).edit(member, task_id, body)


@router.post("/{task_id}/complete", response_model=CompleteResponse)
def complete_task(
    task_id: uuid.UUID,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> CompleteResponse:
    return TaskService(db).complete(member, task_id)


@router.post("/{task_id}/reopen", response_model=TaskOut)
def reopen_task(
    task_id: uuid.UUID,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> TaskOut:
    return TaskService(db).reopen(member, task_id)


@router.post("/{task_id}/claim", response_model=TaskOut)
def claim_task(
    task_id: uuid.UUID,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> TaskOut:
    return TaskService(db).claim(member, task_id)


@router.post("/{task_id}/reassign", response_model=TaskOut)
def reassign_task(
    task_id: uuid.UUID,
    body: ReassignRequest,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> TaskOut:
    return TaskService(db).reassign(member, task_id, body.to_user_id)


@router.post("/{task_id}/assign", response_model=TaskOut)
def assign_task(
    task_id: uuid.UUID,
    body: AssignRequest,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> TaskOut:
    return TaskService(db).assign(member, task_id, body.user_id)


@router.post("/{task_id}/make-recurring", response_model=RecurringTemplateOut)
def make_recurring(
    task_id: uuid.UUID,
    body: MakeRecurringRequest,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> RecurringTemplateOut:
    return TaskService(db).make_recurring(member, task_id, body.days, body.time)


@router.post("/{task_id}/cancel", response_model=MessageResponse)
def cancel_task(
    task_id: uuid.UUID,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> MessageResponse:
    TaskService(db).cancel(member, task_id)
    return MessageResponse(message="Task cancelled.")


# ---- attachments (S2, Pro feature) ------------------------------------
@router.get("/{task_id}/attachments", response_model=list[AttachmentOut])
def list_attachments(
    task_id: uuid.UUID,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> list[AttachmentOut]:
    return AttachmentService(db).list_for_task(member, task_id)


@router.post("/{task_id}/notes", response_model=AttachmentOut)
def add_note(
    task_id: uuid.UUID,
    body: NoteCreateRequest,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> AttachmentOut:
    return AttachmentService(db).add_note(member, task_id, body.content)


@router.post("/{task_id}/photos", response_model=AttachmentOut)
def add_photo(
    task_id: uuid.UUID,
    file: UploadFile = File(...),
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> AttachmentOut:
    return AttachmentService(db).add_photo(member, task_id, file)
