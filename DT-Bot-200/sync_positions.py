"""
Position sync — runs every 30 min.
Reconciles positions.json against actual Alpaca open positions:
  - Alpaca has it, state missing  → add it (with default stops)
  - State has it, Alpaca closed   → remove it (position was closed/expired)
  - Both have it                  → update high_water if price moved up
"""
import json
import logging
import os
import sys
import csv
from datetime import datetime, timezone

import alpaca
import position_manager as pm
from config import STOP_LOSS_PCT, PROFIT_TRAIL_TRIGGER, TRAIL_WIGGLE, LOG_FILE, TRADES_LOG, SYMBOLS

# Only sync positions whose underlying is one of the DayTradingBot symbols
MANAGED_UNDERLYINGS = set(SYMBOLS)  # {'SPY', 'QQQ', 'IWM'}

os.makedirs('logs', exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [SYNC] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger(__name__)


def _opt_type(symbol: str) -> str:
    # OCC symbol: XXXXXXYYMMDDCXXXXXXXX — C or P at position 6+6=12
    for i, ch in enumerate(symbol):
        if ch in ('C', 'P') and i > 6:
            return 'call' if ch == 'C' else 'put'
    return 'call'


def _underlying(symbol: str) -> str:
    # Strip digits/letters after the ticker
    result = ''
    for ch in symbol:
        if ch.isdigit():
            break
        result += ch
    return result.strip()


def _orphan_equity_positions(all_positions):
    """DayTradingBot only ever holds options on SPY/QQQ/IWM, never the underlying stock
    directly. Any equity position in a managed symbol is therefore always an orphan —
    almost always a long put/call that expired in-the-money and got auto-exercised
    instead of closed, silently consuming margin/buying power until someone notices."""
    return {
        p['symbol']: p for p in all_positions
        if p.get('asset_class') == 'us_equity' and p['symbol'] in MANAGED_UNDERLYINGS
    }


def sync():
    logger.info('--- Position sync started ---')

    # 1. Load current state
    state = pm.load_state()

    # 2. Fetch live positions from Alpaca — only options for our managed symbols
    all_positions = alpaca.list_positions()
    alpaca_opts = {
        p['symbol']: p for p in all_positions
        if any(c in p['symbol'] for c in ('C0', 'P0'))           # OCC option symbols
        and _underlying(p['symbol']) in MANAGED_UNDERLYINGS       # only SPY/QQQ/IWM
        and int(float(p.get('qty', 0))) > 0                       # long positions only (not wheel short puts)
    }

    logger.info(f'Alpaca option positions: {len(alpaca_opts)} | State file: {len(state)}')

    # 2b. Detect orphan equity positions (exercise/assignment that fell through the cracks)
    orphan_equity = _orphan_equity_positions(all_positions)
    for sym, pos in orphan_equity.items():
        qty = pos.get('qty', '?')
        mval = pos.get('market_value', '?')
        msg = (f'ORPHAN EQUITY POSITION: {sym} qty={qty} market_value=${mval} — '
               f'DayTradingBot never holds the underlying directly, so this almost '
               f'certainly came from an unclosed option expiring ITM and being '
               f'auto-exercised. It will keep consuming buying power until manually closed.')
        logger.error(msg)
        try:
            from common.notifier import notify
            notify('ERROR', sym, f'qty={qty}', msg, bot='DT-Bot-200')
        except Exception as e:
            logger.warning(f'Failed to send orphan-position alert: {e}')

    # 3. Get current prices for everything we track
    all_syms = list(set(list(state.keys()) + list(alpaca_opts.keys())))
    prices = {}
    if all_syms:
        snaps = alpaca.get_snapshots_by_symbols(all_syms)
        for sym, snap in snaps.items():
            qt  = snap.get('latestQuote', {})
            bid = float(qt.get('bp', 0) or 0)
            ask = float(qt.get('ap', 0) or 0)
            if ask > 0:
                prices[sym] = (bid + ask) / 2 if bid > 0 else ask

    added = removed = updated = 0

    # 4. Add positions Alpaca has but state is missing
    for sym, pos in alpaca_opts.items():
        if sym in state:
            continue  # handled below

        entry  = float(pos.get('avg_entry_price', 0))
        qty    = int(float(pos.get('qty', 0)))
        cur    = prices.get(sym, entry)
        pct    = (cur - entry) / entry if entry else 0

        trailing = pct >= PROFIT_TRAIL_TRIGGER
        if trailing:
            stop = round(cur * (1 - TRAIL_WIGGLE), 4)
        else:
            stop = round(entry * (1 - STOP_LOSS_PCT), 4)

        state[sym] = {
            'underlying':      _underlying(sym),
            'type':            _opt_type(sym),
            'contracts':       qty,
            'entry_cost':      entry,
            'high_water':      max(entry, cur),
            'trailing_active': trailing,
            'stop_price':      stop,
            'order_id':        '',
            'opened_at':       datetime.now(timezone.utc).isoformat(),
        }

        logger.info(
            f'ADDED to state: {sym} | {qty}x @ ${entry:.2f} | '
            f'cur=${cur:.2f} ({pct:+.1%}) | stop=${stop:.2f} '
            f'| {"TRAILING" if trailing else "fixed"}'
        )

        # Log to trades file
        pm.log_trade('SYNC_ADD', sym, _underlying(sym), _opt_type(sym),
                     qty, entry, reason='Added by sync — was missing from state')
        added += 1

    # 5. Remove positions state tracks but Alpaca has closed
    for sym in list(state.keys()):
        if sym not in alpaca_opts:
            underlying = state[sym].get('underlying', _underlying(sym))
            if underlying in orphan_equity:
                reason = 'Removed by sync — likely exercised/assigned (orphan equity position found, see ORPHAN EQUITY alert)'
                logger.error(f'REMOVED from state: {sym} — missing from Alpaca AND {underlying} has an orphan equity position; probable exercise, not a clean expiry')
            else:
                reason = 'Removed by sync — closed/expired'
                logger.info(f'REMOVED from state: {sym} — no longer in Alpaca (expired/closed)')
            pm.log_trade('SYNC_REMOVE', sym, state[sym].get('underlying',''),
                         state[sym].get('type',''), state[sym].get('contracts',0),
                         prices.get(sym, 0), reason=reason)
            pm.remove_position(state, sym)
            removed += 1

    # 6. Update high_water for existing positions if price moved up
    for sym, ts in state.items():
        cur = prices.get(sym)
        if not cur:
            continue

        entry = ts.get('entry_cost', 0)
        old_high = ts.get('high_water', entry)
        pct = (cur - entry) / entry if entry else 0

        if cur > old_high:
            ts['high_water'] = cur
            # Activate trailing if threshold hit
            if not ts.get('trailing_active') and pct >= PROFIT_TRAIL_TRIGGER:
                ts['trailing_active'] = True
                ts['stop_price'] = round(cur * (1 - TRAIL_WIGGLE), 4)
                logger.info(f'TRAILING ACTIVATED: {sym} | cur=${cur:.2f} ({pct:+.1%}) | stop=${ts["stop_price"]:.2f}')
            # Raise trailing stop
            elif ts.get('trailing_active'):
                new_stop = round(cur * (1 - TRAIL_WIGGLE), 4)
                if new_stop > ts['stop_price']:
                    ts['stop_price'] = new_stop
                    logger.info(f'TRAILING RAISED: {sym} | high=${cur:.2f} | stop=${new_stop:.2f}')
            updated += 1

    # 7. Save
    pm.save_state(state)

    logger.info(
        f'Sync complete — added={added} removed={removed} updated={updated} | '
        f'Total tracked: {len(state)}'
    )

    # 8. Print summary
    print(f'\n{"="*55}')
    print(f'  Sync @ {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")} UTC | added={added} removed={removed}')
    for sym, ts in state.items():
        cur  = prices.get(sym, 0)
        pct  = (cur - ts['entry_cost']) / ts['entry_cost'] * 100 if ts['entry_cost'] else 0
        mode = 'TRAILING' if ts['trailing_active'] else 'fixed'
        print(f'  {sym:<30} {pct:+6.1f}%  stop=${ts["stop_price"]:.2f} [{mode}]')
    print(f'{"="*55}\n')


if __name__ == '__main__':
    sync()
