"""
emailer.py — Send email notifications with sold listings
"""

import os
import smtplib
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from db import get_conn
from dotenv import load_dotenv

# Load .env if it exists
env_file = Path(__file__).parent / ".env"
if env_file.exists():
    load_dotenv(env_file)

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASS = os.environ.get("SMTP_PASS")
RECIPIENT = os.environ.get("EMAIL_RECIPIENT", "solutionzeroone@gmail.com")


def get_today_yesterday_listings(config_name: str) -> dict:
    """Get listings sold today and yesterday for a config.
    Returns {"today": [...], "yesterday": [...]}
    """
    with get_conn() as conn:
        today = datetime.now().date().isoformat()
        yesterday = (datetime.now() - timedelta(days=1)).date().isoformat()

        today_listings = conn.execute("""
            SELECT * FROM sold_listings
            WHERE search_config = ? AND sold_date >= ?
            ORDER BY sold_date DESC
        """, (config_name, today)).fetchall()

        yesterday_listings = conn.execute("""
            SELECT * FROM sold_listings
            WHERE search_config = ? AND sold_date >= ? AND sold_date < ?
            ORDER BY sold_date DESC
        """, (config_name, yesterday, today)).fetchall()

    return {
        "today": [dict(r) for r in today_listings],
        "yesterday": [dict(r) for r in yesterday_listings],
    }


def format_listing_html(listing: dict) -> str:
    """Format a single listing as HTML."""
    return f"""
    <tr>
        <td style="padding: 8px; border-bottom: 1px solid #ddd;">
            <strong>{listing['title']}</strong>
            <br><small style="color: #666;">{listing['condition']}</small>
        </td>
        <td style="padding: 8px; border-bottom: 1px solid #ddd; text-align: right;">
            ${listing['sold_price']:.2f}
        </td>
        <td style="padding: 8px; border-bottom: 1px solid #ddd; text-align: right;">
            {listing['sold_date'] if listing['sold_date'] else "N/A"}
        </td>
        <td style="padding: 8px; border-bottom: 1px solid #ddd; text-align: center;">
            <a href="{listing['listing_url']}" style="color: #0066cc;">View</a>
        </td>
    </tr>
    """


def build_email_html(config_name: str, listings: dict) -> str:
    """Build HTML email body with listings."""
    today_rows = "".join(format_listing_html(l) for l in listings["today"])
    yesterday_rows = "".join(format_listing_html(l) for l in listings["yesterday"])

    today_section = f"""
    <h3 style="color: #333; margin-top: 20px;">Today ({datetime.now().date()})</h3>
    <p style="color: #666;">Found {len(listings['today'])} listing(s)</p>
    <table style="width: 100%; border-collapse: collapse;">
        <thead>
            <tr style="background: #f5f5f5;">
                <th style="padding: 8px; text-align: left;">Title</th>
                <th style="padding: 8px; text-align: right;">Price</th>
                <th style="padding: 8px; text-align: right;">Sold Date</th>
                <th style="padding: 8px; text-align: center;">Link</th>
            </tr>
        </thead>
        <tbody>
            {today_rows}
        </tbody>
    </table>
    """ if listings["today"] else ""

    yesterday_section = f"""
    <h3 style="color: #333; margin-top: 20px;">Yesterday ({(datetime.now() - timedelta(days=1)).date()})</h3>
    <p style="color: #666;">Found {len(listings['yesterday'])} listing(s)</p>
    <table style="width: 100%; border-collapse: collapse;">
        <thead>
            <tr style="background: #f5f5f5;">
                <th style="padding: 8px; text-align: left;">Title</th>
                <th style="padding: 8px; text-align: right;">Price</th>
                <th style="padding: 8px; text-align: right;">Sold Date</th>
                <th style="padding: 8px; text-align: center;">Link</th>
            </tr>
        </thead>
        <tbody>
            {yesterday_rows}
        </tbody>
    </table>
    """ if listings["yesterday"] else ""

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <style>
            body {{ font-family: Arial, sans-serif; color: #333; }}
            .container {{ max-width: 800px; margin: 0 auto; padding: 20px; }}
            h2 {{ color: #0066cc; border-bottom: 2px solid #0066cc; padding-bottom: 10px; }}
            h3 {{ color: #333; margin-top: 20px; }}
            table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
            th {{ background: #f5f5f5; padding: 8px; text-align: left; border-bottom: 1px solid #ddd; }}
            td {{ padding: 8px; border-bottom: 1px solid #ddd; }}
            a {{ color: #0066cc; text-decoration: none; }}
            a:hover {{ text-decoration: underline; }}
            .no-listings {{ color: #666; font-style: italic; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h2>eBay Sales Report: {config_name}</h2>
            <p>Report generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>

            {today_section if today_section else '<p class="no-listings">No listings sold today.</p>'}
            {yesterday_section if yesterday_section else '<p class="no-listings">No listings sold yesterday.</p>'}
        </div>
    </body>
    </html>
    """


def send_email(config_name: str, listings: dict) -> bool:
    """Send email with listings for a config. Returns True if sent successfully."""
    if not SMTP_USER or not SMTP_PASS:
        print(f"[emailer] Skipping email for '{config_name}' — SMTP_USER or SMTP_PASS not set")
        return False

    if not listings["today"] and not listings["yesterday"]:
        print(f"[emailer] Skipping email for '{config_name}' — no new listings today or yesterday")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"eBay Sales Report: {config_name}"
        msg["From"] = SMTP_USER
        msg["To"] = RECIPIENT

        html_body = build_email_html(config_name, listings)
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)

        print(f"[emailer] Sent report to {RECIPIENT} for '{config_name}'")
        return True
    except Exception as e:
        print(f"[emailer] Failed to send email for '{config_name}': {e}")
        return False


def send_reports(config_names: list[str] = None):
    """Send emails for one or more configs.
    If config_names is None, send for all active configs.
    """
    if config_names is None:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT name FROM search_configs WHERE active = 1"
            ).fetchall()
        config_names = [r[0] for r in rows]

    if not config_names:
        print("[emailer] No configs found to report on")
        return

    for config_name in config_names:
        listings = get_today_yesterday_listings(config_name)
        send_email(config_name, listings)
