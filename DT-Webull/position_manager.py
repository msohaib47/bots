"""
Tracks open option positions and manages stop-loss/trailing-stop/scale-out --
same rules as DayTradingBot/position_manager.py (copied, not shared/imported,
per this repo's per-bot self-containment convention).

Two differences from the DayTradingBot original, both needed for multi-account:
  1. Every function that touches a state/log file takes an explicit `paths`
     dict (see config.ACCOUNTS[name]) instead of importing fixed filename
     constants from config -- so N accounts' state never collides.
  2. `_underlying_price()` takes an injected `client` (a webull.WebullClient)
     instead of hardcoding `import alpaca` -- each account's own client is
     passed in by bot.py.

Trading-rule thresholds (STOP_LOSS_PCT, TRAIL_WIGGLE, etc.) still come from
config.py and are shared across all accounts, same as DayTradingBot.
"""
import json
import os
import logging
import csv
from datetime import datetime, timezone, timedelta

from config import (STOP_LOSS_PCT, PROFIT_TRAIL_TRIGGER, TRAIL_WIGGLE,
                    HALF_CLOSE_PROFIT_PCT, HALF_CLOSE_ENABLED, COOLDOWN_MINUTES)

logger = logging.getLogger(__name__)


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


# ── State persistence ─────────────────────────────────────────────────────────

def load_state(paths: dict) -> dict:
    """
    Returns dict keyed by option symbol:
    {
      'SYMBOL...': {
        'underlying': 'SPY', 'type': 'call', 'contracts': 2,
        'entry_cost': 1.50, 'high_water': 1.50, 'trailing_active': False,
        'stop_price': None, 'order_id': '...', 'opened_at': '...',
      }
    }
    """
    if not os.path.exists(paths['state_file']):
        return {}
    with open(paths['state_file']) as f:
        return json.load(f)


def save_state(paths: dict, state: dict):
    with open(paths['state_file'], 'w') as f:
        json.dump(state, f, indent=2)


def save_account_snapshot(paths: dict, cash: float, net_liquidation_value: float):
    """Persist the broker's current cash/net-liq each tick so the dashboard can
    show real account balance without needing its own trading credentials."""
    with open(paths['snapshot_file'], 'w') as f:
        json.dump({
            'cash': cash,
            'net_liquidation_value': net_liquidation_value,
            'updated_at': _now_iso(),
        }, f, indent=2)


def load_account_snapshot(paths: dict) -> dict:
    if not os.path.exists(paths['snapshot_file']):
        return {}
    with open(paths['snapshot_file']) as f:
        return json.load(f)


# ── Signal log (every CALL/PUT signal, whether or not it became a trade) ───────
#
# Added 2026-09-08 for the merged multi-bot dashboard's shared "Signals" section
# (see DayTradingBot/position_manager.py's identical function for the full
# rationale). Deliberately NOT per-account (unlike trades_<name>.csv) -- a
# signal is a property of the strategy/symbol, not of which account might
# trade it, so even DT-Webull's own "main"/"live" accounts share one
# signals.csv rather than each getting their own.
SIGNALS_LOG = 'signals.csv'


def log_signal(symbol: str, sig: dict, outcome: str):
    """See DayTradingBot/position_manager.py's log_signal for the full
    docstring -- identical shape and semantics, just no `paths` dict since
    this file isn't per-account."""
    exists = os.path.exists(SIGNALS_LOG)
    with open(SIGNALS_LOG, 'a', newline='') as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(['timestamp', 'symbol', 'direction', 'price', 'rsi', 'adx', 'atr',
                        'ema9', 'ema21', 'vwap', 'htf_ema21', 'htf_slope', 'ema_gap_atr',
                        'reason', 'outcome'])
        w.writerow([_now_iso(), symbol, sig.get('signal'), sig.get('price'), sig.get('rsi'),
                    sig.get('adx'), sig.get('atr'), sig.get('ema9'), sig.get('ema21'),
                    sig.get('vwap'), sig.get('htf_ema21'), sig.get('htf_slope'),
                    sig.get('ema_gap_atr'), sig.get('reason'), outcome])


EXTRA_COLUMNS = [
    'strike', 'expiration', 'underlying_price', 'premium_pct',
    'rsi', 'adx', 'atr', 'ema9', 'ema21', 'vwap', 'htf_ema21', 'htf_slope', 'ema_gap_atr',
    'hold_minutes',
]


def log_trade(paths: dict, action: str, symbol: str, underlying: str, opt_type: str,
              contracts: int, price: float, pnl: float = 0, reason: str = '',
              extra: dict | None = None):
    extra = extra or {}
    trades_log = paths['trades_log']
    exists = os.path.exists(trades_log)
    with open(trades_log, 'a', newline='') as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(['timestamp', 'action', 'symbol', 'underlying', 'type',
                        'contracts', 'price', 'pnl', 'reason'] + EXTRA_COLUMNS)
        w.writerow([_now_iso(), action, symbol, underlying, opt_type,
                    contracts, price, round(pnl, 2), reason] +
                   [extra.get(c, '') for c in EXTRA_COLUMNS])


# ── Position registration ─────────────────────────────────────────────────────

def register_open(paths: dict, state: dict, contract: dict, qty: int, filled_price: float,
                   order_id: str, sig: dict | None = None):
    sym = contract['symbol']
    state[sym] = {
        'underlying':       contract['underlying'],
        'type':             contract['type'],
        'strike':           contract.get('strike'),
        'expiration':       contract.get('expiry'),
        'contracts':        qty,
        'entry_cost':       filled_price,
        'high_water':       filled_price,
        'trailing_active':  False,
        'half_closed':      False,
        'stop_price':       filled_price * (1 - STOP_LOSS_PCT),
        'order_id':         order_id,
        'opened_at':        _now_iso(),
    }
    extra = {'strike': contract.get('strike'), 'expiration': contract.get('expiry')}
    if sig:
        spot = sig.get('price')
        extra.update({
            'underlying_price': spot,
            'premium_pct': round(filled_price / spot * 100, 3) if spot else '',
            'rsi': sig.get('rsi'), 'adx': sig.get('adx'), 'atr': sig.get('atr'),
            'ema9': sig.get('ema9'), 'ema21': sig.get('ema21'), 'vwap': sig.get('vwap'),
            'htf_ema21': sig.get('htf_ema21'), 'htf_slope': sig.get('htf_slope'),
            'ema_gap_atr': sig.get('ema_gap_atr'),
        })
    log_trade(paths, 'OPEN', sym, contract['underlying'], contract['type'],
              qty, filled_price, reason=(sig.get('reason') if sig else 'Entry'), extra=extra)
    logger.info(f'Position registered: {sym} {qty}x @ ${filled_price:.2f} | stop=${state[sym]["stop_price"]:.2f}')


def _hold_minutes(opened_at: str) -> float:
    return round((datetime.now(timezone.utc) - datetime.fromisoformat(opened_at)).total_seconds() / 60, 1)


def _underlying_price(client, underlying: str):
    """Best-effort spot price for the CLOSE row's underlying_price column --
    never blocks a close on failure. `client` is the account's WebullClient
    (or None, if unavailable) -- module-level get_latest_price() in webull.py
    is account-independent so any client works here."""
    try:
        from webull import get_latest_price
        return get_latest_price(underlying)
    except Exception:
        return ''


# ── Cool-down + signal reset (per underlying, after a stop-loss) ───────────────

def load_cooldowns(paths: dict) -> dict:
    if not os.path.exists(paths['cooldown_file']):
        return {}
    with open(paths['cooldown_file']) as f:
        return json.load(f)


def save_cooldowns(paths: dict, cooldowns: dict):
    with open(paths['cooldown_file'], 'w') as f:
        json.dump(cooldowns, f, indent=2)


def set_cooldown(cooldowns: dict, underlying: str, direction: str):
    expires = datetime.now(timezone.utc) + timedelta(minutes=COOLDOWN_MINUTES)
    cooldowns[underlying] = {
        'until': expires.isoformat(), 'direction': direction,
        'reset_seen': False, 'vwap_side': None,
    }
    logger.info(f'{underlying}: cool-down started ({direction}), blocked until {expires.isoformat()} or signal reset')


def update_cooldown_reset(cooldowns: dict, underlying: str, price: float | None,
                           vwap: float | None, ema9: float | None, ema21: float | None):
    c = cooldowns.get(underlying)
    if not c or c.get('reset_seen') or None in (price, vwap, ema9, ema21):
        return
    current_side = 'above' if price > vwap else 'below'
    prev_side = c.get('vwap_side')
    if c['direction'] == 'call':
        trend_broken = ema9 <= ema21
        crossed = prev_side == 'above' and current_side == 'below'
    else:
        trend_broken = ema9 >= ema21
        crossed = prev_side == 'below' and current_side == 'above'
    c['vwap_side'] = current_side
    if trend_broken or crossed:
        c['reset_seen'] = True
        why = 'EMA9/EMA21 breakdown' if trend_broken else 'VWAP cross'
        logger.info(f'{underlying}: signal reset observed ({why}) — cool-down cleared early')


def is_in_cooldown(cooldowns: dict, underlying: str) -> bool:
    c = cooldowns.get(underlying)
    if not c:
        return False
    if c.get('reset_seen'):
        return False
    return datetime.now(timezone.utc) < datetime.fromisoformat(c['until'])


# ── Daily realized P&L (per symbol + total, resets each calendar day) ──────────

def _today_str() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def load_daily_pnl(paths: dict) -> dict:
    today = _today_str()
    if os.path.exists(paths['daily_pnl_file']):
        with open(paths['daily_pnl_file']) as f:
            data = json.load(f)
        if data.get('date') == today:
            return data
    return {'date': today, 'total': 0.0, 'per_symbol': {}}


def save_daily_pnl(paths: dict, daily: dict):
    with open(paths['daily_pnl_file'], 'w') as f:
        json.dump(daily, f, indent=2)


def record_realized_pnl(paths: dict, daily: dict, underlying: str, pnl: float):
    daily['total'] = daily.get('total', 0.0) + pnl
    daily['per_symbol'][underlying] = daily['per_symbol'].get(underlying, 0.0) + pnl
    _record_lifetime_pnl(paths, pnl)


# ── Lifetime realized P&L (informational, shown by --status) ──────────────────

def load_pnl_history(paths: dict) -> dict:
    if os.path.exists(paths['pnl_history_file']):
        with open(paths['pnl_history_file']) as f:
            return json.load(f)
    return {'cum': 0.0, 'peak': 0.0}


def save_pnl_history(paths: dict, hist: dict):
    with open(paths['pnl_history_file'], 'w') as f:
        json.dump(hist, f, indent=2)


def _record_lifetime_pnl(paths: dict, pnl: float):
    hist = load_pnl_history(paths)
    hist['cum']  = hist.get('cum', 0.0) + pnl
    hist['peak'] = max(hist.get('peak', 0.0), hist['cum'])
    save_pnl_history(paths, hist)


# ── Open exposure (premium currently tied up) ─────────────────────────────────

def open_exposure(state: dict) -> float:
    return sum(p['entry_cost'] * p['contracts'] * 100 for p in state.values())


def symbol_daily_loss_exceeded(daily: dict, underlying: str, max_loss: float) -> bool:
    return daily['per_symbol'].get(underlying, 0.0) <= -max_loss


def total_daily_loss_exceeded(daily: dict, max_loss: float) -> bool:
    return daily.get('total', 0.0) <= -max_loss


# ── Stop management ───────────────────────────────────────────────────────────

def check_and_update_stops(paths: dict, client, state: dict, current_prices: dict,
                            cooldowns: dict, daily: dict, account_name: str = '') -> tuple:
    """Same rules as DayTradingBot's check_and_update_stops -- returns DECISIONS
    only, no broker calls and no trade-log/P&L/notify/cooldown side effects.
    Those are deferred to the caller (bot.py) and must only happen after the
    broker close order is CONFIRMED to have succeeded -- see
    finalize_close()/finalize_partial_close() below, and DayTradingBot/
    position_manager.py's identical function for the full 2026-09-08 orphan-
    position bug this fixes (this function had the same bug: it used to log/
    notify/record P&L immediately on detecting a stop breach, before bot.py had
    even attempted the broker close, and bot.py never checked whether that call
    actually succeeded).
    - to_close: [{'symbol','pos','current','reason','pnl','trailing','pct_gain'}]
    - to_partial_close: [{'symbol','pos','current','half_qty','partial_pnl','pct_gain'}]
    """
    to_close = []
    to_partial_close = []

    for sym, pos in state.items():
        current = current_prices.get(sym)
        if current is None:
            logger.warning(f'No current price for {sym}, skipping stop check')
            continue

        entry    = pos['entry_cost']
        high     = pos['high_water']
        trailing = pos['trailing_active']
        pct_gain = (current - entry) / entry

        if current > high:
            pos['high_water'] = current
            if trailing:
                new_stop = current * (1 - TRAIL_WIGGLE)
                if new_stop > pos['stop_price']:
                    pos['stop_price'] = round(new_stop, 4)
                    logger.info(f'{sym}: trailing stop raised to ${pos["stop_price"]:.2f} (high=${current:.2f})')

        if not trailing and pct_gain >= PROFIT_TRAIL_TRIGGER:
            pos['trailing_active'] = True
            pos['stop_price'] = round(current * (1 - TRAIL_WIGGLE), 4)
            logger.info(f'{sym}: trailing stop ACTIVATED at ${pos["stop_price"]:.2f} | profit={pct_gain:.1%}')
            trailing = True

        if HALF_CLOSE_ENABLED and not pos.get('half_closed') and pct_gain >= HALF_CLOSE_PROFIT_PCT:
            half_qty = pos['contracts'] // 2
            if half_qty >= 1:
                partial_pnl = (current - entry) * half_qty * 100
                to_partial_close.append({'symbol': sym, 'pos': pos, 'current': current,
                                          'half_qty': half_qty, 'partial_pnl': partial_pnl, 'pct_gain': pct_gain})

        if current <= pos['stop_price']:
            pnl = (current - entry) * pos['contracts'] * 100
            reason = 'Trailing stop hit' if trailing else 'Stop loss hit'
            to_close.append({'symbol': sym, 'pos': pos, 'current': current,
                              'reason': reason, 'pnl': pnl, 'trailing': trailing, 'pct_gain': pct_gain})

    return to_close, to_partial_close


def finalize_partial_close(paths: dict, client, sym: str, pos: dict, current: float, half_qty: int,
                            partial_pnl: float, pct_gain: float, daily: dict, account_name: str = ''):
    """Call ONLY after client.close_option_position() for this scale-out has
    confirmed success."""
    pos['half_closed'] = True
    pos['contracts'] -= half_qty
    logger.info(f'{sym}: scaling out {half_qty}x at +{pct_gain:.1%} profit | remaining {pos["contracts"]}x')
    log_trade(paths, 'PARTIAL_CLOSE', sym, pos['underlying'], pos['type'],
              half_qty, current, partial_pnl, reason='Scale-out +50%',
              extra={'underlying_price': _underlying_price(client, pos['underlying']),
                     'hold_minutes': _hold_minutes(pos['opened_at'])})
    record_realized_pnl(paths, daily, pos['underlying'], partial_pnl)
    try:
        from common.notifier import notify
        notify('SELL', sym, f'${current:.2f}',
               f'Scaled out {half_qty}x at +{pct_gain:.1%} | {pos["contracts"]}x remaining',
               bot=f'DT-Webull:{account_name}')
    except Exception:
        pass


def finalize_close(paths: dict, client, sym: str, pos: dict, current: float, reason: str, pnl: float,
                    trailing: bool, pct_gain: float, cooldowns: dict, daily: dict, account_name: str = ''):
    """Call ONLY after client.close_option_position() for this stop/trail exit
    has confirmed success. Does NOT remove from state -- caller does that."""
    logger.info(f'{sym}: {reason} | current=${current:.2f} | PnL=${pnl:.2f}')
    log_trade(paths, 'CLOSE', sym, pos['underlying'], pos['type'],
              pos['contracts'], current, pnl, reason=reason,
              extra={'underlying_price': _underlying_price(client, pos['underlying']),
                     'hold_minutes': _hold_minutes(pos['opened_at'])})
    record_realized_pnl(paths, daily, pos['underlying'], pnl)
    if not trailing:
        set_cooldown(cooldowns, pos['underlying'], pos['type'])
    try:
        from common.notifier import notify
        action = 'STOP_LOSS' if not trailing else 'SELL'
        notify(action, sym, f'${current:.2f}', f'{reason} | P&L={pct_gain:+.1%} (${pnl:+.2f})',
               bot=f'DT-Webull:{account_name}')
    except Exception:
        pass


def report_close_failed(sym: str, reason: str, account_name: str = ''):
    """Position stays tracked exactly as it was so the next tick retries --
    see DayTradingBot/position_manager.py's identical function."""
    logger.error(f'{sym}: close order FAILED ({reason}) -- left tracked, will retry next tick')
    try:
        from common.notifier import notify
        notify('ERROR', sym, '', f'{reason} close order FAILED -- still open on the broker, retrying next tick',
               bot=f'DT-Webull:{account_name}')
    except Exception:
        pass


# ── Position queries ──────────────────────────────────────────────────────────

def get_open_underlyings(state: dict) -> set:
    return {pos['underlying'] for pos in state.values()}


def count_contracts_for(state: dict, underlying: str) -> int:
    return sum(p['contracts'] for p in state.values() if p['underlying'] == underlying)


def count_positions_for(state: dict, underlying: str) -> int:
    return sum(1 for p in state.values() if p['underlying'] == underlying)


def count_direction(state: dict, opt_type: str) -> int:
    return sum(1 for p in state.values() if p['type'].lower() == opt_type.lower())


def remove_position(state: dict, symbol: str):
    state.pop(symbol, None)


def contract_from_position(symbol: str, pos: dict) -> dict:
    """Rebuilds the {'underlying','strike','expiry','type',...} shape
    webull.py's close_option_position()/buy_option() need from a stored
    position -- Webull identifies an option by its parts, not by an OCC
    symbol string, unlike Alpaca (see webull.py's order-method docstrings)."""
    return {
        'symbol': symbol,
        'underlying': pos['underlying'],
        'strike': pos.get('strike'),
        'expiry': pos.get('expiration'),
        'type': pos['type'],
    }
