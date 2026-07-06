---
name: trading-server
description: SSH access, Python runtime, file layout, and crontab details for the trading server
metadata:
  type: project
---

Trading server: `192.168.1.250`, user: `claude`

SSH key at `C:\Users\sohai\.ssh\id_ed25519` — passwordless access works from this Windows machine.

**Python runtime:** pyenv 3.12.13 at `/home/claude/.pyenv/versions/3.12.13/bin/python3`
- System Python 3.8 at `/usr/bin/python3` — never use for bots (incompatible with `type | None` syntax)
- Always use full pyenv path in crontab and manual SSH commands

**File layout:**
- Windows source A: `Z:\work\bots\` (CIFS mount at `/mnt/asus/work/bots`)
- Windows source B: `D:\work\bots\` (local copy, kept in sync manually)
- Windows source C: `C:\Work\bots\` — this is the primary working directory Claude Code edits in
- **`Y:\bots-live\`** — mapped drive pointing directly at the server's `~/bots-live/` (user `claude`), i.e. the live crontab master itself, not the CIFS passive-sync mount
- **Live master on server: `~/bots-live/`** — crontab runs from here, not from the CIFS mount
- Server CIFS mount: `/mnt/asus/work/bots` (same as `~/bots/` symlink — used for passive sync only)
- Logs (crontab stdout): `~/bots-live/logs/`
- Logs (bot FileHandler): `~/bots-live/BotName/logs/`

**Sync workflow:** Bot updates must be made in **both** `C:\Work\bots\` and directly on the ubuntu server (`~/bots-live/`, e.g. via `Y:\bots-live\` or SSH) to keep them in sync — editing only one side leaves the other stale. Test runs happen on the ubuntu server as user `claude` (not on Windows).

Separately, the older rsync/robocopy pipeline propagates the server's live master out to the passive CIFS copies:
```bash
# Server to Z:\work\bots (from server):
rsync -rc --exclude='*.pyc' --exclude='__pycache__' --exclude='.env' \
  --exclude='logs/' --exclude='positions.json' --exclude='trades.csv' \
  --exclude='guard_state.json' --exclude='.schwab_tokens.json' \
  --exclude='node_modules/' ~/bots-live/ /mnt/asus/work/bots/
# Z: to D: (from Windows Git Bash): robocopy is used since rsync unavailable
```

**CIFS mount limitations:**
- Symlinks not supported — copy files instead of symlinking
- Atomic `.pyc` rename not supported — always set `PYTHONPYCACHEPREFIX=/home/claude/.pycache`

**Crontab env (already set at top of crontab):**
```
PATH=/home/claude/.pyenv/versions/3.12.13/bin:/home/claude/.local/bin:...
PYTHONPYCACHEPREFIX=/home/claude/.pycache
PYTHONPATH=/home/claude/bots
```
`PYTHONPATH` is also exported at the top of every `.sh` runner script in `~/`.

**How to apply:** Always SSH with `ssh claude@192.168.1.250`. Use full pyenv path for any manual `python3` invocations. Fix CRLF before running: `sed -i 's/\r//' ~/bots/BotName/file.py`. When making a bot change, apply it in `C:\Work\bots\` and mirror it on the server (`~/bots-live/` via `Y:\bots-live\` or SSH) before considering the change done, then run/test it on the server as `claude`.
