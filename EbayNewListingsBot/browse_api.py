"""
browse_api.py — thin wrapper around eBay's official Browse API.

Unlike sold/completed listings (which require a signed-in account — see
ebayCompletedListings/scraper.py), active-listing search is public data.
The Browse API only needs an Application Access Token (client-credentials
OAuth grant, no user login, no partner approval) — register a free keyset
at https://developer.ebay.com.
"""

import time
import base64
import logging

import requests

from config import EBAY_CLIENT_ID, EBAY_CLIENT_SECRET, EBAY_MARKETPLACE_ID

logger = logging.getLogger(__name__)

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
ITEM_URL = "https://api.ebay.com/buy/browse/v1/item/{item_id}"

_token_cache = {"access_token": None, "expires_at": 0}


def _get_token() -> str:
    """Fetch (or reuse) an Application Access Token. Tokens are valid ~2h."""
    now = time.time()
    if _token_cache["access_token"] and now < _token_cache["expires_at"] - 60:
        return _token_cache["access_token"]

    if not EBAY_CLIENT_ID or not EBAY_CLIENT_SECRET:
        raise RuntimeError("EBAY_CLIENT_ID / EBAY_CLIENT_SECRET not set — see .env.example")

    credentials = base64.b64encode(f"{EBAY_CLIENT_ID}:{EBAY_CLIENT_SECRET}".encode()).decode()
    resp = requests.post(
        TOKEN_URL,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "client_credentials",
            "scope": "https://api.ebay.com/oauth/api_scope",
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    _token_cache["access_token"] = data["access_token"]
    _token_cache["expires_at"] = now + data.get("expires_in", 7200)
    return _token_cache["access_token"]


def search_new_listings(query: str, category_id: str = None,
                         min_price=None, max_price=None, limit: int = 50) -> list:
    """Return active item summaries for `query`, newest first."""
    token = _get_token()

    filters = []
    if min_price is not None or max_price is not None:
        lo = "" if min_price is None else min_price
        hi = "" if max_price is None else max_price
        filters.append(f"price:[{lo}..{hi}]")
        filters.append("priceCurrency:USD")

    params = {
        "q": query,
        "sort": "newlyListed",
        "limit": str(limit),
        # estimatedAvailableQuantity (how many units are listed) is only
        # returned by item_summary/search when this is set.
        "fieldgroups": "EXTENDED",
    }
    if category_id and category_id != "0":
        params["category_ids"] = category_id
    if filters:
        params["filter"] = ",".join(filters)

    resp = requests.get(
        SEARCH_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": EBAY_MARKETPLACE_ID,
        },
        params=params,
        timeout=20,
    )
    if resp.status_code != 200:
        logger.error(f"Browse API error {resp.status_code}: {resp.text[:500]}")
        resp.raise_for_status()

    return resp.json().get("itemSummaries", [])


def get_available_quantity(item_id: str) -> int:
    """Look up how many units are listed for one item.

    Not available on item_summary/search (even with fieldgroups=EXTENDED) —
    only the single-item getItem endpoint returns estimatedAvailabilities.
    Only call this for items you actually need it for (e.g. genuinely new
    listings), since it's one extra API call per item.
    """
    token = _get_token()
    resp = requests.get(
        ITEM_URL.format(item_id=item_id),
        headers={
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": EBAY_MARKETPLACE_ID,
        },
        timeout=15,
    )
    if resp.status_code != 200:
        logger.warning(f"getItem failed for {item_id}: {resp.status_code}")
        return None

    availabilities = resp.json().get("estimatedAvailabilities") or []
    if not availabilities:
        return None
    return (availabilities[0].get("estimatedRemainingQuantity")
            or availabilities[0].get("estimatedAvailableQuantity"))
