# eBay Sold Price Agent

Scrapes eBay "Sold Listings" for configurable search terms and stores results in SQLite. Runs on a cron schedule. Built with Playwright for reliable JavaScript-rendered page handling.

## Quick start

```bash
# 1. Install and set up cron
bash setup.sh

# 2. Edit your search configs in agent.py (DEFAULT_CONFIGS list)
#    or add them interactively:
python agent.py add

# 3. Run manually to test
python agent.py run

# 4. View stats
python agent.py stats
```

## Files

| File | Purpose |
|------|---------|
| `agent.py` | Orchestrator — entry point for all commands |
| `scraper.py` | Playwright scraper for eBay sold listings |
| `db.py` | SQLite schema, helpers, upsert logic |
| `analyze.py` | Stats (avg, median, IQR, outliers) + CSV export |
| `emailer.py` | Email reports with daily/yesterday listings |
| `setup.sh` | One-time install: venv, Playwright, cron |
| `.env.example` | Template for email credentials (copy to `.env`) |

## Commands

```bash
python agent.py run                       # scrape all active configs
python agent.py run --config "F-150 Crew 4x4"   # scrape one config
python agent.py add                       # interactively add a config
python agent.py list                      # show all configs
python agent.py stats                     # summary stats for all configs
```

Export to CSV from Python:
```python
from analyze import export_csv
export_csv("F-150 Crew 4x4", days=60)  # → exports/f-150_crew_4x4_2025-01-15.csv
```

## Configuring search targets

Edit `DEFAULT_CONFIGS` in `agent.py`:

```python
DEFAULT_CONFIGS = [
    {
        "name": "F-150 Crew 4x4",          # human-readable label
        "query": "ford f-150 crew cab 4x4", # eBay search string
        "category_id": "6001",              # eBay Motors > Cars & Trucks
        "min_price": 5000,
        "max_price": 35000,
    },
]
```

### Useful eBay category IDs

| ID | Category |
|----|---------|
| `0` | All categories |
| `6001` | eBay Motors > Cars & Trucks |
| `6028` | eBay Motors > Motorcycles |
| `293` | Consumer Electronics |
| `58058` | Computers/Tablets & Networking |
| `1249` | Video Games & Consoles |
| `625` | Cameras & Photo |
| `11450` | Clothing, Shoes & Accessories |

To find any category ID: search on eBay, select a category in the left rail, and look at `_sacat=XXXXX` in the URL.

## Email reports

After each run, agent.py sends HTML emails with listings sold today and yesterday for each config. One email per config, sent to `solutionzeroone@gmail.com`.

### Set up email

1. **Gmail:** Create an [App Password](https://myaccount.google.com/apppasswords) (not your regular password)
   - Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
   - Select "Mail" and "Windows Computer" → Google generates a 16-char password
   - Use that 16-char password in `SMTP_PASS` below

2. **Copy the example file:**
```bash
cp .env.example .env
```

3. **Edit `.env` with your credentials:**
```
SMTP_USER=your-email@gmail.com
SMTP_PASS=your-16-char-app-password
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
EMAIL_RECIPIENT=solutionzeroone@gmail.com
```

4. **Optional:** Install dependencies:
```bash
pip install python-dotenv
```

To disable emails, simply delete the `.env` file or leave `SMTP_USER` and `SMTP_PASS` empty.

## Cron schedule

Default: **Sundays and Wednesdays at 7:00 AM**.

Edit with `crontab -e`. Common alternatives:

```
0 7 * * 0       # weekly (Sundays)
0 7 * * *       # daily
0 */12 * * *    # every 12 hours
```

## Database schema

**`sold_listings`** — core data table

| Column | Type | Description |
|--------|------|-------------|
| `search_config` | TEXT | Config name |
| `title` | TEXT | Listing title |
| `sold_price` | REAL | Item price (no shipping) |
| `shipping_cost` | REAL | Shipping (0 if free) |
| `total_price` | REAL | sold_price + shipping |
| `sold_date` | TEXT | ISO date the item sold |
| `condition` | TEXT | New / Used / etc. |
| `listing_url` | TEXT | eBay listing URL (unique key) |
| `scraped_at` | TEXT | When we captured it |

Query directly:
```bash
sqlite3 ebay_sold.db "SELECT title, total_price, sold_date FROM sold_listings WHERE search_config='F-150 Crew 4x4' ORDER BY sold_date DESC LIMIT 20;"
```

## Notes on eBay scraping

- Playwright uses headless Chromium with a real user-agent, which is significantly more resilient than plain `requests`.
- The scraper adds a 2.5–5 second delay between pages to stay under rate limits.
- Duplicate listings (same URL) are silently skipped via `INSERT OR IGNORE`.
- eBay occasionally changes its HTML structure; if scraping breaks, check the CSS selectors in `scraper.py → _extract_listings()`.
- Max pages per run is set to 5 × 240 items = up to 1,200 listings per config per run. Adjust `MAX_PAGES` in `scraper.py`.
