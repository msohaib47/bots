"""
import_cookies.py — load exported eBay cookies into the Playwright profile,
bypassing Chrome's cookie-file encryption entirely.

Why this exists:
import_chrome_profile.py (copying Chrome's profile folder wholesale) doesn't
work: Chrome's "App-Bound Encryption" on Windows ties the Cookies database
to the specific original installation, so a straight file copy decrypts to
nothing usable — that's by design, to stop exactly this kind of cookie
theft-by-copy. Instead, extract the live (already-decrypted) cookie values
from your normal, logged-in Chrome via a cookie-export extension, and load
those values directly into Playwright's own cookie store — which sidesteps
Chrome's on-disk encryption entirely since we're just injecting known
key/value pairs through Playwright's API, not reading an encrypted file.

Usage:
    1. Install the "Cookie Editor" extension (hotcleaner.com, Chrome Web
       Store id iphcomljdfghbkdcfndaijbokpgddeno) in your normal, everyday
       Chrome.
    2. Log into https://www.ebay.com/ normally in that browser (no
       automation involved, so hCaptcha behaves like it does on any
       ordinary site).
    3. On any ebay.com page, open the extension, Export, and enter a
       password to encrypt the export (mandatory — there's no plain-JSON
       option). Save the downloaded file (or its contents) as:
       ebayCompletedListings\\ebay_cookies.json
    4. Run:  python import_cookies.py
       (it will prompt for the same export password to decrypt it)
    5. Run login_local.py to sanity-check — it should now show you as
       signed in and the sold-listings search should load without a wall.
    6. Copy the profile to the server as usual:
       scp -r .pw-profile-local claude@192.168.1.250:/home/claude/data/ebay-pw-profile

Decryption note: this extension's encrypted export is AES-256-GCM, not the
common CryptoJS/OpenSSL "Salted__" format — reverse-engineered directly from
the extension's bundled JS (fjs129/eeditor129.js, functions G/X/Z) on
2026-08-07 since the export format isn't documented anywhere public:
  key  = PBKDF2-SHA256(password, salt=UTF8(password+password), iterations=1024, dkLen=32)
  blob = base64( IV[12 bytes] || AES-GCM-ciphertext || 16-byte auth tag )
"""

import base64
import getpass
import json
import sys
from pathlib import Path

from Crypto.Cipher import AES
from Crypto.Hash import SHA256
from Crypto.Protocol.KDF import PBKDF2
from playwright.sync_api import sync_playwright

PROFILE_DIR = Path(__file__).parent / ".pw-profile-local"
COOKIES_FILE = Path(__file__).parent / "ebay_cookies.json"


def _decrypt_cookie_editor(blob: str, password: str) -> str:
    raw = base64.b64decode(blob.strip())
    if len(raw) < 12 + 16:
        raise ValueError("Blob too short to contain a 12-byte IV and 16-byte GCM tag.")
    iv, ciphertext, tag = raw[:12], raw[12:-16], raw[-16:]
    salt = (password + password).encode("utf-8")
    key = PBKDF2(password.encode("utf-8"), salt, dkLen=32, count=1024, hmac_hash_module=SHA256)
    cipher = AES.new(key, AES.MODE_GCM, nonce=iv)
    return cipher.decrypt_and_verify(ciphertext, tag).decode("utf-8")


def _decode(data):
    """Recursively unwrap a parsed JSON value into a list of cookie dicts.
    Handles: a plain array, an array wrapped in an extra layer of
    string-quoting (paste artifact), Cookie Editor's actual encrypted-export
    shape {"url":..., "version":2, "data": "<AES-GCM blob>"}, and other
    dict wrappers like {"cookies": [...]}."""
    if isinstance(data, list):
        return data

    if isinstance(data, str):
        try:
            return _decode(json.loads(data))
        except json.JSONDecodeError:
            pass
        # Not JSON — treat as the encrypted blob itself.
        password = getpass.getpass("Cookie Editor export password: ")
        try:
            decrypted = _decrypt_cookie_editor(data, password)
        except Exception as e:
            print(f"Decryption failed: {e}")
            print("Double check the password matches what you entered during export.")
            sys.exit(1)
        try:
            return _decode(json.loads(decrypted))
        except json.JSONDecodeError:
            print("Decrypted successfully but the result isn't valid JSON — wrong password, most likely.")
            sys.exit(1)

    if isinstance(data, dict):
        # Cookie Editor's actual export shape: {"url":..., "version":2, "data": "<encrypted or plain>"}
        if "data" in data and isinstance(data["data"], (str, list)):
            return _decode(data["data"])
        for key in ("cookies", "result", "items"):
            if isinstance(data.get(key), list):
                return data[key]
        values = list(data.values())
        if values and all(isinstance(v, dict) and "value" in v for v in values):
            return values
        print(f"Cookie file is a dict with keys: {list(data.keys())[:20]}")
        print("Doesn't match any known export shape — paste a sample of the file structure and I'll adapt the script.")
        sys.exit(1)

    print(f"Unexpected cookie file format: got {type(data).__name__}, expected a list of cookies.")
    sys.exit(1)


def _load_cookies_json() -> list:
    text = COOKIES_FILE.read_text(encoding="utf-8").strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = text
    return _decode(data)


SAME_SITE_MAP = {
    "no_restriction": "None",
    "unspecified":    "Lax",
    "lax":            "Lax",
    "strict":         "Strict",
    "none":           "None",
}


def _convert(raw_cookies: list) -> list:
    converted = []
    for c in raw_cookies:
        same_site = SAME_SITE_MAP.get(str(c.get("sameSite", "")).lower(), "Lax")

        # chrome.cookies.getAll() (what this extension reads from) represents
        # "applies to all subdomains" via a separate hostOnly:false boolean,
        # with the domain string itself never carrying a leading dot.
        # Playwright/CDP's cookie API has no such separate flag — it infers
        # domain-vs-host-only purely from whether the domain string itself
        # starts with a dot. Without re-adding it here, every hostOnly:false
        # cookie silently becomes host-only for the bare domain and never
        # matches www.ebay.com (or any subdomain) at all — this is what
        # caused the sign-in wall to persist even after cookies "loaded" with
        # no errors, found 2026-08-07.
        domain = c["domain"]
        if not c.get("hostOnly", False) and not domain.startswith("."):
            domain = "." + domain

        cookie = {
            "name":     c["name"],
            "value":    c["value"],
            "domain":   domain,
            "path":     c.get("path", "/"),
            "httpOnly": bool(c.get("httpOnly", False)),
            "secure":   bool(c.get("secure", False)),
            "sameSite": same_site,
        }
        if not c.get("session") and c.get("expirationDate"):
            cookie["expires"] = c["expirationDate"]
        converted.append(cookie)
    return converted


def main():
    if not COOKIES_FILE.exists():
        print(f"Cookie file not found: {COOKIES_FILE}")
        print("Export cookies from Cookie Editor and save them there first (see docstring).")
        sys.exit(1)

    raw = _load_cookies_json()
    cookies = _convert(raw)
    print(f"Loaded {len(cookies)} cookies from {COOKIES_FILE}")

    with sync_playwright() as pw:
        try:
            context = pw.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR),
                channel="chrome",
                headless=False,
                viewport={"width": 1366, "height": 768},
                locale="en-US",
            )
        except Exception:
            context = pw.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR),
                headless=False,
                viewport={"width": 1366, "height": 768},
                locale="en-US",
            )

        context.add_cookies(cookies)

        page = context.new_page()
        page.goto("https://www.ebay.com/", wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        print(f"Loaded homepage — title: {page.title()}")
        print("Check the browser window: are you shown as signed in (account name/avatar top-right)?")
        input("Press Enter to save and close... ")

        context.close()

    print(f"\nCookies imported into {PROFILE_DIR}")
    print("Now run login_local.py to verify the sold-listings search works, then push to the server:")
    print(f'  scp -r "{PROFILE_DIR}" claude@192.168.1.250:/home/claude/data/ebay-pw-profile')


if __name__ == "__main__":
    main()
