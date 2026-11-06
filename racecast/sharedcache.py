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


def get(key, compute, ttl, keep=lambda v: True, now=time.time, path=None):
    """(value, computed_at) for `key`: the shared copy when it is younger
    than `ttl` seconds, else compute() -- once across the workers -- and
    share it when keep(value)."""
    d = _safeDir(path)
    if d is None:
        return compute(), now()
    base = os.path.join(d, _name(key))
    pkl = base + ".pkl"
    if _fresh(pkl, ttl, now()):
        try:
            return _load(pkl), os.stat(pkl).st_mtime
        except Exception:                               # noqa: BLE001
            pass
    with open(base + ".lock", "a+") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            # another worker may have filled it while this one waited
            if _fresh(pkl, ttl, now()):
                try:
                    return _load(pkl), os.stat(pkl).st_mtime
                except Exception:                       # noqa: BLE001
                    pass
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
