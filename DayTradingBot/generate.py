"""
generate.py — Static dashboard for DayTradingBot.
Reads trades.csv and symbols.py, writes a single index.html to OUTPUT_DIR.
Run standalone or after each bot pass.

trades.csv (see position_manager.py EXTRA_COLUMNS) has one row per OPEN,
CLOSE, or PARTIAL_CLOSE action -- this module pairs an OPEN with its
following CLOSE/PARTIAL_CLOSE row(s) for the same option symbol into a single
trade record (entry + one or more exit legs), which is what both the summary
tables and the daily drill-down are built from.
"""

import csv
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from symbols import SYMBOLS

TRADES_CSV = Path(__file__).parent / "trades.csv"
OUTPUT_DIR = Path("/home/sohaib/sites/daytrading")
ET = ZoneInfo("America/New_York")


def fmt_money(val) -> str:
    sign = "+" if val > 0 else ("-" if val < 0 else "")
    return f"{sign}${abs(val):,.2f}"


def pnl_class(val) -> str:
    if val > 0:
        return "pos"
    if val < 0:
        return "neg"
    return ""


def _num(row: dict, key: str):
    v = row.get(key, "")
    if v in (None, ""):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _et_str(dt: datetime, fmt: str = "%Y-%m-%d %H:%M") -> str:
    return dt.astimezone(ET).strftime(fmt)


# ── Load raw rows ────────────────────────────────────────────────────────────

def load_rows() -> list:
    if not TRADES_CSV.exists():
        return []
    with open(TRADES_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["pnl"] = float(r.get("pnl") or 0)
        r["contracts"] = int(r.get("contracts") or 0)
        r["price"] = float(r.get("price") or 0)
        r["dt"] = datetime.fromisoformat(r["timestamp"])
        r["date"] = r["dt"].astimezone(ET).date().isoformat()
    rows.sort(key=lambda r: r["dt"])
    return rows


# ── Pair OPEN -> CLOSE/PARTIAL_CLOSE rows into trade records ────────────────

def _format_trigger(row: dict) -> str:
    """OPEN rows carry the real signal.get_signal() reason text since 2026-09-05;
    older rows (or a missing reason) fall back to a short indicator summary."""
    reason = (row.get("reason") or "").strip()
    if reason and reason != "Entry":
        return reason
    rsi, adx = _num(row, "rsi"), _num(row, "adx")
    bits = []
    if rsi is not None:
        bits.append(f"RSI={rsi:.1f}")
    if adx is not None:
        bits.append(f"ADX={adx:.1f}")
    return ", ".join(bits) if bits else "—"


def build_trades(rows: list) -> list:
    open_stacks = defaultdict(list)  # option symbol -> list of in-progress trade dicts
    trades = []

    for r in rows:
        action = r.get("action")
        sym = r.get("symbol")

        if action == "OPEN":
            trade = {
                "symbol": sym,
                "underlying": r.get("underlying"),
                "type": (r.get("type") or "").lower(),
                "strike": _num(r, "strike"),
                "expiration": r.get("expiration") or "",
                "entry_price": r["price"],
                "entry_contracts": r["contracts"],
                "entry_dt": r["dt"],
                "date": r["date"],
                "trigger": _format_trigger(r),
                "legs": [],
                "total_pnl": 0.0,
                "closed": False,
            }
            open_stacks[sym].append(trade)
            trades.append(trade)

        elif action in ("CLOSE", "PARTIAL_CLOSE"):
            stack = open_stacks.get(sym)
            if not stack:
                continue  # a CLOSE with no matching OPEN in this file's window -- skip
            trade = stack[-1]
            trade["legs"].append({
                "action": action,
                "contracts": r["contracts"],
                "price": r["price"],
                "pnl": r["pnl"],
                "reason": r.get("reason") or "",
                "dt": r["dt"],
            })
            trade["total_pnl"] += r["pnl"]
            trade["exit_price"] = r["price"]
            trade["exit_dt"] = r["dt"]
            if action == "CLOSE":
                trade["closed"] = True
                stack.pop()

    # Finalize derived fields
    for t in trades:
        t["exit_reason"] = "; ".join(leg["reason"] for leg in t["legs"] if leg["reason"]) or (
            "" if t["closed"] else "OPEN"
        )
        t["contract_label"] = (
            f'{t["underlying"]} ${t["strike"]:g} {t["type"].upper()}' if t["strike"] else t["symbol"]
        )
    return trades


# ── Aggregates (trade-centric: one entry per trade, not per CSV row) ────────

def week_key(d) -> str:
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def month_key(d) -> str:
    return d.strftime("%Y-%m")


def build_aggregates(trades: list):
    daily = defaultdict(lambda: {"pnl": 0.0, "count": 0})
    weekly = defaultdict(lambda: {"pnl": 0.0, "count": 0})
    monthly = defaultdict(lambda: {"pnl": 0.0, "count": 0})
    by_symbol = defaultdict(lambda: {"pnl": 0.0, "count": 0, "wins": 0, "losses": 0})

    for t in trades:
        d = t["entry_dt"].astimezone(ET).date()
        daily[t["date"]]["pnl"] += t["total_pnl"]
        daily[t["date"]]["count"] += 1
        weekly[week_key(d)]["pnl"] += t["total_pnl"]
        weekly[week_key(d)]["count"] += 1
        monthly[month_key(d)]["pnl"] += t["total_pnl"]
        monthly[month_key(d)]["count"] += 1

        sym = t["underlying"]
        by_symbol[sym]["count"] += 1
        by_symbol[sym]["pnl"] += t["total_pnl"]
        if t["closed"]:
            if t["total_pnl"] > 0:
                by_symbol[sym]["wins"] += 1
            elif t["total_pnl"] < 0:
                by_symbol[sym]["losses"] += 1

    return daily, weekly, monthly, by_symbol


# ── HTML shell ───────────────────────────────────────────────────────────────

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
    tr.clickable{{cursor:pointer}}
    tr.clickable td:first-child{{color:#3457d5;text-decoration:underline}}

    .modal-overlay{{position:fixed;inset:0;background:rgba(15,17,26,.55);display:flex;
      align-items:center;justify-content:center;padding:20px;z-index:100}}
    .modal-overlay[hidden]{{display:none}}
    .modal-box{{background:#fff;border-radius:12px;max-width:1000px;width:100%;
      max-height:85vh;display:flex;flex-direction:column;box-shadow:0 10px 40px rgba(0,0,0,.25)}}
    .modal-header{{display:flex;align-items:center;justify-content:space-between;
      padding:16px 20px;border-bottom:1px solid #eee}}
    .modal-header h3{{font-size:1.05rem}}
    .modal-header button{{background:none;border:none;font-size:1.4rem;line-height:1;
      cursor:pointer;color:#888;padding:2px 6px}}
    .modal-header button:hover{{color:#222}}
    .modal-body{{padding:12px 20px 20px;overflow-y:auto}}
    .modal-body table{{box-shadow:none}}
    .trigger-cell{{max-width:260px;white-space:normal;font-size:.76rem;color:#555}}
  </style>
</head>
<body>
<nav>
  <span class="brand">\U0001F4C8 Day Trading Bot</span>
</nav>
<div class="wrap">
"""


def _foot(daily_trades_json: str) -> str:
    return f"""
<div id="dayModal" class="modal-overlay" hidden>
  <div class="modal-box">
    <div class="modal-header">
      <h3 id="modalTitle"></h3>
      <button id="modalClose" aria-label="Close">&times;</button>
    </div>
    <div class="modal-body">
      <table>
        <thead><tr>
          <th>Underlying</th><th>Contract</th><th class="r">Entry</th><th class="r">Exit</th>
          <th class="r">P/L</th><th>Entry Trigger</th><th>Exit Reason</th>
          <th>Entry Time</th><th>Exit Time</th>
        </tr></thead>
        <tbody id="modalBody"></tbody>
      </table>
    </div>
  </div>
</div>
</div>
<script>
const DAILY_TRADES = JSON.parse({daily_trades_json});

function td(text, cls) {{
  const d = document.createElement('td');
  d.textContent = text;
  if (cls) d.className = cls;
  return d;
}}

function openDay(date) {{
  const rows = DAILY_TRADES[date] || [];
  document.getElementById('modalTitle').textContent = date + ' — ' + rows.length + ' trade' + (rows.length === 1 ? '' : 's');
  const tbody = document.getElementById('modalBody');
  tbody.innerHTML = '';
  if (!rows.length) {{
    const tr = document.createElement('tr');
    const d = document.createElement('td');
    d.colSpan = 9; d.className = 'no-data'; d.textContent = 'No trades this day.';
    tr.appendChild(d); tbody.appendChild(tr);
  }}
  rows.forEach(t => {{
    const tr = document.createElement('tr');
    tr.appendChild(td(t.underlying));
    tr.appendChild(td(t.contract));
    tr.appendChild(td(t.entry, 'r'));
    tr.appendChild(td(t.exit, 'r'));
    tr.appendChild(td(t.pnl, 'r ' + t.pnl_class));
    tr.appendChild(td(t.trigger, 'trigger-cell'));
    tr.appendChild(td(t.exit_reason));
    tr.appendChild(td(t.entry_time));
    tr.appendChild(td(t.exit_time));
    tbody.appendChild(tr);
  }});
  document.getElementById('dayModal').hidden = false;
}}

document.getElementById('modalClose').addEventListener('click', () => {{
  document.getElementById('dayModal').hidden = true;
}});
document.getElementById('dayModal').addEventListener('click', (e) => {{
  if (e.target.id === 'dayModal') e.currentTarget.hidden = true;
}});
document.addEventListener('keydown', (e) => {{
  if (e.key === 'Escape') document.getElementById('dayModal').hidden = true;
}});
</script>
</body></html>
"""


def build_dashboard(trades: list, rows: list, daily: dict, weekly: dict, monthly: dict, by_symbol: dict) -> str:
    total_pnl = sum(t["total_pnl"] for t in trades)
    closed = [t for t in trades if t["closed"]]
    wins = sum(1 for t in closed if t["total_pnl"] > 0)
    losses = sum(1 for t in closed if t["total_pnl"] < 0)
    win_rate = (wins / len(closed) * 100) if closed else 0

    holds = []
    for t in trades:
        for leg in t["legs"]:
            if leg["action"] == "CLOSE":
                holds.append((leg["dt"] - t["entry_dt"]).total_seconds() / 60)
    avg_hold = sum(holds) / len(holds) if holds else 0

    scale_outs = sum(1 for t in trades if any(leg["action"] == "PARTIAL_CLOSE" for leg in t["legs"]))

    hero = f"""
    <div class="hero">
      <div class="hero-card"><div class="val {pnl_class(total_pnl)}">{fmt_money(total_pnl)}</div><div class="lbl">Overall P/L</div></div>
      <div class="hero-card"><div class="val">{len(closed)}</div><div class="lbl">Closed Trades</div></div>
      <div class="hero-card"><div class="val">{win_rate:.0f}%</div><div class="lbl">Win Rate ({wins}W / {losses}L)</div></div>
      <div class="hero-card"><div class="val">{avg_hold:.0f}m</div><div class="lbl">Avg Hold Time</div></div>
      <div class="hero-card"><div class="val">{scale_outs}</div><div class="lbl">Scale-Outs (+50%)</div></div>
      <div class="hero-card"><div class="val">{len(rows)}</div><div class="lbl">Total Log Entries</div></div>
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
      <thead><tr><th>Symbol</th><th class="r">Trades</th><th class="r">W/L</th><th class="r">P/L</th></tr></thead>
      <tbody>{sym_rows}</tbody>
    </table>"""

    # Daily table (most recent first) -- rows are clickable, opening the drill-down modal
    daily_rows = ""
    for date in sorted(daily.keys(), reverse=True):
        d = daily[date]
        daily_rows += f"""<tr class="clickable" onclick="openDay('{date}')">
          <td>{date}</td>
          <td class="r">{d['count']}</td>
          <td class="r {pnl_class(d['pnl'])}">{fmt_money(d['pnl'])}</td>
        </tr>"""
    if not daily_rows:
        daily_rows = '<tr><td colspan="3" class="no-data">No trades yet.</td></tr>'
    daily_table = f"""
    <p class="sub">Click a day to see its individual trades.</p>
    <div class="scroll-box">
    <table>
      <thead><tr><th>Date</th><th class="r">Trades</th><th class="r">Daily P/L</th></tr></thead>
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
      <thead><tr><th>Week</th><th class="r">Trades</th><th class="r">Weekly P/L</th></tr></thead>
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
      <thead><tr><th>Month</th><th class="r">Trades</th><th class="r">Monthly P/L</th></tr></thead>
      <tbody>{monthly_rows}</tbody>
    </table>"""

    # Full transaction list (raw CSV rows, most recent first) -- now with contract label
    tx_rows = ""
    for r in reversed(rows):
        badge = f'<span class="badge">{r["action"]}</span>'
        pnl_cell = fmt_money(r["pnl"]) if r["action"] != "OPEN" else "—"
        strike = _num(r, "strike")
        contract = f'${strike:g} {r.get("type","").upper()}' if strike else "—"
        tx_rows += f"""<tr>
          <td>{_et_str(r['dt'])}</td>
          <td>{badge}</td>
          <td>{r['underlying']}</td>
          <td>{contract}</td>
          <td class="r">{r['contracts']}</td>
          <td class="r">${r['price']:,.2f}</td>
          <td class="r {pnl_class(r['pnl']) if r['action']!='OPEN' else ''}">{pnl_cell}</td>
          <td>{r.get('reason','')}</td>
        </tr>"""
    if not tx_rows:
        tx_rows = '<tr><td colspan="8" class="no-data">No transactions yet.</td></tr>'
    tx_table = f"""
    <div class="scroll-box">
    <table>
      <thead><tr>
        <th>Time (ET)</th><th>Action</th><th>Underlying</th><th>Contract</th>
        <th class="r">Qty</th><th class="r">Price</th><th class="r">P/L</th><th>Reason</th>
      </tr></thead>
      <tbody>{tx_rows}</tbody>
    </table>
    </div>"""

    # Per-day trade detail, embedded for the drill-down modal
    daily_trades = defaultdict(list)
    for t in sorted(trades, key=lambda x: x["entry_dt"]):
        daily_trades[t["date"]].append({
            "underlying": t["underlying"],
            "contract": t["contract_label"] + (f' exp {t["expiration"]}' if t["expiration"] else ""),
            "entry": f'${t["entry_price"]:.2f}',
            "exit": f'${t["exit_price"]:.2f}' if t.get("exit_price") is not None else "—",
            "pnl": fmt_money(t["total_pnl"]),
            "pnl_class": pnl_class(t["total_pnl"]),
            "trigger": t["trigger"],
            "exit_reason": t["exit_reason"] or "—",
            "entry_time": _et_str(t["entry_dt"], "%H:%M:%S"),
            "exit_time": _et_str(t["exit_dt"], "%H:%M:%S") if t.get("exit_dt") else "—",
        })
    daily_trades_json = json.dumps(json.dumps(dict(daily_trades))).replace("</", "<\\/")

    return (
        _head("Day Trading Bot Dashboard") +
        hero +
        "<h2>Symbols</h2>" + symbols_table +
        "<h2>Daily P/L</h2>" + daily_table +
        "<h2>Weekly P/L</h2>" + weekly_table +
        "<h2>Monthly P/L</h2>" + monthly_table +
        "<h2>All Transactions</h2>" +
        f'<p class="sub">{len(rows)} log entries, most recent first</p>' +
        tx_table +
        _foot(daily_trades_json)
    )


def generate(output_dir: Path = OUTPUT_DIR):
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = load_rows()
    trades = build_trades(rows)
    daily, weekly, monthly, by_symbol = build_aggregates(trades)
    html = build_dashboard(trades, rows, daily, weekly, monthly, by_symbol)
    (output_dir / "index.html").write_text(html, encoding="utf-8")
    print(f"  index.html  ({len(rows)} log entries, {len(trades)} trades)")
    print(f"Done -> {output_dir}")


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else OUTPUT_DIR
    generate(out)
