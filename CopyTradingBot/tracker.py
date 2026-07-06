import csv
import os
import logging
from datetime import datetime
from config import TRADES_LOG

logger = logging.getLogger(__name__)

COLUMNS = [
    'trade_id', 'politician', 'ticker', 'action', 'amount_range',
    'transaction_date', 'report_date', 'ticker_type',
    'copied_at', 'order_id', 'qty', 'side', 'status',
    'excess_return_pct', 'notes'
]


def load_executed_ids() -> set:
    if not os.path.exists(TRADES_LOG):
        return set()
    with open(TRADES_LOG, 'r', newline='') as f:
        reader = csv.DictReader(f)
        return {row['trade_id'] for row in reader}


def log_trade(trade_id: str, trade: dict, order: dict | None, notes: str = ''):
    file_exists = os.path.exists(TRADES_LOG)
    with open(TRADES_LOG, 'a', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        if not file_exists:
            writer.writeheader()
        writer.writerow({
            'trade_id': trade_id,
            'politician': trade.get('Representative', ''),
            'ticker': trade.get('Ticker', ''),
            'action': trade.get('Transaction', ''),
            'amount_range': trade.get('Range', ''),
            'transaction_date': trade.get('TransactionDate', ''),
            'report_date': trade.get('ReportDate', ''),
            'ticker_type': trade.get('TickerType', 'ST'),
            'copied_at': datetime.utcnow().isoformat(),
            'order_id': order.get('order_id', '') if order else 'SKIPPED',
            'qty': order.get('qty', 0) if order else 0,
            'side': order.get('side', '') if order else '',
            'status': order.get('status', 'failed') if order else 'skipped',
            'excess_return_pct': trade.get('ExcessReturn', ''),
            'notes': notes,
        })
    logger.info(f'Logged trade {trade_id} to {TRADES_LOG}')


def load_all_trades() -> list[dict]:
    if not os.path.exists(TRADES_LOG):
        return []
    with open(TRADES_LOG, 'r', newline='') as f:
        return list(csv.DictReader(f))


def print_summary():
    trades = load_all_trades()
    if not trades:
        print('No trades logged yet.')
        return
    executed = [t for t in trades if t['status'] not in ('skipped', 'failed', '')]
    skipped = [t for t in trades if t['status'] == 'skipped']
    print(f'\n=== Copy Trading Bot Summary ===')
    print(f'Total logged:  {len(trades)}')
    print(f'Executed:      {len(executed)}')
    print(f'Skipped:       {len(skipped)}')
    print(f'\nRecent trades:')
    for t in trades[-10:]:
        print(f"  {t['copied_at'][:16]} | {t['ticker']:<6} | {t['side'] or t['action']:<8} | {t['qty']:>4} shares | {t['status']}")
