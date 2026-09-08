# DT-Stocks

Stock-trading variant of `DayTradingBot` — same signal (15m EMA21 trend +
5m VWAP/EMA9/RSI/ADX, see `signals.py`, copied verbatim), same
bidirectional CALL/PUT design, but trades **shares of the underlying
directly** (long on CALL, short on PUT) instead of buying 0DTE option
contracts. Copied/adapted, not shared/imported, per this repo's per-bot
self-containment convention.

Uses the Alpaca **"10k account"** — a separate paper account from the one
`DayTradingBot`/`VerticalSpreadBot`/`WheelBot` share, so there's no
buying-power competition with those bots.

## Why the stop/trail percentages differ from DayTradingBot's

`DayTradingBot/config.py` defaults to `STOP_LOSS_PCT=30%`/
`PROFIT_TRAIL_TRIGGER=30%` — tuned for **option premium** moves, a
leveraged instrument where a sub-1% underlying move can swing the premium
30%+. Applied directly to a stock's own price, a 30% stop would almost
never fire (stocks rarely move 30% intraday), leaving positions essentially
unprotected.

`DT-Stocks/config.py` now defaults to **1.5% stop / 3% trail trigger / 1.5%
wiggle**, tuned 2026-09-08 via a backtest sweep (Jan 1 – Sep 7 2026, 8
symbols): this combination beat every other tried on both win rate (52.2%)
and total P&L (+$1,885 vs. the original untested 1%/2%/1% guess's +$1,235).
See `.env`'s comment for the full sweep table. Most trades still exit at
EOD rather than stop/trail either way, so this isn't "solved" — just the
best of what was tried; `backtest.py` is there to test further variants.

## Long AND short

Unlike a long-only equity bot, this mirrors DayTradingBot's actual design:
a CALL signal opens a long (buy), a PUT signal opens a short (sell-to-open).
Alpaca's paper accounts support shorting marginable stocks by default — no
extra setup needed for paper trading. `position_manager.py`'s stop/trail
logic is side-aware (a short's stop trails **up** as price falls, opposite
of a long's).

## Sizing is dollar-based, not contract-count-based

There's no `MAX_CONTRACTS`-equivalent small integer cap — a stock trade's
quantity is shares, so sizing uses `MAX_POSITION_VALUE` (a $ cap per single
entry) and `CASH_PER_TRADE_PCT`, further capped by `MAX_OPEN_EXPOSURE`
across all open positions (same "exposure cap, not a P&L circuit breaker"
design as every other bot in this repo).

## Files

- `bot.py` — main entry (`--status`/`--close`/plain run), same overall flow as `DayTradingBot/bot.py`
- `alpaca.py` — equities-only Alpaca wrapper (bars reused verbatim; `buy_stock`/`close_stock_position` replace `buy_option`/`close_option_position`)
- `signals.py` — copied verbatim from `DayTradingBot/signals.py`, unchanged
- `symbols.py` — copied verbatim
- `position_manager.py` — adapted for share-based long/short positions (no strike/expiration/premium_pct; a `side` field instead)
- `config.py` — stock-appropriate trading-rule defaults, dollar-based sizing

## Backtest

`backtest.py` replays the same signal on the stock's own 1-minute price
path (no option to price, unlike `DayTradingBot/backtest.py` — this is
actually a simpler backtest, not a stripped-down one). Uses the shared
`backtesting-engine/` cache, so re-running over an already-fetched range is
fast (the full Jan–Sep 2026, 8-symbol backtest runs in ~30 seconds once
cached).

```bash
python backtest.py 2026-01-01 2026-09-07          # date range, all symbols
python backtest.py 2026-01-01 2026-09-07 SPY QQQ  # specific symbols
```

## Not yet done

- Not deployed to the server or added to cron — local only so far.
- The stop/trail sweep so far only tried a handful of flat-percentage
  combinations (see `.env`'s comment) — a volatility-scaled stop (e.g. ATR-based,
  since INTC needed 59 stops in the winning config vs. SPY's 2) hasn't been tried.
