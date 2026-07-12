"""Board endpoints."""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.context import CurrentMember
from app.core.database import get_db
from app.core.dependencies import get_current_member
from app.core.exceptions import NotFoundError
from app.core.visibility import board_report_scope
from app.models import Board
from app.schemas.board import (
    BoardCreateRequest,
    BoardMemberAddRequest,
    BoardOut,
    BoardsListResponse,
    BoardTableResponse,
    BoardUpdateRequest,
    CategoryCreateRequest,
    CategoryUpdateRequest,
)
from app.schemas.common import MessageResponse
from app.schemas.report import BoardReportResponse
from app.schemas.task import TaskOut
from app.services.board import BoardService
from app.services.reports import ReportService
from app.services.task import TaskService

router = APIRouter()


@router.get("", response_model=BoardsListResponse)
def list_boards(
    member: CurrentMember = Depends(get_current_member), db: Session = Depends(get_db)
) -> BoardsListResponse:
    return BoardService(db).list_boards(member)


@router.post("", response_model=BoardOut)
def create_board(
    body: BoardCreateRequest,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> BoardOut:
    return BoardService(db).create(member, body)


@router.get("/{board_id}", response_model=BoardOut)
def get_board(
    board_id: uuid.UUID,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> BoardOut:
    return BoardService(db).get(member, board_id)


@router.patch("/{board_id}", response_model=BoardOut)
def update_board(
    board_id: uuid.UUID,
    body: BoardUpdateRequest,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> BoardOut:
    return BoardService(db).update(member, board_id, body)


@router.delete("/{board_id}", response_model=MessageResponse)
def delete_board(
    board_id: uuid.UUID,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> MessageResponse:
    BoardService(db).delete(member, board_id)
    return MessageResponse(message="Board deleted.")


@router.post("/{board_id}/members", response_model=BoardOut)
def add_board_member(
    board_id: uuid.UUID,
    body: BoardMemberAddRequest,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> BoardOut:
    return BoardService(db).add_member(member, board_id, body.user_id)


@router.delete("/{board_id}/members/{user_id}", response_model=BoardOut)
def remove_board_member(
    board_id: uuid.UUID,
    user_id: uuid.UUID,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> BoardOut:
    return BoardService(db).remove_member(member, board_id, user_id)


@router.get("/{board_id}/report", response_model=BoardReportResponse)
def board_report(
    board_id: uuid.UUID,
    range: str = Query("today", pattern="^(today|week)$"),
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> BoardReportResponse:
    board = db.get(Board, board_id)
    # Visible to whoever can see the board; don't leak existence of private ones.
    # On a privacy board, only the owner/admins — regular members get no report.
    if board is None or not board_report_scope(
        db, board=board, user_id=member.user_id, is_admin=member.is_admin
    ):
        raise NotFoundError("Board")
    return ReportService(db).board_report(board, member.org.timezone, range)


@router.get("/{board_id}/tasks", response_model=list[TaskOut])
def board_tasks(
    board_id: uuid.UUID,
    include_done: bool = True,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> list[TaskOut]:
    return TaskService(db).list_board_tasks(member, board_id, include_done=include_done)


@router.get("/{board_id}/table", response_model=BoardTableResponse)
def board_table(
    board_id: uuid.UUID,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> BoardTableResponse:
    return TaskService(db).board_table(member, board_id)


@router.get("/{board_id}/categories", response_model=list[str])
def board_categories(
    board_id: uuid.UUID,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> list[str]:
    return TaskService(db).board_categories(member, board_id)


@router.post("/{board_id}/categories", response_model=MessageResponse)
def create_category(
    board_id: uuid.UUID,
    body: CategoryCreateRequest,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> MessageResponse:
    TaskService(db).create_category(member, board_id, body.name, body.color)
    return MessageResponse(message="Group added.")


@router.patch("/{board_id}/categories", response_model=MessageResponse)
def update_category(
    board_id: uuid.UUID,
    body: CategoryUpdateRequest,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> MessageResponse:
    TaskService(db).update_category(member, board_id, body.name, body.new_name, body.color)
    return MessageResponse(message="Group updated.")


@router.delete("/{board_id}/categories", response_model=MessageResponse)
def delete_category(
    board_id: uuid.UUID,
    name: str = Query(..., min_length=1),
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> MessageResponse:
    TaskService(db).delete_category(member, board_id, name)
    return MessageResponse(message="Group removed.")
