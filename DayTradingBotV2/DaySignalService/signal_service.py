"""
Signal service (v2) -- streaming replacement for DayTradingBot v1's REST-polled
signal generation. Long-running daemon (see PLAN.md): holds an open Alpaca
WebSocket connection to the 1-minute bar channel, maintains rolling per-symbol
bar buffers (bar_engine.py), and recomputes each symbol's signal (signal_math.py,
unchanged math from DayTradingBot/signals.py v1) every time a new 5-minute bar
closes.

Results go to two places every time:
  - `kv_store` (cold-start snapshot / durable audit trail -- see PLAN.md)
  - ZeroMQ PUB, for instant delivery to already-running subscribers (Sizing,
    Execution, Notifier): state updates on topic `signal.<SYMBOL>`, and on an
    edge-trigger (a symbol's signal flipping to CALL/PUT) a discrete event on
    topic `event.signal_triggered`. A heartbeat publishes on
    `heartbeat.signal` every ~10s regardless of whether anything else changed,
    so subscribers can distinguish "market is quiet" from "this service died"
    (see PLAN.md's Failure handling section).

Usage:
  python signal_service.py            -- run the live streaming daemon
  python signal_service.py --once     -- cold-start seed only, print each
                                          symbol's current signal once, exit
                                          (no WS connection -- for quick
                                          verification against v1's bot.py)
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import alpaca_market
import alpaca_options
import signal_math
import ws_client
from bar_engine import BarEngine
from config import SYMBOLS, LOG_FILE
from common.alpaca_config import API_KEY, API_SECRET

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'shared'))
import kv_store
import pubsub
import ports

os.makedirs('logs', exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler(LOG_FILE, encoding='utf-8'), logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_SECONDS = 10
# Signals only recompute every 5 minutes (one new bar close), so the kv_store
# TTL has to comfortably outlast that natural cadence -- otherwise a value
# would look "expired" to a late reader between two perfectly normal updates,
# not just on genuine staleness. Refreshed every heartbeat tick (see
# refresh_kv_snapshot()), so this only needs to survive a couple of missed
# heartbeats, not a couple of missed signal recomputations.
SIGNAL_TTL_SECONDS = HEARTBEAT_INTERVAL_SECONDS * 4


class SignalEngine:
    def __init__(self, symbols: list, publisher: pubsub.Publisher | None = None,
                 market_data=None, options_data=None):
        """
        `market_data`/`options_data` default to the real `alpaca_market`/
        `alpaca_options` modules (unchanged production behavior) --
        backtest/run_backtest.py (BACKTESTING_ENGINE_PLAN.md items #4/#5)
        passes a BacktestDataSource for both instead, so this exact class
        runs, unmodified, against historical data. These were the only two
        I/O edges of SignalEngine that needed to become injectable.
        """
        self.symbols = symbols
        self.bars = BarEngine(symbols)
        self.last_signal = {s: 'NONE' for s in symbols}    # for edge-trigger detection
        self.last_full_signal: dict[str, dict] = {}         # sym -> most recent compute_signal() result
        self.pub = publisher  # None in --once mode -- no live subscribers to reach anyway
        self.market_data = market_data or alpaca_market
        self.options_data = options_data or alpaca_options

    def cold_start(self):
        """One-time REST (or, in backtest, cached-history) seed per symbol
        before the WS stream (or the orchestrator's bar tape) takes over."""
        for sym in self.symbols:
            multi = self.market_data.get_recent_bars(sym, '1Min', limit=600)
            session = self.market_data.get_session_bars(sym, '1Min', limit=600)
            self.bars.seed(sym, multi, session)
            logger.info(f'{sym}: cold-start seeded with {len(multi)} multi-session / {len(session)} session 1m bars')

    def on_bar(self, stream_bar: dict):
        sym = stream_bar.get('S')
        if sym not in self.symbols:
            return
        boundary = self.bars.add_bar(sym, stream_bar)
        if not boundary:
            return
        self._recompute(sym)

    def _recompute(self, sym: str):
        bars5 = self.bars.multi_5m(sym, limit=60)
        sess5 = self.bars.session_5m(sym, limit=60)
        bars15 = self.bars.multi_15m(sym, limit=30)
        sig = signal_math.compute_signal(bars5, sess5, bars15)
        logger.info(f'{sym}: signal={sig["signal"]} | {sig["reason"]}')

        # ATM contract lookup is centralized here (account-independent) so N
        # accounts' sizing services don't each redundantly hit Alpaca's
        # options endpoints for the same signal -- only done on an actual
        # edge-trigger (signal just flipped to CALL/PUT), NOT on every
        # recomputation while an existing signal persists across multiple
        # 5-min bars. The previous check (`sig['signal'] in ('CALL','PUT')`
        # alone) re-ran this lookup every ~5 minutes for as long as a trend
        # held, contradicting this very comment -- confirmed 2026-09-08 via
        # backtest profiling that this was the dominant cost (a live network
        # call to Alpaca's options endpoint nearly every time, since the
        # contract cache is keyed partly on spot price, which drifts bar to
        # bar and so almost never hits). This is a real live bug too, not
        # just a backtest artifact: production was redundantly re-querying
        # Alpaca every 5 min for an unchanged contract.
        sig['contract'] = None
        # Look up a contract whenever a signal is LIVE, not only on the bar it
        # flips on. DayTradingBot v1's bot.py re-evaluates every tick for as
        # long as a signal holds, so a setup that was unaffordable (or blocked
        # by a cool-down) when it first fired is retried and can still be taken
        # on a later bar. v2 fired once per flip and then went quiet until the
        # signal cycled back through NONE, which made the two engines pick
        # different trades from the same signal stream -- on QQQ 2026-08-05 v1
        # entered at 14:00 while v2 sat out until 15:00 (found 2026-09-08).
        # Re-querying is cheap now that options_data caches a whole day's chain
        # per (type, day) rather than one contract per exact spot.
        is_live = sig['signal'] in ('CALL', 'PUT')
        if is_live and sig['price'] is not None:
            opt_type = 'call' if sig['signal'] == 'CALL' else 'put'
            sig['contract'] = self.options_data.find_atm_contract(sym, opt_type, sig['price'])

        self._publish(sym, sig)

    def _publish(self, sym: str, sig: dict):
        self.last_full_signal[sym] = sig
        self.refresh_kv_snapshot()
        if self.pub:
            self.pub.publish(f'signal.{sym}', sig)

        prev = self.last_signal[sym]
        # Emit on every bar the signal is live (see _recompute) so the sizing
        # service gets the same repeated shot at an entry that v1's per-tick
        # loop does. Execution still refuses a second concurrent position per
        # symbol, so a persisting signal cannot stack positions -- it only
        # allows a retry once the previous one has closed.
        if sig['signal'] in ('CALL', 'PUT'):
            # Include the full indicator/contract context, not just the bare
            # signal -- sizing_service.py acts directly on this event and
            # needs the contract (and premium % check inputs) without a
            # second round-trip to the signal.<SYM> state topic.
            event = {'symbol': sym, **sig}
            kv_store.append_event('signal_triggered', event)
            if self.pub:
                self.pub.publish('event.signal_triggered', event)
            logger.info(f'{sym}: EDGE-TRIGGER {prev} -> {sig["signal"]}')
        self.last_signal[sym] = sig['signal']

    def refresh_kv_snapshot(self):
        """
        Re-writes the whole per-symbol map (PLAN.md's `signal:latest` shape)
        with a fresh TTL. Called on every new computation AND on every
        heartbeat tick (see _heartbeat_loop) -- the latter is what keeps a
        value alive across the ~5-minute gaps between actual recomputations,
        since the TTL alone (tuned to the heartbeat cadence, not the
        recomputation cadence) would otherwise expire it between updates.
        """
        if not self.last_full_signal:
            return
        kv_store.set('signal:latest', {'symbols': self.last_full_signal}, ttl_seconds=SIGNAL_TTL_SECONDS)

    def print_once(self):
        """--once mode: seed then compute+print current signal per symbol, no WS."""
        for sym in self.symbols:
            bars5 = self.bars.multi_5m(sym, limit=60)
            sess5 = self.bars.session_5m(sym, limit=60)
            bars15 = self.bars.multi_15m(sym, limit=30)
            sig = signal_math.compute_signal(bars5, sess5, bars15)
            print(f'{sym}: signal={sig["signal"]} | {sig["reason"]}')


def _on_stream_error(exc: Exception, delay: float):
    kv_store.append_event('alert', {
        'severity': 'error', 'source': 'signal_service',
        'message': f'Stream error: {exc!r}; reconnecting in {delay}s',
    })


async def _heartbeat_loop(pub: pubsub.Publisher, engine: 'SignalEngine'):
    while True:
        pub.heartbeat('signal')
        engine.refresh_kv_snapshot()
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)


async def _run_async(pub: pubsub.Publisher):
    engine = SignalEngine(SYMBOLS, publisher=pub)
    engine.cold_start()

    logger.info(f'Connecting signal stream for {SYMBOLS}...')
    heartbeat_task = asyncio.create_task(_heartbeat_loop(pub, engine))
    try:
        # ws_client.stream_bars runs forever with its own internal
        # reconnect-with-backoff loop (see ws_client.py) -- this call only
        # returns on KeyboardInterrupt/CancelledError.
        await ws_client.stream_bars(
            API_KEY, API_SECRET, SYMBOLS, engine.on_bar, feed='iex', on_error=_on_stream_error,
        )
    finally:
        heartbeat_task.cancel()


def run():
    pub = pubsub.Publisher(ports.signal_endpoint())
    logger.info(f'Signal publisher bound to {ports.signal_endpoint()}')
    try:
        asyncio.run(_run_async(pub))
    except KeyboardInterrupt:
        logger.info('Stopped by user.')
    finally:
        pub.close()


if __name__ == '__main__':
    if '--once' in sys.argv[1:]:
        engine = SignalEngine(SYMBOLS)
        engine.cold_start()
        engine.print_once()
    else:
        run()
