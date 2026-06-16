"""Ops endpoints: manual job triggers + metrics (admin-only).

Lets you test recurrence/reminders on demand without waiting for the scheduler.
"""

from fastapi import APIRouter, Depends, HTTPException

from app.core.context import CurrentMember
from app.core.dependencies import require_admin
from app.services import jobs

router = APIRouter()

_JOBS = {
    "materializer": jobs.run_materializer,
    "miss": jobs.run_miss_marker,
    "sweep": jobs.run_sweep,
    "digest": jobs.run_digest,
    "report_card": jobs.run_report_card,
    "nudge": jobs.run_nudge_scan,
    "grace": jobs.run_grace_downgrade,
}


@router.post("/run/{job}")
def run_job(job: str, _: CurrentMember = Depends(require_admin)) -> dict:
    fn = _JOBS.get(job)
    if fn is None:
        raise HTTPException(status_code=404, detail=f"Unknown job: {job}")
    return {"job": job, "result": fn()}


@router.get("/metrics")
def metrics(_: CurrentMember = Depends(require_admin)) -> dict:
    return {"failed_notifications": jobs.failed_count()}
