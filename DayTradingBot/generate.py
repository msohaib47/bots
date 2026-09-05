"""
generate.py — Static dashboard for DayTradingBot.
Reads trades.csv and symbols.py, writes a single index.html to OUTPUT_DIR.
Run standalone or after each bot pass.
"""

import csv
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from symbols import SYMBOLS

TRADES_CSV = Path(__file__).parent / "trades.csv"
OUTPUT_DIR = Path("/home/sohaib/sites/daytrading")


def fmt_money(val) -> str:
    sign = "+" if val > 0 else ("-" if val < 0 else "")
    return f"{sign}${abs(val):,.2f}"


def pnl_class(val) -> str:
    if val > 0:
        return "pos"
    if val < 0:
        return "neg"
    return ""


def load_trades() -> list:
    if not TRADES_CSV.exists():
        return []
    with open(TRADES_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["pnl"] = float(r["pnl"] or 0)
        r["contracts"] = int(r["contracts"] or 0)
        r["price"] = float(r["price"] or 0)
        r["dt"] = datetime.fromisoformat(r["timestamp"])
        r["date"] = r["dt"].date().isoformat()
    rows.sort(key=lambda r: r["dt"], reverse=True)
    return rows


def week_key(d: datetime.date) -> str:
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def month_key(d: datetime.date) -> str:
    return d.strftime("%Y-%m")


def build_aggregates(trades: list):
    daily = defaultdict(lambda: {"pnl": 0.0, "count": 0})
    weekly = defaultdict(lambda: {"pnl": 0.0, "count": 0})
    monthly = defaultdict(lambda: {"pnl": 0.0, "count": 0})
    by_symbol = defaultdict(lambda: {"pnl": 0.0, "count": 0, "wins": 0, "losses": 0})

    for t in trades:
        d = t["dt"].date()
        daily[t["date"]]["pnl"] += t["pnl"]
        daily[t["date"]]["count"] += 1
        weekly[week_key(d)]["pnl"] += t["pnl"]
        weekly[week_key(d)]["count"] += 1
        monthly[month_key(d)]["pnl"] += t["pnl"]
        monthly[month_key(d)]["count"] += 1

        sym = t["underlying"]
        by_symbol[sym]["count"] += 1
        by_symbol[sym]["pnl"] += t["pnl"]
        if t["action"] == "CLOSE":
            if t["pnl"] > 0:
                by_symbol[sym]["wins"] += 1
            elif t["pnl"] < 0:
                by_symbol[sym]["losses"] += 1

    return daily, weekly, monthly, by_symbol


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
    nav .brand{{font-weight:700;font-size:1.1rem;color:#fff}}
    .wrap{{max-width:1200px;margin:28px auto;padding:0 16px}}
    h2{{font-size:1.3rem;margin:32px 0 12px}}
    h2:first-of-type{{margin-top:0}}
    .sub{{color:#666;font-size:.85rem;margin-bottom:20px}}

    .hero{{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:8px}}
    .hero-card{{background:#fff;border-radius:10px;padding:20px 28px;box-shadow:0 1px 4px rgba(0,0,0,.08);min-width:160px}}
    .hero-card .val{{font-size:1.8rem;font-weight:700}}
    .hero-card .lbl{{font-size:.75rem;color:#888;text-transform:uppercase;letter-spacing:.04em;margin-top:4px}}
    .pos{{color:#16a34a}}
    .neg{{color:#dc2626}}

    table{{width:100%;border-collapse:collapse;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.08);margin-bottom:8px}}
    thead{{background:#1a1a2e;color:#fff}}
    th{{padding:10px 12px;text-align:left;font-size:.8rem;font-weight:600}}
    th.r,td.r{{text-align:right}}
    td{{padding:8px 12px;font-size:.82rem;border-bottom:1px solid #f1f1f1}}
    tr:last-child td{{border-bottom:none}}
    tr:hover td{{background:#fafbff}}
    .no-data{{text-align:center;padding:30px;color:#888;font-size:.9rem}}
    .badge{{display:inline-block;font-size:.68rem;padding:2px 6px;border-radius:4px;background:#e2e8f0;color:#555}}
    .scroll-box{{max-height:600px;overflow-y:auto;border-radius:10px;box-shadow:0 1px 4px rgba(0,0,0,.08);margin-bottom:8px}}
    .scroll-box table{{box-shadow:none;border-radius:0;margin-bottom:0}}
    .scroll-box thead th{{position:sticky;top:0}}
  </style>
</head>
<body>
<nav>
  <span class="brand">\U0001F4C8 Day Trading Bot</span>
</nav>
<div class="wrap">
"""


def _foot() -> str:
    return "</div></body></html>\n"


def build_dashboard(trades: list, daily: dict, weekly: dict, monthly: dict, by_symbol: dict) -> str:
    total_pnl = sum(t["pnl"] for t in trades)
    closes = [t for t in trades if t["action"] == "CLOSE"]
    wins = sum(1 for t in closes if t["pnl"] > 0)
    losses = sum(1 for t in closes if t["pnl"] < 0)
    win_rate = (wins / len(closes) * 100) if closes else 0

    hero = f"""
    <div class="hero">
      <div class="hero-card"><div class="val {pnl_class(total_pnl)}">{fmt_money(total_pnl)}</div><div class="lbl">Overall P/L</div></div>
      <div class="hero-card"><div class="val">{len(closes)}</div><div class="lbl">Closed Trades</div></div>
      <div class="hero-card"><div class="val">{win_rate:.0f}%</div><div class="lbl">Win Rate ({wins}W / {losses}L)</div></div>
      <div class="hero-card"><div class="val">{len(trades)}</div><div class="lbl">Total Log Entries</div></div>
    </div>"""

    # Symbols table
    sym_rows = ""
    for sym in SYMBOLS:
        s = by_symbol.get(sym, {"pnl": 0.0, "count": 0, "wins": 0, "losses": 0})
        sym_rows += f"""<tr>
          <td><strong>{sym}</strong></td>
          <td class="r">{s['count']}</td>
          <td class="r">{s['wins']}W / {s['losses']}L</td>
          <td class="r {pnl_class(s['pnl'])}">{fmt_money(s['pnl'])}</td>
        </tr>"""
    if not sym_rows:
        sym_rows = '<tr><td colspan="4" class="no-data">No symbol data yet.</td></tr>'

    symbols_table = f"""
    <table>
      <thead><tr><th>Symbol</th><th class="r">Log Entries</th><th class="r">W/L</th><th class="r">P/L</th></tr></thead>
      <tbody>{sym_rows}</tbody>
    </table>"""

    # Daily table (most recent first)
    daily_rows = ""
    for date in sorted(daily.keys(), reverse=True):
        d = daily[date]
        daily_rows += f"""<tr>
          <td>{date}</td>
          <td class="r">{d['count']}</td>
          <td class="r {pnl_class(d['pnl'])}">{fmt_money(d['pnl'])}</td>
        </tr>"""
    if not daily_rows:
        daily_rows = '<tr><td colspan="3" class="no-data">No trades yet.</td></tr>'
    daily_table = f"""
    <div class="scroll-box">
    <table>
      <thead><tr><th>Date</th><th class="r">Entries</th><th class="r">Daily P/L</th></tr></thead>
      <tbody>{daily_rows}</tbody>
    </table>
    </div>"""

    # Weekly table
    weekly_rows = ""
    for wk in sorted(weekly.keys(), reverse=True):
        w = weekly[wk]
        weekly_rows += f"""<tr>
          <td>{wk}</td>
          <td class="r">{w['count']}</td>
          <td class="r {pnl_class(w['pnl'])}">{fmt_money(w['pnl'])}</td>
        </tr>"""
    if not weekly_rows:
        weekly_rows = '<tr><td colspan="3" class="no-data">No trades yet.</td></tr>'
    weekly_table = f"""
    <table>
      <thead><tr><th>Week</th><th class="r">Entries</th><th class="r">Weekly P/L</th></tr></thead>
      <tbody>{weekly_rows}</tbody>
    </table>"""

    # Monthly table
    monthly_rows = ""
    for mo in sorted(monthly.keys(), reverse=True):
        m = monthly[mo]
        monthly_rows += f"""<tr>
          <td>{mo}</td>
          <td class="r">{m['count']}</td>
          <td class="r {pnl_class(m['pnl'])}">{fmt_money(m['pnl'])}</td>
        </tr>"""
    if not monthly_rows:
        monthly_rows = '<tr><td colspan="3" class="no-data">No trades yet.</td></tr>'
    monthly_table = f"""
    <table>
      <thead><tr><th>Month</th><th class="r">Entries</th><th class="r">Monthly P/L</th></tr></thead>
      <tbody>{monthly_rows}</tbody>
    </table>"""

    # Full transaction list
    tx_rows = ""
    for t in trades:
        badge = f'<span class="badge">{t["action"]}</span>'
        pnl_cell = fmt_money(t["pnl"]) if t["action"] == "CLOSE" else "—"
        tx_rows += f"""<tr>
          <td>{t['dt'].strftime('%Y-%m-%d %H:%M')}</td>
          <td>{badge}</td>
          <td>{t['symbol']}</td>
          <td>{t['underlying']}</td>
          <td class="r">{t['contracts']}</td>
          <td class="r">${t['price']:,.2f}</td>
          <td class="r {pnl_class(t['pnl']) if t['action']=='CLOSE' else ''}">{pnl_cell}</td>
          <td>{t['reason']}</td>
        </tr>"""
    if not tx_rows:
        tx_rows = '<tr><td colspan="8" class="no-data">No transactions yet.</td></tr>'
    tx_table = f"""
    <div class="scroll-box">
    <table>
      <thead><tr>
        <th>Time</th><th>Action</th><th>Symbol</th><th>Underlying</th>
        <th class="r">Qty</th><th class="r">Price</th><th class="r">P/L</th><th>Reason</th>
      </tr></thead>
      <tbody>{tx_rows}</tbody>
    </table>
    </div>"""

    return (
        _head("Day Trading Bot Dashboard") +
        hero +
        "<h2>Symbols</h2>" + symbols_table +
        "<h2>Daily P/L</h2>" + daily_table +
        "<h2>Weekly P/L</h2>" + weekly_table +
        "<h2>Monthly P/L</h2>" + monthly_table +
        "<h2>All Transactions</h2>" +
        f'<p class="sub">{len(trades)} log entries, most recent first</p>' +
        tx_table +
        _foot()
    )


def generate(output_dir: Path = OUTPUT_DIR):
    output_dir.mkdir(parents=True, exist_ok=True)
    trades = load_trades()
    daily, weekly, monthly, by_symbol = build_aggregates(trades)
    html = build_dashboard(trades, daily, weekly, monthly, by_symbol)
    (output_dir / "index.html").write_text(html, encoding="utf-8")
    print(f"  index.html  ({len(trades)} log entries)")
    print(f"Done → {output_dir}")


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else OUTPUT_DIR
    generate(out)
