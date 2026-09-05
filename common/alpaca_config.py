"""
Shared Alpaca API configuration used by DayTradingBot, CryptoBot, and WheelBot.
Import from this module to get Alpaca credentials and endpoints.

Usage:
    from ..common.alpaca_config import API_KEY, API_SECRET, BASE_URL, DATA_URL
"""
import os
from dotenv import load_dotenv

load_dotenv()

# Shared Alpaca credentials and endpoints
API_KEY    = os.getenv('ALPACA_API_KEY')
API_SECRET = os.getenv('ALPACA_SECRET_KEY')
BASE_URL   = os.getenv('ALPACA_BASE_URL', 'https://paper-api.alpaca.markets')
DATA_URL   = os.getenv('ALPACA_DATA_URL', 'https://data.alpaca.markets')
