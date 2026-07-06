"""
Robinhood API wrapper using robin_stocks 2.1.0.

NOTE on accounts: the standard Robinhood API (what robin_stocks uses) only
exposes the margin brokerage account (5UN85130). The separate "Agentic" cash
account (706672094) is only reachable via the MCP connector, which a headless
cron bot cannot use. So this bot trades the default (margin) account.

DRY_RUN safety: when DRY_RUN is True (default), no real orders are placed —
the bot logs intended trades only. Set DRY_RUN=false in .env to go live.

Auth uses a saved session (~/.tokens/robinhood.pickle). Run login.py once
interactively to authenticate; cron runs reuse the session automatically.
"""
import logging
import sys

import robin_stocks.robinhood as rh
from config import RH_USERNAME, RH_PASSWORD, DRY_RUN

logger = logging.getLogger(__name__)
_logged_in = False


# ── Auth ───────────────────────────────────────────────────────────────────────

def login():
    """Login using saved pickle session (set up once via login.py)."""
    global _logged_in
    if _logged_in:
        return True
    try:
        rh.login(username=RH_USERNAME, password=RH_PASSWORD, store_session=True)
        _logged_in = True
        logger.info('Robinhood login OK (session reused)')
        return True
    except Exception as e:
        logger.error(f'Robinhood login failed: {e}')
        logger.error('Refresh session: ssh -t claude@192.168.1.250 '
                     '"cd /home/claude/bots/RobinhoodDayTradingBot && python3 login.py"')
        return False


def logout():
    try:
        rh.logout()
    except Exception:
        pass


# ── Account ────────────────────────────────────────────────────────────────────

def get_account() -> dict:
    """Return buying power and portfolio value for the default account."""
    try:
        profile = rh.profiles.load_account_profile()
        portfolio = rh.profiles.load_portfolio_profile()
        # Buying power lives on the account profile
        bp = float(profile.get('buying_power', 0) or 0)
        if not bp:
            bp = float(profile.get('cash', 0) or 0)
        equity = float(portfolio.get('equity', 0) or 0)
        return {
            'buying_power':    bp,
            'portfolio_value': equity if equity else bp,
            'cash':            float(profile.get('cash', 0) or 0),
        }
    except Exception as e:
        logger.error(f'get_account error: {e}')
        return {'buying_power': 0, 'portfolio_value': 0, 'cash': 0}


# ── Quotes & bars ──────────────────────────────────────────────────────────────

def get_latest_price(symbol: str) -> float | None:
    try:
        prices = rh.stocks.get_latest_price(symbol)
        if prices and prices[0]:
            return float(prices[0])
    except Exception as e:
        logger.error(f'get_latest_price({symbol}) error: {e}')
    return None


def get_5min_bars(symbol: str, limit: int = 60) -> list[dict]:
    """Returns list of OHLCV dicts with keys o, h, l, c, v."""
    try:
        bars = rh.stocks.get_stock_historicals(symbol, interval='5minute', span='day', bounds='regular')
        if not bars:
            return []
        out = []
        for b in bars[-limit:]:
            # Robinhood returns nulls for some pre/post bars; skip them
            if b.get('open_price') is None or b.get('close_price') is None:
                continue
            out.append({
                'o': float(b['open_price']),
                'h': float(b['high_price']),
                'l': float(b['low_price']),
                'c': float(b['close_price']),
                'v': float(b['volume']),
            })
        return out
    except Exception as e:
        logger.error(f'get_5min_bars({symbol}) error: {e}')
        return []


# ── Positions ──────────────────────────────────────────────────────────────────

def get_position(symbol: str) -> dict | None:
    try:
        positions = rh.account.get_open_stock_positions()
        for pos in positions:
            if float(pos.get('quantity', 0)) <= 0:
                continue
            instrument = rh.stocks.get_instrument_by_url(pos['instrument'])
            if instrument and instrument.get('symbol', '').upper() == symbol.upper():
                return {
                    'symbol':    symbol,
                    'qty':       float(pos.get('quantity', 0)),
                    'avg_price': float(pos.get('average_buy_price', 0)),
                }
    except Exception as e:
        logger.error(f'get_position({symbol}) error: {e}')
    return None


def list_positions() -> list[dict]:
    try:
        positions = rh.account.get_open_stock_positions()
        result = []
        for pos in positions:
            if float(pos.get('quantity', 0)) <= 0:
                continue
            try:
                instrument = rh.stocks.get_instrument_by_url(pos['instrument'])
                symbol = instrument.get('symbol', '?') if instrument else '?'
            except Exception:
                symbol = '?'
            result.append({
                'symbol':    symbol,
                'qty':       float(pos.get('quantity', 0)),
                'avg_price': float(pos.get('average_buy_price', 0)),
            })
        return result
    except Exception as e:
        logger.error(f'list_positions error: {e}')
        return []


# ── Orders (DRY_RUN gated) ─────────────────────────────────────────────────────

def buy_market(symbol: str, dollars: float) -> dict | None:
    """Place a fractional market buy by dollar amount."""
    if DRY_RUN:
        logger.info(f'[DRY_RUN] Would BUY {symbol} for ${dollars:.2f} (no real order placed)')
        return {'id': f'dryrun-buy-{symbol}', 'dry_run': True}
    try:
        order = rh.orders.order_buy_fractional_by_price(
            symbol, amountInDollars=dollars, timeInForce='gfd',
        )
        if order and order.get('id'):
            logger.info(f'BUY order placed: {symbol} ${dollars:.2f} | id={order["id"]}')
            return order
        logger.error(f'BUY order failed for {symbol}: {order}')
    except Exception as e:
        logger.error(f'buy_market({symbol}) error: {e}')
    return None


def sell_all(symbol: str) -> dict | None:
    """Sell entire position in symbol at market."""
    pos = get_position(symbol)
    if not pos or pos['qty'] <= 0:
        logger.warning(f'sell_all({symbol}): no position found')
        return None
    if DRY_RUN:
        logger.info(f'[DRY_RUN] Would SELL {symbol} {pos["qty"]} shares (no real order placed)')
        return {'id': f'dryrun-sell-{symbol}', 'dry_run': True}
    try:
        order = rh.orders.order_sell_fractional_by_quantity(
            symbol, quantity=pos['qty'], timeInForce='gfd',
        )
        if order and order.get('id'):
            logger.info(f'SELL order placed: {symbol} {pos["qty"]} shares | id={order["id"]}')
            return order
        logger.error(f'SELL order failed for {symbol}: {order}')
    except Exception as e:
        logger.error(f'sell_all({symbol}) error: {e}')
    return None


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
    if login():
        acct = get_account()
        print(f'Login OK | mode={"DRY_RUN" if DRY_RUN else "LIVE"}')
        print(f'  Buying power:  ${acct["buying_power"]:,.2f}')
        print(f'  Portfolio:     ${acct["portfolio_value"]:,.2f}')
        positions = list_positions()
        if positions:
            print('  Positions:')
            for p in positions:
                print(f'    {p["symbol"]}: {p["qty"]} @ ${p["avg_price"]:.2f}')
    else:
        sys.exit(1)
