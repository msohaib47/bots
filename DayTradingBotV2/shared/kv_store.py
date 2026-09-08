"""
Shared state for the DayTradingBotV2 streaming services (Signal / Sizing /
Execution / Notifier): a TTL'd latest-value cache plus a durable append-only
event log, both backed by a single local SQLite file (stdlib only -- no new
service to install/run/monitor).

Scoped to DayTradingBotV2 only -- not part of the cross-bot `common/` package,
since this is specific to this redesign, not something WheelBot/CryptoBot/etc.
should ever import.

Role in the v2 design: NOT the primary transport (that's ZeroMQ PUB/SUB) --
this is the cold-start snapshot a freshly-(re)started service reads once
before it starts consuming the live stream, and the durable event history the
Notifier uses to catch up on anything published while it was down.

  - set()/get(): latest-value cache with expiry. Only the most recent value
    for a key ever matters; an overwritten value being lost is fine.
  - append_event()/read_events_since(): durable append-only log with a
    monotonic id, for events that must each be seen exactly once (a signal
    triggering, a trade opening/closing) -- callers track their own cursor
    (the last id they've processed) and pass it back in on the next read.
"""
import sqlite3
import json
import time
import os

DB_PATH = os.environ.get(
    'IPC_DB_PATH',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ipc_store.db'),
)


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.execute('PRAGMA journal_mode=WAL')  # readers don't block the writer, or each other
    conn.execute('''CREATE TABLE IF NOT EXISTS kv (
                       key TEXT PRIMARY KEY,
                       value TEXT NOT NULL,
                       expires_at REAL NOT NULL)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS events (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       type TEXT NOT NULL,
                       payload TEXT NOT NULL,
                       created_at REAL NOT NULL)''')
    return conn


# ── Latest-value cache ──────────────────────────────────────────────────────

def set(key: str, value: dict, ttl_seconds: float):
    """Store `value` under `key`, expiring after `ttl_seconds`. Any sqlite3
    failure is swallowed -- callers treat a failed set() the same as any other
    unavailability of the store (see the v2 plan's Failure handling section)."""
    try:
        conn = _conn()
        conn.execute('INSERT OR REPLACE INTO kv (key, value, expires_at) VALUES (?, ?, ?)',
                     (key, json.dumps(value), time.time() + ttl_seconds))
        conn.commit()
        conn.close()
    except sqlite3.Error:
        pass


def get(key: str) -> dict | None:
    """Returns the value for `key`, or None if missing, expired, or the store
    is unreadable -- callers can't distinguish these cases, by design (all
    three mean "treat this as stale/unavailable")."""
    try:
        conn = _conn()
        row = conn.execute('SELECT value, expires_at FROM kv WHERE key = ?', (key,)).fetchone()
        conn.close()
    except sqlite3.Error:
        return None
    if not row or row[1] < time.time():
        return None
    return json.loads(row[0])


# ── Durable event log ───────────────────────────────────────────────────────

def append_event(event_type: str, payload: dict):
    """Appends one event. Failures are swallowed (see set() above) -- a failed
    append means that occurrence simply never gets a notification, same as
    any other write to a broken store."""
    try:
        conn = _conn()
        conn.execute('INSERT INTO events (type, payload, created_at) VALUES (?, ?, ?)',
                     (event_type, json.dumps(payload), time.time()))
        conn.commit()
        conn.close()
    except sqlite3.Error:
        pass


def read_events_since(last_id: int) -> list[tuple]:
    """Returns [(id, type, payload_dict, created_at), ...] for every event
    after `last_id`, oldest first. Returns [] (not an error) if the store is
    unreadable -- callers retry on their next poll."""
    try:
        conn = _conn()
        rows = conn.execute(
            'SELECT id, type, payload, created_at FROM events WHERE id > ? ORDER BY id',
            (last_id,),
        ).fetchall()
        conn.close()
    except sqlite3.Error:
        return []
    return [(r[0], r[1], json.loads(r[2]), r[3]) for r in rows]
