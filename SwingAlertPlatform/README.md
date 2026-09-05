# SwingAlertPlatform

A multi-user swing/position-trading alert platform: pulls market data from Alpaca, computes
technical indicators, evaluates a pluggable library of signals into a single score, sends rich
NTFY notifications when something worth acting on happens, and can backtest any of those signals
against cached history. It is **not** an intraday scalping tool and it **never places orders** —
this is analysis and alerting only.

Independent of the rest of this repo's cron-based bots: this is a real service (Postgres +
FastAPI + a background scheduler), deployed as its own Docker Compose stack.

## Architecture

```
Alpaca (market data)
   │
   ▼
data/           bar cache — pulls new bars, de-dupes on (symbol, timeframe, ts)
   │
   ▼
indicators/     EMA13/21/50/200, RSI14, MACD, ATR14, RVOL, OBV, Bollinger Bands (pandas-ta)
   │
   ▼
signals/        15 pluggable Signal classes (registry-based), each one-directional
   │
   ▼
scoring/        sums fired signals' weights into a score in [-12, +12] -> classification
   │
   ▼
alerts/         dedup/cooldown -> NTFY (global topic + every subscribed user's topic)
   │
scheduler/      ties the above into one job, run every 5 minutes (APScheduler, in-process)

backtesting/    replays cached bars through the SAME Signal classes used live —
                no separate "backtest version" of a signal, so live and backtest always agree

api/            FastAPI routers: /users /symbols /alerts /signals /backtests /health
database/       SQLAlchemy models + Alembic migrations (Postgres)
```

## Quick start

```bash
cd docker
cp ../.env.example ../.env   # then fill in ALPACA_API_KEY / ALPACA_SECRET_KEY at minimum
docker compose up -d
curl http://localhost:8000/health
```

Migrations run automatically on container start (`alembic upgrade head`, see `docker/Dockerfile`).
The scheduler starts in the same process as the API — no separate worker container needed.

**Create a user and watchlist:**
```bash
curl -X POST http://localhost:8000/users -H "Content-Type: application/json" \
  -d '{"username": "alice", "ntfy_topic": "swing-alerts-alice"}'

curl -X POST http://localhost:8000/symbols -H "Content-Type: application/json" \
  -d '{"user_id": 1, "symbol": "SPY"}'
```

From then on, every 5 minutes the scheduler syncs bars/indicators for every watched symbol (plus
SPY, always, for relative-strength comparisons) and alerts anyone subscribed when a symbol's score
crosses into Buy/Strong Buy/Sell/Strong Sell.

## Configuration (`.env`)

See `.env.example` for the full list with defaults. The ones you actually need to set:

| Variable | Purpose |
|---|---|
| `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` | Market data only — this platform never trades, so a paper or data-only key is fine |
| `NTFY_GLOBAL_TOPIC` / `NTFY_USER_TOPIC_PREFIX` | Where alerts get posted (public ntfy.sh by default) |
| `DEFAULT_SYMBOLS` | Seed watchlist evaluated even with zero users configured |
| `ALERT_COOLDOWN_MINUTES` | How long before the same (symbol, timeframe, classification) can alert again |
| `SCORE_STRONG_BUY` / `SCORE_BUY` / `SCORE_SELL` / `SCORE_STRONG_SELL` | Classification thresholds against the [-12, +12] score |

## Subscribing to alerts (NTFY)

Install the [ntfy app](https://ntfy.sh/) (iOS/Android/web) and subscribe to:
- `swing-alerts-global` — every alert-worthy signal on any tracked symbol
- `swing-alerts-<your-username>` — only symbols on your own watchlist

No account or API key needed for the public ntfy.sh server; self-hosting is a config-only swap of
`NTFY_BASE_URL` if you'd rather run your own instance.

## Adding a new signal

Signals are pluggable — add a file under `app/signals/library/`, subclass `Signal`, and register it:

```python
from app.signals.base import Direction, Signal, SignalResult
from app.signals.registry import register_signal

@register_signal
class MyNewSignal(Signal):
    name = "My New Signal"     # must be unique across the registry
    weight = 1                  # 1 = momentum/confirmation tier, 2 = trend/regime tier

    def evaluate(self, df, context=None):
        # df is ascending-by-ts bars + every IndicatorValue column, through the current bar.
        # Return a SignalResult if it fires on the LAST row, else None.
        # A Signal is one-directional by design — this one should only ever return BULLISH,
        # or only ever BEARISH, never both (see app/backtesting/engine.py's docstring for why).
        curr = df.iloc[-1]
        if curr["rsi14"] > 80:
            return SignalResult(self.name, Direction.BEARISH, self.weight, f"RSI={curr['rsi14']:.1f} extremely overbought")
        return None
```

Then import the new module from `app/signals/library/__init__.py` so the `@register_signal`
decorator actually runs. It's immediately available to live evaluation, `/signals`, and
`/backtests` — no other wiring needed.

## Example strategies / backtesting

Run any registered signal as a standalone long-only strategy (exits via stop-loss, profit-target,
or max-hold — see "Adding a new signal" above for why exits can't wait for a signal reversal):

```bash
curl -X POST http://localhost:8000/backtests -H "Content-Type: application/json" -d '{
  "name": "spy-golden-cross-2023",
  "signal_name": "EMA50/200 Golden Cross",
  "symbol": "SPY",
  "timeframe": "1Day",
  "start_date": "2023-01-01T00:00:00Z",
  "end_date": "2024-01-01T00:00:00Z",
  "stop_loss_pct": 0.08,
  "profit_target_pct": 0.20,
  "max_hold_bars": 60
}'
```

The response includes every computed metric (win rate, CAGR, Sharpe, Sortino, max drawdown, profit
factor, avg hold time) plus the individual trades. `report_path` points at a generated equity-curve
+ drawdown PNG and a JSON summary, both persisted in the `reports_data` Docker volume.

Other signals worth trying: `"MACD Bull Cross"`, `"RSI Recovery"`, `"Volatility Squeeze Breakout"`,
`"High Relative Volume"` — any name from `app/signals/library/`.

## API reference

| Endpoint | Purpose |
|---|---|
| `GET /health` | Liveness + DB connectivity |
| `POST /users`, `GET /users`, `GET /users/{id}` | User management |
| `POST /symbols`, `GET /symbols?user_id=`, `DELETE /symbols/{symbol}?user_id=` | Per-user watchlist |
| `GET /signals/{symbol}?timeframe=1Day` | On-demand signal evaluation + score for a symbol (requires cached bars — the scheduler populates this automatically for watched symbols) |
| `GET /alerts?symbol=&user_id=&since=` | Alert history |
| `POST /backtests`, `GET /backtests`, `GET /backtests/{id}` | Run and inspect backtests |

## Testing

```bash
docker compose -f docker/docker-compose.yaml run --rm api pytest tests/ -v
```

Unit tests cover indicators, every signal class, scoring, dedup/cooldown, the backtest engine, and
metrics (the metrics tests hand-verify Sharpe/Sortino/CAGR/max-drawdown against independently
computed expected values, not just internal consistency). Integration tests cover bar ingestion
against a mocked Alpaca response, the scheduler's evaluate-and-alert path, and every API router
against a throwaway SQLite database.

## Deployment notes

- Timeframes cached: 5Min, 15Min, 1Hour, 4Hour, 1Day (`app/data/timeframes.py`).
- Postgres data and generated backtest reports live in named Docker volumes (`db_data`,
  `reports_data`) — `docker compose down` (without `-v`) preserves both across restarts.
- The scheduler is a thread-based `BackgroundScheduler`, not `AsyncIOScheduler` — deliberately, since
  a sync cycle does blocking HTTP + DB work that would otherwise stall every API request while it ran.
- No API authentication in this version — fine on a private network; `app/api/deps.py` is the natural
  place to add an API-key dependency later without restructuring anything.
