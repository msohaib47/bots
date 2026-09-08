"""
DT-Stocks — stock-trading variant of DayTradingBot. Same signal
(15m EMA21 trend + 5m VWAP/EMA9/RSI/ADX, see signals.py) and the same
CALL/PUT-bidirectional design, but trades shares of the underlying directly
(long on CALL, short on PUT) instead of buying 0DTE options. Runs every
minute. Stop-loss/trailing-stop config in config.py -- NOTE these use
different (tighter) default percentages than DayTradingBot's, since those
were tuned for leveraged option-premium moves, not a stock's own price.
Cool-down after a stop-loss blocks re-entry on that symbol (see position_manager.py).

Uses the Alpaca "10k account" credentials (see .env) -- separate from the
account DayTradingBot/VerticalSpreadBot/WheelBot share.

Usage:
  python bot.py              -- single run (called by cron every minute)
  python bot.py --status     -- show open positions and P&L
  python bot.py --close      -- force close all positions now
"""
import logging
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# Add parent directory to path to enable imports from common modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import alpaca
import signals
import position_manager as pm
from config import (SYMBOLS, STATE_FILE, LOG_FILE,
                    NO_NEW_ENTRY_TIME, FORCE_CLOSE_TIME, STOP_LOSS_PCT,
                    MAX_DAILY_LOSS_PER_SYMBOL, MAX_DAILY_LOSS_TOTAL,
                    MAX_SAME_DIRECTION, MAX_POSITIONS_PER_SYMBOL, MIN_SHARE_PRICE,
                    MAX_OPEN_EXPOSURE, EXPOSURE_TOLERANCE_PCT, MAX_POSITION_VALUE, CASH_PER_TRADE_PCT)

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


# ── Time helpers ────────────────────────────────────────────────────────────────
# Uses a real IANA timezone conversion from the start -- DayTradingBot's
# original hardcoded-UTC-4 bug (fixed 2026-09-07) is never introduced here.

ET = ZoneInfo('America/New_York')


def et_now() -> datetime:
    return datetime.now(ET)


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
    if not os.path.exists(PDT_FLAG_FILE):
        return False
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


# ── Current prices (for stop management) ───────────────────────────────────────

def get_current_prices(state: dict) -> dict:
    if not state:
        return {}
    return {sym: alpaca.get_latest_price(sym) for sym in state}


# ── Force close all positions ──────────────────────────────────────────────────

def close_all_positions(state: dict, reason: str = 'Force close'):
    if not state:
        logger.info('No open positions to close.')
        return
    for sym, pos in list(state.items()):
        logger.info(f'Closing {sym} ({reason})')
        alpaca.close_stock_position(sym)   # full close via DELETE -- handles long/short automatically
        pm.log_trade('CLOSE', sym, pos['side'], pos['shares'], 0, reason=reason,
                     extra={'hold_minutes': pm._hold_minutes(pos['opened_at'])})
        pm.remove_position(state, sym)
    pm.save_state(state)


# ── Main run ───────────────────────────────────────────────────────────────────

def run():
    logger.info(f'--- DT-Stocks tick | ET {et_time_str()} ---')

    if not is_market_open():
        logger.info('Market closed, nothing to do.')
        return

    state     = pm.load_state()
    cooldowns = pm.load_cooldowns()
    daily     = pm.load_daily_pnl()

    # ── 1. Force close before EOD ──────────────────────────────────────────
    if should_force_close():
        logger.info('EOD force close triggered.')
        close_all_positions(state, 'EOD force close')
        return

    # ── 2. Manage existing positions ───────────────────────────────────────
    if state:
        current_prices = get_current_prices(state)
        to_close, to_partial_close = pm.check_and_update_stops(state, current_prices, cooldowns, daily)

        for sym, shares in to_partial_close:
            if sym in to_close:
                continue
            pos = state.get(sym, {})
            closing_side = 'sell' if pos.get('side') == 'long' else 'buy'
            logger.info(f'Executing partial close for {sym}: {shares}x ({closing_side})')
            alpaca.close_stock_position(sym, shares, closing_side=closing_side)

        for sym in to_close:
            logger.info(f'Executing close for {sym}')
            alpaca.close_stock_position(sym)
            pm.remove_position(state, sym)

        pm.save_state(state)
        pm.save_cooldowns(cooldowns)
        pm.save_daily_pnl(daily)

    # ── 3. New entries ──────────────────────────────────────────────────────
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
        if pm.count_positions_for(state, symbol) >= MAX_POSITIONS_PER_SYMBOL:
            continue
        if pm.symbol_daily_loss_exceeded(daily, symbol, MAX_DAILY_LOSS_PER_SYMBOL):
            continue

        sig = signals.get_signal(symbol)

        if symbol in cooldowns:
            pm.update_cooldown_reset(cooldowns, symbol, sig['price'], sig['vwap'], sig['ema9'], sig['ema21'])
        if pm.is_in_cooldown(cooldowns, symbol):
            continue

        logger.info(f'{symbol}: signal={sig["signal"]} | {sig["reason"]}')

        if sig['signal'] == 'NONE':
            continue

        side = 'long' if sig['signal'] == 'CALL' else 'short'
        order_side = 'buy' if side == 'long' else 'sell'
        price = sig['price']

        if price is None or price < MIN_SHARE_PRICE:
            continue

        if pm.count_direction(state, side) >= MAX_SAME_DIRECTION:
            logger.info(f'{symbol}: max {MAX_SAME_DIRECTION} {side}s already open, skipping')
            continue

        # Size: dollar-capped by MAX_POSITION_VALUE and CASH_PER_TRADE_PCT of
        # cash, further capped so total open exposure stays under
        # MAX_OPEN_EXPOSURE (+ tolerance).
        max_by_value  = int(MAX_POSITION_VALUE / price)
        max_by_cash   = int(cash * CASH_PER_TRADE_PCT / price)
        exposure      = pm.open_exposure(state)
        room          = MAX_OPEN_EXPOSURE * (1 + EXPOSURE_TOLERANCE_PCT) - exposure
        max_by_room   = int(room / price) if room > 0 else 0
        shares = min(max_by_value, max_by_cash, max_by_room)

        if shares < 1:
            if max_by_room < 1:
                logger.info(f'{symbol}: open exposure ${exposure:,.0f} + ${price:,.0f}/share would exceed '
                            f'${MAX_OPEN_EXPOSURE:,.0f} cap, skipping')
            else:
                logger.warning(f'{symbol}: cannot afford even 1 share (price=${price:.2f}, cash=${cash:.2f})')
            continue

        order = alpaca.buy_stock(symbol, shares, side=order_side)
        if not order:
            if alpaca.LAST_ERROR_CODE == 40310100:
                logger.warning('PDT protection triggered — skipping new entries for today')
                set_pdt_blocked()
            continue

        # Assume filled at signal price for paper trading (no fill-price poll)
        filled_price = price
        pm.register_open(state, symbol, side, shares, filled_price, order.get('id', ''), sig=sig)
        pm.save_state(state)

        logger.info(f'ENTERED: {symbol} {side.upper()} {shares}x @ ${filled_price:.2f}')
        from common.notifier import notify
        stop = filled_price * (1 - STOP_LOSS_PCT) if side == 'long' else filled_price * (1 + STOP_LOSS_PCT)
        notify(order_side.upper(), symbol, f'${filled_price:.2f}', f'{symbol} {side.upper()} x{shares} | stop=${stop:.2f}', bot='DT-Stocks')

    pm.save_cooldowns(cooldowns)


# ── Status display ────────────────────────────────────────────────────────────

def print_status():
    state = pm.load_state()
    daily = pm.load_daily_pnl()
    acct  = alpaca.get_account()

    print(f'\n{"="*60}')
    print(f'  DT-Stocks | ET {et_time_str()} | {datetime.now(timezone.utc).strftime("%Y-%m-%d")}')
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
    current_prices = get_current_prices(state)

    for sym, pos in state.items():
        cur = current_prices.get(sym, 0) or 0
        entry = pos['entry_price']
        long_pos = pos['side'] == 'long'
        pct = (cur - entry) / entry * 100 if entry and long_pos else (entry - cur) / entry * 100 if entry else 0
        pnl = (cur - entry) * pos['shares'] if long_pos else (entry - cur) * pos['shares']
        trail = 'TRAILING' if pos['trailing_active'] else 'fixed'
        print(f'  {sym}  {pos["side"].upper()} x{pos["shares"]}')
        print(f'    Entry: ${entry:.2f} | Current: ${cur:.2f} | P&L: {pct:+.1f}% (${pnl:+.2f})')
        print(f'    Stop: ${pos["stop_price"]:.2f} ({trail}) | Best: ${pos["high_water"]:.2f}')
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
