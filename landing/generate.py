#!/usr/bin/env python3
"""Generates a landing page by scanning listening TCP ports on the server."""

import subprocess
import re
import os
import json
from datetime import datetime

OUTPUT_PATH = os.environ.get("OUTPUT_PATH", "/home/sohaib/sites/landing/index.html")
PORTS_META  = os.path.join(os.path.dirname(__file__), "ports.json")

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

def get_system_stats():
    """Returns dict with cpu/memory/disk/uptime stats."""
    stats = {}

    # CPU
    try:
        out = subprocess.run(["top", "-bn1"], capture_output=True, text=True).stdout
        m = re.search(r'(\d+\.\d+)\s+id', out)
        idle = float(m.group(1)) if m else 0.0
        stats["cpu_pct"] = round(100.0 - idle, 1)
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


def build_html(ports):
    hostname  = subprocess.run(["hostname"], capture_output=True, text=True).stdout.strip()
    server_ip = get_server_ip()
    now       = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    s         = get_system_stats()

    cpu_color  = bar_color(s["cpu_pct"])
    mem_color  = bar_color(s["mem_pct"])
    disk_color = bar_color(s["disk_pct"])

    stats_html = f"""
<div class="stats-grid">
  <div class="stat-card">
    <div class="stat-header">
      <span class="stat-label">CPU</span>
      <span class="stat-value">{s['cpu_pct']}%</span>
    </div>
    <div class="bar-track">
      <div class="bar-fill" style="width:{s['cpu_pct']}%; background:{cpu_color}"></div>
    </div>
    <div class="stat-sub">utilisation</div>
  </div>
  <div class="stat-card">
    <div class="stat-header">
      <span class="stat-label">Memory</span>
      <span class="stat-value">{s['mem_used_gb']} / {s['mem_total_gb']} GB</span>
    </div>
    <div class="bar-track">
      <div class="bar-fill" style="width:{s['mem_pct']}%; background:{mem_color}"></div>
    </div>
    <div class="stat-sub">{s['mem_pct']}% used</div>
  </div>
  <div class="stat-card">
    <div class="stat-header">
      <span class="stat-label">Disk</span>
      <span class="stat-value">{s['disk_used_gb']} / {s['disk_total_gb']} GB</span>
    </div>
    <div class="bar-track">
      <div class="bar-fill" style="width:{s['disk_pct']}%; background:{disk_color}"></div>
    </div>
    <div class="stat-sub">{s['disk_pct']}% used</div>
  </div>
  <div class="stat-card uptime-card">
    <div class="stat-label">Uptime</div>
    <div class="uptime-val">{s['uptime']}</div>
  </div>
</div>"""

    rows = ""
    for p in ports:
        port    = p["port"]
        process = p["process"]
        pid     = p["pid"]
        info    = KNOWN_PORTS.get(port, {})
        name    = info.get("name", process.capitalize())
        icon    = info.get("icon", "\U0001f4e6")
        proto   = info.get("proto")
        path    = info.get("path", "")

        if proto:
            link_port = "" if port in (80, 443) else f":{port}"
            url  = f"{proto}://{server_ip}{link_port}{path}"
            link = f'<a href="{url}" target="_blank">{url}</a>'
        else:
            link = '<span class="no-link">—</span>'

        rows += f"""
        <tr>
            <td><span class="icon">{icon}</span> {name}</td>
            <td><span class="port">{port}</span></td>
            <td>{process}</td>
            <td>{pid}</td>
            <td>{link}</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="60">
<title>{hostname} — Services</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: #0f172a;
    color: #e2e8f0;
    min-height: 100vh;
    padding: 2rem;
  }}
  header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 1.5rem;
    padding-bottom: 1rem;
    border-bottom: 1px solid #1e293b;
  }}
  h1 {{ font-size: 1.5rem; color: #f8fafc; }}
  h1 span {{ color: #38bdf8; }}
  .meta {{ font-size: 0.8rem; color: #64748b; text-align: right; }}

  .stats-grid {{
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 1rem;
    margin-bottom: 1.5rem;
  }}
  .stat-card {{
    background: #1e293b;
    border-radius: 8px;
    padding: 1rem 1.25rem;
  }}
  .stat-header {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 0.5rem;
  }}
  .stat-label {{
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: #64748b;
  }}
  .stat-value {{
    font-size: 0.9rem;
    font-weight: 600;
    color: #f1f5f9;
    font-family: monospace;
  }}
  .bar-track {{
    background: #0f172a;
    border-radius: 4px;
    height: 6px;
    overflow: hidden;
  }}
  .bar-fill {{
    height: 100%;
    border-radius: 4px;
  }}
  .stat-sub {{
    font-size: 0.75rem;
    color: #475569;
    margin-top: 0.4rem;
  }}
  .uptime-card {{
    display: flex;
    flex-direction: column;
    justify-content: center;
  }}
  .uptime-val {{
    font-size: 0.95rem;
    color: #38bdf8;
    font-weight: 500;
    margin-top: 0.4rem;
  }}

  table {{
    width: 100%;
    border-collapse: collapse;
    background: #1e293b;
    border-radius: 8px;
    overflow: hidden;
  }}
  th {{
    background: #0f172a;
    padding: 0.75rem 1rem;
    text-align: left;
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: #64748b;
  }}
  td {{
    padding: 0.85rem 1rem;
    border-top: 1px solid #0f172a;
    font-size: 0.9rem;
  }}
  tr:hover td {{ background: #263347; }}
  .port {{
    background: #0f172a;
    color: #38bdf8;
    padding: 2px 8px;
    border-radius: 4px;
    font-family: monospace;
    font-size: 0.85rem;
  }}
  .icon {{ font-size: 1.1rem; }}
  a {{ color: #38bdf8; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .no-link {{ color: #475569; }}
  .count {{
    font-size: 0.85rem;
    color: #64748b;
    margin-top: 1rem;
  }}
</style>
</head>
<body>
<header>
  <h1>\U0001f5a5️ <span>{hostname}</span> — Running Services</h1>
  <div class="meta">
    Auto-refreshes every 60s<br>
    Last generated: {now}
  </div>
</header>
{stats_html}
<table>
  <thead>
    <tr>
      <th>Service</th>
      <th>Port</th>
      <th>Process</th>
      <th>PID</th>
      <th>Link</th>
    </tr>
  </thead>
  <tbody>
    {rows}
  </tbody>
</table>
<p class="count">{len(ports)} services listening</p>
</body>
</html>"""


if __name__ == "__main__":
    ports = scan_ports()
    html = build_html(ports)
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        f.write(html)
    print(f"Generated {OUTPUT_PATH} with {len(ports)} services")
