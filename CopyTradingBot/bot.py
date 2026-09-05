"""
Copy Trading Bot — follows congressional trades via Financial Modeling Prep and executes on Alpaca paper account.
Default target: Markwayne Mullin (+12.85% excess return vs SPY, 111 recent trades)
"""
import logging
import os
import sys
import time
import schedule
from datetime import datetime, timezone

# Add parent directory to path to enable imports from common modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import TARGET_POLITICIAN, TRADES_LOG
from scraper import fetch_politician_trades, make_trade_id
from trader import execute_trade, get_portfolio_summary
from tracker import load_executed_ids, log_trade, print_summary

# ── Logging setup ──────────────────────────────────────────────────────────────
os.makedirs('logs', exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('logs/bot.log'),
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger(__name__)

# Only trade stock types (not options — Alpaca paper has limited options support)
SUPPORTED_TYPES = {'st', 'stock', ''}


def is_market_hours() -> bool:
    now = datetime.now(timezone.utc)
    # Mon–Fri, 12:00–19:00 UTC (8am–3pm ET)
    if now.weekday() >= 5:
        return False
    return 12 <= now.hour < 19


def run_once():
    """Single pass: fetch new trades and execute them."""
    logger.info(f'--- Checking trades for {TARGET_POLITICIAN} ---')

    trades = fetch_politician_trades(TARGET_POLITICIAN)
    if not trades:
        logger.info('No trades returned from FMP')
        return

    executed_ids = load_executed_ids()
    new_trades = []
    for t in trades:
        tid = make_trade_id(t)
        if tid not in executed_ids:
            new_trades.append((tid, t))

    logger.info(f'Found {len(new_trades)} new trades to process (out of {len(trades)} total)')

    if not new_trades:
        logger.info('No new trades — nothing to execute')
        return

    if not is_market_hours():
        logger.info('Market is closed — logging trades as pending but not executing')
        for tid, t in new_trades:
            ticker_type = t.get('TickerType', 'ST')
            if ticker_type.lower() not in SUPPORTED_TYPES:
                log_trade(tid, t, None, notes=f'Skipped: unsupported type {ticker_type}')
                continue
            log_trade(tid, t, None, notes='Market closed at time of detection')
        return

    for tid, t in new_trades:
        ticker = t.get('Ticker', '')
        action = t.get('Transaction', '')
        ticker_type = t.get('TickerType', 'ST')

        if not ticker or not action:
            log_trade(tid, t, None, notes='Skipped: missing ticker or action')
            continue

        if ticker_type.lower() not in SUPPORTED_TYPES:
            logger.info(f'Skipping {ticker} — type {ticker_type} not supported (options require separate handling)')
            log_trade(tid, t, None, notes=f'Skipped: ticker type {ticker_type}')
            continue

        logger.info(f'Executing: {action} {ticker} (reported: {t.get("TransactionDate")})')
        order = execute_trade(ticker, action)
        log_trade(tid, t, order)
        from common.notifier import notify
        if order:
            notify('BUY' if 'purchase' in action.lower() else 'SELL', ticker, '', f'Copied {action} from Markwayne Mullin', bot='CopyTradingBot')

        time.sleep(0.5)  # brief pause between orders

    # Print portfolio snapshot after run
    summary = get_portfolio_summary()
    logger.info(
        f'Portfolio: ${summary["account_value"]:,.2f} | '
        f'Cash: ${summary["cash"]:,.2f} | '
        f'Positions: {len(summary["positions"])}'
    )


def run_scheduler():
    """Run the bot on a schedule: every 30 min during market hours, hourly otherwise."""
    logger.info(f'Copy Trading Bot started — tracking: {TARGET_POLITICIAN}')
    logger.info(f'Press Ctrl+C to stop')

    # Run immediately on startup
    run_once()

    # Schedule recurring checks
    schedule.every(30).minutes.do(run_once)

    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--once':
        run_once()
        print_summary()
    elif len(sys.argv) > 1 and sys.argv[1] == '--summary':
        print_summary()
        summary = get_portfolio_summary()
        print(f'\n=== Alpaca Account ===')
        print(f'Portfolio Value: ${summary["account_value"]:,.2f}')
        print(f'Buying Power:    ${summary["buying_power"]:,.2f}')
        print(f'Cash:            ${summary["cash"]:,.2f}')
        if summary['positions']:
            print(f'\nOpen Positions:')
            for p in summary['positions']:
                pl_sign = '+' if p['unrealized_pl'] >= 0 else ''
                print(f"  {p['ticker']:<6} | qty: {p['qty']:>6} | value: ${p['market_value']:>8,.2f} | P/L: {pl_sign}{p['unrealized_pl']:,.2f} ({pl_sign}{p['unrealized_plpc']:.1f}%)")
    else:
        run_scheduler()
