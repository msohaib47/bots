from enum import Enum


class Timeframe(str, Enum):
    MIN_5 = "5Min"
    MIN_15 = "15Min"
    HOUR_1 = "1Hour"
    HOUR_4 = "4Hour"
    DAY_1 = "1Day"


# Alpaca's /v2/stocks/{symbol}/bars `timeframe` query param accepts these strings directly —
# no resampling needed, including "4Hour" (confirmed working against this account's crypto bot).
ALPACA_TIMEFRAME = {
    Timeframe.MIN_5: "5Min",
    Timeframe.MIN_15: "15Min",
    Timeframe.HOUR_1: "1Hour",
    Timeframe.HOUR_4: "4Hour",
    Timeframe.DAY_1: "1Day",
}

ALL_TIMEFRAMES = list(Timeframe)
