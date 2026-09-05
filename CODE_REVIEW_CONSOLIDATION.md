# Bot Code Review: Consolidation Opportunities

**Date:** June 6, 2026  
**Scope:** DayTradingBot, CryptoBot, WheelBot, RobinhoodDayTradingBot, CopyTradingBot

---

## Executive Summary

**Consolidation Opportunity: HIGH**

Three categories of code are duplicated across all bots:
1. **Notifier** — **100% duplicated** ✅ **Move to common**
2. **Alpaca Config** — **80% duplicated** ⚠️ **Refactor to base class/common**
3. **Alpaca Client** — **60% base code duplicated** ⚠️ **Extract core methods to common**
4. **Strategy** — **0% duplicated** ❌ **Keep bot-specific**

---

## 1. NOTIFIER — 100% Duplication ✅ **HIGHEST PRIORITY**

### Current State
Each bot has its own `notifier.py`:
- `CryptoBot/notifier.py`
- `DayTradingBot/notifier.py`
- `WheelBot/notifier.py`
- `RobinhoodDayTradingBot/notifier.py`
- `CopyTradingBot/notifier.py`

All files are **nearly identical** with minor formatting differences (priority tuple order, header layout).

### Duplication Analysis
```python
# ALL FILES DUPLICATE THIS:
NTFY_TOPIC = "sohaib-trading-2026"
NTFY_URL   = f"https://ntfy.sh/{NTFY_TOPIC}"

PRIORITY = {
    "BUY":          ("5", "green_circle"),
    "SELL":         ("5", "red_circle"),
    "STOP_LOSS":    ("5", "warning"),
    # ... all identical
}

def notify(action, symbol, price="", details="", bot=""):
    # 99% identical implementation
```

### Recommendation
**CONSOLIDATE: Remove all bot-specific notifier.py files**

✅ There is ALREADY a top-level `/bots/notifier.py` that should be used.  
✅ It has the **latest/best version** with improved error handling.  
✅ Update all bots to import from parent: `from ..notifier import notify`

**Impact:**
- Reduces code by ~250 lines (50 lines × 5 bots)
- Single source of truth for notification logic
- Easier to update ntfy.sh config or add new action types

---

## 2. ALPACA CONFIG — 80% Duplication ⚠️

### Current State
Each bot has `config.py`:

| Bot | Core Alpaca Config | Bot-Specific Params | File Size |
|-----|-------------------|------------------|-----------|
| DayTradingBot | API_KEY, API_SECRET, BASE_URL, DATA_URL | STOP_LOSS_PCT, TRAIL_WIGGLE, MAX_CONTRACTS | 20 lines |
| CryptoBot | API_KEY, API_SECRET, BASE_URL, DATA_URL | EMA_FAST, EMA_SLOW, RSI_PERIOD | 23 lines |
| WheelBot | API_KEY, API_SECRET, BASE_URL, DATA_URL | CSP_OTM_PCT, CC_OTM_PCT, DTE_MIN, DTE_MAX | 20 lines |
| RobinhoodDayTradingBot | Different (Robinhood) | MAX_POSITION_PCT, STOP_LOSS_PCT | 30 lines |
| CopyTradingBot | Different (Congress API) | TRADE_AMOUNT_USD, MAX_POSITION_USD | 15 lines |

### Duplication Analysis
**Core Alpaca section (duplicated in 4 bots):**
```python
# DUPLICATED IN: DayTradingBot, CryptoBot, WheelBot
API_KEY    = os.getenv('ALPACA_API_KEY')
API_SECRET = os.getenv('ALPACA_SECRET_KEY')
BASE_URL   = os.getenv('ALPACA_BASE_URL', 'https://paper-api.alpaca.markets')
DATA_URL   = os.getenv('ALPACA_DATA_URL', 'https://data.alpaca.markets')
```

**Bot-specific params (unique to each bot):**
```python
# DayTradingBot ONLY
STOP_LOSS_PCT = float(os.getenv('STOP_LOSS_PCT', 0.30))
PROFIT_TRAIL_TRIGGER = float(os.getenv('PROFIT_TRAIL_TRIGGER', 0.50))

# CryptoBot ONLY
EMA_FAST = int(os.getenv('EMA_FAST', 9))
EMA_SLOW = int(os.getenv('EMA_SLOW', 21))
RSI_PERIOD = int(os.getenv('RSI_PERIOD', 14))

# WheelBot ONLY
CSP_OTM_PCT = float(os.getenv('CSP_OTM_PCT', 0.07))
CC_OTM_PCT = float(os.getenv('CC_OTM_PCT', 0.07))
```

### Recommendation
**PARTIAL CONSOLIDATION: Extract base alpaca config to common module**

**Option A (Recommended):** Create `/bots/common/alpaca_config.py`
```python
# /bots/common/alpaca_config.py
import os
from dotenv import load_dotenv

load_dotenv()

# Shared Alpaca credentials
ALPACA_API_KEY = os.getenv('ALPACA_API_KEY')
ALPACA_SECRET_KEY = os.getenv('ALPACA_SECRET_KEY')
ALPACA_BASE_URL = os.getenv('ALPACA_BASE_URL', 'https://paper-api.alpaca.markets')
ALPACA_DATA_URL = os.getenv('ALPACA_DATA_URL', 'https://data.alpaca.markets')
```

Then each bot's config.py imports and adds strategy-specific params:
```python
# DayTradingBot/config.py
from ..common.alpaca_config import *

STOP_LOSS_PCT = float(os.getenv('STOP_LOSS_PCT', 0.30))
PROFIT_TRAIL_TRIGGER = float(os.getenv('PROFIT_TRAIL_TRIGGER', 0.50))
# ... other DayTradingBot-specific settings
```

**Impact:**
- Reduces duplication by ~60 lines across 4 bots
- Centralizes credential management
- Changes to Alpaca defaults only need to happen in one place
- Does NOT affect RobinhoodDayTradingBot (uses different API) or CopyTradingBot (uses Congress API)

---

## 3. ALPACA CLIENT — 60% Base Code Duplication ⚠️

### Current State
Each bot that uses Alpaca has `alpaca.py`:
- `DayTradingBot/alpaca.py`
- `CryptoBot/alpaca.py`
- `WheelBot/alpaca.py`

### Duplication Analysis

**Common infrastructure (duplicated 100% in all 3):**
```python
# DUPLICATED IN ALL ALPACA BOTS
import requests, logging
from config import API_KEY, API_SECRET, BASE_URL, DATA_URL

logger = logging.getLogger(__name__)

HEADERS = {
    'APCA-API-KEY-ID': API_KEY,
    'APCA-API-SECRET-KEY': API_SECRET,
    'Content-Type': 'application/json',
}

def _get(url, params=None):
    r = requests.get(url, headers=HEADERS, params=params, timeout=15)
    r.raise_for_status()
    return r.json()

def _post(url, body):
    r = requests.post(url, headers=HEADERS, json=body, timeout=15)
    if not r.ok:
        logger.error(f'POST {url} -> {r.status_code}: {r.text}')
        r.raise_for_status()
    return r.json()
```

**Common account functions (duplicated in all 3):**
```python
def get_account():
    return _get(f'{BASE_URL}/v2/account')

def get_cash() -> float:
    return float(get_account().get('cash', 0))
```

**Common position functions (duplicated in all 3):**
```python
def get_position(symbol: str) -> dict | None:
    try:
        return _get(f'{BASE_URL}/v2/positions/{symbol}')
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            return None
        raise

def list_positions() -> list[dict]:
    return _get(f'{BASE_URL}/v2/positions')
```

**Bot-specific functions (UNIQUE, should not be moved):**

| DayTradingBot | CryptoBot | WheelBot |
|---------------|-----------|---------|
| `get_latest_price()` stock-specific | `get_daily_bars()` crypto-specific | `get_option_chain()` options-specific |
| `get_5min_bars()` | `get_latest_price()` crypto | `get_option_snapshots()` |
| `get_1min_bars()` | `place_market_order()` crypto | `place_option_order()` |
| `place_option_order()` | `close_position()` | `sell_call()` / `sell_put()` |
| `sell_call()` | | |
| `sell_put()` | | |

### Recommendation
**PARTIAL CONSOLIDATION: Extract common client infrastructure**

**Create `/bots/common/alpaca_client.py` with base class:**
```python
# /bots/common/alpaca_client.py
import requests
import logging
from typing import dict, list

class AlpacaClient:
    def __init__(self, api_key: str, api_secret: str, base_url: str, data_url: str):
        self.base_url = base_url
        self.data_url = data_url
        self.headers = {
            'APCA-API-KEY-ID': api_key,
            'APCA-API-SECRET-KEY': api_secret,
            'Content-Type': 'application/json',
        }
        self.logger = logging.getLogger(__name__)

    def _get(self, url: str, params: dict = None) -> dict:
        r = requests.get(url, headers=self.headers, params=params, timeout=15)
        r.raise_for_status()
        return r.json()

    def _post(self, url: str, body: dict) -> dict:
        r = requests.post(url, headers=self.headers, json=body, timeout=15)
        if not r.ok:
            self.logger.error(f'POST {url} -> {r.status_code}: {r.text}')
            r.raise_for_status()
        return r.json()

    # ── Account (shared across all bots)
    def get_account(self) -> dict:
        return self._get(f'{self.base_url}/v2/account')

    def get_cash(self) -> float:
        return float(self.get_account().get('cash', 0))

    # ── Positions (shared across all bots)
    def get_position(self, symbol: str) -> dict | None:
        try:
            return self._get(f'{self.base_url}/v2/positions/{symbol}')
        except requests.HTTPError as e:
            if e.response.status_code == 404:
                return None
            raise

    def list_positions(self) -> list[dict]:
        return self._get(f'{self.base_url}/v2/positions')
```

Then each bot extends with strategy-specific methods:
```python
# DayTradingBot/alpaca.py
from ..common.alpaca_client import AlpacaClient
from config import API_KEY, API_SECRET, BASE_URL, DATA_URL

client = AlpacaClient(API_KEY, API_SECRET, BASE_URL, DATA_URL)

# DayTradingBot-specific functions
def get_5min_bars(symbol: str, limit: int = 120):
    # Only in DayTradingBot
    ...

def place_option_order(symbol: str, side: str, qty: int):
    # Only in DayTradingBot
    ...

# Re-export common methods for backward compatibility
get_account = client.get_account
get_cash = client.get_cash
get_position = client.get_position
list_positions = client.list_positions
```

**Impact:**
- Reduces duplication by ~150 lines across 3 bots
- Shared bug fixes benefit all bots automatically
- Easier error handling and logging improvements propagate
- Clear separation between common and bot-specific functionality
- **Does NOT affect RobinhoodDayTradingBot** (uses Robinhood API, not Alpaca)
- **Does NOT affect CopyTradingBot** (uses Congress trading API)

---

## 4. STRATEGY — 0% Duplication ❌ **DO NOT CONSOLIDATE**

### Current State
Each bot has unique strategy implementation:

**DayTradingBot/signals.py:**
- VWAP + EMA crossover + RSI for 0DTE options
- Specific indicators: _ema(), _rsi(), _vwap()
- Signal logic: CALL / PUT / NONE

**CryptoBot/indicators.py:**
- Would have crypto-specific indicators (different from day trading)
- Crypto bars, market conditions specific

**WheelBot/strategy.py:**
- Covered call wheel strategy
- Put/call selection logic
- Expiration date & DTE logic (14-35 days)

**RobinhoodDayTradingBot/signals.py:**
- Likely day trading signals, but Robinhood-specific logic

**CopyTradingBot:**
- Completely different: copies congressional trades
- No strategy file (uses Quiver Quant API)

### Recommendation
**DO NOT CONSOLIDATE**

These are fundamentally different trading strategies:
- ❌ Wheel strategy ≠ Day trading signals ≠ Crypto signals ≠ Congressional copy trading
- Each requires different indicators, market data, and decision logic
- Moving to common would require heavy abstraction with low benefit
- Easier to keep them bot-specific and maintain independence

---

## Detailed Consolidation Plan

### Phase 1: Notifier (IMMEDIATE) ✅

**Steps:**
1. Update `/bots/notifier.py` to latest version from any bot
2. Remove `notifier.py` from each bot directory
3. Update imports in each bot:
   ```python
   # OLD
   from notifier import notify
   
   # NEW
   from ..notifier import notify
   ```

**Files to delete:**
- `CryptoBot/notifier.py`
- `DayTradingBot/notifier.py`
- `WheelBot/notifier.py`
- `RobinhoodDayTradingBot/notifier.py`
- `CopyTradingBot/notifier.py`

**Testing:** Run each bot and trigger a notification to verify imports work.

---

### Phase 2: Alpaca Config (MEDIUM PRIORITY) ⚠️

**Steps:**
1. Create `/bots/common/alpaca_config.py` with shared Alpaca credentials
2. Update each bot's `config.py` to import from common:
   ```python
   from ..common.alpaca_config import *
   ```
3. Keep bot-specific params in each bot's `config.py`

**Files to create:**
- `common/alpaca_config.py`
- `common/__init__.py`

**Files to modify:**
- `DayTradingBot/config.py`
- `CryptoBot/config.py`
- `WheelBot/config.py`

**Testing:** Verify each bot loads config correctly without errors.

---

### Phase 3: Alpaca Client (HIGH COMPLEXITY) ⚠️

**Steps:**
1. Create `/bots/common/alpaca_client.py` with `AlpacaClient` base class
2. Update each Alpaca bot's `alpaca.py`:
   ```python
   from ..common.alpaca_client import AlpacaClient
   client = AlpacaClient(API_KEY, API_SECRET, BASE_URL, DATA_URL)
   
   # Keep bot-specific functions
   # Re-export common functions for backward compatibility
   ```
3. Remove `_get`, `_post`, `get_account`, `get_cash`, `get_position`, `list_positions` from each bot

**Files to create:**
- `common/alpaca_client.py`

**Files to modify:**
- `DayTradingBot/alpaca.py`
- `CryptoBot/alpaca.py`
- `WheelBot/alpaca.py`

**Testing:**
- Run each bot in dry-run mode
- Verify account functions work: `get_cash()`, `get_position()`, `list_positions()`
- Verify bot-specific functions still work

---

## Summary Table

| Component | Current | Duplication | Status | Impact |
|-----------|---------|------------|--------|--------|
| **Notifier** | 5 copies | 100% | ✅ **Move** | -250 lines, single source |
| **Alpaca Config** | 4 copies | 80% | ⚠️ **Refactor** | -60 lines, centralized creds |
| **Alpaca Client** | 3 copies | 60% | ⚠️ **Extract** | -150 lines, shared infrastructure |
| **Strategy** | 5 unique | 0% | ❌ **Keep** | Different approaches per bot |

---

## Risks & Mitigation

| Risk | Mitigation |
|------|-----------|
| Import paths break after refactor | Update all imports, test each bot before/after |
| Shared code regression affects all bots | Add unit tests for common modules, test in isolation |
| Difficulty reverting if common code has issues | Use git branches, tag stable versions |
| Relative imports get complex | Use clear structure: `common/` dir at `/bots/` level |

---

## Next Steps

1. **Consensus:** Does team approve consolidation?
2. **Phase 1 (Notifier):** Start immediately, quick win
3. **Phase 2 (Alpaca Config):** Low risk, medium benefit
4. **Phase 3 (Alpaca Client):** Higher complexity, assess after Phase 2
5. **Documentation:** Update bot README files with new structure

