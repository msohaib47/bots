"""
Robinhood API wrapper for RobinhoodSafeGuard.

Shares the ~/.tokens/robinhood.pickle session with RobinhoodDayTradingBot —
no separate login needed. Exposes only what SafeGuard needs: open option
positions with live mark prices, open stop orders, and stop order management.

Options are quoted per-share (a $5.00 mark = $500 per contract of 100 shares).
All price params here are per-share; the underlying API expects the same.
"""
import logging

import robin_stocks.robinhood as rh

from config import RH_USERNAME, RH_PASSWORD, DRY_RUN

logger = logging.getLogger(__name__)
_logged_in = False


def login() -> bool:
    global _logged_in
    if _logged_in:
        return True
    try:
        rh.login(username=RH_USERNAME, password=RH_PASSWORD, store_session=True)
        _logged_in = True
        logger.info('Robinhood login OK (session reused)')
        return True
    except Exception as e:
        logger.error(f'Login failed: {e}')
        logger.error('Refresh session: ssh -t claude@192.168.1.250 '
                     '"cd ~/bots/RobinhoodDayTradingBot && python3 login.py"')
        return False


def _url_to_id(url: str) -> str:
    return url.rstrip('/').split('/')[-1]


def get_open_option_positions() -> list[dict]:
    """
    Returns open LONG option positions with instrument details and live mark
    price merged in. Short positions (covered calls, CSPs) are skipped.

    Each dict: option_id, option_url, symbol, expiration_date, strike_price,
               option_type ('call'/'put'), quantity (contracts), mark_price,
               avg_open_price.
    """
    try:
        raw = rh.options.get_open_option_positions() or []
    except Exception as e:
        logger.error(f'get_open_option_positions error: {e}')
        return []

    result = []
    for pos in raw:
        qty = float(pos.get('quantity', 0))
        if qty <= 0:
            continue
        if pos.get('type') != 'long':
            logger.debug(f'Skipping short position: {pos.get("chain_symbol")}')
            continue

        option_url = pos.get('option', '')
        if not option_url:
            continue
        option_id = _url_to_id(option_url)

        try:
            instrument = rh.options.get_option_instrument_data_by_id(option_id)
        except Exception as e:
            logger.warning(f'Cannot fetch instrument {option_id}: {e}')
            continue
        if not instrument:
            continue

        mark_price = 0.0
        try:
            mdata = rh.options.get_option_market_data_by_id(option_id)
            if isinstance(mdata, list):
                mdata = mdata[0] if mdata else {}
            mark_price = float(
                mdata.get('adjusted_mark_price') or mdata.get('mark_price') or 0
            )
        except Exception as e:
            logger.warning(f'Cannot fetch market data for {option_id}: {e}')
            mark_price = float(pos.get('average_open_price', 0))

        result.append({
            'option_id':       option_id,
            'option_url':      option_url,
            'symbol':          instrument.get('chain_symbol', '?'),
            'expiration_date': instrument.get('expiration_date', ''),
            'strike_price':    float(instrument.get('strike_price', 0)),
            'option_type':     instrument.get('type', 'call'),
            'quantity':        qty,
            'mark_price':      mark_price,
            'avg_open_price':  float(pos.get('average_open_price', 0)),
        })

    return result


def get_open_stop_orders() -> list[dict]:
    """Return all open option stop-limit sell orders (trigger=stop, side=sell)."""
    try:
        orders = rh.orders.get_all_open_option_orders() or []
        return [o for o in orders if o.get('trigger') == 'stop']
    except Exception as e:
        logger.error(f'get_open_stop_orders error: {e}')
        return []


def find_stop_for_option(option_url: str, open_stops: list[dict]) -> dict | None:
    """
    Find an existing stop sell-to-close order for the given option.
    Returns {order_id, stop_price, limit_price, quantity} or None.
    """
    for order in open_stops:
        for leg in order.get('legs', []):
            if (leg.get('option') == option_url
                    and leg.get('side') == 'sell'
                    and leg.get('position_effect') == 'close'):
                return {
                    'order_id':    order['id'],
                    'stop_price':  float(order.get('stop_price', 0)),
                    'limit_price': float(order.get('price', 0)),
                    'quantity':    float(order.get('quantity', 0)),
                }
    return None


def place_stop_loss(pos: dict, stop_price: float) -> dict | None:
    """
    Place a GTC stop-limit sell-to-close order.
    Limit price is set 10% below the stop to allow fills during fast moves.
    """
    stop_price  = round(stop_price, 2)
    limit_price = round(max(stop_price * 0.90, 0.01), 2)
    label = _label(pos)

    if DRY_RUN:
        logger.info(
            f'[DRY_RUN] Would place stop=${stop_price:.2f} limit=${limit_price:.2f}'
            f' qty={int(pos["quantity"])} on {label}'
        )
        return {'id': f'dryrun-{pos["option_id"][:8]}', 'dry_run': True}

    try:
        order = rh.orders.order_sell_option_stop_limit(
            positionEffect='close',
            creditOrDebit='credit',
            limitPrice=str(limit_price),
            stopPrice=str(stop_price),
            symbol=pos['symbol'],
            quantity=int(pos['quantity']),
            expirationDate=pos['expiration_date'],
            strike=pos['strike_price'],
            optionType=pos['option_type'],
            timeInForce='gtc',
        )
        if order and order.get('id'):
            logger.info(
                f'Stop placed {order["id"][:8]}: stop=${stop_price:.2f}'
                f' limit=${limit_price:.2f} on {label}'
            )
            return order
        logger.error(f'Stop order returned no id for {label}: {order}')
    except Exception as e:
        logger.error(f'place_stop_loss error for {label}: {e}')
    return None


def cancel_order(order_id: str) -> bool:
    if DRY_RUN:
        logger.info(f'[DRY_RUN] Would cancel {order_id[:8]}')
        return True
    try:
        rh.orders.cancel_option_order(order_id)
        logger.info(f'Cancelled order {order_id[:8]}')
        return True
    except Exception as e:
        logger.error(f'cancel_order({order_id[:8]}) error: {e}')
        return False


def _label(pos: dict) -> str:
    return (f'{pos["symbol"]} ${pos["strike_price"]:.0f}'
            f'{pos["option_type"][0].upper()} {pos["expiration_date"]}')
