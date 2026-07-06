"""Persistent state for each ticker's wheel position."""
import json
import os
import sys
import logging
from datetime import datetime

# Add parent directory to path to enable imports from common modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import STATE_FILE, TICKERS

logger = logging.getLogger(__name__)

# Valid states in the wheel cycle
IDLE        = 'IDLE'         # No position, ready to sell CSP
CSP_PENDING = 'CSP_PENDING'  # CSP order submitted, waiting for fill
CSP_OPEN    = 'CSP_OPEN'     # CSP filled, monitoring for expiry/assignment
ASSIGNED    = 'ASSIGNED'     # Assigned on put — own 100 shares, need to sell CC
CC_PENDING  = 'CC_PENDING'   # CC order submitted, waiting for fill
CC_OPEN     = 'CC_OPEN'      # CC filled, monitoring for expiry/callaway


def _default_ticker_state() -> dict:
    return {
        'state': IDLE,
        'cost_basis': None,         # Per-share cost after subtracting premiums
        'shares_qty': 0,

        'csp_symbol': None,         # OCC symbol of the put we sold
        'csp_order_id': None,
        'csp_strike': None,
        'csp_expiry': None,
        'csp_premium': None,        # Premium collected per share

        'cc_symbol': None,
        'cc_order_id': None,
        'cc_strike': None,
        'cc_expiry': None,
        'cc_premium': None,

        'total_premium_collected': 0.0,
        'total_realized_pnl': 0.0,
        'cycles_completed': 0,
        'last_updated': None,
    }


def load() -> dict:
    if not os.path.exists(STATE_FILE):
        return {t: _default_ticker_state() for t in TICKERS}
    with open(STATE_FILE, 'r') as f:
        data = json.load(f)
    # Ensure all tickers are present
    for t in TICKERS:
        if t not in data:
            data[t] = _default_ticker_state()
    return data


def save(state: dict):
    for t in state:
        state[t]['last_updated'] = datetime.utcnow().isoformat()
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)
    logger.debug(f'State saved to {STATE_FILE}')


def reset_ticker(state: dict, ticker: str):
    """Reset a ticker back to IDLE, preserving cumulative stats."""
    prev = state.get(ticker, {})
    state[ticker] = _default_ticker_state()
    state[ticker]['total_premium_collected'] = prev.get('total_premium_collected', 0)
    state[ticker]['total_realized_pnl'] = prev.get('total_realized_pnl', 0)
    state[ticker]['cycles_completed'] = prev.get('cycles_completed', 0)
