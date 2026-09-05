"""
Core wheel strategy logic.
Each function handles one state transition for a single ticker.
"""
import logging
import os
import sys
from datetime import date

# Add parent directory to path to enable imports from common modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import alpaca
import state as st
from config import CSP_OTM_PCT, CC_OTM_PCT

# Set to True to log actions without placing real orders
DRY_RUN = False

logger = logging.getLogger(__name__)


# ── Option selection helpers ────────────────────────────────────────────────────

def select_put(ticker: str, spot: float) -> dict | None:
    """
    Find the best put to sell:
    - Strike at or below spot × (1 - CSP_OTM_PCT)
    - Closest to target strike, with a non-zero bid
    - Expiry as close to DTE_MAX as possible
    """
    target_strike = round(spot * (1 - CSP_OTM_PCT), 2)
    strike_min = target_strike * 0.7
    strike_max = spot * 0.99  # must be below spot

    contracts = alpaca.get_option_chain(ticker, 'put', strike_min=strike_min, strike_max=strike_max)
    if not contracts:
        logger.warning(f'{ticker}: No put contracts found in range {strike_min:.2f}–{strike_max:.2f}')
        return None

    snapshots = alpaca.get_option_snapshots(ticker, 'put')

    best = None
    best_score = float('inf')

    for c in contracts:
        sym = c.get('symbol')
        strike = float(c.get('strike_price', 0))
        expiry = c.get('expiration_date', '')

        snap = snapshots.get(sym, {})
        quote = snap.get('latestQuote', {})
        bid = float(quote.get('bp', 0) or 0)
        ask = float(quote.get('ap', 0) or 0)

        # Need a tradeable premium
        if bid <= 0 and ask <= 0:
            continue

        premium = bid if bid > 0 else ask * 0.5

        # Prefer strikes closest to target, then furthest expiry (more premium)
        distance = abs(strike - target_strike)
        if distance < best_score:
            best_score = distance
            best = {
                'symbol': sym,
                'strike': strike,
                'expiry': expiry,
                'bid': bid,
                'ask': ask,
                'premium': premium,
                'mid': (bid + ask) / 2 if bid > 0 and ask > 0 else max(bid, ask),
            }

    if best:
        logger.info(f'{ticker}: Selected put {best["symbol"]} strike={best["strike"]} exp={best["expiry"]} bid={best["bid"]} ask={best["ask"]}')
    else:
        logger.warning(f'{ticker}: No tradeable put found (no bid/ask)')
    return best


def select_call(ticker: str, cost_basis: float) -> dict | None:
    """
    Find the best call to sell:
    - Strike at or above cost_basis × (1 + CC_OTM_PCT)
    - With a non-zero bid
    """
    target_strike = cost_basis * (1 + CC_OTM_PCT)
    spot = alpaca.get_stock_price(ticker) or cost_basis
    strike_min = max(cost_basis, spot * 0.99)   # at or above spot
    strike_max = target_strike * 1.5

    contracts = alpaca.get_option_chain(ticker, 'call', strike_min=strike_min, strike_max=strike_max)
    if not contracts:
        logger.warning(f'{ticker}: No call contracts found above cost_basis {cost_basis:.2f}')
        return None

    snapshots = alpaca.get_option_snapshots(ticker, 'call')

    best = None
    best_score = float('inf')

    for c in contracts:
        sym = c.get('symbol')
        strike = float(c.get('strike_price', 0))

        snap = snapshots.get(sym, {})
        quote = snap.get('latestQuote', {})
        bid = float(quote.get('bp', 0) or 0)
        ask = float(quote.get('ap', 0) or 0)

        if bid <= 0 and ask <= 0:
            continue

        distance = abs(strike - target_strike)
        if distance < best_score:
            best_score = distance
            best = {
                'symbol': sym,
                'strike': strike,
                'expiry': c.get('expiration_date', ''),
                'bid': bid,
                'ask': ask,
                'mid': (bid + ask) / 2 if bid > 0 and ask > 0 else max(bid, ask),
            }

    if best:
        logger.info(f'{ticker}: Selected call {best["symbol"]} strike={best["strike"]} exp={best["expiry"]} bid={best["bid"]} ask={best["ask"]}')
    else:
        logger.warning(f'{ticker}: No tradeable call found')
    return best


def _limit_price(option: dict) -> float:
    """Use mid price, floored at $0.05."""
    mid = option.get('mid', 0)
    bid = option.get('bid', 0)
    # If mid is available use it; otherwise use bid
    price = mid if mid > 0 else bid
    return max(round(price, 2), 0.05)


# ── State handlers ─────────────────────────────────────────────────────────────

def handle_idle(ticker: str, ts: dict) -> dict:
    """Step 1: Sell a cash-secured put."""
    spot = alpaca.get_stock_price(ticker)
    if not spot:
        logger.warning(f'{ticker}: Cannot get price, skipping')
        return ts

    obp = alpaca.get_options_buying_power()
    csp = select_put(ticker, spot)
    if not csp:
        return ts

    required = csp['strike'] * 100
    if obp < required:
        logger.warning(f'{ticker}: Not enough options buying power (have ${obp:.0f}, need ${required:.0f})')
        return ts

    lp = _limit_price(csp)

    if DRY_RUN:
        logger.info(f'[DRY RUN] {ticker}: Would sell put {csp["symbol"]} strike={csp["strike"]} exp={csp["expiry"]} premium=${lp}')
        return ts

    order = alpaca.place_option_order(csp['symbol'], 'sell', 1, lp)
    if not order:
        return ts

    ts.update({
        'state': st.CSP_PENDING,
        'csp_symbol': csp['symbol'],
        'csp_order_id': order['id'],
        'csp_strike': csp['strike'],
        'csp_expiry': csp['expiry'],
        'csp_premium': lp,
    })
    logger.info(f'{ticker}: CSP order placed -> {st.CSP_PENDING} | strike={csp["strike"]} exp={csp["expiry"]} premium=${lp}')
    from common.notifier import notify
    notify('OPEN', ticker, f'${lp}', f'Sold put strike=${csp["strike"]} exp={csp["expiry"]} premium=${lp}', bot='WheelBot')
    return ts


def handle_csp_pending(ticker: str, ts: dict) -> dict:
    """Check if CSP order filled."""
    order = alpaca.get_order(ts['csp_order_id'])
    if not order:
        return ts

    status = order.get('status')
    if status == 'filled':
        filled_price = float(order.get('filled_avg_price') or ts['csp_premium'])
        ts['csp_premium'] = filled_price
        ts['total_premium_collected'] += filled_price * 100
        ts['state'] = st.CSP_OPEN
        logger.info(f'{ticker}: CSP filled @ ${filled_price:.2f} | total premium: ${ts["total_premium_collected"]:.2f}')
    elif status in ('canceled', 'expired', 'rejected'):
        logger.info(f'{ticker}: CSP order {status}, resetting to IDLE')
        st.reset_ticker({ticker: ts}, ticker)
        ts['state'] = st.IDLE

    return ts


def handle_csp_open(ticker: str, ts: dict) -> dict:
    """
    Monitor open CSP.
    - Expired worthless → collect premium, go back to IDLE
    - Assigned (own shares) → move to ASSIGNED
    - Still open → do nothing
    """
    today = str(date.today())
    expiry = ts.get('csp_expiry', '')

    # Check if we now own shares (assignment happened)
    pos = alpaca.get_position(ticker)
    if pos and int(float(pos.get('qty', 0))) >= 100:
        qty = int(float(pos['qty']))
        avg_cost = float(pos.get('avg_entry_price', ts['csp_strike']))
        # True cost basis = strike price - total premiums collected per share
        premiums_per_share = ts['total_premium_collected'] / 100
        cost_basis = avg_cost - premiums_per_share
        ts.update({
            'state': st.ASSIGNED,
            'shares_qty': qty,
            'cost_basis': round(cost_basis, 4),
        })
        logger.info(f'{ticker}: ASSIGNED! {qty} shares @ avg {avg_cost:.2f} | cost basis after premiums: ${cost_basis:.2f}')
        from common.notifier import notify
        notify('ASSIGNED', ticker, f'${avg_cost:.2f}', f'{qty} shares assigned | cost basis=${cost_basis:.2f}', bot='WheelBot')
        return ts

    # Check if option expired worthless (past expiry date)
    if today > expiry:
        logger.info(f'{ticker}: CSP expired worthless on {expiry} — premium kept! Resetting to IDLE.')
        st.reset_ticker({ticker: ts}, ticker)
        ts['state'] = st.IDLE
        ts['total_premium_collected'] = ts.get('total_premium_collected', 0)  # preserved by reset_ticker
        return ts

    # Check order status
    order = alpaca.get_order(ts['csp_order_id'])
    if order:
        status = order.get('status')
        if status == 'expired':
            logger.info(f'{ticker}: CSP order expired worthless — keeping premium')
            prev_premium = ts['total_premium_collected']
            st.reset_ticker({ticker: ts}, ticker)
            ts['state'] = st.IDLE
            ts['total_premium_collected'] = prev_premium
    return ts


def handle_assigned(ticker: str, ts: dict) -> dict:
    """Step 2: We own 100 shares — sell a covered call."""
    cost_basis = ts.get('cost_basis') or ts.get('csp_strike', 0)
    cc = select_call(ticker, cost_basis)
    if not cc:
        return ts

    lp = _limit_price(cc)

    if DRY_RUN:
        logger.info(f'[DRY RUN] {ticker}: Would sell call {cc["symbol"]} strike={cc["strike"]} exp={cc["expiry"]} premium=${lp}')
        return ts

    order = alpaca.place_option_order(cc['symbol'], 'sell', 1, lp)
    if not order:
        return ts

    ts.update({
        'state': st.CC_PENDING,
        'cc_symbol': cc['symbol'],
        'cc_order_id': order['id'],
        'cc_strike': cc['strike'],
        'cc_expiry': cc['expiry'],
        'cc_premium': lp,
    })
    logger.info(f'{ticker}: CC order placed -> {st.CC_PENDING} | strike={cc["strike"]} exp={cc["expiry"]} premium=${lp}')
    return ts


def handle_cc_pending(ticker: str, ts: dict) -> dict:
    """Check if CC order filled."""
    order = alpaca.get_order(ts['cc_order_id'])
    if not order:
        return ts

    status = order.get('status')
    if status == 'filled':
        filled_price = float(order.get('filled_avg_price') or ts['cc_premium'])
        ts['cc_premium'] = filled_price
        ts['total_premium_collected'] += filled_price * 100
        ts['state'] = st.CC_OPEN
        logger.info(f'{ticker}: CC filled @ ${filled_price:.2f} | total premium: ${ts["total_premium_collected"]:.2f}')
    elif status in ('canceled', 'expired', 'rejected'):
        logger.info(f'{ticker}: CC order {status}, back to ASSIGNED')
        ts['state'] = st.ASSIGNED

    return ts


def handle_cc_open(ticker: str, ts: dict) -> dict:
    """
    Monitor open CC.
    - Expired worthless → keep shares + premium, sell another CC (→ ASSIGNED)
    - Called away → full cycle complete, back to IDLE
    - Still open → do nothing
    """
    today = str(date.today())
    expiry = ts.get('cc_expiry', '')

    # Check if shares are gone (called away)
    pos = alpaca.get_position(ticker)
    shares_remain = int(float(pos.get('qty', 0))) if pos else 0

    if shares_remain < 100:
        # Shares were called away
        strike = ts.get('cc_strike', 0)
        csp_strike = ts.get('csp_strike', 0)
        total_premium = ts['total_premium_collected']
        # P&L = (cc_strike - csp_strike) × 100 + all premiums
        realized_pnl = (strike - csp_strike) * 100 + total_premium
        ts['total_realized_pnl'] = ts.get('total_realized_pnl', 0) + realized_pnl
        ts['cycles_completed'] = ts.get('cycles_completed', 0) + 1
        from common.notifier import notify
        notify('CALLED_AWAY', ticker, f'${strike}', f'Called away at ${strike} | cycle P&L=${realized_pnl:.2f}', bot='WheelBot')
        logger.info(
            f'{ticker}: CALLED AWAY at ${strike} | '
            f'Cycle P&L: ${realized_pnl:.2f} | '
            f'Cycles completed: {ts["cycles_completed"]}'
        )
        prev_pnl = ts['total_realized_pnl']
        prev_cycles = ts['cycles_completed']
        st.reset_ticker({ticker: ts}, ticker)
        ts['state'] = st.IDLE
        ts['total_realized_pnl'] = prev_pnl
        ts['cycles_completed'] = prev_cycles
        return ts

    # Check if CC expired worthless
    if today > expiry:
        logger.info(f'{ticker}: CC expired worthless — keeping shares and premium, selling new CC')
        ts['state'] = st.ASSIGNED  # sell another CC next run
        ts['cc_symbol'] = None
        ts['cc_order_id'] = None
        return ts

    # Check order status
    order = alpaca.get_order(ts['cc_order_id'])
    if order and order.get('status') == 'expired':
        ts['state'] = st.ASSIGNED
        logger.info(f'{ticker}: CC order expired, re-entering ASSIGNED to sell new CC')
    return ts


# ── Main dispatcher ─────────────────────────────────────────────────────────────

HANDLERS = {
    st.IDLE:        handle_idle,
    st.CSP_PENDING: handle_csp_pending,
    st.CSP_OPEN:    handle_csp_open,
    st.ASSIGNED:    handle_assigned,
    st.CC_PENDING:  handle_cc_pending,
    st.CC_OPEN:     handle_cc_open,
}


def run_ticker(ticker: str, wheel_state: dict):
    """Run one cycle of the wheel for a single ticker."""
    ts = wheel_state.get(ticker, {})
    current_state = ts.get('state', st.IDLE)
    handler = HANDLERS.get(current_state)

    if not handler:
        logger.error(f'{ticker}: Unknown state {current_state}')
        return

    logger.info(f'{ticker}: State={current_state}')
    try:
        wheel_state[ticker] = handler(ticker, ts)
    except Exception as e:
        logger.error(f'{ticker}: Error in {current_state} handler: {e}', exc_info=True)
