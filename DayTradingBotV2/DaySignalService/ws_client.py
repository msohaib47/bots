"""
Minimal Alpaca market-data WebSocket client (JSON protocol).

Hand-rolled instead of using `alpaca_trade_api.stream.Stream`: that SDK (last
released for an older `websockets` API) calls `websockets.connect(...,
extra_headers=...)`, a parameter removed in the `websockets` version actually
installed here (16.0) -- confirmed by a direct connection test, not assumed.
Rather than pin a fragile, unmaintained SDK version against a live library,
this talks to Alpaca's stream directly using the plain-JSON variant of its
protocol (the SDK's msgpack mode needs the `msgpack` package's special
Timestamp type for `t`; omitting the msgpack Content-Type header gets JSON
text frames instead, with `t` as a plain ISO8601 string -- see
https://docs.alpaca.markets/docs/streaming-market-data).
"""
import asyncio
import json
import logging

import websockets

logger = logging.getLogger(__name__)

RECONNECT_BACKOFF = [1, 2, 4, 8, 16, 30]  # seconds, capped


async def stream_bars(api_key: str, api_secret: str, symbols: list, on_bar,
                       feed: str = 'iex', on_error=None):
    """
    Connects to Alpaca's minute-bar stream and calls `on_bar(bar_dict)` for
    every incoming bar. Runs forever with its own reconnect-with-backoff loop
    (auth failures included) -- callers await this once for the life of the
    process; on KeyboardInterrupt/CancelledError it propagates rather than
    retrying.

    `bar_dict` shape (raw from the wire): {'T':'b', 'S': symbol, 'o','h','l',
    'c','v', 't': ISO8601 string, ...}.

    `on_error`, if given, is called with (exception, retry_delay_seconds) on
    every reconnect attempt -- lets a caller raise its own alert without this
    module needing to know about kv_store/notifications itself.
    """
    url = f'wss://stream.data.alpaca.markets/v2/{feed}'
    attempt = 0
    while True:
        try:
            async with websockets.connect(url, ping_interval=10, ping_timeout=180) as ws:
                connected = json.loads(await ws.recv())
                if not (connected and connected[0].get('T') == 'success'
                        and connected[0].get('msg') == 'connected'):
                    raise ConnectionError(f'unexpected connect response: {connected}')

                await ws.send(json.dumps({'action': 'auth', 'key': api_key, 'secret': api_secret}))
                authed = json.loads(await ws.recv())
                if not (authed and authed[0].get('T') == 'success'
                        and authed[0].get('msg') == 'authenticated'):
                    raise ConnectionError(f'auth failed: {authed}')

                await ws.send(json.dumps({'action': 'subscribe', 'bars': symbols}))
                sub_ack = json.loads(await ws.recv())
                logger.info(f'Subscribed: {sub_ack}')

                attempt = 0  # reset backoff only after a fully clean connect+auth+subscribe
                async for raw in ws:
                    for msg in json.loads(raw):
                        if msg.get('T') == 'b':
                            on_bar(msg)
                        elif msg.get('T') == 'error':
                            logger.error(f'Stream error message: {msg}')
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception as e:
            delay = RECONNECT_BACKOFF[min(attempt, len(RECONNECT_BACKOFF) - 1)]
            logger.error(f'WS stream error ({e!r}); reconnecting in {delay}s')
            if on_error:
                on_error(e, delay)
            await asyncio.sleep(delay)
            attempt += 1
