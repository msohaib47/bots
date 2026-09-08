# backtesting-engine

Shared historical-data layer for backtesting any bot in this repo. See
`../BACKTESTING_ENGINE_PLAN.md` for the full architecture writeup and roadmap — this
file is just usage notes for what's actually built so far (Phase 1: data layer only).

## What's here

- `market_data.py` — `get_bars(symbol, timeframe, start_date, end_date)`: cached,
  paginated Alpaca bar fetch. Cached per `(symbol, timeframe)` — a 5Min request and a
  1Min request for the same symbol are separate cache entries (see the plan doc's "one
  download, use for both" caveat for why no local resampling is attempted here).
- `options_data.py` — `get_option_contract(underlying, opt_type, spot, as_of)` and
  `get_option_price_path(option_symbol, day, start_time)` + `price_at(path, when)`:
  cached historical option contract selection and trade-price tapes. This one *is*
  fully shared, byte-for-byte, across any number of consumers — no per-consumer
  variation the way bar timeframe creates for `market_data.py`.
- `_alpaca.py` — internal, one shared `AlpacaClient` instance. Not for bots to import.
- `cache/` — `bars/`, `options_contracts/`, `options_trades/`, all currently **empty**.
  `DayTradingBot/cache_alpaca/` already holds real cached data (9 months of several
  symbols' bars + options data from this week's backtesting) but is **not** moved here
  yet — it's actively being refreshed. Once that refresh finishes, copy its contents
  into the matching subfolder here:
  - `DayTradingBot/cache_alpaca/<SYMBOL>.json` → `cache/bars/<SYMBOL>_5Min.json`
    (note the added `_5Min` suffix — v1's old cache files have no timeframe in the
    name since v1 only ever fetched one timeframe; this package's cache is keyed by
    `(symbol, timeframe)` so the filename needs it)
  - `DayTradingBot/cache_alpaca/options_contracts/` → `cache/options_contracts/`
    (same filenames, no change needed)
  - `DayTradingBot/cache_alpaca/options_trades/` → `cache/options_trades/`
    (same filenames, no change needed)

## Usage

```python
import sys
sys.path.insert(0, r'D:\Work\bots\backtesting-engine')  # or wherever this lives

from market_data import get_bars
from options_data import get_option_contract, get_option_price_path, price_at

bars = get_bars('SPY', '5Min', start_date='2026-06-01', end_date='2026-09-01')

contract = get_option_contract('SPY', 'call', spot=750.0, as_of='2026-06-01')
if contract:
    path = get_option_price_path(contract['symbol'], '2026-06-01')
    mid_at_10am = price_at(path, some_datetime)
```

This package's directory needs to be on `sys.path` directly (like every other bot in
this repo does with its own siblings) — it's not installed as a pip package, and its
folder name (`backtesting-engine`, with a hyphen) can't be used in a dotted `import`
statement anyway.

## Before first real use

No live network call was made against this code in the pass that wrote it — this
repo's bots each keep their own `.env`, and there's no root-level `D:\Work\bots\.env`
for `common/alpaca_config.py` (which both `market_data.py` and `options_data.py` rely
on for credentials) to find when a script here is run directly. Either:

- add a `D:\Work\bots\.env` with `ALPACA_API_KEY`/`ALPACA_SECRET_KEY` (and optionally
  `ALPACA_BASE_URL`/`ALPACA_DATA_URL` if not using the paper-trading defaults), or
- call `dotenv.load_dotenv('/path/to/some/bot/.env')` explicitly before importing
  anything from this package.

Then run a small real fetch (e.g. `get_bars('SPY', '5Min', start_date='2026-09-01',
end_date='2026-09-04')` — a cheap, short range) to confirm credentials resolve and the
cache file gets written, before trusting this against a real multi-month backtest.

## Not done yet (see the plan doc)

- `DayTradingBot/backtest.py` still uses its own inline fetch/cache code — pointing it
  at this shared module instead is a deliberate future step, not done in this pass.
- No simulated broker, no v2 orchestrator, no DI refactor to v2's services — Phase 1
  is the data layer only.
