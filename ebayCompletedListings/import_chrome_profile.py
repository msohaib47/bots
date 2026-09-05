"""
import_chrome_profile.py — copy an already-logged-in Chrome profile into the
Playwright profile dir, so the scraper can reuse a real session instead of
ever running hCaptcha inside an automated browser.

SUPERSEDED 2026-08-07: tested and confirmed this doesn't work — Chrome's
"App-Bound Encryption" on Windows ties the Cookies database to the specific
original installation, so a plain file copy of the profile decrypts to
nothing usable (signed-out state even after a clean copy with no errors).
Use import_cookies.py instead, which extracts live cookie values via a
cookie-export extension and injects them through Playwright's API, avoiding
the on-disk encryption entirely. Left here for reference only.

Why this exists:
login_local.py drives Chrome via Playwright's CDP connection, and hCaptcha
detects that connection directly — "Your browser or network settings are
blocking hCaptcha" plus chrome-extension://invalid console errors and a 483
response on /signin/srv/identifier. This isn't fixable by picking a
different browser or User-Agent; it's the CDP automation link itself being
detected. Real Chrome, driven by hand with no automation attached, never
triggers it.

Usage:
    1. Log into https://www.ebay.com/ in your normal, everyday Chrome —
       no automation involved, so hCaptcha (if it even appears) behaves
       normally and you solve it like any other website.
    2. Close ALL Chrome windows completely (the profile files are locked
       while Chrome is running, so a copy taken while it's open can be
       inconsistent/corrupt).
    3. Run:  python import_chrome_profile.py
       (pass a profile name as an argument if you use a non-default Chrome
       profile, e.g. `python import_chrome_profile.py "Profile 1"` — check
       chrome://version in the profile you used, "Profile Path" line, for
       the exact folder name)
    4. Run login_local.py — it should now open already signed in (just
       press Enter at the prompt) and the sanity check should pass. This
       verifies the imported session before it goes to the server.
    5. Copy the profile to the server as usual:
       scp -r .pw-profile-local claude@192.168.1.250:/home/claude/data/ebay-pw-profile
"""

import os
import shutil
import sys
from pathlib import Path

CHROME_USER_DATA = Path(os.environ["LOCALAPPDATA"]) / "Google" / "Chrome" / "User Data"
DEST_ROOT = Path(__file__).parent / ".pw-profile-local"


def main():
    profile_name = sys.argv[1] if len(sys.argv) > 1 else "Default"
    src = CHROME_USER_DATA / profile_name

    if not src.exists():
        print(f"Profile not found: {src}")
        print(f"Available profiles under {CHROME_USER_DATA}:")
        for p in CHROME_USER_DATA.iterdir():
            if p.is_dir() and (p / "Preferences").exists():
                print(f"  {p.name}")
        sys.exit(1)

    print(f"Source: {src}")
    print(f"Destination: {DEST_ROOT / profile_name}")
    input("\nMake sure ALL Chrome windows are fully closed, then press Enter to copy... ")

    if DEST_ROOT.exists():
        shutil.rmtree(DEST_ROOT)
    DEST_ROOT.mkdir(parents=True)

    dest = DEST_ROOT / profile_name
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("Cache", "Code Cache", "GPUCache"))

    if profile_name != "Default":
        # Playwright/Chrome expects "Default" as the profile Chrome opens on
        # launch unless told otherwise; simplest is to also drop a copy in
        # under that name so login_local.py doesn't need to know the profile.
        shutil.copytree(dest, DEST_ROOT / "Default")

    print(f"\nCopied. Now run login_local.py to verify the session.")


if __name__ == "__main__":
    main()
