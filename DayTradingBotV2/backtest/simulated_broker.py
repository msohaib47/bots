"""
Backtest-only stand-in for DayTradingExecution/alpaca.py -- implements the
same method surface (`buy_option`, `close_option_position`, `get_account`,
`get_snapshots_by_symbols`, `get_latest_price`, `LAST_ERROR_CODE`) so
ExecutionEngine (v2's real, unmodified production class) can place and close
"orders" against historical data instead of a live account. Passed into
`ExecutionEngine(..., broker=self)` by run_backtest.py -- ExecutionEngine
itself needed no changes beyond the DI refactor (item #3) to accept this.

This is deliberately NOT a copy of any exit-decision math (stop/trail/
scale-out) -- that logic still lives in exactly one place,
DayTradingExecution/position_manager.py's check_and_update_stops(), and runs
unmodified here too. SimulatedBroker's only job is "report what a real broker
would report" (current quote, fill, cash) for a given historical instant --
matching this project's decision (BACKTESTING_ENGINE_PLAN.md, 2026-09-06)
that no second, hand-derived copy of trading logic should exist to drift out
of sync with production. `option_position_sim.py` from the original plan was
dropped for that reason: with the DI design, position_manager.py's own
exit-math already only ever lives in v2 (never shared with v1's separate
backtest.py), which is what "keep it v2-local" actually required.

No historical bid/ask exists for options (confirmed against the real API
2026-09-05) -- last-trade price stands in for bid/ask/mid throughout, same
documented limitation as v1's backtest.py and BacktestDataSource.
"""
from datetime import datetime


class SimulatedBroker:
    def __init__(self, starting_cash: float, data_source):
        """`data_source` is a BacktestDataSource (backtest_market_data.py) --
        reused here purely for its options_data price-path lookups, so both
        the signal side and the execution side of one backtest run draw from
        the exact same cached historical prices."""
        self.cash = starting_cash
        self.LAST_ERROR_CODE = None
        self._data = data_source
        self._opened_day: dict = {}   # option_symbol -> 'YYYY-MM-DD' it was opened on
        self._open_qty: dict = {}     # option_symbol -> contracts currently held (see close_option_position)
        self.sim_time: datetime | None = None
        self._next_order_id = 1

    def set_sim_time(self, dt: datetime):
        self.sim_time = dt

    def _price_now(self, option_symbol: str) -> float | None:
        day = self._opened_day.get(option_symbol) or str(self.sim_time.date())
        path = self._data._od.get_option_price_path(option_symbol, day)
        return self._data._od.price_at(path, self.sim_time)

    # -- alpaca.py surface --------------------------------------------------------

    def get_account(self) -> dict:
        # portfolio_value ignores open options' mark-to-market -- sizing_service.py
        # only reads 'cash' for its affordability check, and open_exposure is
        # tracked separately by position_manager.py's own state, so this
        # simplification doesn't affect any gating decision.
        return {'cash': self.cash, 'portfolio_value': self.cash}

    def get_latest_price(self, symbol: str):
        """Underlying spot -- diagnostic-only (trades.csv's CLOSE row column),
        never gates a decision. Not tracked by the broker in this design
        (SignalEngine already carries spot price on every signal payload);
        returning None here matches v1's own best-effort/never-blocks contract."""
        return None

    def buy_option(self, contract: dict, qty: int) -> dict | None:
        # ExecutionEngine.on_decision() registers the position (and logs it to
        # trades.csv) at contract['mid'] + 0.01 -- the same $0.01/share fill
        # slippage the real alpaca.py's buy_option() assumes. The ledger here
        # must charge the identical price or cash silently drifts out of sync
        # with trades.csv's own numbers (found 2026-09-06: a 4-contract SPY
        # trade showed a $4 gap between the two, i.e. exactly 0.01 * 4 * 100).
        fill_price = contract['mid'] + 0.01
        cost = fill_price * qty * 100
        if cost > self.cash:
            self.LAST_ERROR_CODE = None
            return None   # mirrors a real broker rejecting for insufficient buying power
        self.cash -= cost
        self._opened_day[contract['symbol']] = str(self.sim_time.date())
        self._open_qty[contract['symbol']] = self._open_qty.get(contract['symbol'], 0) + qty
        order_id = f'SIM-{self._next_order_id}'
        self._next_order_id += 1
        self.LAST_ERROR_CODE = None
        return {'id': order_id}

    def close_option_position(self, symbol: str, qty: int = None) -> dict | None:
        """
        `qty=None` means "close the whole position", matching the real client's
        DELETE /v2/positions/{symbol}. That used to compute
        `price * (qty or 0) * 100` -- i.e. zero proceeds -- so a full close
        silently destroyed the position's entire value instead of returning it
        to cash (found 2026-09-08). Nothing exercised it at the time because
        every caller happened to pass an explicit quantity, but it was one
        DELETE-style call away from vaporising an account mid-run.

        Resolving it needs the held size, so the broker now tracks open
        quantity per contract the same way a real one does.
        """
        held = self._open_qty.get(symbol, 0)
        sell_qty = held if not qty else min(qty, held)
        price = self._price_now(symbol) or 0.0
        self.cash += price * sell_qty * 100

        remaining = held - sell_qty
        if remaining > 0:
            self._open_qty[symbol] = remaining
        else:
            # Fully closed: drop both markers. A partial close keeps the
            # opened-day marker so a later full close still resolves the right
            # day's cached price path.
            self._open_qty.pop(symbol, None)
            self._opened_day.pop(symbol, None)
        return {'id': f'SIM-close-{symbol}'}

    def get_snapshots_by_symbols(self, option_symbols: list) -> dict:
        out = {}
        for sym in option_symbols:
            price = self._price_now(sym)
            if price is None:
                continue
            out[sym] = {'latestQuote': {'bp': price, 'ap': price}}
        return out
