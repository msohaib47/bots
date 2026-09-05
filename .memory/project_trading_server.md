---
name: trading-server
description: SSH access, Python runtime, file layout, and crontab details for the trading server
metadata:
  type: project
---

Trading server: `192.168.1.250` — two SSH users, used for different purposes:
- `claude` — runs the bots (crontab, `~/bots-live`, bot logs). Use for all bot-related work: checking logs, running bots manually, editing crontab.
- `sohaib` — has passwordless sudo. Use for any server/infra-level admin work: system services (systemctl), package installs, anything needing root. Confirmed working 2026-07-07 fixing a DNS outage (`sudo systemctl enable --now systemd-resolved`) that `claude` couldn't do (no passwordless sudo for `claude`).

SSH key at `C:\Users\sohai\.ssh\id_ed25519` — passwordless access works from this Windows machine for both users.

**Python runtime:** pyenv 3.12.13 at `/home/claude/.pyenv/versions/3.12.13/bin/python3`
- System Python 3.8 at `/usr/bin/python3` — never use for bots (incompatible with `type | None` syntax)
- Always use full pyenv path in crontab and manual SSH commands

**File layout:**
- Windows source: `Z:\work\bots\` (CIFS mount, live on server)
- Server mount: `/mnt/asus/work/bots`
- Server symlink: `/home/claude/bots -> /mnt/asus/work/bots`
- **Actual live/running copy: `/home/claude/bots-live`** — a separate local directory, NOT the same as `~/bots`. See "bots vs bots-live" below.
- Logs (crontab stdout): `~/bots-live/logs/` (not `~/bots/logs/` — that path is only used by the commented-out legacy cron lines)
- Logs (bot FileHandler): `~/bots-live/BotName/logs/`

**bots vs bots-live (discovered 2026-07-06):**
`~/bots` (the CIFS-mounted, Windows-editable source) is **not** what crontab actually runs. All live crontab entries call wrapper scripts (`~/cryptobot.sh`, `~/wheelbot.sh`, etc.) that `cd` into `~/bots-live/BotName` and set `PYTHONPATH=/home/claude/bots-live`. The old `cd /home/claude/bots/...` cron lines are present but commented out. No automatic sync job was found between `~/bots` and `~/bots-live` — assume they can drift. Before trusting that a Windows-side edit is live, diff them: `ssh claude@192.168.1.250 "diff -rq ~/bots/BotName ~/bots-live/BotName"`.

**CIFS mount limitations (apply to `~/bots`, not `~/bots-live`):**
- Symlinks not supported — copy files instead of symlinking
- Atomic `.pyc` rename not supported — always set `PYTHONPYCACHEPREFIX=/home/claude/.pycache`

**Crontab env (already set at top of crontab):**
```
PATH=/home/claude/.pyenv/versions/3.12.13/bin:/home/claude/.local/bin:...
PYTHONPYCACHEPREFIX=/home/claude/.pycache
```
Each per-bot wrapper script in `~/` (e.g. `~/cryptobot.sh`) additionally sets `PYTHONPATH=/home/claude/bots-live` itself.

**How to apply:** Always SSH with `ssh claude@192.168.1.250`. Use full pyenv path for any manual `python3` invocations. When debugging "why hasn't the bot done X" — check logs and run manual commands against `~/bots-live/BotName`, since that's what's actually executing, not `~/bots/BotName`. Fix CRLF before running manually: `sed -i 's/\r//' ~/bots-live/BotName/file.py`.
