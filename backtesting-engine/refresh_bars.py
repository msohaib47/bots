#!/usr/bin/env python3
"""
Pre-fetch and cache historical bars for a symbol list, independent of any
particular backtest run -- lets a backtest (v1's own, or DayTradingBotV2's
run_backtest.py) hit disk instead of Alpaca on its next run, and lets the
cache be kept up-to-date on its own schedule.

market_data.get_bars() already only fetches the date-range gaps missing from
its cache (see market_data.py's _missing_ranges) -- so re-running this same
command later, with the same symbols/timeframe and end_date left as "today",
is a genuine incremental refresh, not a re-download.

Usage:
  python refresh_bars.py SPY QQQ IWM --timeframe 1Min --start 2026-01-01
  python refresh_bars.py --timeframe 1Min --months 9                # default DayTradingBotV2 symbols
  python refresh_bars.py SPY QQQ IWM --timeframe 5Min --start 2026-01-01 --end 2026-09-07

Requires a discoverable .env with ALPACA_API_KEY/ALPACA_SECRET_KEY -- see
_alpaca.py's docstring for how to point this at one.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from market_data import get_bars

# Matches DayTradingBotV2/DaySignalService/symbols.py -- used only as this
# script's default when no symbols are given on the command line.
_DEFAULT_SYMBOLS = ['SPY', 'QQQ', 'IWM', 'TSLA', 'NVDA', 'INTC', 'MSFT', 'META']


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('symbols', nargs='*', default=_DEFAULT_SYMBOLS)
    ap.add_argument('--timeframe', default='1Min')
    ap.add_argument('--start', default=None, help='YYYY-MM-DD (default: --months back from --end)')
    ap.add_argument('--end', default=None, help='YYYY-MM-DD (default: today)')
    ap.add_argument('--months', type=int, default=9, help='lookback if --start omitted (default 9)')
    args = ap.parse_args()

    print(f'Refreshing {args.timeframe} bars for {", ".join(args.symbols)} '
          f'({args.start or f"{args.months} months back"} to {args.end or "today"})\n')

    for sym in args.symbols:
        try:
            get_bars(sym, args.timeframe, start_date=args.start, end_date=args.end, months=args.months)
        except Exception as e:
            print(f'  {sym}: ERROR {e}')

    print('\nDone. Cache files live in backtesting-engine/cache/bars/.')


if __name__ == '__main__':
    main()
