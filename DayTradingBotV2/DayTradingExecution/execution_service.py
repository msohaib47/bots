"""
Execution service (v2) -- one instance per account, long-running daemon.

Owns this account's actual positions (positions.json/cooldowns.json/
daily_pnl.json/pnl_history.json/trades.csv, via position_manager.py) and is
the only process that ever writes them. Three independent responsibilities,
all running inside one event loop:

  1. Stop-loss/trailing-stop/scale-out management on already-open positions --
     runs on a short fixed interval (STOP_CHECK_INTERVAL_SECONDS), completely
     independent of sizing/signal availability. This is the one thing that
     must never stop even if the rest of the pipeline is down (see PLAN.md's
     Failure handling section).
  2. New entries -- reacts to `decision.<SYMBOL>` from this account's own
     sizing_service.py the instant one arrives.
  3. Cooldown-reset -- reacts to `signal.<SYMBOL>` state updates (an in-memory
     mirror, updated live) for any underlying currently in cool-down.

Publishes this account's snapshot (topic `account.snapshot`) and a heartbeat
(`heartbeat.execution.<account>`) periodically -- sizing_service.py depends on
the snapshot for every gating decision it makes.

Run with CWD set to this account's thin directory (see config.py / PLAN.md).

Usage:
  python execution_service.py            -- run the live daemon
  python execution_service.py --status   -- print current positions + P&L, exit
  python execution_service.py --close    -- force-close everything now, exit
"""
import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import alpaca
import position_manager as pm
from config import (
    ACCOUNT_NAME, LOG_DIR, NO_NEW_ENTRY_TIME, FORCE_CLOSE_TIME,
    MAX_DAILY_LOSS_TOTAL, MAX_OPEN_EXPOSURE, EXPOSURE_TOLERANCE_PCT,
)

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'shared'))
import kv_store
import pubsub
import ports
import sim_clock

os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler(f'{LOG_DIR}/execution_service.log', encoding='utf-8'), logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

STOP_CHECK_INTERVAL_SECONDS = 5    # much tighter than v1's 60s cron tick -- a direct
                                    # latency win from streaming, see PLAN.md
SNAPSHOT_PUBLISH_INTERVAL_SECONDS = 5
HEARTBEAT_INTERVAL_SECONDS = 10
FORCE_CLOSE_CHECK_INTERVAL_SECONDS = 15
PDT_FLAG_FILE = 'logs/pdt_blocked.flag'
SIZING_HEARTBEAT_TOPIC = pubsub.heartbeat_topic(f'sizing.{ACCOUNT_NAME}')
SIGNAL_HEARTBEAT_TOPIC = pubsub.heartbeat_topic('signal')


# ── Time helpers (ET) ────────────────────────────────────────────────────────

def et_now() -> datetime:
    return sim_clock.now_utc() - timedelta(hours=4)


def et_time_str() -> str:
    return et_now().strftime('%H:%M')


def is_market_open() -> bool:
    now = et_now()
    if now.weekday() >= 5:
        return False
    t = now.strftime('%H:%M')
    return '09:30' <= t <= '16:00'


def can_open_new_position() -> bool:
    return et_time_str() < NO_NEW_ENTRY_TIME


def should_force_close() -> bool:
    return et_time_str() >= FORCE_CLOSE_TIME


def is_pdt_blocked() -> bool:
    if not os.path.exists(PDT_FLAG_FILE):
        return False
    flag_day = datetime.fromtimestamp(os.path.getmtime(PDT_FLAG_FILE)).date()
    if flag_day < et_now().date():
        os.remove(PDT_FLAG_FILE)
        return False
    return True


def set_pdt_blocked():
    os.makedirs(os.path.dirname(PDT_FLAG_FILE), exist_ok=True)
    with open(PDT_FLAG_FILE, 'w') as f:
        f.write(et_now().isoformat())


# ── Option price lookups for open positions ─────────────────────────────────

def get_current_option_prices(state: dict, broker=None) -> dict:
    """`broker` defaults to the real `alpaca` module -- backtest/run_backtest.py
    passes a SimulatedBroker instead so this reads historical prices."""
    if not state:
        return {}
    broker = broker or alpaca
    snaps = broker.get_snapshots_by_symbols(list(state.keys()))
    prices = {}
    for sym in state:
        snap = snaps.get(sym, {})
        quote = snap.get('latestQuote', {})
        bid = float(quote.get('bp', 0) or 0)
        ask = float(quote.get('ap', 0) or 0)
        if ask > 0:
            prices[sym] = (bid + ask) / 2 if bid > 0 else ask
        elif bid > 0:
            prices[sym] = bid
        else:
            logger.warning(f'No quote for {sym}')
    return prices


def _emit_event(publisher, event_type: str, payload: dict):
    """Durable log write (always) + live ZMQ push (if a publisher was given) --
    same pattern as position_manager._emit_event, for the event sites that
    live here in execution_service.py instead."""
    kv_store.append_event(event_type, payload)
    if publisher:
        publisher.publish(f'event.{event_type}', payload)


def close_all_positions(state: dict, reason: str, publisher=None, broker=None):
    broker = broker or alpaca
    if not state:
        return
    for sym, pos in list(state.items()):
        logger.info(f'Closing {sym} ({reason})')
        broker.close_option_position(sym, pos['contracts'])
        pm.log_trade('CLOSE', sym, pos['underlying'], pos['type'],
                     pos['contracts'], 0, reason=reason,
                     extra={'underlying_price': pm._underlying_price(pos['underlying']),
                            'hold_minutes': pm._hold_minutes(pos['opened_at'])})
        _emit_event(publisher, 'trade_close', {
            'account': ACCOUNT_NAME, 'symbol': sym, 'underlying': pos['underlying'],
            'type': pos['type'], 'contracts': pos['contracts'], 'price': 0,
            'pnl': 0, 'reason': reason,
        })
        pm.remove_position(state, sym)
    pm.save_state(state)


class ExecutionEngine:
    def __init__(self, account: str, publisher: pubsub.Publisher | None = None, broker=None,
                 publish_snapshot_to_kv: bool = True):
        """`broker` defaults to the real `alpaca` module (unchanged production
        behavior) -- backtest/run_backtest.py (item #4/#5 of
        BACKTESTING_ENGINE_PLAN.md) passes a SimulatedBroker instead so this
        exact class runs, unmodified, against historical data. This is the
        only I/O edge of ExecutionEngine that needed to become injectable.

        `publish_snapshot_to_kv` (default True, unchanged live behavior):
        found 2026-09-08 that a full Jan-Sep/8-symbol backtest was dominated
        by disk I/O, not decision-logic cost -- publish_snapshot() writes to
        the kv_store SQLite file on every single tick, which is right for a
        live daemon publishing its state every few seconds for crash-recovery
        purposes, but pointless overhead in a backtest (~560,000 writes for
        one run) that either finishes or gets rerun from scratch, with no
        snapshot to resume from either way. Set False only by the backtest
        orchestrator -- this does not change what any decision is, only
        whether that one durability write happens."""
        self.account = account
        self.pub = publisher
        self.broker = broker or alpaca
        self.publish_snapshot_to_kv = publish_snapshot_to_kv
        self.state = pm.load_state()
        self.cooldowns = pm.load_cooldowns()
        self.daily = pm.load_daily_pnl()
        self.signal_mirror: dict[str, dict] = {}  # symbol -> latest signal.<SYM> payload

    # -- daily P&L rollover -----------------------------------------------------
    def _sync_daily_pnl_day(self):
        """Re-loads self.daily from disk if the calendar day has rolled over.

        In live trading this is a no-op in practice: bot.py runs one tick per
        cron invocation, so a fresh process always re-reads the file via
        load_daily_pnl() in __init__, and load_daily_pnl() itself resets to a
        zeroed dict when its stored 'date' != today. But a long-running
        process (this ExecutionEngine instantiated once for an entire
        multi-month backtest, or any future always-on live daemon) never
        re-runs __init__, so without this explicit check self.daily just keeps
        accumulating forever -- a symbol/account that ever crosses
        MAX_DAILY_LOSS_PER_SYMBOL/TOTAL cumulatively stays locked out for
        every subsequent day, not just the day it happened on.
        Found 2026-09-08: this silently blocked META (and SPY/QQQ) for the
        rest of an August backtest after one bad day put META's *lifetime*
        total past the $100 'daily' cap on 2026-08-14 -- including a 356%
        winner v1 (whose backtest reloads this cleanly) caught on 2026-08-28
        that v2 never even attempted."""
        today = pm._today_str()
        if self.daily.get('date') != today:
            self.daily = pm.load_daily_pnl()

    # -- signal mirror + cooldown-reset (reacts to Signal service state updates) --
    def on_signal_update(self, symbol: str, sig: dict):
        self.signal_mirror[symbol] = sig
        if symbol in self.cooldowns:
            pm.update_cooldown_reset(self.cooldowns, symbol, sig.get('price'), sig.get('vwap'),
                                      sig.get('ema9'), sig.get('ema21'))

    # -- new entries (reacts to this account's own Sizing service) --
    def on_decision(self, decision: dict):
        self._sync_daily_pnl_day()
        if not decision.get('accepted'):
            return
        if not is_market_open():
            logger.info('Market closed, ignoring decision.')
            return
        if not can_open_new_position():
            logger.info(f'Past {NO_NEW_ENTRY_TIME} ET, ignoring decision for {decision["symbol"]}.')
            return
        if is_pdt_blocked():
            logger.info('PDT protection active, ignoring decision.')
            return

        contract = decision['contract']
        qty = decision['qty']
        sym = decision['symbol']

        if contract['symbol'] in self.state:
            # Found 2026-09-06 via a v2 backtest run: MAX_POSITIONS_PER_SYMBOL
            # allows a second concurrent position on the same underlying, but
            # position_manager.register_open() keys `state` by OPTION SYMBOL --
            # if the second entry happens to pick the exact same contract
            # (very plausible when spot hasn't moved enough to shift strikes),
            # a second buy here would silently overwrite the first position's
            # tracking (stop price, cost basis, cooldown/close bookkeeping)
            # while the exchange side actually holds both. Refuse the re-buy
            # entirely rather than merging/averaging -- simpler and matches
            # "don't add to a position that's already open" being the safer
            # default for this bot's per-trade stop-loss design.
            logger.info(f'{sym}: skipping decision, {contract["symbol"]} is already open')
            return

        order = self.broker.buy_option(contract, qty)
        if not order:
            if self.broker.LAST_ERROR_CODE == 40310100:
                logger.warning('PDT protection triggered -- blocking new entries for today')
                set_pdt_blocked()
            return

        filled_price = contract['mid'] + 0.01
        sig = self.signal_mirror.get(decision['symbol'])
        pm.register_open(self.state, contract, qty, filled_price, order.get('id', ''), sig=sig)
        pm.save_state(self.state)

        _emit_event(self.pub, 'trade_open', {
            'account': self.account, 'symbol': contract['symbol'], 'underlying': sym,
            'type': contract['type'], 'contracts': qty, 'price': filled_price,
            'pnl': 0, 'reason': sig.get('reason') if sig else 'Entry',
        })
        logger.info(f'ENTERED: {sym} {contract["type"].upper()} {qty}x {contract["symbol"]} @ ${filled_price:.2f}')

    # -- stop/trailing/scale-out management (independent of everything else) --
    def check_stops(self):
        self._sync_daily_pnl_day()
        if not self.state:
            return
        current_prices = get_current_option_prices(self.state, self.broker)
        to_close, to_partial_close = pm.check_and_update_stops(
            self.state, current_prices, self.cooldowns, self.daily,
            account_name=self.account, publisher=self.pub,
        )
        for sym, qty in to_partial_close:
            if sym in to_close:
                continue
            logger.info(f'Executing partial close for {sym}: {qty}x')
            self.broker.close_option_position(sym, qty)
        for sym in to_close:
            pos = self.state.get(sym, {})
            logger.info(f'Executing close for {sym}')
            self.broker.close_option_position(sym, pos.get('contracts', 1))
            pm.remove_position(self.state, sym)
        pm.save_state(self.state)
        pm.save_cooldowns(self.cooldowns)
        pm.save_daily_pnl(self.daily)

    def check_force_close(self):
        if is_market_open() and should_force_close() and self.state:
            logger.info('EOD force close triggered.')
            close_all_positions(self.state, 'EOD force close', publisher=self.pub, broker=self.broker)

    # -- snapshot publishing (what sizing_service.py reads) --
    def build_snapshot(self) -> dict:
        self._sync_daily_pnl_day()
        acct = {}
        try:
            acct = self.broker.get_account()
        except Exception as e:
            logger.error(f'get_account failed: {e}')
        return {
            'cash': float(acct.get('cash', 0)),
            'portfolio_value': float(acct.get('portfolio_value', 0)),
            'open_exposure': pm.open_exposure(self.state),
            'daily_pnl_date': self.daily.get('date'),
            'daily_pnl_total': self.daily.get('total', 0.0),
            'daily_pnl_per_symbol': self.daily.get('per_symbol', {}),
            'positions_per_symbol': {
                u: pm.count_positions_for(self.state, u) for u in pm.get_open_underlyings(self.state)
            },
            'direction_counts': {
                'call': pm.count_direction(self.state, 'call'),
                'put': pm.count_direction(self.state, 'put'),
            },
            'cooldowns': self.cooldowns,
            'pdt_blocked': is_pdt_blocked(),
        }

    def publish_snapshot(self):
        snap = self.build_snapshot()
        if self.publish_snapshot_to_kv:
            kv_store.set(f'account:{self.account}:snapshot', snap, ttl_seconds=SNAPSHOT_PUBLISH_INTERVAL_SECONDS * 4)
        self.pub.publish('account.snapshot', snap)


def print_status(engine: ExecutionEngine):
    state = engine.state
    daily = engine.daily
    print(f'\n{"="*60}')
    print(f'  Execution Service ({ACCOUNT_NAME}) | ET {et_time_str()} | {datetime.now(timezone.utc).strftime("%Y-%m-%d")}')
    print(f'  Realized P&L today: ${daily.get("total", 0):+,.2f} (limit: -${MAX_DAILY_LOSS_TOTAL:.0f})')
    print(f'  Open exposure: ${pm.open_exposure(state):,.2f} / ${MAX_OPEN_EXPOSURE:,.0f} cap (+{EXPOSURE_TOLERANCE_PCT:.0%} tolerance)')
    print(f'{"="*60}')
    if not state:
        print('  No open positions.\n')
        return
    prices = get_current_option_prices(state)
    for sym, pos in state.items():
        cur = prices.get(sym, 0)
        entry = pos['entry_cost']
        pct = (cur - entry) / entry * 100 if entry else 0
        pnl = (cur - entry) * pos['contracts'] * 100
        print(f'  {sym}  {pos["underlying"]} {pos["type"].upper()} x{pos["contracts"]}  '
              f'entry=${entry:.2f} cur=${cur:.2f} P&L={pct:+.1f}% (${pnl:+.2f})')


def run():
    pub = pubsub.Publisher(ports.execution_endpoint(ACCOUNT_NAME))
    sub = pubsub.Subscriber(
        [ports.signal_endpoint(), ports.sizing_endpoint(ACCOUNT_NAME)],
        ['signal.', SIGNAL_HEARTBEAT_TOPIC, 'decision.', SIZING_HEARTBEAT_TOPIC],
    )
    logger.info(f'Execution service for account={ACCOUNT_NAME} bound {ports.execution_endpoint(ACCOUNT_NAME)}, '
                f'subscribed to signal={ports.signal_endpoint()} + sizing={ports.sizing_endpoint(ACCOUNT_NAME)}')

    engine = ExecutionEngine(ACCOUNT_NAME, pub)
    # Bootstrap the signal mirror from kv_store so cooldown-reset checks have
    # something before the first live signal.<SYM> message arrives.
    cached = kv_store.get('signal:latest')
    if cached:
        engine.signal_mirror.update(cached.get('symbols', {}))

    last_stop_check = 0.0
    last_snapshot = 0.0
    last_heartbeat = 0.0
    last_force_close_check = 0.0

    try:
        while True:
            for topic, payload in sub.poll(timeout_ms=1000):
                if topic.startswith('signal.'):
                    engine.on_signal_update(topic.split('.', 1)[1], payload)
                elif topic.startswith('decision.'):
                    engine.on_decision(payload)
                # heartbeat topics: sub.poll() already tracked last-seen internally

            now = time.time()
            if now - last_stop_check >= STOP_CHECK_INTERVAL_SECONDS:
                engine.check_stops()
                last_stop_check = now

            if now - last_force_close_check >= FORCE_CLOSE_CHECK_INTERVAL_SECONDS:
                engine.check_force_close()
                last_force_close_check = now

            if now - last_snapshot >= SNAPSHOT_PUBLISH_INTERVAL_SECONDS:
                engine.publish_snapshot()
                last_snapshot = now

            if now - last_heartbeat >= HEARTBEAT_INTERVAL_SECONDS:
                pub.heartbeat(f'execution.{ACCOUNT_NAME}')
                last_heartbeat = now

            if not sub.is_alive(SIGNAL_HEARTBEAT_TOPIC, max_age_seconds=30):
                logger.warning('Signal service heartbeat stale/missing -- cooldown-reset checks are stale until it recovers')
            if not sub.is_alive(SIZING_HEARTBEAT_TOPIC, max_age_seconds=30):
                logger.warning(f'Sizing service ({ACCOUNT_NAME}) heartbeat stale/missing -- no new entries until it recovers')
    except KeyboardInterrupt:
        logger.info('Stopped by user.')
    finally:
        pub.close()
        sub.close()


if __name__ == '__main__':
    args = sys.argv[1:]
    if '--status' in args:
        print_status(ExecutionEngine(ACCOUNT_NAME))
    elif '--close' in args:
        engine = ExecutionEngine(ACCOUNT_NAME)
        close_all_positions(engine.state, 'Manual close')
    else:
        run()
