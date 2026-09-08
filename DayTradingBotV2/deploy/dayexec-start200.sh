#!/bin/bash
# Wrapper for the v2 Execution service, Start-at-200 account.
# CWD is the account's own thin directory -- see daysizing-start200.sh for why.
export PYTHONPYCACHEPREFIX=/home/claude/.pycache
export PYTHONPATH=/home/claude/bots-live
cd /home/claude/bots-live/DayTradingBotV2/DayTradingBot-Start200
exec /home/claude/.pyenv/versions/3.12.13/bin/python3 ../DayTradingExecution/execution_service.py
