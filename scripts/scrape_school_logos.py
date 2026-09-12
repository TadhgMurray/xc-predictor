#!/usr/bin/env python3
"""
scrape_school_logos.py -- one crest per school (305, docs/IMAGES-PLAN.md,
steps 2 and 3). Reads the address book build_school_websites.py filled,
visits each school's home page ONCE, keeps the best icon it declares,
normalises it to a 512 px PNG and records what it did.

    python scripts/scrape_school_logos.py --check                 # no network, no writes
    python scripts/scrape_school_logos.py --limit 50 --dry-run    # fetch, store nothing
    python scripts/scrape_school_logos.py --write --rate 1.0      # the real run

    school_logo (school, state, path, source_url, kind, sha, shared,
                 status, override, fetched)

★ MANNERS ARE THE FEATURE, NOT THE GARNISH. One request per school, a
  second or more between them, robots.txt read and obeyed per host (and
  its Crawl-delay honoured when it states one), a User-Agent that names
  the site and a contact address, no retry after a refusal, a hard cap on
  what will be downloaded. Twenty thousand schools is a few hours; run it
  from tmux at a quiet hour and never beside a pipeline step, because the
  database it writes is the one the site reads.

★ THE PICKING ORDER (the plan's): Open Graph image, Apple touch icon,
  any icon link that declares a size, the Windows tile, the plain
  favicon. The FIRST that is at least MIN_PX and roughly square wins --
  which is why an Open Graph banner at 1200x630 quietly loses to the
  touch icon behind it, and why a 16 px favicon loses to nothing at all.

⚠ ONE CREST ON FORTY PAGES IS WORSE THAN NONE. Districts serve one logo
  from one CMS for every school they run. markShared() counts each
  image's bytes across schools after the run and flags anything worn by
  SHARED_MIN or more; the site skips those rows. Nothing is deleted --
  an owner override still publishes one.

⚠ THIS SANDBOX CANNOT REACH A SCHOOL WEBSITE OR A DATABASE. Everything
  offline here is covered by tests/test_school_logos.py; the network and
  Pillow paths run first on the box behind --dry-run.
"""
import argparse
import hashlib
import io
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from html.parser import HTMLParser

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from school_logo import LOGO_DIR, LOGO_PX, fileFor          # noqa: E402

UA = ("racecast logo fetch (+https://racecast.co/about; "
      "contact: tadhg.a.murray@gmail.com)")

MIN_PX = 96                 # the plan's floor: smaller than this is a favicon
MAX_ASPECT = 1.6            # "roughly square": a banner is not a crest
MAX_BYTES = 5 * 1024 * 1024
TIMEOUT = 20
SHARED_MIN = 4              # this many schools wearing one image = a district's
REFRESH_DAYS = 90           # the plan's quarterly refresh

# what a page may declare, best first. (kind, weight) -- the order here IS
# the picking order.
KINDS = ("og", "apple-touch", "icon-sized", "tile", "icon")


# ===================================================================== #
#  WHAT A PAGE DECLARES                                                 #
# ===================================================================== #

class _Icons(HTMLParser):
    """Every icon a page declares, in document order. Stops at </head>:
    an og:image or an icon link in the body is not a thing, and school
    CMS pages are large."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.found = []                      # (kind, url, declared_px)
        self.done = False

    def handle_starttag(self, tag, attrs):
        if self.done:
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            prop = (a.get("property") or a.get("name") or "").lower()
            if prop in ("og:image", "og:image:url", "og:image:secure_url",
                        "twitter:image", "twitter:image:src"):
                self.found.append(("og", a.get("content", ""), 0))
            elif prop == "msapplication-tileimage":
                self.found.append(("tile", a.get("content", ""), 0))
        elif tag == "link":
            rel = " ".join((a.get("rel") or "").lower().split())
            href = a.get("href", "")
            if not href or not rel:
                return
            px = _sizePx(a.get("sizes", ""))
            if "apple-touch-icon" in rel:
                self.found.append(("apple-touch", href, px))
            elif "icon" in rel.split() or rel in ("shortcut icon", "icon shortcut"):
                self.found.append(("icon-sized" if px else "icon", href, px))
        elif tag == "body":
            self.done = True

    def handle_endtag(self, tag):
        if tag == "head":
            self.done = True


def _sizePx(sizes):
    """"180x180" -> 180; "any"/"" -> 0. The largest when several."""
    best = 0
    for tok in (sizes or "").lower().split():
        m = re.match(r"^(\d+)x(\d+)$", tok)
        if m:
            best = max(best, min(int(m.group(1)), int(m.group(2))))
    return best


def iconCandidates(page_html, base_url):
    """[(kind, absolute_url, declared_px)] in the plan's picking order.
    Data URIs and anything that is not http(s) are dropped; an identical
    URL twice is kept once. A page that declares nothing still yields the
    conventional /favicon.ico, which is where most school CMSes hide the
    only square image they have."""
    p = _Icons()
    try:
        p.feed(page_html)
    except Exception:                                     # noqa: BLE001
        pass                                              # keep what parsed
    out, seen = [], set()
    ranked = sorted(range(len(p.found)),
                    key=lambda i: (KINDS.index(p.found[i][0])
                                   if p.found[i][0] in KINDS else 99,
                                   -p.found[i][2], i))
    for i in ranked:
        kind, href, px = p.found[i]
        url = _absolute(href, base_url)
        if url and url not in seen:
            seen.add(url)
            out.append((kind, url, px))
    fallback = _absolute("/favicon.ico", base_url)
    if fallback and fallback not in seen:
        out.append(("icon", fallback, 0))
    return out


def _absolute(href, base_url):
    href = (href or "").strip()
    if not href or href.lower().startswith(("data:", "javascript:", "about:")):
        return None
    try:
        url = urllib.parse.urljoin(base_url, href)
        p = urllib.parse.urlsplit(url)
    except ValueError:
        return None
    if p.scheme not in ("http", "https") or not p.netloc:
        return None
    return urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, p.query, ""))


# ===================================================================== #
#  MANNERS                                                              #
# ===================================================================== #

class Manners:
    """One request at a time, paced, robots-checked, never retried.

    ! THE PACE IS GLOBAL, NOT PER HOST. Twenty thousand schools are
      twenty thousand hosts, so a per-host delay would be no delay at
      all -- the run would open a thousand connections a second across
      the country and look exactly like an attack."""

    def __init__(self, rate=1.0, timeout=TIMEOUT, ua=UA):
        self.rate = float(rate)
        self.timeout = timeout
        self.ua = ua
        self._last = 0.0
        self._robots = {}
        self.requests = 0
        self.etag = self.modified = None

    def wait(self, extra=0.0):
        gap = max(self.rate, extra)
        now = time.time()
        if now - self._last < gap:
            time.sleep(gap - (now - self._last))
        self._last = time.time()

    def allowed(self, url):
        """(ok, crawl_delay). A robots.txt we cannot read is a yes -- the
        standard's own default -- and a robots.txt that says no is final."""
        p = urllib.parse.urlsplit(url)
        root = f"{p.scheme}://{p.netloc}"
        rp = self._robots.get(root)
        if rp is None:
            rp = urllib.robotparser.RobotFileParser()
            rp.set_url(root + "/robots.txt")
            self.wait()
            try:
                req = urllib.request.Request(root + "/robots.txt",
                                             headers={"User-Agent": self.ua})
                with urllib.request.urlopen(req, timeout=self.timeout) as fh:
                    rp.parse(fh.read(256 * 1024).decode("utf-8", "replace").splitlines())
            except Exception:                             # noqa: BLE001
                rp.parse([])                              # unreadable = allowed
            self.requests += 1
            self._robots[root] = rp
        try:
            ok = rp.can_fetch(self.ua, url)
            delay = rp.crawl_delay(self.ua)
        except Exception:                                 # noqa: BLE001
            ok, delay = True, None
        return ok, (float(delay) if delay else 0.0)

    def get(self, url, max_bytes=MAX_BYTES, etag=None, modified=None):
        """(bytes, content_type) or (None, reason). One attempt. A refusal
        is a reason, never an exception and never a second try.

        `etag` / `modified`: the validators the last fetch of this URL came
        back with. The plan's quarterly refresh is "only-if-changed", and
        this is where that is spent -- a crest that has not moved answers
        304 with no body, which is the cheapest thing either side can do.
        The last response's validators are left on the instance for the
        caller to store."""
        ok, delay = self.allowed(url)
        if not ok:
            return None, "robots"
        self.wait(delay)
        headers = {"User-Agent": self.ua, "Accept": "*/*",
                   "Accept-Language": "en-US,en;q=0.8"}
        if etag:
            headers["If-None-Match"] = etag
        if modified:
            headers["If-Modified-Since"] = modified
        self.etag = self.modified = None
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as fh:
                self.requests += 1
                self.etag = fh.headers.get("ETag")
                self.modified = fh.headers.get("Last-Modified")
                ctype = (fh.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                length = fh.headers.get("Content-Length")
                if length and length.isdigit() and int(length) > max_bytes:
                    return None, "too big"
                body = fh.read(max_bytes + 1)
                if len(body) > max_bytes:
                    return None, "too big"
                return body, ctype
        except urllib.error.HTTPError as exc:
            self.requests += 1
            return None, ("304" if exc.code == 304 else f"HTTP {exc.code}")
        except Exception as exc:                          # noqa: BLE001
            self.requests += 1
            return None, f"{type(exc).__name__}"


# ===================================================================== #
#  THE IMAGE                                                            #
# ===================================================================== #

def acceptable(width, height):
    """The plan's rule: at least MIN_PX on the short side and roughly
    square. Pure arithmetic, so the picking order is testable without
    Pillow and without a network."""
    if not width or not height:
        return False
    if min(width, height) < MIN_PX:
        return False
    return max(width, height) / float(min(width, height)) <= MAX_ASPECT


def normalise(raw, px=LOGO_PX):
    """(png_bytes, sha256, (w, h)) for a fetched image, or (None, None,
    reason) when it is not one we can use. Transparent margins come off,
    the image is fitted into a square of `px` on a transparent ground, so
    every crest the site draws is the same box whatever shape it arrived
    in."""
    try:
        from PIL import Image, ImageOps
    except ImportError:                                   # pragma: no cover
        return None, None, "no Pillow"
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
    except Exception as exc:                              # noqa: BLE001
        return None, None, f"unreadable ({type(exc).__name__})"
    if im.mode not in ("RGBA", "LA"):
        im = im.convert("RGBA")
    else:
        im = im.convert("RGBA")
    box = im.getbbox()                       # transparent margin off
    if box:
        im = im.crop(box)
    w, h = im.size
    if not acceptable(w, h):
        return None, None, f"{w}x{h}"
    im = ImageOps.contain(im, (px, px), Image.LANCZOS)
    canvas = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    canvas.paste(im, ((px - im.width) // 2, (px - im.height) // 2), im)
    out = io.BytesIO()
    canvas.save(out, format="PNG", optimize=True)
    data = out.getvalue()
    return data, hashlib.sha256(data).hexdigest(), (w, h)


def fetchLogo(manners, home_url, direct=None):
    """(png, sha, kind, source_url) for one school, or (None, None, None,
    reason). One page fetch plus one image fetch per candidate that is
    worth trying, and the first acceptable image ends it.

    `direct` is a logo file we already know (Wikidata's P154, or an owner
    override): it is tried first and costs no page visit, and only if it
    fails does the home page get read."""
    if direct:
        raw, ctype = manners.get(direct)
        png, sha, why = normalise(raw) if raw is not None else (None, None, ctype)
        if png is not None:
            return png, sha, "direct", direct
        if not home_url:
            return None, None, None, f"logo {why}"
    if not home_url:
        return None, None, None, "no address"
    page, ctype = manners.get(home_url, max_bytes=2 * 1024 * 1024)
    if page is None:
        return None, None, None, f"page {ctype}"
    if ctype and "html" not in ctype:
        return None, None, None, f"page {ctype}"
    text = page.decode("utf-8", "replace")
    reason = "no icon"
    for kind, url, px in iconCandidates(text, home_url)[:6]:
        if url.lower().endswith(".svg"):
            reason = "svg only"
            continue                                      # Pillow cannot read one
        raw, ct = manners.get(url)
        if raw is None:
            reason = f"{kind} {ct}"
            continue
        png, sha, why = normalise(raw)
        if png is None:
            reason = f"{kind} {why}"
            continue
        return png, sha, kind, url
    return None, None, None, reason


UNCHANGED = "unchanged"


def refetch(manners, source_url, etag=None, modified=None):
    """The quarterly re-ask, straight at the file we kept last time.

    Returns UNCHANGED when the server says 304 (one request, no body, no
    home page read at all -- which is what the whole run costs once it has
    settled), (png, sha) when the crest has moved, or None to fall back to
    reading the school's home page again, because a source URL that has
    gone is exactly the case a re-discovery is for."""
    if not source_url:
        return None
    raw, why = manners.get(source_url, etag=etag, modified=modified)
    if why == "304":
        return UNCHANGED
    if raw is None:
        return None
    png, sha, _why = normalise(raw)
    return (png, sha) if png else None


# ===================================================================== #
#  STORE                                                                #
# ===================================================================== #

DDL = """
CREATE TABLE IF NOT EXISTS school_logo (
    school     text NOT NULL,
    state      text NOT NULL DEFAULT '',
    path       text,
    source_url text,
    kind       text,
    sha        text,
    shared     boolean NOT NULL DEFAULT false,
    status     text NOT NULL DEFAULT 'none',
    override   text,
    etag       text,
    modified   text,
    fetched    date,
    PRIMARY KEY (school, state))
"""


def targets(cur, refresh_days=REFRESH_DAYS, only=None, state=None, limit=None,
            retry_failed=False):
    """[(school, state, url, direct_logo, override)] still to do: every
    school with an address that has never been tried, or whose last try is
    older than the refresh window.

    ⚠ A SCHOOL THAT YIELDED NOTHING IS NOT TRIED AGAIN UNTIL THE WINDOW IS
      UP. `fetched` is stamped on a failure too, so a site with no square
      image on it costs one request a quarter, not one a run. --retry-failed
      when a fix to the picking is worth re-asking sooner."""
    cur.execute(DDL)
    sql = """
        SELECT w.school, w.state, w.url, w.direct_logo, l.override,
               l.source_url, l.etag, l.modified, l.status
        FROM   school_website w
        LEFT   JOIN school_logo l ON l.school = w.school AND l.state = w.state
        WHERE  (w.url IS NOT NULL OR w.direct_logo IS NOT NULL)
          AND  COALESCE(lower(l.override), '') <> 'none'
          AND  (l.fetched IS NULL OR l.fetched < current_date - %s
                OR (%s AND l.status <> 'ok'))
    """
    params = [int(refresh_days), bool(retry_failed)]
    if only:
        sql += " AND w.school ILIKE %s"
        params.append(f"%{only}%")
    if state:
        sql += " AND w.state = %s"
        params.append(state.upper())
    sql += " ORDER BY w.school, w.state"
    if limit:
        sql += " LIMIT %s"
        params.append(int(limit))
    cur.execute(sql, params)
    rows = []
    for r in cur.fetchall():
        rows.append(tuple(r[k] for k in ("school", "state", "url",
                                         "direct_logo", "override",
                                         "source_url", "etag", "modified",
                                         "status"))
                    if isinstance(r, dict) else tuple(r))
    return rows


def writeFile(school, state, png, directory=None):
    """The PNG on disk under its derived name; returns the bare file name,
    which is what the row stores and what the site re-derives."""
    directory = directory or LOGO_DIR
    os.makedirs(directory, exist_ok=True)
    name = fileFor(school, state)
    path = os.path.join(directory, name)
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(png)
    os.replace(tmp, path)
    return name


def record(cur, school, state, name, source_url, kind, sha, status,
           etag=None, modified=None):
    cur.execute("""
        INSERT INTO school_logo (school, state, path, source_url, kind, sha,
                                 status, etag, modified, fetched)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, current_date)
        ON CONFLICT (school, state) DO UPDATE
        SET path = EXCLUDED.path, source_url = EXCLUDED.source_url,
            kind = EXCLUDED.kind, sha = EXCLUDED.sha,
            status = EXCLUDED.status, etag = EXCLUDED.etag,
            modified = EXCLUDED.modified, fetched = current_date
    """, (school, state, name, source_url, kind, sha, status, etag, modified))


def touch(cur, school, state):
    """The crest has not changed: stamp the date and nothing else, so the
    next quarter's run skips it without another request."""
    cur.execute("""UPDATE school_logo SET fetched = current_date
                   WHERE school = %s AND state = %s""", (school, state))


def sharedShas(rows, minimum=SHARED_MIN):
    """{sha} worn by `minimum` or more DISTINCT schools. Distinct schools,
    not rows: one school split into two state clusters legitimately wears
    its own crest twice."""
    by = {}
    for school, sha in rows:
        if sha:
            by.setdefault(sha, set()).add(school)
    return {sha for sha, schools in by.items() if len(schools) >= minimum}


def markShared(cur, minimum=SHARED_MIN):
    """Flag the district crests, clear the flag from anything that is no
    longer one. Runs at the end of every run over the WHOLE table, so a
    crest that becomes shared on the fortieth school stops serving on the
    thirty-nine before it."""
    cur.execute("SELECT school, sha FROM school_logo WHERE sha IS NOT NULL")
    rows = [(r["school"], r["sha"]) if isinstance(r, dict) else (r[0], r[1])
            for r in cur.fetchall()]
    shas = sharedShas(rows, minimum)
    cur.execute("UPDATE school_logo SET shared = false WHERE shared")
    if shas:
        cur.execute("UPDATE school_logo SET shared = true WHERE sha = ANY(%s)",
                    (list(shas),))
    return len(shas)


# ===================================================================== #
#  RUN                                                                  #
# ===================================================================== #

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help="store rows and files")
    ap.add_argument("--dry-run", action="store_true",
                    help="fetch and report, write no row and no file")
    ap.add_argument("--check", action="store_true",
                    help="count what is left to do and stop; no network")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--only", default=None, help="schools whose name contains this")
    ap.add_argument("--state", default=None)
    ap.add_argument("--rate", type=float, default=1.0,
                    help="seconds between requests, everywhere (default 1)")
    ap.add_argument("--refresh-days", type=int, default=REFRESH_DAYS)
    ap.add_argument("--retry-failed", action="store_true",
                    help="also re-ask the schools that yielded nothing last time")
    ap.add_argument("--dir", default=None, help="where the PNGs go (default XCP_LOGO_DIR)")
    ap.add_argument("--sweep-only", action="store_true",
                    help="re-run the shared-crest sweep and stop")
    args = ap.parse_args()
    if not (args.write or args.dry_run or args.check or args.sweep_only):
        ap.error("pass --check, --dry-run, --write or --sweep-only")

    from database import getConn
    with getConn() as conn:
        with conn.cursor() as cur:
            if args.sweep_only:
                cur.execute(DDL)
                n = markShared(cur)
                conn.commit()
                print(f"  shared crests: {n:,} images worn by {SHARED_MIN}+ schools")
                return
            todo = targets(cur, args.refresh_days, args.only, args.state,
                           args.limit, args.retry_failed)
            conn.commit()
            print(f"  {len(todo):,} schools to fetch "
                  f"(rate {args.rate}s, about {len(todo) * args.rate * 2 / 3600:.1f} h)")
            if args.check:
                for t in todo[:10]:
                    print(f"    {t[0]} ({t[1]}) {t[5] or t[2] or t[3]}")
                return

            manners = Manners(rate=args.rate)
            got = same = skipped = 0
            t0 = time.time()
            for i, row in enumerate(todo, 1):
                (school, state, url, direct, override,
                 had_url, etag, modified, status0) = row
                override_url = override if (override or "").startswith("http") else None

                # ★ THE REFRESH IS ONE CONDITIONAL REQUEST, NOT TWO FRESH
                #   ONES. A school we already have a crest for is asked
                #   about that exact file; a 304 ends it there and the home
                #   page is never read. Only a school we have never placed,
                #   or one whose file has gone, costs a discovery.
                again = None
                if had_url and status0 == "ok" and not override_url:
                    again = refetch(manners, had_url, etag, modified)
                if again is UNCHANGED:
                    same += 1
                    if args.write:
                        touch(cur, school, state)
                    continue
                if again:
                    png, sha, kind, src = again[0], again[1], "refresh", had_url
                else:
                    target = override_url or direct or url
                    png, sha, kind, src = fetchLogo(
                        manners, url, direct=(target if target != url else None))
                status = "ok" if png else f"none: {src}"[:60]
                if png:
                    got += 1
                else:
                    skipped += 1
                if args.write:
                    name = writeFile(school, state, png, args.dir) if png else None
                    # the validators belong to the image we KEPT; a failed
                    # run's last request must not leave its ETag behind for
                    # the next refresh to send at a different URL
                    record(cur, school, state, name, src if png else None,
                           kind, sha, status,
                           manners.etag if png else None,
                           manners.modified if png else None)
                    if i % 50 == 0:
                        conn.commit()
                if i % 25 == 0 or args.dry_run:
                    rate = i / max(1e-9, time.time() - t0)
                    print(f"  [{i:,}/{len(todo):,}] {school} ({state}): "
                          f"{kind + ' ok' if png else src}"
                          f"   {got:,} kept, {skipped:,} without, {rate * 3600:.0f}/h",
                          flush=True)
            if args.write:
                n = markShared(cur)
                conn.commit()
                print(f"  shared crests: {n:,} images worn by {SHARED_MIN}+ schools")
            print(f"  done: {got:,} logos, {same:,} unchanged, {skipped:,} without, "
                  f"{manners.requests:,} requests in {(time.time() - t0) / 60:.1f} min")


if __name__ == "__main__":
    main()
