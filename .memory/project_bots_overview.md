---
name: bots-overview
description: All trading bots — what they trade, cron schedule, accounts, and key files
metadata:
  type: project
---

All bots live in `~/bots-live/` on the server (master). Windows copies at `Z:\work\bots\` and `D:\work\bots\` are kept in sync. Each bot is self-contained.

**Shared notifier:** `~/bots-live/common/notifier.py` — pushes to ntfy.sh topic `sohaib-trading-2026`.
Import as `from common.notifier import notify` (never use local bot copies).
`PYTHONPATH=/home/claude/bots-live` is set in crontab `.sh` scripts so `common` is always findable.
No per-bot copies of `notifier.py` — the `common/` package is the single source of truth.

**Cron shell scripts** live in `~/` (home dir, not in bots folder) — one per bot:
`~/wheelbot.sh`, `~/daytradingbot.sh`, `~/cryptobot.sh`, `~/copytradingbot.sh`,
`~/robinhoodbot.sh`, `~/robinhoodsafeguardbot.sh`, `~/daytradingpositionsync.sh`,
`~/schwabbot.sh`, `~/testbots.sh`
Each wraps the bot's command + log redirect. All have `#!/bin/bash` and are `chmod +x`.

**Accounts:**
- Alpaca paper trading: WheelBot, DayTradingBot, CryptoBot, CopyTradingBot
- Robinhood margin account `5UN85130`: accessible via standard robin_stocks API
- Robinhood cash/agentic account `706672094`: MCP-only, not usable in cron bots
- Robinhood session: `~/.tokens/robinhood.pickle` — shared by all RH bots. Refresh with:
  `ssh -t claude@192.168.1.250 "cd ~/bots/RobinhoodDayTradingBot && python3 login.py"` (~every 7 days)

| Bot | Strategy | Schedule (server local time) | Log |
|-----|----------|---------------|-----|
| WheelBot | Options wheel (CSP→CC) on QBTS/RIOT/CIFR/CLSK | `*/5 13-19 * * 1-5` | `logs/wheelbot.log` |
| DayTradingBot | Up-to-5DTE options, VWAP+EMA+RSI+HTF, 8 symbols | `* 8-15 * * 1-5` | `logs/daytrading.log` |
| CryptoBot | BTC/USD 4H EMA9/21 trend + 1H RSI entry filter, 24/7 | `*/5 * * * *` | `logs/cryptobot.log` |
| CopyTradingBot | Mirrors congressional trades (Mullin) | `*/5 13-19 * * 1-5` | `logs/copybot.log` |
| RobinhoodDayTradingBot | Equity day trading on Robinhood, DRY_RUN=true default | `*/5 13-19 * * 1-5` | `logs/rhbot.log` |
| RobinhoodSafeGuard | Trailing stop-loss on RH option positions | `* 13-20 * * 1-5` | `logs/safeguard.log` |
| SchwabStopLossBot | Trailing stop-loss on Schwab option positions (Node.js) | `* 13-20 * * 1-5` | `logs/schwab.log` |

**DayTradingBot symbols (as of 2026-07-06):** SPY, QQQ, IWM, TSLA, NVDA, AMD, INTC, MU

**DayTradingBot signal filters:** VWAP direction + EMA9/21 cross + RSI 40–70 (CALL) / 30–60
(PUT) + SPY 15-min HTF trend. Volume and cross-recency filters exist in code but are
disabled by default (`USE_VOLUME_FILTER`/`USE_CROSS_RECENCY_FILTER=false` in `.env`).
Stop-loss 30%, trailing stop activates at +30% gain, trails 15% below high. See
[[project_daytradingbot_strategy]] for the full tuning history and why these values.

**CryptoBot signal logic:** 4H bars for trend (EMA9 > EMA21 = uptrend). BUY when uptrend AND 4H RSI < 65 AND 1H RSI < 50. SELL when downtrend OR 4H RSI > 72. Uses trend-following entry (not crossover-only).

**Daily cron jobs:**
- `45 7 * * *` — fix CRLF on all `.py`/`.sh` files (Windows edits corrupt them)
- `0 8 * * *` — health check all bots (`test_bots.sh all --status`) + ntfy notification

**How to apply:** When adding a new bot, match the cron pattern, log path convention, and notifier copy pattern above.
