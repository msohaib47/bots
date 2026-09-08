#!/bin/bash
# Wrapper for the v2 Notifier service -- one instance total, centralizes every
# notify() call across all v2 accounts (see DayNotifierService/notifier_service.py).
export PYTHONPYCACHEPREFIX=/home/claude/.pycache
export PYTHONPATH=/home/claude/bots-live
cd /home/claude/bots-live/DayTradingBotV2/DayNotifierService
exec /home/claude/.pyenv/versions/3.12.13/bin/python3 notifier_service.py
