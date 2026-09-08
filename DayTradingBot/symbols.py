# DayTradingBot — symbols to trade
# Add or remove symbols here. One per line for readability.
#
# Updated 2026-09-08 (third pass): swapped SPY out for QQQ + TSLA after
# re-running the 16-symbol combo sweep with re-entry + 15-min cooldown
# properly modeled (both landed the same day -- see DAYTRADING_RULES.md).
# SPY completely flipped from the prior combo's linchpin to a standalone
# loser (-$2,565 unconstrained) once re-entry was allowed -- its old
# advantage was specifically its LOW signal frequency (barely competing for
# cash), which stopped mattering once every symbol could re-enter through
# the day. QQQ flipped the opposite way: previously excluded as a standalone
# loser (-$960), now one of the top performers (+$11,088 unconstrained, 59
# trades) -- its frequent, smaller moves suit a re-entry-capable strategy.
# Winning 5-symbol combo (Aug1-Sep8, $200 cash, real cash-constrained
# sizing): +$35,086, beating every 4-symbol combo tested and every other
# candidate 5th symbol (AAPL/AMD both hurt the combo via cash competition,
# same pattern as before -- see DAYTRADING_RULES.md's full sweep).

SYMBOLS = [
    'META',
    'GOOG',
    'QQQ',
    'MSFT',
    'TSLA',
]
