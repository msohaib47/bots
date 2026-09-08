#!/usr/bin/env python3
"""One-off diagnostic for the newly-confirmed market-data paths. Delete once verified."""
import webull

print('--- get_latest_price(SPY) ---')
try:
    print(webull.get_latest_price('SPY'))
except Exception as e:
    print(f'FAILED: {e}')
    if hasattr(e, 'response') and e.response is not None:
        print(f'  status={e.response.status_code} body={e.response.text[:500]}')

print('\n--- get_intraday_bars(SPY, 5Min, limit=3) ---')
try:
    print(webull.get_intraday_bars('SPY', '5Min', limit=3))
except Exception as e:
    print(f'FAILED: {e}')
    if hasattr(e, 'response') and e.response is not None:
        print(f'  status={e.response.status_code} body={e.response.text[:500]}')

print('\n--- find_atm_contract(SPY, call, ~current price) ---')
from config import ACCOUNTS
from webull import WebullClient
acct = ACCOUNTS['main']
client = WebullClient(acct['app_key'], acct['app_secret'], acct['account_id'])
try:
    price = webull.get_latest_price('SPY') or 650.0
    print(f'using spot={price}')
    print(client.find_atm_contract('SPY', 'call', price))
except Exception as e:
    print(f'FAILED: {e}')
    if hasattr(e, 'response') and e.response is not None:
        print(f'  status={e.response.status_code} body={e.response.text[:500]}')
