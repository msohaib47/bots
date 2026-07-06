#!/usr/bin/env python3
"""
Bot Dashboard Generator
Reads log files, state JSON, and trade CSVs to produce dashboard.html.
Run directly: python3 dashboard.py
Output: dashboard.html (same directory as this script)
"""
import csv
import json
import os
import re
from datetime import datetime, timezone
from html import escape

BASE = os.path.dirname(os.path.abspath(__file__))


# ── Helpers ────────────────────────────────────────────────────────────────────

def fpath(*parts):
    return os.path.join(BASE, *parts)


def slurp(path):
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            return f.read()
    except FileNotFoundError:
        return ''


def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def load_csv_tail(path, n=10):
    try:
        with open(path, encoding='utf-8') as f:
            rows = list(csv.DictReader(f))
        return rows[-n:]
    except (FileNotFoundError, Exception):
        return []


STAMP_RE = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ \[(\w+)\] (.+)$')


def parse_stamp(line):
    """Return (datetime, level, message) or None."""
    m = STAMP_RE.match(line.strip())
    if not m:
        return None
    try:
        dt = datetime.strptime(m.group(1), '%Y-%m-%d %H:%M:%S')
        return dt, m.group(2), m.group(3)
    except ValueError:
        return None


def split_runs(log_text, marker_re, n=3):
    """
    Split log into individual runs.  A run begins at any line matching
    marker_re.  Returns the last n runs as lists-of-lines.
    """
    if not log_text:
        return []
    lines = log_text.splitlines()
    starts = [i for i, l in enumerate(lines) if re.search(marker_re, l)]
    if not starts:
        return [lines] if lines else []
    sel = starts[-n:]
    runs = []
    for idx, start in enumerate(sel):
        end = sel[idx + 1] if idx + 1 < len(sel) else len(lines)
        runs.append(lines[start:end])
    return runs


def run_timestamp(lines):
    """First parseable timestamp found in a run's lines."""
    for line in lines:
        parsed = parse_stamp(line)
        if parsed:
            return parsed[0]
    return None


def run_status(lines):
    """Classify a run: 'ok' | 'closed' | 'error'."""
    text = '\n'.join(lines)
    if 'Traceback' in text or '[ERROR]' in text:
        return 'error'
    if ('Market closed' in text or 'nothing to do' in text
            or 'skipping run' in text or 'Market is closed' in text):
        return 'closed'
    return 'ok'


NOISE = re.compile(
    r'Market closed, nothing to do|'
    r'Market closed — skipping run|'
    r'^-{3,}|^={3,}|^\s*$'
)

KEEP_MSGS = [
    'ENTERED', 'Closing', 'ASSIGNED', 'BUY signal', 'SELL signal',
    'order placed', 'State saved', 'Run complete', 'No new trades',
    'Found .* new trades', 'Portfolio:', 'Account:', 'Cash:',
    'Traceback', 'Error', 'stop hit', 'Stop hit', 'Executed',
    r'signal=(?!NONE)', 'PDT', 'CSP order', 'CC order',
]
KEEP_RE = re.compile('|'.join(KEEP_MSGS))


def notable_lines(lines, limit=12):
    """
    Return the most informative lines from a run.
    For market-closed runs just return the single status line.
    """
    text = '\n'.join(lines)
    if ('Market closed, nothing to do' in text
            or 'Market closed — skipping run' in text):
        for line in lines:
            if 'Market closed' in line:
                return [line.strip()]
        return [lines[0].strip()] if lines else []

    result = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        parsed = parse_stamp(line)
        if parsed:
            _, level, msg = parsed
            if level in ('WARNING', 'ERROR') or KEEP_RE.search(msg):
                result.append(stripped)
        else:
            # Un-timestamped print() output — keep short meaningful lines
            if (stripped and len(stripped) < 120
                    and not NOISE.search(stripped)
                    and not stripped.startswith('Open Positions')
                    and not re.match(r'^[A-Z0-9]{4,}260', stripped)):   # skip option symbols
                result.append(stripped)
    # Always include at least the first timestamp line if nothing else qualifies
    if not result and lines:
        result = [lines[0].strip()]
    return result[:limit]


def fmt_dt(dt):
    if dt is None:
        return '—'
    return dt.strftime('%Y-%m-%d %H:%M') + ' UTC'


def ago(dt):
    if dt is None:
        return ''
    delta = datetime.now(timezone.utc).replace(tzinfo=None) - dt
    s = int(delta.total_seconds())
    if s < 60:
        return f'{s}s ago'
    if s < 3600:
        return f'{s//60}m ago'
    if s < 86400:
        return f'{s//3600}h ago'
    return f'{s//86400}d ago'


# ── HTML helpers ───────────────────────────────────────────────────────────────

STATUS_BADGE = {
    'ok':     '<span class="badge bg-success">OK</span>',
    'closed': '<span class="badge bg-secondary">Closed</span>',
    'error':  '<span class="badge bg-danger">ERROR</span>',
}

STATUS_DOT = {
    'ok':     '<span class="dot dot-ok" title="Last run OK"></span>',
    'closed': '<span class="dot dot-closed" title="Market closed"></span>',
    'error':  '<span class="dot dot-error" title="Error"></span>',
}


def h(text):
    return escape(str(text))


def card(title, body, extra_class=''):
    return f'''
<div class="card h-100 {extra_class}">
  <div class="card-header fw-semibold">{title}</div>
  <div class="card-body p-0">{body}</div>
</div>'''


# ── Bot-specific data builders ─────────────────────────────────────────────────

def build_runs_html(runs):
    if not runs:
        return '<p class="text-muted p-3 mb-0">No log data found.</p>'

    rows = ''
    for run in runs:
        ts   = run_timestamp(run)
        stat = run_status(run)
        note = notable_lines(run)
        note_html = ''.join(f'<div class="log-line log-{stat}">{h(l)}</div>' for l in note) or '—'

        rows += f'''
<tr>
  <td class="text-nowrap small">{h(fmt_dt(ts))}<br>
      <span class="text-muted smaller">{h(ago(ts))}</span></td>
  <td>{STATUS_BADGE[stat]}</td>
  <td><div class="log-block">{note_html}</div></td>
</tr>'''

    return f'''
<div class="table-responsive">
<table class="table table-sm table-hover mb-0">
  <thead><tr><th>Time</th><th>Status</th><th>Log</th></tr></thead>
  <tbody>{rows}</tbody>
</table>
</div>'''


# ── WheelBot ──────────────────────────────────────────────────────────────────

def wheelbot_data():
    log    = slurp(fpath('logs', 'wheelbot.log'))
    state  = load_json(fpath('WheelBot', 'wheel_state.json'))
    trades = load_csv_tail(fpath('WheelBot', 'wheel_trades.csv'))

    runs = split_runs(log, r'(={20}|Market closed)', n=3)
    runs_html = build_runs_html(runs)

    # Holdings from wheel_state.json
    if state:
        rows = ''
        for ticker, ts in state.items():
            s = ts.get('state', 'IDLE')
            badge_color = {
                'IDLE': 'secondary', 'CSP_PENDING': 'warning', 'CSP_OPEN': 'info',
                'ASSIGNED': 'primary', 'CC_PENDING': 'warning', 'CC_OPEN': 'info',
                'CALLED_AWAY': 'success',
            }.get(s, 'secondary')
            option  = ts.get('csp_symbol') or ts.get('cc_symbol') or '—'
            expiry  = ts.get('csp_expiry') or ts.get('cc_expiry') or '—'
            premium = ts.get('csp_premium') or ts.get('cc_premium') or 0
            prem_col = ts.get('total_premium_collected', 0)
            pnl     = ts.get('total_realized_pnl', 0)
            rows += f'''<tr>
  <td class="fw-bold">{h(ticker)}</td>
  <td><span class="badge bg-{badge_color}">{h(s)}</span></td>
  <td class="small font-monospace">{h(option)}</td>
  <td>{h(expiry)}</td>
  <td>${premium:.2f}</td>
  <td>${prem_col:.2f}</td>
  <td class="{'text-success' if pnl >= 0 else 'text-danger'}">${pnl:.2f}</td>
</tr>'''
        holdings_html = f'''
<div class="table-responsive">
<table class="table table-sm table-hover mb-0">
  <thead><tr><th>Ticker</th><th>State</th><th>Contract</th><th>Expiry</th>
             <th>Premium</th><th>Total Prem</th><th>Realized P&L</th></tr></thead>
  <tbody>{rows}</tbody>
</table></div>'''
    else:
        holdings_html = '<p class="text-muted p-3 mb-0">No wheel state found.</p>'

    # Transactions from wheel_trades.csv (if exists)
    if trades:
        cols = list(trades[0].keys()) if trades else []
        thead = ''.join(f'<th>{h(c)}</th>' for c in cols)
        rows_html = ''
        for row in reversed(trades):
            rows_html += '<tr>' + ''.join(f'<td class="small">{h(row.get(c,""))}</td>' for c in cols) + '</tr>'
        trades_html = f'''
<div class="table-responsive">
<table class="table table-sm table-hover mb-0">
  <thead><tr>{thead}</tr></thead>
  <tbody>{rows_html}</tbody>
</table></div>'''
    else:
        trades_html = '<p class="text-muted p-3 mb-0">No trade history yet.</p>'

    return runs_html, holdings_html, trades_html


# ── DayTradingBot ─────────────────────────────────────────────────────────────

def daytrading_data():
    log      = slurp(fpath('logs', 'daytrading.log'))
    state    = load_json(fpath('DayTradingBot', 'positions.json'))
    trades   = load_csv_tail(fpath('DayTradingBot', 'trades.csv'))

    runs = split_runs(log, r'--- DayTradingBot tick', n=3)
    runs_html = build_runs_html(runs)

    # Holdings
    if state:
        rows = ''
        for sym, pos in state.items():
            entry  = pos.get('entry_cost', pos.get('entry_price', 0))
            stop   = pos.get('stop_price', 0)
            trail  = '✓' if pos.get('trailing_active') else ''
            und    = pos.get('underlying', '')
            typ    = pos.get('type', '')
            cont   = pos.get('contracts', pos.get('qty', ''))
            opened = pos.get('opened_at', '')[:16].replace('T', ' ')
            rows += f'''<tr>
  <td class="font-monospace small">{h(sym)}</td>
  <td>{h(und)} {h(typ.upper())}</td>
  <td>{h(cont)}</td>
  <td>${float(entry):.2f}</td>
  <td>${float(stop):.2f}</td>
  <td class="text-center">{trail}</td>
  <td class="small text-muted">{h(opened)}</td>
</tr>'''
        holdings_html = f'''
<div class="table-responsive">
<table class="table table-sm table-hover mb-0">
  <thead><tr><th>Symbol</th><th>Type</th><th>Qty</th><th>Entry</th>
             <th>Stop</th><th>Trail</th><th>Opened</th></tr></thead>
  <tbody>{rows}</tbody>
</table></div>'''
    else:
        holdings_html = '<p class="text-muted p-3 mb-0">No open positions.</p>'

    # Transactions
    if trades:
        rows_html = ''
        for row in reversed(trades):
            ts   = row.get('timestamp', '')[:16].replace('T', ' ')
            act  = row.get('action', '')
            sym  = row.get('symbol', '')
            und  = row.get('underlying', '')
            typ  = row.get('type', '')
            cont = row.get('contracts', '')
            price = row.get('price', '')
            pnl   = float(row.get('pnl', 0) or 0)
            rsn   = row.get('reason', '')
            act_class = 'text-success' if act == 'OPEN' else 'text-danger' if act == 'CLOSE' else ''
            pnl_class = 'text-success' if pnl > 0 else ('text-danger' if pnl < 0 else '')
            rows_html += f'''<tr>
  <td class="small text-muted">{h(ts)}</td>
  <td class="{act_class} fw-bold">{h(act)}</td>
  <td class="font-monospace small">{h(sym)}</td>
  <td>{h(und)} {h(typ)}</td>
  <td class="text-center">{h(cont)}</td>
  <td>${float(price):.2f}</td>
  <td class="{pnl_class}">{f"${pnl:+.2f}" if pnl != 0 else "—"}</td>
  <td class="small text-muted">{h(rsn)}</td>
</tr>'''
        trades_html = f'''
<div class="table-responsive">
<table class="table table-sm table-hover mb-0">
  <thead><tr><th>Time</th><th>Action</th><th>Symbol</th><th>Type</th>
             <th>Qty</th><th>Price</th><th>P&L</th><th>Reason</th></tr></thead>
  <tbody>{rows_html}</tbody>
</table></div>'''
    else:
        trades_html = '<p class="text-muted p-3 mb-0">No trade history yet.</p>'

    return runs_html, holdings_html, trades_html


# ── CryptoBot ─────────────────────────────────────────────────────────────────

def crypto_data():
    log    = slurp(fpath('logs', 'cryptobot.log'))
    trades = load_csv_tail(fpath('CryptoBot', 'trades_log.csv'))

    runs = split_runs(log, r'={20}', n=3)
    runs_html = build_runs_html(runs)

    # Holdings: parse last run for position/signal info
    holdings_rows = ''
    if runs:
        last_run = runs[-1]
        for line in last_run:
            parsed = parse_stamp(line)
            if parsed:
                _, _, msg = parsed
                # e.g. "BTC/USD: price=$61,729.54 | EMA9=... | RSI=16.54 | SIGNAL=HOLD"
                if re.search(r'price=\$', msg):
                    sym_m = re.match(r'(\S+): price=\$([0-9,\.]+)', msg)
                    sig_m = re.search(r'SIGNAL=(\w+)', msg)
                    rsi_m = re.search(r'RSI=([\d\.]+)', msg)
                    pos_m = re.search(r'(HOLD|BUY|SELL) \((.+?)\)', msg)
                    sym   = sym_m.group(1) if sym_m else '?'
                    price = sym_m.group(2) if sym_m else '?'
                    sig   = sig_m.group(1) if sig_m else '?'
                    rsi   = rsi_m.group(1) if rsi_m else '?'
                    pos   = pos_m.group(2) if pos_m else '—'
                    sig_color = {'BUY': 'success', 'SELL': 'danger', 'HOLD': 'secondary'}.get(sig, 'secondary')
                    holdings_rows += f'''<tr>
  <td class="fw-bold">{h(sym)}</td>
  <td>${h(price)}</td>
  <td><span class="badge bg-{sig_color}">{h(sig)}</span></td>
  <td>{h(rsi)}</td>
  <td>{h(pos)}</td>
</tr>'''

    if holdings_rows:
        holdings_html = f'''
<div class="table-responsive">
<table class="table table-sm table-hover mb-0">
  <thead><tr><th>Symbol</th><th>Price</th><th>Signal</th><th>RSI</th><th>Position</th></tr></thead>
  <tbody>{holdings_rows}</tbody>
</table></div>'''
    else:
        holdings_html = '<p class="text-muted p-3 mb-0">No position data in recent log.</p>'

    # Transactions
    if trades:
        rows_html = ''
        for row in reversed(trades):
            ts    = row.get('timestamp', '')[:16].replace('T', ' ')
            sym   = row.get('symbol', '')
            act   = row.get('action', '')
            price = row.get('price', '')
            amt   = row.get('amount', '')
            rsi   = row.get('rsi', '')
            act_class = 'text-success' if act == 'BUY' else 'text-danger'
            rows_html += f'''<tr>
  <td class="small text-muted">{h(ts)}</td>
  <td class="{act_class} fw-bold">{h(act)}</td>
  <td>{h(sym)}</td>
  <td>${float(price):,.2f}</td>
  <td>{h(amt)}</td>
  <td>{h(rsi)}</td>
</tr>'''
        trades_html = f'''
<div class="table-responsive">
<table class="table table-sm table-hover mb-0">
  <thead><tr><th>Time</th><th>Action</th><th>Symbol</th><th>Price</th><th>Amount</th><th>RSI</th></tr></thead>
  <tbody>{rows_html}</tbody>
</table></div>'''
    else:
        trades_html = '<p class="text-muted p-3 mb-0">No trade history yet.</p>'

    return runs_html, holdings_html, trades_html


# ── CopyTradingBot ────────────────────────────────────────────────────────────

def copybot_data():
    log    = slurp(fpath('logs', 'copybot.log'))
    trades = load_csv_tail(fpath('CopyTradingBot', 'trades_log.csv'))

    runs = split_runs(log, r'--- Checking trades for', n=3)
    runs_html = build_runs_html(runs)

    # Holdings: executed (non-skipped) trades from CSV
    if trades:
        executed = [r for r in trades if r.get('order_id', 'SKIPPED') not in ('SKIPPED', '')]
        skipped  = [r for r in trades if r.get('order_id', 'SKIPPED') == 'SKIPPED']

        if executed:
            rows_html = ''
            for row in reversed(executed[-10:]):
                ts   = row.get('copied_at', '')[:16].replace('T', ' ')
                tkr  = row.get('ticker', '')
                act  = row.get('action', '')
                amt  = row.get('amount_range', '')
                qty  = row.get('qty', '')
                side = row.get('side', '')
                act_class = 'text-success' if 'purchase' in act.lower() else 'text-danger'
                rows_html += f'''<tr>
  <td class="small text-muted">{h(ts)}</td>
  <td class="fw-bold">{h(tkr)}</td>
  <td class="{act_class}">{h(act)}</td>
  <td>{h(amt)}</td>
  <td>{h(qty)}</td>
  <td>{h(side)}</td>
</tr>'''
            holdings_html = f'''
<p class="p-2 mb-0 small text-muted">Showing executed trades (positions via Alpaca paper account)</p>
<div class="table-responsive">
<table class="table table-sm table-hover mb-0">
  <thead><tr><th>Time</th><th>Ticker</th><th>Action</th><th>Amount</th><th>Qty</th><th>Side</th></tr></thead>
  <tbody>{rows_html}</tbody>
</table></div>'''
        else:
            skipped_count = len(skipped)
            holdings_html = f'<p class="text-muted p-3 mb-0">No executed trades yet. {skipped_count} trades skipped (unsupported type).</p>'
    else:
        holdings_html = '<p class="text-muted p-3 mb-0">No trades log found.</p>'

    # Transactions: all recent from trades_log.csv
    all_trades = load_csv_tail(fpath('CopyTradingBot', 'trades_log.csv'))
    if all_trades:
        cols = ['copied_at', 'ticker', 'action', 'amount_range', 'transaction_date', 'status', 'notes']
        thead = ''.join(f'<th>{h(c)}</th>' for c in cols)
        rows_html = ''
        for row in reversed(all_trades):
            ts   = row.get('copied_at', '')[:16].replace('T', ' ')
            stat = row.get('status', '')
            stat_class = 'text-success' if stat == 'filled' else 'text-muted'
            rows_html += f'''<tr>
  <td class="small text-muted">{h(ts)}</td>
  <td class="fw-bold">{h(row.get("ticker",""))}</td>
  <td>{h(row.get("action",""))}</td>
  <td class="small">{h(row.get("amount_range",""))}</td>
  <td class="small">{h(row.get("transaction_date",""))}</td>
  <td class="{stat_class}">{h(stat)}</td>
  <td class="small text-muted">{h(row.get("notes",""))}</td>
</tr>'''
        trades_html = f'''
<div class="table-responsive">
<table class="table table-sm table-hover mb-0">
  <thead><tr><th>Copied At</th><th>Ticker</th><th>Action</th><th>Amount</th>
             <th>Trade Date</th><th>Status</th><th>Notes</th></tr></thead>
  <tbody>{rows_html}</tbody>
</table></div>'''
    else:
        trades_html = '<p class="text-muted p-3 mb-0">No trade history yet.</p>'

    return runs_html, holdings_html, trades_html


# ── RobinhoodDayTradingBot ────────────────────────────────────────────────────

def rhbot_data():
    log    = slurp(fpath('logs', 'rhbot.log'))
    state  = load_json(fpath('RobinhoodDayTradingBot', 'positions.json'))
    trades = load_csv_tail(fpath('RobinhoodDayTradingBot', 'trades.csv'))

    runs = split_runs(log, r'--- RobinhoodDayTradingBot tick', n=3)
    runs_html = build_runs_html(runs)

    # Holdings
    if state:
        rows = ''
        for sym, pos in state.items():
            entry  = pos.get('entry_price', 0)
            stop   = pos.get('stop_price', 0)
            target = pos.get('target_price', 0)
            trail  = '✓' if pos.get('trailing_active') else ''
            qty    = pos.get('qty', 0)
            opened = pos.get('opened_at', '')[:16].replace('T', ' ')
            rows += f'''<tr>
  <td class="fw-bold">{h(sym)}</td>
  <td>{float(qty):.4f}</td>
  <td>${float(entry):.2f}</td>
  <td>${float(stop):.2f}</td>
  <td>${float(target):.2f}</td>
  <td class="text-center">{trail}</td>
  <td class="small text-muted">{h(opened)}</td>
</tr>'''
        holdings_html = f'''
<div class="table-responsive">
<table class="table table-sm table-hover mb-0">
  <thead><tr><th>Symbol</th><th>Qty</th><th>Entry</th><th>Stop</th>
             <th>Target</th><th>Trail</th><th>Opened</th></tr></thead>
  <tbody>{rows}</tbody>
</table></div>'''
    else:
        holdings_html = '<p class="text-muted p-3 mb-0">No open positions.</p>'

    # Transactions
    if trades:
        rows_html = ''
        for row in reversed(trades):
            ts    = row.get('timestamp', '')[:16].replace('T', ' ')
            act   = row.get('action', '')
            sym   = row.get('symbol', '')
            qty   = row.get('qty', '')
            price = row.get('price', '')
            pnl   = float(row.get('pnl', 0) or 0)
            rsn   = row.get('reason', '')
            act_class = 'text-success' if act == 'OPEN' else 'text-danger' if act == 'CLOSE' else ''
            pnl_class = 'text-success' if pnl > 0 else ('text-danger' if pnl < 0 else '')
            rows_html += f'''<tr>
  <td class="small text-muted">{h(ts)}</td>
  <td class="{act_class} fw-bold">{h(act)}</td>
  <td class="fw-bold">{h(sym)}</td>
  <td>{h(qty)}</td>
  <td>${float(price):.2f}</td>
  <td class="{pnl_class}">{f"${pnl:+.2f}" if pnl != 0 else "—"}</td>
  <td class="small text-muted">{h(rsn)}</td>
</tr>'''
        trades_html = f'''
<div class="table-responsive">
<table class="table table-sm table-hover mb-0">
  <thead><tr><th>Time</th><th>Action</th><th>Symbol</th><th>Qty</th>
             <th>Price</th><th>P&L</th><th>Reason</th></tr></thead>
  <tbody>{rows_html}</tbody>
</table></div>'''
    else:
        trades_html = '<p class="text-muted p-3 mb-0">No trade history yet.</p>'

    return runs_html, holdings_html, trades_html


# ── Overall health dot from last run ──────────────────────────────────────────

def bot_health(log_path, marker_re):
    log  = slurp(log_path)
    runs = split_runs(log, marker_re, n=1)
    if not runs:
        return 'error'
    return run_status(runs[-1])


# ── Page assembly ──────────────────────────────────────────────────────────────

def build_tab(bot_id, bot_name, description, runs_html, holdings_html, trades_html, health):
    dot = STATUS_DOT[health]
    return f'''
<!-- Tab: {bot_name} -->
<div class="tab-pane fade {'show active' if bot_id == 'wheelbot' else ''}" id="{bot_id}" role="tabpanel">
  <div class="bot-header d-flex align-items-center gap-3 mb-3">
    <div>
      <h5 class="mb-0">{dot} {h(bot_name)}</h5>
      <small class="text-muted">{h(description)}</small>
    </div>
  </div>
  <div class="row g-3">
    <div class="col-12">
      {card("Last 3 Runs", runs_html)}
    </div>
    <div class="col-md-6">
      {card("Current Holdings", holdings_html)}
    </div>
    <div class="col-md-6">
      {card("Recent Transactions (last 10)", trades_html)}
    </div>
  </div>
</div>'''


def build_html(bots):
    generated = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')

    tab_nav = ''
    tab_content = ''
    for idx, (bot_id, bot_name, description, runs_h, hold_h, trade_h, health) in enumerate(bots):
        active = 'active' if idx == 0 else ''
        dot = STATUS_DOT[health]
        tab_nav += f'''
<li class="nav-item" role="presentation">
  <button class="nav-link {active}" id="{bot_id}-tab" data-bs-toggle="tab"
          data-bs-target="#{bot_id}" type="button" role="tab">
    {dot} {h(bot_name)}
  </button>
</li>'''
        tab_content += build_tab(bot_id, bot_name, description, runs_h, hold_h, trade_h, health)

    return f'''<!DOCTYPE html>
<html lang="en" data-bs-theme="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="300">
  <title>Trading Bots Dashboard</title>
  <link rel="stylesheet"
        href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css">
  <style>
    body {{ background: #0d1117; color: #c9d1d9; font-size: 0.875rem; }}
    .navbar-brand {{ font-weight: 700; letter-spacing: .5px; }}
    .nav-tabs .nav-link {{ color: #8b949e; border-color: transparent; }}
    .nav-tabs .nav-link.active {{ color: #f0f6fc; background: #161b22; border-color: #30363d #30363d #161b22; }}
    .card {{ background: #161b22; border: 1px solid #30363d; }}
    .card-header {{ background: #1c2128; border-bottom: 1px solid #30363d; color: #e6edf3; font-size: .8rem; text-transform: uppercase; letter-spacing: .5px; }}
    .table {{ color: #c9d1d9; }}
    .table thead th {{ background: #1c2128; border-bottom: 1px solid #30363d; color: #8b949e; font-size: .75rem; text-transform: uppercase; letter-spacing: .3px; }}
    .table-hover tbody tr:hover {{ background: #1c2128; }}
    .table td, .table th {{ border-color: #21262d; vertical-align: middle; }}
    .bot-header {{ padding: .75rem 0 0; }}
    .log-block {{ font-family: monospace; font-size: .72rem; }}
    .log-line {{ padding: 1px 0; white-space: pre-wrap; word-break: break-all; }}
    .log-ok    {{ color: #7ee787; }}
    .log-closed {{ color: #8b949e; }}
    .log-error  {{ color: #f85149; }}
    .dot {{ display: inline-block; width: 10px; height: 10px; border-radius: 50%; }}
    .dot-ok     {{ background: #3fb950; box-shadow: 0 0 6px #3fb950; }}
    .dot-closed {{ background: #6e7681; }}
    .dot-error  {{ background: #f85149; box-shadow: 0 0 6px #f85149; }}
    .smaller {{ font-size: .7rem; }}
    .font-monospace {{ font-family: SFMono-Regular, Consolas, monospace !important; }}
    #refresh-bar {{ font-size: .72rem; }}
  </style>
</head>
<body>
  <nav class="navbar navbar-dark px-3 py-2" style="background:#161b22; border-bottom:1px solid #30363d;">
    <span class="navbar-brand">⚡ Trading Bots Dashboard</span>
    <span class="text-muted small" id="refresh-bar">
      Generated: {generated} &nbsp;|&nbsp; Auto-refresh in <span id="countdown">300</span>s
    </span>
  </nav>

  <div class="container-fluid py-3">
    <ul class="nav nav-tabs mb-3" id="botTabs" role="tablist">
      {tab_nav}
    </ul>
    <div class="tab-content" id="botTabContent">
      {tab_content}
    </div>
  </div>

  <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
  <script src="https://code.jquery.com/jquery-3.7.1.min.js"></script>
  <script>
    // Countdown timer
    let t = 300;
    setInterval(() => {{
      t--;
      if (t <= 0) location.reload();
      $('#countdown').text(t);
    }}, 1000);

    // Restore last active tab from localStorage
    const savedTab = localStorage.getItem('activeTab');
    if (savedTab) {{
      const el = document.querySelector(`button[data-bs-target="${{savedTab}}"]`);
      if (el) bootstrap.Tab.getOrCreateInstance(el).show();
    }}
    document.querySelectorAll('button[data-bs-toggle="tab"]').forEach(btn => {{
      btn.addEventListener('shown.bs.tab', e => {{
        localStorage.setItem('activeTab', e.target.dataset.bsTarget);
      }});
    }});
  </script>
</body>
</html>'''


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    print('Building dashboard...')

    wheel_runs,  wheel_hold,  wheel_trade  = wheelbot_data()
    day_runs,    day_hold,    day_trade    = daytrading_data()
    crypto_runs, crypto_hold, crypto_trade = crypto_data()
    copy_runs,   copy_hold,   copy_trade   = copybot_data()
    rh_runs,     rh_hold,     rh_trade     = rhbot_data()

    bots = [
        ('wheelbot',   'WheelBot',
         'Wheel strategy (CSP → Assignment → CC) on QBTS, RIOT, CIFR, CLSK',
         wheel_runs, wheel_hold, wheel_trade,
         bot_health(fpath('logs', 'wheelbot.log'), r'(={20}|Market closed)')),

        ('daytrading',  'DayTradingBot',
         '0DTE options (SPY, QQQ, IWM) — VWAP + EMA + RSI',
         day_runs, day_hold, day_trade,
         bot_health(fpath('logs', 'daytrading.log'), r'--- DayTradingBot tick')),

        ('cryptobot',  'CryptoBot',
         'BTC/USD momentum — EMA 9/21 crossover + RSI filter',
         crypto_runs, crypto_hold, crypto_trade,
         bot_health(fpath('logs', 'cryptobot.log'), r'={20}')),

        ('copybot',    'CopyTradingBot',
         'Copies congressional trades (Markwayne Mullin) via QuiverQuant',
         copy_runs, copy_hold, copy_trade,
         bot_health(fpath('logs', 'copybot.log'), r'--- Checking trades for')),

        ('rhbot',      'RobinhoodDayTradingBot',
         'Equity day trading on Robinhood — VWAP + EMA + RSI, 2% stop / 4% target',
         rh_runs, rh_hold, rh_trade,
         bot_health(fpath('logs', 'rhbot.log'), r'--- RobinhoodDayTradingBot tick')),
    ]

    html = build_html(bots)
    out = os.environ.get('DASHBOARD_OUT', fpath('dashboard.html'))
    with open(out, 'w', encoding='utf-8') as f:
        f.write(html)

    print(f'Dashboard written to: {out}')


if __name__ == '__main__':
    main()
