import os
from dotenv import load_dotenv

load_dotenv()

ALPACA_API_KEY = os.getenv('ALPACA_API_KEY')
ALPACA_SECRET_KEY = os.getenv('ALPACA_SECRET_KEY')
ALPACA_BASE_URL = os.getenv('ALPACA_BASE_URL', 'https://paper-api.alpaca.markets')

TARGET_POLITICIAN = os.getenv('TARGET_POLITICIAN', 'Markwayne Mullin')

# Dollar amount per copied trade
TRADE_AMOUNT_USD = float(os.getenv('TRADE_AMOUNT_USD', 300))

# Max to hold in any single position
MAX_POSITION_USD = float(os.getenv('MAX_POSITION_USD', 2000))

QUIVER_API_URL = 'https://api.quiverquant.com/beta/live/congresstrading'
TRADES_LOG = 'trades_log.csv'
LOG_FILE = 'bot.log'
