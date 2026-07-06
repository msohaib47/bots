import logging
import alpaca_trade_api as tradeapi
from config import ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_BASE_URL, TRADE_AMOUNT_USD, MAX_POSITION_USD

logger = logging.getLogger(__name__)

api = tradeapi.REST(
    key_id=ALPACA_API_KEY,
    secret_key=ALPACA_SECRET_KEY,
    base_url=ALPACA_BASE_URL,
)


def get_account():
    return api.get_account()


def get_price(ticker: str) -> float | None:
    try:
        bars = api.get_latest_bar(ticker)
        return float(bars.c)
    except Exception as e:
        logger.warning(f'Could not get price for {ticker}: {e}')
        return None


def get_position_value(ticker: str) -> float:
    try:
        pos = api.get_position(ticker)
        return float(pos.market_value)
    except Exception:
        return 0.0


def calculate_qty(ticker: str, dollar_amount: float) -> int:
    price = get_price(ticker)
    if not price or price <= 0:
        return 0
    qty = int(dollar_amount / price)
    return max(qty, 1)


def execute_trade(ticker: str, action: str, trade_amount: float = None) -> dict | None:
    """
    Execute a buy or sell trade on Alpaca.
    action: 'Purchase' or 'Sale' (matches QuiverQuant terminology)
    Returns order dict or None on failure.
    """
    trade_amount = trade_amount or TRADE_AMOUNT_USD
    side = 'buy' if action.lower() in ('purchase', 'buy') else 'sell'

    # For sells: check we actually hold the position
    if side == 'sell':
        position_value = get_position_value(ticker)
        if position_value <= 0:
            logger.info(f'Skipping sell {ticker} — no position held')
            return None

    # For buys: don't exceed max position size
    if side == 'buy':
        current_pos = get_position_value(ticker)
        if current_pos >= MAX_POSITION_USD:
            logger.info(f'Skipping buy {ticker} — already at max position (${current_pos:.0f})')
            return None

    qty = calculate_qty(ticker, trade_amount)
    if qty <= 0:
        logger.warning(f'Could not calculate qty for {ticker}')
        return None

    try:
        order = api.submit_order(
            symbol=ticker,
            qty=qty,
            side=side,
            type='market',
            time_in_force='day',
        )
        logger.info(f'Order submitted: {side.upper()} {qty} shares of {ticker} | ID: {order.id}')
        return {
            'order_id': order.id,
            'ticker': ticker,
            'side': side,
            'qty': qty,
            'status': order.status,
        }
    except Exception as e:
        logger.error(f'Failed to execute {side} {ticker}: {e}')
        return None


def get_portfolio_summary() -> dict:
    account = get_account()
    try:
        positions = api.list_positions()
        pos_list = [
            {
                'ticker': p.symbol,
                'qty': p.qty,
                'market_value': float(p.market_value),
                'unrealized_pl': float(p.unrealized_pl),
                'unrealized_plpc': float(p.unrealized_plpc) * 100,
            }
            for p in positions
        ]
    except Exception:
        pos_list = []

    return {
        'account_value': float(account.portfolio_value),
        'buying_power': float(account.buying_power),
        'cash': float(account.cash),
        'positions': pos_list,
    }
