"""
EbayNewListingsBot — polls eBay's Browse API for newly listed (active, not
sold) items matching search terms defined in configs.json, and sends an
ntfy notification for each one not seen before.

Usage:
  python bot.py              -- single run (called by cron)
  python bot.py --status     -- print recent notified listings
"""

import os
import sys
import json
import logging
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import CONFIG_FILE, CHECK_LIMIT, LOG_FILE
from db import init_db, get_conn, is_new, mark_seen
from browse_api import search_new_listings, get_available_quantity

os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


def load_configs() -> list:
    if not CONFIG_FILE.exists():
        logger.error(f"Config file not found: {CONFIG_FILE} — see configs.json")
        return []
    with open(CONFIG_FILE) as f:
        return json.load(f)


def run():
    from common.notifier import notify

    configs = load_configs()
    if not configs:
        return

    init_db()
    total_new = 0

    with get_conn() as conn:
        for cfg in configs:
            name = cfg["name"]
            query = cfg["query"]
            category_id = cfg.get("category_id")
            min_price = cfg.get("min_price")
            max_price = cfg.get("max_price")

            try:
                items = search_new_listings(
                    query, category_id=category_id,
                    min_price=min_price, max_price=max_price,
                    limit=CHECK_LIMIT,
                )
            except Exception as e:
                logger.error(f"[{name}] Search failed: {e}")
                continue

            new_count = 0
            for item in items:
                item_id = item.get("itemId")
                if not item_id or not is_new(conn, item_id):
                    continue

                title = item.get("title", "")
                price_info = item.get("price") or {}
                price = price_info.get("value")
                currency = price_info.get("currency", "USD")
                url = item.get("itemWebUrl", "")

                try:
                    quantity = get_available_quantity(item_id)
                except Exception as e:
                    logger.warning(f"[{name}] Quantity lookup failed for {item_id}: {e}")
                    quantity = None

                mark_seen(conn, item_id, name, title, price, url, quantity=quantity)
                new_count += 1

                price_str = f"${price} {currency}" if price else ""
                qty_str = f" | qty: {quantity}" if quantity is not None else ""
                notify(
                    "NEW_LISTING",
                    name,
                    price_str,
                    details=f"{title}{qty_str}\n{url}",
                    bot="EbayNewListingsBot",
                )

            conn.commit()
            logger.info(f"[{name}] {len(items)} checked, {new_count} new")
            total_new += new_count

    logger.info(f"Run complete — {total_new} new listing(s) notified")


def backfill_quantity():
    """Fetch quantity for existing rows that predate the quantity field,
    without touching dedup state (so no re-notifications fire)."""
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT item_id FROM seen_listings WHERE quantity IS NULL"
        ).fetchall()

        if not rows:
            print("Nothing to backfill — all rows already have a quantity.")
            return

        updated = 0
        for row in rows:
            item_id = row["item_id"]
            try:
                quantity = get_available_quantity(item_id)
            except Exception as e:
                logger.warning(f"Quantity lookup failed for {item_id}: {e}")
                continue
            if quantity is not None:
                conn.execute(
                    "UPDATE seen_listings SET quantity = ? WHERE item_id = ?",
                    (quantity, item_id),
                )
                updated += 1
        conn.commit()
    print(f"Backfilled quantity for {updated}/{len(rows)} row(s).")


def print_status():
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT search_name, title, price, quantity, first_seen FROM seen_listings "
            "ORDER BY first_seen DESC LIMIT 20"
        ).fetchall()
    if not rows:
        print("No listings notified yet.")
        return
    print(f"\n{'Seen':<20}  {'Config':<15}  {'Price':>8}  {'Qty':>4}  Title")
    print("─" * 95)
    for r in rows:
        price = f"${r['price']:.0f}" if r["price"] is not None else ""
        qty = str(r["quantity"]) if r["quantity"] is not None else ""
        print(f"{r['first_seen']:<20}  {r['search_name']:<15}  {price:>8}  {qty:>4}  {r['title'][:40]}")


def main():
    parser = argparse.ArgumentParser(description="eBay New Listings Watcher")
    parser.add_argument("--status", action="store_true", help="Print recently notified listings")
    parser.add_argument("--backfill-quantity", action="store_true",
                         help="Fetch quantity for existing rows missing it, without re-notifying")
    args = parser.parse_args()

    if args.status:
        print_status()
    elif args.backfill_quantity:
        backfill_quantity()
    else:
        run()


if __name__ == "__main__":
    main()
