"""
rotate.py -- a share of a source's index pages re-read each night, the rest
from the store (owner, 2026-11-10: "fix the probing": MileSplit's 51 state
lists, each with its own robots.txt, and Arrow's 30 were re-read every
night whatever had changed).

    fresh = due(store, urls, share=3)   # the urls to fetch from the site now

A page never stored is always due; of the rest, the oldest 1/share are, so
every page is re-read every `share` nights and each night asks for about the
same number. XCP_INDEX_ROTATE sets share (1 = every page every night).
"""
import math
import os


def share():
    try:
        return max(1, int(os.environ.get("XCP_INDEX_ROTATE") or 3))
    except ValueError:
        return 3


def due(store, urls, n=None):
    """The set of `urls` to fetch from the site this run."""
    n = share() if n is None else n
    urls = list(urls)
    if n <= 1 or store is None:
        return set(urls)
    seen, out = [], set()
    for u in urls:
        m = store.meta(u)
        if not m or m.get("status") != 200:
            out.add(u)
        else:
            seen.append((m.get("fetched_at") or 0, u))
    seen.sort()
    out.update(u for _t, u in seen[:math.ceil(len(seen) / n)])
    return out


# a stored copy used as is, however old (the page is not due this run)
FOREVER = 10 * 365 * 86400
