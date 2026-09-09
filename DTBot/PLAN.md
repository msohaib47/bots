# Plan: consolidate DayTradingBot / DT-Bot-200 / DT-Webull into `DTBot/`

See `ANALYSIS.md` in this folder for the evidence this plan is built on.
Short version: two of the three bot directories are already ~100% duplicated
code that drifted apart by accident (and already caused two live bugs), and
the third (`DT-Webull`) already solved "one codebase, N accounts" for its own
two accounts — this plan generalizes DT-Webull's shape to cover all four
accounts across both of today's brokers, with a real adapter layer so a fifth
broker is a new file, not a new copy-pasted bot directory.

## Goal

One codebase (`DTBot/`) that runs the identical 0DTE options day-trading
strategy across any number of configured accounts, each pointed at any
supported broker, with:
- Zero copy-pasted per-account code (the Finding-2 bug class becomes
  structurally impossible — there's only one `bot.py` to fix)
- A broker adapter interface clean enough that adding a new broker (Schwab,
  Tradier, IBKR, ...) means writing one new adapter file, not forking the bot
- No behavior change to the trading strategy itself — this is a *packaging*
  refactor, not a strategy change. `signals.py`'s indicator math is not
  touched.

## Scope for this pass

**In scope:** Alpaca (2 accounts: DayTradingBot, DT-Bot-200) and Webull (2
accounts: main, live) — i.e., re-host all 4 existing accounts on the new
structure with identical behavior, verified account-by-account before cutover.

**Explicitly designed for, not built yet:** a third broker. The adapter
interface (below) is shaped so that's a new file implementing the same
interface, not a design change — but no third broker is being added in this
pass; there's nothing to test it against yet.

## Proposed structure

```
DTBot/
  PLAN.md, ANALYSIS.md        -- this plan (kept after migration as the design record)
  accounts.py                  -- ACCOUNTS registry, generalizes DT-Webull's config.py _load_accounts()
  config.py                    -- shared strategy thresholds (defaults; per-account overrides live in accounts.py)
  symbols.py                   -- shared symbol list (unchanged from today)
  signals.py                   -- shared indicator/signal logic (byte-for-byte port, no logic changes)
  position_manager.py          -- shared position/state/P&L logic (byte-for-byte port of DT-Webull's
                                   paths/client-parameterized version -- already the right shape, see Finding 4)
  bot.py                       -- shared runner: `run()` loops every configured account, `run_account()`
                                   drives one tick for one account against its broker adapter
  sync_positions.py            -- shared reconciliation job, one call per account
  brokers/
    __init__.py                 -- BrokerClient Protocol/ABC (the interface every adapter implements)
    alpaca_broker.py             -- wraps common/alpaca_client.py + the options-specific calls
                                     currently duplicated in DayTradingBot/alpaca.py and DT-Bot-200/alpaca.py
    webull_broker.py             -- wraps DT-Webull/webull.py, conforming to the same interface
  dashboard/
    generate.py                  -- successor to daytrading_sandbox_dashboard.py, reading DTBot's
                                     account registry instead of hardcoding 3 bot directories
  state/
    <account_name>/positions.json, cooldowns.json, daily_pnl.json, pnl_history.json,
                    trades.csv, signals.csv, account_snapshot.json, logs/
```

State moves under `state/<account_name>/` (one directory per account) instead
of being scattered at each bot-directory's root — this is a cosmetic
improvement over DT-Webull's current `positions_<name>.json` flat-file-suffix
scheme, not a functional requirement; call it out for confirmation rather than
assuming it during implementation, since it does mean every path reference in
`position_manager.py`/`sync_positions.py` needs updating (mechanical, but
worth a deliberate choice rather than an implicit one).

## The broker adapter interface

Every adapter implements the same surface, normalizing away every quirk
documented in `ANALYSIS.md` Finding 5:

```python
class BrokerClient(Protocol):
    def get_account(self) -> dict:
        """Always returns {'cash': float, 'net_liquidation_value': float}.
        Normalizes Alpaca's cash/portfolio_value and Webull's
        total_cash_balance/total_net_liquidation_value into one shape --
        the same shape account_snapshot.json already uses today (Finding 6)."""

    def list_positions(self) -> list[dict]: ...

    def get_recent_bars(self, symbol: str, timeframe: str, limit: int) -> list[dict]:
        """Always returns numeric OHLCV, chronological (oldest first),
        already filtered to the requested lookback window. The adapter
        owns each broker's own quirk: Alpaca needs sort=desc+reverse
        (Finding 2's exact bug, now impossible to reintroduce by copy-paste
        since there's one Alpaca adapter, not N copies of alpaca.py);
        Webull needs float() casts + reversal + client-side date filtering
        since it has no server-side range param at all."""

    def get_current_option_prices(self, symbols: list[str]) -> dict[str, float]: ...

    def find_atm_contract(self, symbol: str, opt_type: str, spot: float) -> dict | None:
        """Always returns {'symbol','strike','bid','ask','mid','type','underlying','expiry'}."""

    def buy_option(self, contract: dict, qty: int) -> dict | None: ...

    def close_option_position(self, contract: dict, qty: int) -> dict | None:
        """Takes the full contract dict (not just a symbol string) since
        Webull needs it -- Alpaca's adapter just reads contract['symbol']
        and ignores the rest. Every close is a limit order at (current
        mid - small slippage buffer); the adapter is responsible for
        supplying whatever price its broker's API requires (Webull: always
        mandatory; Alpaca: can go to its market-close shortcut instead,
        adapter's choice, interface doesn't care) -- callers never again
        branch on "does this broker support a market close."""

    LAST_ERROR_CODE: int | None  # for PDT detection, same as today's alpaca.py global,
                                  # but now an instance attribute so multiple accounts on
                                  # the same broker don't share one module-level global
```

`bot.py`/`position_manager.py` are written once against this interface and
never import `alpaca_broker`/`webull_broker` directly except in the account
registry's adapter-factory (`accounts.py`'s `build_client(account_cfg)`,
switching on `account_cfg['broker']`).

## Account registry (`accounts.py`)

Generalizes DT-Webull's `_load_accounts()` (Finding 4) with a `broker` field:

```
DTBOT_ACCOUNTS=daytrading,dtbot200,webull_main,webull_live

DAYTRADING_BROKER=alpaca
DAYTRADING_API_KEY=...
DAYTRADING_API_SECRET=...
DAYTRADING_BASE_URL=https://paper-api.alpaca.markets
DAYTRADING_REAL=false

DTBOT200_BROKER=alpaca
DTBOT200_API_KEY=...
...

WEBULL_MAIN_BROKER=webull
WEBULL_MAIN_APP_KEY=...
WEBULL_MAIN_ACCOUNT_ID=...
WEBULL_MAIN_BASE_URL=https://api.sandbox.webull.com
WEBULL_MAIN_REAL=false

WEBULL_LIVE_BROKER=webull
...
WEBULL_LIVE_REAL=true
```

`accounts.py` builds `ACCOUNTS: dict[str, dict]` the same way DT-Webull's
`_load_accounts()` does today, plus a `broker` key the adapter factory reads.
Per-account strategy overrides (symbol list, thresholds) follow the same
`<PREFIX>_<SETTING>` pattern, falling back to `config.py`'s shared defaults —
so an account that never needs a different threshold (true of all 4 today,
per Finding 1/6) needs zero extra lines, and one that does just adds one env
var.

**Credentials location:** one `.env` for all 4 accounts (matching DT-Webull's
existing pattern of one file for 2 accounts) rather than 4 separate `.env`
files. Confirm this is acceptable before implementation — it does mean a
single file holds one real-money credential (`WEBULL_LIVE_*`) alongside three
paper-trading ones, whereas today `DT-Webull/.env` already has exactly that
same mix, so this isn't a new exposure, just consolidating what already
exists in one place instead of three.

## Migration plan (staged, old directories stay as rollback throughout)

Mirrors the staged-cutover approach already used for `DayTradingBotV2` in this
repo (see that plan's Migration section) and for the balance/orphan-position
fixes earlier this session (fix → deploy → verify on the server → confirm
against live broker data before considering it done).

**Stage 0 — build `brokers/` in isolation, verify against real accounts, no live trading.**
Write `BrokerClient` interface + `alpaca_broker.py` + `webull_broker.py`.
For each of the 4 real accounts, call every adapter method (`get_account`,
`list_positions`, `get_recent_bars`, `find_atm_contract`) against the real
broker and diff the result against what today's `DayTradingBot/alpaca.py` /
`DT-Webull/webull.py` return for the same account at the same moment — this
is the same verification style used earlier this session for the
`get_recent_bars` bug (comparing old vs. new output before trusting the fix).
No order placement in this stage.

**Stage 1 — port `signals.py`, `position_manager.py`, `bot.py`, byte-for-byte
where possible.** `position_manager.py` is close to a direct port of
DT-Webull's existing version (already `paths`/`client`-parameterized, per
Finding 4) generalized to accept any `BrokerClient`, not just `WebullClient`.
`bot.py`'s `run()`/`run_account()` shape is likewise a direct generalization
of DT-Webull's existing loop. Run `DTBot/bot.py` manually (not on cron yet)
against one paper account (DT-Bot-200 — lowest stakes) in a scratch state
directory, comparing its signal/entry-decision log line-for-line against the
real `DT-Bot-200/bot.py` running normally on cron, for at least one full
trading day, with **no live orders from the new code** (either a `--dry-run`
flag or literally not registering it in cron yet).

**Stage 2 — cut over one account at a time, lowest risk first.** Order:
DT-Bot-200 (paper, $200) → DayTradingBot (paper, $10k) → DT-Webull main
(sandbox) → DT-Webull live (**real money, last**). For each: disable that
account's old cron entry, add it to `DTBot`'s `DTBOT_ACCOUNTS`, run for a few
full trading days, confirm entries/exits/notifications/dashboard all match
expectations before moving to the next account. Never run the old and new
code against the same account's live state simultaneously (same rule as every
prior migration in this repo — avoids two processes racing on one
`positions.json`).

**Stage 3 — migrate the dashboard.** Point `dashboard/generate.py` at
`DTBot`'s account registry and `state/<name>/` layout instead of
`daytrading_sandbox_dashboard.py`'s hardcoded 3-directory discovery. Same
merged-tabs UI, same nginx vhost, just a different data source — no visual
change for the user.

**Stage 4 — retire (not delete) the old directories.** `DayTradingBot/`,
`DT-Bot-200/`, `DT-Webull/` stay in the repo as reference/rollback (matching
the existing pattern of keeping `DayTradingBot/generate.py` around unused
after the dashboard merge) with their cron entries removed, once all 4
accounts have run cleanly on `DTBot/` for a confirmed stretch (a specific
number of days should be agreed with the user before this stage, not assumed).

## What does NOT change

- The strategy itself (`signals.py`'s indicator math, entry/exit rules,
  cooldown logic) — this is a packaging refactor only.
- `common/notifier.py`, `common/alpaca_client.py` — reused as-is;
  `alpaca_broker.py` wraps `common/alpaca_client.py` rather than replacing it.
- The tiered `contracts_cap_for_balance()` sizing and `account_snapshot.json`
  added earlier this session — both already broker-agnostic in shape (Finding
  6) and port over unchanged.

## Open questions to settle before implementation starts

1. **State layout:** flat `<name>_<file>` suffix (DT-Webull's current scheme)
   vs. `state/<name>/<file>` subdirectories (proposed above) — cosmetic but
   touches every path reference, worth a deliberate answer.
2. **Single `.env` for all 4 accounts** (including the one real-money Webull
   account) vs. keeping credentials split across files — flagged above,
   default recommendation is one file (no new exposure vs. today), but
   confirm.
3. **`backtest.py` consolidation** is out of scope for this plan as written —
   `ANALYSIS.md` Finding 3 documents that it's already diverged, but unifying
   the backtest engine is a separable effort (arguably higher-value on its
   own, since DT-Bot-200's backtest is currently running a strictly worse
   simulation than DayTradingBot's) and should be scoped separately if wanted.
4. **Naming:** is `DTBot/` the intended final name, or a working name for this
   planning pass? Worth confirming before it becomes load-bearing in cron
   wrapper scripts, nginx configs, etc.
5. **Third-broker readiness check:** once Alpaca + Webull adapters exist,
   worth a quick sanity pass asking "could Schwab/Tradier plug into this
   interface without changes" using `SchwabStopLossBot/schwab.js`'s existing
   API surface as a reference point (different language, but same broker,
   useful for spotting interface gaps) — not required for this pass, but
   cheap to check once Stage 0 lands.
