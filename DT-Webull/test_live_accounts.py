#!/usr/bin/env python3
"""
One-off: list real accounts + balances under the LIVE (production) app_key,
so the user can pick which one this bot should trade. Read-only -- no
orders, no account_id selection made automatically. Delete once the user has
picked an account_id and it's been added to .env.
"""
import os
from webull import WebullClient

# Read directly rather than via config.ACCOUNTS -- "live" is deliberately NOT
# in WEBULL_ACCOUNTS yet (this is a discovery step, not "start trading this
# account"), so config._load_accounts() never builds an entry for it.
live = {
    'app_key': os.getenv('WEBULL_LIVE_APP_KEY', ''),
    'app_secret': os.getenv('WEBULL_LIVE_APP_SECRET', ''),
    'base_url': os.getenv('WEBULL_LIVE_BASE_URL', 'https://api.webull.com'),
}
if not live['app_key']:
    print("WEBULL_LIVE_APP_KEY not set in .env")
    raise SystemExit(1)

print(f'Testing against {live["base_url"]} (production)\n')
client = WebullClient(live['app_key'], live['app_secret'], account_id='', base_url=live['base_url'])

accounts = client.list_accounts()
print(f'Found {len(accounts)} account(s):\n')
for a in accounts:
    print(f'  account_id={a.get("account_id")}')
    print(f'    number={a.get("account_number")}  type={a.get("account_type")}  '
          f'label={a.get("account_label")}  class={a.get("account_class")}')
    try:
        bal_client = WebullClient(live['app_key'], live['app_secret'], a['account_id'], base_url=live['base_url'])
        bal = bal_client.get_account()
        print(f'    cash=${bal.get("total_cash_balance")}  '
              f'net_liq=${bal.get("total_net_liquidation_value")}  '
              f'day_trades_left={bal.get("day_trades_left")}')
    except Exception as e:
        print(f'    balance lookup failed: {e}')
    print()
