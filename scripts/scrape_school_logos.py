#!/usr/bin/env python3
"""
scrape_school_logos.py -- one crest per school (305, docs/IMAGES-PLAN.md).
Reads the address book build_school_websites.py filled, finds each school's
ATHLETICS mark, normalises it to a 512 px PNG and records what it did.

    python scripts/scrape_school_logos.py --stats             # where the last run went
    python scripts/scrape_school_logos.py --write --limit 2000
    python scripts/scrape_school_logos.py --write --redo      # re-ask everyone

    school_logo (school, state, path, source_url, kind, sha, shared,
                 status, override, etag, modified, fetched)

★ THE ATHLETICS SITE FIRST, THE SCHOOL'S SECOND (owner, 2026-09-12: MIT's
  homepage gives the institutional wordmark; what belongs on a results
  table is the Engineers mark). A school's home page is read for its own
  icons AND for its link to athletics -- "Athletics", /athletics,
  mitathletics.com, gobearcats.com -- and the athletics site's icons are
  tried first. One extra request, and it is the request that gets the
  right picture.

★ CONCURRENT ACROSS HOSTS, PACED PER HOST (owner: "way too slow"). The
  first version paced one request a second GLOBALLY, which is twenty-four
  hours for forty thousand schools and was simply wrong: forty thousand
  schools are forty thousand different servers, and each one sees three
  requests in total. Politeness is per host -- a second between requests
  to the same server, robots.txt read and obeyed, one attempt, no retry --
  and WORKERS of them run at once. An hour, not a day.

★ THE BIGGEST PROGRAMMES FIRST. Schools are worked in descending athlete
  count, so the first ten minutes cover the schools that appear on most
  pages. --limit then means "the ones that matter", not "the ones that
  sort early".

⚠ ONE CREST ON FORTY PAGES IS WORSE THAN NONE. Districts serve one logo
  from one CMS for every school they run. markShared() counts each
  image's bytes across schools after the run and flags anything worn by
  SHARED_MIN or more; the site skips those rows. Nothing is deleted --
  an owner override still publishes one.

⚠ THIS SANDBOX CANNOT REACH A SCHOOL WEBSITE OR A DATABASE. Everything
  offline here is covered by tests/test_school_logos.py.
"""
import argparse
import hashlib
import io
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from school_logo import LOGO_DIR, LOGO_PX, fileFor          # noqa: E402

# ! THE CONVENTIONAL BOT SHAPE, not a disguise. "Mozilla/5.0 (compatible;
#   <name>; +<url>)" is what Googlebot and every other well-behaved crawler
#   sends, and a great many school WAFs 403 anything that does not start
#   that way -- 487 of them in the first run. It still names us and still
#   carries a contact address, which is the part that matters.
UA = ("Mozilla/5.0 (compatible; racecast/1.0; +https://racecast.co/about; "
      "contact: tadhg.a.murray@gmail.com)")

# ! THE FLOOR IS FOR A MARK, NOT A POSTER (owner: "coverage is atrocious").
#   96 px and 1.6:1 threw away most of what school sites actually publish:
#   a 64 px favicon, a 48 px CMS icon, a wide wordmark. These are drawn at
#   18 px inline and 44 px in a header, so 48 px is real detail, and a
#   wordmark at 3:1 is still the school's mark -- object-fit and the card's
#   tile letterbox it. What the floor exists for is rejecting a 16 px
#   favicon and a 1200x630 social banner, and it still does.
MIN_PX = 48
# ⚠ THE ASPECT CAP DEPENDS ON WHO DECLARED THE IMAGE, and it has to. A
#   1200x630 social banner is 1.9:1 and a school's wordmark is 3:1, so
#   shape alone cannot tell them apart -- but the DECLARATION can. A file
#   a site declares as its icon (apple-touch, manifest, tile, rel=icon) is
#   its mark whatever shape it is; an og:image is a banner until proven
#   otherwise, and so is anything we found some other way.
MAX_ASPECT = 1.6
ICON_MAX_ASPECT = 3.0
_ICON_KINDS = ("apple-touch", "icon-sized", "icon", "tile", "manifest")
MAX_BYTES = 5 * 1024 * 1024
TIMEOUT = 15
SHARED_MIN = 4              # this many schools wearing one image = a district's
REFRESH_DAYS = 90
WORKERS = 24
HOME_TRIES = 3              # spellings of one school's address before giving up

# what a page may declare, best first. Manifest icons are the best of all
# (192 and 512 px, square by convention) but cost a request to discover, so
# they are a second tier: see fetchLogo.
KINDS = ("apple-touch", "icon-sized", "tile", "og", "icon")
MANIFEST = "manifest"

# matched on the registrable domain, not as a substring: "x.com" as a
# substring also matches phoenix.com
_SOCIAL = {"facebook.com", "twitter.com", "x.com", "instagram.com",
           "youtube.com", "youtu.be", "linkedin.com", "tiktok.com",
           "flickr.com", "vimeo.com", "pinterest.com", "threads.net"}


def _isSocial(host):
    return ".".join(host.lower().split(":")[0].split(".")[-2:]) in _SOCIAL


# ===================================================================== #
#  WHAT A PAGE DECLARES                                                 #
# ===================================================================== #

class _Icons(HTMLParser):
    """Every icon a page declares, in document order, plus its web manifest.
    Stops at <body>: an icon link in the body is not a declaration."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.found = []                      # (kind, url, declared_px)
        self.manifest = None
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
            if rel == "manifest":
                self.manifest = href
                return
            px = _sizePx(a.get("sizes", ""))
            if "apple-touch-icon" in rel:
                self.found.append(("apple-touch", href, px))
            elif "icon" in rel.split():
                self.found.append(("icon-sized" if px else "icon", href, px))
        elif tag == "body":
            self.done = True

    def handle_endtag(self, tag):
        if tag == "head":
            self.done = True


class _Links(HTMLParser):
    """Every anchor with its text -- for finding the athletics site."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self._href = None
        self._text = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict((k.lower(), v or "") for k, v in attrs).get("href")
            self._text = []

    def handle_data(self, data):
        if self._href is not None and len(self._text) < 8:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href:
            self.links.append((self._href, " ".join(self._text).strip()[:80]))
            self._href, self._text = None, []


def _sizePx(sizes):
    """"180x180" -> 180; "any"/"" -> 0. The largest when several."""
    best = 0
    for tok in (sizes or "").lower().split():
        m = re.match(r"^(\d+)x(\d+)$", tok)
        if m:
            best = max(best, min(int(m.group(1)), int(m.group(2))))
    return best


def iconCandidates(page_html, base_url):
    """([(kind, absolute_url, declared_px)], manifest_url). Ordered best
    first; data URIs and non-http dropped; the conventional /favicon.ico
    appended, because most school CMSes hide their only square image there.

    ⚠ THE OPEN GRAPH IMAGE IS NOT FIRST ANY MORE. It is a 1200x630 social
      banner on nearly every CMS, so trying it first spent a request per
      school on a certain rejection. The declared-square icons go first
      and og is the fallback before the bare favicon."""
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
    return out, _absolute(p.manifest, base_url)


def manifestIcons(raw, base_url):
    """[(kind, url, px)] from a web app manifest's icons array, biggest
    first. These are the best marks a modern site publishes: square by
    convention and usually 192 or 512 px."""
    try:
        icons = (json.loads(raw.decode("utf-8", "replace")) or {}).get("icons") or []
    except Exception:                                     # noqa: BLE001
        return []
    out = []
    for ic in icons:
        if not isinstance(ic, dict):
            continue
        url = _absolute(ic.get("src"), base_url)
        if url:
            out.append((MANIFEST, url, _sizePx(ic.get("sizes", ""))))
    out.sort(key=lambda t: -t[2])
    return out


def athleticsLink(page_html, base_url):
    """The school's ATHLETICS site, from its own home page, or None.

    ★ WHY THIS IS THE WHOLE POINT (owner, MIT). mit.edu's icons are the
      institutional wordmark; mitathletics.com's are the Engineers mark,
      which is what a results table wants. Every school that has one links
      to it from its home page, so the link is free -- we already have the
      page.

    Scored, not first-match: a separate athletics DOMAIN wins outright,
    then an /athletics path on the school's own site, then a link the page
    itself captions "Athletics" -- which is the case that matters most,
    because a college's athletics domain is very often a nickname with no
    tell in it at all (gopoets.com, rolltide.com), and the caption is the
    only thing that identifies it. Social links are never it."""
    p = _Links()
    try:
        p.feed(page_html)
    except Exception:                                     # noqa: BLE001
        pass
    home = urllib.parse.urlsplit(base_url).netloc.lower()
    best, best_score = None, 0
    for href, text in p.links:
        url = _absolute(href, base_url)
        if not url:
            continue
        parts = urllib.parse.urlsplit(url)
        host, path = parts.netloc.lower(), parts.path.lower()
        if _isSocial(host):
            continue
        low = " ".join(text.lower().split())
        named = "athletic" in low or "varsity" in low
        score = 0
        if host != home:
            # mitathletics.com, gobearcats.com, godevilsports.com
            if "athletic" in host or re.match(
                    r"^(www\.)?go[a-z0-9-]+(sports|athletics)\.", host):
                score += 8
            elif named:
                # gopoets.com captioned "Athletics": the caption is all we get
                score += 3
        if re.search(r"(^|/)athletics?(/|$)", path):
            score += 3
        elif "athletic" in path:
            score += 2
        if re.fullmatch(r"(go to |visit )?athletics( home| site| website)?", low):
            score += 3
        elif named:
            score += 1
        if score > best_score:
            best, best_score = url, score
    return best if best_score >= 3 else None


def _absolute(href, base_url):
    href = (href or "").strip()
    if not href or href.lower().startswith(("data:", "javascript:", "about:",
                                            "mailto:", "tel:", "#")):
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

def _reason(exc):
    """A failure worth reading. "URLError" was 1,886 of the first run's
    misses and said nothing: DNS, a dead certificate and a refused
    connection all arrive as one class, and only one of them is worth
    doing anything about."""
    import socket
    import ssl
    r = getattr(exc, "reason", exc)
    if isinstance(r, (ssl.SSLError, ssl.CertificateError)):
        return "ssl"
    if isinstance(r, socket.gaierror):
        return "dns"
    if isinstance(r, (TimeoutError, socket.timeout)):
        return "timeout"
    if isinstance(r, ConnectionRefusedError):
        return "refused"
    if isinstance(r, ConnectionResetError):
        return "reset"
    return type(r).__name__


# A refusal is final: robots said no, or the server did. Anything else is
# the ADDRESS being wrong, and a different spelling of it is a different
# request rather than a retry of the same one.
_FINAL = ("robots", "HTTP 401", "HTTP 403", "HTTP 429", "too big")


def retryable(reason):
    return not any(reason.endswith(f) for f in _FINAL)


class Manners:
    """Polite per HOST, concurrent across hosts, never retried.

    ★ PER HOST IS THE CORRECT UNIT, and the first version got it wrong.
      A global one-a-second is not politeness when every school is a
      different server -- it is just a day of waiting, and each server
      still sees its three requests. What a server experiences is the
      per-host gap: one second between requests to IT, its robots.txt read
      and obeyed, its Crawl-delay honoured, one attempt and no retry after
      a refusal. Thread-safe, because WORKERS of these run at once."""

    def __init__(self, rate=1.0, timeout=TIMEOUT, ua=UA):
        self.rate = float(rate)
        self.timeout = timeout
        self.ua = ua
        self.requests = 0
        self._lock = threading.Lock()
        self._hostAt = {}
        self._robots = {}
        self._robotLocks = {}
        self._local = threading.local()
        self._insecure = None
        self.insecureHosts = set()

    # -- the last response's validators, per thread (see refetch) --------
    @property
    def etag(self):
        return getattr(self._local, "etag", None)

    @property
    def modified(self):
        return getattr(self._local, "modified", None)

    def wait(self, host="", extra=0.0):
        """Sleep until this HOST may be asked again."""
        gap = max(self.rate, extra)
        if gap <= 0:
            return
        while True:
            with self._lock:
                now = time.time()
                since = now - self._hostAt.get(host, 0.0)
                if since >= gap:
                    self._hostAt[host] = now
                    return
                nap = gap - since
            time.sleep(nap)

    def _count(self):
        with self._lock:
            self.requests += 1

    def allowed(self, url):
        """(ok, crawl_delay). A robots.txt we cannot read is a yes -- the
        standard's own default -- and one that says no is final. Read once
        per host even when twenty workers ask at the same moment."""
        p = urllib.parse.urlsplit(url)
        root = f"{p.scheme}://{p.netloc}"
        with self._lock:
            rp = self._robots.get(root)
            if rp is None:
                lock = self._robotLocks.setdefault(root, threading.Lock())
        if rp is None:
            with lock:
                rp = self._robots.get(root)
                if rp is None:
                    rp = urllib.robotparser.RobotFileParser()
                    rp.set_url(root + "/robots.txt")
                    self.wait(p.netloc)
                    try:
                        req = urllib.request.Request(
                            root + "/robots.txt", headers={"User-Agent": self.ua})
                        with urllib.request.urlopen(req, timeout=self.timeout) as fh:
                            rp.parse(fh.read(256 * 1024)
                                     .decode("utf-8", "replace").splitlines())
                    except Exception:                     # noqa: BLE001
                        rp.parse([])                      # unreadable = allowed
                    self._count()
                    with self._lock:
                        self._robots[root] = rp
        try:
            return rp.can_fetch(self.ua, url), float(rp.crawl_delay(self.ua) or 0.0)
        except Exception:                                 # noqa: BLE001
            return True, 0.0

    def _ctx(self):
        """⚠ A CONTEXT THAT DOES NOT VERIFY, USED ONLY AS A SECOND ATTEMPT
        AFTER A CERTIFICATE FAILURE. School district certificates expire,
        go self-signed and miss their intermediates constantly, and the
        school is still the school. What is at stake if this is ever abused
        is a wrong PNG beside a school's name -- no credential is sent, no
        data is read, nothing is trusted downstream. The verified attempt
        always happens first, and the hosts that needed this are listed at
        the end of the run."""
        if self._insecure is None:
            import ssl
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            self._insecure = ctx
        return self._insecure

    def get(self, url, max_bytes=MAX_BYTES, etag=None, modified=None,
            insecure=False, extra=None):
        """(bytes, content_type) or (None, reason). One attempt.

        `etag` / `modified`: the validators the last fetch of this URL came
        back with -- the quarterly refresh is "only-if-changed", and a crest
        that has not moved answers 304 with no body."""
        ok, delay = self.allowed(url)
        if not ok:
            return None, "robots"
        host = urllib.parse.urlsplit(url).netloc
        self.wait(host, delay)
        headers = {"User-Agent": self.ua, "Accept": "*/*",
                   "Accept-Language": "en-US,en;q=0.8"}
        if etag:
            headers["If-None-Match"] = etag
        if modified:
            headers["If-Modified-Since"] = modified
        headers.update(extra or {})
        self._local.etag = self._local.modified = None
        req = urllib.request.Request(url, headers=headers)
        kw = {"context": self._ctx()} if insecure else {}
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, **kw) as fh:
                self._count()
                self._local.etag = fh.headers.get("ETag")
                self._local.modified = fh.headers.get("Last-Modified")
                ctype = (fh.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                length = fh.headers.get("Content-Length")
                if length and length.isdigit() and int(length) > max_bytes:
                    return None, "too big"
                body = fh.read(max_bytes + 1)
                if len(body) > max_bytes:
                    return None, "too big"
                return body, ctype
        except urllib.error.HTTPError as exc:
            self._count()
            return None, ("304" if exc.code == 304 else f"HTTP {exc.code}")
        except Exception as exc:                          # noqa: BLE001
            self._count()
            why = _reason(exc)
            if why == "ssl" and not insecure:
                self.insecureHosts.add(urllib.parse.urlsplit(url).netloc)
                return self.get(url, max_bytes, etag, modified, insecure=True,
                                extra=extra)
            return None, why


# ===================================================================== #
#  THE IMAGE                                                            #
# ===================================================================== #

def acceptable(width, height, kind=None):
    """At least MIN_PX on the short side, and not a banner -- where "not a
    banner" is looser for a file the site itself declared as an icon. Pure
    arithmetic, so the picking is testable without Pillow or a network."""
    if not width or not height:
        return False
    if min(width, height) < MIN_PX:
        return False
    cap = ICON_MAX_ASPECT if (kind or "").split(":")[-1] in _ICON_KINDS else MAX_ASPECT
    return max(width, height) / float(min(width, height)) <= cap


def _svgToPng(raw, px):
    """An SVG rasterised, when cairosvg is in the venv. Modern school sites
    increasingly publish nothing else, and skipping them was pure lost
    coverage (owner). Absent cairosvg this returns None and the candidate
    is skipped exactly as before."""
    try:
        import cairosvg
    except ImportError:                                   # pragma: no cover
        return None
    try:
        return cairosvg.svg2png(bytestring=raw, output_width=px,
                                output_height=px)
    except Exception:                                     # noqa: BLE001
        return None


def normalise(raw, px=LOGO_PX, ctype="", kind=None):
    """(png_bytes, sha256, (w, h)) for a fetched image, or (None, None,
    reason). Transparent margins come off and the mark is centred in a
    square of `px` on a transparent ground, so every crest the site draws
    is the same box whatever shape it arrived in."""
    try:
        from PIL import Image, ImageOps
    except ImportError:                                   # pragma: no cover
        return None, None, "no Pillow"
    if raw[:5] == b"<?xml" or b"<svg" in raw[:512] or "svg" in (ctype or ""):
        raw = _svgToPng(raw, px)
        if raw is None:
            return None, None, "svg (no cairosvg)"
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
    except Exception:                                     # noqa: BLE001
        return None, None, f"unreadable {ctype or 'image'}"
    im = im.convert("RGBA")
    box = im.getbbox()                       # transparent margin off
    if box:
        im = im.crop(box)
    w, h = im.size
    if not acceptable(w, h, kind):
        return None, None, f"{w}x{h}"
    im = ImageOps.contain(im, (px, px), Image.LANCZOS)
    canvas = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    canvas.paste(im, ((px - im.width) // 2, (px - im.height) // 2), im)
    out = io.BytesIO()
    canvas.save(out, format="PNG", optimize=True)
    data = out.getvalue()
    return data, hashlib.sha256(data).hexdigest(), (w, h)


# ===================================================================== #
#  ONE SCHOOL                                                           #
# ===================================================================== #

MAX_TRIES = 4               # image fetches per site before giving up on it


def homeVariants(url):
    """The same school, spelled the ways a directory entry gets it wrong.

    ★ HALF THE FIRST RUN'S MISSES NEVER REACHED A SITE AT ALL: 1,844 HTTP
      404 (a directory's deep link into a page that moved -- the SITE is
      fine, the path is not), and 1,886 connection failures of which the
      http/https and www ones are just a different spelling. So: as given,
      then the host's own root, then the other scheme, then www toggled.
      At most HOME_TRIES of them, and only while the failure is the kind a
      different spelling could fix -- a refusal is never re-asked."""
    try:
        p = urllib.parse.urlsplit(url)
    except ValueError:
        return []
    if not p.netloc:
        return []
    out = [url]
    def add(scheme, netloc):
        u = urllib.parse.urlunsplit((scheme, netloc, "/", "", ""))
        if u not in out:
            out.append(u)
    if (p.path or "/") != "/" or p.query:
        add(p.scheme, p.netloc)                    # the site, not the page
    add("https" if p.scheme == "http" else "http", p.netloc)
    host = p.netloc
    add(p.scheme, host[4:] if host.startswith("www.") else "www." + host)
    return out[:HOME_TRIES]


def _fetchPage(manners, page_url, tag, variants=False):
    """(page_text, final_url, None) or (None, None, reason).

    With `variants`, the school's address is tried in its several
    spellings (homeVariants) until one answers or the failure turns out to
    be a refusal."""
    # the FIRST failure is the one worth reporting: it is the address as
    # the directory has it, and the later ones are our own guesses at it
    reason = None
    for url in (homeVariants(page_url) if variants else [page_url]):
        page, ctype = manners.get(url, max_bytes=2 * 1024 * 1024)
        if page is not None and (not ctype or "html" in ctype):
            return page.decode("utf-8", "replace"), url, None
        reason = reason or f"{tag} {ctype}"
        if page is None and not retryable(ctype):
            break
    return None, None, reason or f"{tag} no address"


def _fromText(manners, text, page_url, tag):
    """(png, sha, kind, url) from one page's declarations, or (None, None,
    None, reason). Head icons first, at most MAX_TRIES of them; the web
    manifest only if none of those worked, because discovering it costs a
    request of its own."""
    cands, manifest = iconCandidates(text, page_url)
    reason = f"{tag} no icon"
    for tier in (cands[:MAX_TRIES], None):
        if tier is None:                                  # the manifest tier
            if not manifest:
                break
            raw, _ct = manners.get(manifest, max_bytes=512 * 1024)
            tier = manifestIcons(raw, manifest)[:2] if raw else []
        for kind, url, _px in tier:
            raw, ct = manners.get(url)
            if raw is None:
                reason = f"{tag} {kind} {ct}"
                continue
            png, sha, why = normalise(raw, ctype=ct, kind=kind)
            if png is None:
                reason = f"{tag} {kind} {why}"
                continue
            return png, sha, f"{tag}:{kind}", url
    return None, None, None, reason


def fetchLogo(manners, home_url, direct=None):
    """(png, sha, kind, source_url) for one school, or (None, None, None,
    reason).

    ★ THE ORDER IS ATHLETICS, THEN THE SCHOOL. A known logo file (Wikidata,
      or an owner override) is tried first and costs nothing else.
      Otherwise the home page is read once -- for its athletics link AND
      for its own icons -- and if there is an athletics site, ITS icons are
      tried FIRST. That is the difference between MIT's institutional
      wordmark and the Engineers mark.

    ! THE LINK IS READ BEFORE ANY ICON IS FETCHED. Taking the school's own
      icon first and then going to athletics anyway spent an image request
      per school on a picture we were about to throw away."""
    if direct:
        raw, ctype = manners.get(direct)
        png, sha, why = normalise(raw, ctype=ctype) if raw is not None else (None, None, ctype)
        if png is not None:
            return png, sha, "direct", direct
        if not home_url:
            return None, None, None, f"logo {why}"
    if not home_url:
        return None, None, None, "no address"

    text, home, why = _fetchPage(manners, home_url, "school", variants=True)
    if text is None:
        return None, None, None, why

    ath_url = athleticsLink(text, home)
    if ath_url:
        ath_text, ath, _why = _fetchPage(manners, ath_url, "athletics")
        if ath_text is not None:
            got = _fromText(manners, ath_text, ath, "athletics")
            if got[0] is not None:
                return got
    return _fromText(manners, text, home, "school")


UNCHANGED = "unchanged"


def refetch(manners, source_url, etag=None, modified=None):
    """The quarterly re-ask, straight at the file we kept last time.
    UNCHANGED on a 304 (one request, no body, no page read), (png, sha)
    when it has moved, None to fall back to a full rediscovery."""
    if not source_url:
        return None
    raw, why = manners.get(source_url, etag=etag, modified=modified)
    if why == "304":
        return UNCHANGED
    if raw is None:
        return None
    png, sha, _why = normalise(raw, ctype=why)
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


def ensureTable(cur, ddl):
    """CREATE TABLE IF NOT EXISTS, and then add whatever columns the DDL
    has grown since the table was first made.

    ⚠ "IF NOT EXISTS" NEVER ADDS A COLUMN. A table created by last week's
      run keeps last week's shape for ever, and the first INSERT with a new
      column dies with UndefinedColumn -- on the server, mid-run, which is
      exactly how this was found. The ALTERs are derived FROM THE DDL, so
      they cannot drift from it: add a column to the CREATE and it appears
      on old tables too.

    ! A NOT NULL WITH NO DEFAULT IS RELAXED for the ALTER. Postgres cannot
      add one to a table that already has rows, and failing to start is
      worse than a nullable column on old rows."""
    cur.execute(ddl)
    table = re.search(r"CREATE TABLE (?:IF NOT EXISTS )?(\w+)", ddl, re.I)
    body = re.search(r"\((.*)\)", ddl, re.S)
    if not table or not body:
        return
    for col, spec in _ddlColumns(body.group(1)):
        if "NOT NULL" in spec.upper() and "DEFAULT" not in spec.upper():
            spec = re.sub(r"\s*NOT NULL\s*", " ", spec, flags=re.I).strip()
        # an inline PRIMARY KEY / UNIQUE belongs to the CREATE, not to an
        # ALTER adding the column to a table that already has one
        spec = re.sub(r"\s*(PRIMARY KEY|UNIQUE)\s*", " ", spec, flags=re.I).strip()
        if spec:
            cur.execute(f"ALTER TABLE {table.group(1)} "
                        f"ADD COLUMN IF NOT EXISTS {col} {spec}")


_NOT_A_COLUMN = ("primary", "unique", "foreign", "check", "constraint", "exclude")


def _ddlColumns(body):
    """[(name, type-and-modifiers)] from the inside of a CREATE TABLE.
    Splits on commas at bracket depth zero, so numeric(4,1) survives."""
    parts, depth, cur_ = [], 0, ""
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur_)
            cur_ = ""
        else:
            cur_ += ch
    parts.append(cur_)
    out = []
    for part in parts:
        bits = " ".join(part.split()).strip()
        if not bits or bits.split()[0].lower() in _NOT_A_COLUMN:
            continue
        name, _, spec = bits.partition(" ")
        if spec.strip():
            out.append((name, spec.strip()))
    return out


def _tableExists(cur, name):
    cur.execute("SELECT to_regclass(%s)", (f"public.{name}",))
    row = cur.fetchone()
    return (row[0] if not isinstance(row, dict) else row.get("to_regclass")) is not None


def targets(cur, refresh_days=REFRESH_DAYS, only=None, state=None, limit=None,
            retry_failed=False):
    """The schools still to do, BIGGEST PROGRAMME FIRST.

    ★ THE ORDER IS THE POINT. Alphabetical spent the first hour on
      academies with four athletes. Descending athlete count means --limit
      2000 covers the schools that appear on most pages of the site, and
      the long tail can run overnight or never.

    ⚠ A SCHOOL THAT YIELDED NOTHING IS NOT TRIED AGAIN until the refresh
      window is up: `fetched` is stamped on a failure too. --retry-failed
      (or --redo) when the picking has changed and it is worth re-asking."""
    ensureTable(cur, DDL)
    rank = ("COALESCE(si.n_athletes, 0)" if _tableExists(cur, "school_identity")
            else "0")
    join = ("LEFT JOIN school_identity si ON si.school = w.school "
            "AND si.state = w.state" if rank != "0" else "")
    where = ["(w.url IS NOT NULL OR w.direct_logo IS NOT NULL)",
             "COALESCE(lower(l.override), '') <> 'none'",
             "(l.fetched IS NULL OR l.fetched < current_date - %s"
             " OR (%s AND l.status <> 'ok'))"]
    params = [int(refresh_days), bool(retry_failed)]
    if only:
        where.append("w.school ILIKE %s")
        params.append(f"%{only}%")
    if state:
        where.append("w.state = %s")
        params.append(state.upper())
    sql = f"""
        SELECT w.school, w.state, w.url, w.direct_logo, l.override,
               l.source_url, l.etag, l.modified, l.status
        FROM   school_website w
        LEFT   JOIN school_logo l ON l.school = w.school AND l.state = w.state
        {join}
        WHERE  {' AND '.join(where)}
        ORDER  BY {rank} DESC, w.school
    """
    if limit:
        sql += " LIMIT %s"
        params.append(int(limit))
    cur.execute(sql, params)
    keys = ("school", "state", "url", "direct_logo", "override",
            "source_url", "etag", "modified", "status")
    return [tuple(r[k] for k in keys) if isinstance(r, dict) else tuple(r)
            for r in cur.fetchall()]


def writeFile(school, state, png, directory=None):
    """The PNG on disk under its derived name; returns the bare file name,
    which is what the row stores and what the site re-derives."""
    directory = directory or LOGO_DIR
    os.makedirs(directory, exist_ok=True)
    name = fileFor(school, state)
    path = os.path.join(directory, name)
    tmp = f"{path}.{os.getpid()}.tmp"
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
    """Unchanged since last time: stamp the date and nothing else."""
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
    """Flag the district crests over the WHOLE table, and clear the flag
    from anything that is no longer one."""
    cur.execute("SELECT school, sha FROM school_logo WHERE sha IS NOT NULL")
    rows = [(r["school"], r["sha"]) if isinstance(r, dict) else (r[0], r[1])
            for r in cur.fetchall()]
    shas = sharedShas(rows, minimum)
    cur.execute("UPDATE school_logo SET shared = false WHERE shared")
    if shas:
        cur.execute("UPDATE school_logo SET shared = true WHERE sha = ANY(%s)",
                    (list(shas),))
    return len(shas)


def stats(cur):
    """Where the last run went: what worked, and what every failure was.
    This is the answer to "the coverage is atrocious" -- the reason is
    stored on every row."""
    ensureTable(cur, DDL)
    out = []
    cur.execute("""SELECT count(*) FILTER (WHERE status = 'ok') AS ok,
                          count(*) FILTER (WHERE shared) AS shared,
                          count(*) AS n FROM school_logo""")
    r = cur.fetchone()
    ok, shared, n = (r["ok"], r["shared"], r["n"]) if isinstance(r, dict) else r
    out.append(f"  {ok:,} crests of {n:,} tried "
               f"({100.0 * ok / max(1, n):.1f}%), {shared:,} flagged as a district's")
    # ⚠ "196 CRESTS" IS NOT 196 PICTURES. A source that serves a default
    #   mascot for teams with no logo hands us one image over and over, and
    #   the count of rows says nothing about it. The distinct-image count
    #   does, and the biggest groups name the placeholders.
    cur.execute("""SELECT count(DISTINCT sha) AS d FROM school_logo
                   WHERE status = 'ok' AND sha IS NOT NULL""")
    distinct = _one(cur.fetchone())
    out.append(f"  {distinct:,} DISTINCT images among them "
               f"({ok - distinct:,} rows are a repeat of one already seen)")
    cur.execute("""SELECT count(DISTINCT school) AS n, min(kind) AS kind,
                          min(source_url) AS url
                   FROM   school_logo WHERE sha IS NOT NULL AND status = 'ok'
                   GROUP  BY sha HAVING count(DISTINCT school) >= %s
                   ORDER  BY 1 DESC LIMIT 5""", (SHARED_MIN,))
    top = cur.fetchall()
    if top:
        out.append("  the images worn by the most schools (placeholders live here):")
        for row in top:
            n, kind, url = ((row["n"], row["kind"], row["url"])
                            if isinstance(row, dict) else row)
            out.append(f"    {n:,} schools  {kind or '?':<20} {(url or '')[:60]}")
    cur.execute("""SELECT kind, count(*) AS n FROM school_logo
                   WHERE status = 'ok' GROUP BY kind ORDER BY n DESC""")
    out.append("  where the crest came from:")
    for row in cur.fetchall():
        k, c = (row["kind"], row["n"]) if isinstance(row, dict) else row
        out.append(f"    {(k or '?'):<28} {c:,}")
    # the sizes are collapsed so "40x40" and "48x48" land in one bucket:
    # the useful answer is "too small", not a histogram of pixel counts
    cur.execute(r"""SELECT regexp_replace(status, '\d+x\d+', 'WxH') AS why,
                           count(*) AS n
                    FROM   school_logo WHERE status <> 'ok'
                    GROUP  BY 1 ORDER BY n DESC LIMIT 25""")
    out.append("  why the rest failed:")
    for row in cur.fetchall():
        w, c = (row["why"], row["n"]) if isinstance(row, dict) else row
        out.append(f"    {(w or '?').replace('none: ', '').strip():<34} {c:,}")
    if _tableExists(cur, "school_website"):
        cur.execute("SELECT count(*) FROM school_website")
        addrs = _one(cur.fetchone())
        out.append(f"  addresses on file: {addrs:,} "
                   f"(build_school_websites.py; no address, no crest)")
    out += coverageByImportance(cur)
    return "\n".join(out)


def _one(row):
    if row is None:
        return 0
    return row[0] if not isinstance(row, dict) else list(row.values())[0]


def coverageByImportance(cur):
    """★ THE NUMBER THAT ACTUALLY MATTERS. "2,603 of 110,000" is not the
    site's coverage: it counts every middle school and club the corpus has
    ever seen equally with the programmes that appear on every other page.
    A crest is worth having where a school is NAMED, so the honest measure
    is coverage among the biggest programmes -- which is also the order the
    scraper works in, so --limit reads straight off this."""
    if not _tableExists(cur, "school_identity"):
        return ["  (school_identity is missing: no importance to weigh by)"]
    out = ["  coverage where it counts, by athlete count:"]
    cur.execute("SELECT count(*) FROM school_identity WHERE n_athletes >= 3")
    total = _one(cur.fetchone())
    for band in (500, 2000, 10000, None):
        cur.execute("""
            WITH top AS (SELECT school, state FROM school_identity
                         WHERE n_athletes >= 3
                         ORDER BY n_athletes DESC
                         LIMIT %s)
            SELECT count(*) FILTER (WHERE l.status = 'ok' AND NOT l.shared) AS ok,
                   count(*) FILTER (WHERE w.school IS NOT NULL) AS addressed,
                   count(*) AS n
            FROM   top
            LEFT   JOIN school_logo l ON l.school = top.school AND l.state = top.state
            LEFT   JOIN school_website w ON w.school = top.school AND w.state = top.state
        """, (band if band else total or 1,))
        r = cur.fetchone()
        ok, addressed, n = ((r["ok"], r["addressed"], r["n"])
                            if isinstance(r, dict) else r)
        label = f"top {band:,}" if band else f"all {n:,}"
        out.append(f"    {label:<12} {ok:,} of {n:,} have a crest "
                   f"({100.0 * ok / max(1, n):5.1f}%), "
                   f"{addressed:,} have an address "
                   f"({100.0 * addressed / max(1, n):5.1f}%)")
    return out


# ===================================================================== #
#  RUN                                                                  #
# ===================================================================== #

def workOne(manners, row, rediscover=False):
    """One school, entirely in a worker thread: no database, no disk. The
    main thread does every write, because a psycopg2 connection is not
    thread-safe and a torn row is worse than a slow one.

    ! IT CANNOT RAISE. One malformed page must not end a two-hour run, so
      anything unexpected becomes this school's failure reason."""
    try:
        return _workOne(manners, row, rediscover)
    except Exception as exc:                              # noqa: BLE001
        return {"school": row[0], "state": row[1], "png": None,
                "why": f"crashed {type(exc).__name__}"}


def _workOne(manners, row, rediscover=False):
    (school, state, url, direct, override,
     had_url, etag, modified, status0) = row
    override_url = override if (override or "").startswith("http") else None
    # ⚠ --redo MUST NOT TAKE THE CHEAP PATH. The refresh asks the file we
    #   kept last time and a 304 ends it -- which is right for a quarterly
    #   run and exactly wrong after the PICKING has changed: every school
    #   that already has its institutional logo would keep it, and never be
    #   offered the athletics one.
    if had_url and status0 == "ok" and not override_url and not rediscover:
        again = refetch(manners, had_url, etag, modified)
        if again is UNCHANGED:
            return {"school": school, "state": state, "unchanged": True}
        if again:
            return {"school": school, "state": state, "png": again[0],
                    "sha": again[1], "kind": "refresh", "src": had_url,
                    "etag": manners.etag, "modified": manners.modified}
    target = override_url or direct or url
    png, sha, kind, src = fetchLogo(manners, url,
                                    direct=(target if target != url else None))
    return {"school": school, "state": state, "png": png, "sha": sha,
            "kind": kind, "src": src if png else None,
            "why": None if png else src,
            "etag": manners.etag if png else None,
            "modified": manners.modified if png else None}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help="store rows and files")
    ap.add_argument("--dry-run", action="store_true",
                    help="fetch and report, write no row and no file")
    ap.add_argument("--stats", action="store_true",
                    help="what the last run found and why the rest failed; no network")
    ap.add_argument("--limit", type=int, default=None,
                    help="the biggest N programmes still to do")
    ap.add_argument("--only", default=None, help="schools whose name contains this")
    ap.add_argument("--state", default=None)
    ap.add_argument("--workers", type=int, default=WORKERS,
                    help=f"schools fetched at once, across different hosts (default {WORKERS})")
    ap.add_argument("--rate", type=float, default=1.0,
                    help="seconds between requests TO ONE HOST (default 1)")
    ap.add_argument("--refresh-days", type=int, default=REFRESH_DAYS)
    ap.add_argument("--retry-failed", action="store_true",
                    help="also re-ask the schools that yielded nothing last time")
    ap.add_argument("--redo", action="store_true",
                    help="re-ask everyone, whatever they answered last time")
    ap.add_argument("--dir", default=None, help="where the PNGs go (default XCP_LOGO_DIR)")
    ap.add_argument("--sweep-only", action="store_true",
                    help="re-run the shared-crest sweep and stop")
    args = ap.parse_args()
    if args.redo:
        # -1, not 0: "fetched < today - 0" skips everything fetched TODAY,
        # which is exactly the row you are trying to redo an hour later
        args.refresh_days, args.retry_failed = -1, True
    if not (args.write or args.dry_run or args.stats or args.sweep_only):
        ap.error("pass --stats, --dry-run, --write or --sweep-only")

    from database import getConn
    with getConn() as conn:
        with conn.cursor() as cur:
            if args.stats:
                print(stats(cur))
                return
            if args.sweep_only:
                ensureTable(cur, DDL)
                n = markShared(cur)
                conn.commit()
                print(f"  shared crests: {n:,} images worn by {SHARED_MIN}+ schools")
                return
            todo = targets(cur, args.refresh_days, args.only, args.state,
                           args.limit, args.retry_failed)
            conn.commit()
            print(f"  {len(todo):,} schools, biggest programme first, "
                  f"{args.workers} at a time, {args.rate}s per host", flush=True)
            if not todo:
                return

            manners = Manners(rate=args.rate)
            got = same = skipped = 0
            t0 = time.time()
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                for i, res in enumerate(
                        pool.map(lambda r: workOne(manners, r, args.redo), todo), 1):
                    if res.get("unchanged"):
                        same += 1
                        if args.write:
                            touch(cur, res["school"], res["state"])
                    elif res.get("png"):
                        got += 1
                        if args.write:
                            name = writeFile(res["school"], res["state"],
                                             res["png"], args.dir)
                            record(cur, res["school"], res["state"], name,
                                   res["src"], res["kind"], res["sha"], "ok",
                                   res["etag"], res["modified"])
                    else:
                        skipped += 1
                        if args.write:
                            record(cur, res["school"], res["state"], None, None,
                                   None, None, f"none: {res['why']}"[:60])
                    if args.write and i % 100 == 0:
                        conn.commit()
                    if i % 100 == 0 or args.dry_run:
                        rate = i / max(1e-9, time.time() - t0)
                        left = (len(todo) - i) / max(1e-9, rate) / 3600
                        print(f"  [{i:,}/{len(todo):,}] {got:,} kept, "
                              f"{same:,} unchanged, {skipped:,} without · "
                              f"{rate * 3600:,.0f}/h, {left:.1f} h left · "
                              f"last: {res['school']} "
                              f"{res.get('kind') or res.get('why')}", flush=True)
            if args.write:
                n = markShared(cur)
                conn.commit()
                print(f"  shared crests: {n:,} images worn by {SHARED_MIN}+ schools")
            print(f"  done: {got:,} crests, {same:,} unchanged, {skipped:,} without, "
                  f"{manners.requests:,} requests in {(time.time() - t0) / 60:.1f} min")
            if manners.insecureHosts:
                print(f"  {len(manners.insecureHosts):,} hosts had a broken "
                      f"certificate and were read unverified, e.g. "
                      f"{', '.join(sorted(manners.insecureHosts)[:5])}")


if __name__ == "__main__":
    main()
