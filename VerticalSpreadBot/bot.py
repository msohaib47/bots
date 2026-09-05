"""
Vertical Spread Bot — 0DTE debit vertical spreads on SPY, QQQ
Runs every minute during market hours. Strategy: VWAP + EMA crossover + RSI (same signal as DayTradingBot).
  CALL signal -> bull call spread (buy ATM/OTM call, sell call SPREAD_WIDTH higher)
  PUT  signal -> bear put spread  (buy ATM/OTM put,  sell put  SPREAD_WIDTH lower)
Max loss per spread = net debit paid. Profit target 50% of max profit. Stop-loss 50% of debit.

Usage:
  python bot.py              -- single run (called by cron every minute)
  python bot.py --status     -- show open positions and P&L
  python bot.py --close      -- force close all positions now
"""
import logging
import os
import sys
from datetime import datetime, timezone, timedelta

# Add parent directory to path to enable imports from common modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import alpaca
import signals
import position_manager as pm
from config import (SYMBOLS, MAX_CONTRACTS, MAX_DTE, MAX_DEBIT_PER_TRADE, STATE_FILE, LOG_FILE,
                    NO_NEW_ENTRY_TIME, FORCE_CLOSE_TIME)

PDT_FLAG_FILE = 'logs/pdt_blocked.flag'   # written when PDT/approval blocks us; cleared at midnight

# ── Logging ────────────────────────────────────────────────────────────────────
os.makedirs('logs', exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger(__name__)


# ── Time helpers (ET = UTC-4 in summer, UTC-5 in winter) ──────────────────────

def et_now() -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=4)


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


def is_blocked() -> bool:
    """Return True if an order-rejection flag (PDT, options approval level, etc) fired today."""
    if not os.path.exists(PDT_FLAG_FILE):
        return False
    flag_day = datetime.fromtimestamp(os.path.getmtime(PDT_FLAG_FILE)).date()
    if flag_day < et_now().date():
        os.remove(PDT_FLAG_FILE)
        return False
    return True


def set_blocked():
    with open(PDT_FLAG_FILE, 'w') as f:
        f.write(et_now().isoformat())


def should_force_close() -> bool:
    return et_time_str() >= FORCE_CLOSE_TIME


# ── Current spread values (for stop/target management) ────────────────────────

def get_current_spread_values(state: dict) -> dict:
    values = {}
    for key, pos in state.items():
        val = alpaca.get_spread_value(pos['long_symbol'], pos['short_symbol'])
        if val is not None:
            values[key] = val
        else:
            logger.warning(f'No quote for spread {key}')
    return values


def close_spread(key: str, pos: dict, current_value: float | None, reason: str):
    min_credit = max(0.0, (current_value or 0) - 0.03)  # small buffer to help fill
    order = alpaca.close_vertical_spread(pos['long_symbol'], pos['short_symbol'], pos['contracts'], min_credit)
    if not order:
        logger.error(f'Failed to submit close order for {key} ({reason})')
        return False
    logger.info(f'Closing {key} ({reason}) @ min credit ${min_credit:.2f}')
    return True


# ── Force close all positions ──────────────────────────────────────────────────

def close_all_positions(state: dict, reason: str = 'Force close'):
    if not state:
        logger.info('No open positions to close.')
        return
    values = get_current_spread_values(state)
    for key, pos in list(state.items()):
        current = values.get(key)
        if close_spread(key, pos, current, reason):
            spread_like = {
                'underlying': pos['underlying'], 'type': pos['type'],
                'long_symbol': pos['long_symbol'], 'short_symbol': pos['short_symbol'],
                'long_strike': pos['long_strike'], 'short_strike': pos['short_strike'],
            }
            pnl = ((current or 0) - pos['entry_debit']) * pos['contracts'] * 100
            pm.log_trade('CLOSE', spread_like, pos['contracts'], current or 0, pnl, reason=reason)
            pm.remove_position(state, key)
    pm.save_state(state)


# ── Main run ───────────────────────────────────────────────────────────────────

def run():
    logger.info(f'--- VerticalSpreadBot tick | ET {et_time_str()} ---')

    if not is_market_open():
        logger.info('Market closed, nothing to do.')
        return

    state = pm.load_state()

    # ── 1. Force close all 0DTE spreads before EOD ────────────────────────────
    if should_force_close():
        logger.info('EOD force close triggered.')
        close_all_positions(state, 'EOD force close')
        return

    # ── 2. Check stops and profit targets on existing spreads ─────────────────
    if state:
        current_values = get_current_spread_values(state)
        to_close = pm.check_and_update_exits(state, current_values)
        for key in to_close:
            pos = state.get(key, {})
            close_spread(key, pos, current_values.get(key), 'Stop/target')
            pm.remove_position(state, key)
        pm.save_state(state)

    # ── 3. Check for new entry signals ────────────────────────────────────────
    if not can_open_new_position():
        logger.info(f'Past {NO_NEW_ENTRY_TIME} ET — no new entries.')
        return

    if is_blocked():
        logger.info('Order-rejection protection active — managing existing positions only, no new entries today.')
        return

    acct = alpaca.get_account()
    cash = float(acct.get('cash', 0))
    portfolio = float(acct.get('portfolio_value', 0))
    logger.info(f'Cash: ${cash:,.2f} | Portfolio: ${portfolio:,.2f}')

    for symbol in SYMBOLS:
        if pm.has_position(state, symbol):
            logger.info(f'{symbol}: already have a spread open, skipping signal check')
            continue

        sig = signals.get_signal(symbol)
        logger.info(f'{symbol}: signal={sig["signal"]} | {sig["reason"]}')

        if sig['signal'] == 'NONE':
            continue

        spot = sig['price']
        spread = alpaca.find_vertical_spread(symbol, sig['signal'], spot, MAX_DTE)
        if not spread:
            continue

        cost_per_spread = spread['net_debit'] * 100
        max_afford = int((cash * 0.25) / cost_per_spread) if cost_per_spread > 0 else 0
        qty = min(MAX_CONTRACTS, max_afford, int(MAX_DEBIT_PER_TRADE / cost_per_spread) if cost_per_spread > 0 else 0)

        if qty < 1:
            logger.warning(f'{symbol}: cannot afford even 1 spread (debit=${cost_per_spread:.2f}, cash=${cash:.2f})')
            continue

        order = alpaca.open_vertical_spread(spread, qty)
        if not order:
            if alpaca.LAST_ERROR_CODE == 40310100:
                logger.warning('PDT protection triggered — skipping new entries for today')
                set_blocked()
            continue

        filled_debit = spread['net_debit'] + 0.02
        pm.register_open(state, spread, qty, filled_debit, order.get('id', ''))
        pm.save_state(state)

        logger.info(f'ENTERED: {symbol} {sig["signal"]} spread {qty}x '
                    f'{spread["long_symbol"]}/{spread["short_symbol"]} @ debit ${filled_debit:.2f}')
        from common.notifier import notify
        notify('BUY', f'{symbol} {sig["signal"]} spread', f'${filled_debit:.2f}',
               f'{symbol} {sig["signal"]} vertical x{qty} | width=${spread["width"]} '
               f'max_profit=${spread["max_profit"]:.2f}', bot='VerticalSpreadBot')


# ── Status display ────────────────────────────────────────────────────────────

def print_status():
    state = pm.load_state()
    acct  = alpaca.get_account()

    print(f'\n{"="*60}')
    print(f'  Vertical Spread Bot | ET {et_time_str()} | {datetime.now(timezone.utc).strftime("%Y-%m-%d")}')
    print(f'  Cash: ${float(acct.get("cash",0)):,.2f} | Portfolio: ${float(acct.get("portfolio_value",0)):,.2f}')
    print(f'{"="*60}')

    if not state:
        print('  No open positions.\n')
        return

    print(f'\n  Open Spreads ({len(state)}):')
    current_values = get_current_spread_values(state)

    for key, pos in state.items():
        cur   = current_values.get(key, 0)
        entry = pos['entry_debit']
        pct   = (cur - entry) / entry * 100 if entry else 0
        pnl   = (cur - entry) * pos['contracts'] * 100
        print(f'  {key}')
        print(f'    {pos["underlying"]} {pos["type"].upper()} spread x{pos["contracts"]} '
              f'(${pos["long_strike"]}/{pos["short_strike"]}, width=${pos["width"]})')
        print(f'    Entry debit: ${entry:.2f} | Current: ${cur:.2f} | P&L: {pct:+.1f}% (${pnl:+.2f})')
        print(f'    Stop: ${pos["stop_price"]:.2f} | Target: ${pos["profit_target_price"]:.2f}')
        print()


if __name__ == '__main__':
    args = sys.argv[1:]

    if '--status' in args:
        print_status()
    elif '--close' in args:
        state = pm.load_state()
        close_all_positions(state, 'Manual close')
    else:
        run()
