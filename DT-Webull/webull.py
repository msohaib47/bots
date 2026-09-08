"""
Webull OpenAPI REST client -- hand-rolled (no webull-openapi SDK dependency,
matching this repo's own pattern for Alpaca: DayTradingBot/alpaca.py is a
thin `requests` wrapper, not the official `alpaca-py` SDK either).

FULLY VALIDATED LIVE against the real sandbox API as of 2026-09-07, using the
user's real paper-trading credentials (app_key/app_secret + a discovered real
account_id) -- not just matched against documentation. Confirmed working
end-to-end: account discovery, balance, positions, stock quotes, stock bars,
options chain lookup, and options quotes all returned real data; order
placement/cancel bodies match Webull's own official demo sample code exactly
(not yet live-tested with an actual placed order, but the shape is no longer
a guess). Getting here took three real, non-obvious fixes along the way,
each worth knowing if this breaks again:
  1. The signing algorithm needed one more step than the docs page implied --
     str3 must be FULLY URL-encoded (quote(str3, safe='')) before HMAC-SHA1,
     confirmed by reproducing Webull's own worked example byte-for-byte.
     Without it: a well-formed but silently WRONG signature, 401 with no
     hint of a signing bug specifically.
  2. The correct host for this app is api.webull.com's sandbox counterpart,
     api.sandbox.webull.com -- NOT the demo repo's UAT host
     (us-openapi-alb.uat.webullbroker.com), which 401'd because that demo's
     own sample app_key/app_secret are scoped to a different environment.
     A host from an official demo isn't automatically "more correct" than
     one already proven to authenticate.
  3. Several endpoints have TWO differently-pathed request classes in the
     real SDK source depending on API version (e.g. get_account_balance_request.py
     at the request/ root uses "/account/balance"; the one
     account_v2.get_account_balance() -- what actually gets called -- uses
     is request/v2/get_account_balance_request.py, path
     "/openapi/assets/balance"). Reading only the first file found gives a
     path that looks plausible and 404s.

Confirmed REST paths (from a mix of developer.webull.com, the real SDK source
on GitHub, and the SDK's own official demo/sample code):
  - Auth: HMAC-SHA1 over app_key/app_secret -- see _sign()/_headers().
  - Account list: GET /openapi/account/list (bare JSON list response, no
    'data' wrapper -- inconsistent with most other endpoints here).
  - Account balance: GET /openapi/assets/balance?account_id=...
    ('total_cash_balance' is the real cash field).
  - Positions: GET /openapi/assets/positions?account_id=...
  - Stock snapshot/quote: GET /openapi/market-data/stock/snapshot
  - Stock historical bars: GET /openapi/market-data/stock/bars (fields:
    time/open/high/low/close/volume -- 'time' is ISO-8601 with a +0000
    offset, not 'Z').
  - Options chain: GET /openapi/instrument/option/contracts (fields:
    symbol/strike_price/expiration_date/... -- NOT 'expire_date', a wrong
    guess that silently fell back to a default instead of erroring).
    Can return 300+ contracts for one underlying/side/date-range including
    non-standard corporate-action-adjusted symbols (a stray leading digit,
    e.g. '4SPY260908C00770160') mixed into the normal series -- filter to
    the standard <SYM><6-digit-date><C|P><8-digit-strike> pattern and cap
    the candidate count before requesting quotes (a 300-symbol query URL
    gets a 417 from the real API).
  - Options quote/snapshot: GET /openapi/market-data/option/snapshot
  - Options order place/cancel: POST /openapi/trade/option/order/{place,cancel},
    Webull's leg-based body shape (identifies an option by underlying+strike+
    expiry+type inside a `legs` array, NOT by an OCC symbol string -- a
    materially different shape than Alpaca's convention this was first
    written against). NO 'category' header is sent -- the SDK source implied
    one was needed, but live-testing showed adding it (signed or not) broke
    the signature outright ("Header x-signature is invalid"); omitting it
    reaches real order-placement logic instead. Confirmed 2026-09-07 with a
    live order placement attempt during closed market hours, which correctly
    got a business-logic rejection rather than any signing/auth error.
  - Options order-type constraints (confirmed from docs): BUY/SELL only (no
    SHORT), no MARKET or TRAILING_STOP_LOSS for options, sell-side is DAY
    time-in-force only.

Still not independently confirmed: exact stock-order (non-option) response
field names, and get_order()'s path (reused a v3 stock-order path as an
unverified placeholder -- this bot never calls it in its normal run path).

Multi-account: unlike alpaca.py's module-level singleton client, this module
exposes a WebullClient CLASS -- bot.py instantiates one per configured
account (see config.ACCOUNTS) for trading actions (buy/sell/account/positions).
Market data (bars, quotes) is account-independent, so those stay module-level
functions backed by one shared client (the first configured account's
credentials -- Webull's docs don't describe a data-only credential type, so
this reuses a trading account's app_key/app_secret purely for authentication,
same as DayTradingBot's alpaca.py does with its one shared client).
"""
import base64
import hashlib
import hmac
import json
import logging
import time
import uuid
from datetime import date, datetime, timezone, timedelta
from urllib.parse import urlencode, quote
from zoneinfo import ZoneInfo

import requests

from config import WEBULL_BASE_URL, WEBULL_REGION_ID, ACCOUNTS

ET = ZoneInfo('America/New_York')

logger = logging.getLogger(__name__)

_TIMEOUT = 15


# ── Request signing (HMAC-SHA1, confirmed against Webull's documented worked example) ──

def _sign(app_secret: str, path: str, query: dict, body_json_str: str | None, headers: dict) -> str:
    """
    str1 = sorted, merged query params + signing headers, joined with '&'
    str2 = uppercase MD5 of the JSON body (omitted if no body)
    str3 = path & str1 & str2  (or path & str1 if no body)
    str3 is then FULLY URL-encoded (quote(str3, safe='') -- no characters left
    unescaped, confirmed 2026-09-07 by reproducing Webull's documented worked
    example byte-for-byte -- the docs mention "URL encoding" but not which
    characters stay safe, and getting this wrong (originally used the raw,
    unencoded str3) silently produced a well-formed but wrong signature: no
    exception, just a 401 from the real API with no hint of a signing bug).
    signature = base64(HMAC-SHA1(quote(str3, safe=''), app_secret + '&'))

    Takes the body as an ALREADY-SERIALIZED string, not a dict -- the caller
    (_request) must sign and transmit the exact same bytes. Passing a dict
    here and letting this function serialize it separately from whatever the
    HTTP layer sends was a real bug (found 2026-09-07): `requests`'s own
    `json=body` re-serializes independently (different key order/spacing),
    so the signed bytes silently didn't match the transmitted bytes on any
    call with a body -- a 401 "Header x-signature is invalid" with a
    perfectly correct algorithm otherwise.
    """
    merged = {**query, **headers}
    str1 = '&'.join(f'{k}={merged[k]}' for k in sorted(merged))
    if body_json_str:
        str2 = hashlib.md5(body_json_str.encode()).hexdigest().upper()
        str3 = f'{path}&{str1}&{str2}'
    else:
        str3 = f'{path}&{str1}'
    encoded = quote(str3, safe='')
    key = (app_secret + '&').encode()
    digest = hmac.new(key, encoded.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def _headers(app_key: str, app_secret: str, path: str, query: dict, body_json_str: str | None,
             host: str, extra_headers: dict = None) -> dict:
    """`extra_headers` (e.g. option orders' required 'category' header) must be
    included in the SIGNED header set, not merged in afterward -- confirmed
    live 2026-09-07: an option order 401'd with the exact same auth mechanism
    that worked for every other call, because 'category' was being added to
    the outgoing request only, invalidating the signature for that specific
    request without any signing-specific error to point at it.

    `host` must be the ACTUAL target host for this request, not a fixed
    default -- found 2026-09-08 adding a second (production) account: this
    used to hardcode the global WEBULL_BASE_URL, which happened to match the
    first (sandbox) account's base_url by coincidence, but silently signed
    the WRONG host for any client pointed elsewhere -- a mismatched signed
    Host vs. the real one got a 421 Misdirected Request from production's
    load balancer specifically (sandbox apparently tolerates the mismatch
    sandbox's own base_url always equalled the global default there)."""
    signing_headers = {
        'x-app-key': app_key,
        'x-timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'x-signature-algorithm': 'HMAC-SHA1',
        'x-signature-version': '1.0',
        'x-signature-nonce': uuid.uuid4().hex,
        'host': host,
        **(extra_headers or {}),
    }
    signature = _sign(app_secret, path, query, body_json_str, signing_headers)
    return {
        **signing_headers,
        'x-signature': signature,
        'x-version': 'v2',
        'Content-Type': 'application/json',
    }


class WebullClient:
    """One instance per trading account (see config.ACCOUNTS)."""

    def __init__(self, app_key: str, app_secret: str, account_id: str,
                 base_url: str = WEBULL_BASE_URL):
        self.app_key = app_key
        self.app_secret = app_secret
        self.account_id = account_id
        self.base_url = base_url
        self.last_error_code = None   # mirrors alpaca.py's module-level LAST_ERROR_CODE

    def _request(self, method: str, path: str, query: dict = None, body: dict = None,
                 extra_headers: dict = None) -> dict:
        query = query or {}
        # Serialize the body ONCE and sign+transmit that exact string --
        # letting `requests`'s `json=` re-serialize independently (see
        # _sign()'s docstring) signs bytes that don't match what's actually
        # sent, whenever a request has a body.
        body_json_str = json.dumps(body, separators=(',', ':'), sort_keys=True) if body else None
        host = self.base_url.split('//', 1)[-1]
        headers = _headers(self.app_key, self.app_secret, path, query, body_json_str, host, extra_headers)
        # 'host' is needed for the SIGNATURE, but sending it as an explicit
        # outgoing header too -- redundant with the one HTTP/requests sets
        # automatically from the URL -- got a 421 Misdirected Request from
        # production's load balancer (found 2026-09-08); sandbox tolerated
        # it, production didn't. Drop it from the transmitted headers only.
        headers.pop('host', None)
        url = f'{self.base_url}{path}'
        if query:
            url += '?' + urlencode(query)
        data = body_json_str.encode() if body_json_str else None
        resp = requests.request(method, url, headers=headers, data=data, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    def _get(self, path: str, query: dict = None) -> dict:
        return self._request('GET', path, query=query)

    def _post(self, path: str, body: dict = None, extra_headers: dict = None) -> dict:
        return self._request('POST', path, body=body, extra_headers=extra_headers)

    # ── Account ───────────────────────────────────────────────────────────────
    # Paths below (2026-09-07) read directly from the real webull-openapi-python-sdk
    # source on GitHub (webull/trade/request/*.py), not guessed -- see
    # get_account_list_request.py -> "/openapi/account/list",
    # get_account_balance_request.py -> "/account/balance",
    # get_account_positions_request.py -> "/account/positions".

    def list_accounts(self) -> list:
        """Discovers the account_id(s) available under this app_key/app_secret --
        call once (e.g. via test_connection.py) to find the real account_id
        for .env. Confirmed path (unlike the earlier guess this replaced).
        Response is a bare JSON list, not {'data': [...]} -- confirmed live
        2026-09-07 (an earlier version of this assumed a 'data' wrapper key,
        matching most other Webull endpoints, and got it wrong for this one)."""
        result = self._get('/openapi/account/list')
        return result if isinstance(result, list) else result.get('data', [])

    def get_account(self) -> dict:
        """Path fixed 2026-09-07: there are TWO request classes for this --
        webull/trade/request/get_account_balance_request.py (root, path
        "/account/balance") and webull/trade/request/v2/get_account_balance_request.py
        (the one account_v2.get_account_balance() -- what the demo actually
        calls -- uses), whose real path is "/openapi/assets/balance". Missed
        the v2-specific file on the first pass; the root one 404'd live."""
        return self._get('/openapi/assets/balance', query={'account_id': self.account_id})

    def get_cash(self) -> float:
        """Field name confirmed live 2026-09-07: get_account()'s real response
        has 'total_cash_balance' at the top level (a per-currency breakdown
        also exists under 'account_currency_assets', unused here since this
        account only holds USD)."""
        acct = self.get_account()
        return float(acct.get('total_cash_balance', 0) or 0)

    def list_positions(self) -> list:
        result = self._get('/openapi/assets/positions', query={'account_id': self.account_id})
        return result if isinstance(result, list) else result.get('data', [])

    def get_position(self, symbol: str):
        return next((p for p in self.list_positions() if p.get('symbol') == symbol), None)

    # ── Options ───────────────────────────────────────────────────────────────

    def get_0dte_chain(self, symbol: str, opt_type: str, near_price: float, max_dte: int = 5) -> list:
        """Path/params confirmed 2026-09-07 from webull/data/request/
        get_option_contracts_request.py: GET /openapi/instrument/option/contracts,
        underlying_symbols/option_type/strike_price_gte/strike_price_lte/
        start_date/end_date -- structurally close to what this was first
        written against, just needed the real path and 'underlying_symbols'
        (plural) instead of a guessed 'symbol'. Response shape (a bare list
        vs. {'data': [...]}) not yet confirmed live -- handled defensively."""
        today = date.today()
        exp_max = str(today + timedelta(days=max_dte))
        try:
            data = self._get('/openapi/instrument/option/contracts', query={
                'category': 'US_OPTION', 'underlying_symbols': symbol,
                'option_type': opt_type.upper(),
                'start_date': str(today), 'end_date': exp_max,
                'strike_price_gte': round(near_price * 0.95, 2),
                'strike_price_lte': round(near_price * 1.05, 2),
            })
            contracts = data if isinstance(data, list) else data.get('data', [])
            # Field confirmed live 2026-09-07: 'expiration_date', not the
            # guessed 'expire_date' -- the wrong guess silently fell back to
            # find_atm_contract's str(date.today()) default instead of
            # raising, so a wrong contract expiry could have gone unnoticed
            # without checking the actual value returned.
            return sorted(contracts, key=lambda c: (
                c.get('expiration_date', ''),
                abs(float(c.get('strike_price', 0)) - near_price),
            ))
        except Exception as e:
            logger.error(f'Options chain error {symbol} {opt_type}: {e}')
            return []

    def get_snapshots_by_symbols(self, option_symbols: list) -> dict:
        """Path confirmed 2026-09-07 from get_option_snapshot_request.py:
        GET /openapi/market-data/option/snapshot (symbols, category). Response
        field names (bid/ask keys) not yet confirmed live -- kept the same
        defensive .get(..., 0) shape as before so a wrong guess degrades to a
        'no quote' skip rather than crashing."""
        if not option_symbols:
            return {}
        try:
            data = self._get('/openapi/market-data/option/snapshot', query={
                'symbols': ','.join(option_symbols), 'category': 'US_OPTION',
            })
            rows = data if isinstance(data, list) else data.get('data', [])
            out = {}
            for row in rows:
                out[row['symbol']] = {'latestQuote': {'bp': row.get('bid', 0), 'ap': row.get('ask', 0)}}
            return out
        except Exception as e:
            logger.error(f'Snapshot error: {e}')
            return {}

    def find_atm_contract(self, symbol: str, opt_type: str, spot_price: float, max_dte: int = 5) -> dict | None:
        """Same selection logic as alpaca.py's find_atm_contract -- nearest
        expiry, then closest strike, filtered for a tradeable ask/spread."""
        contracts = self.get_0dte_chain(symbol, opt_type, spot_price, max_dte=max_dte)
        if not contracts:
            logger.warning(f'No <={max_dte}DTE {opt_type} contracts found for {symbol}')
            return None

        # get_0dte_chain already sorts nearest-expiry-then-nearest-strike first --
        # cap the batch before requesting quotes. Found live 2026-09-07: the
        # unfiltered chain for one underlying/side/max_dte can be 300+ contracts
        # (every $1 strike across every expiry in range), and batching quotes for
        # all of them in one URL got a 417 (request too large) from the real API.
        # Also found live: the chain mixes in non-standard symbols with a
        # leading digit (e.g. '4SPY260908C00770160') -- almost certainly
        # corporate-action-adjusted contracts (a different strike/multiplier
        # than the round-number standard series) -- skip anything that isn't
        # plain <UNDERLYING><6-digit-date><C|P><8-digit-strike>.
        import re
        std_re = re.compile(rf'^{re.escape(symbol)}\d{{6}}[CP]\d{{8}}$')
        contracts = [c for c in contracts if std_re.match(c.get('symbol', ''))][:15]
        syms = [c.get('symbol', '') for c in contracts if c.get('symbol')]
        snapshots = self.get_snapshots_by_symbols(syms)

        for contract in contracts:
            sym = contract.get('symbol', '')
            strike = float(contract.get('strike_price', 0))
            quote = snapshots.get(sym, {}).get('latestQuote', {})
            bid = float(quote.get('bp', 0) or 0)
            ask = float(quote.get('ap', 0) or 0)

            if ask <= 0 or ask > 20 or ask < 0.05:
                continue
            mid = (bid + ask) / 2 if bid > 0 else ask
            if bid > 0 and ask > bid * 2.5:
                continue

            return {
                'symbol': sym, 'strike': strike, 'bid': bid, 'ask': ask, 'mid': mid,
                'type': opt_type, 'underlying': symbol,
                'expiry': contract.get('expiration_date', str(date.today())),
            }

        logger.warning(f'No liquid {opt_type} contracts near {spot_price} for {symbol}')
        return None

    # ── Orders ────────────────────────────────────────────────────────────────
    # Confirmed constraints (options, from docs): BUY/SELL only (no SHORT), no
    # MARKET or TRAILING_STOP_LOSS order type, sell-side is DAY time-in-force
    # only. LIMIT is used throughout, matching what's actually supported.

    # Confirmed 2026-09-07 from samples/order/order_option_client.py in the real
    # SDK repo: Webull identifies an option by its PARTS (underlying symbol +
    # strike + expiry + type) inside a `legs` array, not by an OCC-style symbol
    # string -- a materially different shape than what this was first written
    # against (a flat body keyed on `contract['symbol']`, matching Alpaca's
    # convention instead). `contract` here must carry 'underlying', 'strike',
    # 'expiry', 'type' (all already present -- see find_atm_contract's return).

    def _option_order_body(self, contract: dict, qty: int, side: str, limit_price: float) -> dict:
        return {
            'client_order_id': str(uuid.uuid4()),
            'combo_type': 'NORMAL',
            'order_type': 'LIMIT',
            'quantity': str(qty),
            'limit_price': str(round(limit_price, 2)),
            'option_strategy': 'SINGLE',
            'side': side,
            'time_in_force': 'DAY',   # confirmed: options sell-side is DAY-only; used for BUY too for consistency
            'entrust_type': 'QTY',
            'legs': [{
                'side': side,
                'quantity': str(qty),
                'symbol': contract['underlying'],
                'strike_price': str(contract['strike']),
                'option_expire_date': contract['expiry'],
                'instrument_type': 'OPTION',
                'option_type': contract['type'].upper(),
                'market': 'US',
            }],
        }

    def _place_option_order(self, contract: dict, qty: int, side: str, limit_price: float) -> dict | None:
        body = self._option_order_body(contract, qty, side, limit_price)
        # NOT sending a 'category' header (removed 2026-09-07): the SDK source's
        # add_custom_headers_from_order() implied option orders need a
        # 'category' header, but adding ANY value for it here -- signed or
        # not -- got "Header x-signature is invalid" from the real API. Live-
        # tested: WITHOUT this header, signing succeeds and the request reaches
        # real order-placement logic (confirmed via a real "market closed,
        # trading hours are 9:30am-4:15pm ET" response). Whatever this header
        # is for, our reproduction of it was wrong in a way that broke the
        # signature outright -- correct behavior was to omit it, not to guess
        # harder at its format.
        try:
            order = self._post('/openapi/trade/option/order/place',
                                body={'account_id': self.account_id, 'new_orders': [body]})
            self.last_error_code = None
            return order
        except requests.HTTPError as e:
            try:
                self.last_error_code = e.response.json().get('code')
            except Exception:
                self.last_error_code = None
            logger.error(f'{side} order failed {contract.get("symbol")}: {e}')
            return None
        except Exception as e:
            self.last_error_code = None
            logger.error(f'{side} order failed {contract.get("symbol")}: {e}')
            return None

    def buy_option(self, contract: dict, qty: int) -> dict | None:
        limit_price = round(contract['mid'] + 0.01, 2)
        order = self._place_option_order(contract, qty, 'BUY', limit_price)
        if order:
            logger.info(f'BUY order: {qty}x {contract["symbol"]} @ ${limit_price} -> {order.get("client_order_id")}')
        return order

    def close_option_position(self, contract: dict, qty: int, limit_price: float = None) -> dict | None:
        """`contract` needs the same shape as buy_option's (underlying/strike/
        expiry/type) -- unlike alpaca.py's close_option_position (bare symbol
        string suffices there since Alpaca closes by OCC symbol directly).
        Options sell-side has no MARKET order type (confirmed), so this always
        needs a real limit price; caller passes the current mid when
        available. Returns None (skips the close) rather than guessing a
        fallback price -- see bot.py's caller for how it handles that."""
        if limit_price is None:
            logger.warning(f'{contract.get("symbol")}: closing without a fresh quote -- skipping this tick')
            return None
        return self._place_option_order(contract, qty, 'SELL', limit_price)

    def get_order(self, client_order_id: str) -> dict | None:
        """FIXME (unverified path): confirmed cancel/place paths are option-specific
        (/openapi/trade/option/order/*); an equivalent option order-detail path
        wasn't found in the SDK source explored so far -- this reuses the v3
        STOCK order-detail path as a placeholder, unconfirmed for options."""
        try:
            return self._get('/openapi/trade/order/detail', query={'account_id': self.account_id, 'client_order_id': client_order_id})
        except Exception:
            return None

    def cancel_order(self, client_order_id: str):
        try:
            self._post('/openapi/trade/option/order/cancel',
                       body={'account_id': self.account_id, 'client_order_id': client_order_id})
        except Exception:
            pass


# ── Market data (account-independent -- one shared client, see module docstring) ──

_market_client: WebullClient | None = None


def _get_market_client() -> WebullClient:
    global _market_client
    if _market_client is None:
        if not ACCOUNTS:
            raise RuntimeError('No WEBULL_ACCOUNTS configured -- cannot fetch market data')
        first = next(iter(ACCOUNTS.values()))
        _market_client = WebullClient(first['app_key'], first['app_secret'], first['account_id'])
    return _market_client


def get_latest_price(symbol: str) -> float | None:
    """Path confirmed 2026-09-07 from get_snapshot_request.py:
    GET /openapi/market-data/stock/snapshot (symbols, category)."""
    try:
        data = _get_market_client()._get('/openapi/market-data/stock/snapshot',
                                          query={'symbols': symbol, 'category': 'US_STOCK'})
        rows = data if isinstance(data, list) else data.get('data', [])
        if not rows:
            return None
        row = rows[0]
        ask, bid = float(row.get('ask', 0) or 0), float(row.get('bid', 0) or 0)
        if ask and bid:
            return (ask + bid) / 2
        return ask or bid or None
    except Exception as e:
        logger.error(f'Price error {symbol}: {e}')
        return None


def _fetch_bars_raw(symbol: str, timeframe: str, count: int) -> list:
    """Path/params confirmed 2026-09-07 from get_historical_bars_request.py:
    GET /openapi/market-data/stock/bars (symbol, category, timespan, count).
    `timeframe` here uses Alpaca-style strings ('5Min'/'1Min') for drop-in
    compatibility with signals.py; translated to Webull's M1/M5 style below
    (matching Timespan.M1.name/.M5.name from the SDK's enum, per the sample
    code). Webull's `count` param has no date-range filter of its own -- it's
    purely "last N bars regardless of day" (confirmed live 2026-09-08: a
    count=200 request for 5-min bars spanned 2026-09-03 through 2026-09-08,
    skipping the weekend -- there is no server-side session boundary to rely
    on). Session-only vs. multi-session scoping is therefore done client-side
    by the two wrapper functions below, not here.

    Two bugs found + fixed 2026-09-08 (this function -- then still named
    get_intraday_bars -- had apparently never been exercised through a real
    live signal computation before; every tick crashed):
    1. o/h/l/c/v come back from Webull's API as JSON STRINGS (e.g. '316.0900'),
       not numbers -- confirmed live. signals.py's `_vwap()` does arithmetic
       directly on these (`(b['h'] + b['l'] + b['c']) / 3`), which raised
       `TypeError: unsupported operand type(s) for /: 'str' and 'int'` on
       literally every tick since deployment (354 tracebacks in one session's
       log). Now cast to float (int for volume) here, once, at the source.
    2. Webull returns bars NEWEST-FIRST (descending) -- also confirmed live.
       signals.py (like DayTradingBot's Alpaca-based version) assumes
       ascending/chronological order and reads `closes[-1]` as "current
       price." Without reversing, that would silently read the OLDEST bar in
       the batch as current -- the same class of bug as DayTradingBot's
       get_recent_bars() ascending/descending mixup (see
       .memory/project_known_issues.md), just the opposite direction. Now
       reversed here to ascending before returning.
    """
    tf_map = {'1Min': 'M1', '5Min': 'M5', '15Min': 'M15'}
    try:
        data = _get_market_client()._get('/openapi/market-data/stock/bars', query={
            'symbol': symbol, 'category': 'US_STOCK',
            'timespan': tf_map.get(timeframe, 'M5'), 'count': count,
        })
        rows = data if isinstance(data, list) else data.get('data', [])
        # Field names confirmed live 2026-09-07: {'time','open','high','low',
        # 'close','volume', ...} -- 'time' is ISO-8601 with milliseconds+offset
        # ('2026-09-04T19:55:00.000+0000'), already compatible with
        # datetime.fromisoformat() elsewhere in this repo after stripping 'Z'
        # handling isn't even needed here since it uses '+0000' not 'Z'.
        bars = [
            {'t': b.get('time'), 'o': float(b.get('open')), 'h': float(b.get('high')),
             'l': float(b.get('low')), 'c': float(b.get('close')), 'v': float(b.get('volume') or 0)}
            for b in rows
        ]
        return list(reversed(bars))
    except Exception as e:
        logger.error(f'Bars error {symbol}: {e}')
        return []


def get_intraday_bars(symbol: str, timeframe: str = '5Min', limit: int = 100) -> list:
    """Today-session-only bars (ET calendar date), for VWAP -- VWAP resets each
    session, so pulling in a prior day's bars (which _fetch_bars_raw's raw
    count-based fetch does whenever today doesn't yet have `limit` bars of its
    own, e.g. early in the morning) would silently corrupt it. Over-fetches
    (5x the requested limit, capped at 300 -- generous enough to guarantee
    today's bars are included even a few minutes after open) then filters to
    today's ET date client-side, since Webull's API has no server-side session
    boundary (see _fetch_bars_raw's docstring). Matches alpaca.py's
    get_intraday_bars' contract (today-only) despite the different mechanism.
    """
    raw = _fetch_bars_raw(symbol, timeframe, min(limit * 5, 300))
    today = datetime.now(ET).date()
    todays = [b for b in raw if datetime.fromisoformat(b['t'].replace('Z', '+00:00')).astimezone(ET).date() == today]
    return todays[-limit:]


def get_1min_bars(symbol: str, limit: int = 60) -> list:
    return get_intraday_bars(symbol, '1Min', limit)


def get_5min_bars(symbol: str, limit: int = 50) -> list:
    return get_intraday_bars(symbol, '5Min', limit)


def get_recent_bars(symbol: str, timeframe: str = '15Min', limit: int = 30) -> list:
    """Most recent N bars regardless of day -- same contract as alpaca.py's
    version (used by signals.py for multi-session EMA/RSI/ADX/ATR warmup and
    the 15-min HTF trend filter). Unlike get_intraday_bars above, this one is
    supposed to span multiple sessions, so it uses the raw (unfiltered) fetch
    directly."""
    return _fetch_bars_raw(symbol, timeframe, limit)
