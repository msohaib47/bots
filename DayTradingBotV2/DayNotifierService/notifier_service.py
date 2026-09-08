"""
Notifier service (v2) -- one instance, long-running daemon. The ONLY place in
the whole system that calls common.notifier.notify() -- every other service
(signal, sizing, execution) just records that something notable happened.

Design: ZMQ live-pushed `event.*` messages are used purely as a "something
was published, go check now" wake-up signal -- the actual notification
content and cursor advancement ALWAYS come from kv_store.read_events_since(),
never from the live-pushed payload directly. This is deliberate: an event is
both durably logged (kv_store.append_event) AND pushed live (ZMQ) from the
same call site (see position_manager._emit_event / execution_service._emit_event),
so acting on the live push directly would double-notify once this service
later catches up on the same event via its cursor after a restart. Treating
the push as "wake up and poll" rather than "here is the data" keeps delivery
exactly-once regardless of restarts, at the cost of a tiny bit of extra
latency (one kv_store round-trip) that doesn't matter for a push notification.

A fallback poll runs on FALLBACK_POLL_SECONDS regardless of whether any ZMQ
message arrived, so a missed/dropped wake-up signal doesn't mean a missed
notification -- just a delayed one.

Usage:
  python notifier_service.py
"""
import logging
import os
import sys
import time
import json

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'shared'))
import kv_store
import pubsub
import ports

LOG_DIR = 'logs'
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler(f'{LOG_DIR}/notifier_service.log', encoding='utf-8'), logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from common.notifier import notify

CURSOR_FILE = 'cursor.json'
FALLBACK_POLL_SECONDS = 5
WAKE_POLL_TIMEOUT_MS = 1000


def load_cursor() -> int:
    if os.path.exists(CURSOR_FILE):
        with open(CURSOR_FILE) as f:
            return json.load(f).get('last_event_id', 0)
    return 0


def save_cursor(last_id: int):
    with open(CURSOR_FILE, 'w') as f:
        json.dump({'last_event_id': last_id}, f)


def _format_and_notify(event_type: str, payload: dict):
    if event_type == 'signal_triggered':
        price = f'${payload["price"]:.2f}' if payload.get('price') is not None else ''
        notify(f'SIGNAL_{payload["signal"]}', payload['symbol'], price, payload.get('reason', ''),
               bot='DaySignalService')

    elif event_type == 'trade_open':
        price = f'${payload["price"]:.2f}' if payload.get('price') is not None else ''
        details = f'{payload.get("type", "").upper()} x{payload.get("contracts")} | {payload.get("reason", "")}'
        notify('OPEN', payload.get('underlying', payload.get('symbol')), price, details,
               bot=f'Execution-{payload.get("account", "?")}')

    elif event_type in ('trade_close', 'trade_partial_close'):
        reason = payload.get('reason', '')
        action = 'STOP_LOSS' if reason == 'Stop loss hit' else ('TRAILING' if reason == 'Trailing stop hit' else
                 ('SELL' if event_type == 'trade_partial_close' else 'CLOSE'))
        price = f'${payload["price"]:.2f}' if payload.get('price') is not None else ''
        pnl = payload.get('pnl', 0)
        details = f'{reason} | P&L=${pnl:+.2f} | {payload.get("contracts")}x remaining/closed'
        notify(action, payload.get('underlying', payload.get('symbol')), price, details,
               bot=f'Execution-{payload.get("account", "?")}')

    elif event_type == 'alert':
        notify('ERROR', payload.get('source', 'system'), '', payload.get('message', ''), bot='DayTradingBotV2')

    else:
        logger.warning(f'Unrecognized event type {event_type!r}, sending generic notification')
        notify('INFO', event_type, '', json.dumps(payload), bot='DayTradingBotV2')


def poll_and_notify(cursor: int) -> int:
    events = kv_store.read_events_since(cursor)
    for event_id, event_type, payload, created_at in events:
        logger.info(f'Notifying event #{event_id} {event_type}: {payload}')
        _format_and_notify(event_type, payload)
        cursor = event_id
    if events:
        save_cursor(cursor)
    return cursor


def run():
    cursor = load_cursor()
    logger.info(f'Starting from cursor={cursor}')

    # Catch up on anything published while this service was down, before
    # subscribing to the live stream.
    cursor = poll_and_notify(cursor)

    endpoints = [ports.signal_endpoint()] + [ports.execution_endpoint(a) for a in ports.ACCOUNTS]
    sub = pubsub.Subscriber(endpoints, ['event.'])
    logger.info(f'Subscribed (wake-up only) to: {endpoints}')

    last_fallback_poll = time.time()
    try:
        while True:
            woke = sub.poll(timeout_ms=WAKE_POLL_TIMEOUT_MS)
            now = time.time()
            if woke or (now - last_fallback_poll >= FALLBACK_POLL_SECONDS):
                cursor = poll_and_notify(cursor)
                last_fallback_poll = now
    except KeyboardInterrupt:
        logger.info('Stopped by user.')
    finally:
        sub.close()


if __name__ == '__main__':
    run()
