"""
scraper.py — curl_cffi + BeautifulSoup scraper for eBay sold listings.

eBay renders sold-listings pages server-side. We use curl_cffi's browser
impersonation (TLS fingerprint + header order) to avoid bot detection.

IMPORTANT: do NOT override User-Agent, Accept, or Accept-Encoding when using
impersonate= — curl_cffi manages those automatically. Overriding them breaks
the fingerprint and gets requests blocked or stalled by eBay's CDN.
"""

import re
import time
import random
import logging
from datetime import datetime
from typing import Generator

from curl_cffi import requests
from curl_cffi.requests import RequestsError
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

EBAY_SEARCH_URL = (
    "https://www.ebay.com/sch/i.html"
    "?_nkw={query}"
    "&_sacat={category}"
    "&LH_Sold=1"
    "&LH_Complete=1"
    "&_sop=13"
    "&_ipg=240"
    "&_pgn={page}"
)

PAGE_DELAY_MIN = 3.0
PAGE_DELAY_MAX = 6.0
MAX_PAGES = 5
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3

# Only set headers that don't conflict with impersonation.
# curl_cffi handles User-Agent, Accept, Accept-Encoding automatically.
EXTRA_HEADERS = {
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.ebay.com/",
}

# Fallback impersonation options if primary fails
IMPERSONATE_OPTIONS = ["chrome120", "chrome110", "chrome124"]


def _build_url(query: str, category: str, page: int,
               min_price=None, max_price=None) -> str:
    url = EBAY_SEARCH_URL.format(
        query=query.replace(" ", "+"),
        category=category or "0",
        page=page,
    )
    if min_price is not None:
        url += f"&_udlo={min_price}"
    if max_price is not None:
        url += f"&_udhi={max_price}"
    return url


def _parse_price(text: str):
    if not text:
        return None
    m = re.search(r"[\d,]+\.?\d*", text.replace(",", ""))
    return float(m.group()) if m else None


def _parse_date(text: str):
    if not text:
        return None
    text = re.sub(r"^Sold\s*", "", text, flags=re.IGNORECASE).strip()
    for fmt in ("%b %d, %Y", "%b %d"):
        try:
            dt = datetime.strptime(text, fmt)
            if fmt == "%b %d":
                dt = dt.replace(year=datetime.now().year)
            return dt.date().isoformat()
        except ValueError:
            continue
    return None


def _parse_shipping(card) -> float:
    """Extract shipping cost from attribute rows.
    eBay shows 'Free delivery' or '+$12.34 delivery' in the attribute list.
    """
    for row in card.select(".s-card__attribute-row"):
        text = row.get_text(strip=True)
        if "delivery" in text.lower() or "shipping" in text.lower():
            if "free" in text.lower():
                return 0.0
            amount = _parse_price(text)
            if amount is not None:
                return amount
    return 0.0


def _extract_listings(soup: BeautifulSoup, config_name: str) -> list:
    listings = []
    for item in soup.select("li.s-card"):
        try:
            # Only process genuinely sold items — they have a "Sold Item" caption
            date_el = item.select_one('.s-card__caption span[aria-label="Sold Item"]')
            if not date_el:
                continue
            sold_date = _parse_date(date_el.get_text(strip=True))

            # Title (primary text in the heading)
            title_el = item.select_one(".s-card__title .su-styled-text")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            if "Shop on eBay" in title or not title:
                continue

            # Price must have 'positive' class (green = sold price)
            price_el = item.select_one(".s-card__price")
            if not price_el or "positive" not in price_el.get("class", []):
                continue
            sold_price = _parse_price(price_el.get_text())
            if not sold_price:
                continue

            shipping = _parse_shipping(item)
            total = round(sold_price + shipping, 2)

            # Condition is in the subtitle
            cond_el = item.select_one(".s-card__subtitle .su-styled-text")
            condition = cond_el.get_text(strip=True) if cond_el else None

            # Link — first s-card__link in the header (not the image link)
            link_el = item.select_one(".su-card-container__header a.s-card__link")
            url = link_el["href"] if link_el else None
            if url and "?" in url:
                url = url.split("?")[0]

            # Image — use data-defer-load (lazy src) falling back to src
            img_el = item.select_one("img.s-card__image")
            image_url = None
            if img_el:
                image_url = img_el.get("data-defer-load") or img_el.get("src")

            listings.append({
                "search_config": config_name,
                "title":         title,
                "sold_price":    sold_price,
                "shipping_cost": shipping,
                "total_price":   total,
                "sold_date":     sold_date,
                "condition":     condition,
                "listing_url":   url,
                "image_url":     image_url,
            })

        except Exception as e:
            logger.debug(f"Skipped item: {e}")

    return listings


def _get_with_retry(session, url: str, name: str, page_num: int):
    """GET with exponential-backoff retries. Returns Response or raises."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp
        except RequestsError as e:
            if attempt < MAX_RETRIES:
                wait = 2 ** attempt + random.uniform(0, 2)
                logger.warning(
                    f"[{name}] Page {page_num} attempt {attempt} failed: {e}. "
                    f"Retrying in {wait:.1f}s…"
                )
                time.sleep(wait)
            else:
                raise


def scrape_config(config: dict) -> Generator[dict, None, None]:
    name     = config["name"]
    query    = config["query"]
    category = config.get("category_id") or "0"
    min_p    = config.get("min_price")
    max_p    = config.get("max_price")

    logger.info(f"[{name}] Starting scrape — query='{query}' category={category}")

    # Try impersonation options in order; use the first one that works
    session = None
    for impersonate in IMPERSONATE_OPTIONS:
        try:
            session = requests.Session(impersonate=impersonate)
            session.headers.update(EXTRA_HEADERS)
            # Warm up: visit homepage so eBay issues session cookies
            session.get("https://www.ebay.com/", timeout=REQUEST_TIMEOUT)
            logger.info(f"[{name}] Session established (impersonate={impersonate})")
            time.sleep(random.uniform(2.0, 4.0))
            break
        except Exception as e:
            logger.warning(f"[{name}] Warm-up failed with {impersonate}: {e}")
            session = None

    if session is None:
        logger.error(f"[{name}] Could not establish eBay session after all impersonation options.")
        return

    for page_num in range(1, MAX_PAGES + 1):
        url = _build_url(query, category, page_num, min_p, max_p)
        logger.info(f"[{name}] Page {page_num}: {url}")

        try:
            resp = _get_with_retry(session, url, name, page_num)
        except RequestsError as e:
            logger.error(f"[{name}] Page {page_num} failed after {MAX_RETRIES} attempts: {e}")
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        listings = _extract_listings(soup, name)
        logger.info(f"[{name}] Page {page_num}: found {len(listings)} listings")

        if not listings:
            # Dump full HTML to a file for debugging
            debug_path = f"debug_{name}_page{page_num}.html"
            try:
                with open(debug_path, "w", encoding="utf-8") as f:
                    f.write(resp.text)
                logger.warning(
                    f"[{name}] No listings on page {page_num} "
                    f"(status={resp.status_code}, size={len(resp.text)} bytes). "
                    f"Full HTML saved to {debug_path} — open it in a browser to see what eBay returned."
                )
            except Exception as dump_err:
                snippet = resp.text[:800].replace("\n", " ")
                logger.warning(f"[{name}] No listings on page {page_num}. Snippet: {snippet}")
            break

        yield from listings

        # Check for next page
        next_btn = soup.select_one("a.pagination__next")
        if not next_btn or next_btn.get("aria-disabled") == "true":
            logger.info(f"[{name}] Last page reached.")
            break

        if page_num < MAX_PAGES:
            time.sleep(random.uniform(PAGE_DELAY_MIN, PAGE_DELAY_MAX))

    logger.info(f"[{name}] Scrape complete.")
