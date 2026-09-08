#!/usr/bin/env python3
"""
Ad-hoc analysis script (not part of the bot, not deployed) -- replays
backtest_trades.csv's priced candidate trades with a $200 account that resets
every week (Mon-Sun), applying the bot's real config.py risk rules
(MAX_CONTRACTS_PER_SYMBOL/CASH_PER_TRADE_PCT/MAX_SAME_DIRECTION/daily loss caps)
chronologically. Independent of backtest.py's own $5,000-account portfolio
pass (_apply_portfolio_rules) -- that pass's accept/skip decisions don't
apply to a $200 account, so this re-derives entry/exit from the raw
option_entry_fill/option_exit_price columns (which are populated regardless
of whether the $5,000 pass accepted or skipped a given candidate) and applies
its own sizing/gating against a $200 balance instead.

Usage: python weekly_200_replay.py [backtest_trades.csv]
"""
import csv
import sys
import os
from collections import defaultdict
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import (MAX_CONTRACTS_PER_SYMBOL, CASH_PER_TRADE_PCT, MAX_SAME_DIRECTION,
                     MAX_DAILY_LOSS_PER_SYMBOL, MAX_DAILY_LOSS_TOTAL)

STARTING_BALANCE = 200.0
CSV_PATH = sys.argv[1] if len(sys.argv) > 1 else 'backtest_trades.csv'


def week_key(d: str) -> str:
    dt = date.fromisoformat(d)
    monday = dt - timedelta(days=dt.weekday())
    return monday.isoformat()


def load_candidates(path: str) -> list:
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            if not r['option_entry_fill'] or not r['option_exit_price']:
                continue
            if not r['entry_time'] or not r['exit_time']:
                continue
            rows.append({
                'date': r['date'], 'symbol': r['symbol'], 'direction': r['direction'],
                'entry_fill': float(r['option_entry_fill']),
                'exit_price': float(r['option_exit_price']),
                'entry_time': r['entry_time'], 'exit_time': r['exit_time'],
                'exit_reason': r['exit_reason'],
            })
    return rows


def replay_week(trades: list) -> dict:
    """
    Every trade is sized off the FIXED $STARTING_BALANCE basis (capped at
    MAX_CONTRACTS_PER_SYMBOL), never off the week's running balance -- so a winning
    streak earlier in the week never inflates the size of a later trade.
    `cash`/`end_cash` below are pure bookkeeping (starting balance + the sum
    of each independently-sized trade's own pnl), not a constraint that
    feeds back into sizing. This deliberately does NOT model a single
    continuously-compounding $200 account intra-week -- see the caller's
    note on why (asked for explicitly: "why do we have intra-week
    compounding? ... each week starting at $200").
    """
    trades = sorted(trades, key=lambda x: x['entry_time'])
    cash = STARTING_BALANCE
    open_positions = []   # (exit_time, direction)
    daily = {'date': None, 'total': 0.0, 'per_symbol': defaultdict(float)}
    taken = []

    for t in trades:
        et = t['entry_time']
        open_positions[:] = [p for p in open_positions if p[0] > et]

        if daily['date'] != t['date']:
            daily['date'], daily['total'], daily['per_symbol'] = t['date'], 0.0, defaultdict(float)

        if daily['total'] <= -MAX_DAILY_LOSS_TOTAL:
            continue
        if daily['per_symbol'][t['symbol']] <= -MAX_DAILY_LOSS_PER_SYMBOL:
            continue
        if sum(1 for _, d in open_positions if d == t['direction']) >= MAX_SAME_DIRECTION:
            continue

        cost_per_contract = t['entry_fill'] * 100
        if cost_per_contract <= 0:
            continue
        max_afford = int(STARTING_BALANCE * CASH_PER_TRADE_PCT / cost_per_contract)   # fixed basis, not `cash`
        qty = min(MAX_CONTRACTS_PER_SYMBOL, max_afford)
        if qty < 1:
            continue

        proceeds = t['exit_price'] * 100 * qty
        pnl = proceeds - cost_per_contract * qty
        cash += pnl

        daily['total'] += pnl
        daily['per_symbol'][t['symbol']] += pnl
        open_positions.append((t['exit_time'], t['direction']))
        taken.append({**t, 'qty': qty, 'pnl': pnl})

    return {'end_cash': cash, 'trades': taken}


def main():
    candidates = load_candidates(CSV_PATH)
    weeks = defaultdict(list)
    for t in candidates:
        weeks[week_key(t['date'])].append(t)

    print(f'\nWeekly $200-reset replay -- {CSV_PATH}')
    print(f'Rules: MAX_CONTRACTS_PER_SYMBOL={MAX_CONTRACTS_PER_SYMBOL}  CASH_PER_TRADE_PCT={CASH_PER_TRADE_PCT:.0%}  '
          f'MAX_SAME_DIRECTION={MAX_SAME_DIRECTION}  MAX_DAILY_LOSS_TOTAL=${MAX_DAILY_LOSS_TOTAL:.0f}  '
          f'MAX_DAILY_LOSS_PER_SYMBOL=${MAX_DAILY_LOSS_PER_SYMBOL:.0f}\n')
    print(f'{"Week of":<12} {"Trades":>7} {"Win%":>6} {"End $":>9} {"P&L":>9} {"P&L%":>7}')
    print('-' * 56)

    total_pnl = 0.0
    total_trades = 0
    profitable_weeks = 0
    for wk in sorted(weeks):
        result = replay_week(weeks[wk])
        n = len(result['trades'])
        wins = sum(1 for t in result['trades'] if t['pnl'] > 0)
        wr = wins / n * 100 if n else 0.0
        pnl = result['end_cash'] - STARTING_BALANCE
        pnl_pct = pnl / STARTING_BALANCE * 100
        total_pnl += pnl
        total_trades += n
        if pnl > 0:
            profitable_weeks += 1
        print(f'{wk:<12} {n:>7} {wr:>5.1f}% {result["end_cash"]:>9.2f} {pnl:>+9.2f} {pnl_pct:>+6.1f}%')

    n_weeks = len(weeks)
    print('-' * 56)
    print(f'{n_weeks} weeks, {profitable_weeks} profitable ({profitable_weeks/n_weeks*100:.0f}%), '
          f'{total_trades} total trades')
    print(f'Sum of weekly P&L (each week independently funded at ${STARTING_BALANCE:.0f}): ${total_pnl:+,.2f}')
    print(f'Average weekly P&L: ${total_pnl/n_weeks:+,.2f}  ({total_pnl/n_weeks/STARTING_BALANCE*100:+.1f}%)\n')


if __name__ == '__main__':
    main()
