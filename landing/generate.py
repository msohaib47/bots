#!/usr/bin/env python3
"""Generates a landing page by scanning listening TCP ports on the server."""

import subprocess
import re
import os
import json
import gzip
import html
from datetime import datetime, timedelta
from collections import Counter

OUTPUT_PATH = os.environ.get("OUTPUT_PATH", "/home/sohaib/sites/landing/index.html")
PORTS_META  = os.path.join(os.path.dirname(__file__), "ports.json")
NGINX_SITES_ENABLED = "/etc/nginx/sites-enabled"
NGINX_LOG_DIR        = "/var/log/nginx"

def get_server_ip():
    """Get the server's primary IP address (non-loopback)."""
    try:
        result = subprocess.run(
            ["hostname", "-I"],
            capture_output=True, text=True, check=True
        )
        ips = result.stdout.strip().split()
        return ips[0] if ips else "localhost"
    except (subprocess.CalledProcessError, IndexError):
        return "localhost"

def load_known_ports():
    with open(PORTS_META) as f:
        raw = json.load(f)
    return {int(k): v for k, v in raw.items()}

KNOWN_PORTS = load_known_ports()

BASE_CSS = """
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    font-size: 16px;
    background: #f8fafc;
    color: #1e293b;
    min-height: 100vh;
    padding: 1.5rem;
    max-width: 720px;
    margin: 0 auto;
  }
  header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 1rem;
    padding-bottom: 1rem;
    border-bottom: 1px solid #e2e8f0;
    flex-wrap: wrap;
    gap: 0.5rem;
  }
  h1 { font-size: 1.5rem; color: #0f172a; }
  h1 span { color: #0284c7; }
  .meta { font-size: 0.9rem; color: #64748b; text-align: right; }

  .statbar {
    display: flex;
    flex-wrap: wrap;
    gap: 1.25rem;
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    padding: 0.9rem 1.1rem;
    margin-bottom: 1.25rem;
  }
  .statbar-item {
    display: flex;
    align-items: baseline;
    gap: 0.4rem;
    font-size: 1rem;
  }
  .statbar-label { color: #64748b; font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.04em; }
  .statbar-value { font-family: monospace; font-weight: 600; }
  .statbar-sub { color: #64748b; font-size: 0.85rem; }
  .statbar-uptime { color: #334155; font-size: 0.9rem; }

  .section-title {
    font-size: 1.15rem;
    color: #334155;
    margin: 1.25rem 0 0.6rem;
    display: flex;
    align-items: baseline;
    gap: 0.5rem;
  }
  .section-count { color: #94a3b8; font-size: 0.85rem; font-weight: normal; }

  .site-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(210px, 1fr));
    gap: 0.6rem;
  }
  .site-card {
    display: block;
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    padding: 0.75rem 0.9rem;
    text-decoration: none;
    color: inherit;
  }
  .site-card:hover { background: #f1f5f9; }
  .site-card-top { display: flex; align-items: center; gap: 0.4rem; margin-bottom: 0.3rem; }
  .site-domain { font-size: 1rem; color: #0f172a; font-weight: 500; overflow-wrap: anywhere; }
  .tls-badge { font-size: 0.85rem; }
  .site-visitors { font-size: 0.9rem; color: #0284c7; }
  .no-link { color: #94a3b8; }

  .sparkline {
    display: flex;
    align-items: flex-end;
    gap: 3px;
    height: 24px;
    margin-top: 0.5rem;
  }
  .spark-bar {
    flex: 1;
    background: #bae6fd;
    border-radius: 2px 2px 0 0;
    min-width: 4px;
  }
  .site-card:hover .spark-bar { background: #7dd3fc; }

  .chip-row { display: flex; flex-wrap: wrap; gap: 0.5rem; }
  .chip {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    background: #ffffff;
    border: 1px solid #e2e8f0;
    color: #1e293b;
    padding: 0.45rem 0.75rem;
    border-radius: 6px;
    font-size: 0.95rem;
    text-decoration: none;
  }
  .chip-link:hover { background: #f1f5f9; }
  .chip-icon { font-size: 1rem; }
  .chip-count { color: #64748b; font-size: 0.85rem; }

  .count {
    font-size: 0.9rem;
    color: #64748b;
    margin-top: 0.75rem;
  }
"""

DETAIL_EXTRA_CSS = """
  .back-link {
    display: inline-block;
    font-size: 0.9rem;
    color: #64748b;
    text-decoration: none;
    margin-bottom: 1rem;
  }
  .back-link:hover { color: #0284c7; }

  .cta {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    background: #0284c7;
    color: #ffffff;
    text-decoration: none;
    font-weight: 500;
    font-size: 0.95rem;
    padding: 0.6rem 1.1rem;
    border-radius: 6px;
    white-space: nowrap;
  }
  .cta:hover { background: #0369a1; }

  .chart {
    display: flex;
    align-items: flex-end;
    gap: 4px;
    height: 170px;
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    padding: 1rem 0.75rem 0.5rem;
    margin-bottom: 1.5rem;
  }
  .chart-col {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: flex-end;
    height: 100%;
    gap: 4px;
  }
  .chart-bar {
    width: 100%;
    max-width: 14px;
    background: #7dd3fc;
    border-radius: 2px 2px 0 0;
  }
  .chart-tick { font-size: 0.7rem; color: #94a3b8; height: 14px; line-height: 14px; }

  .detail-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 1.25rem;
  }
  @media (max-width: 480px) {
    .detail-grid { grid-template-columns: 1fr; }
  }
  .list-box {
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    overflow: hidden;
  }
  .list-row {
    display: flex;
    justify-content: space-between;
    gap: 0.75rem;
    padding: 0.55rem 0.8rem;
    border-bottom: 1px solid #f1f5f9;
    font-size: 0.85rem;
  }
  .list-row:last-child { border-bottom: none; }
  .list-key {
    color: #1e293b;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .list-val { color: #0284c7; font-weight: 500; flex-shrink: 0; }
"""

DETAIL_STYLE = f"<style>{BASE_CSS}{DETAIL_EXTRA_CSS}</style>"

def get_system_stats():
    """Returns dict with cpu/memory/disk/uptime stats."""
    stats = {}

    # CPU
    # top -bn1 (single sample) is unreliable: it needs two readings over an
    # interval to compute an accurate instantaneous %, and can produce a
    # malformed/missing "id" field on a true single-shot run. Take two
    # samples and use the second (accurate) one. Fall back to load average
    # (not a hardcoded 100%) if parsing ever fails, so a parse hiccup can't
    # masquerade as a genuine CPU spike.
    try:
        out = subprocess.run(["top", "-bn2", "-d", "0.2"], capture_output=True, text=True).stdout
        # Anchored to the actual "%Cpu(s):" summary line so this can't match
        # a process-table entry that happens to contain "<number> id..."
        # (e.g. the "idle_inject" kernel threads, truncated to "idle_in+").
        matches = re.findall(r'%Cpu\(s\):.*?(\d+\.\d+)\s+id', out)
        if matches:
            idle = float(matches[-1])
            stats["cpu_pct"] = round(100.0 - idle, 1)
        else:
            raise ValueError("could not parse top output")
    except Exception:
        try:
            load1 = os.getloadavg()[0]
            stats["cpu_pct"] = round(min(load1 / os.cpu_count() * 100.0, 100.0), 1)
        except Exception:
            stats["cpu_pct"] = 0.0

    # Memory
    try:
        out = subprocess.run(["free", "-b"], capture_output=True, text=True).stdout
        for line in out.splitlines():
            if line.startswith("Mem:"):
                parts = line.split()
                total = int(parts[1])
                available = int(parts[6])
                used = total - available
                stats["mem_total_gb"] = round(total / 1024**3, 1)
                stats["mem_used_gb"]  = round(used  / 1024**3, 1)
                stats["mem_pct"]      = round(used / total * 100, 1)
                break
    except Exception:
        stats.update({"mem_total_gb": 0, "mem_used_gb": 0, "mem_pct": 0})

    # Disk (root)
    try:
        out = subprocess.run(["df", "-B1", "/"], capture_output=True, text=True).stdout
        parts = out.splitlines()[1].split()
        total = int(parts[1])
        used  = int(parts[2])
        stats["disk_total_gb"] = round(total / 1024**3, 1)
        stats["disk_used_gb"]  = round(used  / 1024**3, 1)
        stats["disk_pct"]      = round(used / total * 100, 1)
    except Exception:
        stats.update({"disk_total_gb": 0, "disk_used_gb": 0, "disk_pct": 0})

    # Uptime
    try:
        stats["uptime"] = subprocess.run(
            ["uptime", "-p"], capture_output=True, text=True
        ).stdout.strip()
    except Exception:
        stats["uptime"] = ""

    return stats

MONTHS = {"Jan":1,"Feb":2,"Mar":3,"Apr":4,"May":5,"Jun":6,
          "Jul":7,"Aug":8,"Sep":9,"Oct":10,"Nov":11,"Dec":12}
LOG_DATE_RE = re.compile(r'\[(\d{2})/(\w{3})/(\d{4}):')


def _open_log(path):
    if path.endswith(".gz"):
        return gzip.open(path, "rt", errors="replace")
    return open(path, errors="replace")


def _log_paths_for_domain(domain, max_rotations=14):
    """Returns existing log file paths for a domain: today's live log plus
    whatever rotations exist (logrotate: .1 is plain, .2.gz+ are compressed).
    Right now most sites only have the base file since dedicated per-site
    logging just started — this naturally grows as rotations accumulate."""
    base = os.path.join(NGINX_LOG_DIR, f"{domain}.access.log")
    paths = [base] if os.path.exists(base) else []
    for n in range(1, max_rotations + 1):
        p = base + (f".{n}" if n == 1 else f".{n}.gz")
        if os.path.exists(p):
            paths.append(p)
    return paths


def get_site_visitors(domain):
    """Returns dict with total hits and unique visitor IPs for a domain, across all available log data (current + any rotations)."""
    stats = {"total": 0, "unique": 0, "available": False}
    log_paths = _log_paths_for_domain(domain)
    if not log_paths:
        return stats
    try:
        ips = set()
        total = 0
        for path in log_paths:
            with _open_log(path) as f:
                for line in f:
                    m = re.match(r'^(\S+)\s', line)
                    if not m:
                        continue
                    total += 1
                    ips.add(m.group(1))
        stats["total"] = total
        stats["unique"] = len(ips)
        stats["available"] = True
    except Exception:
        pass
    return stats


def get_site_daily_hits(domain, days=7):
    """Returns list of {date, count} for the last `days` calendar days
    (oldest first), by parsing timestamps out of the access log(s) rather
    than assuming one rotated file = one day. Days with no data (including
    all days before per-site logging existed) show count=0 so callers
    always get exactly `days` entries for a sparkline."""
    today = datetime.now().date()
    counts = {today - timedelta(days=i): 0 for i in range(days)}

    for path in _log_paths_for_domain(domain):
        try:
            with _open_log(path) as f:
                for line in f:
                    m = LOG_DATE_RE.search(line)
                    if not m:
                        continue
                    day, mon, year = m.groups()
                    month = MONTHS.get(mon)
                    if not month:
                        continue
                    d = datetime(int(year), month, int(day)).date()
                    if d in counts:
                        counts[d] += 1
        except Exception:
            continue

    return [{"date": d, "count": counts[d]} for d in sorted(counts)]


LOG_LINE_RE = re.compile(
    r'^(?P<ip>\S+) \S+ \S+ \[(?P<day>\d{2})/(?P<mon>\w{3})/(?P<year>\d{4}):[^\]]+\] '
    r'"(?P<method>\S+) (?P<path>\S+)[^"]*" (?P<status>\d+) \S+ '
    r'"(?P<referrer>[^"]*)" "(?P<agent>[^"]*)"'
)


def get_site_detail(domain, days=30):
    """Single-pass log analysis for a site's detail page: daily hit counts
    for the last `days` days, all-time totals/unique IPs (bounded by however
    much log history is available), and top request paths / referrers."""
    today = datetime.now().date()
    counts = {today - timedelta(days=i): 0 for i in range(days)}
    ips = set()
    total = 0
    paths = Counter()
    referrers = Counter()

    for path in _log_paths_for_domain(domain):
        try:
            with _open_log(path) as f:
                for line in f:
                    m = LOG_LINE_RE.match(line)
                    if not m:
                        continue
                    total += 1
                    ips.add(m.group("ip"))

                    month = MONTHS.get(m.group("mon"))
                    if month:
                        d = datetime(int(m.group("year")), month, int(m.group("day"))).date()
                        if d in counts:
                            counts[d] += 1

                    paths[m.group("path")] += 1

                    ref = m.group("referrer")
                    if ref and ref != "-" and domain not in ref:
                        referrers[ref] += 1
        except Exception:
            continue

    return {
        "daily": [{"date": d, "count": counts[d]} for d in sorted(counts)],
        "total": total,
        "unique": len(ips),
        "top_paths": paths.most_common(10),
        "top_referrers": referrers.most_common(8),
    }


def get_hosted_sites():
    """Returns list of {domain, https} dicts for real domains configured in nginx sites-enabled (excludes catch-all/default server blocks)."""
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

            for m in re.finditer(r'server_name\s+([^;]+);', content):
                for name in m.group(1).split():
                    name = name.strip()
                    if name in ("_", "") or name.startswith("www."):
                        continue
                    prev = sites.get(name, False)
                    sites[name] = prev or has_https
    except Exception:
        pass

    return sorted(sites.items())


def detail_filename(domain):
    """Filename for a site's detail page. Domain names only ever contain
    letters, digits, dots, and hyphens, so this is safe to use as-is."""
    return f"site-{domain}.html"


def bar_color(pct):
    if pct >= 85:
        return "#ef4444"
    if pct >= 60:
        return "#f59e0b"
    return "#38bdf8"

def scan_ports():
    """Returns list of {port, pid, process} dicts for listening TCP ports."""
    try:
        result = subprocess.run(
            ["ss", "-tlnp"],
            capture_output=True, text=True, check=True
        )
    except subprocess.CalledProcessError:
        return []

    ports = []
    for line in result.stdout.splitlines()[1:]:
        m = re.search(r':(\d+)\s', line)
        if not m:
            continue
        port = int(m.group(1))

        proc_m = re.search(r'users:\(\("([^"]+)"', line)
        process = proc_m.group(1) if proc_m else "unknown"

        pid_m = re.search(r'pid=(\d+)', line)
        pid = pid_m.group(1) if pid_m else "-"

        ports.append({"port": port, "process": process, "pid": pid})

    seen = set()
    unique = []
    for p in sorted(ports, key=lambda x: x["port"]):
        if p["port"] not in seen:
            seen.add(p["port"])
            unique.append(p)
    return unique


def render_sparkline(daily):
    """Renders a 7-bar sparkline for a list of {date, count} dicts (oldest
    first). Bar height is relative to the max day in the window; a day with
    zero hits still renders a thin visible tick rather than disappearing."""
    max_count = max((d["count"] for d in daily), default=0) or 1
    bars = ""
    for d in daily:
        h = max(3, round(d["count"] / max_count * 24))
        label = d["date"].strftime("%b %d")
        bars += f'<span class="spark-bar" style="height:{h}px" title="{label}: {d["count"]} hits"></span>'
    return f'<div class="sparkline">{bars}</div>'


def render_chart(daily):
    """Renders a larger bar chart (for the detail page) from a list of
    {date, count} dicts, oldest first, with a date label every 5th bar."""
    max_count = max((d["count"] for d in daily), default=0) or 1
    bars = ""
    for i, d in enumerate(daily):
        h = max(4, round(d["count"] / max_count * 140))
        label = d["date"].strftime("%b %d")
        show_label = (i % 5 == 0) or (i == len(daily) - 1)
        tick_text = d["date"].strftime("%-d") if show_label else ""
        tick = f'<span class="chart-tick">{tick_text}</span>'
        bars += (
            f'<div class="chart-col">'
            f'<span class="chart-bar" style="height:{h}px" title="{label}: {d["count"]} hits"></span>'
            f'{tick}'
            f'</div>'
        )
    return f'<div class="chart">{bars}</div>'


def build_detail_html(domain, https, hostname, detail):
    proto = "https" if https else "http"
    url   = f"{proto}://{domain}"
    now   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    chart_html = render_chart(detail["daily"])

    if detail["top_paths"]:
        paths_rows = "".join(
            f'<div class="list-row"><span class="list-key">{html.escape(p)}</span><span class="list-val">{c}</span></div>'
            for p, c in detail["top_paths"]
        )
    else:
        paths_rows = '<p class="no-link">No data yet.</p>'

    if detail["top_referrers"]:
        ref_rows = "".join(
            f'<div class="list-row"><span class="list-key">{html.escape(r)}</span><span class="list-val">{c}</span></div>'
            for r, c in detail["top_referrers"]
        )
    else:
        ref_rows = '<p class="no-link">No external referrers yet.</p>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{domain} — Traffic</title>
{DETAIL_STYLE}
</head>
<body>
<a class="back-link" href="index.html">&larr; All sites</a>
<header>
  <div>
    <h1>{domain}</h1>
    <div class="meta">Last generated: {now}</div>
  </div>
  <a class="cta" href="{url}" target="_blank">Open site &#8599;</a>
</header>

<div class="statbar">
  <span class="statbar-item"><span class="statbar-label">Total hits</span><span class="statbar-value">{detail['total']}</span></span>
  <span class="statbar-item"><span class="statbar-label">Unique visitors</span><span class="statbar-value">{detail['unique']}</span></span>
</div>

<h2 class="section-title">Daily hits <span class="section-count">last 30 days</span></h2>
{chart_html}

<div class="detail-grid">
  <div>
    <h2 class="section-title">Top pages</h2>
    <div class="list-box">{paths_rows}</div>
  </div>
  <div>
    <h2 class="section-title">Top referrers</h2>
    <div class="list-box">{ref_rows}</div>
  </div>
</div>

</body>
</html>"""


def group_ports_by_service(ports, server_ip):
    """Groups individual listening ports into one entry per named service,
    e.g. Mailu's 9 ports collapse into a single 'Mailu Front (9 ports)' entry
    instead of 9 near-identical table rows."""
    groups = {}
    order = []
    for p in ports:
        port    = p["port"]
        process = p["process"]
        info    = KNOWN_PORTS.get(port, {})
        name    = info.get("name") or process.capitalize()
        icon    = info.get("icon") or "\U0001f4e6"
        proto   = info.get("proto")
        path    = info.get("path", "")

        if name not in groups:
            groups[name] = {"name": name, "icon": icon, "ports": [], "link": None}
            order.append(name)
        g = groups[name]
        g["ports"].append(port)
        if icon and icon != "\U0001f4e6":
            g["icon"] = icon
        if proto and not g["link"]:
            link_port = "" if port in (80, 443) else f":{port}"
            g["link"] = f"{proto}://{server_ip}{link_port}{path}"

    return [groups[n] for n in order]


def build_html(ports):
    hostname  = subprocess.run(["hostname"], capture_output=True, text=True).stdout.strip()
    server_ip = get_server_ip()
    now       = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    s         = get_system_stats()
    sites     = get_hosted_sites()

    cpu_color  = bar_color(s["cpu_pct"])
    mem_color  = bar_color(s["mem_pct"])
    disk_color = bar_color(s["disk_pct"])

    statbar_html = f"""
<div class="statbar">
  <span class="statbar-item"><span class="statbar-label">CPU</span><span class="statbar-value" style="color:{cpu_color}">{s['cpu_pct']}%</span></span>
  <span class="statbar-item"><span class="statbar-label">Memory</span><span class="statbar-value" style="color:{mem_color}">{s['mem_pct']}%</span><span class="statbar-sub">{s['mem_used_gb']}/{s['mem_total_gb']} GB</span></span>
  <span class="statbar-item"><span class="statbar-label">Disk</span><span class="statbar-value" style="color:{disk_color}">{s['disk_pct']}%</span><span class="statbar-sub">{s['disk_used_gb']}/{s['disk_total_gb']} GB</span></span>
  <span class="statbar-item"><span class="statbar-label">Uptime</span><span class="statbar-value statbar-uptime">{s['uptime']}</span></span>
</div>"""

    # Sort by 7-day traffic (busiest first) so the sites that matter surface
    # first, instead of alphabetical order.
    site_data = []
    for domain, https in sites:
        visitor_stats = get_site_visitors(domain)
        daily = get_site_daily_hits(domain, days=7)
        week_total = sum(d["count"] for d in daily)
        site_data.append((domain, https, visitor_stats, daily, week_total))
    site_data.sort(key=lambda x: x[4], reverse=True)

    site_cards = ""
    for domain, https, visitor_stats, daily, week_total in site_data:
        if visitor_stats["available"]:
            visitors = f"{week_total} hits (7d) &middot; {visitor_stats['unique']} unique"
        else:
            visitors = '<span class="no-link">no data</span>'
        tls_badge = '<span class="tls-badge">\U0001f512</span>' if https else ""
        sparkline = render_sparkline(daily)
        site_cards += f"""
        <a class="site-card" href="{detail_filename(domain)}">
            <div class="site-card-top">{tls_badge}<span class="site-domain">{domain}</span></div>
            <div class="site-visitors">{visitors}</div>
            {sparkline}
        </a>"""

    sites_html = f"""
<h2 class="section-title">Hosted sites <span class="section-count">{len(sites)}</span></h2>
<div class="site-grid">
    {site_cards}
</div>"""

    groups = group_ports_by_service(ports, server_ip)
    chips = ""
    for g in groups:
        port_list = ", ".join(str(p) for p in sorted(g["ports"]))
        count = f' <span class="chip-count">{len(g["ports"])}</span>' if len(g["ports"]) > 1 else ""
        inner = f'<span class="chip-icon">{g["icon"]}</span>{g["name"]}{count}'
        if g["link"]:
            chips += f'<a class="chip chip-link" href="{g["link"]}" target="_blank" title="ports: {port_list}">{inner}</a>'
        else:
            chips += f'<span class="chip" title="ports: {port_list}">{inner}</span>'

    services_html = f"""
<h2 class="section-title">Services <span class="section-count">{len(groups)}</span></h2>
<div class="chip-row">
    {chips}
</div>
<p class="count">{len(ports)} ports listening</p>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="60">
<title>{hostname} — Services</title>
<style>{BASE_CSS}</style>
</head>
<body>
<header>
  <h1>\U0001f5a5️ <span>{hostname}</span> — Running Services</h1>
  <div class="meta">
    Auto-refreshes every 60s<br>
    Last generated: {now}
  </div>
</header>
{statbar_html}
{sites_html}
{services_html}
</body>
</html>"""


if __name__ == "__main__":
    output_dir = os.path.dirname(OUTPUT_PATH)
    os.makedirs(output_dir, exist_ok=True)

    ports = scan_ports()
    index_html = build_html(ports)
    with open(OUTPUT_PATH, "w") as f:
        f.write(index_html)

    hostname = subprocess.run(["hostname"], capture_output=True, text=True).stdout.strip()
    sites = get_hosted_sites()
    for domain, https in sites:
        detail = get_site_detail(domain, days=30)
        detail_html = build_detail_html(domain, https, hostname, detail)
        with open(os.path.join(output_dir, detail_filename(domain)), "w") as f:
            f.write(detail_html)

    print(f"Generated {OUTPUT_PATH} with {len(ports)} services and {len(sites)} site detail pages")
