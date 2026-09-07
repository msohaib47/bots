"""
0DTE Day Trading Bot — SPY, QQQ, IWM, TSLA, NVDA, AMD, INTC, MU options
Runs every minute. Strategy: 15m EMA21 trend + 5m VWAP/EMA9/RSI/ADX (see signals.py).
Max 2 contracts per position. Stop-loss/trailing stop config in config.py.
Cool-down after a stop-loss blocks re-entry on that symbol (see position_manager.py).

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
                    NO_NEW_ENTRY_TIME, FORCE_CLOSE_TIME, STOP_LOSS_PCT,
                    MAX_DAILY_LOSS_PER_SYMBOL, MAX_DAILY_LOSS_TOTAL,
                    MAX_SAME_DIRECTION, MAX_POSITIONS_PER_SYMBOL, MIN_CONTRACT_PRICE,
                    MAX_OPEN_EXPOSURE, EXPOSURE_TOLERANCE_PCT, MAX_PREMIUM_PCT, CASH_PER_TRADE_PCT)

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
                     pos['contracts'], 0, reason=reason,
                     extra={'underlying_price': pm._underlying_price(pos['underlying']),
                            'hold_minutes': pm._hold_minutes(pos['opened_at'])})
        pm.remove_position(state, sym)
    pm.save_state(state)


# ── Main run ───────────────────────────────────────────────────────────────────

def run():
    logger.info(f'--- DayTradingBot tick | ET {et_time_str()} ---')

    if not is_market_open():
        logger.info('Market closed, nothing to do.')
        return

    state     = pm.load_state()
    cooldowns = pm.load_cooldowns()
    daily     = pm.load_daily_pnl()

    # ── 1. Force close all 0DTE positions before EOD ──────────────────────────
    if should_force_close():
        logger.info('EOD force close triggered.')
        close_all_positions(state, 'EOD force close')
        return

    # ── 2. Get current option prices for stop management ──────────────────────
    if state:
        current_prices = get_current_option_prices(state)

        # 3. Check stops, trailing stops, and scale-out (half close at +50%)
        to_close, to_partial_close = pm.check_and_update_stops(state, current_prices, cooldowns, daily)

        for sym, qty in to_partial_close:
            if sym in to_close:
                continue  # already fully closing this tick, don't also sell the half separately
            logger.info(f'Executing partial close for {sym}: {qty}x')
            alpaca.close_option_position(sym, qty)

        for sym in to_close:
            pos = state.get(sym, {})
            logger.info(f'Executing close for {sym}')
            alpaca.close_option_position(sym, pos.get('contracts', 1))
            pm.remove_position(state, sym)

        pm.save_state(state)
        pm.save_cooldowns(cooldowns)
        pm.save_daily_pnl(daily)

    # ── 4. Check for new entry signals ────────────────────────────────────────
    if not can_open_new_position():
        logger.info(f'Past {NO_NEW_ENTRY_TIME} ET — no new entries.')
        return

    if is_pdt_blocked():
        logger.info('PDT protection active — managing existing positions only, no new entries today.')
        return

    if pm.total_daily_loss_exceeded(daily, MAX_DAILY_LOSS_TOTAL):
        logger.info(f'Max total daily loss (${MAX_DAILY_LOSS_TOTAL:.0f}) hit (${daily["total"]:.2f}) — no new entries today.')
        return

    acct = alpaca.get_account()
    cash = float(acct.get('cash', 0))
    portfolio = float(acct.get('portfolio_value', 0))
    logger.info(f'Cash: ${cash:,.2f} | Portfolio: ${portfolio:,.2f}')

    for symbol in SYMBOLS:
        # Skip if already at the max open positions for this underlying
        if pm.count_positions_for(state, symbol) >= MAX_POSITIONS_PER_SYMBOL:
            logger.info(f'{symbol}: already at max positions ({MAX_POSITIONS_PER_SYMBOL}), skipping signal check')
            continue

        # Skip if this symbol has hit its max daily loss
        if pm.symbol_daily_loss_exceeded(daily, symbol, MAX_DAILY_LOSS_PER_SYMBOL):
            logger.info(f'{symbol}: max daily loss (${MAX_DAILY_LOSS_PER_SYMBOL:.0f}) hit, skipping signal check')
            continue

        # Get signal (also feeds the cool-down's reset check below)
        sig = signals.get_signal(symbol)

        # Post-stop-loss cool-down: blocked until the failed setup resets or the
        # safety-cap timer expires (see position_manager.update_cooldown_reset)
        if symbol in cooldowns:
            pm.update_cooldown_reset(cooldowns, symbol, sig['price'], sig['vwap'], sig['ema9'], sig['ema21'])
        if pm.is_in_cooldown(cooldowns, symbol):
            logger.info(f'{symbol}: in cool-down after stop-loss, skipping signal check')
            continue

        logger.info(f'{symbol}: signal={sig["signal"]} | {sig["reason"]}')

        if sig['signal'] == 'NONE':
            continue

        opt_type = 'call' if sig['signal'] == 'CALL' else 'put'
        spot = sig['price']

        # Skip if already at the max number of symbols open in this direction
        if pm.count_direction(state, opt_type) >= MAX_SAME_DIRECTION:
            logger.info(f'{symbol}: max {MAX_SAME_DIRECTION} {opt_type.upper()}s already open, skipping')
            continue

        # Find ATM contract
        contract = alpaca.find_atm_contract(symbol, opt_type, spot)
        if not contract:
            continue

        # Don't trade contracts quoted below the minimum premium
        if contract['mid'] < MIN_CONTRACT_PRICE:
            logger.info(f'{symbol}: contract {contract["symbol"]} mid=${contract["mid"]:.2f} below ${MIN_CONTRACT_PRICE:.2f} minimum, skipping')
            continue

        # ...or too expensive relative to spot (high IV / not really ATM -- see config.MAX_PREMIUM_PCT)
        prem_pct = contract['mid'] / spot * 100 if spot else 0
        if prem_pct > MAX_PREMIUM_PCT:
            logger.info(f'{symbol}: contract {contract["symbol"]} mid=${contract["mid"]:.2f} is {prem_pct:.2f}% of spot '
                        f'(> {MAX_PREMIUM_PCT:.2f}% max), skipping')
            continue

        # Size: max MAX_CONTRACTS, capped by buying power AND by the open-exposure limit
        # (total premium tied up across all open positions <= MAX_OPEN_EXPOSURE x (1 + tolerance)).
        cost_per_contract = contract['mid'] * 100  # 1 contract = 100 shares
        max_afford = int(cash * CASH_PER_TRADE_PCT / cost_per_contract)  # spend at most CASH_PER_TRADE_PCT of cash per entry
        exposure   = pm.open_exposure(state)
        room       = MAX_OPEN_EXPOSURE * (1 + EXPOSURE_TOLERANCE_PCT) - exposure
        max_fit    = int(room / cost_per_contract) if room > 0 else 0
        qty = min(MAX_CONTRACTS, max_afford, max_fit)

        if qty < 1:
            if max_fit < 1:
                logger.info(f'{symbol}: open exposure ${exposure:,.0f} + ${cost_per_contract:,.0f}/contract would exceed '
                            f'${MAX_OPEN_EXPOSURE:,.0f} cap (+{EXPOSURE_TOLERANCE_PCT:.0%}), skipping')
            else:
                logger.warning(f'{symbol}: cannot afford even 1 contract (cost=${cost_per_contract:.2f}, cash=${cash:.2f})')
            continue
        if qty < MAX_CONTRACTS and max_fit == qty:
            logger.info(f'{symbol}: sized down to {qty}x to stay under ${MAX_OPEN_EXPOSURE:,.0f} open-exposure cap '
                        f'(open ${exposure:,.0f}, ${cost_per_contract:,.0f}/contract)')

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
        pm.register_open(state, contract, qty, filled_price, order.get('id', ''), sig=sig)
        pm.save_state(state)

        logger.info(f'ENTERED: {symbol} {opt_type.upper()} {qty}x {contract["symbol"]} @ ${filled_price:.2f}')
        from common.notifier import notify
        notify('BUY', contract['symbol'], f'${filled_price:.2f}', f'{symbol} {opt_type.upper()} x{qty} | stop=${round(filled_price*(1-STOP_LOSS_PCT),2)}', bot='DayTradingBot')

    pm.save_cooldowns(cooldowns)


# ── Status display ────────────────────────────────────────────────────────────

def print_status():
    state = pm.load_state()
    daily = pm.load_daily_pnl()
    acct  = alpaca.get_account()

    print(f'\n{"="*60}')
    print(f'  Day Trading Bot | ET {et_time_str()} | {datetime.now(timezone.utc).strftime("%Y-%m-%d")}')
    print(f'  Cash: ${float(acct.get("cash",0)):,.2f} | Portfolio: ${float(acct.get("portfolio_value",0)):,.2f}')
    print(f'  Realized P&L today: ${daily.get("total", 0):+,.2f} (limit: -${MAX_DAILY_LOSS_TOTAL:.0f})')
    hist = pm.load_pnl_history()
    print(f'  Lifetime realized: ${hist.get("cum", 0):+,.2f} (peak ${hist.get("peak", 0):+,.2f})')
    print(f'  Open exposure: ${pm.open_exposure(state):,.2f} / ${MAX_OPEN_EXPOSURE:,.0f} cap (+{EXPOSURE_TOLERANCE_PCT:.0%} tolerance)')
    if daily.get('per_symbol'):
        per_sym = ', '.join(f'{s}=${v:+.2f}' for s, v in daily['per_symbol'].items())
        print(f'  Per symbol: {per_sym} (limit: -${MAX_DAILY_LOSS_PER_SYMBOL:.0f}/symbol)')
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
