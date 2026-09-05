# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Memory

@.memory/project_trading_server.md
@.memory/project_bots_overview.md
@.memory/project_known_issues.md
@.memory/project_safeguard.md
@.memory/project_landing_page.md

## Deployment

All bots run on Ubuntu server `192.168.1.250` (user: `claude`). The `Z:\work\bots` directory is the Windows source — it is mounted on the server at `/mnt/asus/work/bots` and symlinked as `~/bots`.

**IMPORTANT — `~/bots` is NOT the live path.** Crontab actually runs bots out of `~/bots-live`, a separate local copy on the server's disk. The `cd /home/claude/bots/...` cron lines are commented out; the active entries call wrapper scripts (`~/cryptobot.sh`, `~/wheelbot.sh`, etc.) that `cd` into `~/bots-live/...` instead. There is no known automatic sync job between `~/bots` and `~/bots-live` — edits made on Windows (`Z:\work\bots`) do **not** automatically reach production. Verify with `ssh claude@192.168.1.250 "diff -rq ~/bots/BotName ~/bots-live/BotName"` before assuming a code change is live, and copy changes over manually (or ask the user how they sync it) if they differ.

**IMPORTANT — on this Windows machine, `D:\Work\bots` (git-tracked, remote `github.com/msohaib47/bots.git`) and `Z:\work\bots` (the CIFS mount that actually reaches the server) are two different directories with nearly-identical contents but no automatic sync between them either** (discovered 2026-07-06). `D:\Work\bots` is the canonical/edit-here copy going forward. Any change made in `D:\Work\bots` needs to be manually copied to `Z:\work\bots` before it can reach `~/bots` on the server (and from there to `~/bots-live`, per above). **Also note:** a file whose name is *only* an extension (e.g. plain `.env`) does not reliably get created through the `Z:` SMB mount from PowerShell (`Copy-Item`/`Out-File` silently fail) — every existing bot's `.env` on the server was created directly via SSH, not copied from Windows. Create/edit `.env` files over SSH, not through `Z:`.

**Python runtime:** pyenv Python 3.12.13 at `/home/claude/.pyenv/versions/3.12.13/bin/python3`

**Scheduler:** All bots run via crontab as user `claude`. To view/edit:
```bash
ssh claude@192.168.1.250 "crontab -l"
```

**IMPORTANT — crontab times are in the server's local time zone (America/Chicago, CDT/CST), not UTC**, even though the schedule table below is labeled "(UTC)" — e.g. DayTradingBot's actual entry is `* 8-15 * * 1-5` (Central), which is market hours (9:30am–4pm ET), not the `13-19 UTC` the table implies (discovered 2026-07-06 while adding VerticalSpreadBot's cron entry). Central and Eastern shift together across DST, so this mapping holds year-round — just don't assume the numbers in crontab are UTC.

**Run a bot manually (single pass):**
```bash
ssh claude@192.168.1.250 "cd ~/bots/WheelBot && PYTHONPYCACHEPREFIX=/home/claude/.pycache python3 bot.py --once"
ssh claude@192.168.1.250 "cd ~/bots/DayTradingBot && PYTHONPYCACHEPREFIX=/home/claude/.pycache python3 bot.py --status"
```

**Install dependencies on server:**
```bash
ssh claude@192.168.1.250 "cd ~/bots/WheelBot && pip3.12 install -r requirements.txt"
```

**Regenerate dashboard:**
```bash
ssh claude@192.168.1.250 "cd ~/bots && PYTHONPYCACHEPREFIX=/home/claude/.pycache python3 dashboard.py"
```

**PYTHONPYCACHEPREFIX is required** when running any bot manually — the CIFS mount doesn't support Python's atomic `.pyc` rename. It's already set in the crontab environment; add it to any manual SSH invocations. The pycache dir is `/home/claude/.pycache`.

## Architecture

Each bot is a self-contained directory with a consistent structure:

```
BotName/
  bot.py          — entry point; runs once per cron tick
  config.py       — loads .env, exposes all constants
  alpaca.py       — Alpaca REST wrapper (or robinhood.py for RH bot)
  signals.py      — indicator logic (where applicable)
  position_manager.py — open/close/stop logic + CSV trade log
  notifier.py     — symlink or copy of shared notifier
  .env            — secrets (not committed)
  positions.json  — live state (in-process persistence)
  trades.csv      — append-only trade log
  logs/           — bot's own FileHandler log
```

Top-level `notifier.py` is the shared push notification module (ntfy.sh topic `sohaib-trading-2026`). Each bot imports it locally as `from notifier import notify`.

**Log files live in two places:**
- `~/bots/BotName/logs/` — written by the bot's Python `logging.FileHandler`
- `~/bots/logs/` — written by crontab stdout/stderr redirect (`>> ~/bots/logs/botname.log 2>&1`)

The `dashboard.py` script reads from `~/bots/logs/` (crontab output).

## Node.js Bots

SchwabStopLossBot is built in Node.js (requires Node 18+). Its structure differs slightly from Python bots:

```
SchwabStopLossBot/
  bot.js             — main bot class
  scheduler.js       — cron scheduler
  schwab.js          — Schwab API wrapper
  position-manager.js — stop-loss logic
  config.js          — loads .env, exposes constants
  notifier.js        — ntfy.sh notifications
  utils.js           — helpers (market hours, formatting)
  logger.js          — Winston logging
  package.json       — npm dependencies
  guard_state.json   — position state (in-process)
  logs/              — bot's FileHandler log
  .env               — secrets (not committed)
```

Install dependencies: `npm install` (one-time in bot directory).
Run a bot manually: `node bot.js --once` or `node bot.js --status`.

## The Seven Bots

### WheelBot
Options wheel strategy (CSP → assignment → covered call → repeat) on QBTS, RIOT, CIFR, CLSK.

- **State machine** in `state.py`: `IDLE → CSP_PENDING → CSP_OPEN → ASSIGNED → CC_PENDING → CC_OPEN → (repeat)`
- State is persisted in `wheel_state.json` keyed by ticker
- `strategy.py` contains one function per state transition; `bot.py` calls `strategy.run_ticker()` in a loop
- Option selection: puts at 7% OTM (`CSP_OTM_PCT`), calls at 7% above cost basis (`CC_OTM_PCT`), DTE 14–35 days
- `--once` mode runs one pass then prints status; `--reset TICKER` resets a ticker to IDLE

### DayTradingBot
0DTE options on SPY, QQQ, IWM. Runs every minute via cron.

- `signals.py` generates CALL/PUT/NONE using VWAP + EMA9/21 crossover + RSI on 5-min bars
- `position_manager.py` tracks open contracts with stop-loss (30%) and trailing stop (activates at +50%, trails 10% below high)
- `alpaca.py` has `find_atm_contract()` to select the at-the-money 0DTE contract
- Force-close all positions at `FORCE_CLOSE_TIME` (15:50 ET); no new entries after `NO_NEW_ENTRY_TIME` (15:45 ET)
- PDT protection: writes a flag file if PDT triggers; clears at midnight

### VerticalSpreadBot
0DTE debit vertical spreads on SPY, QQQ. Runs every minute during market hours. Shares its Alpaca paper account/API key with DayTradingBot (same underlyings — the two bots compete for the same account's buying power).

- Same VWAP + EMA9/21 crossover + RSI signal as DayTradingBot (`signals.py` is a near-duplicate); CALL signal opens a bull call spread, PUT signal opens a bear put spread
- `alpaca.py` `find_vertical_spread()` picks the nearest ITM/OTM strike as the long leg and offsets by `SPREAD_WIDTH` (default $2) for the short leg, within `MAX_DTE` (default 1) days
- Orders use Alpaca's multi-leg (`order_class: "mleg"`) endpoint — both legs fill as one atomic order. Requires the account to have options Level 3 (multi-leg) approval; verified working against this paper account 2026-07-06
- Alpaca rejects new positions in contracts "expiring soon" (error code `42210000`) — this fires for same-day 0DTE contracts once close is near, independent of and in addition to the bot's own `NO_NEW_ENTRY_TIME`/`FORCE_CLOSE_TIME` cutoffs
- Each spread is tracked as one unit (both leg symbols) in `positions.json`; profit target closes at 50% of max possible profit (`width − debit`), stop-loss closes at 50% of debit paid
- SPX is not supported — Alpaca's options API only covers equity/ETF options, not cash-settled index options

### CryptoBot
BTC/USD momentum on Alpaca. Runs every 5 minutes 24/7.

- Signal: EMA9/EMA21 golden/death cross on **4-hour bars** (`get_4h_bars`) + RSI14 filter (buy if RSI < 65 AND 1h RSI < 50, sell if RSI > 72 or death cross); fractional notional orders
- No local state file — position is queried live from Alpaca each run
- Buys only fire on an actual golden cross (EMA9 crossing above EMA21) — in a sustained downtrend the bot will correctly sit out indefinitely with no BUY signal; check `logs/cryptobot.log` for `EMA9`/`EMA21` history before assuming a bug

### CopyTradingBot
Mirrors congressional trades (default: Markwayne Mullin) from QuiverQuant API.

- `scraper.py` fetches trades; `tracker.py` manages `trades_log.csv` as the deduplication ledger
- Trade IDs are `{representative}_{date}_{ticker}_{transaction}` — used to prevent re-execution
- Only executes stock types (`ST`, `stock`, `''`, case-insensitive as of 2026-07-06 — QuiverQuant returns `"Stock"` capitalized, which a case-sensitive check had been silently skipping as "unsupported" since inception); options/other types are logged as skipped
- **QuiverQuant auth is currently broken/incomplete:** `.env` has no `QUIVER_API_KEY`, and `scraper.py` sends no `Authorization` header at all — every fetch has 401'd since 2026-05-29. Needs a real API key added to `.env` plus a header added to `scraper.py` before this bot can find new trades again
- `trader.py` wraps Alpaca market orders sized by trade amount range (midpoint of reported range)

### RobinhoodDayTradingBot
Equity day trading on Robinhood (account 5UN85130). Same VWAP+EMA+RSI strategy as DayTradingBot.

- **DRY_RUN defaults to `true`** — must set `DRY_RUN=false` in `.env` to place real orders
- Auth uses a saved pickle session (`~/.tokens/robinhood.pickle`). Run `login.py` interactively once to set up:
  ```bash
  ssh -t claude@192.168.1.250 "cd ~/bots/RobinhoodDayTradingBot && python3 login.py"
  ```
- Stop-loss 2%, profit target 4% (vs 30%/50% for the Alpaca options bot)
- `robinhood.py` wraps `robin_stocks` 2.1.0; the standard API only reaches the margin account — the agentic cash account is MCP-only

### SchwabStopLossBot (Node.js)
Automated stop-loss manager for Schwab option positions. Monitors open options and maintains dynamic trailing stops with tiered margin adjustments.

- **Language:** Node.js (18+)
- **API:** Schwab REST API (OAuth token required)
- **Schedule:** Every minute Mon-Fri, 9:30 AM - 4:00 PM ET
- **Features:**
  - Options-only (ignores stock positions)
  - Dynamic trailing stops with price-based tier adjustments
  - Tier 1 (< $100): $10 or 25% margin
  - Tier 2 ($100-$200): $15 margin
  - Tier 3 (> $200): $25 margin
- **State:** Persisted in `guard_state.json` (tracks highest prices, existing stop orders)
- **DRY_RUN default:** `true` — must set `DRY_RUN=false` in `.env` to place real orders
- **Setup:**
  ```bash
  cd ~/bots/SchwabStopLossBot
  npm install
  cp .env.example .env
  # Edit .env with SCHWAB_API_KEY, SCHWAB_API_SECRET, SCHWAB_ACCESS_TOKEN, SCHWAB_ACCOUNT_NUMBER
  npm run dry-run  # Test first
  npm run status   # View current positions
  ```
- **Crontab:** `* 13-21 * * 1-5 cd ~/bots/SchwabStopLossBot && node bot.js --once >> ~/bots/logs/schwab-bot.log 2>&1`
- **See:** [SchwabStopLossBot/README.md](./SchwabStopLossBot/README.md) and [QUICKSTART.md](./SchwabStopLossBot/QUICKSTART.md)

## Environment / Secrets

Each bot's `.env` (not committed) contains:
- `ALPACA_API_KEY`, `ALPACA_SECRET_KEY` — Alpaca paper trading credentials
- `ALPACA_BASE_URL` — defaults to `https://paper-api.alpaca.markets`
- Bot-specific overrides (tickers, position sizing, etc.)

RobinhoodDayTradingBot `.env` additionally has `RH_USERNAME`, `RH_PASSWORD`, `DRY_RUN`.

CopyTradingBot `.env` has `QUIVER_API_KEY` / `QUIVER_API_URL`.

SchwabStopLossBot `.env` has `SCHWAB_API_KEY`, `SCHWAB_API_SECRET`, `SCHWAB_ACCESS_TOKEN`, `SCHWAB_ACCOUNT_NUMBER`, plus `DRY_RUN`, `LOG_LEVEL`, `STOPLOSS_INITIAL_MARGIN_PCT`, `STOPLOSS_INITIAL_MARGIN_DOLLAR`.

## Dashboard

`dashboard.py` at the bots root reads log files, `positions.json`, and CSV trade logs to generate `dashboard.html`. It uses no bot modules and has no external dependencies beyond stdlib. Output path is controlled by `DASHBOARD_OUT` env var (defaults to `./dashboard.html`).
