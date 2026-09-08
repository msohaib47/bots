"""
DT-Webull — Webull-API variant of DayTradingBot (v1). Same strategy, same
trading rules (config.py's thresholds are byte-identical defaults to
DayTradingBot/config.py), same 0DTE-options-scalp shape. Runs every minute.

Multi-account: configure any number of accounts via WEBULL_ACCOUNTS in .env
(see config.py's docstring). Each cron tick computes every symbol's signal
ONCE (signals are account-independent -- same market data regardless of which
account trades it) and then runs the full force-close/stop-check/entry
pipeline once per configured account, each against its own state files and
its own WebullClient.

Usage:
  python bot.py                        -- single run, all configured accounts
  python bot.py --status [ACCOUNT]     -- show open positions and P&L
  python bot.py --close [ACCOUNT]      -- force close all positions now
(omit ACCOUNT to apply to every configured account)
"""
import logging
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# Add parent directory to path to enable imports from common modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import signals
import position_manager as pm
from webull import WebullClient
from config import (SYMBOLS, ACCOUNTS, MAX_CONTRACTS, LOG_FILE,
                    NO_NEW_ENTRY_TIME, FORCE_CLOSE_TIME, STOP_LOSS_PCT,
                    MAX_DAILY_LOSS_PER_SYMBOL, MAX_DAILY_LOSS_TOTAL,
                    MAX_SAME_DIRECTION, MAX_POSITIONS_PER_SYMBOL, MIN_CONTRACT_PRICE,
                    MAX_OPEN_EXPOSURE, EXPOSURE_TOLERANCE_PCT, MAX_PREMIUM_PCT, CASH_PER_TRADE_PCT)

# ── Logging ────────────────────────────────────────────────────────────────────
os.makedirs('logs', exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler(LOG_FILE, encoding='utf-8'), logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

ET = ZoneInfo('America/New_York')


# ── Time helpers ────────────────────────────────────────────────────────────────

def et_now() -> datetime:
    return datetime.now(ET)


def et_time_str() -> str:
    return et_now().strftime('%H:%M')


def is_market_open() -> bool:
    now = et_now()
    if now.weekday() >= 5:
        return False
    t = now.strftime('%H:%M')
    return '09:30' <= t <= '16:00'


def can_open_new_position() -> bool:
    return et_time_str() < NO_NEW_ENTRY_TIME


def should_force_close() -> bool:
    return et_time_str() >= FORCE_CLOSE_TIME


def is_pdt_blocked(paths: dict) -> bool:
    flag_file = paths['pdt_flag_file']
    if not os.path.exists(flag_file):
        return False
    flag_day = datetime.fromtimestamp(os.path.getmtime(flag_file)).date()
    if flag_day < et_now().date():
        os.remove(flag_file)
        return False
    return True


def set_pdt_blocked(paths: dict):
    os.makedirs(os.path.dirname(paths['pdt_flag_file']), exist_ok=True)
    with open(paths['pdt_flag_file'], 'w') as f:
        f.write(et_now().isoformat())


# ── Current option prices (for stop management) ───────────────────────────────

def get_current_option_prices(client: WebullClient, state: dict) -> dict:
    if not state:
        return {}
    snaps = client.get_snapshots_by_symbols(list(state.keys()))
    prices = {}
    for sym in state:
        quote = snaps.get(sym, {}).get('latestQuote', {})
        bid = float(quote.get('bp', 0) or 0)
        ask = float(quote.get('ap', 0) or 0)
        if ask > 0:
            prices[sym] = (bid + ask) / 2 if bid > 0 else ask
        elif bid > 0:
            prices[sym] = bid
        else:
            logger.warning(f'No quote for {sym}')
    return prices


def close_all_positions(paths: dict, client: WebullClient, state: dict, reason: str, account_name: str):
    if not state:
        logger.info(f'[{account_name}] No open positions to close.')
        return
    prices = get_current_option_prices(client, state)
    for sym, pos in list(state.items()):
        logger.info(f'[{account_name}] Closing {sym} ({reason})')
        client.close_option_position(pm.contract_from_position(sym, pos), pos['contracts'], limit_price=prices.get(sym))
        pm.log_trade(paths, 'CLOSE', sym, pos['underlying'], pos['type'],
                     pos['contracts'], 0, reason=reason,
                     extra={'underlying_price': pm._underlying_price(client, pos['underlying']),
                            'hold_minutes': pm._hold_minutes(pos['opened_at'])})
        pm.remove_position(state, sym)
    pm.save_state(paths, state)


# ── Per-account tick ───────────────────────────────────────────────────────────

def run_account(name: str, acct_cfg: dict, signals_cache: dict):
    """`signals_cache` is {symbol: sig dict}, computed once per tick in run()
    and shared across every account -- signals don't depend on which account
    trades them."""
    paths = acct_cfg
    client = WebullClient(acct_cfg['app_key'], acct_cfg['app_secret'], acct_cfg['account_id'], base_url=acct_cfg['base_url'])

    state     = pm.load_state(paths)
    cooldowns = pm.load_cooldowns(paths)
    daily     = pm.load_daily_pnl(paths)

    if should_force_close():
        logger.info(f'[{name}] EOD force close triggered.')
        close_all_positions(paths, client, state, 'EOD force close', name)
        return

    if state:
        current_prices = get_current_option_prices(client, state)
        to_close, to_partial_close = pm.check_and_update_stops(
            paths, client, state, current_prices, cooldowns, daily, account_name=name)

        for sym, qty in to_partial_close:
            if sym in to_close:
                continue
            logger.info(f'[{name}] Executing partial close for {sym}: {qty}x')
            pos = state.get(sym, {})
            client.close_option_position(pm.contract_from_position(sym, pos), qty, limit_price=current_prices.get(sym))

        for sym in to_close:
            pos = state.get(sym, {})
            logger.info(f'[{name}] Executing close for {sym}')
            client.close_option_position(pm.contract_from_position(sym, pos), pos.get('contracts', 1), limit_price=current_prices.get(sym))
            pm.remove_position(state, sym)

        pm.save_state(paths, state)
        pm.save_cooldowns(paths, cooldowns)
        pm.save_daily_pnl(paths, daily)

    if not can_open_new_position():
        logger.info(f'[{name}] Past {NO_NEW_ENTRY_TIME} ET — no new entries.')
        return

    if is_pdt_blocked(paths):
        logger.info(f'[{name}] PDT protection active — managing existing positions only.')
        return

    if pm.total_daily_loss_exceeded(daily, MAX_DAILY_LOSS_TOTAL):
        logger.info(f'[{name}] Max total daily loss (${MAX_DAILY_LOSS_TOTAL:.0f}) hit — no new entries today.')
        return

    cash = client.get_cash()
    logger.info(f'[{name}] Cash: ${cash:,.2f}')

    for symbol in SYMBOLS:
        if pm.count_positions_for(state, symbol) >= MAX_POSITIONS_PER_SYMBOL:
            continue
        if pm.symbol_daily_loss_exceeded(daily, symbol, MAX_DAILY_LOSS_PER_SYMBOL):
            continue

        sig = signals_cache.get(symbol)
        if sig is None:
            continue

        if symbol in cooldowns:
            pm.update_cooldown_reset(cooldowns, symbol, sig['price'], sig['vwap'], sig['ema9'], sig['ema21'])
        if pm.is_in_cooldown(cooldowns, symbol):
            continue

        if sig['signal'] == 'NONE':
            continue

        opt_type = 'call' if sig['signal'] == 'CALL' else 'put'
        spot = sig['price']

        if pm.count_direction(state, opt_type) >= MAX_SAME_DIRECTION:
            continue

        contract = client.find_atm_contract(symbol, opt_type, spot)
        if not contract:
            continue

        if contract['mid'] < MIN_CONTRACT_PRICE:
            continue

        prem_pct = contract['mid'] / spot * 100 if spot else 0
        if prem_pct > MAX_PREMIUM_PCT:
            continue

        cost_per_contract = contract['mid'] * 100
        max_afford = int(cash * CASH_PER_TRADE_PCT / cost_per_contract) if cost_per_contract else 0
        exposure = pm.open_exposure(state)
        room = MAX_OPEN_EXPOSURE * (1 + EXPOSURE_TOLERANCE_PCT) - exposure
        max_fit = int(room / cost_per_contract) if room > 0 else 0
        qty = min(MAX_CONTRACTS, max_afford, max_fit)

        if qty < 1:
            continue

        order = client.buy_option(contract, qty)
        if not order:
            if client.last_error_code == 40310100:
                logger.warning(f'[{name}] PDT protection triggered — blocking new entries for today')
                set_pdt_blocked(paths)
            continue

        filled_price = contract['mid'] + 0.01
        pm.register_open(paths, state, contract, qty, filled_price, order.get('client_order_id', ''), sig=sig)
        pm.save_state(paths, state)

        logger.info(f'[{name}] ENTERED: {symbol} {opt_type.upper()} {qty}x {contract["symbol"]} @ ${filled_price:.2f}')
        try:
            from common.notifier import notify
            notify('BUY', contract['symbol'], f'${filled_price:.2f}',
                   f'{symbol} {opt_type.upper()} x{qty} | stop=${round(filled_price*(1-STOP_LOSS_PCT),2)}',
                   bot=f'DT-Webull:{name}')
        except Exception:
            pass

    pm.save_cooldowns(paths, cooldowns)


# ── Main run ───────────────────────────────────────────────────────────────────

def run():
    logger.info(f'--- DT-Webull tick | ET {et_time_str()} | accounts={list(ACCOUNTS)} ---')

    if not ACCOUNTS:
        logger.warning('No WEBULL_ACCOUNTS configured in .env — nothing to do.')
        return

    if not is_market_open():
        logger.info('Market closed, nothing to do.')
        return

    # Compute every symbol's signal once, shared across all accounts.
    signals_cache = {symbol: signals.get_signal(symbol) for symbol in SYMBOLS}
    for symbol, sig in signals_cache.items():
        logger.info(f'{symbol}: signal={sig["signal"]} | {sig["reason"]}')

    for name, acct_cfg in ACCOUNTS.items():
        try:
            run_account(name, acct_cfg, signals_cache)
        except Exception:
            logger.exception(f'[{name}] Unhandled error during account tick')


# ── Status display ────────────────────────────────────────────────────────────

def print_status(account_filter: str = None):
    accounts = {account_filter: ACCOUNTS[account_filter]} if account_filter else ACCOUNTS
    if not accounts:
        print('No accounts configured (or unknown --status account name).')
        return

    for name, paths in accounts.items():
        client = WebullClient(paths['app_key'], paths['app_secret'], paths['account_id'], base_url=paths['base_url'])
        state = pm.load_state(paths)
        daily = pm.load_daily_pnl(paths)
        cash = client.get_cash()

        print(f'\n{"="*60}')
        print(f'  DT-Webull [{name}] | ET {et_time_str()} | {datetime.now(timezone.utc).strftime("%Y-%m-%d")}')
        print(f'  Cash: ${cash:,.2f}')
        print(f'  Realized P&L today: ${daily.get("total", 0):+,.2f} (limit: -${MAX_DAILY_LOSS_TOTAL:.0f})')
        hist = pm.load_pnl_history(paths)
        print(f'  Lifetime realized: ${hist.get("cum", 0):+,.2f} (peak ${hist.get("peak", 0):+,.2f})')
        print(f'  Open exposure: ${pm.open_exposure(state):,.2f} / ${MAX_OPEN_EXPOSURE:,.0f} cap')
        print(f'{"="*60}')

        if not state:
            print('  No open positions.\n')
            continue

        print(f'\n  Open Positions ({len(state)}):')
        current_prices = get_current_option_prices(client, state)
        for sym, pos in state.items():
            cur = current_prices.get(sym, 0)
            entry = pos['entry_cost']
            pct = (cur - entry) / entry * 100 if entry else 0
            pnl = (cur - entry) * pos['contracts'] * 100
            trail = 'TRAILING' if pos['trailing_active'] else 'fixed'
            print(f'  {sym}')
            print(f'    {pos["underlying"]} {pos["type"].upper()} x{pos["contracts"]}')
            print(f'    Entry: ${entry:.2f} | Current: ${cur:.2f} | P&L: {pct:+.1f}% (${pnl:+.2f})')
            print(f'    Stop: ${pos["stop_price"]:.2f} ({trail}) | High: ${pos["high_water"]:.2f}')
            print()


if __name__ == '__main__':
    args = sys.argv[1:]

    if '--status' in args:
        idx = args.index('--status')
        acct = args[idx + 1] if idx + 1 < len(args) else None
        print_status(acct)
    elif '--close' in args:
        idx = args.index('--close')
        acct = args[idx + 1] if idx + 1 < len(args) else None
        targets = {acct: ACCOUNTS[acct]} if acct else ACCOUNTS
        for name, paths in targets.items():
            client = WebullClient(paths['app_key'], paths['app_secret'], paths['account_id'], base_url=paths['base_url'])
            state = pm.load_state(paths)
            close_all_positions(paths, client, state, 'Manual close', name)
    else:
        run()
