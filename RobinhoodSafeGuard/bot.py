"""
RobinhoodSafeGuard — two-phase stop-loss guardian for open option positions.

For each open LONG option position on the individual investing account:

  Phase 1 (profit < 100%):
    - No stop exists → place one at $20 below current mark price
    - Stop exists    → leave it alone (never updated until profit threshold met)

  Phase 2 (profit ≥ 100%):
    - Recalculate stop as current_mark × 80% (20% below current price)
    - If new stop > existing stop → cancel old and place higher one (trail up)
    - Never move stop down

DRY_RUN=true (default) logs intended actions without placing real orders.
Set DRY_RUN=false in .env to go live.

Usage:
  python bot.py           -- single tick (called by cron every 5 min)
  python bot.py --status  -- show all positions and stop levels; no order changes
"""
import json
import logging
import os
import sys
from datetime import datetime, timezone, timedelta

import robinhood
from config import DRY_RUN, LOG_FILE, STATE_FILE, INITIAL_STOP_DISTANCE, TRAILING_STOP_PCT, PROFIT_THRESHOLD

os.makedirs('logs', exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


# ── Time helpers ───────────────────────────────────────────────────────────────

def et_now() -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=4)


def is_market_open() -> bool:
    now = et_now()
    if now.weekday() >= 5:
        return False
    t = now.strftime('%H:%M')
    return '09:30' <= t <= '16:00'


# ── State (tracks current stop and order id per option) ───────────────────────

def load_state() -> dict:
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE) as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f'Cannot load state: {e}')
    return {}


def save_state(state: dict):
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        logger.error(f'Cannot save state: {e}')


# ── Stop price logic ───────────────────────────────────────────────────────────

def profit_pct(mark: float, avg_open: float) -> float:
    if avg_open <= 0:
        return 0.0
    return (mark - avg_open) / avg_open


def calc_trailing_stop(mark: float) -> float:
    """20% below current mark price (Phase 2 trailing)."""
    return round(mark * (1 - TRAILING_STOP_PCT), 2)


def calc_initial_stop(mark: float) -> float:
    """Flat $20 below current mark price (Phase 1 initial)."""
    return round(mark - INITIAL_STOP_DISTANCE, 2)


# ── Notifications ──────────────────────────────────────────────────────────────

def _notify(label: str, mark: float, msg: str):
    try:
        from notifier import notify
        notify('INFO', label, f'${mark:.2f}', msg, bot='SafeGuard')
    except Exception:
        pass


# ── Main run ───────────────────────────────────────────────────────────────────

def run():
    logger.info(f'--- RobinhoodSafeGuard tick | ET {et_now().strftime("%H:%M")} '
                f'| mode={"DRY_RUN" if DRY_RUN else "LIVE"} ---')

    if not is_market_open():
        logger.info('Market closed — nothing to do.')
        return

    if not robinhood.login():
        logger.error('Login failed — aborting tick.')
        return

    positions = robinhood.get_open_option_positions()
    if not positions:
        logger.info('No open long option positions.')
        return

    open_stops = robinhood.get_open_stop_orders()
    state      = load_state()
    now_str    = datetime.now(timezone.utc).isoformat()
    active_ids = set()

    for pos in positions:
        opt_id = pos['option_id']
        active_ids.add(opt_id)
        label    = robinhood._label(pos)
        mark     = pos['mark_price']
        avg_open = pos['avg_open_price']

        if mark <= 0:
            logger.warning(f'{label}: mark price is $0 — skipping (option may be worthless)')
            continue

        pct      = profit_pct(mark, avg_open)
        trailing = pct >= PROFIT_THRESHOLD
        phase    = f'Phase 2 trailing ({pct*100:.0f}% profit)' if trailing else f'Phase 1 initial ({pct*100:.0f}% profit)'

        existing = robinhood.find_stop_for_option(pos['option_url'], open_stops)

        # Quantity mismatch means the position was partially closed — replace stop.
        qty_mismatch = (
            existing is not None
            and int(existing['quantity']) != int(pos['quantity'])
        )

        if existing is None:
            # No stop at all — place initial stop at $20 below mark
            stop_price = calc_initial_stop(mark)
            logger.info(f'{label}: no stop — placing @ ${stop_price:.2f} '
                        f'(mark=${mark:.2f}, ${INITIAL_STOP_DISTANCE:.0f} below) [{phase}]')
            order = robinhood.place_stop_loss(pos, stop_price)
            order_id = order['id'] if order else None
            _notify(label, mark, f'Initial stop placed @ ${stop_price:.2f} (${INITIAL_STOP_DISTANCE:.0f} below mark)')

        elif qty_mismatch:
            # Position partially closed — replace stop with correct quantity
            stop_price = calc_initial_stop(mark) if not trailing else calc_trailing_stop(mark)
            logger.info(f'{label}: qty changed ({int(existing["quantity"])} → {int(pos["quantity"])}) '
                        f'— replacing stop @ ${stop_price:.2f}')
            robinhood.cancel_order(existing['order_id'])
            order = robinhood.place_stop_loss(pos, stop_price)
            order_id = order['id'] if order else None
            _notify(label, mark, f'Stop updated for new qty={int(pos["quantity"])} @ ${stop_price:.2f}')

        elif trailing:
            # Profit ≥ 100% — trail stop up based on current price, never down
            new_stop = calc_trailing_stop(mark)
            if new_stop > existing['stop_price'] + 0.01:
                logger.info(f'{label}: trailing ${existing["stop_price"]:.2f} → ${new_stop:.2f} '
                            f'(mark=${mark:.2f}) [{phase}]')
                robinhood.cancel_order(existing['order_id'])
                order = robinhood.place_stop_loss(pos, new_stop)
                order_id = order['id'] if order else existing['order_id']
                _notify(label, mark, f'Trailing stop ${existing["stop_price"]:.2f} → ${new_stop:.2f}')
            else:
                logger.info(f'{label}: stop OK @ ${existing["stop_price"]:.2f} '
                            f'(mark=${mark:.2f}, would be ${new_stop:.2f}) [{phase}]')
                order_id = existing['order_id']

        else:
            # Profit < 100% — leave the initial stop untouched
            logger.info(f'{label}: stop held @ ${existing["stop_price"]:.2f} '
                        f'(mark=${mark:.2f}, profit threshold not met) [{phase}]')
            order_id = existing['order_id']

        state[opt_id] = {
            'symbol':        pos['symbol'],
            'description':   label,
            'current_stop':  existing['stop_price'] if existing else calc_initial_stop(mark),
            'stop_order_id': order_id,
            'quantity':      pos['quantity'],
            'last_checked':  now_str,
        }

    # Prune state entries for positions no longer held
    for opt_id in list(state.keys()):
        if opt_id not in active_ids:
            desc = state[opt_id].get('description', opt_id)
            logger.info(f'Position closed/expired — removing from state: {desc}')
            del state[opt_id]

    save_state(state)
    logger.info('Tick complete.')


# ── Status display (read-only) ─────────────────────────────────────────────────

def print_status():
    if not robinhood.login():
        print('Login failed.')
        return

    positions  = robinhood.get_open_option_positions()
    open_stops = robinhood.get_open_stop_orders()

    print(f'\n{"="*66}')
    print(f'  RobinhoodSafeGuard | ET {et_now().strftime("%H:%M")}')
    print(f'  Mode: {"DRY_RUN" if DRY_RUN else "LIVE"} | '
          f'Initial: ${INITIAL_STOP_DISTANCE:.0f} below | '
          f'Trailing: {TRAILING_STOP_PCT*100:.0f}% below (after {PROFIT_THRESHOLD*100:.0f}% profit)')
    print(f'{"="*66}')

    if not positions:
        print('  No open long option positions.\n')
        return

    for pos in positions:
        label    = robinhood._label(pos)
        mark     = pos['mark_price']
        avg_open = pos['avg_open_price']
        existing = robinhood.find_stop_for_option(pos['option_url'], open_stops)
        pct      = profit_pct(mark, avg_open)
        trailing = pct >= PROFIT_THRESHOLD
        phase    = f'TRAILING {TRAILING_STOP_PCT*100:.0f}%' if trailing else f'INITIAL ${INITIAL_STOP_DISTANCE:.0f}'

        print(f'\n  {label}  ×{int(pos["quantity"])}')
        print(f'    Entry ${avg_open:.2f} → Mark ${mark:.2f}  ({pct*100:+.1f}%)  [{phase}]')

        if existing:
            gap  = mark - existing['stop_price']
            gap_pct = gap / mark * 100
            if trailing:
                new_stop = calc_trailing_stop(mark)
                note = f'will trail ↑ to ${new_stop:.2f}' if new_stop > existing['stop_price'] + 0.01 else 'current'
            else:
                note = 'held (profit threshold not met)'
            print(f'    Stop order  ${existing["stop_price"]:.2f}  ({gap_pct:.1f}% below mark, {note})')
            print(f'    Order ID    {existing["order_id"][:8]}...')
        else:
            initial = calc_initial_stop(mark)
            print(f'    Stop order  NONE — will place @ ${initial:.2f} on next tick')

    print()


if __name__ == '__main__':
    args = sys.argv[1:]
    if '--status' in args:
        print_status()
    else:
        run()
