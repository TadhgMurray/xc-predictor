# Project: xc-predictor
# File:    scripts/chrome_path.py
# Purpose: Where Chrome is. One definition, because two launchers need it and
#          they disagreed.
#
# ⚠ REAL CHROME, NOT PLAYWRIGHT'S CHROMIUM. Cloudflare fingerprints the
#   binary and blocks the bundled build, which is also why both launchers pass
#   headless=False. The chromium entries below are a last-resort fallback, not
#   an equivalent: expect blocks on them.
#
# ★ THE anet LAUNCHER LEARNED THIS AND THE TFRRS ONE DID NOT (2026-09-18).
#   scripts/launcher.py resolved a path and named the reason in a comment;
#   tfrrs/driver/launch_tfrrs.py called pw.chromium.launch(headless=False)
#   with no executable_path at all, so it used the bundled chromium -- which
#   on a box that never ran `playwright install` does not exist, and on one
#   that did, gets blocked. Both import this now.

import os
import platform

_CANDIDATES = {
    "Windows": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ],
    "Linux": [
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/opt/google/chrome/chrome",
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
    ],
    "Darwin": [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ],
}


def candidates():
    """Every path we would look at on this OS, in order."""
    return list(_CANDIDATES.get(platform.system(), []))


def chromePath():
    """The Chrome binary, or a RuntimeError naming every path tried.

    ! RESOLVED WHEN CALLED, NOT AT IMPORT. Raising at import time breaks every
      module that merely imports the caller -- the tests do -- on any machine
      without Chrome.
    """
    override = os.environ.get("CHROME_PATH")
    if override:
        return override
    system = platform.system()
    if system not in _CANDIDATES:
        raise RuntimeError(f"Unsupported OS: {system}")
    for path in _CANDIDATES[system]:
        if os.path.exists(path):
            return path
    looked = "\n  ".join(_CANDIDATES[system])
    raise RuntimeError(
        "Google Chrome not found. Looked in:\n  " + looked + "\n"
        "Install it (Debian/Ubuntu: apt-get install -y "
        "./google-chrome-stable_current_amd64.deb) or set CHROME_PATH. "
        "Playwright's bundled chromium is NOT a substitute -- Cloudflare "
        "fingerprints the binary and blocks it.")


def found():
    """The path if one exists, else None. For a module-level constant that
    must not raise."""
    try:
        return chromePath()
    except RuntimeError:
        return None
