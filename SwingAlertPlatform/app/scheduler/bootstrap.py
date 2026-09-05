import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import get_settings
from app.scheduler.jobs import run_cycle

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def start_scheduler() -> BackgroundScheduler | None:
    """Starts the recurring market-data/signal/alert cycle in a background thread.

    Uses a thread-based scheduler (not AsyncIOScheduler) deliberately — run_cycle does
    blocking HTTP + DB work, and running that on the asyncio event loop would stall
    every other request while a cycle is in progress.
    """
    global _scheduler
    settings = get_settings()
    if not settings.enable_scheduler:
        logger.info("Scheduler disabled (ENABLE_SCHEDULER=false)")
        return None

    _scheduler = BackgroundScheduler()
    _scheduler.add_job(run_cycle, "interval", minutes=settings.bar_sync_interval_minutes, id="market_cycle")
    _scheduler.start()
    logger.info(f"Scheduler started — running every {settings.bar_sync_interval_minutes} minute(s)")
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
