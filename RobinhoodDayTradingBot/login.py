"""
Robinhood login for RobinhoodDayTradingBot — handles the modern
`verification_workflow` (SUV / pathfinder) device-approval flow.

Run once interactively:
    python3 login.py

When prompted, approve the login on your Robinhood phone app. The script
polls until you approve (up to 2 minutes), then saves the session to
~/.tokens/robinhood.pickle so cron runs reuse it with no further prompts.

Set RH_TOTP_SECRET in .env if you instead use an authenticator app.
"""
import os
import sys
import time
import json
import pickle
import getpass
import warnings

warnings.filterwarnings('ignore')

import requests
from dotenv import load_dotenv
load_dotenv()

import robin_stocks.robinhood as rh
import robin_stocks.robinhood.authentication as auth

# ── Endpoints ──────────────────────────────────────────────────────────────────
TOKEN_URL       = 'https://api.robinhood.com/oauth2/token/'
USER_MACHINE    = 'https://api.robinhood.com/pathfinder/user_machine/'
INQUIRY_VIEW    = 'https://api.robinhood.com/pathfinder/inquiries/{}/user_view/'
PROMPT_STATUS   = 'https://api.robinhood.com/push/{}/get_prompts_status/'
CHALLENGE_RESP  = 'https://api.robinhood.com/challenge/{}/respond/'
CLIENT_ID       = 'c82SH0WZOsabOXGP2sxqcj34FxkvfnWRZBKlBjFS'
PICKLE_PATH     = os.path.expanduser('~/.tokens/robinhood.pickle')

POLL_TIMEOUT    = 120   # seconds to wait for phone approval
VERBOSE         = '--verbose' in sys.argv or '-v' in sys.argv

# Use a plain session with default headers — this is exactly what the working
# diagnostic used. Adding custom headers (User-Agent / X-Robinhood-API-Version)
# caused Robinhood to reject the login with a misleading "invalid credentials".
SESSION = requests.Session()


def _dump(label: str, obj):
    if VERBOSE:
        print(f'\n--- {label} ---')
        try:
            print(json.dumps(obj, indent=2)[:2000])
        except Exception:
            print(str(obj)[:2000])


def _save_session(data: dict, device_token: str):
    os.makedirs(os.path.dirname(PICKLE_PATH), exist_ok=True)
    with open(PICKLE_PATH, 'wb') as f:
        pickle.dump({
            'token_type':    data['token_type'],
            'access_token':  data['access_token'],
            'refresh_token': data['refresh_token'],
            'device_token':  device_token,
        }, f)
    print(f'  Session saved to {PICKLE_PATH}')


def _try_existing_session() -> bool:
    if not os.path.isfile(PICKLE_PATH):
        return False
    try:
        with open(PICKLE_PATH, 'rb') as f:
            d = pickle.load(f)
        hdr = {'Authorization': f'{d["token_type"]} {d["access_token"]}'}
        r = requests.get('https://api.robinhood.com/accounts/', headers=hdr, timeout=10)
        if r.status_code == 200:
            auth.set_login_state(True)
            auth.update_session('Authorization', f'{d["token_type"]} {d["access_token"]}')
            print('  Existing session still valid — no login needed!')
            return True
    except Exception:
        pass
    print('  Saved session expired or missing — logging in fresh.')
    return False


def _handle_pathfinder(workflow_id: str, device_token: str, totp_secret: str) -> bool:
    """
    Drive the SUV / pathfinder verification workflow to completion.
    Returns True once the device/challenge is approved.
    """
    # Step 1: start the user_machine with the workflow id
    machine_payload = {
        'device_id': device_token,
        'flow':      'suv',
        'input':     {'workflow_id': workflow_id},
    }
    r = SESSION.post(USER_MACHINE, json=machine_payload, timeout=15)
    machine = r.json()
    _dump('user_machine response', machine)

    machine_id = machine.get('id')
    if not machine_id:
        print(f'  ERROR: no machine id returned. Response: {machine}')
        return False

    inquiry_url = INQUIRY_VIEW.format(machine_id)

    # Step 2: fetch the inquiry view to discover the challenge
    deadline = time.time() + POLL_TIMEOUT
    challenge_handled = False

    while time.time() < deadline:
        r = SESSION.get(inquiry_url, timeout=15)
        view = r.json()
        _dump('inquiry user_view', view)

        context = view.get('context', {}) or {}
        sheriff = context.get('sheriff_challenge') or {}
        ch_type   = sheriff.get('type')
        ch_status = sheriff.get('status')
        ch_id     = sheriff.get('id')

        # If the overall workflow already shows approved, we're done
        type_context = view.get('type_context', {}) or {}
        if type_context.get('result') == 'workflow_status_approved':
            print('  Workflow approved.')
            return True

        if not sheriff:
            # No challenge surfaced yet — wait and re-poll
            time.sleep(3)
            continue

        # ── Device push approval ───────────────────────────────────────────────
        if ch_type == 'prompt' and not challenge_handled:
            print('\n  >>> Open your Robinhood phone app and tap APPROVE <<<')
            print(f'      Waiting up to {POLL_TIMEOUT}s for approval...')
            sys.stdout.flush()
            prompt_url = PROMPT_STATUS.format(ch_id)
            dots = 0
            while time.time() < deadline:
                ps = SESSION.get(prompt_url, timeout=15).json()
                _dump('prompt status', ps)
                status = ps.get('challenge_status') or ps.get('status')
                if status == 'validated':
                    print('\n  Approval received from phone!')
                    challenge_handled = True
                    break
                dots = (dots % 5) + 1
                print('  waiting' + '.' * dots + '   ', end='\r')
                sys.stdout.flush()
                time.sleep(3)
            if not challenge_handled:
                print('\n  Timed out waiting for phone approval.')
                return False

        # ── SMS / email code ───────────────────────────────────────────────────
        elif ch_type in ('sms', 'email') and not challenge_handled:
            label = ch_type.upper()
            print(f'\n  Robinhood sent a {label} code.')
            for _ in range(3):
                code = input(f'  Enter {label} code: ').strip()
                cr = SESSION.post(CHALLENGE_RESP.format(ch_id),
                                  json={'response': code}, timeout=15).json()
                _dump('challenge respond', cr)
                if cr.get('status') == 'validated':
                    challenge_handled = True
                    break
                print(f'  Incorrect. {cr.get("remaining_attempts", "?")} left.')
            if not challenge_handled:
                return False

        # ── Authenticator app TOTP ─────────────────────────────────────────────
        elif ch_type == 'totp' and not challenge_handled:
            code = None
            if totp_secret:
                try:
                    import pyotp
                    code = pyotp.TOTP(totp_secret).now()
                    print(f'  Auto-generated TOTP: {code}')
                except Exception as e:
                    print(f'  TOTP gen failed: {e}')
            if not code:
                code = input('  Enter authenticator code: ').strip()
            cr = SESSION.post(CHALLENGE_RESP.format(ch_id),
                              json={'response': code}, timeout=15).json()
            _dump('totp respond', cr)
            if cr.get('status') == 'validated':
                challenge_handled = True
            else:
                print('  TOTP rejected.')
                return False

        # After handling the challenge, push the inquiry forward
        if challenge_handled:
            cont = SESSION.post(inquiry_url,
                                json={'sequence': 0, 'user_input': {'status': 'continue'}},
                                timeout=15)
            try:
                cont_json = cont.json()
                _dump('inquiry continue', cont_json)
                result = (cont_json.get('type_context', {}) or {}).get('result')
                if result == 'workflow_status_approved' or cont.status_code in (200, 201):
                    print('  Verification workflow complete.')
                    return True
            except Exception:
                pass
            return True

        time.sleep(3)

    print('  Verification timed out.')
    return False


def do_login(username: str, password: str, totp_secret: str = '') -> bool:
    device_token = auth.generate_device_token()
    if os.path.isfile(PICKLE_PATH):
        try:
            with open(PICKLE_PATH, 'rb') as f:
                device_token = pickle.load(f).get('device_token', device_token)
        except Exception:
            pass

    payload = {
        'client_id':      CLIENT_ID,
        'expires_in':     604800,          # 7 days
        'grant_type':     'password',
        'password':       password,
        'scope':          'internal',
        'username':       username,
        'challenge_type': 'sms',
        'device_token':   device_token,
    }

    print('\nContacting Robinhood...')
    sys.stdout.flush()
    r = SESSION.post(TOKEN_URL, json=payload, timeout=15)
    data = r.json()
    _dump('initial token response', data)

    # ── Modern verification workflow ──────────────────────────────────────────
    if 'verification_workflow' in data:
        wf_id = data['verification_workflow']['id']
        print(f'  Device verification required (workflow {wf_id[:8]}...).')
        if not _handle_pathfinder(wf_id, device_token, totp_secret):
            return False
        # Re-request the token now that the device is approved
        print('\n  Retrieving access token...')
        for attempt in range(5):
            time.sleep(2)
            r = SESSION.post(TOKEN_URL, json=payload, timeout=15)
            data = r.json()
            _dump(f'token retry {attempt+1}', data)
            if 'access_token' in data:
                break
            if 'verification_workflow' not in data:
                break

    # ── Legacy flows (just in case) ───────────────────────────────────────────
    elif 'mfa_required' in data:
        code = None
        if totp_secret:
            try:
                import pyotp
                code = pyotp.TOTP(totp_secret).now()
            except Exception:
                pass
        if not code:
            code = input('  MFA code: ').strip()
        payload['mfa_code'] = code
        r = SESSION.post(TOKEN_URL, json=payload, timeout=15)
        data = r.json()

    # ── Finalize ──────────────────────────────────────────────────────────────
    if not data or 'access_token' not in data:
        detail = data.get('detail', json.dumps(data)) if data else 'no response'
        print(f'\n  Login failed: {detail}')
        return False

    auth.update_session('Authorization', f'{data["token_type"]} {data["access_token"]}')
    auth.set_login_state(True)
    _save_session(data, device_token)
    return True


def main():
    print('=' * 55)
    print('  RobinhoodDayTradingBot -- Login Setup')
    print('=' * 55)

    print('\nChecking for saved session...')
    if _try_existing_session():
        return

    username    = os.getenv('RH_USERNAME') or input('\nRobinhood email: ').strip()
    password    = os.getenv('RH_PASSWORD') or getpass.getpass('Robinhood password: ')
    totp_secret = os.getenv('RH_TOTP_SECRET', '')

    if not username or not password:
        print('ERROR: username and password required (set in .env).')
        sys.exit(1)

    print(f'\nLogging in as: {username}')
    if not do_login(username, password, totp_secret):
        print('\nLogin failed. Re-run with --verbose to see raw API responses.')
        sys.exit(1)

    print('\nLogin successful!')
    try:
        acct = os.getenv('RH_ACCOUNT', '706672094')
        profile = rh.profiles.load_portfolio_profile(account_number=acct)
        bp = float(profile.get('withdrawable_amount', 0) or 0)
        mv = float(profile.get('market_value', 0) or 0)
        print(f'  Account:       {acct}')
        print(f'  Buying power:  ${bp:,.2f}')
        print(f'  Portfolio:     ${mv + bp:,.2f}')
    except Exception as e:
        print(f'  (Could not fetch portfolio: {e})')
    print('\nSession saved. Cron jobs will now run without prompts.')
    print('Re-run this script in ~7 days to refresh.\n')


if __name__ == '__main__':
    main()
