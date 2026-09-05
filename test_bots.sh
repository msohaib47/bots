#!/usr/bin/env bash
# Manual bot test runner — executes bots on the trading server via SSH.
#
# Usage:
#   ./test_bots.sh                        # status of all bots
#   ./test_bots.sh wheel                  # WheelBot status
#   ./test_bots.sh wheel --once           # WheelBot single trade pass
#   ./test_bots.sh crypto --once          # CryptoBot single trade pass
#   ./test_bots.sh copy --summary         # CopyTradingBot portfolio summary
#   ./test_bots.sh copy --once            # CopyTradingBot single pass (places trades!)
#   ./test_bots.sh day --status           # DayTradingBot positions
#   ./test_bots.sh rh --status            # RobinhoodDayTradingBot positions
#   ./test_bots.sh all --status           # all bots status
#
# Bots:  wheel | day | crypto | copy | rh | all
# Modes: --status | --once | --summary | --close
#
# NOTE: --once and --close can place real orders (paper money, but live API calls).

export PYTHONPYCACHEPREFIX=/home/claude/.pycache

PYENV_PY="$HOME/.pyenv/versions/3.12.13/bin/python3"
if [ -x "$HOME/bots-venv/bin/python3" ]; then
    PY="$HOME/bots-venv/bin/python3"
elif [ -x "$PYENV_PY" ]; then
    PY="$PYENV_PY"
else
    echo "ERROR: pyenv Python 3.12.13 not found at $PYENV_PY" >&2
    exit 1
fi

BOT="${1:-all}"
MODE="${2:-}"   # empty = use each bot's default safe mode

# ── Print helper ─────────────────────────────────────────────────────────────────

run_bot() {
    local label="$1"
    local dir="$2"
    local mode="$3"
    printf '\n\033[1;36m══════════════════════════════════════════════════════\033[0m\n'
    printf '\033[1;36m  %-28s  %s\033[0m\n' "$label" "$mode"
    printf '\033[1;36m══════════════════════════════════════════════════════\033[0m\n'
    (cd ~/bots/"$dir" && "$PY" bot.py $mode) 2>&1
    local rc=$?
    if [ $rc -ne 0 ]; then
        printf '\033[1;31m  exit %d\033[0m\n' "$rc"
    fi
}

# ── Mode resolution ───────────────────────────────────────────────────────────────
# Each bot supports different flags; map requested mode to what the bot accepts.
#
# Supported modes per bot:
#   WheelBot              --status  --once
#   DayTradingBot         --status  --close                (no --once)
#   CryptoBot             --status  --once
#   CopyTradingBot        --summary --once                 (no --status, use --summary)
#   RobinhoodDayTradingBot --status --close                (no --once)

bot_mode() {
    local bot="$1"   # wheel | day | crypto | copy | rh
    local req="$2"   # requested mode, or "" for default

    case "$bot" in
        wheel)
            case "$req" in
                --once|--close) echo "--once" ;;
                *)              echo "--status" ;;
            esac ;;
        day)
            case "$req" in
                --close)        echo "--close" ;;
                *)              echo "--status" ;;
            esac ;;
        crypto)
            case "$req" in
                --once|--close) echo "--once" ;;
                *)              echo "--status" ;;
            esac ;;
        copy)
            case "$req" in
                --once)         echo "--once" ;;
                *)              echo "--summary" ;;
            esac ;;
        rh)
            case "$req" in
                --close)        echo "--close" ;;
                *)              echo "--status" ;;
            esac ;;
    esac
}

# ── Individual runners ────────────────────────────────────────────────────────────

run_wheel()  { run_bot "WheelBot"               "WheelBot"               "$(bot_mode wheel  "$MODE")"; }
run_day()    { run_bot "DayTradingBot"           "DayTradingBot"          "$(bot_mode day    "$MODE")"; }
run_crypto() { run_bot "CryptoBot"              "CryptoBot"              "$(bot_mode crypto "$MODE")"; }
run_copy()   { run_bot "CopyTradingBot"          "CopyTradingBot"         "$(bot_mode copy   "$MODE")"; }
run_rh()     { run_bot "RobinhoodDayTradingBot"  "RobinhoodDayTradingBot" "$(bot_mode rh     "$MODE")"; }

# ── Warn on live-action modes ────────────────────────────────────────────────────

if [[ "$MODE" == "--once" || "$MODE" == "--close" ]]; then
    printf '\033[1;33mWARNING: %s will place/cancel orders (paper account).\033[0m\n' "$MODE"
    read -r -p "Continue? [y/N] " confirm
    [[ "$confirm" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 1; }
fi

# ── Dispatch ──────────────────────────────────────────────────────────────────────

case "$BOT" in
    wheel)  run_wheel ;;
    day)    run_day ;;
    crypto) run_crypto ;;
    copy)   run_copy ;;
    rh)     run_rh ;;
    all)
        run_wheel
        run_day
        run_crypto
        run_copy
        run_rh
        ;;
    *)
        echo "Unknown bot: $BOT"
        echo "Usage: $0 [wheel|day|crypto|copy|rh|all] [--status|--once|--summary|--close]"
        exit 1
        ;;
esac
