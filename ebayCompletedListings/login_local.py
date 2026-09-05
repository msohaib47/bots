"""
login_local.py — one-time interactive eBay login, run on a machine with a display
(i.e. your Windows machine, NOT the headless server).

Why this exists:
Testing on 2026-08-07 proved eBay's sold/completed-listings search
(LH_Sold=1&LH_Complete=1) requires an actual signed-in account — a completely
fresh, never-used browser profile could load the eBay homepage and a plain
search fine, but hit a "Sign in or Register" wall the instant LH_Sold=1 was
added. No amount of stealth patching, warm-up navigation, or proxying fixes
that; it's a real auth gate, not bot detection. scraper.py's persistent
profile has been hitting this wall on every run since the 2026-08-03
Playwright rewrite, meaning the bot has silently found 0 listings ever since
(last real data is from 2026-07-22/23).

Usage:
    pip install playwright python-dotenv
    playwright install chromium   # only needed as a fallback if Chrome isn't found below
    python login_local.py

Note: channel="chrome" below uses your existing system Chrome install
directly (the one in Program Files) — it does NOT download a separate copy.
You do not need to run `playwright install chrome` if you already have
Chrome installed normally; that command is only for machines with no Chrome
at all (e.g. a bare CI box), where it fetches Playwright's own managed copy.

This opens a real, visible Chrome window (falls back to Playwright's bundled
Chromium if Chrome isn't found) against a local profile directory and pauses
so you can log into your eBay account by hand (including any 2FA/CAPTCHA/
verify-yourself challenge eBay throws at the login form — this script can't
solve that for you, it just needs a human in the loop once). Once you're
logged in and the eBay homepage shows your account, press Enter in this
terminal to save the session and close the browser.

After that, copy the resulting profile directory to the server so the bot's
headless scraper can reuse the authenticated session:

    scp -r .pw-profile-local claude@192.168.1.250:/home/claude/data/ebay-pw-profile

Re-run this whenever the server-side scraper starts hitting the sign-in wall
again (eBay sessions eventually expire/get revoked).

IMPORTANT — if eBay's hCaptcha challenge fails to render here ("Your browser
or network settings are blocking hCaptcha", chrome-extension://invalid
console errors, a 483 on /signin/srv/identifier): that's hCaptcha detecting
the Chrome DevTools Protocol connection Playwright uses to drive the browser
at all — it's not fixable by switching browsers or User-Agent, since any
CDP-driven automation (Playwright/Puppeteer/Selenium) trips it. Use
import_cookies.py instead: log into eBay by hand in your normal, completely
unautomated everyday Chrome (hCaptcha behaves normally there), export
cookies with a browser extension, and run that script to load them into
.pw-profile-local (a plain file copy of Chrome's profile does NOT work —
Chrome's App-Bound Encryption ties the Cookies database to the original
install; see import_chrome_profile.py's docstring). Re-run this script
afterward just to sanity-check the imported session before pushing it to
the server.
"""

from pathlib import Path
from playwright.sync_api import sync_playwright

PROFILE_DIR = Path(__file__).parent / ".pw-profile-local"


def _launch(pw):
    """Prefer driving the real installed Chrome (channel='chrome') over
    Playwright's bundled test-build Chromium. eBay's "verify yourself"
    challenge (PerimeterX press-and-hold / Arkose-style widget) frequently
    fails to render — blank box, dead spinner, unresponsive puzzle — on the
    bundled Chromium: it carries automation markers these widgets check for,
    and (when combined with a spoofed User-Agent) reports capabilities the
    engine doesn't actually have, which breaks the widget's own rendering
    logic. Real Chrome doesn't have either problem, so no UA override here —
    let Chrome report itself honestly.
    """
    try:
        return pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="chrome",
            headless=False,
            viewport={"width": 1366, "height": 768},
            locale="en-US",
            args=["--disable-blink-features=AutomationControlled"],
        )
    except Exception as e:
        print(f"Could not launch real Chrome ({e}); falling back to Playwright's bundled Chromium.")
        print("If the verify-yourself challenge still fails to render, install Google Chrome and re-run.\n")
        return pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1366, "height": 768},
            locale="en-US",
            args=["--disable-blink-features=AutomationControlled"],
        )


def main():
    print(f"Profile directory: {PROFILE_DIR}")
    print("A Chrome window will open. Log into your eBay account, then come")
    print("back here and press Enter once you're signed in.\n")

    with sync_playwright() as pw:
        context = _launch(pw)
        page = context.new_page()
        page.goto("https://signin.ebay.com/")

        input("Press Enter here once you're logged in (and the browser shows your account)... ")

        # Sanity check: sold-listings search should no longer show the sign-in wall.
        page.goto(
            "https://www.ebay.com/sch/i.html?_nkw=laptop&_sacat=0&LH_Sold=1&LH_Complete=1",
            wait_until="domcontentloaded",
        )
        page.wait_for_timeout(2000)
        title = page.title()
        if "Sign in" in title or "Register" in title:
            print(f"\n⚠ Still seeing a sign-in wall (title='{title}'). Login may not have completed — try again.")
        else:
            print(f"\n✓ Sold-listings search loads normally now (title='{title}'). Session looks good.")

        context.close()

    print(f"\nSession saved to {PROFILE_DIR}")
    print("Now copy it to the server:")
    print(f'  scp -r "{PROFILE_DIR}" claude@192.168.1.250:/home/claude/data/ebay-pw-profile')


if __name__ == "__main__":
    main()
