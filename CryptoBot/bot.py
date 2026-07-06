"""
Crypto Momentum Bot — EMA 9/21 crossover + RSI filter on BTC/USD
Runs 24/7 since crypto never closes.

Usage:
  python bot.py           -- continuous scheduler (checks every 4 hours)
  python bot.py --once    -- single pass
  python bot.py --status  -- show account + positions + latest indicators
  python bot.py --dryrun  -- show signals without placing orders
"""
import csv
import logging
import os
import sys
import time
import schedule
from datetime import datetime, timezone

# Add parent directory to path to enable imports from common modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import alpaca
import indicators as ind
from config import (SYMBOLS, EMA_FAST, EMA_SLOW, RSI_PERIOD,
                    RSI_BUY_MAX, RSI_SELL_MIN,
                    MAX_POSITION_PCT, TRADE_AMOUNT_USD, TRADES_LOG, LOG_FILE)

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

DRY_RUN = False


# ── Trade logging ──────────────────────────────────────────────────────────────

def log_trade(symbol, action, price, qty_or_notional, signal_data, order=None, notes=''):
    exists = os.path.exists(TRADES_LOG)
    with open(TRADES_LOG, 'a', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(['timestamp', 'symbol', 'action', 'price', 'amount',
                        'ema_fast', 'ema_slow', 'rsi', 'cross', 'order_id', 'notes'])
        w.writerow([
            datetime.now(timezone.utc).isoformat(),
            symbol, action, price, qty_or_notional,
            signal_data.get('ema_fast'), signal_data.get('ema_slow'),
            signal_data.get('rsi'), signal_data.get('cross'),
            order.get('id') if order else 'DRY_RUN',
            notes,
        ])


# ── Core strategy per symbol ───────────────────────────────────────────────────

def run_symbol(symbol: str):
    logger.info(f'--- {symbol} ---')

    # 1. Fetch bars and compute indicators
    bars = alpaca.get_4h_bars(symbol)
    if len(bars) < EMA_SLOW + 5:
        logger.warning(f'{symbol}: Not enough bars ({len(bars)}), skipping')
        return

    bars_1h = alpaca.get_1h_bars(symbol)
    sig = ind.compute_signals(bars, EMA_FAST, EMA_SLOW, RSI_PERIOD, RSI_BUY_MAX, RSI_SELL_MIN, bars_1h=bars_1h)
    logger.info(
        f'{symbol}: price=${sig["price"]:,.2f} | '
        f'EMA{EMA_FAST}={sig["ema_fast"]:,.2f} | '
        f'EMA{EMA_SLOW}={sig["ema_slow"]:,.2f} | '
        f'RSI(4H)={sig["rsi"]} | RSI(1H)={sig["rsi_1h"]} | cross={sig["cross"]} | SIGNAL={sig["signal"]}'
    )

    # 2. Check current position
    position = alpaca.get_position(symbol)
    has_position = position is not None and float(position.get('qty', 0)) > 0

    # 3. Act on signal
    if sig['signal'] == 'BUY' and not has_position:
        portfolio = alpaca.get_portfolio_value()
        cash = alpaca.get_cash()
        max_spend = min(TRADE_AMOUNT_USD, portfolio * MAX_POSITION_PCT, cash * 0.99)

        if max_spend < 1:
            logger.warning(f'{symbol}: BUY signal but insufficient cash (${cash:.2f})')
            return

        logger.info(f'{symbol}: BUY signal -> buying ${max_spend:.2f} worth')
        if DRY_RUN:
            logger.info(f'[DRY RUN] Would BUY ${max_spend:.2f} of {symbol}')
            log_trade(symbol, 'BUY', sig['price'], max_spend, sig, notes='DRY RUN')
        else:
            order = alpaca.place_market_order(symbol, 'buy', notional=max_spend)
            log_trade(symbol, 'BUY', sig['price'], max_spend, sig, order=order)

    elif sig['signal'] == 'SELL' and has_position:
        qty = float(position['qty'])
        mkt_value = float(position.get('market_value', 0))
        logger.info(f'{symbol}: SELL signal -> closing position ({qty} coins, ~${mkt_value:.2f})')
        if DRY_RUN:
            logger.info(f'[DRY RUN] Would SELL entire {symbol} position')
            log_trade(symbol, 'SELL', sig['price'], qty, sig, notes='DRY RUN')
        else:
            order = alpaca.close_position(symbol)
            log_trade(symbol, 'SELL', sig['price'], qty, sig, order=order)
            from common.notifier import notify
            notify('SELL', symbol, f'${sig["price"]:,.2f}', f'Death cross or RSI>{RSI_SELL_MIN}', bot='CryptoBot')

    elif sig['signal'] == 'HOLD':
        status = 'holding position' if has_position else 'no position'
        logger.info(f'{symbol}: HOLD ({status})')

    elif sig['signal'] == 'BUY' and has_position:
        logger.info(f'{symbol}: BUY signal but already holding, skipping')

    elif sig['signal'] == 'SELL' and not has_position:
        logger.info(f'{symbol}: SELL signal but no position, skipping')


# ── Main runners ───────────────────────────────────────────────────────────────

def run_once():
    logger.info('=' * 60)
    logger.info(f'Crypto Momentum Bot | {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")} UTC')

    acct = alpaca.get_account()
    logger.info(f'Portfolio: ${float(acct.get("portfolio_value", 0)):,.2f} | Cash: ${float(acct.get("cash", 0)):,.2f}')

    for symbol in SYMBOLS:
        try:
            run_symbol(symbol)
        except Exception as e:
            logger.error(f'{symbol}: Unhandled error: {e}', exc_info=True)

    logger.info('Run complete.')


def print_status():
    acct = alpaca.get_account()
    positions = alpaca.list_positions()

    print(f'\n{"="*60}')
    print(f'  Crypto Bot Status | {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")} UTC')
    print(f'  Portfolio: ${float(acct.get("portfolio_value",0)):,.2f} | Cash: ${float(acct.get("cash",0)):,.2f}')
    print(f'{"="*60}')

    print(f'\n  Strategy: EMA{EMA_FAST}/EMA{EMA_SLOW} crossover + RSI{RSI_PERIOD} filter')
    print(f'  Buy when: golden cross + RSI < {RSI_BUY_MAX}')
    print(f'  Sell when: death cross OR RSI > {RSI_SELL_MIN}')

    print(f'\n  --- Live Indicators ---')
    for symbol in SYMBOLS:
        bars = alpaca.get_4h_bars(symbol)
        if bars:
            sig = ind.compute_signals(bars, EMA_FAST, EMA_SLOW, RSI_PERIOD, RSI_BUY_MAX, RSI_SELL_MIN)
            print(f'  {symbol}: ${sig["price"]:,.2f} | EMA{EMA_FAST}={sig["ema_fast"]:,.2f} | '
                  f'EMA{EMA_SLOW}={sig["ema_slow"]:,.2f} | RSI={sig["rsi"]} -> {sig["signal"]}')

    if positions:
        print(f'\n  --- Open Positions ---')
        for p in positions:
            pct = float(p.get('unrealized_plpc', 0)) * 100
            print(f'  {p["symbol"]}: qty={p["qty"]} | ${float(p["market_value"]):,.2f} | P/L: {pct:+.2f}%')
    else:
        print(f'\n  No open positions.')

    if os.path.exists(TRADES_LOG):
        with open(TRADES_LOG, 'r') as f:
            rows = list(csv.DictReader(f))
        print(f'\n  --- Recent Trades ({len(rows)} total) ---')
        for row in rows[-5:]:
            print(f'  {row["timestamp"][:16]} | {row["symbol"]} | {row["action"]} | '
                  f'${float(row["price"]):,.0f} | RSI={row["rsi"]}')
    print()


def run_scheduler():
    logger.info(f'Crypto Momentum Bot starting | coins: {", ".join(SYMBOLS)}')
    logger.info(f'Strategy: EMA{EMA_FAST}/{EMA_SLOW} + RSI{RSI_PERIOD} | Check interval: every 4h')

    run_once()

    # Check every 4 hours (crypto is 24/7, daily bars update once per day,
    # but checking frequently catches intraday momentum shifts)
    schedule.every(4).hours.do(run_once)

    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == '__main__':
    args = sys.argv[1:]

    if '--status' in args:
        print_status()
    elif '--dryrun' in args:
        DRY_RUN = True
        run_once()
    elif '--once' in args:
        run_once()
    else:
        run_scheduler()
