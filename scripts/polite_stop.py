# Project: xc-predictor / scripts
# File:    polite_stop.py
# Purpose: Stop the athletic.net scrape at the first sign it is too fast.
#
# ★ A RATE LIMIT MEANS "SLOWER", AND THE SLOWEST IS "NOT TONIGHT" (owner,
#   2026-10-11: athletic.net rate-limited the server). With NO_VPN=1 there is
#   one IP and nothing to rotate to -- and rotating to dodge a limit is not
#   respecting it anyway. The scraper's old answer to a 429 was to mark the
#   meet failed and take the next one at the same pace, so a limited run kept
#   knocking all night. Now, in polite mode, the FIRST 429 or Cloudflare
#   block stops every session: their claimed meets go back to the queue and
#   the run ends. The next run (the nightly update) starts where it stopped.
#
# ! POLITE IS THE DEFAULT WHENEVER NO_VPN=1; ANET_POLITE=0 turns it off.
import os
import threading

_STOP = threading.Event()
_WHY = []


class PoliteStop(Exception):
    """Raised inside a session to end it politely."""


def active():
    raw = os.environ.get("ANET_POLITE")
    if raw is not None and raw.strip() != "":
        return raw.strip() not in ("0", "false")
    return os.environ.get("NO_VPN", "") not in ("", "0", "false")


def trip(label, why):
    """Record the first reason and stop every session (polite mode only).
    Returns True when the run is now stopping."""
    if not active():
        return False
    if not _STOP.is_set():
        _WHY.append(why)
        print(f"{label} [polite] {why} -- stopping the whole run; claimed meets "
              f"go back to the queue, the next run resumes", flush=True)
        _STOP.set()
    return True


def stopped():
    return _STOP.is_set()


def check():
    """Raise PoliteStop when the run is stopping."""
    if _STOP.is_set():
        raise PoliteStop(_WHY[0] if _WHY else "stopped")
