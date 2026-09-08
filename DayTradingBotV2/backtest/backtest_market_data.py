"""
Backtest-only stand-in for DaySignalService's `alpaca_market`/`alpaca_options`
modules -- implements the exact same call signatures SignalEngine uses
(`get_recent_bars`, `get_session_bars`, `find_atm_contract`), backed by
`backtesting-engine/`'s cached historical data (item #1) instead of a live
Alpaca REST call. Passed into `SignalEngine(..., market_data=self,
options_data=self)` by run_backtest.py -- SignalEngine itself needed no
changes beyond the DI refactor (item #3) to accept this.

Not shared with v1's backtest.py (item #2's boundary): v1 built its own
inline fetch/contract-lookup code well before this package existed and is
left untouched. This module and v1's backtest.py both ultimately read the
SAME underlying cache files via backtesting-engine/, but the code that shapes
that data into "what SignalEngine expects" vs. "what v1's bot.py expects" is
separate, per BACKTESTING_ENGINE_PLAN.md's boundary decision.

Time model: the orchestrator calls `set_sim_time(dt)` before every simulated
bar/tick. Every method below answers "as of that instant", never real
wall-clock time.
"""
import os
import sys
from datetime import datetime, timedelta, date


class BacktestDataSource:
    def __init__(self, engine_dir: str):
        sys.path.insert(0, engine_dir)
        import market_data as _md
        import options_data as _od
        self._md = _md
        self._od = _od
        self.sim_time: datetime | None = None
        self._contract_cache: dict = {}   # underlying -> cache dict (options_data.load_contract_cache)

    def set_sim_time(self, dt: datetime):
        self.sim_time = dt

    # -- SignalEngine.cold_start() surface --------------------------------------

    def get_recent_bars(self, symbol: str, timeframe: str = '1Min', limit: int = 600) -> list:
        """Multi-session history strictly before sim_time (mirrors alpaca_market.get_recent_bars,
        which fetches 'the most recent N bars regardless of day')."""
        day = self.sim_time.date()
        bars = self._md.get_bars(symbol, timeframe, start_date=str(day - timedelta(days=10)),
                                  end_date=str(day), verbose=False)
        cutoff = self.sim_time.isoformat()
        bars = [b for b in bars if b['t'] < cutoff]
        return bars[-limit:]

    def get_session_bars(self, symbol: str, timeframe: str = '1Min', limit: int = 600) -> list:
        """Today-only bars strictly before sim_time (mirrors alpaca_market.get_session_bars)."""
        day = str(self.sim_time.date())
        bars = self._md.get_bars(symbol, timeframe, start_date=day, end_date=day, verbose=False)
        cutoff = self.sim_time.isoformat()
        bars = [b for b in bars if b['t'] < cutoff]
        return bars[-limit:]

    # -- SignalEngine._recompute() surface ---------------------------------------

    def find_atm_contract(self, symbol: str, opt_type: str, spot_price: float, max_dte: int = 5) -> dict | None:
        """Mirrors alpaca_options.find_atm_contract's return shape. No historical
        bid/ask exists (confirmed against the real API 2026-09-05, see v1's
        backtest.py) -- last-trade price stands in for bid/ask/mid, same
        documented limitation as v1's backtest.

        Always re-selects the freshest ATM contract for the current spot,
        matching v1's backtest.py exactly -- this used to keep reusing a
        previously-selected strike within a 3% spot band (a performance
        workaround from before SignalEngine's edge-trigger-only lookup fix,
        see signal_service.py's _recompute()), which meant v2 could trade a
        contract that was no longer actually closest-to-the-money. Removed
        2026-09-08: the edge-trigger fix already limits how often this gets
        called (only on a genuine signal flip, same cadence as v1's
        per-entry-decision lookup), so the approximation was pure drift with
        no remaining performance benefit."""
        day = str(self.sim_time.date())
        cache = self._contract_cache.setdefault(symbol, self._od.load_contract_cache(symbol))
        contract = self._od.get_option_contract(symbol, opt_type, spot_price, self.sim_time.date(),
                                                  max_dte=max_dte, cache=cache)
        if not contract:
            return None
        contract_sym, strike, expiration = contract['symbol'], contract['strike'], contract['expiration']

        path = self._od.get_option_price_path(contract_sym, day)
        mid = self._od.price_at(path, self.sim_time)
        if mid is None:
            return None
        return {
            'symbol': contract_sym, 'strike': strike,
            'bid': mid, 'ask': mid, 'mid': mid,
            'type': opt_type, 'underlying': symbol, 'expiry': expiration,
        }

    # -- Orchestrator-only helpers -----------------------------------------------

    def get_bars_for_backtest(self, symbol: str, start_date: str, end_date: str,
                               timeframe: str = '1Min') -> list:
        """Full simulated tape for one symbol, filtered to regular market hours
        (09:30-16:00 ET) the same way v1's backtest.py's _group_by_day does --
        pre/post-market 1-min bars would otherwise feed the bar tape too."""
        from zoneinfo import ZoneInfo
        et = ZoneInfo('America/New_York')
        bars = self._md.get_bars(symbol, timeframe, start_date=start_date, end_date=end_date)
        out = []
        for b in bars:
            t = datetime.fromisoformat(b['t'].replace('Z', '+00:00')).astimezone(et).strftime('%H:%M')
            if '09:30' <= t <= '16:00':
                out.append(b)
        return out

    def save_caches(self):
        for underlying, cache in self._contract_cache.items():
            self._od.save_contract_cache(underlying, cache)
