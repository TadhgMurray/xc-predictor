#!/usr/bin/env python3
"""
edge_snapshot.py -- publish the most-read board and search answers to
Cloudflare Workers KV, where deploy/cloudflare/worker.js answers them at the
edge without the origin (prototype, 2026-11-08; deploy/cloudflare/README.md).

    # what share of the origin's API traffic the snapshot would answer
    python scripts/edge_snapshot.py --estimate /var/log/nginx/access.log
    python scripts/edge_snapshot.py --estimate today.log --train yesterday.log --top 300

    # look before publishing: fetch the answers into a folder, no upload
    python scripts/edge_snapshot.py --from-log /var/log/nginx/access.log.1 --top 200 --dry-run /tmp/snap

    # publish (after a go-live, once the workers' board copies have expired:
    # the pipeline's delayed second purge, edge_purge.py --delay 960)
    python scripts/edge_snapshot.py --from-log /var/log/nginx/access.log.1 --top 200

    # roll back: the Worker stops answering from KV at once (within 60 s)
    python scripts/edge_snapshot.py --off

★ THE BYTES ARE THE SITE'S OWN. Every value is the live app's response to
  that URL, fetched from the origin (gunicorn on 127.0.0.1:8000 by default,
  so neither Cloudflare nor the Worker is in the way) -- feed scrub, opt-out
  masking and HS stamps included, because the app applied them. Only a 200
  JSON answer the app itself marked public is stored.

★ WHICH URLS: the ones people ask for. --from-log ranks the board/search
  requests that reached the origin by their canonical spelling
  (edge_cache.canonicalUrl -- the Worker's key), and --urls adds a list.
  Guessing the board page's defaults here would drift from rankings.js; the
  log cannot.

! A NO-OP WITHOUT CREDENTIALS, like edge_purge.py: XCP_CF_ACCOUNT_ID,
  XCP_CF_KV_NAMESPACE and XCP_CF_KV_TOKEN (Account > Workers KV Storage >
  Edit) come from /etc/xc-predictor.env.

! THE FREE TIER COUNTS WRITES: 1,000 a day. One publish is one write per
  URL plus one for the pointer, so --top 200 after the pipeline and again
  after the nightly run is ~400 a day. Search prefixes (--search) add 676.
"""
import argparse
import collections
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "racecast"))
import edge_cache as EC                                   # noqa: E402

# the Worker's routes (deploy/cloudflare/wrangler.toml)
ROUTES = ("/api/rankings", "/api/teams", "/api/courses", "/search/api")
API = "https://api.cloudflare.com/client/v4/accounts/{acct}/storage/kv/namespaces/{ns}"
# ! the site's own contact, never a person's (CLAUDE.md)
UA = "racecast-edge-snapshot (racecast@racecast.co)"
KEEP_SECONDS = 3 * 86400          # an old generation's keys expire on their own
MAX_VALUE = 2 * 1024 * 1024       # a whole-board CSV is not a snapshot
_BULK = 5000                      # pairs per bulk call (Cloudflare's cap: 10,000)
_LOG = re.compile(r'"([A-Z]+) (\S+) HTTP/[\d.]+" (\d{3})')


def onRoute(path):
    p = path.split("?", 1)[0]
    return any(p == r or p.startswith(r + "/") for r in ROUTES)


def canonicalKey(target):
    """'/api/rankings?pool=hs_m&board=ability&offset=0' -> the Worker's key."""
    path, _sep, qs = target.partition("?")
    return EC.canonicalUrl(path, qs)


def parseLogLine(line):
    """(method, target, status) from an nginx combined-format line, or None."""
    m = _LOG.search(line)
    return (m.group(1), m.group(2), int(m.group(3))) if m else None


def routeRequests(lines):
    """The canonical keys of the GETs on the Worker's routes, one per request."""
    out = []
    for line in lines:
        got = parseLogLine(line)
        if got and got[0] == "GET" and onRoute(got[1]):
            out.append(canonicalKey(got[1]))
    return out


def topKeys(keys, n):
    return [k for k, _c in collections.Counter(keys).most_common(n)]


def searchPrefixes():
    """Every two-letter query the topbar box sends first (/search/api?q=xx)."""
    letters = "abcdefghijklmnopqrstuvwxyz"
    return [f"/search/api?q={a}{b}" for a in letters for b in letters]


def estimate(test_lines, snapshot_keys, total_lines=None):
    """What the Worker would have answered of a log's origin traffic."""
    total = sum(1 for ln in (total_lines if total_lines is not None else test_lines)
                if parseLogLine(ln))
    reqs = routeRequests(test_lines)
    keys = set(snapshot_keys)
    parsed = [parseLogLine(ln) for ln in test_lines]
    raw_targets = [p[1] for p in parsed if p and p[0] == "GET" and onRoute(p[1])]
    respelled = sum(1 for t, k in zip(raw_targets, reqs) if t != k)
    hit = sum(1 for k in reqs if k in keys)
    distinct = len(set(reqs))
    return {"origin_requests": total, "route_requests": len(reqs),
            "route_share": (len(reqs) / total) if total else 0.0,
            "snapshot_answers": hit,
            "snapshot_share_of_routes": (hit / len(reqs)) if reqs else 0.0,
            "snapshot_share_of_origin": (hit / total) if total else 0.0,
            "respelled_by_canonical": respelled,
            "distinct_route_urls": distinct,
            "distinct_raw_urls": len(set(raw_targets)),
            "snapshot_keys": len(keys)}


def fetchOne(base, host, path, opener=urllib.request.urlopen):
    """(body bytes, content type, cache-control) for a storable answer, or
    None with the reason printed."""
    req = urllib.request.Request(base.rstrip("/") + path, headers={
        "Host": host, "X-Forwarded-Proto": "https", "Accept-Encoding": "identity",
        "X-Racecast-Snapshot": "bypass", "User-Agent": UA})
    try:
        with opener(req, timeout=60) as resp:
            body = resp.read()
            status = resp.status
            ct = resp.headers.get("Content-Type", "")
            cc = resp.headers.get("Cache-Control", "")
    except urllib.error.HTTPError as exc:
        print(f"  skip {path}: HTTP {exc.code}")
        return None
    except Exception as exc:                            # noqa: BLE001
        print(f"  skip {path}: {type(exc).__name__}: {exc}")
        return None
    if status != 200 or "json" not in ct or "public" not in cc or "no-store" in cc:
        print(f"  skip {path}: {status} {ct!r} {cc!r}")
        return None
    if len(body) > MAX_VALUE:
        print(f"  skip {path}: {len(body)} bytes")
        return None
    return body, ct, cc


def bulkBodies(gen, items):
    """KV bulk-write payloads for one generation: items = [(key, body, ct, cc)]."""
    pairs = [{"key": f"g{gen}:{k}", "value": body.decode("utf-8"),
              "expiration_ttl": KEEP_SECONDS,
              "metadata": {"ct": ct, "cc": cc, "gen": str(gen)}}
             for k, body, ct, cc in items if len(f"g{gen}:{k}".encode()) <= 512]
    return [pairs[i:i + _BULK] for i in range(0, len(pairs), _BULK)]


def _credentials(env=os.environ):
    vals = tuple((env.get(k) or "").strip() for k in
                 ("XCP_CF_ACCOUNT_ID", "XCP_CF_KV_NAMESPACE", "XCP_CF_KV_TOKEN"))
    return vals if all(vals) else None


def _call(method, url, token, data, ctype, opener=urllib.request.urlopen):
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}", "Content-Type": ctype, "User-Agent": UA})
    try:
        with opener(req, timeout=60) as resp:
            got = json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        got = {"success": False, "errors": [f"HTTP {exc.code}"]}
    except Exception as exc:                            # noqa: BLE001
        got = {"success": False, "errors": [f"{type(exc).__name__}: {exc}"]}
    return bool(got.get("success")), got.get("errors")


def setPointer(value, env=os.environ, opener=urllib.request.urlopen):
    creds = _credentials(env)
    if not creds:
        print("edge snapshot: XCP_CF_ACCOUNT_ID / XCP_CF_KV_NAMESPACE / XCP_CF_KV_TOKEN not set -- skipped")
        return None
    acct, ns, token = creds
    ok, err = _call("PUT", API.format(acct=acct, ns=ns) + "/values/current", token,
                    str(value).encode(), "text/plain", opener)
    print(f"edge snapshot: current = {value} -- {'ok' if ok else 'FAILED ' + str(err)}")
    return ok


def publish(items, env=os.environ, opener=urllib.request.urlopen, gen=None):
    creds = _credentials(env)
    if not creds:
        print("edge snapshot: XCP_CF_ACCOUNT_ID / XCP_CF_KV_NAMESPACE / XCP_CF_KV_TOKEN not set -- skipped")
        return None
    acct, ns, token = creds
    gen = gen or int(time.time())
    for body in bulkBodies(gen, items):
        ok, err = _call("PUT", API.format(acct=acct, ns=ns) + "/bulk", token,
                        json.dumps(body).encode(), "application/json", opener)
        if not ok:
            print(f"edge snapshot: bulk write FAILED {err} -- pointer left as it was")
            return False
    # ! THE POINTER LAST: until it flips, readers keep the previous generation
    return setPointer(gen, env, opener)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from-log", action="append", default=[],
                    help="nginx access log(s) to rank the URLs from")
    ap.add_argument("--top", type=int, default=200, help="how many URLs (default 200)")
    ap.add_argument("--urls", help="a file of extra paths, one per line")
    ap.add_argument("--search", action="store_true",
                    help="also the 676 two-letter search prefixes")
    ap.add_argument("--base", default=os.environ.get("XCP_SNAPSHOT_BASE", "http://127.0.0.1:8000"))
    ap.add_argument("--host", default="racecast.co")
    ap.add_argument("--dry-run", metavar="DIR", help="write the answers here; upload nothing")
    ap.add_argument("--estimate", metavar="LOG", help="report coverage of this log and exit")
    ap.add_argument("--train", metavar="LOG",
                    help="with --estimate: pick the URLs from this log (default: the same log)")
    ap.add_argument("--off", action="store_true", help="stop the Worker answering from KV")
    ap.add_argument("--delay", type=int, default=0, help="seconds to wait first")
    args = ap.parse_args(argv)

    if args.off:
        return 0 if setPointer("off") is not False else 1

    if args.estimate:
        test = open(args.estimate, encoding="utf-8", errors="replace").read().splitlines()
        train = (open(args.train, encoding="utf-8", errors="replace").read().splitlines()
                 if args.train else test)
        keys = topKeys(routeRequests(train), args.top)
        if args.search:
            keys += [canonicalKey(p) for p in searchPrefixes()]
        print(json.dumps(estimate(test, keys), indent=2))
        return 0

    if args.delay > 0:
        time.sleep(args.delay)
    keys = []
    for log in args.from_log:
        lines = open(log, encoding="utf-8", errors="replace").read().splitlines()
        keys += routeRequests(lines)
    paths = topKeys(keys, args.top)
    if args.urls:
        paths += [canonicalKey(ln.strip()) for ln in open(args.urls, encoding="utf-8")
                  if ln.strip() and not ln.startswith("#")]
    if args.search:
        paths += [canonicalKey(p) for p in searchPrefixes()]
    paths = list(dict.fromkeys(p for p in paths if onRoute(p)))
    if not paths:
        print("edge snapshot: no URLs (give --from-log, --urls or --search)")
        return 1
    print(f"edge snapshot: fetching {len(paths)} answers from {args.base}")
    items = []
    for p in paths:
        got = fetchOne(args.base, args.host, p)
        if got:
            items.append((p,) + got)
        time.sleep(0.05)                 # a trickle, not a burst, on the box
    print(f"edge snapshot: {len(items)} storable of {len(paths)}")
    if args.dry_run:
        os.makedirs(args.dry_run, exist_ok=True)
        index = []
        for i, (k, body, ct, cc) in enumerate(items):
            fn = os.path.join(args.dry_run, f"{i:05d}.json")
            with open(fn, "wb") as fh:
                fh.write(body)
            index.append({"key": k, "file": os.path.basename(fn), "ct": ct, "cc": cc,
                          "bytes": len(body)})
        with open(os.path.join(args.dry_run, "index.json"), "w") as fh:
            json.dump(index, fh, indent=1)
        print(f"edge snapshot: dry run, {len(items)} files in {args.dry_run}")
        return 0
    got = publish(items)
    return 1 if got is False else 0


if __name__ == "__main__":
    sys.exit(main())
