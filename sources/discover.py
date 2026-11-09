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

from sources import meetpro

MAX_DEPTH = 3                 # directory levels walked under a listing
MAX_LINKS = 400               # links read off one index page
MAX_SUBPAGES = 8              # pages (not files) a timer's index sends us to, per run
SHORTENERS = re.compile(r"(^|\.)(tinyurl\.com|bit\.ly|t\.co|goo\.gl|ow\.ly|rb\.gy)$", re.I)
PAGE_SKIP = re.compile(r"/(category|tag|tags|author|page|feed|wp-admin|wp-login|cart|shop)(/|$)|^/20\d\d/(\d\d/?)?$", re.I)
# ! not 'tri' or 'trail': Tri-County and Oregon Trail invitationals are XC meets
ROAD = re.compile(r"(?<![a-z])(road|roadrace|road[-_ ]?races?|marathon|half[-_ ]?marathon|triathlon|duathlon|"
                  r"turkey[-_ ]?trot|fun[-_ ]?run|color[-_ ]?run|5k[-_ ]?run[-_ ]?walk)(?![a-z])", re.I)
MEET_MAX_AGE = 30 * 86400     # a report page read within 30 days comes from the store (timers.MAX_AGE)

FEED_HOSTS = re.compile(r"(^|\.)(athletic\.net|anet\.live|athletic\.live|milesplit\.(com|live)|"
                        r"tfrrs\.org|runsignup\.com|raceroster\.com|chronotrack\.com|"
                        r"raceresult\.com|flotrack\.org|flosports\.tv|facebook\.com|"
                        r"instagram\.com|twitter\.com|x\.com|youtube\.com|linktr\.ee|"
                        r"google\.com|apple\.com)$", re.I)
# ! AN UNQUOTED HREF IS A LINK TOO (owner, 2026-11-08: "you ever scrape
#   diablo timing?" -- never: its day pages write <a href = mp/#event2>, and
#   only quoted values were read, so the results under mp/ were never seen)
_A = re.compile(r"<a\b[^>]*?href\s*=\s*(?:[\"']([^\"'#]*)[\"']|([^\s>\"'#]+)[^\s>]*)[^>]*>(.*?)</a>",
                re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_YEAR = re.compile(r"(?<!\d)(20[0-3]\d)(?!\d)")
_FILE_DATE = re.compile(r"^(\d{1,2}[-_.]\d{1,2}(?:[-_.]\d{2,4})?)\b")
_REPORT_FILE = re.compile(r"\.(html?|pdf)$", re.I)
_RESULTS_WORDS = re.compile(r"result|res\b|xc|cc\b|cross|invit|champ|classic|relays|meet|"
                            r"conference|sectional|regional|state|district|open|track|indoor|"
                            r"outdoor|\d{1,2}[-_.]\d{1,2}|"
                            # ★ A RACE'S FILE NAMED BY ITS RUNNERS AND DISTANCE (Finished
                            #   Results: '.../men-8k.pdf' linked as "Men")
                            r"(?<![a-z])(?:men|women|boys|girls|varsity|jv)(?![a-z])|"
                            r"(?<![a-z\d])\d{1,2}(?:\.\d)?\s*-?\s*(?:k|km|mi|mile)(?![a-z])|"
                            r"(?<![a-z\d])\d{3,5}\s*-?\s*m(?:eters?)?(?![a-z])", re.I)
_NAV_WORDS = re.compile(r"(?<![a-z])(?:contact|about|privacy|terms|login|sign[-_ ]?up|register|entry|entries|"
                        r"schedule|calendar|staff|services|pricing|quote|faq|blog|news|"
                        r"photo|gallery|cart|shop|store|sponsor|heat[-_ ]?sheet|psych|"
                        r"start[-_ ]?list|info|map|directions|course[-_ ]?map|"
                        # ★ PAPERS BEFORE THE MEET (2026-11-09: Finished Results' "Meet
                        #   Program", CFPI's "Participant Manual", "Declared ... Entries")
                        r"programs?|programme|manual|packet|declared|psych[-_ ]?sheet|seed(?:ing)?s?|"
                        r"bus[-_ ]?schedule|parking|waiver|flyer|timeline|"
                        # a race's team score page (Hy-Tek real time: 231104F001C.htm "... Score")
                        r"scores?)(?![a-z])", re.I)


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


# ★ A SHORT YEAR IN A DATE (2026-11-09, Snake River Timing's archive: "Weiser
#   Invite XC 9-8-16"): link text names its meet's day as M-D-YY
_SHORT_DATE = re.compile(r"(?<![\d/.-])\d{1,2}[-/.]\d{1,2}(?:[-/]\d{1,2})?[-/.](\d{2})(?![\d/.-])")


# and Hy-Tek's real-time files are named for their day: 220222F001.htm
_REALTIME = re.compile(r"(?<![\d])(\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])[A-Z]\d{3}[A-Z]?\.html?\b", re.I)


def _years(text):
    return [int(y) for y in _YEAR.findall(text or "")] + \
        [2000 + int(y) for y in _SHORT_DATE.findall(text or "")] + \
        [2000 + int(m[0]) for m in _REALTIME.findall(text or "")]


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
        # ! PLAIN HTTP: results.tfmeetpro.com is an S3 website endpoint, which
        #   has no https -- an https link to it never answers
        return urllib.parse.urlunsplit(("http", sp.netloc,
                                        f"/{parts[0]}/{parts[1]}/index.html", "", ""))
    if own and _site(host.split(":")[0]) != own:
        return None                          # another site's index.html is not this timer's meet
    if re.search(r"/mp/", path) or re.search(r"/index\.html?$", path, re.I):
        return urllib.parse.urlunsplit((sp.scheme, sp.netloc, path, "", ""))
    return None


# ★ THE MEETPRO BUCKET LISTS ITSELF (2026-11-09: 39 registered MeetPro
#   accounts had "no index; S3 NoSuchKey" -- results.tfmeetpro.com/<Account>/
#   has no index.html, so nothing was ever found for them). The bucket behind
#   it is publicly listable through S3's own ListObjects call: every key under
#   <Account>/ with its upload time, i.e. every meet folder the account has
#   posted and when. That listing is the account's index. One request lists
#   up to 1,000 files (a meet is ~20-150), at most MAX_BUCKET_PAGES a run.
MEETPRO_BUCKET_LIST = "https://s3.amazonaws.com/results.tfmeetpro.com/"
MAX_BUCKET_PAGES = 100
_S3_ENTRY = re.compile(r"<Contents>\s*<Key>(.*?)</Key>\s*<LastModified>(.*?)</LastModified>", re.S)


def meetproAccount(url):
    """'http://results.tfmeetpro.com/FTR_Race_Management/' -> 'FTR_Race_Management';
    None for a meet's own url or any other host."""
    sp = urllib.parse.urlsplit((url or "").split(" ")[0])
    if not sp.netloc.lower().split(":")[0].endswith("results.tfmeetpro.com"):
        return None
    parts = [p for p in urllib.parse.unquote(sp.path).split("/") if p]
    return parts[0] if len(parts) == 1 else None


def bucketPage(xml):
    """One ListObjects (v1) answer -> ([(key, 'YYYY-MM-DD...')], next marker
    or None when it is the last page). Pure."""
    entries = [(_html.unescape(k), d) for k, d in _S3_ENTRY.findall(xml or "")]
    truncated = re.search(r"<IsTruncated>\s*true\s*</IsTruncated>", xml or "", re.I)
    nxt = re.search(r"<NextMarker>(.*?)</NextMarker>", xml or "", re.S)
    marker = _html.unescape(nxt.group(1)) if nxt else (entries[-1][0] if entries else None)
    return entries, (marker if truncated else None)


def accountMeets(entries, account, since_year):
    """The account's meet folders from its bucket keys -> [(meet index url,
    newest upload date)], newest first; a folder is a meet when it holds an
    index.htm(l); folders last touched before since_year are left out. Pure."""
    newest, has_index = {}, set()
    for key, modified in entries:
        parts = key.split("/")
        if len(parts) < 3 or parts[0] != account or not parts[1]:
            continue
        meet = parts[1]
        if len(parts) == 3 and re.fullmatch(r"index\.html?", parts[2], re.I):
            has_index.add((meet, parts[2]))
        if modified > newest.get(meet, ""):
            newest[meet] = modified
    out = []
    for meet, page in has_index:
        date = newest[meet][:10]
        if date[:4].isdigit() and int(date[:4]) < since_year:
            continue
        # ! A FOLDER OF PAPERS, NOT RESULTS (Clennie Wilkins' "..._full_roster_
        #   and_entries", Aldrich's "Sample"): never lands, so it would take
        #   a meet's place in every night's budget
        words = meet.replace("_", " ")
        if (_NAV_WORDS.search(words) and not re.search(r"result", words, re.I)) or ROAD.search(words) \
                or re.search(r"(?<![a-z])(roster|rosters|sample|test|template|practice)(?![a-z])", words, re.I):
            continue
        url = "http://results.tfmeetpro.com/" + urllib.parse.quote(f"{account}/{meet}/{page}")
        out.append((url, date))
    return sorted(out, key=lambda x: (x[1], x[0]), reverse=True)


async def listMeetproAccount(fetcher, account, since_year, max_age):
    """([(meet url, date)], note): an account's meets read off the bucket listing."""
    entries, marker, pages = [], "", 0
    while marker is not None and pages < MAX_BUCKET_PAGES:
        q = {"prefix": account + "/"}
        if marker:
            q["marker"] = marker
        got = await fetcher.fetch(MEETPRO_BUCKET_LIST + "?" + urllib.parse.urlencode(q), max_age)
        pages += 1
        if not got.body:
            return [], got.note or f"bucket listing HTTP {got.status}"
        page, marker = bucketPage(got.body.decode("utf-8", "replace"))
        entries += page
    meets = accountMeets(entries, account, since_year)
    return meets, (f"bucket listing: {len(entries):,} files in {pages} pages"
                   + (" (cut short)" if marker else ""))


# ★ A FRAMESET IS ITS FRAMES (2026-11-09, TRXC Timing: a meet's index.htm is
#   three iframes -- header.htm, menu.htm with the race links, main.htm).
#   Their pages are read as part of the page, a few at most, own site only.
_FRAME = re.compile(r"<i?frame\b[^>]*?\bsrc\s*=\s*[\"']?([^\"'\s>]+)", re.I)
MAX_FRAMES = 4


def frameUrls(page, base):
    """The timer's own frame / iframe pages a page is built from. Pure."""
    own, out = _site(_host(base)), []
    sp = urllib.parse.urlsplit(base)
    if sp.path and not sp.path.endswith("/") and "." not in sp.path.rsplit("/", 1)[-1]:
        # '/events/academyxc26' is a folder the server answered without its
        # slash: its frames are inside it, as a browser sent there would see
        base = urllib.parse.urlunsplit((sp.scheme, sp.netloc, sp.path + "/", "", ""))
    for src in _FRAME.findall(page or ""):
        u = urllib.parse.urljoin(base, _html.unescape(src.strip()))
        if u.startswith("http") and _site(_host(u)) == own and u not in out and u != base:
            out.append(u)
    return out[:MAX_FRAMES]


async def frameBodies(fetcher, page, base):
    out = []
    for u in frameUrls(page, base):
        got = await fetcher.fetch(u, MEET_MAX_AGE)
        if got.body:
            out.append((u, got.body.decode("utf-8", "replace")))
    return out


def _rebase(page, url):
    """A frame's links made absolute, so they read right inside its parent."""
    return _A.sub(lambda m: m.group(0).replace(m.group(1) or m.group(2),
                                               urllib.parse.urljoin(url, m.group(1) or m.group(2)), 1)
                  if (m.group(1) or m.group(2)) else m.group(0), page)


_ONCLICK = re.compile(r"""onclick\s*=\s*["'][^"']*?location(?:\.href)?\s*=\s*(?:&#39;|\\?')([^'&\\]+)""", re.I)


def links(page, base):
    """(absolute url, link text) for every <a href> on a page, in order."""
    out, seen = [], set()
    for quoted, bare, inner in _A.findall(page)[:MAX_LINKS]:
        href = _html.unescape((quoted or bare).strip())
        if not href:
            continue
        if href.lower().startswith(("mailto:", "javascript:", "tel:")):
            continue
        u = urllib.parse.urljoin(base, href)
        if not u.startswith("http") or u in seen:
            continue
        seen.add(u)
        out.append((u, _html.unescape(_TAG.sub(" ", inner)).strip()))
    # ★ A ROW THAT IS A LINK (2026-11-09, CAG's Timing: <tr onclick=
    #   "window.location='/results/2025/<meet>/index.html'">): read like one
    for href in _ONCLICK.findall(page)[:MAX_LINKS]:
        u = urllib.parse.urljoin(base, _html.unescape(href.strip()))
        if u.startswith("http") and u not in seen:
            seen.add(u)
            out.append((u, ""))
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
        if FEED_HOSTS.search(host) or tooOld(u, text, since_year) \
                or ROAD.search(urllib.parse.unquote(u) + " " + text):
            continue                         # road races are not rated
        root = meetproRoot(u, own)
        if root:
            add(root, "meetpro")
            continue
        sp = urllib.parse.urlsplit(u)
        path = sp.path
        name = urllib.parse.unquote(path.rsplit("/", 1)[-1]) + " " + text
        if _site(host) != own:
            # ★ A SISTER DOMAIN'S PDFs (Black Squirrel Timing posts to
            #   blacksquirrelresults.com): a results PDF the timer links to
            if path.lower().endswith(".pdf") and not SHORTENERS.search(host) \
                    and (_RESULTS_WORDS.search(name) or _years(u)):
                add(u, "hytek")
            continue
        if listing:
            if u.rstrip("/") == index_url.rstrip("/") or not u.startswith(index_url):
                continue                     # '../' and links up the tree
            if path.endswith("/"):
                add(u, "listing")
            continue
        if _REPORT_FILE.search(path):
            if _NAV_WORDS.search(name) and not re.search(r"result", name, re.I):
                continue
            if _RESULTS_WORDS.search(name) or _years(name):
                add(u, "hytek")
        elif not re.search(r"\.\w{2,4}$", path) and u.rstrip("/") != index_url.rstrip("/"):
            # ★ A PAGE, NOT A FILE (Ticker Timing's WordPress posts
            #   /2026/10/maple-leaf-classic-xc/, EliteFeats' xc-results?ID=):
            #   read for its own report links, a few per timer (MAX_SUBPAGES)
            if PAGE_SKIP.search(path) or (_NAV_WORDS.search(name) and not re.search(r"result", name, re.I)):
                continue
            # ★ OR AN EVENT'S PAGE, OR ONE LINKED AS RESULTS (Live Running Results'
            #   /events/academyxc26, Pirate Timing's /jpi222 "Live Results")
            if re.search(r"/20\d\d/", path) or (sp.query and re.search(r"result|meet", path + sp.query, re.I)) \
                    or re.search(r"result|meet|/events?/", path, re.I) or re.search(r"result", text, re.I):
                add(u, "page")
    return out


def inSeason(urls, month=None):
    """Folders of the sport in season first (Aug-Nov: cross country), order
    otherwise kept -- a season's budget goes to the meets being run now."""
    month = month or datetime.date.today().month
    xc = re.compile(r"cross|(^|[^a-z])(xc|cc)([^a-z]|$)", re.I)

    def isXc(u):
        return bool(xc.search(urllib.parse.unquote(urllib.parse.urlsplit(u).path)))
    first = (lambda u: isXc(u)) if 8 <= month <= 11 else (lambda u: not isXc(u))
    return [u for u in urls if first(u)] + [u for u in urls if not first(u)]


_NUMBERED = re.compile(r"^(?P<dir>.*/)(?P<stem>[^/]*?\D)\d+\.html?$", re.I)


def grouped(found):
    """Numbered report files in one folder ('.../4ASSXC/Results1.htm',
    'Results2.htm') are ONE meet: -> (folder url, ('group', [files])), at
    the place of its first file. Everything else as it was. Pure."""
    groups, order = {}, []
    for u, k in found:
        m = _NUMBERED.match(u) if k == "hytek" else None
        stem = m.group("stem").lower() if m else None
        # ! HY-TEK'S REAL-TIME FILES ARE ONE MEET (Live Running Results:
        #   260730F036.htm finals beside 260730P074.htm prelims): grouped by
        #   their day, not by the round letter -- two groups would both land
        #   under the folder's url, the second over the first
        if stem and re.fullmatch(r"\d{6}[a-z]", stem):
            stem = stem[:6]
        key = (m.group("dir"), stem) if m else (u, None)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append((u, k))
    out = []
    for key in order:
        items = groups[key]
        if key[1] is not None and len(items) > 1:
            files = sorted((u for u, _ in items), key=lambda u: int(re.search(r"(\d+)\.html?$", u, re.I).group(1)))
            out.append((key[0], ("group", files)))
        else:
            out += items
    return out


def ordered(cands, month=None):
    """Report files and meet folders first, then pages to read for more;
    within each, the sport in season first (inSeason). The page's own order
    (usually newest first) is kept otherwise."""
    def isFile(u, k):
        return k in ("meetpro", "listing") or bool(re.search(r"\.(html?|pdf)$", urllib.parse.urlsplit(u).path, re.I))
    files = [c for c in cands if isFile(*c)]
    pages = [c for c in cands if not isFile(*c)]
    out = []
    for group in (files, pages):
        order = inSeason([u for u, _ in group], month)
        kind = dict(group)
        out += [(u, kind[u]) for u in order]
    return out


def isReport(body):
    """A page that is itself a results report (Hy-Tek text or PDF, or a
    MeetPro meet index), not an index of them."""
    if not body:
        return False
    if body[:5] == b"%PDF-":
        return True
    head = body[:60000]
    from sources import hytek
    # ! CRYSTAL REPORTS HTML (Hy-Tek's web export: YenTiming, Ticker Timing)
    #   places every word in its own <div>, so the Hy-Tek line never reads
    #   whole in the raw page
    from sources import datarows, flashresults, meetpro
    text = body[:400000].decode("utf-8", "replace")
    return (bool(hytek._HEADER.search(head.decode("utf-8", "replace"))) or meetpro.isMeetProPage(head)
            or b"Crystal Reports" in head[:2000] or datarows.isDataPage(text)
            or flashresults.isMeetIndex(text))


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


_ACCOUNT_IN_TEXT = re.compile(r"tfmeetpro\.com/([\w\-]+)/?((?:\s*(?:and|,|&)\s*/[\w\-]+/?)*)")


def accountsNamed(*texts):
    """Every MeetPro account the research file names for a timer, in order:
    'results.tfmeetpro.com/Pierre_Timing and /Jayy_Pierre_Family_Enter/' ->
    ['Pierre_Timing', 'Jayy_Pierre_Family_Enter']. Pure."""
    out = []
    for m in _ACCOUNT_IN_TEXT.finditer(" ".join(t or "" for t in texts)):
        for a in [m.group(1)] + re.findall(r"/([\w\-]+)", m.group(2) or ""):
            if a not in out:
                out.append(a)
    return out


async def discover(fetcher, source, since_year=None, per_source=10, known=frozenset(),
                   index_max_age=6 * 3600):
    """([(url, kind)], note) for one registry Source: up to per_source meet
    pages not already landed -- off its own results index first, then off
    every MeetPro account the research file names for it, newest first.

    ★ A TIMER'S MEETPRO ACCOUNTS ARE ITS INDEX TOO (2026-11-09). Eleven timers
      (Finish Line Results, Swobie, Common Timing, Aldrich ...) had a MileSplit
      profile on record as their results page -- a feed's page, never read --
      though the research file names their own account on results.tfmeetpro
      .com; AthleticLIVE tenants post some meets only there."""
    since_year = since_year or datetime.date.today().year
    index = source.results_url.split(" ")[0] if source.results_url else source.website
    accounts = accountsNamed(getattr(source, "results_url", ""), getattr(source, "platform", ""),
                             getattr(source, "notes", ""), getattr(source, "sample_url", ""),
                             getattr(source, "website", ""))
    own, note, listed = [], "", 0
    if not index or not index.startswith("http"):
        note = "no results page on record"
    elif meetproAccount(index):
        note = ""                                    # an account root: listed below
    elif FEED_HOSTS.search(_host(index)):
        note = "results page on record is a feed's (not read)"
    else:
        own, note, listed = await _ownIndex(fetcher, index, since_year, per_source, known, index_max_age)
    # ! ONLY WHEN THE OWN PAGE LISTS NO MEETS: a timer whose own index works
    #   (Mitchell Timing links its MeetPro meets itself) is not listed again
    #   off the bucket every night -- 700 meet folders are 70 listing pages
    if not accounts or listed:
        return own, note
    seen = {u for u, _k in own}
    extra, notes = [], []
    for account in accounts:
        if len(own) + len(extra) >= per_source:
            break
        meets, n = await listMeetproAccount(fetcher, account, since_year, index_max_age)
        notes.append(f"{account} ({n}): {len(meets)} meets from {since_year} on")
        for u, _d in meets:
            if u not in known and u not in seen:
                seen.add(u)
                extra.append((u, "meetpro"))
    new = (own + extra)[:per_source]
    return new, "; ".join(x for x in [note] + notes if x) + f"; {len(new)} new"


async def _ownIndex(fetcher, index, since_year, per_source, known, index_max_age):
    """([(url, kind)], note, meet pages found): the meets the timer's own
    results page links to (found counts the known ones too)."""
    got = await fetcher.fetch(index, index_max_age)
    if not got.body:
        return [], got.note or f"index HTTP {got.status}", 0
    if isReport(got.body):
        # the page on record is itself a report (AllTrax: one TSSAA PDF)
        return ([] if index in known else [(index, "hytek")]), "results page on record is one report", 0
    page = got.body.decode("utf-8", "replace")
    if isAliveTenant(page):
        return [], "AthleticLIVE tenant -- read by sources.athleticlive", 0
    found, subindexes, subpages, verified = [], 0, [0], set()

    def fresh():
        return [(u, k) for u, k in found if u not in known]

    def freshMeets():
        # a numbered group lands under its folder: known by that
        return [g for g in grouped(fresh()) if g[0] not in known]

    async def take(cands, depth):
        nonlocal subindexes
        for u, kind in cands:
            # ! counted as meets, not files: a numbered group is one meet, and
            #   one group past the budget is gathered then dropped, so the last
            #   meet kept is never cut short
            if len(freshMeets()) > per_source:
                return
            if any(u == f for f, _ in found):
                continue
            # ! ONE FILE OF A NUMBERED GROUP SAYS WHAT THE REST ARE (Hy-Tek real
            #   time: 100 event files, each fetched only to be called a report)
            m = _NUMBERED.match(u) if kind == "hytek" else None
            if m and (m.group("dir"), m.group("stem").lower()) in verified:
                found.append((u, "hytek"))
                continue
            if kind == "listing":
                need = per_source + 1 - len(freshMeets())
                for meet in await walkListing(fetcher, u, since_year, index_max_age, need, 1):
                    found.append((meet, "hytek"))
            elif u in known or (kind == "meetpro" and _host(u).endswith("tfmeetpro.com")):
                found.append((u, kind))
            else:
                if kind == "page":
                    if subpages[0] >= MAX_SUBPAGES:
                        continue
                    subpages[0] += 1
                # ★ A SUB-INDEX ("2026-results.html", "cross-country-timing.html"):
                #   a page on the timer's site that lists reports rather than
                #   being one -- its own links are read, one level down
                # ! AN "index.htm" ON THE TIMER'S OWN SITE IS NOT ALWAYS MEETPRO
                #   (TRXC Timing's /MSHSAA_XC/index.htm is a Hy-Tek frameset):
                #   it is read first, like any page, and kept as MeetPro only
                #   when it is one
                got = await fetcher.fetch(u, MEET_MAX_AGE)
                if got.body and meetpro.isMeetProPage(got.body):
                    found.append((u, "meetpro"))
                elif isReport(got.body):
                    found.append((u, "hytek"))
                    if m:
                        verified.add((m.group("dir"), m.group("stem").lower()))
                elif got.body and (depth == 0 or kind == "meetpro"):
                    subindexes += 1
                    body = got.body.decode("utf-8", "replace")
                    for fu, fbody in await frameBodies(fetcher, body, u):
                        body += "\n" + _rebase(fbody, fu)
                    await take(ordered(candidates(body, u, since_year)), 1)
    await take(ordered(candidates(page, index, since_year)), 0)
    new = freshMeets()[:per_source]
    return new, (f"{len(found)} meet pages" + (f" (via {subindexes} sub-index pages)" if subindexes else "")
                 + f", {len(new)} new"), len(found)
