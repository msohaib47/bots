from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

import pandas as pd


class Direction(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"


@dataclass
class SignalResult:
    name: str
    direction: Direction
    weight: int
    detail: str

    @property
    def signed_weight(self) -> int:
        return self.weight if self.direction == Direction.BULLISH else -self.weight


class Signal(ABC):
    """A pluggable rule that evaluates the most recent bar of a (bars + indicators) frame.

    `df` is ascending by ts with OHLCV columns plus every IndicatorValue column
    (see indicators.engine.merge_bars_and_indicators). `context` carries auxiliary data
    a signal may need beyond its own symbol's series — currently just `spy_df` for
    relative-strength comparisons.
    """

    name: str
    weight: int

    @abstractmethod
    def evaluate(self, df: pd.DataFrame, context: dict | None = None) -> SignalResult | None: ...
