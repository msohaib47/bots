"""
Injectable "now" for position_manager.py / execution_service.py (v2 only --
DayTradingBot v1 is untouched and has no equivalent).

Why this exists: position_manager.py's cooldown expiry, daily-P&L date
rollover, and trades.csv timestamps all read the wall clock directly via
`datetime.now(timezone.utc)`. That's correct for the live daemons, but a
backtest replaying, say, three months of 2026-06 through 2026-08 bars in one
process would have every one of those calls return today's real date instead
of the simulated one -- silently breaking MAX_DAILY_LOSS_TOTAL/per-symbol
day-rollover (every simulated day would be treated as "today", so daily loss
never resets) and mislabeling every trades.csv row with the wrong timestamp.
This is exactly the class of bug BACKTESTING_ENGINE_PLAN.md exists to avoid
repeating (see the HTF-bars incident in project_known_issues.md).

Default behavior (no override) is real wall-clock time -- zero behavior
change for the live daemons. Only `DayTradingBotV2/backtest/run_backtest.py`
ever calls `set()`.
"""
from datetime import datetime, timezone

_override: datetime | None = None


def now_utc() -> datetime:
    return _override if _override is not None else datetime.now(timezone.utc)


def set(dt: datetime | None):
    """Pass a tz-aware UTC datetime to freeze `now_utc()` at that instant, or
    None to release the override back to the real wall clock."""
    global _override
    _override = dt
