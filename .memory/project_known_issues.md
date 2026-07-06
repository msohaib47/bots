---
name: known-issues
description: Recurring gotchas — Python version, CRLF line endings, CIFS limitations, notifier
metadata:
  type: project
---

## Python 3.8 fallback (FIXED)

System `python3` on the server is 3.8.10. Bots use `type | None` syntax (Python 3.10+) and crash with 3.8.

**Fix:** `test_bots.sh` exits with error if pyenv 3.12 not found. All crontab entries use full pyenv path.

**Rule:** Never use bare `python3` in SSH commands or new crontab entries. Always use `/home/claude/.pyenv/versions/3.12.13/bin/python3`.

## Windows CRLF line endings

Bots edited on Windows get `\r\n` endings. Linux bash chokes on `\r`.

**Fix:** Daily cron at 7:45am: `find /home/claude/bots -name "*.py" -o -name "*.sh" | xargs sed -i 's/\r//'`

**Rule:** After editing on Windows and running manually: `sed -i 's/\r//' ~/bots/BotName/file.py`

## CIFS mount limitations

- **No symlinks:** Use `cp` instead of `ln -s` for shared files like `notifier.py`
- **No atomic .pyc rename:** Set `PYTHONPYCACHEPREFIX=/home/claude/.pycache` before running Python manually

## notifier.py — HTTP header encoding (FIXED 2026-06-11)

The `Title` HTTP header sent to ntfy.sh must be ASCII/latin-1. Any Unicode character in the title silently fails with `'latin-1' codec can't encode character` warning. Two bugs were fixed:
1. Em-dash `—` in `f":{emoji}: {action} — {symbol}"` → replaced with `|`
2. Literal newline in `body = "\` + newline + `".join(...)` (valid Python 3.8, SyntaxError in 3.12) → replaced with `"\n".join(...)`

**Rule:** Keep all HTTP header strings ASCII-only in `common/notifier.py`. No per-bot copies exist — edit only `common/notifier.py`.
