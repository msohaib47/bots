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
        # (symbol, opt_type, day) -> (spot_at_selection, contract dict). SignalEngine's
        # live production code calls find_atm_contract() on every 5-min recompute where
        # the signal is CALL/PUT, not just the edge-trigger -- fine live (one fast Alpaca
        # call), but in backtest each distinct (opt_type, spot) is a fresh paginated
        # historical-options fetch, confirmed 2026-09-06 to make a full month
        # impractically slow (contract selection alone, ignoring price-path fetches, was
        # re-querying every ~30-60s of wall time per recompute). Backtest-only
        # approximation: keep the previously-selected contract, just re-pricing it at the
        # new sim_time, as long as spot hasn't moved more than CONTRACT_REUSE_BAND_PCT
        # since it was selected -- only re-select when spot actually drifts. Not shared
        # with any production code path.
        self._last_contract: dict = {}

    CONTRACT_REUSE_BAND_PCT = 0.03

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
        documented limitation as v1's backtest. See CONTRACT_REUSE_BAND_PCT's
        note above for why this doesn't always re-select on every call."""
        day = str(self.sim_time.date())
        key = (symbol, opt_type, day)
        cached = self._last_contract.get(key)

        if cached and abs(spot_price - cached[0]) / cached[0] <= self.CONTRACT_REUSE_BAND_PCT:
            contract_sym, strike, expiration = cached[1]
        else:
            cache = self._contract_cache.setdefault(symbol, self._od.load_contract_cache(symbol))
            contract = self._od.get_option_contract(symbol, opt_type, spot_price, self.sim_time.date(),
                                                      max_dte=max_dte, cache=cache)
            if not contract:
                return None
            contract_sym, strike, expiration = contract['symbol'], contract['strike'], contract['expiration']
            self._last_contract[key] = (spot_price, (contract_sym, strike, expiration))

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
