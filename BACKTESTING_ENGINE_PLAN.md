# Backtesting Engine — Architecture Plan

Status as of 2026-09-06. Written after reviewing the actual code of `DayTradingBot` (v1),
`DayTradingBotV2` (in-progress streaming rewrite, see its own `PLAN.md`), and `WheelBot` —
this isn't a generic backtesting-framework writeup, it's grounded in what these three
bots' code actually look like today, since they're structurally very different.

## Why this exists

`DayTradingBot/backtest.py` (v1) is a full bespoke simulator, hand-written for that one
bot. It works well — this week's rewrite made it replay exits on the real option price
path using `position_manager.py`'s actual rules, after an earlier version approximated
exits on the underlying price and was materially wrong (9 stops in 192 trades where 75
of them had actually lost >80% of premium). That fix was expensive to discover and fix
once; the goal here is to not have to rediscover it independently for every new bot.

Today: `WheelBot` has no backtest at all. `DayTradingBotV2` has no backtest at all (it's
mid-rewrite). More bots are coming. A shared engine should mean each new bot doesn't
start from zero, and — more importantly — should structurally prevent the
"backtest logic quietly drifts from live logic" failure class described above.

## Current state of each bot (as of 2026-09-06)

| Bot | Decision logic | Execution logic | Backtest |
|---|---|---|---|
| **DayTradingBot v1** | `signals.py` — pure, no broker calls | `position_manager.py`/`bot.py` — calls `alpaca.py` directly | `backtest.py`, bespoke, working, option-price-path-accurate as of this week |
| **DayTradingBot v2** | `signal_math.py` + `bar_engine.py` — already pure, explicitly designed to "take bar lists directly" (see `DayTradingBotV2/PLAN.md`) | `sizing_service.py` / `execution_service.py` — separate long-running daemons, real Alpaca calls, ZeroMQ pub/sub between them | None yet — the whole design is streaming/event-driven, which doesn't map onto "replay 6 months in 5 minutes" without new work |
| **WheelBot** | Not separated — `strategy.py`'s handlers call `alpaca.get_stock_price`, `place_option_order`, `get_order`, etc. **inline**, mixed with the decision logic | Same file, same functions | None |

## The core design question

Three places to draw the "reusable" boundary, in increasing order of ambition:

**Option A — shared data layer only.** Extract v1's fetch/cache machinery (paginated
Alpaca bar fetch, option-contract cache, option-trade-price-path cache — all three
already built and working in `backtest.py`) into a standalone module. Every bot's own
backtest script calls it instead of reimplementing rate-limited historical fetch and
local caching from scratch. Cheap, immediately useful, doesn't touch strategy logic at
all, doesn't fix the drift risk on its own.

**Option B — a generic Strategy interface + one engine drives everything.** Define a
formal interface every bot implements (`on_bar() -> Signal`, `on_signal() -> Order`,
etc.); one `BacktestEngine` drives any conforming strategy — clock, simulated broker,
risk-rule enforcement, reporting, all written once. The "textbook" answer, but these
three bots are genuinely different shapes (a 15-minute 0DTE scalper, a multi-week
mechanical options wheel, a streaming multi-daemon pipeline) — forcing one abstraction
over all three risks either being too thin to be useful or accumulating bot-specific
escape hatches that defeat the point. Highest effort, most likely to become its own
maintenance burden. Not recommended now.

**Option C — shared data + a simulated broker; each bot's real code runs unmodified
against it via dependency injection.** No new interface. A `SimulatedBroker` exposes the
same method surface a bot's own `alpaca.py` does; the bot's actual production
decision/execution code runs against it instead of the real one. This is what fixed the
v1 correctness bug this week, generalized into a pattern instead of a one-off. Moderate
effort — each bot needs a small, mechanical refactor to accept an injectable broker
instead of a hardcoded module import.

**Decision:** Option A now (this document's immediate scope), Option C next, targeted at
v2 first per the user's stated priority. Option B is deliberately not planned.

## v1 and v2 keep separate backtest engines, sharing only the data layer

Confirmed workable, and it's the right shape — data acquisition and simulation are
independent concerns:

- v1's day-by-day, one-entry-per-symbol-per-day chronological replay stays exactly as
  it is.
- v2's backtest (see below) walks bar-by-bar through the real `bar_engine.py`/
  `signal_math.py`, which is a different clock granularity and mechanic by design — it's
  validating the streaming pipeline's own aggregation, not just the strategy math.
- Neither has to change to accommodate the other. They both call the same
  `backtesting-engine/market_data.py` and `options_data.py` for historical data.

**One honest caveat on "download once, use for both":** this is fully true for **options
data** (a historical option contract selection and its trade-price tape are the same
regardless of which bot is asking — no duplication, ever) but only partly true for
**equity/underlying bars**, because v1 wants 5-minute bars and v2's `bar_engine.py` wants
native 1-minute bars. Building a "fetch 1-minute once, resample to 5-minute for v1
myself" layer was considered and **deliberately rejected for this pass** — bar
aggregation with correct session-boundary alignment is exactly the kind of thing that's
bitten this codebase before (see `.memory/project_known_issues.md`'s HTF-bars bug), and
Alpaca's bars endpoint already aggregates correctly server-side for any timeframe
requested. So `get_bars()` fetches and caches per-`(symbol, timeframe)` pair — v1 and v2
each get their own cache file per timeframe, sharing the fetch/cache *machinery*
(pagination, rate-limiting, gap-detection), not literal bytes. If a genuine need for
local resampling shows up later, it's a separate, deliberate piece of work — not bundled
in here.

## What v2's "full trading backtest" (signals + sizing + execution + P&L) actually needs

Read the real v2 service files before writing this — the architecture is already shaped
well for this:

| Class (file) | Already pure | The only real I/O |
|---|---|---|
| `SignalEngine` (`DaySignalService/signal_service.py`) | `on_bar()`/`_recompute()` — takes a bar dict, no network itself | `alpaca_options.find_atm_contract()` in `_recompute()`; `alpaca_market.get_recent_bars()`/`get_session_bars()` in `cold_start()` |
| `SizingEngine` (`DayTradingExecution/sizing_service.py`) | `_evaluate()` is **already fully pure** — symbol/contract/spot + an in-memory snapshot in, a decision dict out | none — only reads `self.snapshot`, already in-memory |
| `ExecutionEngine` (`DayTradingExecution/execution_service.py`) | `check_stops()`/`build_snapshot()` mostly logic over `self.state` | `alpaca.buy_option()`, `alpaca.close_option_position()`, `alpaca.get_account()`, `alpaca.get_snapshots_by_symbols()` |

A real bonus: v2's `position_manager.py` was **ported from v1 unchanged** (per
`DayTradingBotV2/PLAN.md`), so the exact stop/trail/scale-out math v1's backtest already
validated this week is identical in v2. The "replay an option's price path and apply
`position_manager.check_and_update_stops()`" logic v1's `backtest.py` built this week
(`_simulate_option`) is a fourth shareable piece, not just data.

### Planned pieces, once Phase 1 (this document's immediate scope) is done

**Boundary decided 2026-09-06: `backtesting-engine/` is data-only, permanently.**
Everything about *how* a specific bot simulates trading — exit math, a simulated
broker, the orchestration loop — is strategy-shaped and lives inside that bot's own
directory, not the shared package, specifically so each bot's simulation logic can
diverge and be experimented with independently without risking another bot's
already-validated backtest. This was decided when scoping v2's exit-simulation (item 2
below): v1 and v2 do **not** share one copy of the exit-sim, even though the underlying
`position_manager.py` math started out identical — v2 may deliberately try different
stop/trail/scale-out rules over time, and that must never silently change v1's numbers
or vice versa. The same reasoning was then applied consistently to the broker and the
orchestrator (items 4–5) — both are v2-specific classes/interfaces, not general-purpose.

1. **Shared data layer** — this phase, done. `backtesting-engine/market_data.py`,
   `options_data.py`. The *only* thing ever shared between v1 and v2's backtesting.
2. **v2's own exit-simulation** — `DayTradingBotV2/backtest/option_position_sim.py`. A
   v2-owned adaptation of v1's `_simulate_option` (replay a price path through
   `position_manager.check_and_update_stops()`), started from the same logic but
   **not** the same file as v1's — free to diverge.
3. **Small DI refactor, v2 only** — `DayTradingBotV2/DaySignalService/signal_service.py`
   and `DayTradingBotV2/DayTradingExecution/execution_service.py` accept an injectable
   contract-lookup + bar source (`SignalEngine`) and an injectable `broker`
   (`ExecutionEngine`), defaulting to the real modules in production. **v1
   (`DayTradingBot/`) is not touched by this or any other item here.**
   `sizing_service.py` needs no changes — `_evaluate()` is already pure.
4. **`SimulatedBroker`** — `DayTradingBotV2/backtest/simulated_broker.py`. Implements
   the same method surface as `DayTradingExecution/alpaca.py`
   (`buy_option`/`close_option_position`/`get_account`/`get_snapshots_by_symbols`),
   using item 2 for fills/exits. Lives in `DayTradingBotV2/`, not the shared package —
   it internally imports `backtesting-engine/options_data.py` for the raw historical
   option data (data shared; the broker logic that consumes it is not).
5. **Backtest orchestrator** — `DayTradingBotV2/backtest/run_backtest.py`. Walks
   historical 1-minute bars chronologically (fetched via
   `backtesting-engine/market_data.py`), feeds them into a backtest-configured
   `SignalEngine.on_bar()` (exactly the shape `ws_client` delivers live), calls
   `SizingEngine._evaluate()` directly on edge-triggers, calls
   `ExecutionEngine.on_decision()` on acceptance — all in-process, no ZMQ sockets, no
   heartbeats, no wall-clock pacing, so it runs at CPU speed instead of real time. Also
   lives in `DayTradingBotV2/` — it drives v2's actual classes directly and isn't
   reusable by v1 (already has its own working backtest) or WheelBot (different shape)
   as-is.

**Why this avoids v1's exact bug:** the real `SignalEngine`/`SizingEngine`/
`ExecutionEngine` classes run unmodified in backtest — only their I/O edges are swapped.
There's no second, hand-derived copy of the signal or risk-gate math to silently drift
out of sync with production.

**Items 2–5 are built (2026-09-06).** No v1 bot code was changed by any item on this
list. What actually got built for items 2–4 differs from the plan above in one
respect, flagged here and to the user directly — see "Deviation from the plan" below.

### What was actually built

- **Item 3 (DI refactor, v2 only):** `SignalEngine.__init__` now accepts
  `market_data=`/`options_data=` (default: the real `alpaca_market`/`alpaca_options`
  modules); `ExecutionEngine.__init__` now accepts `broker=` (default: the real
  `alpaca` module). `cold_start()`/`_recompute()`/`on_decision()`/`check_stops()`/
  `build_snapshot()` route through these instead of calling the modules directly.
  `sizing_service.py` needed no change. Files touched:
  [DaySignalService/signal_service.py](DayTradingBotV2/DaySignalService/signal_service.py),
  [DayTradingExecution/execution_service.py](DayTradingBotV2/DayTradingExecution/execution_service.py).
- **Time injection (needed for #4/#5 to be correct, not in the original 5-item list):**
  a multi-day backtest running in one process would otherwise have every
  `datetime.now(timezone.utc)` call in `position_manager.py`/`execution_service.py`
  return the *real* today, silently breaking daily-loss-cap day-rollover and
  cooldown expiry across simulated days. Added
  [shared/sim_clock.py](DayTradingBotV2/shared/sim_clock.py) (defaults to real wall
  time; only the orchestrator ever overrides it) and swapped the `datetime.now()`
  call sites in `position_manager.py`/`execution_service.py` to read it. Also added
  `position_manager.set_broker()` so its one direct Alpaca call
  (`_underlying_price()`, a diagnostic CSV column only) is overridable too.
- **Item 4 (SimulatedBroker) — in `DayTradingBotV2/`, not `backtesting-engine/`:**
  [backtest/simulated_broker.py](DayTradingBotV2/backtest/simulated_broker.py)
  implements `alpaca.py`'s method surface (`buy_option`/`close_option_position`/
  `get_account`/`get_snapshots_by_symbols`/`get_latest_price`/`LAST_ERROR_CODE`)
  over an in-memory cash ledger, reading historical option prices via
  `backtesting-engine/options_data.py`.
- **Item 5 (orchestrator) — in `DayTradingBotV2/`:**
  [backtest/run_backtest.py](DayTradingBotV2/backtest/run_backtest.py) walks a
  chronological 1-minute bar tape (via
  [backtest/backtest_market_data.py](DayTradingBotV2/backtest/backtest_market_data.py),
  which also serves as SignalEngine's injected `market_data`/`options_data`) and
  drives real `SignalEngine`/`SizingEngine`/`ExecutionEngine` instances connected by
  an `InProcessBus` (a fake `publisher` that calls the same `on_*` handlers directly
  instead of over ZMQ — all three classes already accepted an injected publisher, so
  this needed no further code changes). Each run gets its own scratch directory
  (`backtest/runs/<run_id>/`, gitignored) for `positions.json`/`trades.csv`/
  `cooldowns.json`/`kv_store`'s sqlite file, so a backtest can never touch a real
  account's live state — this also required overriding `kv_store.py`'s
  `IPC_DB_PATH` per run, since its default path is fixed next to `kv_store.py`
  itself, not CWD-relative (would otherwise have shared/locked the live daemons'
  real event-log database).

### Deviation from the plan — item #2 (exit-simulation) was NOT built as a separate file

The original item #2 was "v2 gets its own copy of the exit-simulation math,
separate from v1's, so it can be experimented with independently." That copy was
**not created**. Reasoning: once items #3–#5 exist, `ExecutionEngine.check_stops()`
already calls `position_manager.check_and_update_stops()` — v2's real, already
v1-independent stop/trail/scale-out logic — directly against `SimulatedBroker`'s
historical prices. Writing a second, forked copy of that same math into
`backtest/option_position_sim.py` would have reintroduced exactly the
drift-between-two-copies risk this whole effort exists to eliminate, for logic that
was already v2-local and never shared with v1.

**This does not fully satisfy the original ask** ("we might experiment with
different strategies") **if the intent was specifically to try alternate exit rules
in backtest without touching (or risking) the live `position_manager.py`.** Under
what was actually built, experimenting with a different stop/trail/scale-out rule
means editing `position_manager.py` directly, which is also what the live daemons
run. Flagged for the user to confirm this is acceptable, or to ask for
`option_position_sim.py` to be built after all as a true fork.

## Phase 1 — what was actually built (this pass)

New top-level folder, sibling to every bot directory and to `common/`:

```
backtesting-engine/
  _alpaca.py         # internal: one shared AlpacaClient instance, credentials via common/
  market_data.py      # get_bars(symbol, timeframe, start_date, end_date) — cached
  options_data.py      # get_option_contract(...), get_option_price_path(...), price_at(...) — cached
  requirements.txt
  README.md
  cache/
    bars/                 # <SYMBOL>_<TIMEFRAME>.json
    options_contracts/    # <UNDERLYING>.json
    options_trades/       # <OPT_SYMBOL>__<DAY>.json
```

**Deliberately not done in this phase, per instruction:**
- `DayTradingBot/cache_alpaca/` is **not** moved or touched — it's actively being
  refreshed. The user will manually copy its contents into
  `backtesting-engine/cache/{bars,options_contracts,options_trades}/` once that refresh
  finishes. The new cache folders are created now, empty, ready to receive that copy.
- No bot's code was changed. `DayTradingBot/backtest.py` still uses its own inline
  fetch/cache functions, untouched. Pointing it at the shared module is a future,
  separate, deliberate step (this document's Option A follow-through) — not done here.
- No live network call was made to verify `market_data.py`/`options_data.py` against
  the real Alpaca API in this pass: this repo's bots each keep their own `.env`, and
  there is no root-level `.env` for `common/alpaca_config.py` to find when a script in
  `backtesting-engine/` is run directly. Syntax-checked (`ast.parse` / `py_compile`)
  only. **First real use should include a live smoke test** — e.g.
  `get_bars('SPY', '5Min', start_date=..., end_date=...)` for a short, cheap range —
  before trusting the cache contents.

## Open items, deliberately left for later

- Pointing `DayTradingBot/backtest.py` at the shared data layer (mechanical, no
  behavior change expected, but should be verified against its own existing cached
  output before/after).
- Steps 2–5 above (exit-sim extraction, v2 DI refactor, `SimulatedBroker`, v2
  orchestrator) — the actual "v2 gets a full backtest" work; this document's Phase 1 is
  a prerequisite for it, not the thing itself.
- Whether `WheelBot` ever gets Option C treatment — no immediate priority stated; its
  `strategy.py` would need the same kind of "separate decide from act" refactor v2
  already has for free, but starting from a fully mixed state instead.
- Local bar resampling (1m → 5m done in-process instead of two separate Alpaca fetches)
  — deliberately deferred, see the caveat above.
