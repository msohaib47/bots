# Memory.md

Running notes for the `C:\Work\bots` workspace. This is a workspace-level scratchpad — for the
trading bots' architecture, deployment, and per-bot details, see [CLAUDE.md](./CLAUDE.md) and
`.memory/` (those are the canonical, maintained references). This file exists to capture things
that fall outside that scope: other projects living in this same directory, and dated notes on
decisions/status that don't belong in the static docs.

## What's in this directory

Beyond the six trading bots documented in CLAUDE.md, this workspace also contains:

- **`ebayCompletedListings/`** — eBay sold-listings price scraper. Playwright-based, runs on a
  cron schedule, stores results in SQLite. Entry point `agent.py` (`run`, `add`, `stats`
  subcommands). See its own [README.md](./ebayCompletedListings/README.md).
- **`landing/`** — generates a status landing page (`generate.py`) by scanning listening TCP
  ports on the server and rendering them against `ports.json` metadata, plus basic system stats
  (CPU/memory/disk/uptime). Output goes to `/home/sohaib/sites/landing/index.html`.
- **`DayTradingBot-old/`** — superseded version of DayTradingBot (has its own `alpaca.py`,
  `backtest.py`). Kept around for reference/backtesting; not deployed. Confirm with the user
  before treating it as dead code to delete.
- **`CODE_REVIEW_CONSOLIDATION.md`** — historical code review (dated June 6, 2026) that led to
  the shared `common/notifier.py` module described in CLAUDE.md. Kept as a record of that
  decision; the consolidation it recommended has since been carried out.

## Dated notes

<!-- Add short dated entries below for decisions, status changes, or context worth keeping that
     isn't already covered by CLAUDE.md/.memory. Keep entries terse; prune ones that go stale. -->

- **2026-07-05** — Memory.md created (was an empty stub since Jun 10) to track workspace context
  outside the trading-bot docs.
- **2026-07-06** — DayTradingBot strategy tuning session: dropped MSTR/HIMS/QBTS/RIOT from
  `symbols.py` (backtested net-negative, and had drifted out of sync between here and the
  server); RSI bands widened, then volume + cross-recency filters disabled via new
  `USE_VOLUME_FILTER`/`USE_CROSS_RECENCY_FILTER` env toggles (code stays, just gated off);
  trailing stop changed to 30% trigger / 15% buffer; `backtest.py` rebuilt to pull real
  history from Alpaca's IEX feed instead of yfinance (24mo vs ~60 days). Full details and the
  train/test methodology that drove these calls are in
  [.memory/project_daytradingbot_strategy.md](.memory/project_daytradingbot_strategy.md).
