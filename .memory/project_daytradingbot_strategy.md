## DayTradingBot — data source, config, and tuning history (as of 2026-07-06)

**Symbols (live):** SPY, QQQ, IWM, TSLA, NVDA, AMD, INTC, MU (8 symbols — MSTR, HIMS, QBTS,
RIOT were removed 2026-07-06; they were backtested net-negative performers and this list
had drifted out of sync between `C:\Work\bots` and the server before being caught and fixed).

**Live config (`.env`):**
- `STOP_LOSS_PCT=0.30`, `PROFIT_TRAIL_TRIGGER=0.30`, `TRAIL_WIGGLE=0.15` (changed 2026-07-06
  from the old 30%/50%/10% — trailing activates earlier and trails tighter)
- `USE_VOLUME_FILTER=false`, `USE_CROSS_RECENCY_FILTER=false` (added 2026-07-06 — both
  filters' code is still in `signals.py`, just gated off; flip to `true` to re-enable, no
  code changes needed)
- RSI bands in `signals.py`: `RSI_CALL_RANGE=(40,70)`, `RSI_PUT_RANGE=(30,60)` (widened
  2026-07-05 from the original 45-60/40-55)

### Backtest architecture

- `backtest.py` fetches from **Alpaca's IEX feed** (`/v2/stocks/{symbol}/bars`), not
  yfinance — same data vendor the live bot trades against, 24+ months of real 5-min history
  available (vs. yfinance's ~60-day cap). Cached in `cache_alpaca/<SYMBOL>.json`, separate
  from the old `cache/` (yfinance) dir so vendors never mix. `--backtest` months cap raised
  1-12 → 1-24 to match.
- `backtest.py` imports its filter thresholds directly from `signals.py`
  (`RSI_CALL_RANGE`, `RSI_PUT_RANGE`, `EMA_CROSS_LOOKBACK`, `VOL_AVG_PERIOD`, `HTF_SYMBOL`,
  `_recent_cross`) and its stop/trail underlying-equivalents from `config.py`
  (`STOP_LOSS_PCT`, `PROFIT_TRAIL_TRIGGER`, `TRAIL_WIGGLE`, converted via fixed ratios
  0.05/0.04/0.07 — see comment above `_STOP`/`_TRAIL`/`_WIGGLE` in the file). This was fixed
  after being caught drifting out of sync with live logic **twice** in one session — don't
  hardcode filter values directly in `backtest.py` again.
- `BT_USE_VOLUME_FILTER` / `BT_USE_CROSS_RECENCY_FILTER` in `backtest.py` are separate
  module-level copies of the live config flags (mirror live by default, so a plain
  `python backtest.py` run matches production) but can be overridden in an ad-hoc script
  (`import backtest; backtest.BT_USE_VOLUME_FILTER = True`) to explore filter combinations
  without touching `.env`/`config.py`.
- No historical option-premium data is used for P&L — stop/trail/P&L are all simulated on
  the *underlying* price via the ratio conversion above, since real 0DTE option contracts
  only have bar history from their own listing date (a few weeks at most). Alpaca *does*
  provide real historical option bars (`/v1beta1/options/bars`) for currently-listed
  contracts if a true option-premium backtest is ever built — would need per-day OCC symbol
  reconstruction (strike/expiry) since the contracts-listing endpoint only returns
  currently-active contracts, not historical ones.

### Key lesson from the 2026-07 tuning session

A short (~2-3 month) backtest window and a full 12-month window gave **contradictory**
"best" filter/parameter recommendations multiple times (RSI band width, volume filter,
cross-recency filter all flipped sign between windows). A train/test split (train on first
9mo, validate on held-out last 3mo) caught this directly: the trigger/buffer combo that
looked best on train (10%/5%) actually *lost money* on the untouched test data, despite
having the highest win rate of any candidate tested.

**Conclusion drawn from this:** the trailing-stop trigger/buffer parameter is not reliably
tunable from backtesting at this trade volume (~1-2k trades/year) — treat any single-window
"optimal" value with skepticism. The **HTF (SPY 15-min trend) filter is the one finding that
held up consistently** across every window tested (short/full-year/train/test) — removing it
always hurt P&L significantly. The volume and cross-recency filters showed weak-to-negative
support on the full year and out-of-sample test, which is why they were disabled 2026-07-06
rather than further tuned.

**How to apply:** don't re-litigate the trailing-stop value from a single backtest run again
— if retuning, use a train/test split, not a single window. Trust the HTF filter. Treat
`USE_VOLUME_FILTER`/`USE_CROSS_RECENCY_FILTER` as "probably fine off" rather than "proven
bad" — re-evaluate once enough live/paper trading data accumulates to serve as a genuine
out-of-sample check.
