"""
0DTE Day Trading Bot — SPY, QQQ, IWM, TSLA, NVDA, AMD, INTC, MU options
Runs every minute. Strategy: VWAP + EMA crossover + RSI.
Max 2 contracts per position. Stop-loss 30%. Trailing stop from +30%.

Usage:
  python bot.py              -- single run (called by cron every minute)
  python bot.py --status     -- show open positions and P&L
  python bot.py --close      -- force close all positions now
  python bot.py --backtest 3 -- backtest 3 months (1-24); optional: [symbol ...]
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
from config import (SYMBOLS, MAX_CONTRACTS, STATE_FILE, LOG_FILE,
                    NO_NEW_ENTRY_TIME, FORCE_CLOSE_TIME)

PDT_FLAG_FILE = 'logs/pdt_blocked.flag'   # written when PDT blocks us; cleared at midnight

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
    """Current time in US/Eastern (approximate via UTC-4)."""
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


def is_pdt_blocked() -> bool:
    """Return True if PDT protection fired today."""
    if not os.path.exists(PDT_FLAG_FILE):
        return False
    # Clear flag if it's from a previous day
    flag_day = datetime.fromtimestamp(os.path.getmtime(PDT_FLAG_FILE)).date()
    if flag_day < et_now().date():
        os.remove(PDT_FLAG_FILE)
        return False
    return True


def set_pdt_blocked():
    with open(PDT_FLAG_FILE, 'w') as f:
        f.write(et_now().isoformat())


def should_force_close() -> bool:
    return et_time_str() >= FORCE_CLOSE_TIME


# ── Current option prices (for stop management) ───────────────────────────────

def get_current_option_prices(state: dict) -> dict:
    """Fetch current mid prices for all open option positions."""
    if not state:
        return {}
    # Direct symbol lookup — most reliable
    snaps = alpaca.get_snapshots_by_symbols(list(state.keys()))
    prices = {}
    for sym in state:
        snap  = snaps.get(sym, {})
        quote = snap.get('latestQuote', {})
        bid   = float(quote.get('bp', 0) or 0)
        ask   = float(quote.get('ap', 0) or 0)
        if ask > 0:
            prices[sym] = (bid + ask) / 2 if bid > 0 else ask
        elif bid > 0:
            prices[sym] = bid
        else:
            logger.warning(f'No quote for {sym}')
    return prices


# ── Force close all positions ──────────────────────────────────────────────────

def close_all_positions(state: dict, reason: str = 'Force close'):
    if not state:
        logger.info('No open positions to close.')
        return
    for sym, pos in list(state.items()):
        logger.info(f'Closing {sym} ({reason})')
        alpaca.close_option_position(sym, pos['contracts'])
        pm.log_trade('CLOSE', sym, pos['underlying'], pos['type'],
                     pos['contracts'], 0, reason=reason)
        pm.remove_position(state, sym)
    pm.save_state(state)


# ── Main run ───────────────────────────────────────────────────────────────────

def run():
    logger.info(f'--- DayTradingBot tick | ET {et_time_str()} ---')

    if not is_market_open():
        logger.info('Market closed, nothing to do.')
        return

    state = pm.load_state()

    # ── 1. Force close all 0DTE positions before EOD ──────────────────────────
    if should_force_close():
        logger.info('EOD force close triggered.')
        close_all_positions(state, 'EOD force close')
        return

    # ── 2. Get current option prices for stop management ──────────────────────
    if state:
        current_prices = get_current_option_prices(state)

        # 3. Check stops and trailing stops
        to_close = pm.check_and_update_stops(state, current_prices)
        for sym in to_close:
            pos = state.get(sym, {})
            logger.info(f'Executing close for {sym}')
            alpaca.close_option_position(sym, pos.get('contracts', 1))
            pm.remove_position(state, sym)

        pm.save_state(state)

    # ── 4. Check for new entry signals ────────────────────────────────────────
    if not can_open_new_position():
        logger.info(f'Past {NO_NEW_ENTRY_TIME} ET — no new entries.')
        return

    if is_pdt_blocked():
        logger.info('PDT protection active — managing existing positions only, no new entries today.')
        return

    acct = alpaca.get_account()
    cash = float(acct.get('cash', 0))
    portfolio = float(acct.get('portfolio_value', 0))
    logger.info(f'Cash: ${cash:,.2f} | Portfolio: ${portfolio:,.2f}')

    for symbol in SYMBOLS:
        # Skip if already in a position for this underlying
        if pm.has_position(state, symbol, 'call') or pm.has_position(state, symbol, 'put'):
            logger.info(f'{symbol}: already have a position, skipping signal check')
            continue

        # Get signal
        sig = signals.get_signal(symbol)
        logger.info(f'{symbol}: signal={sig["signal"]} | {sig["reason"]}')

        if sig['signal'] == 'NONE':
            continue

        opt_type = 'call' if sig['signal'] == 'CALL' else 'put'
        spot = sig['price']

        # Find ATM contract
        contract = alpaca.find_atm_contract(symbol, opt_type, spot)
        if not contract:
            continue

        # Size: max MAX_CONTRACTS, but also check buying power
        cost_per_contract = contract['mid'] * 100  # 1 contract = 100 shares
        max_afford = int(cash * 0.25 / cost_per_contract)  # use max 25% cash per trade
        qty = min(MAX_CONTRACTS, max_afford)

        if qty < 1:
            logger.warning(f'{symbol}: cannot afford even 1 contract (cost=${cost_per_contract:.2f}, cash=${cash:.2f})')
            continue

        # Place buy order
        order = alpaca.buy_option(contract, qty)
        if not order:
            # Check if blocked by PDT
            if alpaca.LAST_ERROR_CODE == 40310100:
                logger.warning('PDT protection triggered — skipping new entries for today')
                set_pdt_blocked()
            continue

        # Wait for fill (assume filled at mid+0.01 for paper trading)
        filled_price = contract['mid'] + 0.01
        pm.register_open(state, contract, qty, filled_price, order.get('id', ''))
        pm.save_state(state)

        logger.info(f'ENTERED: {symbol} {opt_type.upper()} {qty}x {contract["symbol"]} @ ${filled_price:.2f}')
        from common.notifier import notify
        notify('BUY', contract['symbol'], f'${filled_price:.2f}', f'{symbol} {opt_type.upper()} x{qty} | stop=${round(filled_price*(1-0.30),2)}', bot='DayTradingBot')


# ── Status display ────────────────────────────────────────────────────────────

def print_status():
    state = pm.load_state()
    acct  = alpaca.get_account()

    print(f'\n{"="*60}')
    print(f'  Day Trading Bot | ET {et_time_str()} | {datetime.now(timezone.utc).strftime("%Y-%m-%d")}')
    print(f'  Cash: ${float(acct.get("cash",0)):,.2f} | Portfolio: ${float(acct.get("portfolio_value",0)):,.2f}')
    print(f'{"="*60}')

    if not state:
        print('  No open positions.\n')
        return

    print(f'\n  Open Positions ({len(state)}):')
    current_prices = get_current_option_prices(state)

    for sym, pos in state.items():
        cur   = current_prices.get(sym, 0)
        entry = pos['entry_cost']
        pct   = (cur - entry) / entry * 100 if entry else 0
        pnl   = (cur - entry) * pos['contracts'] * 100
        trail = 'TRAILING' if pos['trailing_active'] else 'fixed'
        print(f'  {sym}')
        print(f'    {pos["underlying"]} {pos["type"].upper()} x{pos["contracts"]}')
        print(f'    Entry: ${entry:.2f} | Current: ${cur:.2f} | P&L: {pct:+.1f}% (${pnl:+.2f})')
        print(f'    Stop: ${pos["stop_price"]:.2f} ({trail}) | High: ${pos["high_water"]:.2f}')
        print()


if __name__ == '__main__':
    args = sys.argv[1:]

    if '--status' in args:
        print_status()
    elif '--close' in args:
        state = pm.load_state()
        close_all_positions(state, 'Manual close')
    elif '--backtest' in args:
        idx    = args.index('--backtest')
        months = 3
        syms   = None
        if idx + 1 < len(args) and args[idx + 1].isdigit():
            months = int(args[idx + 1])
            if not 1 <= months <= 24:
                print('--backtest months must be 1–24')
                sys.exit(1)
            syms = args[idx + 2:] or None
        from backtest import run_backtest
        run_backtest(months, syms)
    else:
        run()
