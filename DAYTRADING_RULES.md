# DayTradingBot Family — Trading Rules Reference

Covers the three bots that share the same "ChatGPT-Upgrades" 0DTE options strategy: **DayTradingBot** (canonical, real ~$10k Alpaca paper account), **DT-Bot-200** (identical clone, $200 Alpaca paper account — "Start-at-200"), and **DT-Webull** (same strategy ported to the Webull broker API, multi-account capable). Split out from `TRADING_RULES.md` on 2026-09-08 once this family had enough bot-specific drift (account size, broker, symbol list, live-vs-not-yet-synced settings) to warrant its own document — see that file for every other bot in the repo.

Signal/exit logic below is verified identical across all three (`signals.py`/`position_manager.py` are literal copies differing only in which broker module they import from — `alpaca` vs `webull`). Where the three bots' *live settings* diverge, that's called out explicitly in the per-bot section — don't assume `.env` values match just because the code does.

---

## Shared strategy — entry/exit logic (identical across all three bots)

**Entry (CALL)** — all must hold:
- 15-min: `close(15m) > EMA21(15m)` **AND** `EMA21(15m)` rising — needs ≥23 15-min bars or the HTF check returns "no trend" and blocks entry
- 5-min: `price > VWAP` (session VWAP)
- 5-min: `EMA9 > EMA21`, and `EMA9` itself rising
- `50 < RSI(14) < 70` (`RSI_CALL_RANGE`)
- `ADX(14) > 20` (`ADX_MIN`) — trend-strength filter
- `|EMA9 − EMA21| / ATR(14) ≤ 1.2` (`MAX_EMA_GAP_ATR`) — don't chase an already-extended move

**Entry (PUT)** — mirror: 15m `close < EMA21` and EMA21 falling; 5m `price < VWAP`, `EMA9 < EMA21` falling, `30 < RSI < 50` (`RSI_PUT_RANGE`), `ADX > 20`.

Needs ≥29 5-min bars (`2 × ADX_PERIOD + 1`) or signal is NONE.

**Exit:**
- Stop-loss: `entry_cost × (1 − STOP_LOSS_PCT)` — live default `30%` on all three (code default is `15%`, overridden in every deployed `.env`).
- Trailing stop activates at `+PROFIT_TRAIL_TRIGGER` gain (`30%`, no override anywhere); stop then trails below the high-water mark by `TRAIL_WIGGLE` (live `15%`, code default `10%`).
- Scale-out (`HALF_CLOSE_ENABLED`) — **off** on all three. When enabled: first time a position hits `+HALF_CLOSE_PROFIT_PCT` (default `50%`), sells `contracts // 2` at market, remainder keeps trailing.
- After a (non-trailing) stop-loss, the underlying enters a **cool-down** (`COOLDOWN_MINUTES=30`): blocked from new entries until either the failed setup's own trend/VWAP condition breaks down in the stopped-out direction, or the 30-minute cap expires.

**Risk limits (fixed dollar amounts, not scaled to account size — see the DT-Bot-200 section for why this matters):**
- Max realized loss per symbol per day: `$100` (`MAX_DAILY_LOSS_PER_SYMBOL`)
- Max realized loss total per day: `$500` (`MAX_DAILY_LOSS_TOTAL`)
- **A symbol never gets a second concurrent position** — `bot.py` skips any symbol that already has an open position, full stop (no config knob; this is now unconditional, per explicit user intent as of 2026-09-08 — see `MAX_CONTRACTS_PER_SYMBOL` below for the prior model this replaced).
- Max concurrent same-direction positions across all symbols: `4` (`MAX_SAME_DIRECTION`)
- Minimum contract premium: `$0.20`/share (`MIN_CONTRACT_PRICE`)
- Maximum contract premium: `1.0%` of spot (`MAX_PREMIUM_PCT`) — skips a contract whose mid exceeds 1% of the underlying's price
- Max open exposure: `$5,000` (`MAX_OPEN_EXPOSURE`, +10% tolerance = `$5,500` hard ceiling) of premium tied up across all open positions at once. A new entry is sized down to whatever fits and skipped only if not even 1 contract fits.

**Sizing:** `min(MAX_CONTRACTS_PER_SYMBOL=10, floor(cash × 75% / cost), floor(exposure room / cost))` — at most 75% of current cash per entry (`CASH_PER_TRADE_PCT`), never more than fits under the open-exposure cap.

**`MAX_CONTRACTS_PER_SYMBOL` (renamed from `MAX_CONTRACTS`, merged with the old `MAX_POSITIONS_PER_SYMBOL` on 2026-09-08):** previously two separate knobs — `MAX_CONTRACTS` capped one entry order's size, `MAX_POSITIONS_PER_SYMBOL` (default `2`) separately capped how many concurrent positions could exist on one underlying, so in principle a symbol could have 2 positions of up to `MAX_CONTRACTS` each open at once. Per explicit user intent ("I don't want to open the same symbol position multiple times — I only wa[nt] max contracts per symbol and max concurrent symbols"), these were collapsed: a symbol now gets **at most one open position, ever**, sized up to `MAX_CONTRACTS_PER_SYMBOL` contracts. `MAX_SAME_DIRECTION` is unchanged and is now the only "how many symbols at once" lever, alongside `MAX_CONTRACTS_PER_SYMBOL` for "how big is one symbol's position." Worst case: 4 same-direction positions (one per symbol, since a symbol can't have two) × 10 contracts each = 40 contracts of one-directional exposure open at once; `MAX_OPEN_EXPOSURE` is the actual dollar backstop, not the position/contract counters.

**Filters that can veto a signal:** 15-min HTF trend+slope filter (always on); post-stop-loss cool-down per symbol; per-symbol and total daily-loss caps; open-exposure cap (sizes down, then skips); existing-position-on-this-symbol and max-same-direction checks; PDT-block flag file (existing positions still managed, clears at midnight ET).

---

## Per-bot: what actually differs

| | DayTradingBot | DT-Bot-200 | DT-Webull |
|---|---|---|---|
| Broker | Alpaca (paper) | Alpaca (paper) | Webull ("main" = sandbox/paper; a real/live account exists in `.env` but is deliberately excluded from `WEBULL_ACCOUNTS`, not traded) |
| Account balance | ~$10,000 | $200 ("Start-at-200") | sandbox paper |
| Symbols | `META, GOOG, QQQ, MSFT, TSLA` (5 symbols, swapped SPY->QQQ+TSLA on 2026-09-08, fourth pass — see below) | same (synced) | same (synced) |
| `MAX_CONTRACTS_PER_SYMBOL` | `10` (raised from 4 on 2026-09-08, second pass; renamed from `MAX_CONTRACTS` + merged with `MAX_POSITIONS_PER_SYMBOL` on 2026-09-08, third pass — see below) | same (synced) | same (synced) |
| `NO_NEW_ENTRY_TIME` / `FORCE_CLOSE_TIME` | `15:58` / `15:58` (no entry cutoff, updated 2026-09-08) | `15:58` / `15:58` (synced) | same (synced) |
| `COOLDOWN_MINUTES` | `15` (down from 30, 2026-09-08, fourth pass) | same (synced) | same (synced) |
| Server path | `~/bots-live/DayTradingBot` | `~/bots-live/DT-Bot-200` | `~/bots-live/DT-Webull` |
| Cron | `* 8-15 * * 1-5` (`~/daytradingbot.sh`) | `* 8-15 * * 1-5` (`~/dtbot200.sh`) | `* 8-15 * * 1-5` (`~/dtwebull.sh`) |
| Position sync | `~/daytradingpositionsync.sh`, every 30 min | `~/dtbot200sync.sh`, every 30 min | none |

**All three bots run identical settings as of 2026-09-08** (symbols, `MAX_CONTRACTS`, entry-cutoff/force-close) — DT-Webull briefly lagged behind DayTradingBot/DT-Bot-200 earlier the same day (the symbol swap and entry-cutoff removal were validated against DayTradingBot's Alpaca-sourced backtest data first) but was brought in sync same-day. Standing convention going forward: any setting change deployed to the server applies to all three unless a bot is explicitly called out as an exception.

---

## 2026-09-08 changes: entry-cutoff removal + symbol swap

**Why:** the original `NO_NEW_ENTRY_TIME=12:00` cutoff was justified by a Jun–Sep 2026 backtest run *before* the `MAX_EMA_GAP_ATR`/`MAX_PREMIUM_PCT` entry-quality filters existed. Re-tested against an August 2026 / $200-seed backtest with the current filters in place, and found to be actively costing money at small account sizes: afternoon signals were being skipped that would have compounded the account and unlocked larger later sizing (cash-blocked entries dropped from 41 to 3 once the cutoff was removed, because the account grew fast enough early to stop being cash-constrained).

**Symbol swap:** dropped `IWM, QQQ, INTC` (flat-to-negative in the same window), added `AMZN, GOOG, AMD` (GOOG/META/TSLA were the standout performers).

**Backtest results, August 2026, $200 starting cash** (`DayTradingBot/backtest.py`, real historical option trade prices, `--cash 200` cash-based sizing):

| Symbol set | Entry cutoff | Trades | Win% | Total P&L |
|---|---|---|---|---|
| Old (`SPY,QQQ,IWM,TSLA,NVDA,INTC,MSFT,META`) | 12:00 (old) | 80 | 30.0% | +$4,550 |
| Old | No cutoff (new) | 134 | 41.0% | +$13,347 |
| New (`SPY,TSLA,NVDA,MSFT,META,AMZN,GOOG,AMD`) | 12:00 (old) | 109 | 45.0% | +$14,920 |
| **New** | **No cutoff (new)** | **129** | **46.5%** | **+$19,668** |

Combined effect: **+$4,550 → +$19,668 (+332%)** on the same backtest window. Both changes stack cleanly and help independently. Deployed live to DayTradingBot + DT-Bot-200 same day; DayTradingBot opened real trades under the new no-cutoff rule before that day's market close.

To reproduce or extend this comparison: `python backtest.py 2026-08-01 2026-08-31 --cash 200 SYM1 SYM2 ...` (symbol args override `symbols.py`; `NO_NEW_ENTRY_TIME=12:00 FORCE_CLOSE_TIME=15:50 python backtest.py ...` env-overrides the old cutoff for a side-by-side run without touching `config.py`).

---

## 2026-09-08 changes, second pass: symbol narrowing to 4 + MAX_CONTRACTS raise

**Why:** asked directly "what 4 symbols should we use to maximize profit" — ran a broader sweep: first an unconstrained (no cash cap) profitability ranking across 16 candidate symbols (the existing 8 plus `IBM, AAPL, QCOM, WDC, ORCL`) over Aug1-Sep8, then ~8 concrete 4-symbol combinations tested with the real `$200`-cash constraint, since combo performance under cash competition doesn't just follow individual-symbol quality (see the AAPL result below).

**Unconstrained ranking (Aug1-Sep8, no cash cap) — top and bottom of 16 candidates:**

| Symbol | N | Win% | Total P&L |
|---|---|---|---|
| META | 20 | 65.0% | +$10,868 |
| GOOG | 17 | 35.3% | +$6,256 |
| MSFT | 21 | 66.7% | +$3,248 |
| ... | | | (TSLA/AAPL/AMD/NVDA/AMZN/SPY all +$1,100-1,800) |
| INTC | 13 | 30.8% | -$96 |
| IBM | 10 | 30.0% | -$736 |
| QQQ | 20 | 10.0% | -$960 |

IBM/QCOM/QQQ excluded as standalone losers. WDC/ORCL excluded despite strong per-trade numbers -- only 2-3 trades each over the window, too thin a sample to trust.

**4-symbol combo sweep, $200 starting cash (real cash-constrained sizing):**

| Combo | Total P&L | Cash-blocked entries |
|---|---|---|
| **META, GOOG, MSFT, SPY** | **+$16,400** | 4 |
| META, MSFT, SPY, NVDA | +$11,841 | 10 |
| META, GOOG, MSFT, NVDA | +$10,719 | 24 |
| META, GOOG, SPY, AMZN | +$9,313 | 13 |
| META, GOOG, MSFT, TSLA | +$9,253 | 27 |
| META, GOOG, NVDA, TSLA | +$8,850 | 27 |
| META, GOOG, MSFT, AMD | +$7,668 | 36 |
| META, GOOG, MSFT, AAPL | **-$126** | 77 |

**Winner: META, GOOG, MSFT, SPY.** Notably, this isn't simply "the 4 highest-ranked symbols individually" (that would've included TSLA or AAPL over SPY) -- SPY's comparatively low signal frequency is what makes this combo work at $200: only 4 entries were ever cash-blocked over the whole window, vs. 10-77 for every other combo. AAPL is the clearest counter-example: decent standalone (+$1,720, 22 trades), but pairing it with META/GOOG/MSFT flips the combo to a net loss (-$126) purely by out-competing the stronger symbols for the account's limited cash (77 cash-blocked entries). GOOG and MSFT both proved load-bearing too -- removing either dropped the combo to the $9,300-11,800 range.

**Sizing-limit sweep on the winning combo** (same symbols/window, varying `MAX_CONTRACTS`/`MAX_OPEN_EXPOSURE`):

| `MAX_CONTRACTS` | Exposure cap | Total P&L |
|---|---|---|
| 4 (original) | $5,000 (original) | +$16,400 |
| 6 | $10,000 | +$21,745 |
| 10 | $5,000 | +$27,876 |
| **10** | **$10,000** | **+$30,501** |
| 10 | unlimited | +$30,501 (identical -- exposure never bound past $10k) |

`MAX_CONTRACTS` turned out to be the dominant lever, not the exposure cap -- raising contracts alone (4->10, exposure unchanged at $5,000) captured $11,476 of the total $14,101 upside available (81%); raising the exposure cap further from $5k to $10k only added another $2,625 on top, and going beyond $10k added nothing (peak exposure usage never exceeded $10,270 even fully uncapped).

**Deployed:** `symbols.py` narrowed to `META, GOOG, MSFT, SPY` and `MAX_CONTRACTS` raised 4->10 (exposure cap left at the original `$5,000` -- the 10-contracts/$5k combination, not the higher-exposure variants, since the extra ~$2,600 from raising exposure further wasn't judged worth the larger real-money position sizes it implies). Live on DayTradingBot + DT-Bot-200 same day.

**Caveat not fully modeled by this backtest:** larger position sizes (10 contracts vs. 4) may face more real-world slippage/liquidity impact on 0DTE fills, especially exiting quickly on a stop-out, than this backtest's real-historical-trade-price fills account for. Worth watching live fill quality before assuming the backtest edge holds exactly at this size.

---

## 2026-09-08 changes, third pass: `MAX_CONTRACTS` + `MAX_POSITIONS_PER_SYMBOL` merged into `MAX_CONTRACTS_PER_SYMBOL`

**Why:** user feedback, verbatim: *"MAX_CONTRACTS / MAX_POSITIONS_PER_SYMBOL are pretty much the same. combine into one, call it MAX_CONTRACTS_PER_SYMBOL. this is my intent as well. i dont want to open same symbol position multiple times. i only wa[nt] max contracts per symbol and max concurrent symbols (i.e. MAX_SAME_DIRECTION)."* These had been two separate knobs since before this doc's split: `MAX_CONTRACTS` (an entry order's contract count) and `MAX_POSITIONS_PER_SYMBOL` (default `2`, how many concurrent positions one underlying could have open). In principle a symbol could carry 2 separate positions at once, each up to `MAX_CONTRACTS` contracts.

**Change:** `bot.py`'s per-symbol gate (`pm.count_positions_for(state, symbol) >= MAX_POSITIONS_PER_SYMBOL`) became a hardcoded `>= 1` — a symbol can never have more than one open position, full stop, no config knob. `MAX_CONTRACTS` was renamed `MAX_CONTRACTS_PER_SYMBOL` (same value, `10`, unchanged) and now purely controls that one position's size. `MAX_SAME_DIRECTION` is untouched and remains the only "how many symbols concurrently" lever. `MAX_POSITIONS_PER_SYMBOL` no longer exists anywhere in config/code.

**Applied to:** `config.py`, `bot.py`, `position_manager.py` (unused import only), `backtest.py`, `weekly_200_replay.py`, and `.env` (key renamed) across all three bots (DayTradingBot, DT-Bot-200, DT-Webull) — DT-Webull needed hand-applied equivalent edits since its `bot.py`/`config.py` structure differs (multi-account). Verified live on the server: all three import cleanly with no references to either old name, and `bot.py --status` runs correctly on all three post-deploy.

**Not re-run:** the backtest sweep above (symbol/sizing selection) was run *before* this rename and never modeled "one position per symbol, ever" as a hard rule — it modeled the old `MAX_POSITIONS_PER_SYMBOL=2` behavior throughout. Since none of the winning combo's 77 trades in that sweep ever actually opened a second concurrent position on the same symbol (one entry per symbol per day was already the backtest's structural assumption — see `_apply_portfolio_rules`'s docstring), this change is not expected to alter those results, but it hasn't been explicitly re-verified.

---

## 2026-09-08 changes, fourth pass: backtest re-entry + cooldown, symbol re-sweep (SPY out, QQQ+TSLA in)

**Why:** user question — *"why does backtest assume only ever considers one candidate entry per symbol per day?"* — surfaced that `backtest_symbol()`'s per-day loop had a hardcoded `break` after the first trade, a pure simulation simplification never present in the live bot (`bot.py` has no per-day trade counter; its only per-symbol gate is "skip if already has an open position," which already allows immediate re-entry the moment a position closes). User intent, verbatim: *"I want 4 [now `MAX_CONTRACTS_PER_SYMBOL`] contracts max per symbol at any given moment. Once that is closed, it should be allowed to enter again."*

**Fix #1 -- re-entry:** `backtest_symbol()`'s per-day scan converted from a `for` loop with `break` to a `while` loop that resumes scanning from the first bar at/after a closed trade's exit time, allowing a new entry the instant the prior one closes. Verified zero overlapping trades across 51 symbol-days with multiple entries in a spot check.

**Fix #2 -- cooldown was still missing:** the re-entry fix alone made the backtest *more optimistic* than live, since it didn't model `bot.py`'s post-stop-loss cooldown (blocks re-entry until `COOLDOWN_MINUTES` passes or the stopped-out direction's trend/VWAP breaks down, whichever first). Implemented the identical logic in `backtest_symbol()` (tracks `{until, direction, reset_seen, vwap_side}` per symbol per day, checked every bar). Verified: a 1-minute re-entry only occurred when the EMA9/EMA21 trend had genuinely flipped that fast (a real early-reset case, not a bug) -- confirmed against `position_manager.py`'s exact reset conditions.

**Also changed same day:** `COOLDOWN_MINUTES` lowered `30 -> 15` on all three bots (live + backtest), per direct request.

**Impact of re-entry + cooldown alone** (Aug1-Sep4, $200 cash, `META/GOOG/MSFT/SPY`):

| Version | Trades | Total P&L |
|---|---|---|
| Single entry/symbol/day (pre-fix) | ~76 | ~$27-28K (partial window, not directly comparable) |
| Re-entry, no cooldown modeled | 271 | +$30,931 |
| **Re-entry + 15-min cooldown (correct)** | **241** | **+$26,568** |

**Re-swept all 16 candidate symbols with re-entry + cooldown now modeled** (Aug1-Sep8, unconstrained) -- the ranking **reordered substantially** from the second-pass sweep:

| Symbol | N | Win% | Total P&L | vs. second-pass sweep |
|---|---|---|---|---|
| META | 52 | 40.4% | +$16,272 | still top |
| GOOG | 15 | 33.3% | +$11,579 | still top |
| **QQQ** | 59 | 32.2% | **+$11,088** | **was -$960 (excluded as a loser) -- now a top performer** |
| AAPL | 35 | 42.9% | +$7,433 | up from +$1,720 |
| TSLA | 32 | 43.8% | +$3,546 | roughly flat |
| MSFT | 59 | 42.4% | +$3,038 | down from +$3,248 (similar) |
| **SPY** | 51 | 29.4% | **-$2,565** | **was +$1,180 -- now a standalone loser** |

**Why SPY and QQQ swapped places:** SPY's advantage in the old (no-re-entry) sweep was specifically its *low* signal frequency -- it barely competed for the account's limited cash. Once every symbol can re-enter through the day, that advantage disappears, and SPY's smaller, choppier moves just produce more marginal stop-outs. QQQ has the inverse profile: its frequent, smaller moves were a liability when only one shot per day was allowed (more chances to catch it on a bad entry with no recourse), but are an asset once re-entry lets the strategy catch several of its moves per session.

**4/5-symbol combo sweep** ($200 cash, real cash-constrained sizing):

| Combo | Total P&L |
|---|---|
| **META, GOOG, QQQ, MSFT, TSLA (5-symbol)** | **+$35,086** |
| META, GOOG, QQQ, MSFT (4-symbol) | +$31,388 |
| META, GOOG, QQQ, TSLA (4-symbol) | +$28,824 |
| META, GOOG, QQQ, MSFT, AAPL (5-symbol) | +$23,234 |
| META, GOOG, QQQ, MSFT, AMD (5-symbol) | +$22,702 |
| META, GOOG, QQQ, AAPL (4-symbol) | +$16,939 |

Same pattern as every prior sweep: AAPL and AMD both hurt the combo despite decent-to-good standalone numbers, via cash competition with the stronger core. TSLA is the best 5th symbol, adding ~$3,700 over the best 4-symbol combo.

**Deployed:** `symbols.py` -> `META, GOOG, QQQ, MSFT, TSLA` (SPY removed) on all three bots (DayTradingBot, DT-Bot-200, DT-Webull), same day, verified live.

---

## Known bugs fixed 2026-09-08 (see `.memory/project_known_issues.md` for full detail)

- **`get_recent_bars()` stale-data bug (DayTradingBot, DT-Bot-200 via shared code, VerticalSpreadBot, DayTradingBotV2)** — an ascending-order/pagination bug caused this function to silently serve a frozen, market-open snapshot for the rest of every session since 2026-08-10, with no exceptions thrown. Fixed via `sort=desc` + local reversal. DT-Webull does **not** share this bug (different broker API, no date-range param) but has its own issues below.
- **DT-Webull crashed on every single tick since deployment** — Webull's API returns OHLCV as JSON strings (not numbers) and bars newest-first (not chronological); `signals.py`'s VWAP math assumed both were otherwise, causing a `TypeError` on every tick. Fixed via type casting + reversal in `webull.py`, plus added proper session-date filtering (Webull's API has no server-side date param, so `get_intraday_bars` now over-fetches and filters client-side to today's ET date).
- **Still open:** DT-Webull's sandbox API is actively rate-limiting (`429 Too Many Requests`) on most symbols per tick — likely why several symbols show "Not enough bars" even after the fixes above. Needs a decision: reduce call volume (share one raw fetch across the 3 derived bar calls per symbol per tick), add retry/backoff, or check a production API key's limits.

---

## 2026-09-08 changes, fifth pass: merged multi-account dashboard + shared Signals log

**Why:** DayTradingBot and DT-Webull each had their own standalone dashboard (`DayTradingBot/generate.py` -> `daytrading.sandbox.solutionzeroone.com`, `DT-Webull/generate.py` -> `dt-webull.sandbox.solutionzeroone.com`), and DT-Bot-200 had none at all. Asked to merge all three into one, using DT-Webull's existing multi-account tab pattern, plus add a section listing every signal the strategy has generated (not just ones that became trades) -- shared across all three bots since they all run the identical strategy.

**New file: `daytrading_sandbox_dashboard.py`** (repo root, run from `~/bots-live/` since it reads all three bot directories as siblings) -- one merged page with a tab per account:
- `DayTradingBot ($10k)`, `DT-Bot-200 ($200)` -- each reads its own `trades.csv` directly.
- `DT-Webull: Main`, `DT-Webull: Live` -- discovered dynamically by parsing `DT-Webull/.env`'s `WEBULL_DASHBOARD_ACCOUNTS` (via `dotenv_values()`, a pure file parse -- deliberately *not* importing `DT-Webull/config.py`, which would hit the exact "bare `load_dotenv()` searches from the wrong script's directory" bug already documented in DayTradingBotV2's `PLAN.md`, since this script isn't the one DT-Webull's config.py was written to be imported by). The "Live" account is flagged real-money (red, ⚠️) same as DT-Webull's own dashboard used to do.
- **`Signals`** tab (shared, not per-account) -- lists every CALL/PUT signal the strategy generated, whether or not it led to a trade, grouped by day plus a full chronological table. Reads `DayTradingBot/signals.csv` only; DT-Bot-200's and DT-Webull's own `signals.csv` are redundant copies of the same data (same strategy, same symbols) and are not read.

**New: `position_manager.log_signal()`** (added identically to all three bots) -- records every CALL/PUT signal to `signals.csv` (columns: timestamp, symbol, direction, price, RSI/ADX/ATR/EMA9/EMA21/VWAP/HTF/ema_gap_atr, reason, outcome). `bot.py` calls it at every point a real signal could end without becoming a trade (`SKIP_SAME_DIRECTION`, `SKIP_NO_CONTRACT`, `SKIP_MIN_PRICE`, `SKIP_PREMIUM`, `SKIP_EXPOSURE`, `SKIP_CASH`, `SKIP_PDT`, `SKIP_ORDER_FAILED`) as well as on an actual `TRADED` outcome. `NONE` signals are never logged (too voluminous, no decision to record).

**Deploy changes on the server:**
- `~/daytradingdashboard.sh` now calls `daytrading_sandbox_dashboard.py` instead of `DayTradingBot/generate.py`.
- `~/dtwebulldashboard.sh`'s crontab entry retired (commented out) -- it was racing with the merged script's writes to the same output directory, each 5-minute tick flip-flopping between the old single-bot page and the new merged one until this was caught and fixed.
- nginx's internal `dtwebull` vhost (port 8086, backing `dt-webull.sandbox.solutionzeroone.com`) had its `root` repointed from `/home/sohaib/sites/dt-webull` to `/home/sohaib/sites/daytrading` (backup saved as `dtwebull.bak-20260908`) -- both public subdomains now serve the identical merged page from one output directory.

**Not deleted (still present, just no longer in the live cron pipeline):** `DayTradingBot/generate.py` and `DT-Webull/generate.py`, the two original standalone generators. Left in place rather than removed since they still work standalone for single-bot debugging and deleting them has no upside -- just don't expect their output to reach either public URL anymore.

---

## 2026-09-08 changes, sixth pass: orphaned-close-order fix + account balance on dashboard

**Why:** user reported "2 alpaca accounts, sync balance and positions with server, there is a mismatch" plus asked for account balance/net-liq to be shown next to Overall P/L on the merged dashboard.

**Orphaned-position fix** -- see the new entry at the top of the "Known bugs fixed" section above and the full writeup in `.memory/project_known_issues.md`. Applied identically to `DayTradingBot`, `DT-Bot-200`, `DT-Webull`.

**New: `account_snapshot.json` (per-bot; DT-Webull writes one per account, `account_snapshot_<name>.json`)** -- each bot's `bot.py` now fetches `{cash, portfolio_value}` (Alpaca) / `{total_cash_balance, total_net_liquidation_value}` (Webull) once per live tick and writes it via `position_manager.save_account_snapshot()`. This avoids giving the dashboard script its own trading credentials -- it just reads the small JSON file each bot already produces. DT-Webull's "live" account never runs `run_account()` (it's dashboard-only, not in `WEBULL_ACCOUNTS`), so `bot.py`'s `run()` separately refreshes snapshots for every `DASHBOARD_ACCOUNTS` entry not already covered by `ACCOUNTS`, or its balance card would never update.

**Dashboard change:** `daytrading_sandbox_dashboard.py`'s `build_account_panel()` now takes a `snapshot` dict and renders an "Account Balance (Net Liq)" hero-card immediately next to "Overall P/L" whenever a snapshot file exists for that account (dims/tooltips with the snapshot's `updated_at` if it's over an hour stale). Verified live on `daytrading.sandbox.solutionzeroone.com`: DayTradingBot $9,938.80, DT-Bot-200 $204.95, DT-Webull Main $1,000,000 (sandbox), DT-Webull Live $200 (real money).
