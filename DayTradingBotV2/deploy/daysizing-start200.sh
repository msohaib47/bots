#!/bin/bash
# Wrapper for the v2 Sizing service, Start-at-200 account.
# CWD is the account's own thin directory -- config.py resolves .env from
# os.getcwd(), NOT from the script's own directory (see PLAN.md's dotenv bug fix).
export PYTHONPYCACHEPREFIX=/home/claude/.pycache
export PYTHONPATH=/home/claude/bots-live
cd /home/claude/bots-live/DayTradingBotV2/DayTradingBot-Start200
exec /home/claude/.pyenv/versions/3.12.13/bin/python3 ../DayTradingExecution/sizing_service.py
