"""Personal endpoints (Home / Today)."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.context import CurrentMember
from app.core.database import get_db
from app.core.dependencies import get_current_member
from app.schemas.board import MyTasksResponse
from app.schemas.home import HomeResponse
from app.schemas.report import HistoryResponse
from app.services.history import HistoryService
from app.services.home import HomeService

router = APIRouter()


@router.get("/today", response_model=HomeResponse)
def today(
    member: CurrentMember = Depends(get_current_member), db: Session = Depends(get_db)
) -> HomeResponse:
    return HomeService(db).today(member)


@router.get("/tasks", response_model=MyTasksResponse)
def my_tasks(
    member: CurrentMember = Depends(get_current_member), db: Session = Depends(get_db)
) -> MyTasksResponse:
    return HomeService(db).my_tasks(member)


@router.get("/history", response_model=HistoryResponse)
def history(
    member: CurrentMember = Depends(get_current_member), db: Session = Depends(get_db)
) -> HistoryResponse:
    return HistoryService(db).history(member)
