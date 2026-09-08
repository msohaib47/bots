"""
Tracks open STOCK positions (long or short) and manages:
  - Stop loss / trailing stop (thresholds in config.py, tuned for stock price
    moves -- NOT DayTradingBot's option-premium-tuned percentages)
  - Scale-out (off by default, HALF_CLOSE_ENABLED)

Adapted from DayTradingBot/position_manager.py: positions are keyed by
SYMBOL directly (not an option contract symbol), track 'shares' and
'entry_price' (not 'contracts'/'entry_cost'), and carry a 'side' field
('long'/'short') since a stock bot trades both CALL->long and PUT->short,
which options didn't need to distinguish (buying a put and buying a call are
both just "buy an option" -- shorting a stock is mechanically different from
buying one).

trades.csv columns: timestamp/action/symbol/type(long|short)/shares/price/
pnl/reason, plus EXTRA_COLUMNS (RSI/ADX/ATR/EMA9/EMA21/VWAP/htf_ema21/
htf_slope/ema_gap_atr on OPEN rows, hold_minutes on CLOSE/PARTIAL_CLOSE) --
no strike/expiration/premium_pct columns since there's no option contract
involved.
"""
import json
import os
import logging
import csv
from datetime import datetime, timezone, timedelta

from config import (STATE_FILE, TRADES_LOG, STOP_LOSS_PCT,
                    PROFIT_TRAIL_TRIGGER, TRAIL_WIGGLE, HALF_CLOSE_PROFIT_PCT, HALF_CLOSE_ENABLED,
                    COOLDOWN_FILE, COOLDOWN_MINUTES, DAILY_PNL_FILE, PNL_HISTORY_FILE)

logger = logging.getLogger(__name__)


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


# ── State persistence ─────────────────────────────────────────────────────────

def load_state() -> dict:
    """
    Returns dict keyed by symbol:
    {
      'SPY': {
        'side': 'long' | 'short',
        'shares': 10,
        'entry_price': 750.25,
        'high_water': 750.25,   # best price seen since entry (max for long, min for short)
        'trailing_active': False,
        'stop_price': None,
        'order_id': '...',
        'opened_at': '...',
      }
    }
    """
    if not os.path.exists(STATE_FILE):
        return {}
    with open(STATE_FILE) as f:
        return json.load(f)


def save_state(state: dict):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)


EXTRA_COLUMNS = [
    'rsi', 'adx', 'atr', 'ema9', 'ema21', 'vwap', 'htf_ema21', 'htf_slope', 'ema_gap_atr',
    'hold_minutes',
]


def log_trade(action: str, symbol: str, side: str, shares: int, price: float,
              pnl: float = 0, reason: str = '', extra: dict | None = None):
    extra = extra or {}
    exists = os.path.exists(TRADES_LOG)
    with open(TRADES_LOG, 'a', newline='') as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(['timestamp', 'action', 'symbol', 'side', 'shares', 'price', 'pnl', 'reason'] + EXTRA_COLUMNS)
        w.writerow([_now_iso(), action, symbol, side, shares, price, round(pnl, 2), reason] +
                   [extra.get(c, '') for c in EXTRA_COLUMNS])


# ── Position registration ─────────────────────────────────────────────────────

def register_open(state: dict, symbol: str, side: str, shares: int, filled_price: float,
                   order_id: str, sig: dict | None = None):
    stop_price = filled_price * (1 - STOP_LOSS_PCT) if side == 'long' else filled_price * (1 + STOP_LOSS_PCT)
    state[symbol] = {
        'side':             side,
        'shares':           shares,
        'entry_price':      filled_price,
        'high_water':       filled_price,
        'trailing_active':  False,
        'half_closed':      False,
        'stop_price':       stop_price,
        'order_id':         order_id,
        'opened_at':        _now_iso(),
    }
    extra = {}
    if sig:
        extra = {
            'rsi': sig.get('rsi'), 'adx': sig.get('adx'), 'atr': sig.get('atr'),
            'ema9': sig.get('ema9'), 'ema21': sig.get('ema21'), 'vwap': sig.get('vwap'),
            'htf_ema21': sig.get('htf_ema21'), 'htf_slope': sig.get('htf_slope'),
            'ema_gap_atr': sig.get('ema_gap_atr'),
        }
    log_trade('OPEN', symbol, side, shares, filled_price,
              reason=(sig.get('reason') if sig else 'Entry'), extra=extra)
    logger.info(f'Position registered: {symbol} {side} {shares}x @ ${filled_price:.2f} | stop=${stop_price:.2f}')


def _hold_minutes(opened_at: str) -> float:
    return round((datetime.now(timezone.utc) - datetime.fromisoformat(opened_at)).total_seconds() / 60, 1)


# ── Cool-down + signal reset (per symbol, after a stop-loss) ───────────────────
# Identical logic to DayTradingBot's -- see that file's module notes.

def load_cooldowns() -> dict:
    if not os.path.exists(COOLDOWN_FILE):
        return {}
    with open(COOLDOWN_FILE) as f:
        return json.load(f)


def save_cooldowns(cooldowns: dict):
    with open(COOLDOWN_FILE, 'w') as f:
        json.dump(cooldowns, f, indent=2)


def set_cooldown(cooldowns: dict, symbol: str, side: str):
    expires = datetime.now(timezone.utc) + timedelta(minutes=COOLDOWN_MINUTES)
    cooldowns[symbol] = {
        'until': expires.isoformat(),
        'direction': 'call' if side == 'long' else 'put',  # matches signals.py's CALL/PUT vocabulary
        'reset_seen': False,
        'vwap_side': None,
    }
    logger.info(f'{symbol}: cool-down started ({side}), blocked until {expires.isoformat()} or signal reset')


def update_cooldown_reset(cooldowns: dict, symbol: str, price: float | None,
                           vwap: float | None, ema9: float | None, ema21: float | None):
    c = cooldowns.get(symbol)
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
        logger.info(f'{symbol}: signal reset observed ({why}) — cool-down cleared early')


def is_in_cooldown(cooldowns: dict, symbol: str) -> bool:
    c = cooldowns.get(symbol)
    if not c:
        return False
    if c.get('reset_seen'):
        return False
    return datetime.now(timezone.utc) < datetime.fromisoformat(c['until'])


# ── Daily realized P&L ──────────────────────────────────────────────────────

def _today_str() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def load_daily_pnl() -> dict:
    today = _today_str()
    if os.path.exists(DAILY_PNL_FILE):
        with open(DAILY_PNL_FILE) as f:
            data = json.load(f)
        if data.get('date') == today:
            return data
    return {'date': today, 'total': 0.0, 'per_symbol': {}}


def save_daily_pnl(daily: dict):
    with open(DAILY_PNL_FILE, 'w') as f:
        json.dump(daily, f, indent=2)


def record_realized_pnl(daily: dict, symbol: str, pnl: float):
    daily['total'] = daily.get('total', 0.0) + pnl
    daily['per_symbol'][symbol] = daily['per_symbol'].get(symbol, 0.0) + pnl
    _record_lifetime_pnl(pnl)


def load_pnl_history() -> dict:
    if os.path.exists(PNL_HISTORY_FILE):
        with open(PNL_HISTORY_FILE) as f:
            return json.load(f)
    return {'cum': 0.0, 'peak': 0.0}


def save_pnl_history(hist: dict):
    with open(PNL_HISTORY_FILE, 'w') as f:
        json.dump(hist, f, indent=2)


def _record_lifetime_pnl(pnl: float):
    hist = load_pnl_history()
    hist['cum']  = hist.get('cum', 0.0) + pnl
    hist['peak'] = max(hist.get('peak', 0.0), hist['cum'])
    save_pnl_history(hist)


# ── Open exposure (dollars currently tied up) ───────────────────────────────

def open_exposure(state: dict) -> float:
    """Total position value at entry price -- no x100 multiplier (that was
    the options-contract-to-shares conversion; a stock IS the share)."""
    return sum(p['entry_price'] * p['shares'] for p in state.values())


def symbol_daily_loss_exceeded(daily: dict, symbol: str, max_loss: float) -> bool:
    return daily['per_symbol'].get(symbol, 0.0) <= -max_loss


def total_daily_loss_exceeded(daily: dict, max_loss: float) -> bool:
    return daily.get('total', 0.0) <= -max_loss


# ── Stop management ───────────────────────────────────────────────────────────

def check_and_update_stops(state: dict, current_prices: dict, cooldowns: dict, daily: dict) -> tuple:
    """
    Same rule shape as DayTradingBot's, but side-aware: a long profits as
    price rises (stop trails up, below the high), a short profits as price
    falls (stop trails down, above the low -- 'high_water' is repurposed to
    mean "best price seen for this position", i.e. the low for a short).
    Returns (to_close, to_partial_close) exactly like DayTradingBot's version.
    """
    to_close = []
    to_partial_close = []

    for sym, pos in state.items():
        current = current_prices.get(sym)
        if current is None:
            logger.warning(f'No current price for {sym}, skipping stop check')
            continue

        side     = pos['side']
        entry    = pos['entry_price']
        best     = pos['high_water']
        trailing = pos['trailing_active']
        long_pos = side == 'long'
        pct_gain = (current - entry) / entry if long_pos else (entry - current) / entry

        favorable_move = current > best if long_pos else current < best
        if favorable_move:
            pos['high_water'] = current
            if trailing:
                new_stop = current * (1 - TRAIL_WIGGLE) if long_pos else current * (1 + TRAIL_WIGGLE)
                improves = new_stop > pos['stop_price'] if long_pos else new_stop < pos['stop_price']
                if improves:
                    pos['stop_price'] = round(new_stop, 4)
                    logger.info(f'{sym}: trailing stop moved to ${pos["stop_price"]:.2f} (best=${current:.2f})')

        if not trailing and pct_gain >= PROFIT_TRAIL_TRIGGER:
            pos['trailing_active'] = True
            pos['stop_price'] = round(current * (1 - TRAIL_WIGGLE) if long_pos else current * (1 + TRAIL_WIGGLE), 4)
            logger.info(f'{sym}: trailing stop ACTIVATED at ${pos["stop_price"]:.2f} | profit={pct_gain:.1%}')

        if HALF_CLOSE_ENABLED and not pos.get('half_closed') and pct_gain >= HALF_CLOSE_PROFIT_PCT:
            half_shares = pos['shares'] // 2
            pos['half_closed'] = True
            if half_shares >= 1:
                partial_pnl = (current - entry) * half_shares if long_pos else (entry - current) * half_shares
                pos['shares'] -= half_shares
                logger.info(f'{sym}: scaling out {half_shares}x at +{pct_gain:.1%} profit | remaining {pos["shares"]}x')
                log_trade('PARTIAL_CLOSE', sym, side, half_shares, current, partial_pnl, reason='Scale-out',
                          extra={'hold_minutes': _hold_minutes(pos['opened_at'])})
                record_realized_pnl(daily, sym, partial_pnl)
                try:
                    from common.notifier import notify
                    notify('SELL' if long_pos else 'BUY', sym, f'${current:.2f}',
                           f'Scaled out {half_shares}x at +{pct_gain:.1%} | {pos["shares"]}x remaining', bot='DT-Stocks')
                except Exception:
                    pass
                to_partial_close.append((sym, half_shares))

        stop_hit = current <= pos['stop_price'] if long_pos else current >= pos['stop_price']
        if stop_hit:
            pnl = (current - entry) * pos['shares'] if long_pos else (entry - current) * pos['shares']
            reason = 'Trailing stop hit' if trailing else 'Stop loss hit'
            logger.info(f'{sym}: {reason} | current=${current:.2f} stop=${pos["stop_price"]:.2f} | PnL=${pnl:.2f}')
            log_trade('CLOSE', sym, side, pos['shares'], current, pnl, reason=reason,
                      extra={'hold_minutes': _hold_minutes(pos['opened_at'])})
            record_realized_pnl(daily, sym, pnl)
            if not trailing:
                set_cooldown(cooldowns, sym, side)
            try:
                from common.notifier import notify
                action = 'STOP_LOSS' if not trailing else ('SELL' if long_pos else 'BUY')
                notify(action, sym, f'${current:.2f}', f'{reason} | P&L={pct_gain:+.1%} (${pnl:+.2f})', bot='DT-Stocks')
            except Exception:
                pass
            to_close.append(sym)

    return to_close, to_partial_close


# ── Position queries ──────────────────────────────────────────────────────────

def count_positions_for(state: dict, symbol: str) -> int:
    return 1 if symbol in state else 0


def count_direction(state: dict, side: str) -> int:
    return sum(1 for p in state.values() if p['side'] == side)


def remove_position(state: dict, symbol: str):
    state.pop(symbol, None)
