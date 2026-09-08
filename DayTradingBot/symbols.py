# DayTradingBot — symbols to trade
# Add or remove symbols here. One per line for readability.
#
# Updated 2026-09-08: dropped IWM/QQQ/INTC, added AMZN/GOOG/AMD after an
# August/$200-seed backtest comparison (same 12:00 ET cutoff for both sets)
# showed the new set roughly tripled total P&L ($4,550 -> $14,920) with a
# higher win rate (30.0% -> 45.0%) -- GOOG/META/TSLA were the standout
# performers, AMD/QQQ/INTC/IWM were flat-to-negative. Combined with the
# NO_NEW_ENTRY_TIME removal in config.py, the same August window went to
# +$19,668 (129 trades, 46.5% win rate). See TRADING_RULES.md.

SYMBOLS = [
    'SPY',
    'TSLA',
    'NVDA',
    'MSFT',
    'META',
    'AMZN',
    'GOOG',
    'AMD',
]
