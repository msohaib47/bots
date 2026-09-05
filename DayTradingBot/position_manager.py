"""
Tracks open option positions and manages:
  - Stop loss: close if value drops 30% below entry cost
  - Trailing stop: once +50% profit, stop trails 10% below the high-water mark
"""
import json
import os
import sys
import logging
import csv
from datetime import datetime, timezone

# Add parent directory to path to enable imports from common modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (STATE_FILE, TRADES_LOG, STOP_LOSS_PCT,
                    PROFIT_TRAIL_TRIGGER, TRAIL_WIGGLE, MAX_CONTRACTS)

logger = logging.getLogger(__name__)


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


# ── State persistence ─────────────────────────────────────────────────────────

def load_state() -> dict:
    """
    Returns dict keyed by option symbol:
    {
      'SPYXXXXCXXXXXX': {
        'underlying': 'SPY',
        'type': 'call',
        'contracts': 2,
        'entry_cost': 1.50,      # per-share price paid (option premium)
        'high_water': 1.50,      # highest value seen since entry
        'trailing_active': False,
        'stop_price': None,      # per-share stop trigger
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


def log_trade(action: str, symbol: str, underlying: str, opt_type: str,
              contracts: int, price: float, pnl: float = 0, reason: str = ''):
    exists = os.path.exists(TRADES_LOG)
    with open(TRADES_LOG, 'a', newline='') as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(['timestamp', 'action', 'symbol', 'underlying', 'type',
                        'contracts', 'price', 'pnl', 'reason'])
        w.writerow([_now_iso(), action, symbol, underlying, opt_type,
                    contracts, price, round(pnl, 2), reason])


# ── Position registration ─────────────────────────────────────────────────────

def register_open(state: dict, contract: dict, qty: int, filled_price: float, order_id: str):
    sym = contract['symbol']
    state[sym] = {
        'underlying':       contract['underlying'],
        'type':             contract['type'],
        'contracts':        qty,
        'entry_cost':       filled_price,
        'high_water':       filled_price,
        'trailing_active':  False,
        'stop_price':       filled_price * (1 - STOP_LOSS_PCT),
        'order_id':         order_id,
        'opened_at':        _now_iso(),
    }
    log_trade('OPEN', sym, contract['underlying'], contract['type'],
              qty, filled_price, reason='Entry')
    logger.info(f'Position registered: {sym} {qty}x @ ${filled_price:.2f} | stop=${state[sym]["stop_price"]:.2f}')


# ── Stop management ───────────────────────────────────────────────────────────

def check_and_update_stops(state: dict, current_prices: dict) -> list[str]:
    """
    Check each position against its stop price.
    Update trailing stops if profit >= PROFIT_TRAIL_TRIGGER.
    Returns list of option symbols that should be closed.
    """
    to_close = []

    for sym, pos in state.items():
        current = current_prices.get(sym)
        if current is None:
            logger.warning(f'No current price for {sym}, skipping stop check')
            continue

        entry     = pos['entry_cost']
        high      = pos['high_water']
        stop      = pos['stop_price']
        trailing  = pos['trailing_active']
        pct_gain  = (current - entry) / entry

        # Update high water mark
        if current > high:
            pos['high_water'] = current

            # Update trailing stop if active
            if trailing:
                new_stop = current * (1 - TRAIL_WIGGLE)
                if new_stop > pos['stop_price']:
                    pos['stop_price'] = round(new_stop, 4)
                    logger.info(f'{sym}: trailing stop raised to ${pos["stop_price"]:.2f} (high=${current:.2f})')

        # Activate trailing stop when profit hits trigger
        if not trailing and pct_gain >= PROFIT_TRAIL_TRIGGER:
            pos['trailing_active'] = True
            pos['stop_price'] = round(current * (1 - TRAIL_WIGGLE), 4)
            logger.info(f'{sym}: trailing stop ACTIVATED at ${pos["stop_price"]:.2f} | profit={pct_gain:.1%}')

        # Check stop
        if current <= pos['stop_price']:
            pnl = (current - entry) * pos['contracts'] * 100
            reason = f'Trailing stop hit' if trailing else f'Stop loss hit'
            logger.info(f'{sym}: {reason} | current=${current:.2f} stop=${pos["stop_price"]:.2f} | PnL=${pnl:.2f}')
            log_trade('CLOSE', sym, pos['underlying'], pos['type'],
                      pos['contracts'], current, pnl, reason=reason)
            try:
                from common.notifier import notify
                action = 'STOP_LOSS' if not trailing else 'SELL'
                notify(action, sym, f'${current:.2f}', f'{reason} | P&L={pct_gain:+.1%} (${pnl:+.2f})', bot='DayTradingBot')
            except Exception:
                pass
            to_close.append(sym)

    return to_close


# ── Position queries ──────────────────────────────────────────────────────────

def get_open_underlyings(state: dict) -> set[str]:
    return {pos['underlying'] for pos in state.values()}


def count_contracts_for(state: dict, underlying: str) -> int:
    return sum(p['contracts'] for p in state.values() if p['underlying'] == underlying)


def has_position(state: dict, underlying: str, opt_type: str) -> bool:
    return any(
        p['underlying'] == underlying and p['type'].lower() == opt_type.lower()
        for p in state.values()
    )


def remove_position(state: dict, symbol: str):
    state.pop(symbol, None)
