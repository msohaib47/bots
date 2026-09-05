---
name: bots-overview
description: All trading bots — what they trade, cron schedule, accounts, and key files
metadata:
  type: project
---

Canonical source: `D:\Work\bots\` (Windows, git-tracked). Mirror manually to `Z:\work\bots\` (CIFS mount) = `~/bots/` (server) = manually copy again to `~/bots-live/` (actual cron execution path). See [[trading-server]] for the full D:/Z:/bots/bots-live chain — there is no automatic sync at any of those three hops. Each bot is self-contained.

**Shared notifier:** `common/notifier.py` — pushes to ntfy.sh topic `sohaib-trading-2026`.
Import as `from common.notifier import notify` (never use local bot copies).
`PYTHONPATH=/home/claude/bots-live` is set inside each per-bot wrapper script (not globally in crontab) so `common` is always findable — this is `bots-live`, not `bots`, matching where the code actually executes.
No per-bot copies of `notifier.py` — the `common/` package is the single source of truth.

**Cron shell scripts** live in `~/` (home dir, not in bots folder) — one per bot:
`~/wheelbot.sh`, `~/daytradingbot.sh`, `~/cryptobot.sh`, `~/copytradingbot.sh`,
`~/robinhoodbot.sh`, `~/robinhoodsafeguardbot.sh`, `~/daytradingpositionsync.sh`,
`~/schwabbot.sh`, `~/testbots.sh`, `~/verticalspreadbot.sh`
Each wraps the bot's command + log redirect, `cd`s into `~/bots-live/BotName`. All have `#!/bin/bash` and are `chmod +x`.

**Accounts:**
- Alpaca paper trading: WheelBot, DayTradingBot, VerticalSpreadBot share one account, account number `PA31AA4WRNGQ` (moved here 2026-08-10 — key `PKSPIRM2AC2JL4XNX4GB25AVVP` in each bot's `.env`). CryptoBot and CopyTradingBot are on a separate, still-shared account.
  - **Why moved:** the prior shared account (`PA38W04KLU9O`) had only ~$3,900 equity, and WheelBot's open cash-secured-put collateral alone consumed nearly all of it (`initial_margin` ~$3,783 of ~$3,919 equity), leaving DayTradingBot/VerticalSpreadBot with as little as ~$137 in `options_buying_power` — most 0DTE order attempts 403'd with "insufficient options buying power" even once signals were generating correctly. This surfaced 2026-08-10 right after fixing the HTF-bars bug below, once DayTradingBot started actually trying to place orders again.
  - All three still draw from **one shared pool** on the new account — same three-way competition risk as before, just with more headroom (~$9,300 cash / ~$7,900 options buying power at time of migration). If buying-power 403s return, check whether it's genuinely thin margin (compare `initial_margin` vs `equity`) before assuming a bug — see [[project_known_issues]].
- Robinhood margin account `5UN85130`: accessible via standard robin_stocks API
- Robinhood cash/agentic account `706672094`: MCP-only, not usable in cron bots
- Robinhood session: `~/.tokens/robinhood.pickle` — shared by all RH bots. Refresh with:
  `ssh -t claude@192.168.1.250 "cd ~/bots/RobinhoodDayTradingBot && python3 login.py"` (~every 7 days)

**Schedule column below is server-local time (America/Chicago, CDT/CST), not UTC** — confirmed by reading the actual crontab (discovered 2026-07-06). Central and Eastern move together across DST so this stays valid year-round, but don't convert these numbers as if they were UTC.

**IMPORTANT — this status table drifts; verify with `crontab -l` before trusting it.** As of 2026-07-06 all three of RobinhoodDayTradingBot, RobinhoodSafeGuard, and SchwabStopLossBot were documented disabled — but by 2026-08-10 both RobinhoodSafeGuard and SchwabStopLossBot had been re-enabled in crontab (`*/5 8-15 * * 1-5 ~/robinhoodsafeguardbot.sh` and `* 8-15 * * 1-5 ~/schwabbot.sh`, both active, uncommented) without this memory being updated. On 2026-08-10, user asked to stop the Robinhood bots — RobinhoodDayTradingBot was already off; RobinhoodSafeGuard's cron line was commented back out (both now disabled). **SchwabStopLossBot was left active** (user only said "robinhood bots"). Table below reflects state as of 2026-08-10 — re-verify with `crontab -l` for anything time-sensitive.

| Bot | Strategy | Schedule (local/Central) | Log | Status |
|-----|----------|---------------------------|-----|--------|
| WheelBot | Options wheel (CSP→CC) on QBTS/RIOT/CIFR/CLSK | `*/5 8-15 * * 1-5` | `logs/wheelbot.log` | active |
| DayTradingBot | 0DTE SPY/QQQ/IWM options, VWAP+EMA+RSI | `* 8-15 * * 1-5` | `logs/daytrading.log` | active |
| VerticalSpreadBot | 0DTE SPY/QQQ debit vertical spreads, VWAP+EMA+RSI | `* 8-15 * * 1-5` | `logs/verticalspread.log` | active |
| CryptoBot | BTC/USD EMA9/21 crossover + RSI, 24/7 | `*/5 * * * *` | `logs/cryptobot.log` | active |
| CopyTradingBot | Mirrors congressional trades (Mullin) | `*/5 8-15 * * 1-5` | `logs/copybot.log` | active (but QuiverQuant fetch 401ing since 2026-05-29, see [[project_known_issues]]) |
| DayTradingPositionSync | Syncs DayTradingBot positions | `*/30 * * * *` | n/a | active |
| RobinhoodDayTradingBot | Equity day trading on Robinhood, DRY_RUN=true default | — | `logs/rhbot.log` | **disabled** (commented out) |
| RobinhoodSafeGuard | Trailing stop-loss on RH option positions | — | `logs/safeguard.log` | **disabled 2026-08-10** (was active since some point after 2026-07-06; user asked to stop Robinhood bots) |
| SchwabStopLossBot | Trailing stop-loss on Schwab option positions (Node.js) | `* 8-15 * * 1-5` | `logs/schwab.log` | **active** (re-enabled at some point after 2026-07-06; not touched 2026-08-10) |

There is also an `ebaybot.sh` cron entry (`0 7 * * *`) unrelated to trading — corresponds to the `ebayCompletedListings/` folder, not covered by this memory set. As of 2026-08-03 it now runs from `~/bots-live/ebayCompletedListings` like the other bots (previously it was the one exception running straight off the CIFS mount at `/mnt/asus/work/bots/ebayCompletedListings` — that turned out to be load-bearing-broken for it, see [[project_known_issues]]). It uses its own dedicated venv (`/home/claude/venvs/ebaybot`, with Playwright + Chromium installed), not the shared pyenv 3.12 + `PYTHONPATH=/home/claude/bots-live` pattern the trading bots use, and it has no dependency on `common/`. Logs now to `~/bots-live/logs/ebaybot.log` (previously `~/bots/logs/`).

**Daily cron jobs:**
- `45 7 * * *` — fix CRLF on all `.py`/`.sh` files under `~/bots` (Windows edits corrupt them)
- `45 7 * * *` — same CRLF fix for `~/bots-live`
- `0 8 * * *` — health check all bots (`test_bots.sh all --status`) + ntfy notification

**How to apply:** When adding a new bot: build/test it in `D:\Work\bots\BotName`, copy to `Z:\work\bots\BotName` (create `.env` via SSH directly, not through `Z:` — see [[trading-server]]), copy `~/bots/BotName` → `~/bots-live/BotName` on the server, `pip install` requirements there, add a `~/botname.sh` wrapper script matching the pattern above, then add its crontab line in server-local (Central) time.
