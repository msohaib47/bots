"""
Tracks open vertical spread positions and manages:
  - Profit target: close at PROFIT_TARGET_PCT of max possible profit (width - debit)
  - Stop loss: close if spread value drops STOP_LOSS_PCT below entry debit
Each position is one spread (long leg + short leg) keyed by 'long_symbol/short_symbol'.
"""
import json
import os
import sys
import logging
import csv
from datetime import datetime, timezone

# Add parent directory to path to enable imports from common modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import STATE_FILE, TRADES_LOG, STOP_LOSS_PCT, PROFIT_TARGET_PCT

logger = logging.getLogger(__name__)


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _key(long_symbol: str, short_symbol: str) -> str:
    return f'{long_symbol}/{short_symbol}'


# ── State persistence ─────────────────────────────────────────────────────────

def load_state() -> dict:
    """
    Returns dict keyed by 'long_symbol/short_symbol':
    {
      'SPY...C600/SPY...C602': {
        'underlying': 'SPY',
        'type': 'call',
        'expiry': '2026-07-06',
        'long_symbol': '...', 'short_symbol': '...',
        'long_strike': 600.0, 'short_strike': 602.0,
        'width': 2.0,
        'contracts': 1,
        'entry_debit': 0.85,     # per-spread price paid
        'max_profit': 1.15,      # width - entry_debit
        'high_water': 0.85,      # highest spread value seen since entry
        'stop_price': 0.4250,    # spread value that triggers stop loss
        'profit_target_price': 1.425,  # spread value that triggers profit take
        'order_id': '...',
        'opened_at': '...',
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


def log_trade(action: str, spread: dict, contracts: int, price: float, pnl: float = 0, reason: str = ''):
    exists = os.path.exists(TRADES_LOG)
    with open(TRADES_LOG, 'a', newline='') as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(['timestamp', 'action', 'underlying', 'type', 'long_symbol', 'short_symbol',
                        'long_strike', 'short_strike', 'contracts', 'price', 'pnl', 'reason'])
        w.writerow([_now_iso(), action, spread['underlying'], spread['type'],
                    spread['long_symbol'], spread['short_symbol'],
                    spread['long_strike'], spread['short_strike'],
                    contracts, price, round(pnl, 2), reason])


# ── Position registration ─────────────────────────────────────────────────────

def register_open(state: dict, spread: dict, qty: int, filled_debit: float, order_id: str):
    key = _key(spread['long_symbol'], spread['short_symbol'])
    max_profit = round(spread['width'] - filled_debit, 4)
    state[key] = {
        'underlying':          spread['underlying'],
        'type':                spread['type'],
        'expiry':              spread['expiry'],
        'long_symbol':         spread['long_symbol'],
        'short_symbol':        spread['short_symbol'],
        'long_strike':         spread['long_strike'],
        'short_strike':        spread['short_strike'],
        'width':               spread['width'],
        'contracts':           qty,
        'entry_debit':         filled_debit,
        'max_profit':          max_profit,
        'high_water':          filled_debit,
        'stop_price':          round(filled_debit * (1 - STOP_LOSS_PCT), 4),
        'profit_target_price': round(filled_debit + max_profit * PROFIT_TARGET_PCT, 4),
        'order_id':            order_id,
        'opened_at':           _now_iso(),
    }
    log_trade('OPEN', spread, qty, filled_debit, reason='Entry')
    logger.info(f'Spread registered: {key} {qty}x @ debit ${filled_debit:.2f} | '
                f'stop=${state[key]["stop_price"]:.2f} target=${state[key]["profit_target_price"]:.2f}')


# ── Stop / profit-target management ───────────────────────────────────────────

def check_and_update_exits(state: dict, current_values: dict) -> list[str]:
    """
    Check each spread's current value against stop and profit-target prices.
    Returns list of state keys that should be closed.
    """
    to_close = []

    for key, pos in state.items():
        current = current_values.get(key)
        if current is None:
            logger.warning(f'No current value for {key}, skipping exit check')
            continue

        entry = pos['entry_debit']

        if current > pos['high_water']:
            pos['high_water'] = current

        if current >= pos['profit_target_price']:
            pnl = (current - entry) * pos['contracts'] * 100
            logger.info(f'{key}: PROFIT TARGET hit | current=${current:.2f} target=${pos["profit_target_price"]:.2f} | PnL=${pnl:.2f}')
            to_close.append((key, 'Profit target hit', current, pnl))
        elif current <= pos['stop_price']:
            pnl = (current - entry) * pos['contracts'] * 100
            logger.info(f'{key}: STOP LOSS hit | current=${current:.2f} stop=${pos["stop_price"]:.2f} | PnL=${pnl:.2f}')
            to_close.append((key, 'Stop loss hit', current, pnl))

    result_keys = []
    for key, reason, current, pnl in to_close:
        pos = state[key]
        spread_like = {
            'underlying': pos['underlying'], 'type': pos['type'],
            'long_symbol': pos['long_symbol'], 'short_symbol': pos['short_symbol'],
            'long_strike': pos['long_strike'], 'short_strike': pos['short_strike'],
        }
        log_trade('CLOSE', spread_like, pos['contracts'], current, pnl, reason=reason)
        try:
            from common.notifier import notify
            pct = (current - pos['entry_debit']) / pos['entry_debit'] if pos['entry_debit'] else 0
            notify('SELL', key, f'${current:.2f}', f'{reason} | P&L={pct:+.1%} (${pnl:+.2f})', bot='VerticalSpreadBot')
        except Exception:
            pass
        result_keys.append(key)

    return result_keys


# ── Position queries ──────────────────────────────────────────────────────────

def get_open_underlyings(state: dict) -> set[str]:
    return {pos['underlying'] for pos in state.values()}


def has_position(state: dict, underlying: str) -> bool:
    return any(p['underlying'] == underlying for p in state.values())


def remove_position(state: dict, key: str):
    state.pop(key, None)
