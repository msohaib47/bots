#!/usr/bin/env python3
"""
Shared ntfy.sh notifier — used by all bots.
Install: pip3 install requests
Usage:   from notifier import notify
         notify("BUY", "BTC/USD", "$71,500", details="EMA cross + RSI=45")
"""
import requests
import logging

NTFY_TOPIC = "sohaib-trading-2026"
NTFY_URL   = f"https://ntfy.sh/{NTFY_TOPIC}"

PRIORITY = {
    "BUY":          ("high",   "green_circle",   "5"),
    "SELL":         ("high",   "red_circle",      "5"),
    "STOP_LOSS":    ("urgent", "warning",         "5"),
    "TRAILING":     ("default","chart_increasing","3"),
    "OPEN":         ("high",   "moneybag",        "4"),
    "CLOSE":        ("high",   "moneybag",        "4"),
    "ASSIGNED":     ("high",   "inbox_tray",      "4"),
    "CALLED_AWAY":  ("high",   "outbox_tray",     "4"),
    "ERROR":        ("urgent", "rotating_light",  "5"),
    "INFO":         ("low",    "information",     "2"),
}

def notify(action: str, symbol: str, price: str = "", details: str = "", bot: str = ""):
    try:
        prio_label, emoji, prio_num = PRIORITY.get(action.upper(), ("default", "bell", "3"))

        title = f":{emoji}: {action} | {symbol}"
        if bot:
            title = f"[{bot}] {title}"

        body_parts = []
        if price:
            body_parts.append(f"Price: {price}")
        if details:
            body_parts.append(details)
        body = "\n".join(body_parts) if body_parts else action

        requests.post(
            NTFY_URL,
            data=body.encode("utf-8"),
            headers={
                "Title":    title,
                "Priority": prio_num,
                "Tags":     f"{emoji},chart_with_upwards_trend",
            },
            timeout=5,
        )
    except Exception as e:
        logging.getLogger(__name__).warning(f"ntfy notification failed: {e}")


def test():
    notify("BUY",       "BTC/USD",  "$71,500",  "EMA golden cross, RSI=45",   bot="CryptoBot")
    notify("SELL",      "SPY",      "$757.00",  "Trailing stop hit +85%",     bot="DayTradingBot")
    notify("ASSIGNED",  "MARA",     "$14.00",   "Put expired ITM, 100 shares", bot="WheelBot")
    notify("INFO",      "System",   "",         "All bots running",            bot="Monitor")
    print("Test notifications sent to ntfy.sh/sohaib-trading-2026")

if __name__ == "__main__":
    test()
