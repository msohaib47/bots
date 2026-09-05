"""
analyze.py — Summary statistics for scraped sold listings

Provides:
  - print_stats()       : CLI summary for all configs
  - stats_for_config()  : returns dict of stats for one config
  - export_csv()        : dump listings to CSV
"""

import csv
import statistics
from pathlib import Path
from db import get_conn, get_listings


def stats_for_config(config_name: str, days: int = 90) -> dict:
    """
    Compute summary stats for a config over the last N days.
    Returns a dict with avg, median, min, max, std_dev, count, outliers.
    """
    listings = get_listings(config_name, days=days)
    prices = [l["total_price"] for l in listings if l["total_price"]]

    if not prices:
        return {"config": config_name, "count": 0}

    avg = statistics.mean(prices)
    med = statistics.median(prices)
    std = statistics.stdev(prices) if len(prices) > 1 else 0

    # IQR-based outlier detection
    sorted_p = sorted(prices)
    q1 = sorted_p[len(sorted_p) // 4]
    q3 = sorted_p[(3 * len(sorted_p)) // 4]
    iqr = q3 - q1
    low_fence = q1 - 1.5 * iqr
    high_fence = q3 + 1.5 * iqr

    outliers_low  = [p for p in prices if p < low_fence]
    outliers_high = [p for p in prices if p > high_fence]

    return {
        "config":        config_name,
        "count":         len(prices),
        "avg":           round(avg, 2),
        "median":        round(med, 2),
        "min":           round(min(prices), 2),
        "max":           round(max(prices), 2),
        "std_dev":       round(std, 2),
        "q1":            round(q1, 2),
        "q3":            round(q3, 2),
        "outliers_low":  sorted(outliers_low),
        "outliers_high": sorted(outliers_high, reverse=True),
        "days":          days,
    }


def print_stats(days: int = 90):
    """Print a formatted stats table for all active configs."""
    with get_conn() as conn:
        configs = conn.execute(
            "SELECT name FROM search_configs WHERE active = 1"
        ).fetchall()

    if not configs:
        print("No active configs found.")
        return

    print(f"\n{'─'*70}")
    print(f"  eBay Sold Price Summary  (last {days} days)")
    print(f"{'─'*70}")

    for row in configs:
        name = row["name"]
        s = stats_for_config(name, days=days)

        if s["count"] == 0:
            print(f"\n  {name}: no data yet")
            continue

        print(f"""
  {name}
  ─────────────────────────────────────────
  Listings scraped : {s['count']}
  Avg total price  : ${s['avg']:>10,.2f}
  Median           : ${s['median']:>10,.2f}
  Std deviation    : ${s['std_dev']:>10,.2f}
  Range            : ${s['min']:,.2f}  →  ${s['max']:,.2f}
  IQR              : ${s['q1']:,.2f}  –  ${s['q3']:,.2f}
  High outliers    : {len(s['outliers_high'])} listings above ${s['q3'] + 1.5*(s['q3']-s['q1']):,.0f}
  Low outliers     : {len(s['outliers_low'])} listings below ${s['q1'] - 1.5*(s['q3']-s['q1']):,.0f}""")

    print(f"\n{'─'*70}\n")


def export_csv(config_name: str = None, days: int = None, out_path: str = None):
    """
    Export listings to a CSV file.
    Defaults to 'exports/<config_name>_<date>.csv'
    """
    listings = get_listings(config_name, days=days)
    if not listings:
        print("No listings to export.")
        return

    Path("exports").mkdir(exist_ok=True)
    from datetime import date
    slug = (config_name or "all").replace(" ", "_").lower()
    out_path = out_path or f"exports/{slug}_{date.today().isoformat()}.csv"

    keys = [
        "search_config", "title", "sold_price", "shipping_cost", "total_price",
        "sold_date", "condition", "listing_url", "scraped_at"
    ]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(listings)

    print(f"Exported {len(listings)} listings to {out_path}")
    return out_path


if __name__ == "__main__":
    print_stats()
