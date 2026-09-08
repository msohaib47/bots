#!/usr/bin/env python3
"""
One-off diagnostic: test the real credentials against the Webull sandbox API
to see whether webull.py's signing implementation and guessed endpoint paths
actually work. Not part of the bot's normal run path -- delete once the real
account_id is confirmed and put in .env.
"""
import sys
from config import ACCOUNTS
from webull import WebullClient

acct = ACCOUNTS['main']
client = WebullClient(acct['app_key'], acct['app_secret'], acct.get('account_id', ''))

print(f'Testing against {client.base_url}')
print(f'app_key={client.app_key[:12]}... account_id={client.account_id!r}\n')

try:
    accounts = client.list_accounts()
    print('list_accounts() SUCCESS:')
    print(accounts)
except Exception as e:
    print(f'list_accounts() FAILED: {type(e).__name__}: {e}')
    if hasattr(e, 'response') and e.response is not None:
        print(f'  status={e.response.status_code}  body={e.response.text[:500]}')

print()
try:
    acct = client.get_account()
    print('get_account() SUCCESS:')
    print(acct)
    print(f'  get_cash() -> {client.get_cash()}')
except Exception as e:
    print(f'get_account() FAILED: {type(e).__name__}: {e}')
    if hasattr(e, 'response') and e.response is not None:
        print(f'  status={e.response.status_code}  body={e.response.text[:500]}')

print()
try:
    positions = client.list_positions()
    print('list_positions() SUCCESS:')
    print(positions)
except Exception as e:
    print(f'list_positions() FAILED: {type(e).__name__}: {e}')
    if hasattr(e, 'response') and e.response is not None:
        print(f'  status={e.response.status_code}  body={e.response.text[:500]}')
