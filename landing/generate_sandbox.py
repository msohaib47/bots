"""
generate_sandbox.py — Landing page for sandbox.solutionzeroone.com.
Auto-discovers *.sandbox.solutionzeroone.com vhosts from nginx sites-enabled
and lists them as cards. Run standalone or on its own cron schedule.
"""

import os
import re
import sys
from pathlib import Path

NGINX_SITES_ENABLED = "/etc/nginx/sites-enabled"
OUTPUT_DIR = Path("/home/sohaib/sites/sandbox-landing")
APEX = "sandbox.solutionzeroone.com"


def get_sandbox_sites() -> list:
    """Returns sorted list of {domain, https} dicts for *.sandbox.solutionzeroone.com
    vhosts configured in nginx sites-enabled (excludes the apex landing vhost itself)."""
    sites = {}
    try:
        for fname in os.listdir(NGINX_SITES_ENABLED):
            path = os.path.join(NGINX_SITES_ENABLED, fname)
            try:
                with open(path, errors="replace") as f:
                    content = f.read()
            except Exception:
                continue

            has_https = "listen 443" in content

            for m in re.finditer(r"server_name\s+([^;]+);", content):
                for name in m.group(1).split():
                    name = name.strip()
                    if not name.endswith(f".{APEX}"):
                        continue
                    prev = sites.get(name, False)
                    sites[name] = prev or has_https
    except Exception:
        pass

    return sorted(sites.items())


def build_html(sites: list) -> str:
    cards = ""
    for domain, https in sites:
        scheme = "https" if https else "http"
        badge = '<span class="badge https">HTTPS</span>' if https else '<span class="badge http">HTTP</span>'
        label = domain.split(f".{APEX}")[0]
        cards += f"""
        <a class="card" href="{scheme}://{domain}" target="_blank" rel="noopener">
          <h3>{label}</h3>
          <div class="domain">{domain}</div>
          {badge}
        </a>"""

    if not cards:
        cards = '<p class="no-data">No sandbox subdomains configured yet.</p>'

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Sandbox — solutionzeroone.com</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: system-ui, sans-serif; background: #f4f5f7; color: #222; }}
    nav {{ background: #1a1a2e; color: #fff; padding: 14px 24px; }}
    nav .brand {{ font-weight: 700; font-size: 1.1rem; }}
    .wrap {{ max-width: 1000px; margin: 32px auto; padding: 0 16px; }}
    h2 {{ font-size: 1.4rem; margin-bottom: 4px; }}
    .sub {{ color: #666; font-size: .85rem; margin-bottom: 24px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 16px; }}
    .card {{ background: #fff; border-radius: 10px; padding: 20px; box-shadow: 0 1px 4px rgba(0,0,0,.08); text-decoration: none; color: inherit; display: block; transition: box-shadow .15s; }}
    .card:hover {{ box-shadow: 0 4px 14px rgba(0,0,0,.13); }}
    .card h3 {{ font-size: 1.05rem; margin-bottom: 4px; text-transform: capitalize; }}
    .card .domain {{ color: #888; font-size: .78rem; margin-bottom: 10px; word-break: break-all; }}
    .badge {{ display: inline-block; font-size: .68rem; padding: 2px 8px; border-radius: 4px; }}
    .badge.https {{ background: #dcfce7; color: #166534; }}
    .badge.http {{ background: #fef3c7; color: #92400e; }}
    .no-data {{ color: #888; padding: 40px; text-align: center; }}
  </style>
</head>
<body>
<nav><span class="brand">🧪 Sandbox — solutionzeroone.com</span></nav>
<div class="wrap">
  <h2>Sandbox Subdomains</h2>
  <p class="sub">{len(sites)} site{'s' if len(sites) != 1 else ''} hosted under *.{APEX}</p>
  <div class="grid">{cards}</div>
</div>
</body>
</html>
"""


def generate(output_dir: Path = OUTPUT_DIR):
    output_dir.mkdir(parents=True, exist_ok=True)
    sites = get_sandbox_sites()
    (output_dir / "index.html").write_text(build_html(sites), encoding="utf-8")
    print(f"  index.html  ({len(sites)} sandbox site(s))")
    print(f"Done → {output_dir}")


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else OUTPUT_DIR
    generate(out)
