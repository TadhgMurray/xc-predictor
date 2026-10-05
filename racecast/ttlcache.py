# Project: xc-predictor / racecast
# File:    ttlcache.py
# Purpose: a small per-process cache with a time-to-live, for pages that are
#          computed on request rather than by the pipeline (/projections,
#          /breakouts -- owner, 2026-10-04).
#
# ★ WHY NOT THE PIPELINE. The ideal home for both pages is a precomputed
#   table, built after step 10 like homepage_recent is. That is a pipeline
#   change, and these pages shipped without one: the answer is computed the
#   first time somebody asks and held for the TTL. The data under it moves
#   when a pipeline run goes live, hours apart, so six hours is about how
#   stale a page may be just after a go-live -- the same reasoning as
#   PAGE_S_MAXAGE, one layer down.
#
# ★ ONE COMPUTE PER KEY AT A TIME. Two requests for the same cold key used to
#   be the shape of every slow page on this site: both run the query, both
#   wait on each other's I/O, and the second is no faster for the first having
#   started. A lock per key makes the second wait for the first and read its
#   answer.
#
# ! PER PROCESS, ON PURPOSE. Each gunicorn worker holds its own copy; with the
#   edge cache in front (public, s-maxage) a worker sees a fraction of the
#   views anyway. A shared table would be one more thing the pipeline swap
#   has to know about.
#
# ! A FAILURE IS NOT CACHED. A compute that raises leaves the key empty, so a
#   database blip costs one request rather than six hours of an error page.
import threading
import time

_DEFAULT_TTL = 6 * 3600.0
# ⚠ BOUNDED. 51 states x a handful of divisions x two genders is a few
#   hundred keys; the cap is there so a crawler walking every slug it can
#   invent cannot grow a worker without limit.
_MAX_KEYS = 2000

_store = {}               # key -> (stamp, value, ttl)
_locks = {}               # key -> Lock
_guard = threading.Lock()


def _lockFor(key):
    with _guard:
        lk = _locks.get(key)
        if lk is None:
            lk = _locks[key] = threading.Lock()
        return lk


def get(key, compute, ttl=_DEFAULT_TTL, now=time.time, ttl_of=None):
    """The cached value for `key`, computing it with `compute()` when it is
    missing or older than its ttl. Returns (value, stamp) -- the stamp is
    when the value was computed, which the pages print as "computed at".

    ttl_of(value), when given, picks the ttl for a freshly computed value: a
    page that half-worked (one of two queries timed out) is kept minutes,
    not hours, so the next reader gets another try."""
    def fresh(hit):
        return hit and now() - hit[0] <= hit[2]

    hit = _store.get(key)
    if fresh(hit):
        return hit[1], hit[0]
    with _lockFor(key):
        # somebody else may have filled it while we waited for the lock
        hit = _store.get(key)
        if fresh(hit):
            return hit[1], hit[0]
        value = compute()
        stamp = now()
        keep = ttl_of(value) if ttl_of else ttl
        with _guard:
            if len(_store) >= _MAX_KEYS:
                # ! THE EXPIRED GO FIRST (2026-10-05). The predictions page's
                #   squads live here too now, five minutes each and many per
                #   visit; "oldest first" alone would throw out a six-hour
                #   /projections page computed this morning to make room
                #   for them, while their own dead entries sat there.
                t = now()
                for k in [k for k, v in _store.items() if t - v[0] > v[2]]:
                    _store.pop(k, None)
            if len(_store) >= _MAX_KEYS:
                # drop the oldest tenth; cheaper than an LRU and the keys are
                # all the same size of problem
                for k, _v in sorted(_store.items(),
                                    key=lambda kv: kv[1][0])[:_MAX_KEYS // 10]:
                    _store.pop(k, None)
            _store[key] = (stamp, value, keep)
        return value, stamp


def peek(key, now=time.time):
    """The cached value for `key` while it is fresh, else None -- never
    computes. For a caller that batches its misses (the predictions page's
    squads, 2026-10-05): it asks which keys are held, computes the rest in
    one go, and files each with get(key, lambda: value)."""
    hit = _store.get(key)
    if hit and now() - hit[0] <= hit[2]:
        return hit[1]
    return None


def clear():
    """Forget everything (tests)."""
    with _guard:
        _store.clear()
