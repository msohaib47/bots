"""
agent.py — Main orchestrator for the eBay sold price agent

Usage:
    python agent.py run              # scrape all active configs
    python agent.py run --config "F-150 4x4"   # scrape one config
    python agent.py add              # interactive: add a new search config
    python agent.py list             # list all configs
    python agent.py stats            # print summary stats for all configs
"""

import sys
import logging
import argparse
from datetime import datetime

from db import init_db, get_conn, upsert_listing, start_run, finish_run
from scraper import scrape_config
from analyze import print_stats
from emailer import send_reports

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler("agent.log"),
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger(__name__)


# ── Default search configurations ───────────────────────────────────────────
# Edit this list to set up your targets, or use `python agent.py add`
DEFAULT_CONFIGS = [
    {
        "name": "R640",
        "query": "Dell R640",
        "category_id": "58058",   # computer/tables&networking
        "min_price": 50,
        "max_price": 5000,
    },
    {
        "name": "R740",
        "query": "Dell R740",
        "category_id": "58058",   # computer/tables&networking
        "min_price": 50,
        "max_price": 5000,
    },
    {
        "name": "16GB DDR4",
        "query": "16GB DDR4 ECC 2133 -8GB -4GB",
        "category_id": "0",   # computer/tables&networking
        "min_price": 5,
        "max_price": 500,
    },    
    {
        "name": "32GB DDR4",
        "query": "32GB DDR4 ECC 2133 -16GB -8GB -4GB",
        "category_id": "0",   # computer/tables&networking
        "min_price": 5,
        "max_price": 500,
    },    
    # Add more configs here, or via `python agent.py add`
]

# ── eBay category IDs for reference ─────────────────────────────────────────
COMMON_CATEGORIES = {
    "0":    "All Categories",
    "6001": "eBay Motors > Cars & Trucks",
    "6028": "eBay Motors > Motorcycles",
    "293":  "Consumer Electronics",
    "58058": "Computers/Tablets & Networking",
    "11450": "Clothing, Shoes & Accessories",
    "220":  "Toys & Hobbies",
    "1249": "Video Games & Consoles",
    "625":  "Cameras & Photo",
}


def seed_default_configs():
    """Insert default configs if the table is empty."""
    with get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM search_configs").fetchone()[0]
        if count == 0:
            for cfg in DEFAULT_CONFIGS:
                conn.execute("""
                    INSERT OR IGNORE INTO search_configs
                        (name, query, category_id, min_price, max_price)
                    VALUES (:name, :query, :category_id, :min_price, :max_price)
                """, cfg)
            conn.commit()
            logger.info(f"Seeded {len(DEFAULT_CONFIGS)} default search configs.")


def load_configs(name: str = None) -> list[dict]:
    with get_conn() as conn:
        if name:
            rows = conn.execute(
                "SELECT * FROM search_configs WHERE name = ? AND active = 1", (name,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM search_configs WHERE active = 1"
            ).fetchall()
    return [dict(r) for r in rows]


def run_all(config_name: str = None):
    configs = load_configs(config_name)
    if not configs:
        logger.error("No active search configs found. Use `python agent.py add` to add one.")
        return

    logger.info(f"Starting agent run — {len(configs)} config(s)")
    total_new = 0
    config_names = []

    with get_conn() as conn:
        for cfg in configs:
            run_id = start_run(conn, cfg["name"])
            found = 0
            new = 0
            try:
                for listing in scrape_config(cfg):
                    found += 1
                    is_new = upsert_listing(conn, listing)
                    if is_new:
                        new += 1
                conn.commit()
                finish_run(conn, run_id, found, new)
                logger.info(f"[{cfg['name']}] Done — {found} found, {new} new")
                config_names.append(cfg["name"])
            except Exception as e:
                conn.rollback()
                finish_run(conn, run_id, found, new, status="error", error=str(e))
                logger.error(f"[{cfg['name']}] Run failed: {e}")
            total_new += new

    logger.info(f"Agent run complete — {total_new} new listings total")

    # Send email reports for each config
    if config_names:
        logger.info(f"Sending email reports for {len(config_names)} config(s)")
        send_reports(config_names)


def cmd_add():
    """Interactively add a new search config."""
    print("\n── Add new search config ──")
    name = input("Name (e.g. 'Silverado 1500 4x4'): ").strip()
    query = input("eBay search query: ").strip()

    print("\nCommon category IDs:")
    for cid, label in COMMON_CATEGORIES.items():
        print(f"  {cid:>6}  {label}")
    category = input("Category ID (leave blank for All): ").strip() or "0"

    min_p = input("Min price (leave blank to skip): ").strip()
    max_p = input("Max price (leave blank to skip): ").strip()

    with get_conn() as conn:
        conn.execute("""
            INSERT INTO search_configs (name, query, category_id, min_price, max_price)
            VALUES (?, ?, ?, ?, ?)
        """, (
            name, query, category,
            float(min_p) if min_p else None,
            float(max_p) if max_p else None,
        ))
        conn.commit()
    print(f"\n✓ Added '{name}'. Run `python agent.py run --config \"{name}\"` to scrape it.")


def cmd_list():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM search_configs ORDER BY name").fetchall()
    print(f"\n{'ID':>4}  {'Name':<28}  {'Query':<30}  {'Cat':>6}  {'Min':>8}  {'Max':>8}  Active")
    print("─" * 95)
    for r in rows:
        print(f"{r['id']:>4}  {r['name']:<28}  {r['query']:<30}  "
              f"{r['category_id'] or '':>6}  "
              f"{('$'+str(int(r['min_price']))) if r['min_price'] else '':>8}  "
              f"{('$'+str(int(r['max_price']))) if r['max_price'] else '':>8}  "
              f"{'yes' if r['active'] else 'no'}")


def main():
    parser = argparse.ArgumentParser(description="eBay Sold Price Agent")
    sub = parser.add_subparsers(dest="cmd")

    p_run = sub.add_parser("run", help="Run scraper for all (or one) config")
    p_run.add_argument("--config", help="Name of a specific config to run")

    sub.add_parser("add", help="Add a new search config interactively")
    sub.add_parser("list", help="List all search configs")
    sub.add_parser("stats", help="Print summary stats")

    args = parser.parse_args()

    init_db()
    seed_default_configs()

    if args.cmd == "run":
        run_all(args.config)
    elif args.cmd == "add":
        cmd_add()
    elif args.cmd == "list":
        cmd_list()
    elif args.cmd == "stats":
        print_stats()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
