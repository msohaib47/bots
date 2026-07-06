"""
Wheel Strategy Bot
Runs the wheel (CSP → assignment → CC → repeat) on QBTS, RIOT, CIFR, CLSK.

Usage:
  python bot.py           — continuous scheduler (every 30 min during market hours)
  python bot.py --once    — single pass
  python bot.py --status  — print current state of all tickers
  python bot.py --reset TICKER — reset a ticker back to IDLE
"""
import logging
import os
import sys
import time
import schedule
from datetime import datetime, timezone

# Add parent directory to path to enable imports from common modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import state as st
import strategy
import alpaca
from config import TICKERS, LOG_FILE

# ── Logging ────────────────────────────────────────────────────────────────────
os.makedirs('logs', exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger(__name__)


def is_market_hours() -> bool:
    now = datetime.now(timezone.utc)
    if now.weekday() >= 5:
        return False
    # 8am–3pm ET = 12:00–19:00 UTC
    return 12 <= now.hour < 19


def run_once():
    if not is_market_hours():
        logger.info('Market closed — skipping run')
        return

    logger.info('=' * 60)
    logger.info(f'Wheel Bot — {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M") + " UTC"}')

    acct = alpaca.get_account()
    logger.info(f'Account: ${float(acct.get("portfolio_value", 0)):,.2f} | Cash: ${float(acct.get("cash", 0)):,.2f}')

    wheel_state = st.load()

    for ticker in TICKERS:
        logger.info(f'--- {ticker} ---')
        strategy.run_ticker(ticker, wheel_state)

    st.save(wheel_state)
    logger.info('State saved.')


def print_status():
    wheel_state = st.load()
    acct = alpaca.get_account()

    print(f'\n{"="*65}')
    print(f'  Wheel Bot Status — {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M") + " UTC"}')
    print(f'  Portfolio: ${float(acct.get("portfolio_value",0)):,.2f} | Cash: ${float(acct.get("cash",0)):,.2f}')
    print(f'{"="*65}')

    for ticker in TICKERS:
        ts = wheel_state.get(ticker, {})
        s = ts.get('state', 'IDLE')
        print(f'\n  {ticker}  [{s}]')

        if s in (st.CSP_PENDING, st.CSP_OPEN):
            print(f'    Put:   {ts.get("csp_symbol")} | strike=${ts.get("csp_strike")} | exp={ts.get("csp_expiry")} | premium=${ts.get("csp_premium")}')

        if s in (st.ASSIGNED, st.CC_PENDING, st.CC_OPEN):
            print(f'    Shares: {ts.get("shares_qty")} | cost basis: ${ts.get("cost_basis")}')

        if s in (st.CC_PENDING, st.CC_OPEN):
            print(f'    Call:  {ts.get("cc_symbol")} | strike=${ts.get("cc_strike")} | exp={ts.get("cc_expiry")} | premium=${ts.get("cc_premium")}')

        print(f'    Premiums collected: ${ts.get("total_premium_collected", 0):.2f}')
        print(f'    Realized P&L:       ${ts.get("total_realized_pnl", 0):.2f}')
        print(f'    Cycles completed:   {ts.get("cycles_completed", 0)}')

    print()

    # Live positions
    positions = alpaca.list_positions()
    if positions:
        print('  Open Positions:')
        for p in positions:
            pct = float(p.get('unrealized_plpc', 0)) * 100
            print(f'    {p["symbol"]:<6} {p["qty"]:>6} shares | ${float(p["market_value"]):>9,.2f} | P/L: {pct:+.1f}%')
    print()


def reset_ticker(ticker: str):
    wheel_state = st.load()
    if ticker not in wheel_state:
        print(f'{ticker} not found in state.')
        return
    st.reset_ticker(wheel_state, ticker)
    st.save(wheel_state)
    print(f'{ticker} reset to IDLE.')


def run_scheduler():
    logger.info(f'Wheel Bot starting — tickers: {", ".join(TICKERS)}')
    run_once()
    schedule.every(30).minutes.do(run_once)
    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == '__main__':
    args = sys.argv[1:]

    if '--status' in args:
        print_status()
    elif '--reset' in args:
        idx = args.index('--reset')
        ticker = args[idx + 1].upper() if idx + 1 < len(args) else None
        if ticker:
            reset_ticker(ticker)
        else:
            print('Usage: python bot.py --reset TICKER')
    elif '--once' in args:
        run_once()
        print_status()
    elif '--dryrun' in args:
        # Bypass market hours check, NO real orders placed
        strategy.DRY_RUN = True
        logger.info('DRY RUN — no real orders will be placed')
        wheel_state = st.load()
        for ticker in TICKERS:
            logger.info(f'--- {ticker} ---')
            strategy.run_ticker(ticker, wheel_state)
        logger.info('DRY RUN complete — state NOT saved')
        print_status()
    else:
        run_scheduler()
