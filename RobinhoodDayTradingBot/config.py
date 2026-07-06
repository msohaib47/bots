import os
from dotenv import load_dotenv

load_dotenv()

# Robinhood credentials (set in .env)
RH_USERNAME      = os.getenv('RH_USERNAME')
RH_PASSWORD      = os.getenv('RH_PASSWORD')
RH_MFA_CODE      = os.getenv('RH_MFA_CODE', '')       # TOTP secret for MFA (optional)
# Standard API only exposes the margin account; agentic cash acct (706672094)
# is MCP-only and not reachable from a cron bot.
ACCOUNT_NUMBER   = os.getenv('RH_ACCOUNT', '5UN85130')
DRY_RUN          = os.getenv('DRY_RUN', 'true').lower() in ('1', 'true', 'yes')

# Position sizing
MAX_POSITION_PCT = float(os.getenv('MAX_POSITION_PCT', 0.20))  # max 20% of buying power per trade
STOP_LOSS_PCT    = float(os.getenv('STOP_LOSS_PCT', 0.02))     # 2% stop loss per trade
PROFIT_TARGET_PCT = float(os.getenv('PROFIT_TARGET_PCT', 0.04)) # 4% take-profit target

# Trading hours (ET)
NO_NEW_ENTRY_TIME = os.getenv('NO_NEW_ENTRY_TIME', '15:45')
FORCE_CLOSE_TIME  = os.getenv('FORCE_CLOSE_TIME', '15:50')

from symbols import SYMBOLS  # edit symbols.py to change trading symbols

# File paths
STATE_FILE = 'positions.json'
LOG_FILE   = 'logs/rhbot.log'
TRADES_LOG = 'trades.csv'
