# EbayNewListingsBot

Watches eBay for newly listed (active, not sold) items matching configured search terms and price ceilings, and sends an ntfy push notification for each new match.

Unlike `ebayCompletedListings` (sold/completed listings, which require a signed-in eBay account and were fought hard against bot detection — see that bot's README), active-listing search is public data. This bot uses eBay's official **Browse API** with a client-credentials token — no login, no partner approval, no scraping, no bot-detection risk.

## Setup

1. Register a free eBay developer account at [developer.ebay.com](https://developer.ebay.com) and create a **Production** keyset under "Application Keys". Copy the Client ID and Client Secret.
2. `cp .env.example .env` and fill in `EBAY_CLIENT_ID` / `EBAY_CLIENT_SECRET`.
3. Edit `configs.json` to set your search terms and max prices (see below).
4. Install deps: `pip install -r requirements.txt`
5. Test: `python bot.py`
6. Check history: `python bot.py --status`

## Configuring search terms

Edit `configs.json` — a plain JSON list, no code changes needed:

```json
[
  {
    "name": "R640",            // label shown in notifications/status
    "query": "Dell R640",      // eBay search query
    "category_id": "58058",    // optional eBay category filter ("0" or omit = all)
    "min_price": null,         // optional
    "max_price": 1000          // optional — filters out anything pricier
  }
]
```

Find category IDs the same way as `ebayCompletedListings`: search on eBay, pick a category in the left rail, read `_sacat=XXXXX` from the URL.

## Notifications

Sends via the shared `common/notifier.py` (ntfy.sh topic `sohaib-trading-2026`), same as the trading bots — action type `NEW_LISTING`, bot label `EbayNewListingsBot`. Notification body includes the listing title and URL.

## Dedup

Each item's eBay `itemId` is recorded in a local SQLite DB (`EBAY_NEW_DB_PATH`, default `/home/claude/data/ebay_new_listings.db`) the first time it's seen, so repeated cron runs don't re-notify on the same listing.

## Cron

No `setup.sh` — wire it up the same way as the other Python bots (see root `CLAUDE.md`): a `~/ebaynewlistingsbot.sh` wrapper that `cd`s into `~/bots-live/EbayNewListingsBot`, sets `PYTHONPATH=/home/claude/bots-live`, and runs `python bot.py`. Suggested schedule: every 15–30 min during the day (`*/15 8-22 * * *`) — active listings churn faster than sold listings, so a daily check is too slow to catch a good deal.

## Rate limits

Browse API allows 5,000 calls/day and up to 200 items per call on the free tier — one call per config per run. At a 15-minute cron interval with even a dozen configs, you're nowhere near the limit.
