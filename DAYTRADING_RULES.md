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
- Max concurrent positions per symbol: `2` (`MAX_POSITIONS_PER_SYMBOL`)
- Max concurrent same-direction positions across all symbols: `4` (`MAX_SAME_DIRECTION`)
- Minimum contract premium: `$0.20`/share (`MIN_CONTRACT_PRICE`)
- Maximum contract premium: `1.0%` of spot (`MAX_PREMIUM_PCT`) — skips a contract whose mid exceeds 1% of the underlying's price
- Max open exposure: `$5,000` (`MAX_OPEN_EXPOSURE`, +10% tolerance = `$5,500` hard ceiling) of premium tied up across all open positions at once. A new entry is sized down to whatever fits and skipped only if not even 1 contract fits.

**Sizing:** `min(MAX_CONTRACTS=4, floor(cash × 75% / cost), floor(exposure room / cost))` — at most 75% of current cash per entry (`CASH_PER_TRADE_PCT`), never more than fits under the open-exposure cap.

**How `MAX_CONTRACTS` / `MAX_POSITIONS_PER_SYMBOL` / `MAX_SAME_DIRECTION` compose** (they gate different things, not contradictory, but they compound): `MAX_CONTRACTS` caps one entry's size; `MAX_POSITIONS_PER_SYMBOL` caps concurrent positions on one underlying; `MAX_SAME_DIRECTION` caps total same-direction positions across *all* symbols — with 8 symbols traded this can be reached using just 2 of them. Worst case: 4 same-direction positions × 4 contracts each = 16 contracts of one-directional exposure open at once; `MAX_OPEN_EXPOSURE` is the actual dollar backstop, not the position/contract counters.

**Filters that can veto a signal:** 15-min HTF trend+slope filter (always on); post-stop-loss cool-down per symbol; per-symbol and total daily-loss caps; open-exposure cap (sizes down, then skips); max-positions-per-symbol and max-same-direction caps; PDT-block flag file (existing positions still managed, clears at midnight ET).

---

## Per-bot: what actually differs

| | DayTradingBot | DT-Bot-200 | DT-Webull |
|---|---|---|---|
| Broker | Alpaca (paper) | Alpaca (paper) | Webull ("main" = sandbox/paper; a real/live account exists in `.env` but is deliberately excluded from `WEBULL_ACCOUNTS`, not traded) |
| Account balance | ~$10,000 | $200 ("Start-at-200") | sandbox paper |
| Symbols | `META, GOOG, MSFT, SPY` (narrowed to 4 on 2026-09-08, second pass — see below) | same as DayTradingBot (synced 2026-09-08) | **`SPY, QQQ, IWM, TSLA, NVDA, INTC, MSFT, META`** — old 8-symbol set, **not yet synced** |
| `MAX_CONTRACTS` | `10` (raised from 4 on 2026-09-08, same pass) | same (synced) | `4` — old setting, **not yet synced** |
| `NO_NEW_ENTRY_TIME` / `FORCE_CLOSE_TIME` | `15:58` / `15:58` (no entry cutoff, updated 2026-09-08) | `15:58` / `15:58` (synced) | **`12:00` / `15:50`** — old settings, **not yet synced** |
| Server path | `~/bots-live/DayTradingBot` | `~/bots-live/DT-Bot-200` | `~/bots-live/DT-Webull` |
| Cron | `* 8-15 * * 1-5` (`~/daytradingbot.sh`) | `* 8-15 * * 1-5` (`~/dtbot200.sh`) | `* 8-15 * * 1-5` (`~/dtwebull.sh`) |
| Position sync | `~/daytradingpositionsync.sh`, every 30 min | `~/dtbot200sync.sh`, every 30 min | none |

**DT-Webull deliberately still runs the OLD symbol list and OLD entry-cutoff settings as of 2026-09-08** — the symbol swap and entry-cutoff removal (see below) were only validated against DayTradingBot's Alpaca-sourced backtest data and deployed to DayTradingBot + DT-Bot-200. Bringing DT-Webull's `.env`/`symbols.py` in line is a pending follow-up, not an oversight to be alarmed by — just don't assume all three are running the same rules without checking `.env` first.

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

## Known bugs fixed 2026-09-08 (see `.memory/project_known_issues.md` for full detail)

- **`get_recent_bars()` stale-data bug (DayTradingBot, DT-Bot-200 via shared code, VerticalSpreadBot, DayTradingBotV2)** — an ascending-order/pagination bug caused this function to silently serve a frozen, market-open snapshot for the rest of every session since 2026-08-10, with no exceptions thrown. Fixed via `sort=desc` + local reversal. DT-Webull does **not** share this bug (different broker API, no date-range param) but has its own issues below.
- **DT-Webull crashed on every single tick since deployment** — Webull's API returns OHLCV as JSON strings (not numbers) and bars newest-first (not chronological); `signals.py`'s VWAP math assumed both were otherwise, causing a `TypeError` on every tick. Fixed via type casting + reversal in `webull.py`, plus added proper session-date filtering (Webull's API has no server-side date param, so `get_intraday_bars` now over-fetches and filters client-side to today's ET date).
- **Still open:** DT-Webull's sandbox API is actively rate-limiting (`429 Too Many Requests`) on most symbols per tick — likely why several symbols show "Not enough bars" even after the fixes above. Needs a decision: reduce call volume (share one raw fetch across the 3 derived bar calls per symbol per tick), add retry/backoff, or check a production API key's limits.
