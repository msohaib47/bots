"""
Tracks open equity positions and manages stop-loss / profit-target.
"""
import csv
import json
import logging
import os
from datetime import datetime, timezone

from config import STATE_FILE, TRADES_LOG, STOP_LOSS_PCT, PROFIT_TARGET_PCT

logger = logging.getLogger(__name__)


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


# ── State persistence ─────────────────────────────────────────────────────────

def load_state() -> dict:
    """
    Returns dict keyed by symbol:
    {
      'TSLA': {
        'qty':           5.0,        # shares held
        'entry_price':   250.00,     # avg fill price
        'stop_price':    245.00,     # stop-loss trigger
        'target_price':  260.00,     # profit target trigger
        'high_water':    250.00,     # highest price seen since entry
        'trailing_active': False,
        'order_id':      '...',
        'opened_at':     '...',
        'dollars_in':    1250.00,    # dollars invested
      }
    }
    """
    if not os.path.exists(STATE_FILE):
        return {}
    with open(STATE_FILE) as f:
        return json.load(f)


def save_state(state: dict):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)


def log_trade(action: str, symbol: str, qty: float, price: float, pnl: float = 0.0, reason: str = ''):
    exists = os.path.exists(TRADES_LOG)
    with open(TRADES_LOG, 'a', newline='') as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(['timestamp', 'action', 'symbol', 'qty', 'price', 'pnl', 'reason'])
        w.writerow([_now_iso(), action, symbol, qty, price, round(pnl, 2), reason])


# ── Position management ────────────────────────────────────────────────────────

def register_open(state: dict, symbol: str, qty: float, fill_price: float,
                  dollars_in: float, order_id: str):
    stop   = fill_price * (1 - STOP_LOSS_PCT)
    target = fill_price * (1 + PROFIT_TARGET_PCT)
    state[symbol] = {
        'qty':              qty,
        'entry_price':      fill_price,
        'stop_price':       round(stop, 4),
        'target_price':     round(target, 4),
        'high_water':       fill_price,
        'trailing_active':  False,
        'order_id':         order_id,
        'opened_at':        _now_iso(),
        'dollars_in':       dollars_in,
    }
    log_trade('OPEN', symbol, qty, fill_price, reason='Entry signal')
    logger.info(f'Position registered: {symbol} {qty:.4f}sh @ ${fill_price:.2f} | stop=${stop:.2f} target=${target:.2f}')


def remove_position(state: dict, symbol: str):
    state.pop(symbol, None)


def has_position(state: dict, symbol: str) -> bool:
    return symbol in state and state[symbol].get('qty', 0) > 0


# ── Stop / target checks ──────────────────────────────────────────────────────

def check_exits(state: dict, current_prices: dict) -> list[str]:
    """
    Check each position against stop-loss and profit-target.
    Returns list of symbols that should be closed.
    """
    to_close = []
    for symbol, pos in state.items():
        current = current_prices.get(symbol)
        if current is None:
            logger.warning(f'No current price for {symbol}, skipping')
            continue

        entry  = pos['entry_price']
        pct    = (current - entry) / entry

        # Update high water
        if current > pos['high_water']:
            pos['high_water'] = current

        # Activate trailing stop at profit target (+4%)
        if not pos['trailing_active'] and current >= pos['target_price']:
            pos['trailing_active'] = True
            new_stop = current * (1 - STOP_LOSS_PCT)
            pos['stop_price'] = round(new_stop, 4)
            logger.info(f'{symbol}: trailing stop ACTIVATED @ ${pos["stop_price"]:.2f} | profit={pct:.1%}')

        # Raise trailing stop
        if pos['trailing_active']:
            new_stop = current * (1 - STOP_LOSS_PCT)
            if new_stop > pos['stop_price']:
                pos['stop_price'] = round(new_stop, 4)
                logger.info(f'{symbol}: trailing stop raised to ${pos["stop_price"]:.2f}')

        # Check stop
        if current <= pos['stop_price']:
            pnl = (current - entry) * pos['qty']
            reason = 'Trailing stop' if pos['trailing_active'] else 'Stop loss'
            logger.info(f'{symbol}: {reason} hit | current=${current:.2f} stop=${pos["stop_price"]:.2f} | PnL=${pnl:+.2f} ({pct:+.1%})')
            log_trade('CLOSE', symbol, pos['qty'], current, pnl, reason)
            to_close.append(symbol)

    return to_close
