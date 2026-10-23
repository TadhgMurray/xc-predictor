# Project: xc-predictor
# File:    sources/fetch.py
# Purpose: The polite, parallel fetcher every results source shares: many
#          hosts at once, each host slow. Throughput comes from BREADTH --
#          the number of timing companies in flight -- never from asking one
#          site faster.
#
# ★ WHY (owner, 2026-10-23: "I'd prefer a distributed system so we can spread
#   load so we don't get banned ... the main thing is reducing load/speed-up
#   by parallelizing"). athletic.net closed its whole-meet results call; the
#   replacement is hundreds of timing companies' own sites
#   (docs/TIMING-COMPANIES.md). Each is a small business on a small server:
#   one request at a time per host, seconds apart, and a page fetched once is
#   kept (RawStore) so a parser change never costs a second visit.
#
# THE PIECES
#   HostLimiter   one request in flight per host, at least `interval` seconds
#                 between a host's requests (jittered); hosts run in parallel
#   Robots        robots.txt per host, honoured, its Crawl-delay raises the
#                 host's interval; an unreachable robots.txt allows (the
#                 RFC 9309 rule for 4xx), a 5xx disallows for the run
#   RawStore      every fetched body, gzipped on disk by content hash, with a
#                 sqlite index (url -> status, etag, last-modified, sha); a
#                 refetch sends If-None-Match / If-Modified-Since, a 304 costs
#                 the host almost nothing
#   Fetcher       the three together; fetchAll(urls) runs them with a global
#                 cap on requests in flight across all hosts
#
# ! PLAIN HTTP, NO BROWSER. Timer sites are static Hy-Tek / MeetPro pages and
#   PDFs. athletic.net and TFRRS keep their own Chrome launchers.
# ! AN HONEST USER AGENT with a contact URL: a timer who wants us to slow down
#   or stop can say so, and sources.registry can switch them off.

import asyncio
import gzip
import hashlib
import os
import random
import sqlite3
import time
import urllib.parse
import urllib.robotparser
from dataclasses import dataclass

import requests

USER_AGENT = ("racecast-results/1.0 (+https://racecast.co/about#results; "
              "results for high school runners; contact via the site)")

DEFAULT_INTERVAL = float(os.environ.get("XCP_SOURCE_INTERVAL", "6"))   # s per host
JITTER = 0.3                                                       # +-30%
TIMEOUT = 30
RAW_DIR = os.environ.get("XCP_RAW_DIR", "/srv/raw")


def hostOf(url):
    return urllib.parse.urlsplit(url).netloc.lower()


# ------------------------------------------------------------------ #
# HostLimiter
# ------------------------------------------------------------------ #

class HostLimiter:
    """One request in flight per host, `interval` seconds apart (jittered).
    Hosts are independent: ten hosts make ten requests at once."""

    def __init__(self, interval=DEFAULT_INTERVAL, clock=time.monotonic,
                 sleep=asyncio.sleep, rand=random.random):
        self.interval = interval
        self.per_host = {}           # host -> its own interval (Crawl-delay, registry)
        self._locks = {}
        self._next = {}
        self._clock, self._sleep, self._rand = clock, sleep, rand

    def setInterval(self, host, seconds):
        self.per_host[host] = max(seconds, self.per_host.get(host, 0.0))

    def intervalFor(self, host):
        return max(self.interval, self.per_host.get(host, 0.0))

    async def acquire(self, host):
        lock = self._locks.setdefault(host, asyncio.Lock())
        await lock.acquire()
        wait = self._next.get(host, 0.0) - self._clock()
        if wait > 0:
            await self._sleep(wait)
        return lock

    def release(self, host, lock):
        gap = self.intervalFor(host) * (1 + JITTER * (2 * self._rand() - 1))
        self._next[host] = self._clock() + gap
        lock.release()


# ------------------------------------------------------------------ #
# Robots
# ------------------------------------------------------------------ #

class Robots:
    """robots.txt per host, fetched once per run. get(url) -> (allowed,
    crawl_delay or None)."""

    def __init__(self, getter=None):
        self._getter = getter or (lambda u: requests.get(
            u, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT))
        self._cache = {}

    def _load(self, scheme, host):
        url = f"{scheme}://{host}/robots.txt"
        try:
            r = self._getter(url)
            status, text = r.status_code, r.text
        except Exception:                                   # noqa: BLE001
            return None, False          # unreachable host: skip it this run
        if status >= 500:
            return None, False
        if status >= 400:
            return None, True           # no robots.txt: allowed
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(text.splitlines())
        return rp, True

    def get(self, url):
        parts = urllib.parse.urlsplit(url)
        host = parts.netloc.lower()
        if host not in self._cache:
            self._cache[host] = self._load(parts.scheme or "https", host)
        rp, ok = self._cache[host]
        if rp is None:
            return ok, None
        return rp.can_fetch(USER_AGENT, url), rp.crawl_delay(USER_AGENT)


# ------------------------------------------------------------------ #
# RawStore
# ------------------------------------------------------------------ #

class RawStore:
    """Every fetched body, kept: <dir>/<host>/<sha256>.gz plus a sqlite index.
    A parser change re-reads from here, never from the timer's server."""

    def __init__(self, root=RAW_DIR):
        self.root = root
        os.makedirs(root, exist_ok=True)
        self.db = sqlite3.connect(os.path.join(root, "index.sqlite"))
        self.db.execute("""CREATE TABLE IF NOT EXISTS raw_doc (
            url TEXT PRIMARY KEY, host TEXT, status INTEGER, fetched_at REAL,
            etag TEXT, last_modified TEXT, sha TEXT, bytes INTEGER,
            content_type TEXT)""")
        self.db.commit()

    def meta(self, url):
        row = self.db.execute("SELECT status, etag, last_modified, sha, fetched_at, "
                              "content_type FROM raw_doc WHERE url = ?", (url,)).fetchone()
        if not row:
            return None
        return dict(zip(("status", "etag", "last_modified", "sha", "fetched_at",
                         "content_type"), row))

    def _path(self, host, sha):
        return os.path.join(self.root, host.replace(":", "_"), sha + ".gz")

    def put(self, url, status, body, etag=None, last_modified=None, content_type=None):
        host = hostOf(url)
        sha = hashlib.sha256(body).hexdigest() if body else None
        if body:
            path = self._path(host, sha)
            if not os.path.exists(path):
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with gzip.open(path, "wb") as f:
                    f.write(body)
        self.db.execute("""INSERT INTO raw_doc VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(url) DO UPDATE SET status=excluded.status,
                fetched_at=excluded.fetched_at, etag=excluded.etag,
                last_modified=excluded.last_modified, sha=excluded.sha,
                bytes=excluded.bytes, content_type=excluded.content_type""",
                        (url, host, status, time.time(), etag, last_modified, sha,
                         len(body or b""), content_type))
        self.db.commit()
        return sha

    def touch(self, url):
        self.db.execute("UPDATE raw_doc SET fetched_at = ? WHERE url = ?",
                        (time.time(), url))
        self.db.commit()

    def body(self, url):
        m = self.meta(url)
        if not m or not m["sha"]:
            return None
        with gzip.open(self._path(hostOf(url), m["sha"]), "rb") as f:
            return f.read()


# ------------------------------------------------------------------ #
# Fetcher
# ------------------------------------------------------------------ #

@dataclass
class Fetched:
    url: str
    status: int            # HTTP status; 304 = unchanged (body from the store);
                           # 0 = not fetched (robots, error, fresh enough)
    body: bytes = None
    changed: bool = False
    note: str = ""


class Fetcher:
    """fetch(url) politely; fetchAll(urls) in parallel across hosts."""

    def __init__(self, store, limiter=None, robots=None, getter=None,
                 max_in_flight=int(os.environ.get("XCP_SOURCE_IN_FLIGHT", "16"))):
        self.store = store
        self.limiter = limiter or HostLimiter()
        self.robots = robots or Robots()
        self._getter = getter or (lambda url, headers: requests.get(
            url, headers=headers, timeout=TIMEOUT))
        self._sem = asyncio.Semaphore(max_in_flight)
        self.stats = {"fetched": 0, "unchanged": 0, "skipped": 0, "errors": 0}

    async def fetch(self, url, max_age=None):
        """max_age: seconds; a stored copy younger than this is used as is
        (a finished meet's results page never needs a second visit)."""
        m = self.store.meta(url)
        if m and max_age is not None and m["status"] == 200 \
                and time.time() - m["fetched_at"] < max_age:
            self.stats["skipped"] += 1
            return Fetched(url, 0, self.store.body(url), False, "fresh in store")
        allowed, delay = self.robots.get(url)
        if not allowed:
            self.stats["skipped"] += 1
            return Fetched(url, 0, None, False, "robots.txt disallows")
        host = hostOf(url)
        if delay:
            self.limiter.setInterval(host, float(delay))
        headers = {"User-Agent": USER_AGENT}
        if m and m.get("etag"):
            headers["If-None-Match"] = m["etag"]
        if m and m.get("last_modified"):
            headers["If-Modified-Since"] = m["last_modified"]
        async with self._sem:
            lock = await self.limiter.acquire(host)
            try:
                r = await asyncio.to_thread(self._getter, url, headers)
            except Exception as exc:                        # noqa: BLE001
                self.stats["errors"] += 1
                return Fetched(url, 0, None, False, f"{type(exc).__name__}: {exc}")
            finally:
                self.limiter.release(host, lock)
        if r.status_code == 304:
            self.store.touch(url)
            self.stats["unchanged"] += 1
            return Fetched(url, 304, self.store.body(url), False, "unchanged")
        if r.status_code == 429 or r.status_code == 503:
            # the host asks us to back off: double its interval for the run
            self.limiter.setInterval(host, self.limiter.intervalFor(host) * 2)
        body = r.content if r.status_code == 200 else b""
        old = m["sha"] if m else None
        sha = self.store.put(url, r.status_code, body, r.headers.get("ETag"),
                             r.headers.get("Last-Modified"), r.headers.get("Content-Type"))
        self.stats["fetched"] += 1
        return Fetched(url, r.status_code, body or None,
                       bool(body) and sha != old, "")

    async def fetchAll(self, urls, max_age=None):
        """Every url, hosts in parallel, each host at its own pace."""
        return await asyncio.gather(*(self.fetch(u, max_age) for u in urls))
