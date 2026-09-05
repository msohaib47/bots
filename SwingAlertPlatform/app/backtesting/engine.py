from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from app.data.timeframes import Timeframe
from app.signals.base import Direction
from app.signals.registry import SIGNAL_REGISTRY

MIN_BARS_BEFORE_EVALUATING = 2  # crossover-style signals need at least a prior bar to compare against


@dataclass
class BacktestConfig:
    signal_name: str
    symbol: str
    timeframe: Timeframe
    start_date: datetime
    end_date: datetime
    stop_loss_pct: float = 0.05
    profit_target_pct: float = 0.10
    max_hold_bars: int = 20


@dataclass
class TradeResult:
    symbol: str
    entry_ts: datetime
    entry_price: float
    exit_ts: datetime
    exit_price: float
    exit_reason: str
    hold_bars: int

    @property
    def pnl(self) -> float:
        return self.exit_price - self.entry_price

    @property
    def pnl_pct(self) -> float:
        return (self.exit_price - self.entry_price) / self.entry_price


@dataclass
class _OpenPosition:
    entry_ts: datetime
    entry_price: float
    entry_idx: int


def run_backtest(df: pd.DataFrame, config: BacktestConfig) -> list[TradeResult]:
    """Replay `df` (ascending bars+indicators, already filtered to the backtest window) bar by
    bar using the live `signal_name`'s own Signal class as the entry trigger. A signal detected
    using data through bar i is acted on at bar i+1's open — the same information a live run
    would actually have at decision time, so there's no lookahead.

    Exits are governed entirely by stop_loss_pct / profit_target_pct / max_hold_bars, not by
    waiting for the entry signal to "reverse" — every Signal subclass is one-directional by
    design (e.g. Ema1321BullCross can only ever return BULLISH), so a signal can never itself
    produce the opposite-direction result needed to trigger a signal-based exit.
    """
    if config.signal_name not in SIGNAL_REGISTRY:
        raise ValueError(f"Unknown signal: {config.signal_name}")
    signal = SIGNAL_REGISTRY[config.signal_name]()

    trades: list[TradeResult] = []
    position: _OpenPosition | None = None
    pending: str | None = None  # "enter" or an exit reason, decided at bar i-1, executed at bar i's open

    for i in range(len(df)):
        curr = df.iloc[i]

        if pending == "enter" and position is None:
            position = _OpenPosition(entry_ts=curr["ts"], entry_price=curr["open"], entry_idx=i)
        elif pending is not None and position is not None:
            trades.append(
                TradeResult(
                    symbol=config.symbol,
                    entry_ts=position.entry_ts,
                    entry_price=position.entry_price,
                    exit_ts=curr["ts"],
                    exit_price=curr["open"],
                    exit_reason=pending,
                    hold_bars=i - position.entry_idx,
                )
            )
            position = None
        pending = None

        if i < MIN_BARS_BEFORE_EVALUATING:
            continue

        window = df.iloc[: i + 1]
        result = signal.evaluate(window)

        if position is None:
            if result is not None and result.direction == Direction.BULLISH:
                pending = "enter"
        else:
            pnl_pct = (curr["close"] - position.entry_price) / position.entry_price
            bars_held = i - position.entry_idx
            if pnl_pct <= -config.stop_loss_pct:
                pending = "stop_loss"
            elif pnl_pct >= config.profit_target_pct:
                pending = "profit_target"
            elif bars_held >= config.max_hold_bars - 1:
                # detected one bar early so the realized hold time (executed next-bar-open)
                # comes out to exactly max_hold_bars, not max_hold_bars + 1
                pending = "max_hold"

    if position is not None:
        curr = df.iloc[-1]
        trades.append(
            TradeResult(
                symbol=config.symbol,
                entry_ts=position.entry_ts,
                entry_price=position.entry_price,
                exit_ts=curr["ts"],
                exit_price=curr["close"],
                exit_reason="end_of_backtest",
                hold_bars=len(df) - 1 - position.entry_idx,
            )
        )

    return trades
