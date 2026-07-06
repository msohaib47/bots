import os
from dotenv import load_dotenv

load_dotenv()

RH_USERNAME    = os.getenv('RH_USERNAME')
RH_PASSWORD    = os.getenv('RH_PASSWORD')
DRY_RUN        = os.getenv('DRY_RUN', 'true').lower() in ('1', 'true', 'yes')

# Stop loss parameters
INITIAL_STOP_DISTANCE = float(os.getenv('INITIAL_STOP_DISTANCE', '20.0'))  # flat $20 below mark
TRAILING_STOP_PCT     = float(os.getenv('TRAILING_STOP_PCT', '0.20'))       # 20% below mark when trailing
PROFIT_THRESHOLD      = float(os.getenv('PROFIT_THRESHOLD', '1.0'))         # 100% profit to activate trailing

LOG_FILE   = 'logs/safeguard.log'
STATE_FILE = 'guard_state.json'
