"""
db.py — SQLite schema and helpers for eBay sold price agent
"""

import os
import sqlite3
from pathlib import Path
from datetime import datetime

# Default to local storage so SQLite locking works (CIFS mounts don't support it)
DB_PATH = Path(os.environ.get("EBAY_DB_PATH", "/home/claude/data/ebay_sold.db"))


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables if they don't exist."""
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS search_configs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL UNIQUE,
                query       TEXT NOT NULL,
                category_id TEXT,
                min_price   REAL,
                max_price   REAL,
                active      INTEGER DEFAULT 1,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS sold_listings (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                search_config TEXT NOT NULL,
                title         TEXT NOT NULL,
                sold_price    REAL NOT NULL,
                shipping_cost REAL,
                total_price   REAL,
                sold_date     TEXT,
                condition     TEXT,
                listing_url   TEXT,
                image_url     TEXT,
                scraped_at    TEXT DEFAULT (datetime('now')),
                UNIQUE(listing_url)
            );

            CREATE TABLE IF NOT EXISTS scrape_runs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                search_config   TEXT NOT NULL,
                started_at      TEXT NOT NULL,
                finished_at     TEXT,
                items_found     INTEGER DEFAULT 0,
                items_new       INTEGER DEFAULT 0,
                status          TEXT DEFAULT 'running',
                error_msg       TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_listings_config
                ON sold_listings(search_config);
            CREATE INDEX IF NOT EXISTS idx_listings_date
                ON sold_listings(sold_date);
        """)
    print(f"[db] Initialized database at {DB_PATH}")


def upsert_listing(conn: sqlite3.Connection, listing: dict) -> bool:
    """Insert a listing. Returns True if it was new, False if duplicate."""
    try:
        conn.execute("""
            INSERT INTO sold_listings
                (search_config, title, sold_price, shipping_cost, total_price,
                 sold_date, condition, listing_url, image_url)
            VALUES
                (:search_config, :title, :sold_price, :shipping_cost, :total_price,
                 :sold_date, :condition, :listing_url, :image_url)
        """, listing)
        return True
    except sqlite3.IntegrityError:
        return False


def start_run(conn: sqlite3.Connection, config_name: str) -> int:
    cur = conn.execute(
        "INSERT INTO scrape_runs (search_config, started_at) VALUES (?, ?)",
        (config_name, datetime.now().isoformat())
    )
    conn.commit()
    return cur.lastrowid


def finish_run(conn: sqlite3.Connection, run_id: int, found: int, new: int,
               status: str = "ok", error: str = None):
    conn.execute("""
        UPDATE scrape_runs
        SET finished_at = ?, items_found = ?, items_new = ?, status = ?, error_msg = ?
        WHERE id = ?
    """, (datetime.now().isoformat(), found, new, status, error, run_id))
    conn.commit()


def get_listings(config_name: str = None, days: int = None) -> list[dict]:
    """Fetch listings, optionally filtered by config name and recency."""
    query = "SELECT * FROM sold_listings WHERE 1=1"
    params = []
    if config_name:
        query += " AND search_config = ?"
        params.append(config_name)
    if days:
        query += " AND scraped_at >= datetime('now', ?)"
        params.append(f"-{days} days")
    query += " ORDER BY sold_date DESC"
    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]
