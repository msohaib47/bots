"""
db.py — SQLite dedup store for EbayNewListingsBot.

Tracks which active-listing item IDs have already triggered a notification,
so re-running the search doesn't re-notify on the same listing.
"""

import sqlite3
from pathlib import Path

from config import DB_PATH


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _migrate(conn: sqlite3.Connection):
    """Add columns to a table created before this field existed."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(seen_listings)")}
    if "quantity" not in cols:
        conn.execute("ALTER TABLE seen_listings ADD COLUMN quantity INTEGER")


def init_db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS seen_listings (
                item_id     TEXT PRIMARY KEY,
                search_name TEXT NOT NULL,
                title       TEXT,
                price       REAL,
                quantity    INTEGER,
                url         TEXT,
                first_seen  TEXT DEFAULT (datetime('now'))
            )
        """)
        _migrate(conn)


def is_new(conn: sqlite3.Connection, item_id: str) -> bool:
    row = conn.execute("SELECT 1 FROM seen_listings WHERE item_id = ?", (item_id,)).fetchone()
    return row is None


def mark_seen(conn: sqlite3.Connection, item_id: str, search_name: str,
              title: str, price, url: str, quantity: int = None):
    conn.execute(
        "INSERT OR IGNORE INTO seen_listings (item_id, search_name, title, price, quantity, url) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (item_id, search_name, title, price, quantity, url),
    )
