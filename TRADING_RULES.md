# Trading Rules Reference

Entry and exit logic for all bots, as literally implemented in code (verified against `signals.py`, `position_manager.py`, `config.py`, `strategy.py`, etc. in each bot's directory as of 2026-09-04). Numbers are the *effective config defaults* — check each bot's `.env` for overrides.

Where a docstring/comment in the code disagrees with the actual config default, that's called out explicitly below — trust the numbers in this doc over stale comments in the source.

---

## DayTradingBot — 0DTE equity options (SPY, QQQ, IWM)

**Entry (CALL)** — all must hold on 5-min bars:
- `price > VWAP` (session VWAP)
- `EMA9 > EMA21`
- `40 < RSI(14) < 70`
- Symbol's own 15-min trend bullish: `close(15m) > EMA21(15m)` — **always enforced**
- *Optional, off by default:* bull EMA cross within last 20 bars (`USE_CROSS_RECENCY_FILTER=false`); volume > 5-bar avg (`USE_VOLUME_FILTER=false`)

**Entry (PUT)** — mirror: `price < VWAP`, `EMA9 < EMA21`, `30 < RSI < 60`, 15-min HTF bearish.

Needs ≥22 5-min bars or signal is NONE.

**Exit:**
- Stop-loss: `entry_cost × (1 − 15%)` (`STOP_LOSS_PCT=0.15`) — **note:** `position_manager.py`'s docstring says "30%", that's stale; 15% is the real default.
- Trailing stop activates at `+30%` gain (`PROFIT_TRAIL_TRIGGER=0.30`); stop then trails `10%` below high-water mark (`TRAIL_WIGGLE=0.10`). Only ratchets up.
- Force-close all positions at **15:50 ET**; no new entries after **15:45 ET**.

**Filters that can veto a signal:** HTF trend filter (always on); PDT-block flag file (blocks new entries only, existing positions still managed, clears at midnight ET).

**Sizing:** `min(MAX_CONTRACTS=2, affordable qty)`.

---

## VerticalSpreadBot — 0-1 DTE debit vertical spreads (SPY, QQQ)

**Entry** — same VWAP/EMA/RSI/HTF structure as DayTradingBot, but every filter is **always on** (no config toggles) and RSI bands are tighter:
- CALL: `price > VWAP`, `EMA9 > EMA21`, bull cross within 20 bars, `45 < RSI < 60`, volume > 5-bar avg, 15-min HTF bullish.
- PUT: mirror, `40 < RSI < 55`, 15-min HTF bearish.

**Spread construction:**
- Nearest expiration within `MAX_DTE=1` day.
- Long leg = nearest strike to spot (ITM/ATM side); short leg offset by `SPREAD_WIDTH=$2.00`.
- Rejected if either leg illiquid (bid/ask ≤ 0) or net debit ≤ 0 or ≥ width.

**Exit:**
- Profit target: `entry_debit + (width − entry_debit) × 50%` (`PROFIT_TARGET_PCT=0.50`) — checked first.
- Stop-loss: `entry_debit × (1 − 50%)` (`STOP_LOSS_PCT=0.50`).
- No trailing stop — both levels are fixed at entry.
- Same **15:45 / 15:50 ET** entry cutoff / force-close as DayTradingBot.

**Sizing:** `min(MAX_CONTRACTS=1 spread, affordable qty, $150 max debit per trade)`.

---

## CryptoBot — BTC/USD momentum (24/7)

**Entry (BUY)** — on 4-hour bars:
- `EMA9 > EMA21` (state, not just a fresh cross)
- `RSI(14, 4h) < 65`
- 1-hour RSI filter: blocked if 1h RSI ≥ 50 (allowed through if 1h data unavailable)
- No existing position in the symbol

**Exit (SELL)** — full exit, no partial:
- `EMA9 < EMA21` **OR** `RSI(4h) > 72`
- No price-based stop-loss/take-profit — exit is purely indicator-driven, checked every 4 hours.

**Sizing:** `min($500 per trade, 40% of portfolio value, 99% of cash)`.

No time-of-day gating — trades 24/7.

---

## WheelBot — CSP → assignment → CC wheel (QBTS, RIOT, CIFR, CLSK)

Fully mechanical state machine: `IDLE → CSP_PENDING → CSP_OPEN → ASSIGNED → CC_PENDING → CC_OPEN → (IDLE or back to ASSIGNED)`. **No stop-loss or profit-target exit exists anywhere in this bot** — by design, positions only resolve via assignment, expiration, or being called away.

- **Sell CSP** (IDLE): strike = `spot × (1 − 7%)` (`CSP_OTM_PCT=0.07`), DTE window **14–35 days**. Picks the liquid contract closest to target strike.
- **CSP fills** → assignment detected once share qty ≥ 100 → `ASSIGNED`, cost basis = `avg_entry_price − premium/100`.
- **CSP expires worthless** (`today > expiry`) → keep premium, back to IDLE.
- **Sell CC** (ASSIGNED): strike = `cost_basis × (1 + 7%)` (`CC_OTM_PCT=0.07`), same 14–35 DTE window.
- **CC fills, then shares called away** (qty drops below 100) → cycle complete, realized P&L = `(CC_strike − CSP_strike) × 100 + total_premium` → IDLE.
- **CC expires worthless** → keep shares + premium, sell a new CC (stays in `ASSIGNED`, doesn't reset to IDLE).

---

## RobinhoodDayTradingBot — equity day trading

**Entry** — simpler than the Alpaca options bots (no HTF/volume/cross filters), on 5-min bars:
- BUY: `price > VWAP` AND `EMA9 > EMA21` AND `40 < RSI(14) < 70`
- SELL: `price < VWAP` AND `EMA9 < EMA21` AND `30 < RSI(14) < 60`

**Exit:**
- Stop-loss: **2%** below fill (`STOP_LOSS_PCT=0.02`)
- Profit target: **4%** above fill (`PROFIT_TARGET_PCT=0.04`) — once hit, stop trails 2% below current price (reuses `STOP_LOSS_PCT` as the trail distance), ratchets up only.
- Same **15:45 / 15:50 ET** cutoff/force-close convention as the other day-trading bots.

**Sizing:** `20% of buying power` per trade (`MAX_POSITION_PCT=0.20`).

**Note:** `DRY_RUN=true` by default — must be explicitly set `false` to place real orders.

---

## SchwabStopLossBot (Node.js) — tiered trailing stop on long options

Not an entry strategy — a stop-loss *guardian* for existing positions. Tiered by contract price:

| Price tier | Margin | Effective rule |
|---|---|---|
| $0–$100 | $10 or 25% | whichever produces the higher (closer) stop |
| $100–$200 | $15 or 25% | whichever is higher |
| $200+ | $25 or 25% | whichever is higher |

`stop_price = max(price − $margin, price × (1 − 25%))`, recalculated live off **current price** (not a peak/high-water mark). A stop is (re)placed only if the new stop is higher than the existing one by >$0.01, or the price crossed a tier boundary — so it only ever ratchets up. Active only during market hours (9:30–16:00 ET).

**Note:** `DRY_RUN=true` by default.

---

## RobinhoodSafeGuard — two-phase stop-loss guardian for long options

Also a guardian, not an entry strategy. Only applies to **long** option positions (short/covered legs are skipped). Runs 09:30–16:00 ET, Mon–Fri.

- **Phase 1** (profit < 100%): place a stop once, at `mark − $20` flat (`INITIAL_STOP_DISTANCE=20.0`). Left untouched afterward until profit threshold is hit.
- **Phase 2** (profit ≥ 100%, `PROFIT_THRESHOLD=1.0`): stop recalculated as `mark × (1 − 20%)` (`TRAILING_STOP_PCT=0.20`), replacing the old stop only if the new one is higher by >$0.01 (never trails down).
- Partial close / quantity mismatch → stop is immediately replaced at the current phase's price.
- Order type: GTC stop-limit, limit = `stop × 0.90` (10% slippage buffer).

**Note:** `DRY_RUN=true` by default.

---

## CopyTradingBot — mirrors congressional trades (default: Markwayne Mullin)

Not indicator-driven — mirrors disclosed trades from an external feed.

**Entry decision:**
1. Fetch politician's trades; skip any `trade_id` already in `trades_log.csv` (dedup ledger).
2. Only `stock`/`ST`/blank ticker types are executed — options/other types logged as skipped.
3. Only executes during market hours (12:00–19:00 UTC / 8am–3pm ET); outside that window a new trade is logged as pending but **not executed or queued for later**.
4. SELL only executes if a position is currently held; BUY skipped if existing position value ≥ `$2,000` (`MAX_POSITION_USD`).

**Sizing:** fixed `$300` per trade (`TRADE_AMOUNT_USD`), regardless of the politician's actual disclosed trade size. Market order, day time-in-force.

**No exit/stop-loss logic exists** — this bot only mirrors buy/sell *signals*; it does not independently manage risk on the resulting position.

**Currently broken:** QuiverQuant auth was never wired up (`.env` has no API key, no `Authorization` header sent) — every fetch has 401'd since 2026-05-29. See `.memory/project_known_issues.md`.

---

## Cross-bot inconsistencies worth reviewing

- **DayTradingBot vs VerticalSpreadBot:** nearly identical indicator code, but VerticalSpreadBot's RSI bands are tighter (45–60 / 40–55 vs 40–70 / 30–60) and its volume/cross-recency/HTF filters are always-on, while DayTradingBot's volume and cross-recency filters are config-gated and **disabled by default**. Worth deciding if this divergence is intentional.
- **DayTradingBot's stop-loss docstring says 30%,** but the actual default is 15% (`STOP_LOSS_PCT=0.15`). Doc/code mismatch — the code wins, but the comment should be fixed to avoid future confusion.
- **CLAUDE.md's bot summary describes RobinhoodSafeGuard as "25% or $20 cap"** — the actual code implements a flat **$20** initial stop and a **20%** trailing stop (with a 100%-profit phase trigger), not 25%. This doc reflects the real code; CLAUDE.md's description should be corrected.
- Three bots (DayTradingBot, VerticalSpreadBot, RobinhoodDayTradingBot) share the exact same **15:45 / 15:50 ET** no-new-entry / force-close convention — intentional shared risk control for 0DTE-style strategies.
- Only WheelBot and CopyTradingBot have **no price-based exit logic at all** — WheelBot by design (mechanical wheel), CopyTradingBot because it only mirrors entry signals and never manages risk on the resulting position.
