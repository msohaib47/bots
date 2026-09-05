"""
config.py — loads .env and exposes constants for EbayNewListingsBot
"""

import os
from pathlib import Path
from dotenv import load_dotenv

BOT_DIR = Path(__file__).parent

env_file = BOT_DIR / ".env"
if env_file.exists():
    load_dotenv(env_file)

# eBay Developer Program application credentials (Browse API, no user login
# needed — client-credentials / "Application Access Token" grant only).
EBAY_CLIENT_ID = os.environ.get("EBAY_CLIENT_ID")
EBAY_CLIENT_SECRET = os.environ.get("EBAY_CLIENT_SECRET")
EBAY_MARKETPLACE_ID = os.environ.get("EBAY_MARKETPLACE_ID", "EBAY_US")

# Search terms + max price live in a plain JSON config file, not code, so
# they can be edited without touching bot.py.
CONFIG_FILE = Path(os.environ.get("EBAY_NEW_CONFIG_FILE", str(BOT_DIR / "configs.json")))

# Local storage — same convention as ebayCompletedListings: not on the CIFS
# mount, since SQLite locking doesn't work there.
DB_PATH = Path(os.environ.get("EBAY_NEW_DB_PATH", "/home/claude/data/ebay_new_listings.db"))

# Max items to fetch per search per run (Browse API allows up to 200/call).
CHECK_LIMIT = int(os.environ.get("EBAY_NEW_LIMIT", "50"))

LOG_FILE = str(BOT_DIR / "logs" / "bot.log")
