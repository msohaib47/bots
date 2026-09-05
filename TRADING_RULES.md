# Trading Rules Reference

Entry and exit logic for all bots, as literally implemented in code (verified against `signals.py`, `position_manager.py`, `config.py`, `strategy.py`, etc. in each bot's directory as of 2026-09-04; DayTradingBot section re-verified 2026-09-05 after its "ChatGPT-Upgrades" signal rewrite, entry-quality filters, and sizing change). Numbers are the *effective config defaults* — check each bot's `.env` for overrides.

**DayTradingBot deploy log (2026-09-05):** ChatGPT-Upgrades signal rewrite; 12:00 entry cutoff (was 15:45); scale-out disabled by default; `$2,000`(+10%) open-exposure cap replacing an earlier max-drawdown-halt prototype; `MAX_PREMIUM_PCT=1.0%` and `MAX_EMA_GAP_ATR=1.2` entry filters (out-of-sample validated on an Apr–May holdout); `CASH_PER_TRADE_PCT=0.75` (was a hard-coded 25%). All deployed to `~/bots-live/DayTradingBot` same day; paper account balance left at its real ~$10,000 (the $200-seed backtest was a sizing-behavior check only, not a deploy instruction). Watch `~/bots-live/logs/daytrading.log` over the next few live sessions before trusting the backtest numbers.

Where a docstring/comment in the code disagrees with the actual config default, that's called out explicitly below — trust the numbers in this doc over stale comments in the source.

---

## DayTradingBot — 0DTE equity options (SPY, QQQ, IWM, TSLA, NVDA, INTC, MSFT, META)

**Rewritten 2026-09-05** ("ChatGPT-Upgrades" strategy) — replaces the old VWAP/EMA/RSI + static HTF filter described in prior versions of this doc. Symbol list (`symbols.py`) now also runs TSLA, NVDA, INTC, MSFT, META alongside SPY/QQQ/IWM.

**Entry (CALL)** — all must hold:
- 15-min: `close(15m) > EMA21(15m)` **AND** `EMA21(15m)` rising (trend direction now required, not just position relative to EMA21) — needs ≥23 15-min bars or the HTF check returns "no trend" and blocks entry
- 5-min: `price > VWAP` (session VWAP)
- 5-min: `EMA9 > EMA21`, and `EMA9` itself rising
- `50 < RSI(14) < 70` (`RSI_CALL_RANGE`, tightened from the old 40–70)
- `ADX(14) > 20` (`ADX_MIN`) — new trend-strength filter, wasn't in the old strategy at all
- `|EMA9 − EMA21| / ATR(14) ≤ 1.2` (`MAX_EMA_GAP_ATR`, added and deployed 2026-09-05) — don't chase: entries where the 5-min trend was already extended past 1.2 ATR won 44% with PF 0.81 in the Jun–Sep 2026 option-path backtest vs 55–85% below that. Out-of-sample confirmed on an Apr–May 2026 holdout the filter never saw: win rate 58.8% → 70.7%, PF 1.18 → 2.17 (combined with the premium filter below).

**Entry (PUT)** — mirror: 15m `close < EMA21` and EMA21 falling; 5m `price < VWAP`, `EMA9 < EMA21` falling, `30 < RSI < 50` (`RSI_PUT_RANGE`, tightened from 30–60), `ADX > 20`.

Needs ≥29 5-min bars (`2 × ADX_PERIOD + 1`, `ADX_PERIOD=14`) or signal is NONE — up from the old ≥22-bar minimum, since ADX needs the longer lookback.

The old `USE_VOLUME_FILTER` / `USE_CROSS_RECENCY_FILTER` toggles are gone — volume and cross-recency are no longer part of the signal at all, superseded by the ADX + EMA21-slope requirements above.

**Exit:**
- Stop-loss: `entry_cost × (1 − 30%)` live — code default is `STOP_LOSS_PCT=0.15`, but both the local `.env` and (as of the 2026-09-05 deploy) the server's `~/bots-live/DayTradingBot/.env` override it to `0.30`. The server `.env` had been sitting at `0.15` until that deploy, i.e. live was *not* what this doc previously claimed; it now matches the value the option-path backtest was run with.
- Trailing stop activates at `+30%` gain (`PROFIT_TRAIL_TRIGGER=0.30`, no override); stop then trails below the high-water mark by `TRAIL_WIGGLE` — code default `10%`, overridden to `15%` in both local and server `.env` (server aligned 2026-09-05, same as above).
- **Scale-out — OFF by default** (`HALF_CLOSE_ENABLED=false`, added 2026-09-05): when enabled, the first time a position is up `+50%` (`HALF_CLOSE_PROFIT_PCT=0.50`) it sells `contracts // 2` at market and the remainder keeps trailing. Disabled because the option-path backtest (Jun–Sep 2026, SPY/QQQ/IWM) showed it cost ~$400 on +$2.3k with no drawdown benefit once the noon cutoff below was in place. Per-tick order of checks when on: high-water update → trail activation → scale-out → stop check.
- Force-close all positions at **15:50 ET**; **no new entries after 12:00 ET** (`NO_NEW_ENTRY_TIME`, changed from 15:45 on 2026-09-05 — afternoon entries were net negative in the same backtest, 19 trades / −$347 / PF 0.76, and dropping them lowered max drawdown). The bot is effectively a morning scalper: median hold ~14 minutes, almost every trade exits on the 30% stop or the trail, essentially none reach the force-close.
- After a (non-trailing) stop-loss fires, the underlying enters a **cool-down** (`COOLDOWN_MINUTES=30`, new): blocked from new entries until either the failed setup's own trend/VWAP condition breaks down (`EMA9`/`EMA21` cross back, or a VWAP-side cross, in the stopped-out direction) or the 30-minute cap expires, whichever comes first.

**Risk limits (new, none of this existed in the prior version of this bot):**
- Max realized loss per symbol per day: `$100` (`MAX_DAILY_LOSS_PER_SYMBOL`) — blocks new entries on that symbol for the rest of the day once hit.
- Max realized loss total per day: `$500` (`MAX_DAILY_LOSS_TOTAL`) — blocks *all* new entries for the rest of the day once hit; existing positions still managed.
- Max concurrent positions per symbol: `2` (`MAX_POSITIONS_PER_SYMBOL`).
- Max concurrent same-direction positions across all symbols: `2` (`MAX_SAME_DIRECTION`) — e.g. at most 2 CALLs open at once regardless of underlying.
- Minimum contract premium to trade: `$0.20`/share (`MIN_CONTRACT_PRICE`) — skips contracts quoted below $20/contract.
- Maximum contract premium: `1.0%` of spot (`MAX_PREMIUM_PCT`, added and deployed 2026-09-05) — skips a contract whose mid exceeds 1% of the underlying's price (high IV or not truly ATM). Such contracts won 42% with PF 0.68 in the Jun–Sep 2026 backtest vs 70% / PF 3.5 under 0.5%; this is also what made INTC (median premium 1.1–1.6%) a break-even symbol. The bot re-evaluates on the next tick, so a symbol can still enter later in the morning once premium normalizes.
- **Max open exposure** (added 2026-09-05): `$2,000` (`MAX_OPEN_EXPOSURE`) of premium tied up across all open positions at once, measured at entry cost (`entry_cost × contracts × 100`, summed over `positions.json`), with a `10%` tolerance (`EXPOSURE_TOLERANCE_PCT`) so the hard ceiling is `$2,200`. A new entry is **sized down** to however many contracts fit under the ceiling (`min(MAX_CONTRACTS, affordable, floor(room / cost_per_contract))`) and skipped only if not even 1 fits; a scale-out or close frees room immediately. This is a capital-at-risk limit, not a P&L circuit breaker — nothing in this bot halts trading on cumulative losses (an earlier `$2,000` *drawdown* halt was prototyped and dropped the same day: in the Jun–Sep 2026 8-symbol backtest it tripped on Jun 29 and would have idled the bot for the remaining 48 sessions). `bot.py --status` shows current open exposure vs the cap, plus lifetime realized P&L (`pnl_history.json`, informational only).

**Filters that can veto a signal:** 15-min HTF trend+slope filter (always on, no toggle); post-stop-loss cool-down per symbol; per-symbol and total daily-loss caps; open-exposure cap (sizes down, then skips); max-positions-per-symbol and max-same-direction caps; PDT-block flag file (blocks new entries only, existing positions still managed, clears at midnight ET).

**Sizing:** `min(MAX_CONTRACTS=2, floor(cash × 75% / cost), floor(exposure room / cost))` — at most 75% of current cash per entry (`CASH_PER_TRADE_PCT`, relaxed from a hard-coded 25% on 2026-09-05 so small balances can still buy one contract), and never more than fits under the `$2,000` (+10%) open-exposure cap.

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

- **DayTradingBot vs VerticalSpreadBot:** these have diverged further since DayTradingBot's 2026-09-05 rewrite — DayTradingBot now adds an ADX(14)>20 trend-strength filter and requires the 15m EMA21 to be *sloping* (not just positioned) in the trade direction, neither of which VerticalSpreadBot has; VerticalSpreadBot still has the older volume/cross-recency filters (always-on) that DayTradingBot has dropped entirely. RSI bands also still differ (VerticalSpreadBot 45–60/40–55 vs DayTradingBot's now-tightened 50–70/30–50). Worth deciding if this divergence is intentional.
- **DayTradingBot's stop-loss docstring says 30%,** but the actual default is 15% (`STOP_LOSS_PCT=0.15`). Doc/code mismatch — the code wins, but the comment should be fixed to avoid future confusion.
- **CLAUDE.md's bot summary describes RobinhoodSafeGuard as "25% or $20 cap"** — the actual code implements a flat **$20** initial stop and a **20%** trailing stop (with a 100%-profit phase trigger), not 25%. This doc reflects the real code; CLAUDE.md's description should be corrected.
- Three bots (DayTradingBot, VerticalSpreadBot, RobinhoodDayTradingBot) share the exact same **15:45 / 15:50 ET** no-new-entry / force-close convention — intentional shared risk control for 0DTE-style strategies.
- Only WheelBot and CopyTradingBot have **no price-based exit logic at all** — WheelBot by design (mechanical wheel), CopyTradingBot because it only mirrors entry signals and never manages risk on the resulting position.
