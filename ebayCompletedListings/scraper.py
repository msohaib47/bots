"""
scraper.py — eBay sold listings via the Apify "sync-network/ebay-sold-listings-scraper"
actor, called through the Apify API.

Replaced the in-house Playwright scraper (2026-08-14) after eBay's Akamai bot
detection blocked every run for a week straight ("Sign in or Register" /
"Security Measure") despite an authenticated cookie session, real Chrome, and
WebGL fingerprint spoofing — see git history / project memory for the full
debugging trail.

Tried three Apify actors live before settling on caffein.dev:
- sync-network/ebay-sold-listings-scraper ($2/1K, cheapest): plain HTTP fetch
  with no proxy at all — 403'd immediately, same block we were trying to
  escape. Not usable.
- crawloop/ebay-sold-listings-scraper ($3.50/1K): also gets blocked on the
  direct sold-search page, but falls back to a search-engine item-ID
  discovery + per-item PDP scrape, which does get through. Works, but
  slower and pricier.
- caffein.dev/ebay-sold-listings (from $2.50/1K): fetches the sold-search
  page directly via their own fetch service and got through cleanly with no
  block on every test run. Cheapest actor that actually works — this is
  what's wired in below.

Pricing is per RESULT RETURNED, not per new/deduped item — keep `count` and
`daysToScrape` small for the daily cron run (see COUNT_PER_RUN / DAYS_TO_SCRAPE
below) since dedup against the local DB happens after Apify has already been
paid for every item it returned.
"""

import os
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Generator, Optional

from dotenv import load_dotenv
from apify_client import ApifyClient

logger = logging.getLogger(__name__)

# Load directly here rather than relying on emailer.py's load_dotenv() —
# agent.py imports scraper before emailer, so APIFY_API_TOKEN would read as
# None at module-load time otherwise.
env_file = Path(__file__).parent / ".env"
if env_file.exists():
    load_dotenv(env_file)

ACTOR_ID = "caffein.dev/ebay-sold-listings"

APIFY_API_TOKEN = os.environ.get("APIFY_API_TOKEN")

# Kept small on purpose — this actor bills per result returned, and daily
# runs only need to catch what's sold recently (dedup against the DB is
# free; results returned by Apify are not).
#
# DAYS_TO_SCRAPE=1 was tested live and returned 0 results — a listing that
# sold "today" got excluded by what looks like a timezone edge in the
# actor's date window. 2 days reliably picked up the same item, so that's
# the floor for a daily cron run.
COUNT_PER_RUN = int(os.environ.get("EBAY_APIFY_COUNT", "25"))
DAYS_TO_SCRAPE = int(os.environ.get("EBAY_APIFY_DAYS", "2"))


def _parse_sold_date(raw) -> Optional[str]:
    """Normalize whatever date/datetime format the actor returns to an ISO date string."""
    if not raw:
        return None
    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(raw / 1000 if raw > 1e10 else raw, tz=timezone.utc).date().isoformat()
        except (ValueError, OSError):
            return None
    raw = str(raw)
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return raw[:10] if len(raw) >= 10 else raw


def _map_item(item: dict, config_name: str) -> Optional[dict]:
    title = item.get("title")
    url = item.get("url") or item.get("itemUrl") or item.get("link")
    if not title or not url:
        logger.debug(f"[{config_name}] Skipping item with no title/url: {item}")
        return None

    if "?" in url:
        url = url.split("?")[0]

    sold_price = item.get("soldPrice")
    if sold_price is None:
        sold_price = item.get("price")
    if sold_price is None:
        logger.debug(f"[{config_name}] Skipping item with no sold price: {item}")
        return None

    shipping_cost = item.get("shippingPrice")
    if shipping_cost is None:
        shipping_cost = item.get("shippingCost", 0.0)
    shipping_cost = shipping_cost or 0.0

    total_price = item.get("totalPrice")
    if total_price is None:
        total_price = round(float(sold_price) + float(shipping_cost), 2)

    return {
        "search_config": config_name,
        "title":         title,
        "sold_price":    float(sold_price),
        "shipping_cost": float(shipping_cost),
        "total_price":   float(total_price),
        "sold_date":     _parse_sold_date(item.get("endedAt") or item.get("soldDate")),
        "condition":     item.get("condition"),
        "listing_url":   url,
        "image_url":     item.get("imageUrl") or item.get("image"),
    }


def scrape_config(config: dict) -> Generator[dict, None, None]:
    name     = config["name"]
    query    = config["query"]
    category = config.get("category_id")
    min_p    = config.get("min_price")
    max_p    = config.get("max_price")

    if not APIFY_API_TOKEN:
        logger.error(f"[{name}] APIFY_API_TOKEN not set — skipping scrape. Add it to .env.")
        return

    logger.info(f"[{name}] Starting Apify scrape — query='{query}' category={category or 'any'}")

    run_input = {
        "keywords": [query],
        "ebaySite": "ebay.com",
        "count": COUNT_PER_RUN,
        "daysToScrape": DAYS_TO_SCRAPE,
    }
    if category and category != "0":
        run_input["categoryId"] = category
    if min_p is not None:
        run_input["minPrice"] = min_p
    if max_p is not None:
        run_input["maxPrice"] = max_p

    try:
        client = ApifyClient(APIFY_API_TOKEN)
        run = client.actor(ACTOR_ID).call(run_input=run_input)
        dataset_id = run.default_dataset_id if hasattr(run, "default_dataset_id") else run["defaultDatasetId"]

        count = 0
        for item in client.dataset(dataset_id).iterate_items():
            mapped = _map_item(item, name)
            if mapped:
                count += 1
                yield mapped
        logger.info(f"[{name}] Apify run returned {count} usable listing(s).")
    except Exception as e:
        logger.error(f"[{name}] Apify scrape failed: {e}")
        return

    logger.info(f"[{name}] Scrape complete.")
