# DT-Webull

Webull-API variant of `DayTradingBot` (v1) — same signal math, same
stop-loss/trailing-stop/scale-out rules, same risk limits. Built as a
self-contained copy (not a shared import), per this repo's convention that
each bot owns its own code — see `webull.py`'s docstring for exactly which
pieces are copied vs. adapted.

**Multi-account**: configure any number of Webull accounts in `.env`
(`WEBULL_ACCOUNTS=main,secondary,...` + one `WEBULL_<NAME>_*` block each).
Every cron tick computes each symbol's signal once (signals don't depend on
which account trades them) and then runs the full
force-close/stop-check/entry pipeline once per configured account, each
against its own `positions_<name>.json`/`trades_<name>.csv`/etc.

## Setup

```bash
cp .env.example .env
# fill in real WEBULL_<NAME>_APP_KEY / APP_SECRET / ACCOUNT_ID
pip install -r requirements.txt
python bot.py --status         # sanity check, all configured accounts
python bot.py --status main    # just one account
```

`.env` currently ships with **placeholder credentials** (`WEBULL_BASE_URL`
defaults to the **sandbox** host specifically so a stray real run can't touch
money) — replace them once real Webull OpenAPI credentials are available.

## What's verified

Started 2026-09-07 against `developer.webull.com`'s public docs with no real
credentials. Once the user provided real paper-trading credentials, every
piece below was checked against the **live sandbox API** (`test_connection.py`,
`test_market_data.py`) or the real SDK source on GitHub — not just matched
against documentation:

- **Auth/signing** (`webull.py::_sign`/`_headers`) — live-verified. One real
  bug found and fixed: the signed string needs full URL-encoding
  (`quote(str3, safe='')`) before HMAC-SHA1, a step the docs mentioned but
  didn't spell out; missing it produced a well-formed but wrong signature
  with no error hinting at a signing problem specifically.
- **Host**: `api.sandbox.webull.com` — confirmed empirically (real account
  data returned), after a red herring: the official demo repo's own sample
  host (`us-openapi-alb.uat.webullbroker.com`) 401'd, because that demo's
  sample credentials are scoped to a different environment than this app.
- **Account discovery, balance, positions** — all live-tested with real
  data (`list_accounts()`, `get_account()` → real $1,000,000 paper balance,
  `list_positions()` → `[]`).
- **Stock quotes and bars** — live-tested, real SPY price/OHLCV data.
- **Options chain lookup and quotes** — live-tested, found a real ATM SPY
  call contract with a real bid/ask.
- **Order placement/cancel shape** — matches Webull's own official demo
  sample code exactly (`legs`-based, not an OCC symbol string like Alpaca).
  Not yet tested with an actual placed order.

See `webull.py`'s module docstring for the full list of confirmed REST paths
and the exact field-name gotchas found along the way (several endpoints
silently returned a wrong-but-plausible value — like an expiry date that
fell back to "today" — rather than erroring, so each was checked against a
live response, not assumed).

**Not yet tested**: actually placing a real (paper) option order.

## Files

- `bot.py` — multi-account orchestrator (was `DayTradingBot/bot.py`, restructured for N accounts)
- `webull.py` — Webull REST client (new; replaces `DayTradingBot/alpaca.py`)
- `signals.py` — signal math, copied verbatim from `DayTradingBot/signals.py` (one import line changed: `alpaca` → `webull`)
- `symbols.py` — copied verbatim
- `position_manager.py` — stop/trail/scale-out logic, copied from `DayTradingBot/position_manager.py`, adapted so every state/log file is per-account
- `config.py` — same trading-rule defaults as `DayTradingBot/config.py`, plus multi-account credential parsing
