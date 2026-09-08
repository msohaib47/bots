# DayTradingBot — symbols to trade
# Add or remove symbols here. One per line for readability.
#
# Updated 2026-09-08 (second pass): narrowed from the 8-symbol
# SPY/TSLA/NVDA/MSFT/META/AMZN/GOOG/AMD set down to these 4, after an
# Aug1-Sep8/$200-seed sweep of ~15 candidate symbols and ~8 different 4-symbol
# combinations. META and GOOG were the standout individual performers
# (+$10,868 / +$6,256 unconstrained over the window); this specific 4-symbol
# combo won not just because of that but because SPY's comparatively low
# signal frequency barely competes for the account's limited cash (only 4
# cash-blocked entries the whole window, vs. 10-77 for every other tested
# combo), letting the account compound almost unconstrained. Result:
# $200 -> $16,400 over the window (vs. $9,253 for the next-best combo tested,
# META/GOOG/MSFT/TSLA). Also raised MAX_CONTRACTS to 10 in config.py the same
# day after finding it (not the exposure cap) was the binding constraint on
# this combo's upside. IBM/QCOM/QQQ were excluded as standalone losers;
# AAPL/AMD were excluded despite decent standalone numbers because their
# higher signal frequency actively hurt combo performance by diluting cash
# away from META/GOOG/MSFT/SPY (AAPL in particular flipped a winning
# 4-symbol combo to a net loss). See DAYTRADING_RULES.md for the full sweep.

SYMBOLS = [
    'META',
    'GOOG',
    'MSFT',
    'SPY',
]
