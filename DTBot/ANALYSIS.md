# Current-state analysis: DayTradingBot, DT-Bot-200, DT-Webull

Audit performed 2026-09-12 to support consolidating the three v1 day-trading bot
directories (four accounts total) into one codebase. See `PLAN.md` in this
folder for the proposed design; this file is the evidence for it.

## The four accounts today

| # | Directory | Account | Broker | Size (2026-09-12) | Real money? |
|---|---|---|---|---|---|
| 1 | `DayTradingBot/` | "10k-account" | Alpaca (paper) | ~$9,939 | No |
| 2 | `DT-Bot-200/` | "Start-at-200" | Alpaca (paper) | ~$205 | No |
| 3 | `DT-Webull/` (`main`) | Webull sandbox | Webull | ~$1,000,000 (sandbox play money) | No |
| 4 | `DT-Webull/` (`live`) | Webull real account | Webull | ~$200 | **Yes** |

All four run the byte-identical strategy (`signals.py`: VWAP + EMA9/21 crossover
+ RSI + 15-min HTF trend filter + ADX/ATR entry-quality filters) on the
byte-identical symbol list (META, GOOG, QQQ, MSFT, TSLA — see
`DAYTRADING_RULES.md`).

## Finding 1: DayTradingBot and DT-Bot-200 are already ~100% duplicated code

`diff DayTradingBot/<file> DT-Bot-200/<file>` (after stripping CRLF, since
Windows edits leave line-ending-only diffs):

| File | Diff |
|---|---|
| `bot.py` | **byte-identical** |
| `config.py` | **byte-identical** |
| `position_manager.py` | **byte-identical** |
| `signals.py` | identical (not separately re-checked this pass, same as prior sessions) |
| `symbols.py` | identical |
| `sync_positions.py` | **byte-identical** |
| `alpaca.py` | differs only in in-code comments/docstrings — see Finding 2 |
| `backtest.py` | genuinely diverged — see Finding 3 |
| `.env` | differs only in `ALPACA_API_KEY`/`ALPACA_SECRET_KEY` (separate paper accounts) — every threshold value (`MAX_CONTRACTS_PER_SYMBOL`, `STOP_LOSS_PCT`, etc.) is currently set to the *same* number in both files |

**Conclusion:** these two directories are not really two codebases — they are
one codebase, copy-pasted, differing only in which Alpaca API key gets loaded.
This is the strongest possible argument for consolidation: there is currently
*zero* intentional behavioral difference between accounts 1 and 2, only
accidental drift from copy-paste (see Finding 2).

## Finding 2: copy-paste drift already caused two live bugs (found + fixed this session, 2026-09-12)

Because `DT-Bot-200/alpaca.py` was copied from `DayTradingBot/alpaca.py` at some
point in the past and never re-synced, it missed a bug fix applied to the
original:

1. **Stale-bars bug reintroduced.** The 2026-09-08 `sort=desc` fix for
   `get_recent_bars()` (see `.memory/project_known_issues.md`, "signals frozen
   at market-open snapshot all session") was applied to `DayTradingBot/alpaca.py`,
   `VerticalSpreadBot/alpaca.py`, and `DayTradingBotV2/...` — but **not**
   `DT-Bot-200/alpaca.py`. DT-Bot-200 was silently running on stale,
   near-market-open indicator data for its entire signal pipeline since
   2026-08-10, for over a month, undetected. **Fixed live 2026-09-12** as part
   of this audit (same `sort=desc` + reversal fix, verified via `py_compile`
   and a clean module import on the server).

2. **All DT-Bot-200 notifications mislabeled "DayTradingBot".** `bot.py`,
   `position_manager.py`, and `sync_positions.py` all call
   `notify(..., bot='DayTradingBot')` — hardcoded from the original file, never
   updated after the copy. Every BUY/SELL/STOP_LOSS/ERROR alert this bot has
   ever sent to ntfy.sh was labeled as coming from DayTradingBot instead of
   DT-Bot-200, with no way for the user to tell which bot actually fired
   without checking dollar amounts. **Fixed live 2026-09-12** (5 call sites
   across 3 files, `bot='DayTradingBot'` → `bot='DT-Bot-200'`).

**This is exactly the failure mode the requested consolidation eliminates**:
with one shared codebase, a bug fix or the bot-name-in-notify parameter is
either impossible to fix in only one place, or is data (an account's `name`
field), not a hardcoded string duplicated N times.

## Finding 3: the backtest engines have also diverged

`DayTradingBot/backtest.py` (1,363 lines) has grown a materially more
sophisticated sequential-cash-simulation engine (`_scan_symbol()` generator +
`_simulate_sequential()`, added to correctly model one shared, depleting cash
balance across concurrent positions) that `DT-Bot-200/backtest.py` (1,113
lines) never received — DT-Bot-200 is still on the older `backtest_symbol()`
list-returning approach. Anyone re-running a backtest sweep against DT-Bot-200
today gets answers from a strictly older, less accurate engine than
DayTradingBot's. `DT-Webull` has no backtest engine of its own at all (never
had one — its `.env`/config were validated by porting DayTradingBot's backtest
results, not by an independent DT-Webull-specific backtest).

## Finding 4: DT-Webull already solved "multi-account," and its shape is the right template

DT-Webull is structurally different from DayTradingBot/DT-Bot-200 in exactly
one respect, and it's the one that matters for this consolidation:

- **DayTradingBot / DT-Bot-200** — one account per process. Credentials load
  once into **module-level globals** at import time
  (`common/alpaca_config.py`'s `API_KEY`/`API_SECRET`/`BASE_URL`, read via
  `os.getenv()` + `load_dotenv()` at import), and every function in `bot.py`/
  `position_manager.py`/`alpaca.py` implicitly operates on "the" account —
  there is no parameter for *which* account, because there is only ever one
  per process.
- **DT-Webull** — already runs 2 accounts (`main`, `live`) from one process.
  It solves this by threading an explicit **`paths: dict`** (per-account file
  paths: `state_file`, `cooldown_file`, `trades_log`, `snapshot_file`, etc.)
  and an explicit **`client: WebullClient`** instance through every function
  signature, plus an `account_name: str` parameter anywhere a log line or
  notification needs to say which account fired. `config.py`'s
  `_load_accounts()` builds the `ACCOUNTS: dict[str, dict]` registry from
  `WEBULL_ACCOUNTS=main,live` + `WEBULL_<NAME>_*` prefixed env vars.
  `bot.py`'s `run()` loops `for name, acct_cfg in ACCOUNTS.items(): run_account(name, acct_cfg, ...)`.

This is precisely the shape a consolidated 4-account (soon more) bot needs.
**DT-Webull's pattern is the one to generalize, not DayTradingBot's** — it
already proves out "one codebase, N accounts, one config-driven registry"; it
just needs the account-registry and the broker client to both be
broker-agnostic instead of hardcoded to Webull.

## Finding 5: the broker-client method surfaces are close but not identical — a real adapter is needed, not a thin rename

| Capability | Alpaca (`DayTradingBot/alpaca.py` + `common/alpaca_client.py`) | Webull (`DT-Webull/webull.py`) |
|---|---|---|
| Account info | `get_account()` → `{cash, portfolio_value, buying_power, options_buying_power, ...}` | `get_account()` → `{total_cash_balance, total_net_liquidation_value, total_market_value, ...}` — different field names |
| Bars | Returns numeric OHLCV, chronological once `sort=desc`+reverse is applied; native `start`/date-range param | Returns OHLCV as **JSON strings**, needs explicit `float()` casts; newest-first by default, needs reversal; **no server-side date-range param at all** — session-only bars are done by over-fetching and filtering client-side to today's ET date |
| Find ATM contract | `find_atm_contract(symbol, opt_type, spot)` → dict with `symbol/strike/bid/ask/mid/type/underlying/expiry` | `find_atm_contract(symbol, opt_type, spot)` → same shape, different underlying REST calls |
| Buy | `buy_option(contract, qty)` → order dict or `None`; always a limit order at mid+0.01 | Equivalent, but Webull options have **no MARKET sell order type at all** — every close must carry an explicit limit price |
| Close | `close_option_position(symbol: str, qty=None)` — plain OCC symbol string, `qty=None` means "close everything" via `DELETE /v2/positions/{symbol}` | `close_option_position(contract: dict, qty, limit_price)` — needs the **full contract dict**, not just a symbol, and `limit_price` is **mandatory** (no default full-close-by-symbol shortcut) |
| Rate limits | Not hit in practice on the current call volume | Webull's **sandbox** API 429s heavily at ~24 calls/min/symbol (see `.memory/project_known_issues.md`, DT-Webull section) — still an open, unresolved issue, relevant to any consolidated design's call budget |

**Conclusion:** a consolidated `BrokerClient` interface cannot be a pure
pass-through — it has to normalize account-field names, bar numeric
types/ordering/date-filtering, and the close-position call shape (symbol vs.
full contract, optional vs. mandatory limit price) once, in one adapter per
broker, instead of leaving each bot to reimplement (and occasionally forget
to fix) its own version of the same normalization. See `PLAN.md` for the
proposed interface.

## Finding 6: today's per-account customization need is smaller than it used to be

Two things landed in this same working session that materially shrink the
amount of genuine per-account config a consolidated bot will need:

1. **`contracts_cap_for_balance()`** (added 2026-09-12, all three bots) already
   auto-scales position size off the account's live net-liq (`<$500`→1,
   `$500-999`→2, `$1,000-1,999`→4, `≥$2,000`→10) every tick. This was the main
   reason `MAX_CONTRACTS_PER_SYMBOL` used to need manual per-account tuning
   (DT-Bot-200 at $200 vs. DayTradingBot at $10k) — that tuning is now
   automatic and derived from a value every broker adapter already exposes
   (net-liq/portfolio-value), not something that needs a broker-specific
   config path.
2. **`account_snapshot.json`** (also added 2026-09-12, all three bots/accounts)
   already gives every account a broker-agnostic `{cash,
   net_liquidation_value, updated_at}` file the merged dashboard reads —
   proof that a small, uniform, broker-independent snapshot shape is
   sufficient for cross-account reporting today, which is a good sign for
   designing the same shape into the consolidated `BrokerClient.get_account()`
   return value.

## What's genuinely account-specific (the real config surface)

Having ruled out most of what looked like per-account difference as either
accidental drift (Finding 2) or now-automatic (Finding 6), what's left that a
consolidated design actually needs to parameterize per account:

- Broker + credentials (API key/secret, account ID, base URL — sandbox vs.
  production endpoint)
- File paths / state namespace (so 4 accounts' `positions.json` etc. never collide)
- Whether it's real money (`real: bool` — already exists as a dashboard-only
  flag today; a consolidated design should also gate anything
  extra-cautious, e.g. always logging a louder warning banner, off this flag)
- Symbol list, in principle — currently identical across all 4 accounts, but
  there's no reason a future account couldn't trade a different list
- Any of the numeric thresholds in `config.py` — currently identical across
  accounts in practice (see Finding 1's `.env` table), but should remain
  overridable per-account since nothing guarantees they'll always be identical
