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
#  DOES THIS HOST BELONG TO THIS SCHOOL?                                #
# ===================================================================== #
#
# ★ THE BUG THIS EXISTS FOR (owner, 2026-09-17: "it was giving random ass
#   pictures that are not on anet. Lawrence hs got the world athletics
#   picture?"). The scraper does not invent a crest: candidates come from
#   school_website, it fetches that URL, and it takes whatever the page
#   DECLARES as its mark. Point it at the wrong domain and it faithfully
#   returns that domain's logo. So the defect is upstream, in the address
#   book -- but the crest is where it shows, and this is the cheapest place
#   to catch it.
#
# ! markShared WAS THE ONLY DEFENCE, AND IT IS NOT ENOUGH. It hides an image
#   worn by SHARED_MIN (4) or more schools, so one governing body's logo on
#   three schools sails through -- and it only ever fires AFTER the bad crest
#   is already stored and served.
#
# ★ THE TEST IS THE SCHOOL'S OWN NAME IN THE DOMAIN, or a host that is
#   self-evidently a school's. A domain has to earn the crest:
#     - a real name token of the school appears in the registrable domain, or
#     - the host is educational or governmental (.edu, .k12.*.us, .sch.*,
#       *.gov), or
#     - it is plainly a district or school host (contains "school",
#       "district", "isd", "usd", "academy", ...)
#   Everything else is refused, which costs nothing: a school whose real site
#   is unrecognisable simply keeps no crest, and no crest beats a wrong one.
#
# ⚠ NOT APPLIED TO anet. An athletic.net crest is fetched by TEAM ID, so it is
#   already tied to the right team by construction; the name test would refuse
#   every one of them because the host is athletic.net.
#
# ⚠⚠ AND NOT APPLIED TO AN ADDRESS anet RESOLVED EITHER (owner, 2026-09-17:
#    "Penn state still has no logo at all"). This is the same construction one
#    step earlier, and missing it threw away most of the college corpus.
#
#    anet_teams.storeAddress writes anet's `WebsiteSport` -- the ATHLETICS
#    site, handed over per team id -- into school_website with source 'anet'.
#    A college athletics site is branded by its MASCOT, not by its school:
#    gopsusports.com, goducks.com, rolltide.com, guhoyas.com, gohuskies.com,
#    hawkeyesports.com, und.com, cuse.com, byucougars.com. Not one carries a
#    name token, none is .edu, and none reads as "schooly" -- so the name test
#    refuses 10 of 15 real programmes, measured
#    (tests/test_school_logo_host.py). Penn State's own row is
#    "address is not this school's (https://gopsusports.com/)", and then no
#    crest at all.
#
#    The same trade the crest ranking already made: RESOLUTION LOSES TO
#    PROVENANCE. This test exists to catch a WRONG ADDRESS-BOOK ROW -- a
#    guessed one, from Wikidata or a search -- and an address that came back
#    from anet keyed on the team, or that a person typed, is not a guess. So
#    the NAME test is skipped for those, and the blocklist below is not: if
#    anet's WebsiteSport points at a hosting vendor or a governing body, that
#    is still nobody's crest.
#
# ! fetchLogo ALREADY RELIES ON THIS REASONING one level down -- the athletics
#   site it finds by following a link from the school's own page is fetched
#   without a name test, "because it is linked FROM the school's own page,
#   which is the evidence". An anet team id is the better evidence.
_TRUSTED_ADDRESS = {"anet", "manual", "override"}

# hosts that are somebody's logo but never a school's
_GENERIC_HOSTS = {
    "worldathletics.org", "iaaf.org", "olympics.com", "teamusa.org",
    "usatf.org", "ncaa.org", "ncaa.com", "nfhs.org", "naia.org", "njcaa.org",
    "milesplit.com", "maxpreps.com", "athletic.net", "tfrrs.org",
    "runnerspace.com", "flosports.tv", "flotrack.org", "hudl.com",
    "8to18.com", "rankonesport.com", "schedulegalaxy.com", "arbitersports.com",
    "wordpress.com", "squarespace.com", "wixsite.com", "weebly.com",
    "godaddy.com", "gstatic.com", "googleusercontent.com", "cloudflare.com",
}

# words that say nothing about WHICH school this is
_NAME_STOPWORDS = {
    "high", "school", "schools", "hs", "middle", "ms", "elementary", "junior",
    "senior", "academy", "college", "university", "institute", "the", "of",
    "and", "at", "saint", "st", "mount", "mt", "north", "south", "east",
    "west", "central", "county", "district", "area", "regional", "public",
    "charter", "prep", "preparatory", "catholic", "christian", "lutheran",
    "community", "unified", "consolidated", "township", "city", "new",
    "old", "upper", "lower", "great", "fort", "ft", "lake", "valley", "park",
}

# a host that is self-evidently a school's, whatever its name
#
# ⚠ THE SHORT ONES ARE NOT SUBSTRINGS. `stem` runs the host's labels
#   together, so a bare "sd" matched inside any longer word and admitted
#   newsdaily.com, wisdomtree.com, sportsdesk.net and kidsdirect.org to a
#   guard whose whole job is refusing domains like those. A district code is
#   a WORD in a hostname -- lawrence-usd497.org, sd44.bc.ca, cusd.org -- so
#   it is matched as a label or a hyphen/digit-delimited part of one, while
#   the long words stay substrings because they cannot collide by accident.
_SCHOOLY = ("school", "district", "academy", "collegiate", "univ", "college")
_SCHOOLY_CODES = ("isd", "usd", "csd", "sd")


def _schoolyHost(host):
    """Does this host read as a school's or a district's? Pure."""
    labels = host.split(".")
    stem = "".join(labels[:-1])          # everything but the TLD, run together
    if any(word in stem for word in _SCHOOLY):
        return True
    # the district codes as WORDS: each label split on hyphens and on the
    # digit runs that carry the district number (usd497, sd44). A code is
    # also allowed as the tail of a short acronym, which is how most real
    # ones are spelled -- cusd, pusd, ccsd -- while staying far away from
    # "newsdaily" and "wisdomtree".
    parts = set()
    for label in labels[:-1]:
        parts.update(p for p in re.split(r"[^a-z]+", re.sub(r"\d+", " ", label))
                     if p)
    return any(p == code or (len(p) <= 5 and p.endswith(code))
               for p in parts for code in _SCHOOLY_CODES)


def _registrable(host):
    """The last two labels of a host, lowercased and portless -- the same
    reduction _isSocial uses, so "x.com" cannot match phoenix.com."""
    return ".".join((host or "").lower().split(":")[0].split(".")[-2:])


def nameTokens(school):
    """The words of a school name that identify WHICH school it is: at least
    four letters and not a stopword. Pure."""
    words = re.split(r"[^a-z0-9]+", (school or "").lower())
    return {w for w in words if len(w) >= 4 and w not in _NAME_STOPWORDS}


def plausibleHost(school, url, kind=None, source=None):
    """Whether `url` is plausibly this school's own site. Pure; no network.

    Returns True for an anet crest whatever the host -- see the note above.

    `source` is the PROVENANCE of the address (school_website.source): one of
    _TRUSTED_ADDRESS means it was resolved by anet's team id or set by hand,
    so only the blocklist applies and the school's name need not appear in
    the domain. Anything else -- a guess -- must still earn the crest.
    """
    if (kind or "").split(":")[0] == "anet":
        return True
    try:
        host = urllib.parse.urlsplit(url).hostname or ""
    except ValueError:
        return False
    if not host:
        return False
    host = host.lower()
    if _registrable(host) in _GENERIC_HOSTS or _isSocial(host):
        return False
    # ★ THE ADDRESS WAS NOT GUESSED: the name test has nothing to add
    if str(source or "").strip().lower() in _TRUSTED_ADDRESS:
        return True
    # educational or governmental: nobody else gets these
    if re.search(r"\.(edu|edu\.[a-z]{2}|gov)$", host) or \
            re.search(r"\.k12\.[a-z]{2}\.us$", host) or \
            re.search(r"\.sch\.[a-z]{2}$", host):
        return True
    stem = "".join(host.split(".")[:-1])  # everything but the TLD, run together
    if any(tok in stem for tok in nameTokens(school)):
        return True
    return _schoolyHost(host)


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


# ★ A FLAT BACKGROUND COMES OFF, WHATEVER COLOUR IT IS (owner, 2026-09-16:
#   "the backgrounds are black but the background on anet are white, so idk
#   where the black is coming from (I also don't like it)").
#
#   The black is in the FILE, not in the page: .school-mark already sets
#   `background: #fff`, so a crest with real transparency renders white.
#   What puts an opaque ground there is the source: a mascot that was stored
#   as a transparent PNG and came back as a JPEG has had its alpha flattened
#   by whoever re-encoded it, and a flatten with no colour given is BLACK.
#   anet's own page shows the untouched original, which is why theirs looks
#   white and ours does not.
#
# ⚠ AND IT SILENTLY BROKE THE CROP TOO. normalise promises "transparent
#   margins come off", and getbbox() can only crop what is transparent -- so
#   for every opaque source the margin stayed, the mark was shrunk to fit a
#   box it was already inside, and a logo with a wide ground rendered tiny
#   at 18 px. Keying the ground out fixes the crop and the colour together.
#
# ! ONLY A GROUND ALL FOUR CORNERS AGREE ON, and only on an image that has
#   no alpha of its own. A mark that reaches its own corners has no flat
#   ground to key, and disagreeing corners mean a photograph or a gradient,
#   where keying one colour would punch holes in it. TOLERANCE is tight for
#   the same reason: a JPEG's ground is not one exact value, but it is
#   within a few levels of itself.
# ⚠ MEASURED AGAINST REAL FILES, NOT CHOSEN (2026-09-17, the owner's
#   --worst scan). These were 16 and 10, and 10 was too tight to be useful:
#
#       015113a0  corners (9,13,22) (0,4,7) (11,11,11) (8,8,8)
#
#   -- one flat near-black ground by eye, refused because its channels
#   differ by 11. A JPEG's flat ground is not one exact value; it is a few
#   levels of noise around one. 28 keeps the four corners of a REAL gradient
#   apart (the scan's (41,96,150)/(0,66,126) blue spreads 41) while
#   accepting the noise, and the tolerance has to be at least as wide as the
#   spread it just accepted or the corners themselves survive the key.
KEY_TOLERANCE = 24          # per channel, against the corners' mean
KEY_MAX_SPREAD = 28         # the corners must agree this closely

# ★ A GROUND HAS SOMETHING ON IT. Once the probe moved to the corners of the
#   OPAQUE region, a crest that arrived transparent started reporting a
#   "ground" -- the colour of its own mark, because the opaque region IS the
#   mark and all four of its corners agree. Keying that empties the crest.
#   So a candidate colour that accounts for essentially the whole opaque
#   region is not a ground: there is no mark inside it to keep.
GROUND_MAX_SHARE = 0.995


def _keyGround(im, ground, tol=KEY_TOLERANCE):
    """`im` with every pixel within `tol` of `ground` made transparent.
    Returns (image, pixels keyed)."""
    lo = tuple(max(0, c - tol) for c in ground)
    hi = tuple(min(255, c + tol) for c in ground)
    r, g, b, a = im.split()
    from PIL import Image, ImageChops

    def band(ch, i):
        return ch.point(lambda v, i=i: 255 if lo[i] <= v <= hi[i] else 0)

    mask = ImageChops.multiply(ImageChops.multiply(band(r, 0), band(g, 1)),
                              band(b, 2))
    # ! getdata() is deprecated in Pillow 12 and gone in 14; a histogram
    #   counts the same thing and is faster
    n = mask.histogram()[255] if mask.mode == "L" else 0
    if not n:
        return im, 0
    keyed = im.copy()
    keyed.putalpha(ImageChops.subtract(a, mask))
    return keyed, n


def _opaqueBox(im):
    """The bounding box of the pixels that are actually opaque, or None.

    ★ WHY THE CANVAS CORNERS WERE THE WRONG PLACE TO LOOK, and this is the
      black-background bug (owner, 2026-09-16: "the backgrounds are black but
      the background on anet are white, so idk where the black is coming
      from"). normalise centres a crest on a TRANSPARENT square. So a logo
      that arrives as a dark card narrower than it is tall -- or one that has
      already been through here once -- is an opaque black rectangle with
      transparent margins beside it, and the canvas corners are those
      margins: alpha 0, so _flatGround bailed with "it already has its own
      alpha" and the ground it was looking at went untouched. Probing the
      corners of the OPAQUE REGION finds it.

    ! FOR A FULLY OPAQUE IMAGE THIS IS THE WHOLE IMAGE, so nothing about the
      square case changes.
    """
    try:
        alpha = im.getchannel("A")
    except (ValueError, KeyError):
        return (0, 0) + im.size
    return alpha.point(lambda v: 255 if v >= 250 else 0).getbbox()


def _flatGround(im):
    """The (r, g, b) of a flat background every corner of the opaque region
    agrees on, or None. Pure; `im` is RGBA."""
    box = _opaqueBox(im)
    if not box:
        return None                      # nothing opaque: no ground to key
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    if w < 4 or h < 4:
        return None
    px = im.load()
    corners = [px[x0, y0], px[x1 - 1, y0], px[x0, y1 - 1], px[x1 - 1, y1 - 1]]
    if any(c[3] < 250 for c in corners):
        return None                      # a ragged edge, not a card
    mean = tuple(sum(c[i] for c in corners) // 4 for i in range(3))
    for c in corners:
        if max(abs(c[i] - mean[i]) for i in range(3)) > KEY_MAX_SPREAD:
            return None                  # a gradient or a photograph
    # ! AND IT HAS TO SURROUND SOMETHING. See GROUND_MAX_SHARE.
    inside = im.crop(box)
    _keyed, n = _keyGround(inside, mean)
    if n >= GROUND_MAX_SHARE * (w * h):
        return None                      # all ground, no mark
    return mean


def normalise(raw, px=LOGO_PX, ctype="", kind=None):
    """(png_bytes, sha256, (w, h)) for a fetched image, or (None, None,
    reason). A flat opaque ground is keyed out, transparent margins come
    off, and the mark is centred in a square of `px` on a transparent
    ground, so every crest the site draws is the same box whatever shape it
    arrived in."""
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
    return finish(im.convert("RGBA"), px=px, kind=kind)


def finish(im, px=LOGO_PX, kind=None):
    """(png_bytes, sha256, (w, h)) for an RGBA image, or (None, None, reason).

    Key the ground out, crop the transparent margin, centre what is left in a
    square of `px`. Split out of normalise so the repair pass can run the
    same steps over a crest already on disk -- the ground is IN the stored
    PNG, so fixing it needs no network and no rescrape.
    """
    from PIL import Image, ImageOps
    # ⚠ AND ONLY WHEN SOMETHING SURVIVES IT. An image that is ENTIRELY its
    #   ground -- a solid block, a one-colour badge -- has no ground to key:
    #   keying it leaves nothing, every colour normalises to the same empty
    #   square, and the district sweep would then see a thousand schools
    #   wearing one crest (tests/test_school_logos: "the same bytes hash the
    #   same and different ones do not"). So the key is kept only if what is
    #   left is still a usable mark, and reverted otherwise.
    ground = _flatGround(im)
    if ground is not None:
        keyed, n = _keyGround(im, ground)
        kbox = keyed.getbbox() if n else None
        if kbox and acceptable(kbox[2] - kbox[0], kbox[3] - kbox[1], kind):
            im = keyed
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
#  THE REPAIR: re-key the grounds already on disk                       #
# ===================================================================== #
#
# ★ NO NETWORK, NO RESCRAPE (owner, 2026-09-16: "I stopped the scrape until we
#   can fix amherst type issues (can we rescrape them?)" -- and the answer for
#   the black grounds is that we do not have to). The ground is baked into the
#   stored PNG, and keying a FLAT ground out of an image cannot damage the
#   mark on it: the mark is by definition the pixels that are not that colour.
#   So the repair reads each file, runs `finish` over it again, and writes it
#   back when the result is smaller in ink and still an acceptable mark.
#
# ! IDEMPOTENT. A crest with no flat ground comes back byte-identical, so the
#   pass can be run as often as you like and a second run is a no-op.
def regroundBytes(raw, px=LOGO_PX, kind=None):
    """(new_png, sha, reason) for a stored crest, or (None, None, reason) when
    there is nothing to do. `reason` is why, either way."""
    try:
        from PIL import Image
    except ImportError:                                   # pragma: no cover
        return None, None, "no Pillow"
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
        im = im.convert("RGBA")
    except Exception:                                     # noqa: BLE001
        return None, None, "unreadable"
    ground = _flatGround(im)
    if ground is None:
        return None, None, "no flat ground"
    data, sha, size = finish(im, px=px, kind=kind)
    if data is None:
        return None, None, f"would not survive ({size})"
    if data == raw:
        return None, None, "unchanged"
    return data, sha, f"keyed rgb{ground}"


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


def fetchLogo(manners, home_url, direct=None, school=None, source=None):
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
    # ★ THE HOST HAS TO EARN IT (owner: "it was giving random ass pictures").
    #   Checked BEFORE the request, not after: a host that cannot be this
    #   school's is not worth a fetch either. `school` is optional so a caller
    #   that has no name to check against behaves exactly as before.
    def mine(url, kind=None):
        return school is None or plausibleHost(school, url, kind, source)

    if direct and mine(direct, "direct"):
        raw, ctype = manners.get(direct)
        png, sha, why = normalise(raw, ctype=ctype) if raw is not None else (None, None, ctype)
        if png is not None:
            return png, sha, "direct", direct
        if not home_url:
            return None, None, None, f"logo {why}"
    elif direct and not home_url:
        return None, None, None, "logo host is not this school's"
    if not home_url:
        return None, None, None, "no address"

    text, home, why = _fetchPage(manners, home_url, "school", variants=True)
    if text is None:
        return None, None, None, why

    # ! AND THE PAGE WE LANDED ON. A redirect or a wrong address book entry
    #   means `home` is not this school at all, and every icon it declares is
    #   somebody else's mark -- which is how a high school ended up wearing
    #   the World Athletics logo.
    if not mine(home, "school"):
        return None, None, None, f"address is not this school's ({home})"

    ath_url = athleticsLink(text, home)
    if ath_url:
        ath_text, ath, _why = _fetchPage(manners, ath_url, "athletics")
        # an athletics site on a vendor host is normal and fine -- it is
        # linked FROM the school's own page, which is the evidence
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

# ★ `level` IS THE THIRD PART OF A SCHOOL'S IDENTITY (owner, 2026-09-16:
#   "The anet pools should match our school pools. If they don't, separate
#   them"). (Amherst, MA) is Amherst College AND Amherst Regional High
#   School; one key held one crest, so whichever mascot landed there was
#   wrong for the other -- and anet's row-count-modal team for the pair is
#   the high school, which is how the college came to wear the Falcons.
#   '' means "any level", which is all a match by NAME from the school's own
#   website can honestly claim.
#
# ⚠ NO SQL COMMENTS INSIDE THIS STRING. ensureTable derives an
#   ADD COLUMN IF NOT EXISTS from every line of the body (that is the point
#   -- the migration cannot drift from the DDL), and _ddlColumns reads a
#   `--` line as a column named `--`. Three of them, and the first ALTER on
#   a real server is a syntax error. Notes go here instead.
DDL = """
CREATE TABLE IF NOT EXISTS school_logo (
    school     text NOT NULL,
    state      text NOT NULL DEFAULT '',
    level      text NOT NULL DEFAULT '',
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
    PRIMARY KEY (school, state, level))
"""

# ⚠ ensureTable ADDS COLUMNS, NEVER KEYS. A table made before the level
#   existed keeps PRIMARY KEY (school, state), and then the first INSERT
#   with ON CONFLICT (school, state, level) fails -- on the server,
#   mid-run, with no matching unique constraint. Migrated here, once,
#   idempotently: the old key is dropped and the three-column one added,
#   which is safe because every existing row has level '' and was already
#   unique on (school, state).
def ensureLevelKey(cur):
    """Move school_logo's primary key to (school, state, level). True when
    it moved it.

    ! IN A SAVEPOINT, AND IT NEVER RAISES. This runs at the top of every
      job, including the ones that only READ, so a probe it cannot make
      sense of must mean "leave the key alone" rather than end the run --
      and a failed ALTER must not poison the caller's transaction, which is
      what an un-savepointed error does to every statement after it.
    """
    try:
        cur.execute("SAVEPOINT school_logo_key")
        # ⚠ THE CONSTRAINT'S REAL NAME, NOT THE ONE POSTGRES USUALLY PICKS.
        #   A table restored from a dump, or created by an older hand, can
        #   carry any name -- and then DROP CONSTRAINT IF EXISTS
        #   school_logo_pkey finds nothing, ADD PRIMARY KEY fails because
        #   there already is one, and every INSERT afterwards dies on
        #   "no unique or exclusion constraint matching the ON CONFLICT".
        #   pg_constraint knows the name and the column count.
        cur.execute("""
            SELECT conname, array_length(conkey, 1) AS n
            FROM   pg_constraint
            WHERE  conrelid = 'school_logo'::regclass AND contype = 'p'
        """)
        row = cur.fetchone()
        if row is None:                        # no primary key at all
            name, cols = None, 0
        elif isinstance(row, dict):
            name, cols = row["conname"], int(row["n"])
        else:
            name, cols = row[0], int(row[1])
        if cols >= 3:
            cur.execute("RELEASE SAVEPOINT school_logo_key")
            return False
        cur.execute("UPDATE school_logo SET level = '' WHERE level IS NULL")
        if name:
            cur.execute(f'ALTER TABLE school_logo DROP CONSTRAINT "{name}"')
        cur.execute("ALTER TABLE school_logo "
                    "ADD PRIMARY KEY (school, state, level)")
        cur.execute("RELEASE SAVEPOINT school_logo_key")
        return True
    except Exception:                              # noqa: BLE001
        try:
            cur.execute("ROLLBACK TO SAVEPOINT school_logo_key")
        except Exception:                          # noqa: BLE001
            pass
        return False


SHA_INDEX = "CREATE INDEX IF NOT EXISTS idx_school_logo_sha ON school_logo (sha)"


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


# ★ THE PAIRS ANET DAMAGED, AND YES THEY CAN BE RE-ASKED (owner,
#   2026-09-16: "I stopped the scrape until we can fix amherst type issues
#   (can we rescrape them?)").
#
#   A crest is never one-way: the row is a file name and a hash, and asking
#   the school's own site again replaces both. What to re-ask is knowable
#   exactly -- a (school, state) that holds MORE THAN ONE INSTITUTION
#   (school_level, two or more non-bucket levels) whose stored crest is
#   anet's mascot filed under NO level. That is the shape of the damage:
#   anet's modal team for the pair is the bigger institution, its mascot
#   went in as the pair's one crest, and "anet wins" replaced whatever the
#   colleges' own athletics sites had given.
#
# ! IT RETURNS PAIRS, NOT NAMES, so a district with one good crest and one
#   bad one is not re-asked wholesale.
def damagedPairs(cur):
    """[(school, state)] whose crest is an anet mascot on a pair holding
    two institutions. Empty without school_level."""
    if not _tableExists(cur, "school_level"):
        return []
    cur.execute("""
        WITH multi AS (
            SELECT school, state FROM school_level
            WHERE  NOT is_bucket
            GROUP  BY school, state HAVING count(*) >= 2
        )
        SELECT l.school, l.state
        FROM   school_logo l JOIN multi m
               ON m.school = l.school AND m.state = l.state
        WHERE  l.kind = 'anet' AND COALESCE(l.level, '') = ''
          AND  l.path IS NOT NULL
        ORDER  BY l.school, l.state
    """)
    return [((r["school"], r["state"]) if isinstance(r, dict) else (r[0], r[1]))
            for r in cur.fetchall()]


def suspectPairs(cur, verbose=True):
    """[(school, state)] whose stored crest is NOT evidence of that school.

    ★ THE OWNER'S REPORT (2026-09-18): "it seems it's taken some logos not
      actually from the anet site (or it's taken them from a similar named
      school). This is interesting bcs the team id and name resolved correctly
      otherwise. Is there some way to only scrape these subsets so I don't have
      to keep hammering anet?"

      Yes, and the answer is already in the rows. `kind` records WHERE a crest
      came from. A kind starting 'anet' is the team page for a team id we
      resolved -- that is the school itself saying so, and those are the ones
      the owner is not complaining about. Every other kind came off a school
      WEBSITE, chosen by matching the school's name against a domain, and a
      loose match there is exactly how a neighbour with a similar name hands
      over its logo. The team id being right is no protection: it was never
      consulted for those.

    Two populations, reported separately:

      off-anet   status ok, kind not 'anet*' -- a website crest, re-askable
                 from anet's team page, which is the better evidence.
      implausible  the stored source_url no longer passes plausibleHost for
                 this school. These are rows written before the host rule
                 tightened, and they are the "similar named school" ones.

    ! PURE, AND NO NETWORK. plausibleHost is a string test, so the whole
      suspect set is computed from the table in one query.
    """
    cur.execute("""
        SELECT school, state, level, kind, source_url
        FROM   school_logo
        WHERE  status = 'ok' AND path IS NOT NULL
          AND  COALESCE(lower(override), '') <> 'none'
        ORDER  BY school, state
    """)
    rows = [r if isinstance(r, dict) else
            {"school": r[0], "state": r[1], "level": r[2], "kind": r[3],
             "source_url": r[4]}
            for r in cur.fetchall()]

    off_anet, implausible = [], []
    for r in rows:
        kind = (r.get("kind") or "")
        if kind.split(":")[0] == "anet":
            continue                      # the school's own team page: keep
        pair = (r["school"], r["state"])
        off_anet.append(pair)
        if not plausibleHost(r["school"], r.get("source_url") or "",
                             kind, None):
            implausible.append(pair)

    # ! DEDUPED ACROSS LEVELS. school_logo is keyed (school, state, level) and
    #   targets() matches on (school, state), so a school with a crest per
    #   level would otherwise be queued several times.
    seen, pairs = set(), []
    for pair in implausible + off_anet:
        if pair not in seen:
            seen.add(pair)
            pairs.append(pair)

    if verbose:
        print(f"  {len(rows):,} crests stored")
        print(f"  {len(off_anet):,} came from a school website rather than "
              f"anet's team page")
        print(f"  {len(set(implausible)):,} of those have a host that no "
              f"longer passes the name test -- the likeliest wrong ones")
        print(f"  {len(pairs):,} schools to re-ask (deduped across levels), "
              f"worst first")
    return pairs


def targets(cur, refresh_days=REFRESH_DAYS, only=None, state=None, limit=None,
            retry_failed=False, pairs=None):
    """The schools still to do, BIGGEST PROGRAMME FIRST.

    ★ THE ORDER IS THE POINT. Alphabetical spent the first hour on
      academies with four athletes. Descending athlete count means --limit
      2000 covers the schools that appear on most pages of the site, and
      the long tail can run overnight or never.

    ⚠ A SCHOOL THAT YIELDED NOTHING IS NOT TRIED AGAIN until the refresh
      window is up: `fetched` is stamped on a failure too. --retry-failed
      (or --redo) when the picking has changed and it is worth re-asking."""
    ensureTable(cur, DDL)
    ensureLevelKey(cur)
    cur.execute(SHA_INDEX)
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
    if pairs:
        # (school, state) pairs, as a VALUES list rather than two ANYs: the
        # cross product of the two columns is not the same set
        where.append("(w.school, w.state) IN (SELECT school, state FROM "
                     "unnest(%s::text[], %s::text[]) AS t(school, state))")
        params.append([p[0] for p in pairs])
        params.append([p[1] for p in pairs])
    # ! AND WHERE THE ADDRESS CAME FROM. An address anet resolved by team id
    #   is not a guess, and the host check must not treat it as one -- see
    #   plausibleHost. Selected LAST so every existing positional unpack of
    #   this row is unchanged.
    sql = f"""
        SELECT w.school, w.state, w.url, w.direct_logo, l.override,
               l.source_url, l.etag, l.modified, l.status, w.source
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
            "source_url", "etag", "modified", "status", "source")
    return [tuple(r[k] for k in keys) if isinstance(r, dict) else tuple(r)
            for r in cur.fetchall()]


def writeFile(school, state, png, directory=None, level=None):
    """The PNG on disk under its derived name; returns the bare file name,
    which is what the row stores and what the site re-derives."""
    directory = directory or LOGO_DIR
    os.makedirs(directory, exist_ok=True)
    name = fileFor(school, state, level)
    path = os.path.join(directory, name)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "wb") as fh:
        fh.write(png)
    os.replace(tmp, path)
    return name


# ★ WHICH CREST WINS WHEN TWO SOURCES BOTH HAVE ONE (owner, 2026-09-13:
#   "if they differ from the previously scraped ones do we take the new
#   ones?"). Not blindly, no. Best first:
#
#     override    a person said so
#     athletics   the school's OWN athletics site: the real mark, at the
#                 resolution the school publishes it
#     anet        the athletics mark too, but one small mascot image, and
#                 a placeholder for any team that never uploaded one
#     direct      Wikidata's logo file: usually institutional
#     school      the school's main site: the institutional wordmark
#
#   A later run only replaces a crest with one of the same rank or better,
#   so re-running anet over a corpus that already has athletics-site
#   crests improves the gaps and leaves the good ones alone. --replace
#   overrides the rule where a caller means to.
# ★ anet OUTRANKS THE OPEN WEB NOW (owner, 2026-09-17: "Bro just take the
#   anet one please"). It was second to a school's own athletics site, which
#   has the better image -- higher resolution, the real athletics mark rather
#   than one small mascot. The trade was wrong:
#
#     - an anet crest is fetched BY TEAM ID, so it cannot be the wrong
#       school's. Everything from the open web is only as good as the
#       school_website row that pointed at it, and a wrong row returns that
#       domain's logo faithfully -- which is how a high school ended up
#       wearing the World Athletics mark.
#     - anet covers every team in the corpus, because the corpus IS anet.
#       A school website has to be found, reached and parsed first.
#
#   So resolution loses to provenance. plausibleHost now refuses the worst of
#   the open web, but "refused" still costs a request and a judgement call per
#   school, and anet needs neither.
#
# ! athletics IS STILL KEPT WHERE IT IS ALREADY STORED, and still beats
#   `direct` and `school`. This changes which one WINS when both exist, not
#   whether the others are worth having -- an anet placeholder for a team that
#   never uploaded a mascot is worse than a real athletics-site crest. The
#   defence there is markShared: a placeholder is by definition worn by many
#   schools, so SHARED_MIN catches it and the site draws nothing.
KIND_RANK = {"override": 0, "anet": 1, "refresh": 1, "athletics": 2,
             "direct": 3, "school": 4}


def kindRank(kind):
    """The rank of a stored kind. Kinds are "<where>:<tag>" for the web
    scraper ("athletics:apple-touch") and bare for the rest."""
    return KIND_RANK.get(str(kind or "").split(":")[0], 5)


def sharedAlready(cur, sha, minimum=SHARED_MIN):
    """True when this exact image is already worn by `minimum` schools --
    a placeholder, and not worth installing on one more. The sweep would
    flag it afterwards anyway; this stops it being written at all."""
    if not sha:
        return False
    # ! FAMILIES, THE SAME COUNT markShared USES. Asking for distinct school
    #   NAMES here while the sweep counts families would refuse to install a
    #   crest the sweep would then not have flagged.
    cur.execute("SELECT school FROM school_logo WHERE sha = %s", (sha,))
    names = [r["school"] if isinstance(r, dict) else r[0]
             for r in cur.fetchall()]
    return len({_family(n) for n in names
                if not _isRosterish(n)}) >= minimum


def storedKind(cur, school, state, level=None):
    """The kind of the crest already stored for this school, or None.

    ! THE LEVEL-LESS ROW COUNTS. A crest matched by name from the school's
      own site is stored with level '' and serves every level, so a mascot
      about to be filed under a LEVEL must still be ranked against it --
      otherwise "anet wins" wins against nothing and replaces it anyway."""
    cur.execute("""SELECT kind FROM school_logo
                   WHERE school = %s AND state = %s AND path IS NOT NULL
                     AND status = 'ok'
                     AND (level = %s OR level = '')
                   ORDER BY (level = %s) DESC, kind LIMIT 1""",
                (school, state, level or "", level or ""))
    row = cur.fetchone()
    if row is None:
        return None
    return row[0] if not isinstance(row, dict) else row.get("kind")


def record(cur, school, state, name, source_url, kind, sha, status,
           etag=None, modified=None, level=None):
    cur.execute("""
        INSERT INTO school_logo (school, state, level, path, source_url, kind,
                                 sha, status, etag, modified, fetched)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, current_date)
        ON CONFLICT (school, state, level) DO UPDATE
        SET path = EXCLUDED.path, source_url = EXCLUDED.source_url,
            kind = EXCLUDED.kind, sha = EXCLUDED.sha,
            status = EXCLUDED.status, etag = EXCLUDED.etag,
            modified = EXCLUDED.modified, fetched = current_date
    """, (school, state, level or "", name, source_url, kind, sha, status,
          etag, modified))


def touch(cur, school, state):
    """Unchanged since last time: stamp the date and nothing else."""
    cur.execute("""UPDATE school_logo SET fetched = current_date
                   WHERE school = %s AND state = %s""", (school, state))


# ⚠⚠ AND "UNAT-Penn State" IS NOT AN INSTITUTION (owner, 2026-09-18: the
#    PA row came back shared = True after the family fix). _family takes the
#    first two words, so the unattached spellings each invented a family of
#    their own:
#
#      Penn State            -> "penn state"
#      UNAT-Penn State       -> "unat penn"
#      UNA-Penn State        -> "una penn"
#      U-Penn State Altoona  -> "u penn"
#
#    Four families, SHARED_MIN is four, and the correct crest was suppressed
#    again -- by the very fix meant to stop that. A roster status means "no
#    team"; it cannot be evidence that a picture is a district placeholder, so
#    it contributes no family at all.
#
# ! THE REPO ALREADY KNEW THIS SHAPE: racecast/school_name.isRosterStatus and
#   panels._NOT_A_TEAM_PREFIXES are the same judgement. Spelled out here
#   rather than imported because this module is the scraper's and must run
#   with no racecast on the path.
_ROSTER_PREFIXES = ("unat", "unnat", "unath", "una", "unaff", "u")


def _isRosterish(name):
    """True when a name's FIRST word is an unattached marker rather than part
    of the school's name. Only the first word, and only as a whole word: a
    school really is called "Una" (AL) and another "Unalakleet" (AK), so this
    asks whether the rest of the string still names a school."""
    words = [w for w in re.split(r"[^A-Za-z0-9]+", str(name or "").lower()) if w]
    if len(words) < 2:
        return False
    return words[0] in _ROSTER_PREFIXES


def _family(name):
    """The institution FAMILY a name belongs to: its first two words.

    ★ ONE UNIVERSITY'S CAMPUSES ARE NOT FOUR SCHOOLS (owner, 2026-09-18:
      "Penn State still doesn't have a logo", reported four times). anet
      serves the SAME Nittany Lions image for Penn State, Penn State Berks,
      Penn State Shenango and the rest, so SHARED_MIN counted four distinct
      names, called the crest a district placeholder, and the site drew
      nothing -- for the one school whose crest was completely correct.

    ! TWO WORDS, NOT A PREFIX WALK, and deliberately conservative. "Penn
      State *" collapses to one family, which is the case being fixed.
      "Lincoln High" and "Lincoln Middle" do NOT -- their second words
      differ -- so a district placeholder across a town's schools still
      flags exactly as before. Only names that agree on their first two
      words merge, which is close to "the same institution" and far from
      "the same town".

    ! AND THE RULE THE SHARED SWEEP EXISTS FOR IS UNTOUCHED: one governing
      body's logo on four hundred unrelated schools is four hundred
      families.
    """
    words = [w for w in re.split(r"[^A-Za-z0-9]+", str(name or "").lower()) if w]
    return " ".join(words[:2]) if words else ""


def sharedShas(rows, minimum=SHARED_MIN):
    """{sha} worn by `minimum` or more distinct institution FAMILIES.

    Families, not rows and not names: one school split into two state
    clusters wears its own crest twice, and one university's campuses wear
    it a dozen times. Neither is a placeholder.
    """
    by = {}
    for school, sha in rows:
        # ! A ROSTER STATUS CONTRIBUTES NO FAMILY. See _isRosterish.
        if sha and not _isRosterish(school):
            by.setdefault(sha, set()).add(_family(school))
    return {sha for sha, fams in by.items() if len(fams) >= minimum}


# ⚠⚠ THERE WAS A --prune-stale HERE AND IT WAS WRONG TWICE (2026-09-18).
#
#    The idea: school_identity has ONE "Penn State" cluster (PA) while
#    school_logo had Penn State rows under seventeen states, so rows whose
#    (school, state) is not a cluster must be leftovers from an older build
#    and can go. The first dry run proposed 12,268 rows. Narrowing it to
#    schools that still have a cluster somewhere still proposed 9,923 --
#    nearly all club teams: 3DElite (CA/FL/KS/NC), 3M Track Club (AZ/NE),
#    301 Panthers (NC/VA).
#
# ★ AND THOSE ROWS SERVE. crestState's resolver answers None for a name the
#   identity cannot place -- every club -- and then falls back to the
#   CALLER'S OWN state (`st = (st or state or "").upper()`, and read its
#   comment: without that floor every crest on the site disappears whenever
#   the label cache is empty). A club racing in four states is looked up
#   under each of them in turn, so a row under a state that is not a cluster
#   is not unreachable. It is the row that answers. There is no predicate
#   over (school, state) that separates a rebuild leftover from a live club
#   crest, because the read path does not distinguish them either.
#
# ★ AND PENN STATE WAS NEVER THIS ANYWAY. Its crest was suppressed by the
#   `shared` flag -- loadCrests filters `(NOT shared OR override IS NOT
#   NULL)` -- because sharedShas counted five spellings of ONE institution
#   as five schools. That is fixed in sharedShas/_family, above, and the
#   seventeen rows were a cosmetic annoyance this escalated into a feature
#   that deletes ten thousand live crests. Do not rebuild it on this theory.


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
    # ! TOLERANT OF THE SHORTER ROW. `source` is the last column and a caller
    #   holding a row built before it existed (a test, a saved queue) must
    #   still work: no provenance means "a guess", the old behaviour exactly.
    (school, state, url, direct, override,
     had_url, etag, modified, status0) = row[:9]
    source = row[9] if len(row) > 9 else None
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
    # ! AN OVERRIDE IS A PERSON'S DECISION AND IS NEVER SECOND-GUESSED. The
    #   host check exists to catch a wrong ADDRESS BOOK entry; a URL somebody
    #   typed on purpose skips it by passing no name to check against.
    png, sha, kind, src = fetchLogo(manners, url,
                                    direct=(target if target != url else None),
                                    school=(None if override_url else school),
                                    source=source)
    return {"school": school, "state": state, "png": png, "sha": sha,
            "kind": kind, "src": src if png else None,
            "why": None if png else src,
            "etag": manners.etag if png else None,
            "modified": manners.modified if png else None}


PROGRESS_EVERY = 2000       # rows between progress lines; see regroundAll


def regroundAll(cur, directory=None, write=False, limit=None, only=None,
                out=None):
    """Re-key every stored crest's ground in place. Returns a census dict.

    ⚠ NO NETWORK. Every byte it needs is already on disk, so this is a repair
      and not a rescrape -- which is the whole point (owner: "I stopped the
      scrape until we can fix amherst type issues").
    """
    # ! flush BY DEFAULT, for the same reason. A caller passing its own `out`
    #   gets the plain one-argument signature.
    if out is None:
        def out(msg, flush=True):
            print(msg, flush=flush)
    else:
        _given = out

        def out(msg, flush=False, _f=_given):
            _f(msg)
    directory = directory or LOGO_DIR
    params, where = {}, ["path IS NOT NULL"]
    if only:
        params["only"] = f"%{only}%"
        where.append("school ILIKE %(only)s")
    cur.execute(f"""
        SELECT school, state, COALESCE(level, '') AS level, path, kind, sha
        FROM   school_logo
        WHERE  {' AND '.join(where)}
        ORDER  BY school, state, level
        {"LIMIT %(limit)s" if limit else ""}
    """, dict(params, limit=limit))
    rows = [dict(zip(("school", "state", "level", "path", "kind", "sha"), r))
            if not isinstance(r, dict) else dict(r) for r in cur.fetchall()]
    census = {"rows": len(rows), "missing": 0, "fixed": 0, "unchanged": 0,
              "no_ground": 0, "refused": 0, "unreadable": 0}
    # ! IT HAS TO SAY SOMETHING (owner: "I ctrl cd the reground one, I think
    #   it was hung, or it wasn't printing anything at least"). Decoding and
    #   re-keying tens of thousands of PNGs is minutes of work with nothing to
    #   show, and a silent process is indistinguishable from a wedged one.
    out(f"  {len(rows):,} crests to check in {directory}", flush=True)
    fixed = []
    for i, row in enumerate(rows):
        if i and i % PROGRESS_EVERY == 0:
            out(f"    {i:,}/{len(rows):,}  {len(fixed):,} re-keyed so far",
                flush=True)
        full = os.path.join(directory, row["path"])
        try:
            with open(full, "rb") as fh:
                raw = fh.read()
        except OSError:
            census["missing"] += 1
            continue
        data, sha, why = regroundBytes(raw, kind=row.get("kind"))
        if data is None:
            if why == "no flat ground":
                census["no_ground"] += 1
            elif why == "unchanged":
                census["unchanged"] += 1
            elif why == "unreadable":
                census["unreadable"] += 1
            else:
                census["refused"] += 1
            continue
        census["fixed"] += 1
        fixed.append((row, sha, why, len(raw), len(data)))
        if write:
            tmp = f"{full}.{os.getpid()}.tmp"
            with open(tmp, "wb") as fh:
                fh.write(data)
            os.replace(tmp, full)
            cur.execute("""UPDATE school_logo SET sha = %s
                           WHERE school = %s AND state = %s
                             AND COALESCE(level, '') = %s""",
                        (sha, row["school"], row["state"], row["level"]))
    for row, _sha, why, was, now in fixed[:20]:
        out(f"    {row['school']} ({row['state'] or '-'}"
            f"{'/' + row['level'] if row['level'] else ''}): {why}, "
            f"{was:,} -> {now:,} bytes")
    if len(fixed) > 20:
        out(f"    ... and {len(fixed) - 20:,} more")
    out(f"  {census['rows']:,} crests: {census['fixed']:,} re-keyed, "
        f"{census['no_ground']:,} already transparent or not a flat ground, "
        f"{census['unchanged']:,} unchanged, {census['refused']:,} refused "
        f"(keying would leave no mark), {census['unreadable']:,} unreadable, "
        f"{census['missing']:,} file missing")
    if not write:
        out("  --dry-run: nothing was written.")
    return census


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
    ap.add_argument("--suspect", action="store_true",
                    help="only schools whose crest did NOT come from anet's "
                         "team page, worst first. The wrong-logo subset, "
                         "without re-asking anet for everything. Implies "
                         "--redo; pair with --dry-run to see the list.")
    ap.add_argument("--fix-multi", action="store_true",
                    help="re-ask only the (school, state) pairs whose crest is "
                         "an anet mascot on a pair holding TWO INSTITUTIONS -- "
                         "the Amherst case, where anet's modal team is the "
                         "bigger school and its mascot replaced the other's "
                         "real crest. Implies --redo. See damagedPairs.")
    ap.add_argument("--reground", action="store_true",
                    help="re-key the ground out of the crests ALREADY ON "
                         "DISK and stop. No network: the black background is "
                         "in the stored PNG, so this is a repair rather than "
                         "a rescrape. Idempotent -- a crest with no flat "
                         "ground comes back byte-identical.")
    args = ap.parse_args()
    if args.suspect:
        # ! IMPLIES --redo, or the refresh window skips the very rows being
        #   re-asked. Same reason --fix-multi does.
        args.redo = True
    if args.fix_multi:
        args.redo = True
    if args.redo:
        # -1, not 0: "fetched < today - 0" skips everything fetched TODAY,
        # which is exactly the row you are trying to redo an hour later
        args.refresh_days, args.retry_failed = -1, True
    if not (args.write or args.dry_run or args.stats or args.sweep_only
            or args.reground):
        ap.error("pass --stats, --dry-run, --write, --sweep-only or --reground")

    from database import getConn
    with getConn() as conn:
        with conn.cursor() as cur:
            if args.stats:
                print(stats(cur))
                return
            if args.reground:
                # ! THE MIGRATION FIRST, as below: this reads `level`.
                ensureTable(cur, DDL)
                ensureLevelKey(cur)
                regroundAll(cur, args.dir, write=args.write,
                            limit=args.limit, only=args.only)
                if args.write:
                    conn.commit()
                else:
                    conn.rollback()
                return
            if args.sweep_only:
                ensureTable(cur, DDL)
                n = markShared(cur)
                conn.commit()
                print(f"  shared crests: {n:,} images worn by {SHARED_MIN}+ schools")
                return
            # ! THE MIGRATION FIRST. damagedPairs reads `level`, and on a
            #   table made before that column existed the query is an
            #   UndefinedColumn -- which would end the run before the
            #   repair it was asked for.
            ensureTable(cur, DDL)
            ensureLevelKey(cur)

            pairs = None
            if args.fix_multi:
                pairs = damagedPairs(cur)
                print(f"  {len(pairs):,} (school, state) pairs wear an anet "
                      f"mascot while holding two institutions", flush=True)
                if not pairs:
                    return
            elif args.suspect:
                # ★ ONLY THE CRESTS THAT ARE NOT ANET'S OWN ANSWER, so a
                #   wrong-logo sweep costs one fetch per suspect school rather
                #   than one per school in the corpus.
                pairs = suspectPairs(cur)
                if not pairs:
                    print("  nothing suspect -- every stored crest came from "
                          "anet's team page.", flush=True)
                    return
            todo = targets(cur, args.refresh_days, args.only, args.state,
                           args.limit, args.retry_failed, pairs)
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
