# DayTradingBotV2 deploy templates

Wrapper scripts (`*.sh`) and systemd unit files (`*.service`) for the four
Start-at-200 daemons. These are **local templates only** -- nothing here has
been copied to or enabled on the trading server yet.

## Install steps (system-level, needs `sohaib`'s passwordless sudo -- see
`.memory/project_trading_server.md`)

```bash
# 1. Get the code onto the server first (standard deploy chain: D: -> Z: -> ~/bots -> ~/bots-live)
#    see .memory/project_bots_overview.md

# 2. Copy wrapper scripts to ~/ (claude user) and make executable
scp DayTradingBotV2/deploy/*.sh claude@192.168.1.250:~/
ssh claude@192.168.1.250 "chmod +x ~/daysignalservice.sh ~/daysizing-start200.sh ~/dayexec-start200.sh ~/daynotifierservice.sh"

# 3. Copy unit files to systemd's system dir (needs sudo -- use sohaib)
scp DayTradingBotV2/deploy/*.service sohaib@192.168.1.250:/tmp/
ssh sohaib@192.168.1.250 "sudo mv /tmp/*.service /etc/systemd/system/ && sudo systemctl daemon-reload"

# 4. Enable + start (order matters least at boot since unit deps handle it,
#    but start signal first manually so sizing/exec don't spend their first
#    30s logging "heartbeat stale" for no reason)
ssh sohaib@192.168.1.250 "sudo systemctl enable --now daysignalservice"
ssh sohaib@192.168.1.250 "sudo systemctl enable --now daysizing-start200"
ssh sohaib@192.168.1.250 "sudo systemctl enable --now dayexec-start200"
ssh sohaib@192.168.1.250 "sudo systemctl enable --now daynotifierservice"

# 5. Verify
ssh sohaib@192.168.1.250 "sudo systemctl status daysignalservice daysizing-start200 dayexec-start200 daynotifierservice"
```

## Before doing any of this on the real server

Per PLAN.md's Stage 1 migration order, do **not** enable these until:

1. `kv_store.read_events_since(0) == []` is confirmed clean (avoid the
   accidental-notification incident happening again, this time for real).
2. The v1 monolith's cron entry for the Start-at-200 account is disabled
   first -- **never run both pipelines against the same Alpaca account
   simultaneously** (double-execution risk against the same account/state).
3. Ideally, a first enable happens shortly before market open on a trading
   day, not mid-session, so the very first cold-start + signal-to-notify
   latency check happens under clean conditions.

## Logs

Each service also writes its own `logs/*.log` via Python's own
`logging.FileHandler` (see each service's own LOG_DIR), same convention as
every other bot in this repo. The systemd `StandardOutput`/`StandardError`
redirects above additionally capture anything printed to stdout/stderr
(tracebacks that occur before logging is configured, interpreter-level
errors, etc.) -- mirrors the crontab convention's separate
`~/bots-live/logs/<bot>.log` (stdout) vs `BotName/logs/` (FileHandler) split.

## Uninstall / rollback

```bash
ssh sohaib@192.168.1.250 "sudo systemctl disable --now daysignalservice daysizing-start200 dayexec-start200 daynotifierservice"
ssh sohaib@192.168.1.250 "sudo rm /etc/systemd/system/day{signalservice,sizing-start200,exec-start200,notifierservice}.service && sudo systemctl daemon-reload"
```

Then re-enable the v1 `DayTradingBot` cron entry for this account.
