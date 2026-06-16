"""APScheduler setup. Runs on exactly one instance (ENABLE_SCHEDULER)."""

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import settings
from app.services import jobs

logger = logging.getLogger("trackbit.scheduler")

_scheduler: BackgroundScheduler | None = None


def start_scheduler() -> None:
    global _scheduler
    if not settings.ENABLE_SCHEDULER:
        logger.info("Scheduler disabled (ENABLE_SCHEDULER=false)")
        return
    if _scheduler is not None:
        return
    _scheduler = BackgroundScheduler(
        timezone="UTC",
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600},
    )
    # Hourly tick at :05 — materialize, miss-mark, digest, nudge (jobs are TZ-aware).
    _scheduler.add_job(jobs.run_hourly, CronTrigger(minute=5), id="hourly", replace_existing=True)
    # Notification sweep every 2 minutes.
    _scheduler.add_job(jobs.run_sweep, IntervalTrigger(minutes=2), id="sweep", replace_existing=True)
    _scheduler.start()
    logger.info("Scheduler started (hourly tick + 2-min sweep)")


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
