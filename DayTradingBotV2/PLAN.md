# DayTradingBot v2: streaming Signal / Sizing / Execution / Notifier services

## Context

DayTradingBot currently runs as one monolithic process (`bot.py`) on a 1-minute cron tick, polling REST bars and trading a fixed symbol list against a single Alpaca account whose credentials are loaded once into module-level globals in `config.py`. This plan redesigns it as four cooperating long-running services, driven by three requirements:

1. **Multi-account trading** — two paper accounts today ("10k-account", "Start-at-200", ~$200), each needing its own balance-appropriate risk limits; the current single-`.env`/single-global-config design can't run both at once.
2. **Separation of concerns** — a signal generator (account-independent), a sizing/risk decision-maker (account-aware, one instance per account), and an executor (per-account order placement/position management).
3. **Streaming, not polling** — the defining decision of this v2 pass. Prices come from Alpaca's WebSocket stream instead of REST bar polling, and the moment a signal fires, it must reach Sizing → Execution → Notifier **immediately**, not on the next 1-minute cron tick. An earlier version of this plan used cron + a polled SQLite cache; that entire model is superseded here because polling fundamentally cannot deliver "instant," no matter how short the poll interval — this is a genuine push/pub-sub requirement.

**Direct consequence of "instant":** a ZeroMQ `SUB` socket only receives messages while it's connected and running — there is no cron-friendly "wake up, check once, exit" version of a subscriber. So this redesign converts **all four services into long-running daemons** supervised by systemd, not one-shot cron scripts. This is a first for this repo (every existing bot is a short-lived cron-invoked script) and is called out explicitly rather than glossed over — it changes how these processes are deployed, restarted, and monitored.

**Transport decision (made with the user):** ZeroMQ (`pyzmq`) for inter-process push — a library, not a broker process. No new standalone service to install/run/monitor, unlike Redis pub/sub or Redis Streams (the alternative considered and declined) — each service just opens direct sockets over localhost. The real trade-off, accepted explicitly: ZMQ PUB/SUB has **no persistence** — a subscriber that isn't connected at publish time simply never sees that message, with no replay. This is fine for steady-state (every subscriber is a long-running daemon that's normally connected), but it means every service needs its own way to **bootstrap current state on startup/restart** rather than assuming the stream will backfill it. The SQLite-backed store from the earlier plan is repurposed for exactly this: not the primary transport anymore, but the snapshot/history a freshly-started or reconnecting service reads once before it starts consuming the live stream.

## Process model: four systemd-supervised daemons, not cron

| Service | What keeps it running | Why it must be long-lived |
|---|---|---|
| Signal | Holds an open Alpaca WebSocket connection (price stream) | Can't react to streaming ticks if it exits between polls |
| Sizing (per account) | Holds an open ZMQ `SUB` socket to the signal stream + its account's execution stream | A `SUB` socket only receives while connected |
| Execution (per account) | Holds `SUB` sockets to its sizing decisions + the signal stream, plus its own Alpaca connectivity for order placement/position management | Same reason, plus needs to keep managing open positions continuously |
| Notifier | Holds `SUB` sockets to every producer's event stream | Same reason |

All four move from crontab to **systemd unit files** (`~/.config/systemd/user/` or system-level via `sohaib`'s sudo access, per this repo's existing sudo convention — exact placement is an implementation detail, not a design decision) with `Restart=on-failure` so a crash gets the process back up automatically, and `WantedBy=multi-user.target` (or an equivalent boot-start) so they survive a server reboot without a human re-launching them. This is a materially bigger operational commitment than the cron-script convention every other bot in this repo uses today — worth the user's explicit awareness, not just an implementation footnote.

## Streaming price ingestion (Signal service)

The Signal service replaces `alpaca.get_recent_bars()`/`get_5min_bars()` REST polling with Alpaca's market-data WebSocket (`wss://stream.data.alpaca.markets/v2/iex`, matching the IEX feed already used everywhere else in this bot — see `.memory` notes on real-time-but-single-exchange IEX vs SIP).

**Implementation note (found during Stage 0b, not anticipated in the original design):** `alpaca-trade-api`'s `Stream` class (the obvious off-the-shelf choice) calls `websockets.connect(..., extra_headers=...)`, a parameter removed in the `websockets` version actually installed (16.0) — confirmed by a direct connection test, not assumed. Rather than pin a fragile, unmaintained SDK version, `DaySignalService/ws_client.py` hand-rolls a minimal client against Alpaca's plain-JSON stream protocol (connect → `{"action":"auth",...}` → `{"action":"subscribe","bars":[...]}` → JSON array messages), using only the `websockets` library directly. Verified live: connects, authenticates, and receives a subscription ack against the real Alpaca endpoint.

Concretely:

- Subscribe to the **minute-bar channel** for all `SYMBOLS` (Alpaca streams 1-minute bars natively; there's no 5-minute streaming channel, so 5-minute and 15-minute bars are built by aggregating the incoming 1-minute bars in-memory — a rolling deque per symbol, re-aggregated into 5m/15m windows each time a new 1m bar arrives). This replaces `get_recent_bars`'s REST-fetched multi-session window with an in-memory rolling buffer seeded once at startup (see "Cold-start / warm-up" below) and then extended forever by the stream.
- On every new aggregated 5-minute bar close, recompute `signals.get_signal()`-equivalent logic (unchanged math — EMA/RSI/ADX/ATR/VWAP/15m-trend — just fed from the in-memory buffer instead of a fresh REST call) for that symbol.
- **Websocket reconnect-with-backoff is required, not optional** — a dropped connection with no reconnect logic silently stops the entire pipeline. Standard exponential backoff (1s, 2s, 4s... capped) with a resubscribe on reconnect, and a heartbeat/liveness check (see "Failure handling") so downstream services can tell "market is just quiet" from "the signal service's WS connection died."

**Cold-start / warm-up:** on startup, before the WS stream can supply enough history, seed the rolling buffers with one REST call to `get_recent_bars()` (same call the old design used) — this is the one place a REST call remains, purely for bootstrapping a fresh process, not for steady-state operation.

**Open item, not designed in detail here:** the user also wants to move option-price checking (for stop-loss/trailing/scale-out on open positions) to streaming — Alpaca does offer an options WebSocket feed, but this account's actual entitlement to it hasn't been verified (we've only confirmed historical/snapshot options data access works, not streaming). Execution's exit-management loop should keep using the current snapshot-based `get_snapshots_by_symbols()` approach, polled on a short interval (e.g. every few seconds inside its own event loop, not tied to any external tick) until that entitlement is checked — flagged as a fast-follow, not blocking this plan.

## Inter-process transport: ZeroMQ PUB/SUB, with SQLite as the cold-start snapshot

**Topology** (all localhost, no external network exposure):

```
Signal service          binds tcp://127.0.0.1:5556  (PUB: per-symbol signal/state updates + edge-triggered events)

Sizing, per account      SUBs to :5556 (signal stream)
                         SUBs to its own account's execution PUB port (account snapshot updates)
                         binds its own PUB port (sizing decisions) -> that account's execution SUBs to it

Execution, per account   SUBs to its own account's sizing PUB port (sizing decisions)
                         SUBs to :5556 (signal stream, for the cooldown-reset check's live indicators)
                         binds its own PUB port (account snapshot updates + trade/alert events)
                         -> consumed by: its own account's sizing (above) and the Notifier (below)

Notifier                SUBs to :5556 (signal_triggered events)
                         SUBs to every account's execution PUB port (trade_open/close/partial_close, alert events)
```

Port allocation: `5556` fixed for Signal; each account gets two ports, `base_port + account_index*10` for sizing and `+1` for execution (e.g. account 0 = 5560/5561, account 1 = 5570/5571) — documented in one place (`DayTradingBotV2/shared/ports.py`) so adding an account doesn't risk a collision.

**Message shape:** every ZMQ message is `topic_string` + a JSON payload, matching a pattern like `signal.SPY {"signal":"CALL",...}` — topics let a subscriber filter at the socket level (ZMQ's native `SUB` filtering) rather than receiving everything and discarding.

**Two kinds of messages, same as the polling design's two SQLite tables, now delivered as pushes instead of polled reads:**
- **State updates** (continuous) — published on every new 5-minute bar close per symbol, an account's every position-management tick, etc. Same field shapes as the earlier plan's `signal:latest`/`account:<account>:snapshot` keys. Subscribers keep an **in-memory mirror**, updated on each message, rather than re-fetching — this is what "long-running daemon" buys you that a cron script couldn't do.
- **Discrete events** (edge-triggered) — `signal_triggered`, `trade_open`/`trade_close`/`trade_partial_close`, `alert` — same shapes as before, published once per occurrence.

**SQLite's role now — cold-start snapshot and durable audit trail, not the transport:** every PUB message is also written to the same `DayTradingBotV2/shared/kv_store.py` (**built and verified** — see Migration plan; state updates → the `kv` table with a long-ish TTL just as a safety net; discrete events → the append-only `events` table). A service that starts fresh (or reconnects after being down) does one `kv_store.get()`/`read_events_since()` call to load current state / catch up on anything it missed, **then** starts consuming the live ZMQ stream for everything after that point. This is the standard "snapshot + delta stream" pattern.

## Ownership of exit management, cooldowns, and notifications (mostly unchanged in spirit, updated for push)

**Execution still manages stop-loss/trailing/scale-out autonomously**, independent of sizing — this doesn't change with streaming, it's still pure position-state logic keyed off current option prices. What changes: instead of a once-a-minute check, execution's own event loop can re-check on a much shorter interval (bounded by the snapshot-polling cadence noted above, or by an options stream once that's verified) — a direct latency improvement that falls out of the redesign rather than something that had to be separately engineered.

**Cooldown-reset** now reacts to the live `signal.*` state stream directly (execution's in-memory mirror of each symbol's price/vwap/ema9/ema21, updated every time a state message arrives) instead of a per-tick `kv_store.get()` — same logic (`update_cooldown_reset`), just fed continuously instead of once a minute.

**Notifications**: same centralization goal as before — every `notify()` call happens in exactly one place, `notifier_service.py` — but now delivery is push-driven: the Notifier's `SUB` sockets receive `signal_triggered`/`trade_*`/`alert` events the instant they're published, instead of waiting up to ~20s for its cron slot. On startup/reconnect it calls `read_events_since(cursor)` once to catch up on anything published while it was down, then switches to live consumption.

## Failure handling: heartbeats, since ZMQ SUB has no built-in liveness signal

A `SUB` socket receiving nothing doesn't tell you whether "nothing happened" or "the publisher died" — this needs an explicit heartbeat:

- Every `PUB`-ing service publishes a `heartbeat` message on its own topic every ~10s, even when it has nothing else to say.
- Every `SUB`-ing service tracks the last heartbeat time per publisher it depends on; if none arrives for ~30s, that dependency is treated as down: log a warning, append an `alert` event (own local write — this doesn't depend on the thing that's actually down), and fall back to degraded behavior identical to the polling design's staleness handling (sizing still produces a decision with `no_new_entries: true`; execution never stops managing its own open positions regardless of any upstream dependency's health).
- Reconnection is automatic for both the Alpaca WS (Signal service, exponential backoff) and ZMQ sockets (ZMQ's own socket semantics reconnect automatically once the peer is back — the heartbeat is purely for *detecting* the outage, not for re-establishing the connection).

## Confirmed non-issue: `LAST_ERROR_CODE` global

`alpaca.buy_option()` sets a module-global for PDT detection, read immediately after by whichever code calls it. Order placement and the PDT check both stay inside `execution_service.py`, one process — unaffected by moving to a long-running event-loop shape.

## Migration plan (staged, monolith stays as fallback throughout)

**Stage 0a — `DayTradingBotV2/shared/kv_store.py` — DONE.** The cold-start snapshot/event-log module. Built and unit-verified standalone (set/get/expiry round-trip, append_event/read_events_since cursor behavior all confirmed) — everything under this plan lives in the new `DayTradingBotV2/` top-level directory, kept fully separate from `DayTradingBot/` (v1) and from the cross-bot `common/` package (this module is v2-specific, not something WheelBot/CryptoBot/etc. should import).

**Stage 0b — Signal service, streaming ingestion only, no ZMQ yet — IN PROGRESS.** Built: `bar_engine.py` (rolling 1-min buffers + time-bucketed 5m/15m aggregation, unit-verified against hand-computed OHLCV), `signal_math.py` (v1's indicator math copied unchanged, restructured to take bar lists directly), `ws_client.py` (hand-rolled WS client, live-verified: connects/authenticates/subscribes against real Alpaca), `alpaca_market.py` (REST cold-start; also fixed a latent UTC-vs-ET "today" bug in the session-bars date calc — harmless in v1 since it's cron-gated to market hours, but a real bug for v2's always-on daemon crossing UTC midnight), `signal_service.py` (orchestrates cold-start → stream → recompute → `kv_store` writes + edge-triggered `signal_triggered` events). Verified end-to-end via `--once` mode (real REST cold-start, 600 bars/symbol) and a live WS smoke test. **Still needed before this stage is done:** a live run during actual market hours to diff computed signals against `bot.py`'s REST-polled equivalent for the same symbols/times (session is currently outside market hours, so this specific comparison hasn't been possible yet).

**Stage 0c — ZMQ pub/sub — DONE.** Built `shared/ports.py` (port-allocation table) and `shared/pubsub.py` (`Publisher`/`Subscriber` wrappers: multipart `[topic, json]` messages, ZMQ-native topic-prefix filtering, per-source heartbeat topics `heartbeat.<source>` so a subscriber connected to multiple publishers can track each one's liveness independently). Wired into `signal_service.py`: state updates on `signal.<SYMBOL>`, edge-triggers on `event.signal_triggered`, heartbeat on `heartbeat.signal` every 10s. `DaySignalService/_test_subscriber.py` (throwaway) verified live against the real running service: topic filtering works, heartbeat liveness tracking works, and a subscriber started fresh correctly shows `alive: False` until the first heartbeat arrives then `True` continuously. Deleted 2026-09-06 once `sizing_service.py`/`execution_service.py` were confirmed exercising the same subscription path in the Stage 1 integration test.

**Real bug found and fixed during this stage:** signals only recompute every ~5 minutes (one new bar close), but the original TTL (90s, carried over from the old cron-based plan's assumptions) was shorter than that gap — so `signal:latest` would look "expired" to any reader between two perfectly normal updates, not just on genuine staleness. Fixed by decoupling the two: TTL is now derived from the heartbeat interval (`4 × HEARTBEAT_INTERVAL_SECONDS` = 40s) instead of the recomputation cadence, and `refresh_kv_snapshot()` re-persists the last-known full state on *every* heartbeat tick (every 10s), not just when a symbol's signal actually changes. Verified directly: the snapshot survives repeated heartbeat-driven refreshes indefinitely, and would correctly expire within ~40s of the service actually dying (heartbeats stopping) rather than sitting stale-but-undetected for many minutes.

**Stage 1 — one account only (Start-at-200) — IN PROGRESS.** Added `alpaca_options.py` to `DaySignalService/` (ATM contract lookup, centralized per the design — was missing from the Stage 0b/0c build; `signal.<SYMBOL>` and `event.signal_triggered` payloads now both carry the full `contract` object, live-verified against the real options endpoint). Built `DayTradingExecution/` (shared code, one copy, run from any account's thin directory): `config.py`, `alpaca.py` (account-specific: get_account/buy_option/close_option_position/snapshots), `position_manager.py` (ported from v1 unchanged except `notify()` calls replaced with `kv_store.append_event('trade_close'/'trade_partial_close', ...)` — unit-verified to produce identical numbers to the v1 test), `sizing_service.py` (event-driven: reacts to `event.signal_triggered`, applies this account's risk gates against its own latest snapshot, publishes a `decision.<SYMBOL>` per opportunity evaluated rather than v1's batched per-tick list), `execution_service.py` (owns all state; stop/trailing/scale-out on a 5s loop — independent of everything else; reacts to `decision.<SYMBOL>` for new entries and `signal.<SYMBOL>` for cooldown-reset; publishes `account.snapshot` + heartbeat).

**Real bug found and fixed during this stage:** `python-dotenv`'s bare `load_dotenv()` searches from the `__main__` script's own directory, walking upward -- NOT the process's CWD. Since `execution_service.py`/`sizing_service.py` live in `DayTradingExecution/` (a *sibling* of every account directory, not an ancestor), running them the way the real wrapper scripts do (`cd` into the account dir, then invoke the shared script by full path) silently found no `.env` at all and fell through with no credentials -- confirmed live: a `--status` run showed the wrong account name (falling back to the CWD directory's basename) with no visible error. This directly undermined the plan's core "same code, different CWD, zero duplication" premise. Fixed by passing `dotenv_path=os.path.join(os.getcwd(), '.env')` explicitly in both `DayTradingExecution/config.py` and `DaySignalService/config.py`, forcing genuine CWD-based resolution. Re-verified against the real Start-at-200 account via the actual full-path invocation (matching how cron/systemd will call it, not a `python -c` shortcut): correct account name, correct `$200` cash/portfolio pulled live from Alpaca.

**Incident (resolved) -- a real ntfy notification was sent by accident during testing.** An isolated logic test of `ExecutionEngine.on_decision()` (Alpaca order call stubbed, no real order placed) left a fake `trade_open` event sitting in the shared `ipc_store.db` event log. Starting the real `notifier_service.py` shortly after for a genuine integration test picked that stale event up via its catch-up poll and sent an actual push to the user's live ntfy topic with fabricated data. No real trade occurred. All background processes were stopped immediately and the user was told in the same turn. **Standing rule going forward, now baked into the test procedure below:** always confirm `kv_store.read_events_since(0) == []` and `kv_store.get('signal:latest') is None` before starting the real `notifier_service.py` in any test, and never run it alongside ad-hoc/stubbed scripts that write to the same shared event log.

**Multi-daemon interop -- verified 2026-09-06 (outside market hours, Saturday night/Sunday):** ran signal+sizing+execution (notifier withheld) for ~6 minutes after confirming a clean `kv_store`; both sizing and execution correctly logged "Signal service heartbeat stale/missing" only until signal's cold-start finished and its first heartbeat landed, then went silent for the rest of the run (no further warnings, no crashes) -- confirming ZMQ heartbeat-liveness detection works end to end. `execution_service.py`'s periodic `account.snapshot` publish was independently confirmed to reach `kv_store` (`account:start200:snapshot` showed the correct live $200 cash/portfolio pulled from Alpaca with clean/empty state). Re-ran with all four daemons (notifier included) after re-confirming `kv_store` was empty: notifier started from `cursor=0`, found nothing to catch up on (correctly silent, no spurious notification this time), and all four ran ~1 minute with heartbeats stabilizing the same way and zero events logged -- expected, since it was outside market hours so no 1-minute bars streamed in, no 5-minute bar boundary was ever crossed, and `_recompute()` correctly never ran (staying quiet with nothing to report, rather than fabricating stale output). All test processes stopped and all state (`ipc_store.db`, `cursor.json`, `positions.json`, `trades.csv`, logs, `__pycache__`) wiped clean afterward. **Still not verified: real signal-to-notification behavior during actual market hours** (this test proves the plumbing; it can't prove correctness of live signal computation, sizing decisions, or order placement until the market is open).

**Also noted, not yet fixed (low priority):** `alpaca_market.get_session_bars()` logs an `ERROR`-level "400 Client Error" for every symbol whenever cold-start runs before the current ET trading session has opened (e.g. testing on a weekend/pre-market) -- Alpaca appears to reject a bars request whose `start` is in the future relative to now. Currently falls back to "0 session bars" and continues, so it's non-fatal, but it's noisy at `ERROR` level for what's actually an expected condition outside market hours. Not blocking; revisit if it ever produces log noise during a real trading day for a legitimate reason (e.g. a mid-session restart) rather than only pre-market.

**Deploy templates built 2026-09-06** (`DayTradingBotV2/deploy/`): wrapper scripts (`daysignalservice.sh`, `daysizing-start200.sh`, `dayexec-start200.sh`, `daynotifierservice.sh`, matching this repo's existing per-bot wrapper-script convention) and systemd unit files for all four Start-at-200 daemons (`Restart=on-failure`, `RestartSec=5`, `WantedBy=multi-user.target`, dependency ordering via `After=`/`Requires=` so sizing/execution start after signal). `deploy/README.md` documents install/verify/rollback steps (system-level units need `sohaib`'s sudo, per repo convention) and explicitly gates actual server deployment on: (1) `kv_store` confirmed clean, (2) v1's cron entry for this account disabled first (never run both pipelines against the same account), (3) enabling shortly before a market open rather than mid-session. **Not yet deployed to the server** -- these are local templates only, pending the still-open market-hours verification below.

Also found: trade events (`trade_open`/`trade_close`/`trade_partial_close`) were only ever written to the durable `kv_store` log, never pushed live over ZMQ -- meaning the Notifier would only ever see them on its periodic catch-up poll, not instantly, contradicting the design. Fixed with a small `_emit_event(publisher, event_type, payload)` helper (in both `position_manager.py` and `execution_service.py`) that does both the durable write and the live push from one call site, so they can't drift apart. Live-verified: a real `check_and_update_stops()` scale-out correctly produced both the `kv_store` row and an instantly-received `event.trade_partial_close` ZMQ message.

Built `DayNotifierService/notifier_service.py` -- deliberately does NOT act on live-pushed event payloads directly; a ZMQ message is treated purely as a "something was published, poll now" wake-up signal, with the actual notification content and cursor advancement always coming from `kv_store.read_events_since()`. This is what keeps delivery exactly-once across restarts: an event is both logged and pushed from the same call site, so acting on the live push directly would double-notify once the cursor-based catch-up later replays the same event. Verified (with `notify()` mocked): all four event types format correctly (`signal_triggered`→`SIGNAL_CALL`/`SIGNAL_PUT`, `trade_open`→`OPEN`, `trade_close`→`STOP_LOSS`/`TRAILING`/`CLOSE` depending on reason, `trade_partial_close`→`SELL`, `alert`→`ERROR`), the cursor correctly prevents re-notification on a repeat poll, and cursor persistence round-trips through `cursor.json`.

**Incident during integration testing (self-inflicted, disclosed to the user):** an isolated logic test of `ExecutionEngine.on_decision()` (with `alpaca.buy_option` stubbed, no real order) left a fake `trade_open` event sitting in the shared `kv_store` event log. Minutes later, starting the real `notifier_service.py` for a genuine multi-daemon integration test picked up that stale event and sent an actual ntfy push to the user's real notification topic with the fake test data. No real trade occurred -- this was a test-hygiene failure (not clearing shared state between an isolated unit test and a real end-to-end run), not a logic bug, but a real lesson: **the shared `ipc_store.db` must be wiped before any test that starts the real `notifier_service.py`**, since unlike the other three services it has no "test mode" and will act on anything sitting in the log as if it were genuine.

Full 4-daemon integration smoke test (clean state this time) confirmed: all four processes start and bind cleanly, `sizing`/`execution` each correctly detect the signal service's ~15-20s cold-start as a stale heartbeat until it finishes (expected, not a bug -- cold-start currently blocks the heartbeat loop from starting), then correctly see it recover once cold-start completes.

**Still to build in this stage:** wrapper scripts + systemd unit files, and the full live daemon-set verification during actual market hours (3-5 days in paper trading, kill-and-restart resilience per service, signal-to-notification latency timing, a real order placed end-to-end through the new pipeline) -- credentials/wiring/event-plumbing are now proven correct in isolation and via a clean smoke test, but no live signal has fired through the real pipeline yet since testing has all been outside market hours.

**Stage 2 — second account (10k-account).** Reuses the Signal service and Notifier unchanged; new account just means a new sizing/execution daemon pair on its own ports, per the port-allocation scheme above. Also the point to settle the still-open fixed-$-vs-%-of-equity risk-limit question, and to duplicate `sync_positions.py` per account.

**Stage 3 (optional, later)** — decommission the monolith's cron entries once both accounts run reliably; `DayTradingBot/` stays as reference.

## Naming

Everything for this redesign lives under a new top-level `DayTradingBotV2/` directory — fully separate from `DayTradingBot/` (v1, kept as the rollback fallback) and from the cross-bot `common/` package.

| Purpose | Directory | Supervisor |
|---|---|---|
| Shared code (kv_store, ports) | `DayTradingBotV2/shared/` | n/a (library, not a process) |
| Signal service | `DayTradingBotV2/DaySignalService/` | systemd: `daysignalservice.service` |
| Sizing/Execution, per account | `DayTradingBotV2/DayTradingExecution/` (shared code) run from `DayTradingBotV2/DayTradingBot-<slug>/` | systemd: `daysizing-<slug>.service`, `dayexec-<slug>.service` |
| Notifier | `DayTradingBotV2/DayNotifierService/` | systemd: `daynotifierservice.service` |
| Monolith (kept, disabled) | `DayTradingBot/` (v1, unchanged) | cron (existing), disabled once superseded |

## Open items deliberately left for later (not blocking this plan)

- **Fixed-$ vs %-of-equity risk limits per account** — unchanged from earlier discussion, still undecided.
- **Options streaming entitlement** — verify before attempting to move execution's exit-price checks off snapshot polling.
- **systemd unit placement** (user-level vs system-level services, exact restart/backoff policy tuning) — an implementation detail once this plan is approved, not a design fork.
- **ZMQ port allocation scheme durability** — the `base_port + account_index*10` scheme works for a handful of accounts; if this ever needs to scale to many more, a proper service-discovery mechanism would be worth revisiting (not needed at 2-3 accounts).
- **`sync_positions.py` per-account duplication** — same follow-up as the earlier plan, unchanged.
- **Events-table retention** in `kv_store.py`'s SQLite file — still relevant even though it's now the cold-start path rather than the primary transport; same "revisit later, not urgent at this volume" as before.

## Critical files

- `DayTradingBot/bot.py`, `position_manager.py`, `alpaca.py`, `signals.py`, `config.py` (v1, read-only reference) — source the split is built from; `signals.py`'s indicator math (EMA/RSI/ADX/ATR/VWAP) is reused as-is in `DayTradingBotV2/`, just fed from streaming-aggregated bars instead of REST-fetched ones
- `common/notifier.py`, `common/alpaca_client.py`, `common/alpaca_config.py` — cross-bot shared code the new services still depend on (imported from `DayTradingBotV2/`, not moved)
- `DayTradingBotV2/shared/kv_store.py` — **done** — cold-start snapshot/event-log module
- `DayTradingBotV2/shared/ports.py`, `DayTradingBotV2/shared/pubsub.py` — **done** (Stage 0c) — port allocation + Publisher/Subscriber wrappers
- `DayTradingBotV2/DaySignalService/{bar_engine,signal_math,ws_client,alpaca_market,alpaca_options,config,symbols,signal_service}.py` — **Stage 0b/0c/1, built** — see Migration plan for status and the market-hours verification still pending
- `DayTradingBotV2/requirements.txt` — deliberately excludes `alpaca-trade-api` (see Streaming price ingestion note); includes `pyzmq`
- `DayTradingBot/sync_positions.py` (v1) — needs a `DayTradingBotV2`-side per-account equivalent before Stage 2
- `.memory/project_known_issues.md`, `.memory/project_bots_overview.md`, `.memory/project_trading_server.md` — deploy chain, cron/sudo conventions; note this plan is the first to need systemd units rather than crontab entries, so these files' guidance covers credentials/deploy-path conventions but not process supervision — that part is new ground for this repo

## Verification

- Stage 0a: `kv_store` set/get/expiry and append_event/read_events_since round-trips, as in the earlier plan.
- Stage 0b: run the streaming Signal service manually during market hours; compare its computed signal/indicator values against `bot.py`'s REST-polled values for the same symbol at the same wall-clock moment — confirms the in-memory bar aggregation produces equivalent bars to the REST endpoint before trusting it.
- Stage 0c: start a throwaway ZMQ subscriber before and after the Signal service is running, confirm both bootstrap correctly and receive live updates with no duplicates; kill and restart the Signal service, confirm heartbeat loss is detected within the expected window and recovery is clean.
- Stage 1: run all four Start-at-200 daemons together in paper trading for 3-5 full trading days with the monolith's cron disabled for that account. Specifically time-stamp a signal trigger through to its ntfy notification arriving, to confirm the latency win is real (should be low seconds, not up to a minute). Kill each of the four services one at a time mid-session and confirm systemd restarts it and it recovers state correctly with no duplicate orders or missed stop-losses.
- Stage 2: repeat the Stage 1 verification window for the second account.
