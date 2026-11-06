#!/usr/bin/env python3
"""
edge_purge.py -- empty Cloudflare's copy of the site when what it shows has
changed (a pipeline go-live, an athlete hidden at their request).

    python scripts/edge_purge.py                      # everything, now
    python scripts/edge_purge.py --delay 960          # everything, in 16 min
    python scripts/edge_purge.py --url https://racecast.co/athlete/123

★ WHY (2026-11-06: Cloudflare's hit rate fell from ~15% to ~2% while pages
  took 30-60 s at the origin). Pages and board APIs are public with a 15
  minute edge TTL (app.PAGE_S_MAXAGE), chosen because nothing told the edge
  when a go-live changed the numbers: a longer TTL was a longer window of
  last night's ratings. With a purge at every go-live the TTL stops being a
  staleness bound and can be raised (XCP_PAGE_S_MAXAGE) so the long tail of
  athlete, race and school pages is answered at the edge between runs.

! A NO-OP WITHOUT CREDENTIALS. XCP_CF_ZONE_ID and XCP_CF_API_TOKEN (a token
  with Zone > Cache Purge on this zone only) come from /etc/xc-predictor.env;
  unset, this prints that and exits 0, so the pipeline step is harmless on a
  box that has not been set up.

! PURGE TWICE AFTER A GO-LIVE. The workers keep their own copies of a board
  (app._BOARD_API_TTL, 900 s): an edge refilled the moment after the first
  purge can be refilled from a worker still holding the old rows. The second
  purge, once those have run out, catches it. The pipeline runs the second
  one detached (--delay), so the run does not wait for it.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

API = "https://api.cloudflare.com/client/v4/zones/{zone}/purge_cache"
# ! the site's own contact, never a person's (CLAUDE.md, test_no_owner_address)
UA = "racecast-edge-purge (racecast@racecast.co)"
_BATCH = 30            # Cloudflare's limit on files per purge call


def _credentials(env=os.environ):
    zone = (env.get("XCP_CF_ZONE_ID") or "").strip()
    token = (env.get("XCP_CF_API_TOKEN") or "").strip()
    return (zone, token) if zone and token else (None, None)


def bodies(urls):
    """The request bodies for one purge: everything, or the URLs in batches."""
    if not urls:
        return [{"purge_everything": True}]
    return [{"files": urls[i:i + _BATCH]} for i in range(0, len(urls), _BATCH)]


def purge(urls=None, env=os.environ, opener=urllib.request.urlopen):
    """Send the purge; True when Cloudflare said success to every call,
    None when there are no credentials (nothing sent)."""
    zone, token = _credentials(env)
    if not zone:
        print("edge purge: XCP_CF_ZONE_ID / XCP_CF_API_TOKEN not set -- skipped")
        return None
    ok = True
    for body in bodies(list(urls or [])):
        req = urllib.request.Request(
            API.format(zone=zone), data=json.dumps(body).encode(), method="POST",
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json", "User-Agent": UA})
        try:
            with opener(req, timeout=30) as resp:
                got = json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            got = {"success": False, "errors": [f"HTTP {exc.code}"]}
        except Exception as exc:                        # noqa: BLE001
            got = {"success": False, "errors": [f"{type(exc).__name__}: {exc}"]}
        what = "everything" if body.get("purge_everything") else f"{len(body['files'])} URL(s)"
        if got.get("success"):
            print(f"edge purge: {what} -- ok")
        else:
            ok = False
            print(f"edge purge: {what} -- FAILED {got.get('errors')}")
    return ok


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", action="append", default=[],
                    help="purge only this URL (repeatable); default everything")
    ap.add_argument("--delay", type=int, default=0,
                    help="seconds to wait first (the second purge after a go-live)")
    args = ap.parse_args(argv)
    if args.delay > 0:
        time.sleep(args.delay)
    got = purge(args.url)
    return 1 if got is False else 0


if __name__ == "__main__":
    sys.exit(main())
