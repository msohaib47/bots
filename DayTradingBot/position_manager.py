"""
Tracks open option positions and manages:
  - Stop loss / trailing stop (thresholds in config.py)
  - Scale-out (off by default, HALF_CLOSE_ENABLED): closes half the contracts once,
    the first time profit hits HALF_CLOSE_PROFIT_PCT; the remainder keeps trailing

trades.csv columns as of 2026-09-05 (see EXTRA_COLUMNS below): every row still carries
the original timestamp/action/symbol/underlying/type/contracts/price/pnl/reason columns,
plus strike/expiration/underlying_price/premium_pct/RSI/ADX/ATR/EMA9/EMA21/VWAP/
htf_ema21/htf_slope/ema_gap_atr (OPEN rows only) and hold_minutes (CLOSE/PARTIAL_CLOSE
only) -- the same fields backtest.py records, so live trades can be analyzed the same
way. Rows from before this date use the original 9-column format (archived separately,
see .memory notes) -- don't assume every row in an old file has these columns.
"""
import json
import os
import sys
import logging
import csv
from datetime import datetime, timezone, timedelta

# Add parent directory to path to enable imports from common modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (STATE_FILE, TRADES_LOG, STOP_LOSS_PCT,
                    PROFIT_TRAIL_TRIGGER, TRAIL_WIGGLE, MAX_CONTRACTS_PER_SYMBOL, HALF_CLOSE_PROFIT_PCT, HALF_CLOSE_ENABLED,
                    COOLDOWN_FILE, COOLDOWN_MINUTES, DAILY_PNL_FILE, PNL_HISTORY_FILE, SNAPSHOT_FILE)

logger = logging.getLogger(__name__)


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


# ── State persistence ─────────────────────────────────────────────────────────

def load_state() -> dict:
    """
    Returns dict keyed by option symbol:
    {
      'SPYXXXXCXXXXXX': {
        'underlying': 'SPY',
        'type': 'call',
        'contracts': 2,
        'entry_cost': 1.50,      # per-share price paid (option premium)
        'high_water': 1.50,      # highest value seen since entry
        'trailing_active': False,
        'stop_price': None,      # per-share stop trigger
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


def save_account_snapshot(cash: float, portfolio_value: float):
    """Persist the broker's current cash/net-liq each tick so the dashboard can
    show real account balance without needing its own trading credentials."""
    with open(SNAPSHOT_FILE, 'w') as f:
        json.dump({
            'cash': cash,
            'net_liquidation_value': portfolio_value,
            'updated_at': _now_iso(),
        }, f, indent=2)


def load_account_snapshot() -> dict:
    if not os.path.exists(SNAPSHOT_FILE):
        return {}
    with open(SNAPSHOT_FILE) as f:
        return json.load(f)


def contracts_cap_for_balance(account_size: float) -> int:
    """Position size scales with account size so a small account can't blow
    itself up on one contract-heavy entry: 1 contract under $500, 2 under
    $1,000, 4 under $2,000, 10 (the config ceiling) at/above $2,000. The
    caller still applies MAX_CONTRACTS_PER_SYMBOL as an overall ceiling on
    top of this, in case that's ever configured below 10."""
    if account_size < 500:
        return 1
    if account_size < 1000:
        return 2
    if account_size < 2000:
        return 4
    return 10


# ── Signal log (every CALL/PUT signal, whether or not it became a trade) ───────
#
# Added 2026-09-08 for the merged multi-bot dashboard's shared "Signals" section:
# since DayTradingBot, DT-Bot-200, and DT-Webull all run the identical strategy
# against the identical symbol list, they'd otherwise each generate the exact
# same signal independently -- one shared log (from whichever bot's cron tick
# happens to run) is the single source of truth the dashboard reads, rather
# than three redundant copies. Every bot still writes its own signals.csv
# (matching the "sync all 3" convention and letting each bot's own dashboard
# panel work standalone if ever needed), but only DayTradingBot's is actually
# read by the merged dashboard -- see DAYTRADING_RULES.md.
SIGNALS_LOG = 'signals.csv'


def log_signal(symbol: str, sig: dict, outcome: str):
    """Call once per symbol per tick, only when sig['signal'] is CALL/PUT (NONE
    signals aren't logged -- too voluminous, no decision to record). `outcome`
    is 'TRADED' or a short SKIP_<reason> string describing why it didn't become
    a trade (mirrors backtest.py's SKIP_*/FILTER_* reason vocabulary where
    possible, so live and backtest data read the same way)."""
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


# Signal-diagnostic columns appended to every trade row (blank where not applicable --
# e.g. only OPEN rows carry entry-signal indicators, only CLOSE/PARTIAL_CLOSE carry
# hold_minutes). Mirrors the fields backtest.py records per trade, so live results can
# be analyzed the same way (premium %, EMA/RSI/ADX/ATR/VWAP/HTF context at entry).
EXTRA_COLUMNS = [
    'strike', 'expiration', 'underlying_price', 'premium_pct',
    'rsi', 'adx', 'atr', 'ema9', 'ema21', 'vwap', 'htf_ema21', 'htf_slope', 'ema_gap_atr',
    'hold_minutes',
]


def log_trade(action: str, symbol: str, underlying: str, opt_type: str,
              contracts: int, price: float, pnl: float = 0, reason: str = '',
              extra: dict | None = None):
    extra = extra or {}
    exists = os.path.exists(TRADES_LOG)
    with open(TRADES_LOG, 'a', newline='') as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(['timestamp', 'action', 'symbol', 'underlying', 'type',
                        'contracts', 'price', 'pnl', 'reason'] + EXTRA_COLUMNS)
        w.writerow([_now_iso(), action, symbol, underlying, opt_type,
                    contracts, price, round(pnl, 2), reason] +
                   [extra.get(c, '') for c in EXTRA_COLUMNS])


# ── Position registration ─────────────────────────────────────────────────────

def register_open(state: dict, contract: dict, qty: int, filled_price: float, order_id: str,
                   sig: dict | None = None):
    """
    `sig` is the signals.get_signal() dict that produced this entry (bot.py has it in
    hand already) -- recorded on the OPEN row so trades.csv carries the same entry
    diagnostics backtest.py does (premium %, RSI/ADX/ATR/EMA/VWAP/HTF context).
    """
    sym = contract['symbol']
    state[sym] = {
        'underlying':       contract['underlying'],
        'type':             contract['type'],
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
    log_trade('OPEN', sym, contract['underlying'], contract['type'],
              qty, filled_price, reason=(sig.get('reason') if sig else 'Entry'), extra=extra)
    logger.info(f'Position registered: {sym} {qty}x @ ${filled_price:.2f} | stop=${state[sym]["stop_price"]:.2f}')


def _hold_minutes(opened_at: str) -> float:
    return round((datetime.now(timezone.utc) - datetime.fromisoformat(opened_at)).total_seconds() / 60, 1)


def _underlying_price(underlying: str):
    """Best-effort spot price for the CLOSE row's underlying_price column -- never blocks a close on failure."""
    try:
        import alpaca
        return alpaca.get_latest_price(underlying)
    except Exception:
        return ''


# ── Cool-down + signal reset (per underlying, after a stop-loss) ───────────────
#
# After a stop-loss, `underlying` is blocked from new entries until EITHER:
#   - the stopped-out signal actually breaks down (reset observed), confirming
#     the failed setup is gone rather than still-active noise, or
#   - COOLDOWN_MINUTES elapses regardless (safety cap, in case the market just
#     chops sideways and the reset condition never clearly fires)
#
# Reset condition (mirrors the entry signal's own invalidation):
#   stopped out of a CALL: EMA9 <= EMA21   OR   price crosses below VWAP
#   stopped out of a PUT:  EMA9 >= EMA21   OR   price crosses above VWAP

def load_cooldowns() -> dict:
    """
    Returns dict keyed by underlying symbol -> {
        'until':      ISO timestamp, safety-cap expiry,
        'direction':  'call' | 'put'  (the direction that got stopped out),
        'reset_seen': bool,
        'vwap_side':  'above' | 'below' | None  (last observed side, for cross detection),
    }
    """
    if not os.path.exists(COOLDOWN_FILE):
        return {}
    with open(COOLDOWN_FILE) as f:
        return json.load(f)


def save_cooldowns(cooldowns: dict):
    with open(COOLDOWN_FILE, 'w') as f:
        json.dump(cooldowns, f, indent=2)


def set_cooldown(cooldowns: dict, underlying: str, direction: str):
    """Start (or restart) the cool-down window for `underlying` after a stop-loss hit."""
    expires = datetime.now(timezone.utc) + timedelta(minutes=COOLDOWN_MINUTES)
    cooldowns[underlying] = {
        'until': expires.isoformat(),
        'direction': direction,
        'reset_seen': False,
        'vwap_side': None,
    }
    logger.info(f'{underlying}: cool-down started ({direction}), blocked until {expires.isoformat()} or signal reset')


def update_cooldown_reset(cooldowns: dict, underlying: str, price: float | None,
                           vwap: float | None, ema9: float | None, ema21: float | None):
    """
    Call once per tick with the underlying's current indicators while it's in
    cool-down. Marks the cool-down's signal as reset the first time the
    stopped-out direction's setup actually breaks down.
    """
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
    """True if `underlying` is still blocked from new entries (see module notes above)."""
    c = cooldowns.get(underlying)
    if not c:
        return False
    if c.get('reset_seen'):
        return False
    return datetime.now(timezone.utc) < datetime.fromisoformat(c['until'])


# ── Daily realized P&L (per symbol + total, resets each calendar day) ──────────

def _today_str() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def load_daily_pnl() -> dict:
    """Returns {'date': 'YYYY-MM-DD', 'total': float, 'per_symbol': {underlying: float}}."""
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


def record_realized_pnl(daily: dict, underlying: str, pnl: float):
    daily['total'] = daily.get('total', 0.0) + pnl
    daily['per_symbol'][underlying] = daily['per_symbol'].get(underlying, 0.0) + pnl
    _record_lifetime_pnl(pnl)


# ── Lifetime realized P&L (informational, shown by --status) ──────────────────

def load_pnl_history() -> dict:
    """Returns {'cum': float, 'peak': float} -- cumulative realized P&L and its high-water mark."""
    if os.path.exists(PNL_HISTORY_FILE):
        with open(PNL_HISTORY_FILE) as f:
            return json.load(f)
    return {'cum': 0.0, 'peak': 0.0}


def save_pnl_history(hist: dict):
    with open(PNL_HISTORY_FILE, 'w') as f:
        json.dump(hist, f, indent=2)


def _record_lifetime_pnl(pnl: float):
    """Read-modify-write immediately (called once per realized close, at most a few times/min)."""
    hist = load_pnl_history()
    hist['cum']  = hist.get('cum', 0.0) + pnl
    hist['peak'] = max(hist.get('peak', 0.0), hist['cum'])
    save_pnl_history(hist)


# ── Open exposure (premium currently tied up) ─────────────────────────────────

def open_exposure(state: dict) -> float:
    """Total premium paid for all open contracts, at entry cost (not current mark)."""
    return sum(p['entry_cost'] * p['contracts'] * 100 for p in state.values())


def symbol_daily_loss_exceeded(daily: dict, underlying: str, max_loss: float) -> bool:
    return daily['per_symbol'].get(underlying, 0.0) <= -max_loss


def total_daily_loss_exceeded(daily: dict, max_loss: float) -> bool:
    return daily.get('total', 0.0) <= -max_loss


# ── Stop management ───────────────────────────────────────────────────────────

def check_and_update_stops(state: dict, current_prices: dict, cooldowns: dict, daily: dict) -> tuple:
    """
    Check each position against its stop price.
    Update trailing stops if profit >= PROFIT_TRAIL_TRIGGER.
    Returns (to_close, to_partial_close) -- DECISIONS only, no broker calls and no
    trade-log/P&L/notify/cooldown side effects. Those are deferred to the caller
    (bot.py) and must only happen after the broker close order is CONFIRMED to have
    succeeded -- see finalize_close()/finalize_partial_close() below.

    Found + fixed 2026-09-08: this function used to log the CLOSE trade, record
    realized P&L, set the cool-down, and send a notification immediately upon
    detecting a stop breach -- BEFORE bot.py had even attempted the actual broker
    close. bot.py's close_option_position() call right after was never checked for
    failure, so a failed/errored close order still got recorded as a successful
    close and the position was purged from state regardless -- orphaning a real,
    still-open Alpaca position with zero further management (confirmed live: an
    NVDA put's 2026-09-08 15:58 ET force-close failed silently this exact way,
    leaving it open and untracked with the bot providing zero further stop-loss
    protection). Moving all logging/P&L/notify to only fire after a confirmed
    success fixes the orphan risk AND avoids the alternative failure mode of
    re-logging a duplicate close every retry tick if the position were simply left
    in place unconditionally.
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

        entry     = pos['entry_cost']
        high      = pos['high_water']
        trailing  = pos['trailing_active']
        pct_gain  = (current - entry) / entry

        # Update high water mark
        if current > high:
            pos['high_water'] = current

            # Update trailing stop if active
            if trailing:
                new_stop = current * (1 - TRAIL_WIGGLE)
                if new_stop > pos['stop_price']:
                    pos['stop_price'] = round(new_stop, 4)
                    logger.info(f'{sym}: trailing stop raised to ${pos["stop_price"]:.2f} (high=${current:.2f})')

        # Activate trailing stop when profit hits trigger
        if not trailing and pct_gain >= PROFIT_TRAIL_TRIGGER:
            pos['trailing_active'] = True
            pos['stop_price'] = round(current * (1 - TRAIL_WIGGLE), 4)
            logger.info(f'{sym}: trailing stop ACTIVATED at ${pos["stop_price"]:.2f} | profit={pct_gain:.1%}')
            trailing = True

        # Scale out (if HALF_CLOSE_ENABLED): close half the contracts once, the first
        # time profit hits HALF_CLOSE_PROFIT_PCT. No-op on a 1-contract position.
        # Doesn't mutate pos['contracts']/half_closed here -- that only happens on
        # confirmed success, in finalize_partial_close().
        if HALF_CLOSE_ENABLED and not pos.get('half_closed') and pct_gain >= HALF_CLOSE_PROFIT_PCT:
            half_qty = pos['contracts'] // 2
            if half_qty >= 1:
                partial_pnl = (current - entry) * half_qty * 100
                to_partial_close.append({'symbol': sym, 'pos': pos, 'current': current,
                                          'half_qty': half_qty, 'partial_pnl': partial_pnl, 'pct_gain': pct_gain})

        # Check stop
        if current <= pos['stop_price']:
            pnl = (current - entry) * pos['contracts'] * 100
            reason = 'Trailing stop hit' if trailing else 'Stop loss hit'
            to_close.append({'symbol': sym, 'pos': pos, 'current': current,
                              'reason': reason, 'pnl': pnl, 'trailing': trailing, 'pct_gain': pct_gain})

    return to_close, to_partial_close


def finalize_partial_close(sym: str, pos: dict, current: float, half_qty: int,
                            partial_pnl: float, pct_gain: float, daily: dict):
    """Call ONLY after alpaca.close_option_position() for this scale-out has
    confirmed success. Mutates pos/state, logs the trade, records P&L, notifies."""
    pos['half_closed'] = True
    pos['contracts'] -= half_qty
    logger.info(f'{sym}: scaling out {half_qty}x at +{pct_gain:.1%} profit | remaining {pos["contracts"]}x')
    log_trade('PARTIAL_CLOSE', sym, pos['underlying'], pos['type'],
              half_qty, current, partial_pnl, reason='Scale-out +50%',
              extra={'underlying_price': _underlying_price(pos['underlying']),
                     'hold_minutes': _hold_minutes(pos['opened_at'])})
    record_realized_pnl(daily, pos['underlying'], partial_pnl)
    try:
        from common.notifier import notify
        notify('SELL', sym, f'${current:.2f}',
               f'Scaled out {half_qty}x at +{pct_gain:.1%} | {pos["contracts"]}x remaining', bot='DayTradingBot')
    except Exception:
        pass


def finalize_close(sym: str, pos: dict, current: float, reason: str, pnl: float,
                    trailing: bool, pct_gain: float, cooldowns: dict, daily: dict):
    """Call ONLY after alpaca.close_option_position() for this stop/trail exit has
    confirmed success. Logs the trade, records P&L, sets cool-down, notifies.
    Does NOT remove from state -- caller does that once this returns."""
    logger.info(f'{sym}: {reason} | current=${current:.2f} | PnL=${pnl:.2f}')
    log_trade('CLOSE', sym, pos['underlying'], pos['type'],
              pos['contracts'], current, pnl, reason=reason,
              extra={'underlying_price': _underlying_price(pos['underlying']),
                     'hold_minutes': _hold_minutes(pos['opened_at'])})
    record_realized_pnl(daily, pos['underlying'], pnl)
    if not trailing:
        set_cooldown(cooldowns, pos['underlying'], pos['type'])
    try:
        from common.notifier import notify
        action = 'STOP_LOSS' if not trailing else 'SELL'
        notify(action, sym, f'${current:.2f}', f'{reason} | P&L={pct_gain:+.1%} (${pnl:+.2f})', bot='DayTradingBot')
    except Exception:
        pass


def report_close_failed(sym: str, reason: str):
    """Call when a close order (force-close, stop/trail, or scale-out) errors or
    is rejected -- fires an alert so this isn't silently missed, but deliberately
    does NOT touch state: the position stays tracked exactly as it was so the
    very next tick retries the close, instead of being purged from tracking while
    still genuinely open on the broker (the exact bug this whole refactor fixes)."""
    logger.error(f'{sym}: close order FAILED ({reason}) -- left tracked, will retry next tick')
    try:
        from common.notifier import notify
        notify('ERROR', sym, '', f'{reason} close order FAILED -- still open on the broker, retrying next tick', bot='DayTradingBot')
    except Exception:
        pass


# ── Position queries ──────────────────────────────────────────────────────────

def get_open_underlyings(state: dict) -> set[str]:
    return {pos['underlying'] for pos in state.values()}


def count_contracts_for(state: dict, underlying: str) -> int:
    return sum(p['contracts'] for p in state.values() if p['underlying'] == underlying)


def count_positions_for(state: dict, underlying: str) -> int:
    """Number of separate open option positions (entries in state) on this underlying."""
    return sum(1 for p in state.values() if p['underlying'] == underlying)


def count_direction(state: dict, opt_type: str) -> int:
    """Number of open positions across all symbols in the given direction ('call'/'put')."""
    return sum(1 for p in state.values() if p['type'].lower() == opt_type.lower())


def remove_position(state: dict, symbol: str):
    state.pop(symbol, None)
