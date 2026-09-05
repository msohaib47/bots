"""
generate.py — Static dashboard for EbayNewListingsBot.
Writes index.html + one page per search config to OUTPUT_DIR.
Run after each bot.py pass (see wrapper script), or on its own schedule.
"""

import re
import sys
import json
from pathlib import Path

from config import CONFIG_FILE
from db import get_conn

OUTPUT_DIR = Path("/home/sohaib/sites/ebaynewlisting")
DAYS = 14


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def fmt_price(val) -> str:
    if val is None:
        return "—"
    return f"${val:,.2f}"


def _head(title: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:system-ui,sans-serif;background:#f4f5f7;color:#222}}
    nav{{background:#1a1a2e;color:#fff;padding:14px 24px;display:flex;align-items:center;gap:12px}}
    nav a{{color:#aac4ff;text-decoration:none;font-size:.9rem}}
    nav .brand{{font-weight:700;font-size:1.1rem;color:#fff}}
    .wrap{{max-width:1100px;margin:28px auto;padding:0 16px}}
    h2{{font-size:1.4rem;margin-bottom:4px}}
    .sub{{color:#666;font-size:.85rem;margin-bottom:24px}}

    .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px}}
    .card{{background:#fff;border-radius:10px;padding:20px;box-shadow:0 1px 4px rgba(0,0,0,.08);text-decoration:none;color:inherit;display:block;transition:box-shadow .15s}}
    .card:hover{{box-shadow:0 4px 14px rgba(0,0,0,.13)}}
    .card h3{{font-size:1rem;margin-bottom:4px}}
    .card .query{{color:#888;font-size:.8rem;margin-bottom:14px}}
    .stats{{display:flex;gap:10px;flex-wrap:wrap}}
    .stat{{background:#f0f4ff;border-radius:6px;padding:8px 12px;text-align:center;min-width:70px}}
    .stat .val{{font-weight:700;font-size:1rem;color:#2563eb}}
    .stat .lbl{{font-size:.65rem;color:#666;margin-top:2px}}
    .meta{{font-size:.75rem;color:#999;margin-top:12px;display:flex;justify-content:space-between}}
    .badge{{display:inline-block;font-size:.7rem;padding:2px 6px;border-radius:4px;background:#e2e8f0;color:#555}}

    table{{width:100%;border-collapse:collapse;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.08)}}
    thead{{background:#1a1a2e;color:#fff}}
    th{{padding:10px 12px;text-align:left;font-size:.82rem;font-weight:600}}
    th.r,td.r{{text-align:right}}
    td{{padding:9px 12px;font-size:.83rem;border-bottom:1px solid #f1f1f1}}
    tr:last-child td{{border-bottom:none}}
    tr:hover td{{background:#fafbff}}
    td a{{color:#2563eb;text-decoration:none}}
    td a:hover{{text-decoration:underline}}
    .no-data{{text-align:center;padding:40px;color:#888;font-size:.9rem}}
    .back{{display:inline-block;margin-bottom:20px;font-size:.85rem;color:#2563eb;text-decoration:none}}
    .back:hover{{text-decoration:underline}}
  </style>
</head>
<body>
<nav>
  <span class="brand">🔔 eBay New Listings</span>
  <a href="index.html">All searches</a>
</nav>
<div class="wrap">
"""


def _foot() -> str:
    return "</div></body></html>\n"


def build_index(configs: list, counts: dict, latest: dict) -> str:
    cards = ""
    for cfg in configs:
        name = cfg["name"]
        href = f"{slug(name)}.html"
        count = counts.get(name, 0)
        last = latest.get(name)
        price_bits = f'<span class="badge">max ${cfg["max_price"]:,.0f}</span>' if cfg.get("max_price") else ""
        cards += f"""
        <a class="card" href="{href}">
          <h3>{name}</h3>
          <div class="query">{cfg['query']} {price_bits}</div>
          <div class="stats">
            <div class="stat"><div class="val">{count}</div><div class="lbl">seen</div></div>
          </div>
          <div class="meta">
            <span>{'last: ' + last[:16] if last else 'none yet'}</span>
          </div>
        </a>"""

    return (
        _head("eBay New Listings") +
        "<h2>Search Terms</h2>" +
        f'<p class="sub">Newly listed items matched, most recent first</p>' +
        f'<div class="grid">{cards}</div>' +
        _foot()
    )


def build_config_page(cfg: dict, listings: list) -> str:
    rows = ""
    for item in listings:
        title_cell = (
            f'<a href="{item["url"]}" target="_blank" rel="noopener">{item["title"]}</a>'
            if item.get("url") else item["title"]
        )
        qty = item.get("quantity")
        rows += f"""<tr>
          <td>{(item.get('first_seen') or '—')[:16]}</td>
          <td>{title_cell}</td>
          <td class="r">{fmt_price(item.get('price'))}</td>
          <td class="r">{qty if qty is not None else '—'}</td>
        </tr>"""

    if not rows:
        rows = '<tr><td colspan="4" class="no-data">No listings seen yet.</td></tr>'

    table = f"""
    <table>
      <thead><tr>
        <th>First Seen</th><th>Title</th><th class="r">Price</th><th class="r">Qty</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>"""

    return (
        _head(cfg["name"] + " — eBay New Listings") +
        f'<a class="back" href="index.html">← All searches</a>' +
        f'<h2>{cfg["name"]}</h2>' +
        f'<p class="sub">query: <em>{cfg["query"]}</em>'
        + (f' &middot; max ${cfg["max_price"]:,.0f}' if cfg.get("max_price") else '') +
        '</p>' +
        table +
        _foot()
    )


def _load_configs() -> list:
    if not CONFIG_FILE.exists():
        return []
    with open(CONFIG_FILE) as f:
        return json.load(f)


def generate(output_dir: Path = OUTPUT_DIR):
    output_dir.mkdir(parents=True, exist_ok=True)

    configs = _load_configs()
    if not configs:
        print("No configs found — nothing to generate.")
        return

    with get_conn() as conn:
        counts = {
            r["search_name"]: r["c"]
            for r in conn.execute(
                "SELECT search_name, COUNT(*) AS c FROM seen_listings GROUP BY search_name"
            ).fetchall()
        }
        latest = {
            r["search_name"]: r["m"]
            for r in conn.execute(
                "SELECT search_name, MAX(first_seen) AS m FROM seen_listings GROUP BY search_name"
            ).fetchall()
        }

    (output_dir / "index.html").write_text(build_index(configs, counts, latest), encoding="utf-8")
    print("  index.html")

    with get_conn() as conn:
        for cfg in configs:
            name = cfg["name"]
            rows = conn.execute(
                "SELECT title, price, quantity, url, first_seen FROM seen_listings "
                "WHERE search_name = ? ORDER BY first_seen DESC LIMIT 500",
                (name,)
            ).fetchall()
            listings = [dict(r) for r in rows]

            fname = f"{slug(name)}.html"
            html = build_config_page(cfg, listings)
            (output_dir / fname).write_text(html, encoding="utf-8")
            print(f"  {fname}  ({len(listings)} listings)")

    print(f"Done → {output_dir}")


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else OUTPUT_DIR
    generate(out)
