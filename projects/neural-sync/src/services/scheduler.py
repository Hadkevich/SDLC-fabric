"""Optional background re-optimization loop (Task04 §4 — continuous re-optimization;
closes the §10 "static allocation" failure condition).

Opt-in via the NEURAL_SYNC_REOPT_INTERVAL env var (seconds; 0 or unset = disabled, the
default). When enabled it periodically refreshes the cached risk scores so allocation
health doesn't drift between manual admin triggers. APScheduler is imported lazily, so the
app runs fine without it installed when the loop is off (the default).
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_scheduler = None  # module-level handle so shutdown can stop it


def reopt_interval_seconds() -> int:
    try:
        return max(0, int(os.getenv("NEURAL_SYNC_REOPT_INTERVAL", "0")))
    except ValueError:
        return 0


async def _run_cycle() -> None:
    """One re-optimization tick: refresh cached risk scores for all developers."""
    from src.db.session import AsyncSessionLocal
    from src.services.reoptimization import refresh_all_risk_scores

    try:
        async with AsyncSessionLocal() as db:
            n = await refresh_all_risk_scores(db)
            await db.commit()
        logger.info("re-optimization cycle: refreshed risk for %d developer(s)", n)
    except Exception as exc:  # a scheduled tick must never crash the loop
        logger.warning("re-optimization cycle failed: %s", exc)


def start_scheduler():
    """Start the periodic loop if NEURAL_SYNC_REOPT_INTERVAL>0. Returns the scheduler or
    None (disabled / apscheduler absent). Safe to call once at app startup."""
    global _scheduler
    interval = reopt_interval_seconds()
    if interval <= 0:
        logger.info(
            "re-optimization scheduler disabled "
            "(set NEURAL_SYNC_REOPT_INTERVAL>0 to enable the continuous loop)"
        )
        return None
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
    except ImportError:
        logger.warning(
            "NEURAL_SYNC_REOPT_INTERVAL=%d set but apscheduler is not installed — "
            "continuous loop disabled (pip install apscheduler to enable)", interval
        )
        return None

    sched = AsyncIOScheduler()
    sched.add_job(
        _run_cycle, "interval", seconds=interval, id="reopt-risk-refresh",
        max_instances=1, coalesce=True,
    )
    sched.start()
    _scheduler = sched
    logger.info("re-optimization scheduler started (every %ds)", interval)
    return sched


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
        except Exception:
            pass
        _scheduler = None
