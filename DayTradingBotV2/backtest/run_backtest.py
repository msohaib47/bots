#!/usr/bin/env python3
"""
v2 backtest orchestrator (BACKTESTING_ENGINE_PLAN.md items #4/#5).

Walks historical 1-minute bars chronologically and drives v2's ACTUAL
SignalEngine / SizingEngine / ExecutionEngine classes in-process -- the exact
same objects the live daemons run, completely unmodified in their decision
logic. Only three I/O edges are swapped for the duration of the run:

  - SignalEngine's market/options data source  -> BacktestDataSource
  - ExecutionEngine's broker                    -> SimulatedBroker
  - ZeroMQ pub/sub between all three services   -> InProcessBus (below),
    a fake `publisher` that calls the same `on_*` handlers directly in one
    process instead of over a socket -- all three engines already accepted
    an injected `publisher` from day one (see PLAN.md), so this needed no
    code changes at all.

No second, hand-derived copy of the signal or risk-gate math exists anywhere
in this file -- that is the entire reason the DI refactor (item #3) was done
first. See BACKTESTING_ENGINE_PLAN.md's 2026-09-06 boundary decision for why
this differs from v1's separate, already-working DayTradingBot/backtest.py.

Historical bars/options data come from the shared backtesting-engine/
package (item #1) -- the only thing this shares with v1's backtest.

Each run gets its own scratch directory under backtest/runs/<run_id>/ --
positions.json/trades.csv/cooldowns.json/daily_pnl.json/kv_store's sqlite
file all land there, never in a real account's live directory. Safe to
delete a run's folder any time; nothing outside it is touched.

Usage:
  python run_backtest.py SPY QQQ --start 2026-06-01 --end 2026-08-31 --cash 5000
  python run_backtest.py SPY --start 2026-04-01 --end 2026-05-31 --cash 200 --run-id apr_may_200
"""
import argparse
import csv
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
V2_ROOT = os.path.dirname(HERE)
BOTS_ROOT = os.path.dirname(V2_ROOT)
ENGINE_DIR = os.path.join(BOTS_ROOT, 'backtesting-engine')

# Dummy credentials so common/alpaca_config.py's AlpacaClient constructs
# cleanly at import time even though this run never uses it -- every actual
# data/order call in a backtest goes through BacktestDataSource/
# SimulatedBroker instead, never the real credentialed client.
os.environ.setdefault('ALPACA_API_KEY', 'backtest')
os.environ.setdefault('ALPACA_SECRET_KEY', 'backtest')

SIGNAL_DIR = os.path.join(V2_ROOT, 'DaySignalService')
EXEC_DIR = os.path.join(V2_ROOT, 'DayTradingExecution')

sys.path.insert(0, os.path.join(V2_ROOT, 'shared'))
sys.path.insert(0, ENGINE_DIR)
sys.path.insert(0, HERE)

import sim_clock
from backtest_market_data import BacktestDataSource
from simulated_broker import SimulatedBroker


def _import_signal_engine():
    """DaySignalService and DayTradingExecution each have their own
    config.py -- putting both directories on sys.path at once (as an earlier
    version of this script did) makes `import config`/`from config import
    ...` ambiguous: Python caches modules by bare name in sys.modules, so
    whichever service's config.py loads first "wins" and the other service's
    `from config import STATE_FILE` (etc.) silently gets the wrong module's
    names instead of an ImportError -- confirmed the hard way, 2026-09-06.
    Loading each service's modules in its own turn, with the other service's
    directory removed from sys.path and its cached same-named modules evicted
    in between (see _import_execution_modules), keeps them isolated."""
    sys.path.insert(0, SIGNAL_DIR)
    from signal_service import SignalEngine
    return SignalEngine


def _import_execution_modules():
    # Evict anything DaySignalService's imports may have cached under a name
    # DayTradingExecution also uses (today just 'config', but this guards any
    # future same-named module too) so the next `from config import ...`
    # re-reads DayTradingExecution/config.py instead of reusing the wrong
    # cached module.
    for name in ('config',):
        sys.modules.pop(name, None)
    sys.path[:] = [p for p in sys.path if os.path.normcase(os.path.normpath(p)) != os.path.normcase(os.path.normpath(SIGNAL_DIR))]
    sys.path.insert(0, EXEC_DIR)
    from execution_service import ExecutionEngine, get_current_option_prices
    from sizing_service import SizingEngine
    import position_manager as pm
    return ExecutionEngine, get_current_option_prices, SizingEngine, pm


class InProcessBus:
    """Fake pubsub.Publisher: routes publish() calls straight to in-process
    handlers instead of a ZMQ socket, so SignalEngine/SizingEngine/
    ExecutionEngine (all unmodified) talk to each other exactly the way the
    live daemons do, just synchronously and in one process."""

    def __init__(self):
        self._handlers = defaultdict(list)

    def on(self, topic_prefix: str, callback):
        self._handlers[topic_prefix].append(callback)

    def publish(self, topic: str, payload: dict):
        for prefix, callbacks in self._handlers.items():
            if topic.startswith(prefix):
                for cb in callbacks:
                    cb(topic, payload)

    def heartbeat(self, name: str):
        pass   # no liveness concept in a backtest

    def close(self):
        pass


def run(symbols: list, start_date: str, end_date: str, starting_cash: float, run_id: str = None):
    """
    NOTE: IPC_DB_PATH (below) only takes effect the first time this process
    imports kv_store.py -- its DB_PATH is a module-level constant read once at
    import time. Calling run() more than once in the same process would have
    every call after the first still write to the first run's sqlite file.
    Fine for this script's CLI usage (one run per process); if a future
    caller wants to sweep several runs in one process, give kv_store.py a
    reset/reconfigure hook rather than relying on the env var per call.
    """
    run_id = run_id or f'{start_date}_to_{end_date}_{"-".join(symbols)}_{int(starting_cash)}'
    run_dir = os.path.join(HERE, 'runs', run_id)
    os.makedirs(run_dir, exist_ok=True)
    prev_cwd = os.getcwd()
    os.chdir(run_dir)
    os.environ['ACCOUNT_NAME'] = f'backtest-{run_id}'
    # shared/kv_store.py's DB_PATH defaults to a FIXED path next to kv_store.py
    # itself (not CWD-relative) -- without this override every backtest run
    # would share, and could lock, the live daemons' real event-log database.
    os.environ['IPC_DB_PATH'] = os.path.join(run_dir, 'ipc_store.db')

    try:
        # Imported only now (not at module top) so their module-level
        # `os.makedirs('logs', ...)` / dotenv resolution happen against the
        # scratch run_dir we just chdir'd into, not wherever this script was
        # invoked from. Staged (signal service, then execution/sizing) to
        # avoid the config.py name collision -- see _import_execution_modules.
        SignalEngine = _import_signal_engine()
        ExecutionEngine, get_current_option_prices, SizingEngine, pm = _import_execution_modules()
        import execution_service as _exec_mod
        import kv_store   # safe to import only now -- IPC_DB_PATH is already set above,
                           # and execution_service's own `import kv_store` already forced
                           # this same module object into sys.modules with that path applied

        data = BacktestDataSource(ENGINE_DIR)
        broker = SimulatedBroker(starting_cash, data)
        pm.set_broker(broker)   # trades.csv's diagnostic underlying_price column

        bus = InProcessBus()
        sig_engine = SignalEngine(symbols, publisher=bus, market_data=data, options_data=data)
        exec_engine = ExecutionEngine('backtest', publisher=bus, broker=broker)
        sizing = SizingEngine('backtest', publisher=bus)
        sizing.snapshot_fresh = lambda: True   # no async staleness concept when
                                                 # every step runs synchronously

        bus.on('signal.', lambda topic, payload: exec_engine.on_signal_update(topic.split('.', 1)[1], payload))
        bus.on('event.signal_triggered', lambda topic, payload: sizing.on_signal_triggered(payload))
        bus.on('decision.', lambda topic, payload: exec_engine.on_decision(payload))
        bus.on('account.snapshot', lambda topic, payload: sizing.on_snapshot(payload))

        print(f'Fetching bars for {", ".join(symbols)} ({start_date} to {end_date})...')
        all_bars = {sym: data.get_bars_for_backtest(sym, start_date, end_date) for sym in symbols}
        tape = sorted((b['t'], sym, b) for sym in symbols for b in all_bars[sym])
        if not tape:
            print('No bars found for this range.')
            return
        print(f'{len(tape)} total 1-min bars across {len(symbols)} symbols.')

        first_t = datetime.fromisoformat(tape[0][0].replace('Z', '+00:00'))
        sim_clock.set(first_t)
        data.set_sim_time(first_t)
        broker.set_sim_time(first_t)
        sig_engine.cold_start()
        sizing.snapshot = exec_engine.build_snapshot()

        # Throttle check_stops/check_force_close/publish_snapshot the same way
        # the live daemon's run() loop does, but keyed on SIMULATED time (the
        # `now` derived from each bar's own timestamp) instead of wall-clock
        # time.time(). NOTE: the live intervals (STOP_CHECK_INTERVAL_SECONDS=5s
        # etc.) are all shorter than one simulated bar step (60s), so this
        # throttle alone is a no-op here -- it's kept anyway for correctness if
        # this script is ever adapted to sub-minute bars, and costs nothing.
        # The actual fix is below: these three calls' disk writes
        # (positions.json/cooldowns.json/daily_pnl.json + kv_store's sqlite
        # commit) are pure persistence for live crash-recovery / cross-process
        # handoff -- meaningless inside one synchronous in-process backtest,
        # where check_and_update_stops already operates on the in-memory
        # state/cooldowns/daily dicts directly. Confirmed 2026-09-08: calling
        # them for real on every one of ~530k bar-events made a full Jan-Sep
        # run take on the order of 7-8 hours, almost entirely synchronous I/O.
        # No-op'd for the duration of the loop; real saves still happen once
        # at the end so positions.json/cooldowns.json/daily_pnl.json reflect
        # final state for anyone inspecting the run directory afterward.
        _real_save_state, _real_save_cooldowns, _real_save_daily_pnl = (
            pm.save_state, pm.save_cooldowns, pm.save_daily_pnl)
        pm.save_state = lambda *a, **k: None
        pm.save_cooldowns = lambda *a, **k: None
        pm.save_daily_pnl = lambda *a, **k: None
        kv_store.set = lambda *a, **k: None

        last_stop_check = last_force_close_check = last_snapshot = first_t
        stop_interval        = timedelta(seconds=_exec_mod.STOP_CHECK_INTERVAL_SECONDS)
        force_close_interval = timedelta(seconds=_exec_mod.FORCE_CLOSE_CHECK_INTERVAL_SECONDS)
        snapshot_interval    = timedelta(seconds=_exec_mod.SNAPSHOT_PUBLISH_INTERVAL_SECONDS)

        for t_str, sym, bar in tape:
            now = datetime.fromisoformat(t_str.replace('Z', '+00:00'))
            sim_clock.set(now)
            data.set_sim_time(now)
            broker.set_sim_time(now)

            if now - last_stop_check >= stop_interval:
                exec_engine.check_stops()
                last_stop_check = now
            if now - last_force_close_check >= force_close_interval:
                exec_engine.check_force_close()
                last_force_close_check = now
            if now - last_snapshot >= snapshot_interval:
                exec_engine.publish_snapshot()          # -> bus -> sizing.on_snapshot
                last_snapshot = now
            sig_engine.on_bar({'S': sym, **bar})     # may edge-trigger -> sizing -> exec, all synchronously via bus

        # Final pass so a position that would've closed/force-closed between
        # the last throttled check and the tape's end isn't silently dropped.
        # Real disk saves restored first so positions.json/cooldowns.json/
        # daily_pnl.json in the run directory reflect final state, in case
        # anyone wants to inspect them after the run.
        pm.save_state, pm.save_cooldowns, pm.save_daily_pnl = (
            _real_save_state, _real_save_cooldowns, _real_save_daily_pnl)
        exec_engine.check_stops()
        exec_engine.check_force_close()

        data.save_caches()
        _print_report(run_dir, starting_cash, broker.cash)
    finally:
        os.chdir(prev_cwd)


def _print_report(run_dir: str, starting_cash: float, ending_cash: float):
    trades_path = os.path.join(run_dir, 'trades.csv')
    print(f'\n{"="*60}')
    print(f'  DayTradingBotV2 backtest -- {run_dir}')
    print(f'{"="*60}')
    print(f'  Starting cash: ${starting_cash:,.2f}')
    print(f'  Ending cash:   ${ending_cash:,.2f}  ({ending_cash - starting_cash:+,.2f})')
    if not os.path.exists(trades_path):
        print('  No trades.csv written -- no positions were ever opened.\n')
        return
    with open(trades_path) as f:
        rows = list(csv.DictReader(f))
    opens = [r for r in rows if r['action'] == 'OPEN']
    closes = [r for r in rows if r['action'] in ('CLOSE', 'PARTIAL_CLOSE')]
    wins = [r for r in closes if float(r['pnl'] or 0) > 0]
    total_pnl = sum(float(r['pnl'] or 0) for r in closes)
    print(f'  Entries: {len(opens)}   Closes/partial-closes: {len(closes)}   '
          f'Win rate: {len(wins)/len(closes)*100:.1f}%' if closes else '  No closes recorded.')
    print(f'  Realized P&L (sum of trades.csv pnl column): ${total_pnl:+,.2f}')
    print(f'  Full detail: {trades_path}\n')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('symbols', nargs='+')
    ap.add_argument('--start', required=True)
    ap.add_argument('--end', required=True)
    ap.add_argument('--cash', type=float, default=5000.0)
    ap.add_argument('--run-id', default=None)
    args = ap.parse_args()
    run(args.symbols, args.start, args.end, args.cash, args.run_id)
