from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SignalOut(BaseModel):
    name: str
    direction: str
    weight: int
    detail: str


class SignalEvaluationOut(BaseModel):
    symbol: str
    timeframe: str
    ts: datetime
    price: float
    score: int
    classification: str
    signals: list[SignalOut]
    support: float | None
    resistance: float | None


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int | None
    symbol: str
    timeframe: str
    ts: datetime
    score: int
    classification: str
    signals_fired: list
    price: float
    support: float | None
    resistance: float | None
    created_at: datetime


class UserCreate(BaseModel):
    username: str
    ntfy_topic: str | None = None


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    ntfy_topic: str | None
    is_active: bool
    created_at: datetime


class SymbolCreate(BaseModel):
    user_id: int
    symbol: str


class UserSymbolOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    symbol: str
    is_active: bool
    added_at: datetime


class BacktestCreate(BaseModel):
    name: str
    signal_name: str
    symbol: str
    timeframe: str
    start_date: datetime
    end_date: datetime
    stop_loss_pct: float = 0.05
    profit_target_pct: float = 0.10
    max_hold_bars: int = 20


class BacktestTradeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    symbol: str
    entry_ts: datetime
    entry_price: float
    exit_ts: datetime | None
    exit_price: float | None
    pnl: float | None
    pnl_pct: float | None
    hold_time_hours: float | None
    exit_reason: str | None


class BacktestRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    strategy: str
    symbols: list
    timeframe: str
    start_date: datetime
    end_date: datetime
    params: dict
    win_rate: float | None
    cagr: float | None
    sharpe: float | None
    sortino: float | None
    max_drawdown: float | None
    profit_factor: float | None
    avg_hold_time_hours: float | None
    report_path: str | None
    created_at: datetime


class BacktestRunDetailOut(BacktestRunOut):
    trades: list[BacktestTradeOut]
