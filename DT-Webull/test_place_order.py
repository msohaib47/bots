#!/usr/bin/env python3
"""One-off: place a single small test option order on the paper account to
validate the order-placement path end-to-end. Delete once verified."""
from config import ACCOUNTS
from webull import WebullClient, get_latest_price

acct = ACCOUNTS['main']
client = WebullClient(acct['app_key'], acct['app_secret'], acct['account_id'])

spot = get_latest_price('SPY')
print(f'SPY spot: {spot}')

contract = client.find_atm_contract('SPY', 'call', spot)
print(f'Contract: {contract}')

if not contract:
    print('No contract found -- aborting test order.')
else:
    print(f'\nPlacing BUY 1x {contract["symbol"]} @ ~${contract["mid"] + 0.01:.2f} limit...')
    order = client.buy_option(contract, 1)
    print('Order result:', order)
    print('last_error_code:', client.last_error_code)
