"""
generate.py — Static site generator for eBay sold price agent.
Writes index.html + one page per config to OUTPUT_DIR.
Run after scraping, or on its own schedule.
"""

import re
import sys
from pathlib import Path
from db import get_conn, get_listings
from analyze import stats_for_config

OUTPUT_DIR = Path("/home/sohaib/sites/ebaylisting")
DAYS = 90


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

    /* Cards */
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

    /* Detail page */
    .stat-row{{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:24px}}
    .scard{{background:#fff;border-radius:8px;padding:14px 18px;box-shadow:0 1px 4px rgba(0,0,0,.07);text-align:center;min-width:110px}}
    .scard .sv{{font-size:1.3rem;font-weight:700;color:#2563eb}}
    .scard .sl{{font-size:.7rem;color:#888;margin-top:2px}}
    .days-bar{{display:flex;gap:8px;margin-bottom:20px;align-items:center}}
    .days-bar span{{font-size:.85rem;color:#666}}
    .days-bar a{{padding:4px 12px;border-radius:20px;font-size:.82rem;text-decoration:none;border:1px solid #cbd5e1;color:#444}}
    .days-bar a.active{{background:#2563eb;color:#fff;border-color:#2563eb}}
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
  <span class="brand">📦 eBay Sold Prices</span>
  <a href="index.html">All searches</a>
</nav>
<div class="wrap">
"""


def _foot() -> str:
    return "</div></body></html>\n"


def build_index(configs_stats: list[dict]) -> str:
    cards = ""
    for s in configs_stats:
        href = f"{slug(s['name'])}.html"
        inactive = "" if s["active"] else ' <span class="badge">inactive</span>'
        if s["count"]:
            stats_html = f"""
            <div class="stats">
              <div class="stat"><div class="val">${s['avg']:,.0f}</div><div class="lbl">avg</div></div>
              <div class="stat"><div class="val">${s['median']:,.0f}</div><div class="lbl">median</div></div>
              <div class="stat"><div class="val">${s['min']:,.0f}–${s['max']:,.0f}</div><div class="lbl">range</div></div>
            </div>
            <div class="meta">
              <span>{s['count']} listing{'s' if s['count'] != 1 else ''}</span>
              <span>{"last scraped " + s['last_scraped'][:10] if s['last_scraped'] else ''}</span>
            </div>"""
        else:
            stats_html = '<p style="color:#aaa;font-size:.85rem;margin-top:8px">No data yet</p>'

        cards += f"""
        <a class="card" href="{href}">
          <h3>{s['name']}{inactive}</h3>
          <div class="query">{s['query']}</div>
          {stats_html}
        </a>"""

    return (
        _head("eBay Sold Prices") +
        "<h2>Search Terms</h2>" +
        f'<p class="sub">Last {DAYS} days</p>' +
        f'<div class="grid">{cards}</div>' +
        _foot()
    )


def build_config_page(cfg: dict, all_stats_by_days: dict, listings: list[dict], days: int) -> str:
    s = all_stats_by_days[days]

    stat_cards = ""
    if s.get("count"):
        for val, lbl in [
            (fmt_price(s.get("avg")),     "average"),
            (fmt_price(s.get("median")),  "median"),
            (fmt_price(s.get("min")),     "min"),
            (fmt_price(s.get("max")),     "max"),
            (fmt_price(s.get("std_dev")), "std dev"),
            (str(s.get("count", 0)),      "listings"),
        ]:
            stat_cards += f'<div class="scard"><div class="sv">{val}</div><div class="sl">{lbl}</div></div>'

    rows = ""
    for item in listings:
        total = item.get("total_price") or item.get("sold_price") or 0
        ship = item.get("shipping_cost") or 0
        ship_str = f'<br><span style="color:#aaa;font-size:.75rem">+{fmt_price(ship)} ship</span>' if ship else ""
        cond = f'<span class="badge">{item["condition"]}</span>' if item.get("condition") else "—"
        title_cell = (
            f'<a href="{item["listing_url"]}" target="_blank" rel="noopener">{item["title"]}</a>'
            if item.get("listing_url") else item["title"]
        )
        rows += f"""<tr>
          <td>{item.get('sold_date','—') or '—'}</td>
          <td>{title_cell}</td>
          <td>{cond}</td>
          <td class="r">{fmt_price(item.get('sold_price'))}</td>
          <td class="r"><strong>{fmt_price(total)}</strong>{ship_str}</td>
        </tr>"""

    if not rows:
        rows = '<tr><td colspan="5" class="no-data">No listings for this period.</td></tr>'

    days_links = ""
    for d in [30, 60, 90, 180, 365]:
        active = ' class="active"' if d == days else ""
        days_links += f'<a href="{slug(cfg["name"])}.html?d={d}"{active}>{d}d</a>'

    table = f"""
    <table>
      <thead><tr>
        <th>Sold Date</th><th>Title</th><th>Condition</th>
        <th class="r">Item Price</th><th class="r">Total</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>"""

    return (
        _head(cfg["name"] + " — eBay Sold Prices") +
        f'<a class="back" href="index.html">← All searches</a>' +
        f'<h2>{cfg["name"]}</h2>' +
        f'<p class="sub">query: <em>{cfg["query"]}</em></p>' +
        f'<div class="days-bar"><span>Show last:</span>{days_links}</div>' +
        f'<div class="stat-row">{stat_cards}</div>' +
        table +
        _foot()
    )


def _last_scraped(config_name: str):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT MAX(scraped_at) AS ts FROM sold_listings WHERE search_config = ?",
            (config_name,)
        ).fetchone()
    return row["ts"] if row else None


def generate(output_dir: Path = OUTPUT_DIR):
    output_dir.mkdir(parents=True, exist_ok=True)

    with get_conn() as conn:
        configs = [dict(r) for r in conn.execute(
            "SELECT * FROM search_configs ORDER BY name"
        ).fetchall()]

    if not configs:
        print("No configs found — nothing to generate.")
        return

    # Build index data
    index_data = []
    for cfg in configs:
        s = stats_for_config(cfg["name"], days=DAYS)
        index_data.append({**s, "name": cfg["name"], "query": cfg["query"],
                            "active": cfg["active"],
                            "last_scraped": _last_scraped(cfg["name"])})

    (output_dir / "index.html").write_text(build_index(index_data), encoding="utf-8")
    print(f"  index.html")

    for cfg in configs:
        # Pre-compute stats for all day windows
        all_stats = {d: stats_for_config(cfg["name"], days=d) for d in [30, 60, 90, 180, 365]}
        # Default view: 90 days
        listings = get_listings(cfg["name"], days=90)
        listings.sort(key=lambda x: (x["sold_date"] is None, x["sold_date"]), reverse=True)

        fname = f"{slug(cfg['name'])}.html"
        html = build_config_page(cfg, all_stats, listings, days=90)
        (output_dir / fname).write_text(html, encoding="utf-8")
        print(f"  {fname}  ({len(listings)} listings)")

    print(f"Done → {output_dir}")


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else OUTPUT_DIR
    generate(out)
