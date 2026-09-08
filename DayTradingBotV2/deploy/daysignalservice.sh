#!/bin/bash
# Wrapper for the v2 Signal service -- account-independent, one instance total.
# Deployed to ~/daysignalservice.sh on the trading server (192.168.1.250, user claude),
# matching the existing per-bot wrapper-script convention (see .memory/project_bots_overview.md).
export PYTHONPYCACHEPREFIX=/home/claude/.pycache
export PYTHONPATH=/home/claude/bots-live
cd /home/claude/bots-live/DayTradingBotV2/DaySignalService
exec /home/claude/.pyenv/versions/3.12.13/bin/python3 signal_service.py
