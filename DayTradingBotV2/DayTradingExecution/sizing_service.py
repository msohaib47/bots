"""
Sizing service (v2) -- one instance per account, long-running daemon.

Reacts to `event.signal_triggered` from the shared Signal service the instant
one arrives (not on a periodic scan of all symbols, unlike v1's bot.py loop --
streaming means sizing only has work to do when a signal actually fires).
Applies this account's risk gates using its own latest account snapshot
(published by this account's execution_service.py), and if accepted, publishes
a sizing decision that execution_service.py acts on immediately.

Run with CWD set to this account's thin directory (see config.py / PLAN.md).

Usage:
  python sizing_service.py
"""
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import position_manager as pm
from config import (
    ACCOUNT_NAME, MAX_DAILY_LOSS_PER_SYMBOL, MAX_DAILY_LOSS_TOTAL,
    MAX_SAME_DIRECTION, MAX_POSITIONS_PER_SYMBOL, MAX_OPEN_EXPOSURE,
    EXPOSURE_TOLERANCE_PCT, CASH_PER_TRADE_PCT, MAX_CONTRACTS,
    MIN_CONTRACT_PRICE, MAX_PREMIUM_PCT, LOG_DIR,
)

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'shared'))
import kv_store
import pubsub
import ports

os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler(f'{LOG_DIR}/sizing_service.log', encoding='utf-8'), logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_SECONDS = 10
SIGNAL_HEARTBEAT_TOPIC = pubsub.heartbeat_topic('signal')
EXEC_HEARTBEAT_TOPIC = pubsub.heartbeat_topic(f'execution.{ACCOUNT_NAME}')
SNAPSHOT_STALE_SECONDS = 30  # an account snapshot older than this is treated as missing


class SizingEngine:
    def __init__(self, account: str, publisher: pubsub.Publisher):
        self.account = account
        self.pub = publisher
        self.snapshot: dict | None = None
        self.snapshot_at: float = 0.0

    def bootstrap(self):
        """Cold-start: load this account's last-known snapshot from kv_store
        (published by execution_service.py) so gating isn't blind on startup."""
        snap = kv_store.get(f'account:{self.account}:snapshot')
        if snap:
            self.snapshot = snap
            self.snapshot_at = time.time()
            logger.info(f'Bootstrapped account snapshot from kv_store: cash=${snap.get("cash", 0):,.2f}')
        else:
            logger.warning('No cold-start account snapshot available yet -- will wait for one to arrive live.')

    def on_snapshot(self, snapshot: dict):
        self.snapshot = snapshot
        self.snapshot_at = time.time()

    def snapshot_fresh(self) -> bool:
        return self.snapshot is not None and (time.time() - self.snapshot_at) < SNAPSHOT_STALE_SECONDS

    def on_signal_triggered(self, event: dict):
        symbol = event['symbol']
        direction = event['signal']  # 'CALL' or 'PUT'
        opt_type = 'call' if direction == 'CALL' else 'put'
        contract = event.get('contract')
        spot = event.get('price')

        decision = self._evaluate(symbol, opt_type, contract, spot)
        logger.info(f'{symbol}: {"ACCEPT" if decision["accepted"] else "skip"} - {decision["reason"]}')
        self.pub.publish(f'decision.{symbol}', decision)

    def _evaluate(self, symbol: str, opt_type: str, contract: dict | None, spot: float | None) -> dict:
        base = {'symbol': symbol, 'opt_type': opt_type, 'contract': contract,
                'qty': 0, 'accepted': False, 'reason': ''}

        if not self.snapshot_fresh():
            return {**base, 'reason': 'no fresh account snapshot -- execution_service may be down'}

        snap = self.snapshot
        daily = {'date': snap.get('daily_pnl_date', ''), 'total': snap.get('daily_pnl_total', 0.0),
                  'per_symbol': snap.get('daily_pnl_per_symbol', {})}
        cooldowns = snap.get('cooldowns', {})

        if snap.get('pdt_blocked'):
            return {**base, 'reason': 'PDT protection active'}

        if pm.total_daily_loss_exceeded(daily, MAX_DAILY_LOSS_TOTAL):
            return {**base, 'reason': f'max total daily loss (${MAX_DAILY_LOSS_TOTAL:.0f}) hit'}

        if snap.get('positions_per_symbol', {}).get(symbol, 0) >= MAX_POSITIONS_PER_SYMBOL:
            return {**base, 'reason': f'already at max positions per symbol ({MAX_POSITIONS_PER_SYMBOL})'}

        if pm.symbol_daily_loss_exceeded(daily, symbol, MAX_DAILY_LOSS_PER_SYMBOL):
            return {**base, 'reason': f'max daily loss for {symbol} (${MAX_DAILY_LOSS_PER_SYMBOL:.0f}) hit'}

        if pm.is_in_cooldown(cooldowns, symbol):
            return {**base, 'reason': 'in cool-down after stop-loss'}

        if snap.get('direction_counts', {}).get(opt_type, 0) >= MAX_SAME_DIRECTION:
            return {**base, 'reason': f'max {MAX_SAME_DIRECTION} {opt_type.upper()}s already open'}

        if not contract:
            return {**base, 'reason': 'no liquid ATM contract found'}

        if contract['mid'] < MIN_CONTRACT_PRICE:
            return {**base, 'reason': f'contract mid ${contract["mid"]:.2f} below ${MIN_CONTRACT_PRICE:.2f} minimum'}

        prem_pct = contract['mid'] / spot * 100 if spot else 0
        if prem_pct > MAX_PREMIUM_PCT:
            return {**base, 'reason': f'premium {prem_pct:.2f}% of spot > {MAX_PREMIUM_PCT:.2f}% max'}

        cash = snap.get('cash', 0.0)
        exposure = snap.get('open_exposure', 0.0)
        cost_per_contract = contract['mid'] * 100
        max_afford = int(cash * CASH_PER_TRADE_PCT / cost_per_contract) if cost_per_contract else 0
        room = MAX_OPEN_EXPOSURE * (1 + EXPOSURE_TOLERANCE_PCT) - exposure
        max_fit = int(room / cost_per_contract) if room > 0 and cost_per_contract else 0
        qty = min(MAX_CONTRACTS, max_afford, max_fit)

        if qty < 1:
            if max_fit < 1:
                return {**base, 'reason': f'open exposure ${exposure:,.0f} + ${cost_per_contract:,.0f}/contract would exceed ${MAX_OPEN_EXPOSURE:,.0f} cap'}
            return {**base, 'reason': f'cannot afford even 1 contract (cost=${cost_per_contract:.2f}, cash=${cash:.2f})'}

        notes = f'sized {qty}x (cash cap {max_afford}, exposure cap {max_fit}, config cap {MAX_CONTRACTS})'
        return {**base, 'qty': qty, 'accepted': True, 'reason': notes}


def run():
    account = ACCOUNT_NAME
    pub = pubsub.Publisher(ports.sizing_endpoint(account))
    sub = pubsub.Subscriber(
        [ports.signal_endpoint(), ports.execution_endpoint(account)],
        ['event.signal_triggered', SIGNAL_HEARTBEAT_TOPIC, 'account.snapshot', EXEC_HEARTBEAT_TOPIC],
    )
    logger.info(f'Sizing service for account={account} bound {ports.sizing_endpoint(account)}, '
                f'subscribed to signal={ports.signal_endpoint()} + execution={ports.execution_endpoint(account)}')

    engine = SizingEngine(account, pub)
    engine.bootstrap()

    last_heartbeat = 0.0
    try:
        while True:
            for topic, payload in sub.poll(timeout_ms=1000):
                if topic == 'account.snapshot':
                    engine.on_snapshot(payload)
                elif topic == 'event.signal_triggered':
                    engine.on_signal_triggered(payload)
                # heartbeat topics: sub.poll() already updated last-seen internally

            if not sub.is_alive(SIGNAL_HEARTBEAT_TOPIC, max_age_seconds=30):
                logger.warning('Signal service heartbeat stale/missing -- new-entry decisions will report "no fresh signal" implicitly via stale event flow')
            if not sub.is_alive(EXEC_HEARTBEAT_TOPIC, max_age_seconds=30) and engine.snapshot is not None:
                logger.warning(f'Execution service ({account}) heartbeat stale/missing')

            now = time.time()
            if now - last_heartbeat >= HEARTBEAT_INTERVAL_SECONDS:
                pub.heartbeat(f'sizing.{account}')
                last_heartbeat = now
    except KeyboardInterrupt:
        logger.info('Stopped by user.')
    finally:
        pub.close()
        sub.close()


if __name__ == '__main__':
    run()
