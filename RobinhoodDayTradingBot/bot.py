"""
RobinhoodDayTradingBot -- equity day trading on Robinhood.
Strategy: VWAP + EMA9/21 + RSI on 5-min bars. Stop-loss 2%, profit target 4%.
Cash account (706672094, agentic-enabled). Max 20% of buying power per trade.

Usage:
  python bot.py           -- single tick (called by cron every 5min)
  python bot.py --status  -- print open positions
  python bot.py --close   -- force close all positions
"""
import logging
import os
import sys
from datetime import datetime, timezone, timedelta

# Add parent directory to path to enable imports from common modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import robinhood
import signals
import position_manager as pm
from config import (SYMBOLS, ACCOUNT_NUMBER, MAX_POSITION_PCT,
                    NO_NEW_ENTRY_TIME, FORCE_CLOSE_TIME, LOG_FILE)

# Logging
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


# Time helpers

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


def should_force_close() -> bool:
    return et_time_str() >= FORCE_CLOSE_TIME


# Close helpers

def close_position(state: dict, symbol: str, reason: str = 'Close'):
    pos = state.get(symbol)
    if not pos:
        return
    robinhood.sell_all(symbol)
    price = robinhood.get_latest_price(symbol) or pos['entry_price']
    pnl   = (price - pos['entry_price']) * pos['qty']
    pm.log_trade('CLOSE', symbol, pos['qty'], price, pnl, reason)
    pm.remove_position(state, symbol)
    pm.save_state(state)
    logger.info(f'Closed {symbol} ({reason}) | PnL=${pnl:+.2f}')
    try:
        from common.notifier import notify
        pct = (price - pos['entry_price']) / pos['entry_price']
        notify('SELL', symbol, f'${price:.2f}',
               f'{reason} | P&L={pct:+.1%} (${pnl:+.2f})', bot='RobinhoodDayTradingBot')
    except Exception:
        pass


def close_all(state: dict, reason: str = 'Force close'):
    if not state:
        logger.info('No open positions to close.')
        return
    for symbol in list(state.keys()):
        close_position(state, symbol, reason)


# Main run

def run():
    logger.info(f'--- RobinhoodDayTradingBot tick | ET {et_time_str()} ---')

    if not is_market_open():
        logger.info('Market closed, nothing to do.')
        return

    if not robinhood.login():
        logger.error('Cannot login to Robinhood -- aborting tick')
        return

    state = pm.load_state()

    # 1. EOD force close
    if should_force_close():
        logger.info('EOD force close triggered.')
        close_all(state, 'EOD force close')
        return

    # 2. Check stops on open positions
    if state:
        current_prices = {}
        for symbol in state:
            p = robinhood.get_latest_price(symbol)
            if p:
                current_prices[symbol] = p

        to_close = pm.check_exits(state, current_prices)
        for symbol in to_close:
            close_position(state, symbol, 'Stop hit')

        pm.save_state(state)

    # 3. New entry signals
    if not can_open_new_position():
        logger.info(f'Past {NO_NEW_ENTRY_TIME} ET -- no new entries.')
        return

    acct = robinhood.get_account()
    buying_power = acct['buying_power']
    logger.info(f'Buying power: ${buying_power:,.2f} | Account: {ACCOUNT_NUMBER}')

    for symbol in SYMBOLS:
        if pm.has_position(state, symbol):
            logger.info(f'{symbol}: already have a position, skipping')
            continue

        sig = signals.get_signal(symbol)
        logger.info(f'{symbol}: signal={sig["signal"]} | {sig["reason"]}')

        if sig['signal'] != 'BUY':
            continue

        dollars = round(buying_power * MAX_POSITION_PCT, 2)
        if dollars < 1.0:
            logger.warning(f'{symbol}: insufficient buying power (${buying_power:.2f})')
            continue

        order = robinhood.buy_market(symbol, dollars)
        if not order:
            logger.error(f'{symbol}: buy order failed')
            continue

        price = sig['price'] or robinhood.get_latest_price(symbol) or 0
        qty   = dollars / price if price > 0 else 0
        pm.register_open(state, symbol, qty, price, dollars, order.get('id', ''))
        pm.save_state(state)

        logger.info(f'ENTERED: {symbol} ${dollars:.2f} @ ~${price:.2f}')
        try:
            from common.notifier import notify
            notify('BUY', symbol, f'${price:.2f}',
                   f'${dollars:.2f} invested | stop=${price*0.98:.2f} target=${price*1.04:.2f}',
                   bot='RobinhoodDayTradingBot')
        except Exception:
            pass


# Status display

def print_status():
    if not robinhood.login():
        print('Login failed.')
        return
    state = pm.load_state()
    acct  = robinhood.get_account()

    print(f'\n{"="*60}')
    print(f'  RobinhoodDayTradingBot | ET {et_time_str()}')
    print(f'  Account: {ACCOUNT_NUMBER}')
    print(f'  Buying power: ${acct["buying_power"]:,.2f} | Portfolio: ${acct["portfolio_value"]:,.2f}')
    print(f'{"="*60}')

    if not state:
        print('  No open positions.\n')
    else:
        print(f'\n  Open Positions ({len(state)}):')
        for symbol, pos in state.items():
            price = robinhood.get_latest_price(symbol) or pos['entry_price']
            pct   = (price - pos['entry_price']) / pos['entry_price'] * 100
            pnl   = (price - pos['entry_price']) * pos['qty']
            trail = 'TRAILING' if pos['trailing_active'] else 'fixed'
            print(f'  {symbol}')
            print(f'    {pos["qty"]:.4f} shares | Entry: ${pos["entry_price"]:.2f} | Current: ${price:.2f}')
            print(f'    P&L: {pct:+.1f}% (${pnl:+.2f}) | Stop: ${pos["stop_price"]:.2f} ({trail})')
            print()

    rh_positions = robinhood.list_positions()
    if rh_positions:
        print('  Robinhood actual positions:')
        for p in rh_positions:
            print(f'    {p["symbol"]}: {p["qty"]} shares @ avg ${p["avg_price"]:.2f}')
        print()


if __name__ == '__main__':
    args = sys.argv[1:]
    if '--status' in args:
        print_status()
    elif '--close' in args:
        if not robinhood.login():
            sys.exit(1)
        state = pm.load_state()
        close_all(state, 'Manual close')
    else:
        run()
