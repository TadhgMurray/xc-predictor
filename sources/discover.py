# Project: xc-predictor
# File:    sources/discover.py
# Purpose: Which meet pages a timer has posted -- read off the timer's own
#          results index (docs/timing_companies.csv results_url), so
#          `python -m sources.timers all` needs no hand-typed meet urls.
#
#   candidates(page, index_url, since_year) -> [(url, kind_hint)]     pure
#   discover(fetcher, source, since_year, per_source, known)          async
#
# WHAT COUNTS AS A MEET LINK ON AN INDEX PAGE
#   meetpro   a MeetPro meet directory: results.tfmeetpro.com/<Timer>/<Meet>/,
#             rtspt.com .../mp/..., or any link to .../index.htm(l) on the
#             timer's own host (Snake River's archive copies)
#   listing   an Apache "Index of" directory (YenTiming): its year folders
#             from since_year on are walked down (depth 3) until a folder
#             holds report files -- that folder is one meet
#             (timers.directoryReports reads it)
#   hytek     a .htm/.html/.pdf on the timer's own site whose file name or
#             link text reads like a results report
#
# ! NOT HERE: AthleticLIVE tenants (live.<timer>.com pages built on
#   livestatic.athletic.net) -- sources.athleticlive reads those meets from
#   their JSON, so the index is skipped and said so. Links into feeds we
#   already scrape (athletic.net, MileSplit, TFRRS) and road platforms are
#   dropped; off-limits hosts are refused by timers.fetchMeet anyway.
# ! RECENCY FROM THE URL. Index pages carry no dates we can trust, so a link
#   whose url or text names a year before since_year is left out; one naming
#   no year is kept. A page already in meets_ext is not fetched again.
import datetime
import html as _html
import re
import urllib.parse

MAX_DEPTH = 3                 # directory levels walked under a listing
MAX_LINKS = 400               # links read off one index page
MEET_MAX_AGE = 30 * 86400     # a report page read within 30 days comes from the store (timers.MAX_AGE)

FEED_HOSTS = re.compile(r"(^|\.)(athletic\.net|anet\.live|athletic\.live|milesplit\.(com|live)|"
                        r"tfrrs\.org|runsignup\.com|raceroster\.com|chronotrack\.com|"
                        r"raceresult\.com|flotrack\.org|flosports\.tv|facebook\.com|"
                        r"instagram\.com|twitter\.com|x\.com|youtube\.com|linktr\.ee|"
                        r"google\.com|apple\.com)$", re.I)
_A = re.compile(r"<a\b[^>]*?href\s*=\s*[\"']([^\"'#]+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_YEAR = re.compile(r"(?<!\d)(20[0-3]\d)(?!\d)")
_FILE_DATE = re.compile(r"^(\d{1,2}[-_.]\d{1,2}(?:[-_.]\d{2,4})?)\b")
_REPORT_FILE = re.compile(r"\.(html?|pdf)$", re.I)
_RESULTS_WORDS = re.compile(r"result|res\b|xc|cc\b|cross|invit|champ|classic|relays|meet|"
                            r"conference|sectional|regional|state|district|open|track|indoor|"
                            r"outdoor|\d{1,2}[-_.]\d{1,2}", re.I)
_NAV_WORDS = re.compile(r"contact|about|privacy|terms|login|sign[-_ ]?up|register|entry|entries|"
                        r"schedule|calendar|staff|services|pricing|quote|faq|blog|news|"
                        r"photo|gallery|cart|shop|store|sponsor|heat[-_ ]?sheet|psych|"
                        r"start[-_ ]?list|info|map|directions|course[-_ ]?map", re.I)


def isAliveTenant(page):
    """An AthleticLIVE tenant's page (sources.athleticlive reads those)."""
    return "livestatic.athletic.net" in page[:20000]


def isListing(page):
    return bool(re.search(r"<title>\s*Index of", page, re.I))


def _host(url):
    return urllib.parse.urlsplit(url).netloc.lower().split(":")[0]


def _site(host):
    """'results.yentiming.com' -> 'yentiming.com': the timer's own domain."""
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _years(text):
    return [int(y) for y in _YEAR.findall(text or "")]


def tooOld(url, text, since_year):
    ys = _years(urllib.parse.unquote(url)) + _years(text)
    return bool(ys) and max(ys) < since_year


def meetproRoot(url, own=None):
    """A MeetPro meet's index url, or None. results.tfmeetpro.com/<T>/<M>/...
    -> .../<T>/<M>/index.html; a link to .../index.htm(l) as it is."""
    sp = urllib.parse.urlsplit(url)
    host, path = sp.netloc.lower(), sp.path
    if host.endswith("tfmeetpro.com"):
        parts = [p for p in path.split("/") if p]
        if len(parts) < 2:
            return None                      # a timer's root, not a meet
        return urllib.parse.urlunsplit((sp.scheme or "https", sp.netloc,
                                        f"/{parts[0]}/{parts[1]}/index.html", "", ""))
    if own and _site(host.split(":")[0]) != own:
        return None                          # another site's index.html is not this timer's meet
    if re.search(r"/mp/", path) or re.search(r"/index\.html?$", path, re.I):
        return urllib.parse.urlunsplit((sp.scheme, sp.netloc, path, "", ""))
    return None


def links(page, base):
    """(absolute url, link text) for every <a href> on a page, in order."""
    out, seen = [], set()
    for href, inner in _A.findall(page)[:MAX_LINKS]:
        href = _html.unescape(href.strip())
        if href.lower().startswith(("mailto:", "javascript:", "tel:")):
            continue
        u = urllib.parse.urljoin(base, href)
        if not u.startswith("http") or u in seen:
            continue
        seen.add(u)
        out.append((u, _html.unescape(_TAG.sub(" ", inner)).strip()))
    return out


def candidates(page, index_url, since_year):
    """[(url, kind)] meet pages an index page links to; kind is 'meetpro',
    'hytek' or 'listing' (a directory to walk). Pure."""
    own = _site(_host(index_url))
    out, seen = [], set()

    def add(u, kind):
        if u not in seen:
            seen.add(u)
            out.append((u, kind))
    listing = isListing(page)
    for u, text in links(page, index_url):
        host = _host(u)
        if FEED_HOSTS.search(host) or tooOld(u, text, since_year):
            continue
        root = meetproRoot(u, own)
        if root:
            add(root, "meetpro")
            continue
        if _site(host) != own:
            continue
        path = urllib.parse.urlsplit(u).path
        if listing:
            if u.rstrip("/") == index_url.rstrip("/") or not u.startswith(index_url):
                continue                     # '../' and links up the tree
            if path.endswith("/"):
                add(u, "listing")
            continue
        if _REPORT_FILE.search(path):
            name = urllib.parse.unquote(path.rsplit("/", 1)[-1]) + " " + text
            if _NAV_WORDS.search(name) and not re.search(r"result", name, re.I):
                continue
            if _RESULTS_WORDS.search(name) or _years(name):
                add(u, "hytek")
    return out


def inSeason(urls, month=None):
    """Folders of the sport in season first (Aug-Nov: cross country), order
    otherwise kept -- a season's budget goes to the meets being run now."""
    month = month or datetime.date.today().month
    xc = re.compile(r"cross|(^|[^a-z])(xc|cc)([^a-z]|$)", re.I)

    def isXc(u):
        return bool(xc.search(urllib.parse.unquote(u.rstrip("/").rsplit("/", 1)[-1])))
    first = (lambda u: isXc(u)) if 8 <= month <= 11 else (lambda u: not isXc(u))
    return [u for u in urls if first(u)] + [u for u in urls if not first(u)]


def isReport(body):
    """A page that is itself a results report (Hy-Tek text or PDF, or a
    MeetPro meet index), not an index of them."""
    if not body:
        return False
    if body[:5] == b"%PDF-":
        return True
    head = body[:60000]
    from sources import hytek
    return bool(hytek._HEADER.search(head.decode("utf-8", "replace"))) or b"MeetPro" in head[:20000]


async def walkListing(fetcher, url, since_year, max_age, need, depth=0):
    """Up to `need` meet folders under a directory listing, newest first: a
    folder holding report files is a meet; otherwise its sub-folders are
    walked (year folders before since_year left out)."""
    from sources import timers
    got = await fetcher.fetch(url, max_age)
    if not got.body or need <= 0:
        return []
    page = got.body.decode("utf-8", "replace")
    reports = timers.directoryReports(page, url)
    if reports:
        # ★ A SEASON FOLDER, NOT A MEET (YenTiming /2026/outdoor/: one report
        #   per meet, "04-11-26-MercyInvit.htm"): reports dated differently
        #   are meets of their own, newest first; otherwise the folder is one
        #   meet's races
        dates = {_FILE_DATE.match(urllib.parse.unquote(r.rsplit("/", 1)[-1])).group(1)
                 for r in reports if _FILE_DATE.match(urllib.parse.unquote(r.rsplit("/", 1)[-1]))}
        if len(dates) >= 2:
            return list(reversed(reports))[:need]
        return [url]
    if depth >= MAX_DEPTH:
        return []
    meets = []
    subs = [u for u, k in candidates(page, url, since_year) if k == "listing"]
    for sub in inSeason(list(reversed(subs))):   # listings sort oldest first
        meets += await walkListing(fetcher, sub, since_year, max_age, need - len(meets), depth + 1)
        if len(meets) >= need:
            break
    return meets


async def discover(fetcher, source, since_year=None, per_source=10, known=frozenset(),
                   index_max_age=6 * 3600):
    """([(url, kind)], note) for one registry Source: up to per_source meet
    pages not already landed."""
    since_year = since_year or datetime.date.today().year
    index = source.results_url.split(" ")[0] if source.results_url else source.website
    if not index or not index.startswith("http"):
        return [], "no results page on record"
    got = await fetcher.fetch(index, index_max_age)
    if not got.body:
        return [], got.note or f"index HTTP {got.status}"
    page = got.body.decode("utf-8", "replace")
    if isAliveTenant(page):
        return [], "AthleticLIVE tenant -- read by sources.athleticlive"
    found, subindexes = [], 0

    def fresh():
        return [(u, k) for u, k in found if u not in known]

    async def take(cands, depth):
        nonlocal subindexes
        for u, kind in cands:
            if len(fresh()) >= per_source:
                return
            if any(u == f for f, _ in found):
                continue
            if kind == "listing":
                need = per_source - len(fresh())
                for meet in await walkListing(fetcher, u, since_year, index_max_age, need, 1):
                    found.append((meet, "hytek"))
            elif kind == "meetpro" or u in known:
                found.append((u, kind))
            else:
                # ★ A SUB-INDEX ("2026-results.html", "cross-country-timing.html"):
                #   a page on the timer's site that lists reports rather than
                #   being one -- its own links are read, one level down
                got = await fetcher.fetch(u, MEET_MAX_AGE)
                if isReport(got.body):
                    found.append((u, "hytek"))
                elif got.body and depth == 0:
                    subindexes += 1
                    await take(candidates(got.body.decode("utf-8", "replace"), u, since_year), 1)
    await take(candidates(page, index, since_year), 0)
    new = fresh()[:per_source]
    return new, (f"{len(found)} meet pages" + (f" (via {subindexes} sub-index pages)" if subindexes else "")
                 + f", {len(new)} new")
