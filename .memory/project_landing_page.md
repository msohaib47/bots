---
name: project-landing-page
description: Landing page bot (landing/generate.py) — hosted-sites list and AlHadi visitor count, nginx log access setup
metadata:
  type: project
---

`landing/generate.py` on the server scans listening ports (`ss -tlnp`) plus `/etc/nginx/sites-enabled/*` to build `/home/sohaib/sites/landing/index.html`, refreshed every 15 min via cron (`*/15 * * * * python3 /home/claude/bots-live/landing/generate.py`, no `PYTHONPYCACHEPREFIX` set on this entry unlike the other bots). Runs as user `claude`.

**Hosted Sites section (added 2026-08-07):** `get_hosted_sites()` parses `server_name`/`listen 443` out of every file in `/etc/nginx/sites-enabled/`, dedupes `www.` variants into the bare domain, and lists all real (non-`_`) domains with an HTTPS badge and a live link. Currently 7 domains: alhadiassociation.com, ebaylistings.solutionzeroone.com, erp.solutionzeroone.com, mail.alhadiassociation.com, mail.solutionzeroone.com, solutionzeroone.com, v3.solutionzeroone.com.

**Per-site visitor counts (added 2026-08-07, extended to all sites same day):** every domain previously logged into the shared, un-filterable `/var/log/nginx/access.log` (default `access_log` in `nginx.conf`, no per-domain field) except solutionzeroone.com, which already had its own (`solutionzeroone_log` custom format, first field still IP). Gave the other 6 domains (alhadiassociation.com, erp.solutionzeroone.com, ebaylistings.solutionzeroone.com, mail.alhadiassociation.com, mail.solutionzeroone.com, v3.solutionzeroone.com) their own `access_log /var/log/nginx/<domain>.access.log;` line in their respective `/etc/nginx/sites-available/<file>` (backups saved alongside as `.bak-20260807`) and reloaded nginx. `get_site_visitors(domain)` in generate.py is now generic — it looks for `/var/log/nginx/{domain}.access.log` (+ `.log.1` rotation) for *any* domain from `get_hosted_sites()` and reports total hits + unique IPs; a domain with no matching log file just shows "—". Adding a new nginx vhost automatically gets a visitors column once it has a matching `access_log` file — no code changes needed, just the nginx config line.

**`claude` user added to the `adm` group** (2026-08-07, via `sohaib`'s passwordless sudo: `usermod -aG adm claude`) so the cron job can read nginx's `www-data:adm`-owned, `640`-permission log files without sudo. This is a standing group membership, not per-file — any nginx log claude needs to read in the future should already be readable.

**Why:** user asked to add a visitor counter for the AlHadi site and list all nginx-hosted sites on the landing page dashboard.
**How to apply:** when adding a new nginx vhost that should show visitor counts, add its own `access_log` line + reload nginx, same as AlHadi's; `get_hosted_sites()` will pick up the new domain automatically from `sites-enabled` without code changes. Deploy chain for `landing/generate.py` edits follows the standard D: → Z: → `~/bots` → `~/bots-live` copy chain (see [[project_bots_overview]]).
