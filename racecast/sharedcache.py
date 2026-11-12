# Project: xc-predictor / racecast
# File:    sharedcache.py
# Purpose: one computed value shared by every gunicorn worker on the box, so a
#          page's expensive answer is computed once per TTL, not once per
#          worker per TTL.
#
# ★ WHY (2026-11-06: the site's slow log for a week). "What it takes"
#   (cuts.py) fills each state's marks in a background thread per worker
#   (ttlcache, 6 h). With 24 workers that is every state computed 24 times
#   every 6 hours: the two statements behind it ran 39,038 and 46,891 times
#   over 250 ms in a week -- ~55,000 s of database time, outside any request,
#   each holding one of the worker's three connections. The answer is the
#   same for every worker.
#
# ★ A FILE PER KEY, A LOCK PER KEY. The first worker to need a key takes an
#   exclusive flock on <key>.lock, computes, writes <key>.pkl atomically
#   (tmp + rename) and releases; a worker that arrives meanwhile waits on the
#   lock and then reads the file instead of computing again. The file's
#   mtime is when it was computed; past `ttl` it is computed again.
#
# ⚠ PICKLE, SO THE DIRECTORY MUST BE OURS. A pickle runs code when loaded,
#   so a file someone else could have written is never read: the directory
#   is created 0700, and it is used only while it is owned by this process's
#   user and writable by nobody else. Otherwise every call just computes, as
#   if this module did not exist.
#
# ! A FAILURE IS NOT SHARED. A value `keep` rejects (an error page) is
#   returned to the caller but not written, as ttlcache does not keep one.
import fcntl
import os
import pickle
import re
import stat
import tempfile
import time

DIR = os.environ.get("XCP_SHARED_CACHE_DIR") or os.path.join(
    tempfile.gettempdir(), f"racecast-shared-cache-{os.getuid()}")


def _safeDir(path=None):
    """The cache directory when it is safe to use (see above), else None."""
    path = path or DIR
    try:
        os.makedirs(path, mode=0o700, exist_ok=True)
        st = os.stat(path)
    except OSError:
        return None
    if st.st_uid != os.getuid() or (st.st_mode & (stat.S_IWGRP | stat.S_IWOTH)):
        return None
    return path


def _name(key):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(key))[:120]


def _fresh(path, ttl, now):
    try:
        return now - os.stat(path).st_mtime <= ttl
    except OSError:
        return False


def _load(path):
    with open(path, "rb") as fh:
        return pickle.load(fh)


def _usable(pkl, ttl, ttl_of, now):
    """(value, mtime) of a shared copy still inside its ttl, else None.
    ttl_of(value), when given, is that value's own ttl (at most `ttl`): a
    failure kept minutes beside successes kept hours."""
    if not _fresh(pkl, ttl, now):
        return None
    try:
        value, at = _load(pkl), os.stat(pkl).st_mtime
    except Exception:                                   # noqa: BLE001
        return None
    if ttl_of is not None and now - at > ttl_of(value):
        return None
    return value, at


# ★ THE WAIT IS BOUNDED (2026-11-07, "the connection thing ... again"). A
#   worker that finds another one computing waited on the lock for as long
#   as that took -- with no limit, and holding its database connection the
#   whole time. One slow compute (a query stuck behind a pipeline swap's
#   lock) then held a connection in every worker. Past LOCK_WAIT seconds a
#   worker stops waiting and computes for itself, as before this module.
LOCK_WAIT = float(os.environ.get("XCP_SHARED_LOCK_WAIT") or 15)


def _lockWithin(fh, seconds):
    """flock(LOCK_EX) on fh, giving up after `seconds`; True when held."""
    end = time.monotonic() + seconds
    pause = 0.05
    while True:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            if time.monotonic() >= end:
                return False
            time.sleep(pause)
            pause = min(pause * 2, 0.5)


def get(key, compute, ttl, keep=lambda v: True, now=time.time, path=None, ttl_of=None):
    """(value, computed_at) for `key`: the shared copy when it is younger
    than `ttl` seconds (or ttl_of(value)), else compute() -- once across the
    workers -- and share it when keep(value)."""
    d = _safeDir(path)
    if d is None:
        return compute(), now()
    _sweepMany(d, now())
    base = os.path.join(d, _name(key))
    pkl = base + ".pkl"
    hit = _usable(pkl, ttl, ttl_of, now())
    if hit is not None:
        return hit
    with open(base + ".lock", "a+") as lk:
        if not _lockWithin(lk, LOCK_WAIT):
            # another worker is still at it: answer this request ourselves
            # (not shared -- the one holding the lock writes the copy)
            hit = _usable(pkl, ttl, ttl_of, now())
            return hit if hit is not None else (compute(), now())
        try:
            # another worker may have filled it while this one waited
            hit = _usable(pkl, ttl, ttl_of, now())
            if hit is not None:
                return hit
            value = compute()
            at = now()
            if keep(value):
                tmp = f"{pkl}.{os.getpid()}.tmp"
                try:
                    with open(tmp, "wb") as fh:
                        pickle.dump(value, fh, protocol=pickle.HIGHEST_PROTOCOL)
                    os.utime(tmp, (at, at))
                    os.replace(tmp, pkl)
                except Exception:                       # noqa: BLE001
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
            return value, at
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)


# ★ MANY VALUES UNDER ONE KEY (2026-11-07, the predictions page): a field's
#   per-runner answers -- rating rows, model times -- are asked by one
#   request for some runners and by the next for a subset or a superset, so
#   one value per key does not fit. A bucket is one file holding
#   {key: (computed_at, value)}; peekMany reads the fresh ones, the caller
#   computes the rest in one go and files them with putMany, merged with
#   what another worker filed meanwhile under the bucket's lock.
# ! THE SAME RULES AS get(): our own 0700 directory or nothing (then peekMany
#   finds nothing and putMany keeps nothing), an unreadable file is a miss.
# ! THE FILE NAME IS A DIGEST of the bucket, not _name(): a bucket is a long
#   tuple, and _name's 120 characters would let two of them share a file.
def _bucketBase(d, bucket):
    import hashlib
    return os.path.join(d, "many-" + hashlib.sha1(
        repr(bucket).encode("utf-8")).hexdigest())


def _bucketLoad(pkl):
    try:
        data = _load(pkl)
    except Exception:                                   # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def peekMany(bucket, keys, ttl, now=time.time, path=None):
    """{key: (value, computed_at)} for those of `keys` the bucket holds
    younger than `ttl` seconds. Never computes."""
    d = _safeDir(path)
    if d is None:
        return {}
    data = _bucketLoad(_bucketBase(d, bucket) + ".pkl")
    t = now()
    out = {}
    for k in keys:
        hit = data.get(k)
        if (isinstance(hit, tuple) and len(hit) == 2
                and t - hit[0] <= ttl):
            out[k] = (hit[1], hit[0])
    return out


# ! A BUCKET IS BOUNDED, AND A DEAD ONE IS SWEPT (2026-11-07). One rating
#   bucket is every runner of every race on a date, and each put rewrites
#   the whole file and each peek reads it: past MANY_MAX entries the oldest
#   are dropped (a dropped runner is only read again). And buckets are named
#   by the day and the race, so a file nobody puts to again would sit in the
#   directory forever: at most every SWEEP_EVERY seconds a put removes the
#   many-* files untouched for SWEEP_AGE seconds (a worker still holding a
#   swept lock at worst loses one merge -- the value is computed again).
MANY_MAX = int(os.environ.get("XCP_SHARED_MANY_MAX") or 3000)
SWEEP_EVERY = 600.0
SWEEP_AGE = 3600.0
_last_sweep = [0.0]


# ! AND EVERY OTHER FILE A DAY AFTER ITS LAST WRITE (2026-11-08). The
#   directory now lives where a reboot does not empty it (/var/cache), and a
#   key per distinct short search is a file per query: past SWEEP_AGE_ANY
#   (no ttl here is longer than six hours) a file is dead and goes.
SWEEP_AGE_ANY = 86400.0


def _sweepMany(d, t):
    if t - _last_sweep[0] < SWEEP_EVERY:
        return
    _last_sweep[0] = t
    try:
        names = os.listdir(d)
    except OSError:
        return
    for n in names:
        age = SWEEP_AGE if n.startswith("many-") else SWEEP_AGE_ANY
        p = os.path.join(d, n)
        try:
            if t - os.stat(p).st_mtime > age:
                os.unlink(p)
        except OSError:
            pass


_SAID = {}


def putMany(bucket, values, ttl, now=time.time, path=None, max_entries=None):
    """File {key: value} into the bucket, computed now, beside the entries
    already there that are younger than `ttl` (older ones are dropped), the
    newest `max_entries` (MANY_MAX) kept."""
    d = _safeDir(path)
    if d is None or not values:
        return
    cap = max_entries or MANY_MAX
    base = _bucketBase(d, bucket)
    pkl = base + ".pkl"
    _sweepMany(d, now())
    with open(base + ".lock", "a+") as lk:
        if not _lockWithin(lk, 2.0):
            return                   # a share skipped, never a request held
        try:
            t = now()
            data = {k: v for k, v in _bucketLoad(pkl).items()
                    if isinstance(v, tuple) and len(v) == 2 and t - v[0] <= ttl}
            for k in values:
                data.pop(k, None)          # re-inserted last: newest
            for k, v in values.items():
                data[k] = (t, v)
            if len(data) > cap:
                # dicts keep insertion order and old entries came in oldest
                # first, so the first ones are the oldest
                for k in list(data)[:len(data) - cap]:
                    del data[k]
            tmp = f"{pkl}.{os.getpid()}.tmp"
            try:
                with open(tmp, "wb") as fh:
                    pickle.dump(data, fh, protocol=pickle.HIGHEST_PROTOCOL)
                os.replace(tmp, pkl)
            except Exception as exc:                    # noqa: BLE001
                # ! SAID ONCE, NOT SWALLOWED (2026-11-12: a bucket that never
                #   stores looks exactly like a cold one from outside)
                if not _SAID.get("putMany"):
                    _SAID["putMany"] = True
                    print(f"[sharedcache] putMany {str(bucket)[:60]}: not stored "
                          f"({type(exc).__name__}: {exc})", flush=True)
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)


# ★ A TABLE'S IDENTITY, FOR A KEY (2026-11-09). The boards tables are rebuilt
#   as a shadow and swapped in, so a new build is a new oid: a key carrying
#   the oid never serves a copy read from the table before. Read at most
#   once a minute per process per table, under a savepoint so a failure
#   leaves the caller's transaction as it was; None when it cannot be read
#   (then the caller computes, as if this module did not exist).
_OIDS = {}


def tableOid(cur, table, now=time.time):
    t = now()
    hit = _OIDS.get(table)
    if hit is not None and t - hit[0] < 60:
        return hit[1]
    oid = None
    try:
        with cur.connection.cursor() as c:
            c.execute("SAVEPOINT shared_oid")
            try:
                c.execute("SELECT to_regclass(%s)::oid", (table,))
                row = c.fetchone()
                oid = int(row[0]) if row and row[0] is not None else None
                c.execute("RELEASE SAVEPOINT shared_oid")
            except Exception:                           # noqa: BLE001
                c.execute("ROLLBACK TO SAVEPOINT shared_oid")
                oid = None
    except Exception:                                   # noqa: BLE001
        oid = None
    _OIDS[table] = (t, oid)
    return oid
