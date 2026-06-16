"""API v1 router aggregation."""

from fastapi import APIRouter

from app.api.v1.endpoints import auth, billing, boards, me, ops, org, push, recurring, tasks

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(org.router, prefix="/org", tags=["org"])
api_router.include_router(billing.router, prefix="/billing", tags=["billing"])
api_router.include_router(boards.router, prefix="/boards", tags=["boards"])
api_router.include_router(tasks.router, prefix="/tasks", tags=["tasks"])
api_router.include_router(recurring.router, prefix="/recurring", tags=["recurring"])
api_router.include_router(me.router, prefix="/me", tags=["me"])
api_router.include_router(push.router, prefix="/push", tags=["push"])
api_router.include_router(ops.router, prefix="/ops", tags=["ops"])
