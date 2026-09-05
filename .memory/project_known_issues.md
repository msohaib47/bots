---
name: known-issues
description: Recurring gotchas — Python version, CRLF line endings, CIFS limitations, notifier
metadata:
  type: project
---

## DayTradingBot / VerticalSpreadBot — signal always NONE, "HTF data unavailable" every tick (FIXED 2026-08-10)

DayTradingBot generated zero signals on any ticker, every tick, indefinitely — every log line read `signal=NONE | No signal — HTF data unavailable`, even with clearly bullish/bearish price action. Not an intermittent issue; this had likely been silently blocking every trade since the HTF filter was added.

**Root cause:** `get_recent_bars()` in `DayTradingBot/alpaca.py` (and a near-duplicate in `VerticalSpreadBot/alpaca.py`) is documented to fetch "the most recent N bars regardless of day — for multi-session indicators," but never actually sent a `start` param to Alpaca's `/v2/stocks/{symbol}/bars` endpoint. Without `start`, Alpaca defaults to **today's session only**. The HTF trend filter (`_htf_trend_bullish()` in `signals.py`) needs 22+ 15-min bars to compute a 21-period EMA on SPY — that requires ~5.5 hours of trading, so for most of the day (and worse, right after open) fewer than 22 bars existed, `_htf_trend_bullish()` returned `None`, and every ticker's signal got vetoed by the `htf_bullish is None` filter regardless of the actual VWAP/EMA/RSI conditions.

**Fix:** `get_recent_bars()` now passes `start = (now_utc - 10 days).isoformat()` explicitly, so the bars endpoint actually returns a multi-session window as the docstring always claimed. Verified live 2026-08-10: within one cron tick of deploying, DayTradingBot started producing real CALL/PUT signals (TSLA, INTC, AMD, SPY all fired) instead of blanket "HTF data unavailable."

**Rule:** never rely on an exchange/market-data API's implicit default date range for a "regardless of day" / multi-session fetch — pass `start` explicitly. This bug shipped silently because `get_recent_bars()` catches exceptions and logs them, but an empty-but-successful response (fewer bars than expected, no error) produces no log line at all — check bar *counts*, not just for exceptions, when debugging a filter that depends on lookback depth.

**Fixing this immediately surfaced a second, unrelated blocker** — see the Alpaca account migration note in [[project_bots_overview]] (options buying power was nearly exhausted by WheelBot's CSP collateral on the previously-shared account).

## DayTradingBot — orphaned exercised-option positions silently ate all buying power for 3 weeks (FIXED 2026-08-07)

DayTradingBot placed zero real trades between 2026-07-17 and 2026-08-07 despite running every minute during market hours and generating correct signals the whole time (confirmed valid PUT signals on IWM/TSLA in logs) — every order attempt failed with `403 insufficient options buying power, options_buying_power:"0"`.

**Root cause:** on 2026-07-17, `sync_positions.py` found 2-contract IWM and QQQ put positions missing from Alpaca's live position list and logged them as `SYNC_REMOVE ... "closed/expired"` — but they hadn't expired worthless, they'd expired **in-the-money and been auto-exercised**, leaving the account short 200 shares each of IWM (-$59,710) and QQQ (-$143,610). Since a long put exercise = selling stock you don't own = going short. `sync_positions.py` never checked for this — it assumed "missing from options list" always meant "expired worthless." The resulting short equity positions weren't tracked in `positions.json` by any bot (`bot.py --status` reported "no open positions" the whole time) but still counted against the account's real margin/buying power on the Alpaca side, silently blocking every subsequent order for 3 weeks. VerticalSpreadBot shares this same Alpaca account and was equally blocked, also with zero trades since 2026-07-14.

**Fix:** `sync_positions.py` now has `_orphan_equity_positions()` — since DayTradingBot never legitimately holds SPY/QQQ/IWM stock directly (options only), *any* equity position in those symbols is by definition an orphan. On each sync run (every 30 min) it now checks for this, logs an ERROR, and fires an immediate `common.notifier` alert (`ERROR` priority/urgent on ntfy) instead of staying silent. It also relabels the `SYNC_REMOVE` reason from generic `"closed/expired"` to `"likely exercised/assigned"` when an orphan equity position is found for that underlying, so `trades.csv` stops lying about what actually happened.

**Rule:** when an option position disappears from Alpaca's list, don't assume it expired worthless — check for a resulting equity position on the underlying before logging it as a clean close. This applies to any bot syncing state against Alpaca's live positions, not just DayTradingBot.

**Resolved 2026-08-07:** user manually closed both orphan positions in the Alpaca dashboard. Confirmed after: `options_buying_power` back to $4,046.68 (was $0), `list_positions()` empty, both bots' `positions.json` reconciled clean via `sync_positions.py` (no orphan-equity alert fired — correctly quiet on a clean account), and a live cron tick ran with no `403`/`422` errors. Both bots resumed normal operation on their existing schedule.

**Still open:** VerticalSpreadBot has no equivalent sync job at all (only DayTradingBot has a crontab sync entry, `daytradingpositionsync.sh` every 30 min) — the same orphan-position risk exists there unaddressed if one leg of a spread gets exercised. Not fixed yet.

**How to clear an existing orphan position:** `alpaca.close_option_position(symbol)` (in each bot's `alpaca.py`) with no `qty` arg calls Alpaca's `DELETE /v2/positions/{symbol}`, which fully flattens the position in one call regardless of whether it's actually an option or equity symbol.

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
- **Chromium/Playwright profile dirs crash on CIFS** (found 2026-08-03, ebayCompletedListings): a Playwright `launch_persistent_context(user_data_dir=...)` pointed at a path under the CIFS mount (`/mnt/asus/work/bots/...`) hit `Page.goto: Page crashed`. Chromium's profile dir needs file locking/atomic renames the same way `.pyc` does — CIFS can't provide it. **Fix:** point any Chromium/Playwright `user_data_dir` at local disk (e.g. `/home/claude/data/...`), never at a CIFS-mounted bot directory. This is also why `ebayCompletedListings` was moved to run from `~/bots-live` instead of straight off `~/bots` (see [[project_bots_overview]]) — it was the one bot still executing directly off the CIFS mount.

## eBay bot detection (ebayCompletedListings) — real root cause: sold-listings search requires login (found 2026-08-07)

`curl_cffi` TLS-impersonation scraping got fully blocked by eBay's PerimeterX-style bot detection starting 2026-07-24 — every daily run returned 0 listings, serving either a "Pardon Our Interruption..." JS challenge or a "Sign in or Register" wall. The 2026-08-03 Playwright rewrite (real headless Chromium + persistent profile + homepage warm-up) was believed to fix this, but **it never actually worked even once** — every single run from 2026-08-03 through at least 2026-08-07 still hit "Sign in or Register" (or, escalating on repeated attempts within the same run, "Security Measure"), 0 listings every time. This went unnoticed for days because the DB logged `status='ok'` with `items_found=0` — no exception, just silently empty. (A separate red herring during this debugging: `grep -v WARNING` used to strip the SSH client's "post-quantum" banner also strips every Python `[WARNING]`-level log line — the actual block-reason log lines were always there in `agent.log`, just invisible to that grep pattern. Use a more specific filter, e.g. `grep -v '^\*\* '`, when greping these logs over SSH.)

**Actual root cause, confirmed by testing:** launched a completely fresh, never-used Playwright profile against the server — the eBay homepage and a plain search (no `LH_Sold`) both loaded fine with real titles. Adding `LH_Sold=1&LH_Complete=1` (the sold/completed-listings filter) hit the sign-in wall on the very first request, no history needed. This is not IP reputation, not a burned profile, not escalating bot-detection — **eBay now requires a real signed-in account to view sold listings at all**, and repeated guest attempts against that gated endpoint escalate further to "Security Measure." No amount of stealth/warm-up/proxy tuning can fix an actual auth requirement.

**Fix:** [ebayCompletedListings/login_local.py](../ebayCompletedListings/login_local.py) — run once on a machine with a display (Windows, not the headless server) to interactively log into a real eBay account in a visible Chromium window using Playwright's persistent-context profile. Copy the resulting profile dir to the server: `scp -r .pw-profile-local claude@192.168.1.250:/home/claude/data/ebay-pw-profile`. `scraper.py`'s `PROFILE_DIR` then reuses that authenticated session for all future headless cron runs. Re-run `login_local.py` whenever the sign-in wall comes back (eBay sessions eventually expire).

**Rule:** if `agent.py run` reports 0 found with no error, check `agent.log`'s `[WARNING]` lines (not `ebaybot.log`, and not through a `grep -v WARNING` filter) for the actual block title before assuming the scrape logic is broken — the debug HTML dumps (`debug_<config>_page1.html`, regenerated fresh each run in the bot's own directory) show exactly what eBay served.

**`login_local.py` gotcha #1 (fixed 2026-08-07):** the first version forced a spoofed `Chrome/124.0.0.0` User-Agent onto Playwright's bundled test-build Chromium for the interactive login. eBay's "verify yourself" challenge (hCaptcha) failed to render on that combination. **Fix:** launch via `channel="chrome"` (real installed Chrome) with no UA override, `args=["--disable-blink-features=AutomationControlled"]`.

**`login_local.py` gotcha #2 (found 2026-08-07, real Chrome still didn't fix it):** even with real Chrome, hCaptcha still failed with "Your browser or network settings are blocking hCaptcha", `chrome-extension://invalid` console errors, and a `483` response on `/signin/srv/identifier`. Root cause: hCaptcha detects the **Chrome DevTools Protocol connection itself** — the mechanism Playwright (and Puppeteer/Selenium) use to drive any browser, regardless of which browser or how well its fingerprint is disguised. Switching browsers/UA can't fix this; the automation link is the thing being detected. **Fix:** [ebayCompletedListings/import_chrome_profile.py](../ebayCompletedListings/import_chrome_profile.py) — log into eBay by hand in a completely normal, unautomated everyday Chrome (hCaptcha behaves fine there, no CDP attached), close Chrome, then run this script to copy that authenticated profile (`%LOCALAPPDATA%\Google\Chrome\User Data\<profile>`) into `.pw-profile-local`. `login_local.py` then just verifies the imported session instead of performing the login itself. Once authenticated, the headless scraper reusing that session for normal page navigation doesn't re-trigger hCaptcha — it only guards the login/verification flow itself.

**Rule:** any interactive login flow gated by hCaptcha/reCAPTCHA/similar cannot be done through Playwright, Puppeteer, Selenium, or any other CDP-based tool, no matter how well-disguised — do the login in a real, unautomated browser session and import the resulting profile/cookies instead.

**`import_chrome_profile.py` gotcha (found 2026-08-07, superseded same day):** copying Chrome's whole profile folder (Cookies DB + Local State) into a fresh `user_data_dir` completed with no errors but produced a signed-out session — Chrome's Windows "App-Bound Encryption" ties the Cookies database's decryption key to the *original installation*, so a plain file copy decrypts to nothing usable. This is a deliberate anti-cookie-theft protection, not a bug to work around at the file level. **Fix:** [ebayCompletedListings/import_cookies.py](../ebayCompletedListings/import_cookies.py) — extract live (already-decrypted) cookie values from the normal logged-in Chrome via the "Cookie-Editor" extension's JSON export, then inject them directly through Playwright's `context.add_cookies()` API, which bypasses Chrome's on-disk encryption entirely since it's writing known key/value pairs through the browser's own API rather than reading an encrypted file.

**Rule:** never try to reuse a Chrome profile by copying its files on Windows — App-Bound Encryption breaks it silently (no error, just signed-out). Extract and re-inject cookie values instead.

**`import_cookies.py` decryption gotcha (found + fixed 2026-08-07, same day):** the "Cookie-Editor" extension the user actually had installed is **not** the popular open-source Moustachauve extension (`hlkenndednhfkekhgcdicdfddnkalmdm`) — it's a different, closed-source "Cookie Editor" by hotcleaner.com (`iphcomljdfghbkdcfndaijbokpgddeno`), and its encrypted export format is undocumented anywhere public. First guess (CryptoJS/OpenSSL `AES.encrypt(text, password)`, the "Salted__"-prefixed format) failed immediately — the base64 blob doesn't start with that header at all, so the password was never even the issue. **Resolution:** downloaded the actual `.crx` from the Chrome Web Store (`https://clients2.google.com/service/update2/crx?response=redirect&prodversion=120&acceptformat=crx3&x=id%3D<extension_id>%26uc`), unzipped the CRX3 payload (strip everything before the `PK\x03\x04` zip signature), and read the real minified JS (`fjs129/eeditor129.js`, functions `G`/`X`/`Z`) to get the exact algorithm: AES-256-GCM, key = `PBKDF2-SHA256(password, salt=UTF8(password+password), iterations=1024, dkLen=32)`, blob = `base64(IV[12 bytes] || ciphertext || 16-byte GCM tag)`. Verified by decrypting the user's real export file directly before telling them to retry.

**Rule:** when reverse-engineering an undocumented browser-extension export/encryption format, don't guess based on "what's common" (CryptoJS is common, but wasn't it here) — pull the actual extension package and read the real code. It's a public zip download, no special access needed, and settles the question in minutes instead of repeated guess-and-check with the user.

**`import_cookies.py` domain-dot gotcha (found + fixed 2026-08-07):** even after decryption worked and `context.add_cookies()` ran with no errors, the imported session still showed guest state ("Hi! Sign in or register"). Root cause: `chrome.cookies.getAll()` (what the extension reads from) represents "applies to all subdomains" via a separate `hostOnly: false` boolean, with the `domain` string itself never carrying a leading dot (e.g. `domain: "ebay.com", hostOnly: false`). Playwright/CDP's cookie API has no equivalent separate flag — it infers domain-vs-host-only purely from whether the `domain` string itself starts with a dot. Every subdomain-wide cookie (including the actual auth cookies `dp1`, `nonsession`, `ebay`, `s`) was silently becoming host-only for the bare `ebay.com`, never matching `www.ebay.com` where eBay actually checks them. **Fix:** `_convert()` in `import_cookies.py` now prepends a `.` to `domain` whenever `hostOnly` is false. Verified directly: injecting the same cookie set with this fix produced `"Hi Muhammad!"` in eBay's header; without it, guest state every time.

**Production deploy still blocked after cookie fix (found 2026-08-07, root cause identified, partial fix applied) — GPU fingerprinting:** the corrected cookie import worked flawlessly when tested locally on the Windows machine (real Chrome, real GPU) but still hit the "Sign in or Register" wall when the identical profile was used on the server — even after confirming the server and Windows machine share the same public IP (both behind the same home NAT, so IP wasn't the variable) and after installing real Chrome system-wide on the server (`sudo apt install google-chrome-stable`, done via `sohaib`'s passwordless sudo since installing browser deps needs root) and switching `scraper.py`'s `_new_context()` to `channel="chrome"` with no spoofed UA. Root cause: the server has no real GPU, so Chrome's WebGL renderer reports `ANGLE (Google, Vulkan ... SwiftShader Device (Subzero) ...)` — software rendering, a well-known automated/server-environment signal that anti-fraud engines (eBay uses Akamai — see the `ak_bmsc`/`bm_sv` cookies) check independent of cookies or IP.

**Fix applied (2026-08-07, effectiveness unconfirmed):** `STEALTH_INIT_SCRIPT` in `scraper.py` now patches `WebGLRenderingContext`/`WebGL2RenderingContext.prototype.getParameter` to fake `UNMASKED_VENDOR_WEBGL`/`UNMASKED_RENDERER_WEBGL` as a plausible Intel UHD 620 laptop GPU, plus spoofs `navigator.platform` and `navigator.userAgentData.platform` to `Win32`/`Windows`. Confirmed this changed eBay's response (block title flipped from "Sign in or Register" to "Security Measure" — a different, harder classification), proving the fingerprint check was real and reactive to the patch, but did not yet produce a clean pass — likely compounded by rate-limiting from the heavy volume of manual test requests during this same debugging session (matches the already-documented "Security Measure"/"Error Page" escalation pattern from repeated rapid automated traffic, see the entry above from 2026-08-03). **Not yet re-verified after a cooldown period** — check `~/bots-live/logs/ebaybot.log` after the next real scheduled cron run (not a manual test, to avoid further escalating) to see if the combination (real Chrome + GPU spoof + authenticated cookies) actually works once traffic isn't itself the trigger.

**Rule:** GPU/WebGL fingerprint spoofing on a headless/GPU-less server is inherently an arms race, not a permanent fix — Akamai and similar vendors actively adapt detection. If this breaks again, that's expected, not a regression to chase indefinitely; the durable fix if this keeps failing is running the actual scraping step from a machine with a real GPU (the user's Windows machine, discussed and deferred 2026-08-07 in favor of trying spoofing first).

## notifier.py — HTTP header encoding (FIXED 2026-06-11)

The `Title` HTTP header sent to ntfy.sh must be ASCII/latin-1. Any Unicode character in the title silently fails with `'latin-1' codec can't encode character` warning. Two bugs were fixed:
1. Em-dash `—` in `f":{emoji}: {action} — {symbol}"` → replaced with `|`
2. Literal newline in `body = "\` + newline + `".join(...)` (valid Python 3.8, SyntaxError in 3.12) → replaced with `"\n".join(...)`

**Rule:** Keep all HTTP header strings ASCII-only in `common/notifier.py`. No per-bot copies exist — edit only `common/notifier.py`.

## CopyTradingBot — ticker_type case-sensitivity (FIXED 2026-07-06)

`bot.py` had `SUPPORTED_TYPES = {'ST', 'stock', ''}` (case-sensitive) but QuiverQuant returns `TickerType: "Stock"` (capital S) for every trade. Every single trade — 111/111 since inception — was being skipped as "unsupported type Stock", so the bot never executed a trade even before the QuiverQuant auth issue below started.

**Fix:** `SUPPORTED_TYPES` lowercased to `{'st', 'stock', ''}`, and both comparison sites now do `ticker_type.lower() not in SUPPORTED_TYPES`.

**Rule:** When adding new ticker/asset-type allowlists from any external API, assume the API's casing is inconsistent and normalize before comparing.

## CopyTradingBot — QuiverQuant auth missing (OPEN, found 2026-07-06)

Every fetch since 2026-05-29 fails with `401 Unauthorized` from `api.quiverquant.com/beta/live/congresstrading`. Root cause: `.env` has no `QUIVER_API_KEY` at all, and `scraper.py` sends no `Authorization` header — only a spoofed browser `User-Agent`. This isn't a renewal issue; auth was never wired up for this endpoint.

**Fix needed (not yet done):** get a QuiverQuant API key (their live-trades endpoint is paid tier), add `QUIVER_API_KEY` to `.env`, and add the header to `scraper.py` (exact header format needs checking against current QuiverQuant docs).

**Rule:** Don't assume "401 Unauthorized" means "key expired" — check that a key is actually being sent at all first.
