# Project: xc-predictor
# File:    sources/timers.py
# Purpose: The timing-company results job, one step at a time, each one a
#          dry run until told otherwise (owner's rule: parse into a file and
#          show counts before anything is written).
#
#   python -m sources.timers init                                  # the landing tables
#   python -m sources.timers fetch --source mitchell-timing --url URL [--url URL ...]
#                                  [--urls FILE] --out landed.jsonl  # fetch + parse, no database
#   python -m sources.timers land --from landed.jsonl [--write]    # into meets_ext / races_ext / results_ext
#   python -m sources.timers dedup [--write]                       # athletic.net / TFRRS already have it?
#   python -m sources.timers promote [--apply]                     # the rest into results / meets ...
#   python -m sources.timers status                                # counts, and distances that disagree
#
#   python -m sources.timers all [--dry] [--only SLUG ...] [--per-source 10] [--since-year 2026]
#       ★ THE ONE COMMAND (owner, 2026-10-31: "make all the local timers
#       scrapers actually work with an easy command"): every enabled timer's
#       results index is read for meet pages (sources/discover.py), the new
#       ones fetched and parsed, then land -> dedup -> promote, written.
#       --dry stops after the parse (logs/timers_<date>.jsonl, counts).
#
#   Then the pipeline's own steps do the rest: 04a pass 4 links the promoted
#   rows (scripts/link_uploads.py --source timer), 04c flags a race a feed
#   posts later (twin_upload), 05 normalises them.
#
# THE ADAPTERS (a url's kind, from its host and its page):
#   meetpro  a MeetPro meet's index.html (results.tfmeetpro.com/<Timer>/<Meet>/,
#            rtspt.com .../mp/, a timer's own copy): the index, then each
#            event's results_<n>.html
#   hytek    a Hy-Tek Meet Manager report: Crystal Reports HTML (and its
#            X2.html, X3.html page files), a <pre> page, or a PDF -- or a
#            DIRECTORY of them (YenTiming: /<year>/cross/<meet>/, one report
#            per race, each also as a PDF): every HTML report in it, the PDFs
#            only when there is no HTML, one landed meet
#
# ! POLITE BY CONSTRUCTION: sources.fetch.Fetcher -- one request in flight per
#   host, 10 s apart, robots.txt honoured, every body kept under /srv/raw so a
#   parser change never costs the timer a second visit. A url on an
#   off-limits host (sources.registry.OFF_LIMITS_HOSTS) is refused before any
#   request. A bot challenge is not worked around: the page simply fails to
#   parse and is reported.

import argparse
import asyncio
import json
import os
import re
import sys
import time
import urllib.parse

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from sources import datarows, flashresults, hytek, land, meetpro, meetreport, registry     # noqa: E402

_now = time.monotonic       # the budget's clock (tests replace it)
MAX_AGE = 30 * 86400        # a page fetched within 30 days is read from the store
MAX_PAGES = 40              # Hy-Tek page files followed per report


_LINK = re.compile(r'href="([^"?#]+\.(html?|pdf))"', re.I)


def directoryReports(page, url):
    """The report files a directory listing links to, in order: its HTML
    reports (not a report's own page files, 'X2.html' beside 'X.html'), or
    its PDFs when it has no HTML. [] when the page is not a listing. Pure."""
    if hytek._HEADER.search(page) or not re.search(r"<title>\s*Index of|href=\"\.\./\"", page, re.I):
        return []
    links = []
    for href, ext in _LINK.findall(page):
        u = urllib.parse.urljoin(url, href)
        if u.startswith(url) and u not in links:
            links.append(u)
    html = [u for u in links if not u.lower().endswith(".pdf")]
    stems = {urllib.parse.unquote(u.rsplit(".", 1)[0]) for u in html}

    def pageFile(u):
        stem = urllib.parse.unquote(u.rsplit(".", 1)[0])
        m = re.match(r"^(.*\D)\d+$", stem)
        return bool(m and m.group(1) in stems)
    html = [u for u in html if not pageFile(u)]
    return html or [u for u in links if u.lower().endswith(".pdf")]


def kindOf(url, body=b""):
    u = (url or "").lower()
    if "tfmeetpro.com" in u or re.search(r"/mp/", u) or meetpro.isMeetProPage(body or b""):
        return "meetpro"
    return "hytek"


def _isChallenge(body):
    """A bot challenge page: never worked around, only reported."""
    head = (body or b"")[:6000].lower()
    return (b"cf-chl" in head or b"challenge-platform" in head or b"just a moment..." in head
            or b"attention required! | cloudflare" in head)


def folderDate(url, page_date, slack_days=3):
    """The meet's date: a page's own, unless the folder it is posted in is
    named for a date more than `slack_days` away (Diablo Timing's
    results/2026-11-05/ held a page still headed 11/06/2025 -- last year's
    header left on the template). Pure."""
    import datetime as _dt
    m = re.search(r"/(20\d\d-\d\d-\d\d)/", url or "")
    if not m:
        return page_date
    try:
        folder = _dt.date.fromisoformat(m.group(1))
        page = _dt.date.fromisoformat(page_date) if page_date else None
    except ValueError:
        return page_date
    if page is None or abs((page - folder).days) > slack_days:
        return folder.isoformat()
    return page_date


async def fetchMeet(fetcher, slug, url, kind=None, max_age=None):
    """(LandedMeet | None, note) for one meet url. max_age: how old a stored
    page may be and still be used (MAX_AGE when None)."""
    max_age = MAX_AGE if max_age is None else max_age
    if registry.offLimits(url):
        return None, "off limits (terms or robots.txt)"
    got = await fetcher.fetch(url, max_age)
    if not got.body:
        return None, got.note or f"HTTP {got.status}"
    if _isChallenge(got.body):
        return None, "bot challenge -- not worked around"
    kind = kind or kindOf(url, got.body)
    if kind == "meetpro":
        page = got.body.decode("utf-8", "replace")
        if meetpro.isSinglePage(page):              # the whole meet on one page
            idx, results = meetpro.parseSinglePage(page)
            idx.date = folderDate(url, idx.date)
            return land.fromMeetPro(idx, results, slug, url), "meetpro (one page)"
        idx = meetpro.parseMeetIndex(page)
        results = {}
        for e in idx.events:
            if not e.results_href or e.relay:
                continue
            r = await fetcher.fetch(urllib.parse.urljoin(url, e.results_href), max_age)
            if r.body:
                results[e.number] = meetpro.parseRounds(r.body.decode("utf-8", "replace"))
        return land.fromMeetPro(idx, results, slug, url), "meetpro"
    if got.body[:5] != b"%PDF-" and flashresults.isMeetIndex(got.body.decode("utf-8", "replace")):
        return await flashMeet(fetcher, slug, url, got.body.decode("utf-8", "replace"), max_age), "flash results"
    if got.body[:5] != b"%PDF-" and datarows.isDataPage(got.body.decode("utf-8", "replace")):
        return await dataRowsMeet(fetcher, slug, url, got.body.decode("utf-8", "replace"), max_age), "data rows"
    listing = directoryReports(got.body.decode("utf-8", "replace"), url) if got.body[:5] != b"%PDF-" else []
    if listing:
        return await mergeReports(fetcher, slug, url, listing, max_age)
    return await _hytekReport(fetcher, slug, url, got.body, max_age), "hytek"


async def flashMeet(fetcher, slug, url, page, max_age=MAX_AGE):
    """A Flash Results meet index and its races' result pages -> LandedMeet."""
    name, date, place = flashresults.parseIndex(page)
    meet = hytek.Meet(name=name, date=date, venue=place)
    xc = bool(re.search(r"/xc/", url, re.I))
    for k, (rurl, title) in enumerate(flashresults.raceLinks(page, url)[:MAX_PAGES], 1):
        r = await fetcher.fetch(rurl, max_age)
        if not r.body or _isChallenge(r.body):
            continue
        e = hytek.Event(number=k, gender=meetpro._gender(title), title=title, meters=hytek.metersOf(title))
        e.rows = flashresults.parseRace(r.body.decode("utf-8", "replace"))
        if xc:
            e.sport = "XC"
        meet.events.append(e)
    lm = land.fromHytek(meet, slug, url)
    lm.place, lm.state = place, land.stateOf(place)
    return lm


async def dataRowsMeet(fetcher, slug, url, page, max_age=MAX_AGE):
    """A data-rows results page (sources.datarows: EliteFeats) and the pages
    of its other divisions -> one LandedMeet."""
    meet = datarows.parsePage(page, url)
    for k, durl in enumerate(datarows.divisionUrls(page, url)[:MAX_PAGES], 1):
        r = await fetcher.fetch(durl, max_age)
        if not r.body or _isChallenge(r.body):
            continue
        other = datarows.parsePage(r.body.decode("utf-8", "replace"), durl)
        have = {e.title for e in meet.events}
        for e in other.events:
            if e.title not in have:
                e.number = len(meet.events) + 1
                meet.events.append(e)
    return land.fromHytek(meet, slug, url)


async def mergeReports(fetcher, slug, url, listing, max_age=MAX_AGE):
    """Several report files that are one meet (a directory's per-race
    reports, Ticker Timing's Results1.htm, Results2.htm ...) -> one
    LandedMeet under `url`."""
    merged = None
    for k, rurl in enumerate(listing[:MAX_PAGES], 1):
        r = await fetcher.fetch(rurl, max_age)
        if not r.body or _isChallenge(r.body):
            continue
        lm = await _hytekReport(fetcher, slug, rurl, r.body, max_age)
        if merged is None:
            merged = land.LandedMeet(slug=slug, url=url, name=lm.name, date=lm.date, venue=lm.venue,
                                     place=lm.place, state=lm.state, licensee=lm.licensee)
        for race in lm.races:            # each report numbers its events from 1
            race.n = k * 1000 + race.n
            merged.races.append(race)
        merged.skipped += lm.skipped
    return merged, f"hytek directory ({len(listing)} reports)"


async def _hytekReport(fetcher, slug, url, body, max_age=MAX_AGE):
    """One Hy-Tek report: its first page, then its page files."""
    texts, seen, cur = [], {url}, url
    meta = fetcher.store.meta(url) or {}
    while body is not None and len(seen) <= MAX_PAGES:
        texts.append(hytek.textFromAny(body, meta.get("content_type") or ""))
        page = body.decode("utf-8", "replace") if body[:5] != b"%PDF-" else ""
        nxt = [urllib.parse.urljoin(cur, h) for h in hytek.nextPageLinks(page)]
        nxt = [n for n in nxt if n not in seen]
        if not nxt:
            break
        cur = nxt[0]
        seen.add(cur)
        r = await fetcher.fetch(cur, max_age)
        body = r.body
        meta = fetcher.store.meta(cur) or {}
    text = "\n".join(texts)
    meet = hytek.parseReport(text)
    if not any(e.rows for e in meet.events) and meetreport.isReport(text):
        # not Hy-Tek: MeetPro's desktop report or a two-column results PDF
        meet = meetreport.parseReport(text, url)
    elif not any(e.rows for e in meet.events) and meetreport.isRunMeet(text):
        meet = meetreport.parseRunMeet(text, url)
    elif not any(e.rows for e in meet.events) and meetreport.isTabbed(text):
        meet = meetreport.parseTabbed(text, url)
    return land.fromHytek(meet, slug, url)


def _urls(a):
    out = list(a.url or [])
    if a.urls:
        with open(a.urls, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    out.append(line)
    return out


def cmdFetch(a):
    from sources.fetch import Fetcher, RawStore
    known = {s.slug: s for s in registry.load()}
    src = known.get(a.source)
    if src is None:
        print(f"[timers] ⚠ '{a.source}' is not in docs/timing_companies.csv -- landing under that slug anyway")
    elif src.kind == "blocked":
        print(f"[timers] '{a.source}' is off limits ({src.platform[:80]}) -- nothing fetched")
        return 1
    urls = _urls(a)
    if not urls:
        print("[timers] no urls (--url or --urls)")
        return 2
    fetcher = Fetcher(RawStore())

    async def go():
        return await asyncio.gather(*(fetchMeet(fetcher, a.source, u, a.kind) for u in urls))

    got = asyncio.run(go())
    n_meets = n_rows = 0
    with open(a.out, "w", encoding="utf-8") as f:
        for url, (lm, note) in zip(urls, got):
            if lm is None:
                print(f"[timers] ✗ {url}: {note}")
                continue
            if not lm.races:
                print(f"[timers] ✗ {url}: parsed, no individual races ({note}; skipped {lm.skipped})")
                continue
            f.write(json.dumps(lm.toJson()) + "\n")
            n_meets += 1
            n_rows += lm.rowCount()
            print(f"[timers] ✓ {land.summary(lm)}")
    print(f"[timers] {n_meets} of {len(urls)} meets parsed, {n_rows:,} rows -> {a.out}; "
          f"fetcher {fetcher.stats}")
    print(f"[timers] next: python -m sources.timers land --from {a.out}            # what would land")
    return 0


def readLanded(path):
    with open(path, encoding="utf-8") as f:
        return [land.LandedMeet.fromJson(json.loads(line)) for line in f if line.strip()]


def cmdLand(a):
    meets = readLanded(a.from_)
    for lm in meets:
        print(f"[land] {lm.source:<30} {land.summary(lm)}")
    print(f"[land] {len(meets)} meets, {sum(m.rowCount() for m in meets):,} rows in {a.from_}")
    if not a.write:
        print("[land] dry run -- nothing written; --write to land them")
        return 0
    from database import getConn
    from collections import Counter
    what = Counter()
    with getConn() as conn:
        with conn.cursor() as cur:
            land.initTables(cur)
            for lm in meets:
                ext, w = land.landMeet(cur, lm)
                what[w] += 1
                if w == "kept":
                    print(f"[land] ⚠ meets_ext {ext} was already promoted and its page changed: "
                          f"left as it was ({lm.url})")
        conn.commit()
    print(f"[land] written: {dict(what)}")
    print("[land] next: python -m sources.timers dedup            # which are already in a feed")
    return 0


def cmdDedup(a):
    from database import getConn
    from sources import dedup
    with getConn() as conn:
        with conn.cursor() as cur:
            land.initTables(cur)
        conn.commit()
        dedup.run(conn, write=a.write, limit=a.limit, show=a.show,
                  ext_ids=[int(x) for x in a.ext] if a.ext else None)
    return 0


def cmdPromote(a):
    from database import getConn
    from sources import promote
    with getConn() as conn:
        promote.run(conn, apply=a.apply, limit=a.limit, show=a.show)
    if a.apply:
        print("[promote] next: the nightly 04a links them (or now: "
              "python scripts/link_uploads.py --source timer [--apply])")
    return 0


def cmdStatus(a):
    from database import getConn
    from sources import dedup
    with getConn() as conn:
        with conn.cursor() as cur:
            land.initTables(cur)
            conn.commit()
            cur.execute("""SELECT source, status, count(*), sum(n_rows) FROM meets_ext
                           GROUP BY 1, 2 ORDER BY 1, 2""")
            rows = cur.fetchall()
            print(f"{'source':<34} {'status':<9} {'meets':>6} {'rows':>8}")
            for s, st, n, r in rows:
                print(f"{s:<34} {st:<9} {n:>6} {int(r or 0):>8}")
            cur.execute("""SELECT 'XC', count(*), count(person_id) FROM results WHERE source = 'timer'
                           UNION ALL
                           SELECT 'TF', count(*), count(person_id) FROM results_tf WHERE source = 'timer'""")
            for sp, n, linked in cur.fetchall():
                print(f"promoted {sp}: {n:,} rows, {linked:,} with a person")
            dis = dedup.distanceDisagreements(cur, limit=a.show)
            if dis:
                print(f"\ntwin races whose feed distance differs from the timer's by more than "
                      f"{dedup.DISTANCE_TOL:.0%}:")
                for ext, name, date, title, stated, feed, src, mid, div in dis:
                    print(f"  {str(date)[:10]} {(name or '')[:40]:<40} {(title or '')[:28]:<28} "
                          f"timer {stated:>6.0f} m  {src} {feed:>6.0f} m  (meet {mid}, div {div})")
        conn.rollback()
    return 0


REJECT_REST_DAYS = 3         # a page that did not land is not offered again for this long
REJECT_TRIES = 5             # ... and after this many looks, not at all
RETRY_AGE = 2 * 86400        # a rejected page, asked again, is read from the store only if this fresh
RECENT_DAYS = 10             # a meet this many days old or newer ...
RECENT_AGE = 18 * 3600       # ... is read again when its stored copy is older than this


def _recent(date, today=None):
    """True for an ISO date within the last RECENT_DAYS (or later). Pure."""
    import datetime as _dt
    try:
        d = _dt.date.fromisoformat(str(date)[:10])
    except (TypeError, ValueError):
        return False
    return (d - (today or _dt.date.today())).days >= -RECENT_DAYS


async def readMeet(fetcher, slug, url, kind=None, retried=False):
    """(LandedMeet | None, note) for one meet page off a timer's index.

    ★ A MEET OF THE LAST FEW DAYS IS READ FRESH (2026-11-10). A page stored on
      the meet's own day ("dated today, lands tomorrow") came back from the
      30-day store the next night, and the meet landed -- for good, promoted
      is final -- with only the races posted on day one. A meet RECENT_DAYS
      old or newer is read again when its stored copy is older than
      RECENT_AGE: a conditional request, so an unchanged page costs the timer
      a 304. A page asked again after it did not land (retried) is read
      fresh too (RETRY_AGE), or the store would give the same answer."""
    if retried:
        return await _meet(fetcher, slug, url, kind, RETRY_AGE)
    lm, why = await _meet(fetcher, slug, url, kind, None)
    if lm is not None and _recent(lm.date):
        lm, why = await _meet(fetcher, slug, url, kind, RECENT_AGE)
    return lm, why


async def _meet(fetcher, slug, url, kind, max_age):
    if isinstance(kind, tuple):          # ('group', [report files]) -- one meet
        return await mergeReports(fetcher, slug, url, kind[1], MAX_AGE if max_age is None else max_age)
    return await fetchMeet(fetcher, slug, url, kind if kind == "meetpro" else None, max_age)


def _outcome(lm, why, today=None):
    """What became of one meet page, as report() decides it below. Pure."""
    import datetime as _dt
    today = (today or _dt.date.today()).isoformat()
    if lm is not None and lm.races and lm.date and lm.date >= today:
        return "waits a day"
    if lm is None or not lm.races:
        return f"not parsed: {why if lm is None else 'no individual races'}"[:200]
    if not lm.date:
        return "no date"
    if land.stateOf(lm.place, lm.venue) in land.CANADA:
        return "outside the US"
    return "landed"


def noteSeen(cur, url, slug, lm, outcome):
    """One meet page's outcome into ext_seen (tries counts the looks)."""
    races = len(lm.races) if lm is not None else None
    cur.execute("""INSERT INTO ext_seen (url, source, last_tried, tries, outcome, meet_name, meet_date,
                                         sport, n_races, n_rows)
                   VALUES (%s, %s, now(), 1, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (url) DO UPDATE SET last_tried = now(), tries = ext_seen.tries + 1,
                     outcome = EXCLUDED.outcome,
                     meet_name = COALESCE(EXCLUDED.meet_name, ext_seen.meet_name),
                     meet_date = COALESCE(EXCLUDED.meet_date, ext_seen.meet_date),
                     sport = COALESCE(EXCLUDED.sport, ext_seen.sport),
                     n_races = EXCLUDED.n_races, n_rows = EXCLUDED.n_rows""",
                (url, land.EXT_PREFIX + slug, outcome,
                 getattr(lm, "name", None), getattr(lm, "date", None) or None,
                 lm.sport if lm is not None and lm.races else None,
                 races, lm.rowCount() if lm is not None else None))


def cmdAll(a):
    """discover -> fetch -> land -> dedup -> promote, every enabled timer."""
    if a.dry:
        return _all(a, None)
    from database import getConn
    with getConn() as conn:
        return _all(a, conn)


def _all(a, conn):
    """cmdAll with its connection (None for a dry run)."""
    import datetime as _dt
    from collections import Counter
    from sources import discover
    from sources.fetch import Fetcher, RawStore
    since = a.since_year or _dt.date.today().year
    srcs = [s for s in registry.load() if s.enabled and s.kind != "blocked"]
    # the platforms read whole (Arrow, Race Roster): run when the registry
    # lists timers on them
    platforms = {s.kind for s in registry.load()}
    # ! --try-kind (dry runs only): sources of a kind not switched on yet,
    #   through the same reader, to see which already parse
    if getattr(a, "try_kind", None):
        if not a.dry:
            raise SystemExit("--try-kind is for --dry runs only")
        srcs = [s for s in registry.load() if s.kind in set(a.try_kind) and s.kind != "blocked"
                and not registry.offLimits(s.results_url or s.website or "")]
    if a.only:
        srcs = [s for s in srcs if s.slug in set(a.only)]
    known, retry = set(), set()
    if conn is not None:
        with conn.cursor() as cur:
            land.initTables(cur)
            cur.execute("SELECT url FROM meets_ext")
            known = {r[0] for r in cur.fetchall()}
            # ★ A PAGE THAT DID NOT LAND RESTS, THEN IS ASKED AGAIN, THEN IS
            #   LEFT (2026-11-10). Before, it was never remembered: Trey
            #   Jackson's empty 2026 templates and Jeff Offringa's relays meets
            #   sat at the top of their indexes and took every one of the
            #   timer's --per-source slots each night, so the meets under them
            #   ("wait for later nights") never came. Now it is out of the
            #   running for REJECT_REST_DAYS, back for another look after that
            #   (read fresh, see RETRY_AGE), and dropped after REJECT_TRIES.
            cur.execute("""SELECT url, last_tried > now() - make_interval(days => %s) OR tries >= %s
                           FROM ext_seen WHERE outcome IS DISTINCT FROM 'landed'
                             AND outcome IS DISTINCT FROM 'waits a day'""",
                        (REJECT_REST_DAYS, REJECT_TRIES))
            for url, resting in cur.fetchall():
                (known if resting else retry).add(url)
        conn.commit()
    # ★ A BUDGET, AND LANDED AS IT GOES (2026-11-09). The nightly kills this
    #   step at its bound (01_local_timers, 40 minutes); everything used to be
    #   landed only after the LAST timer finished, so one slow host (a MeetPro
    #   meet is 20-150 pages, a Crawl-delay of 30 s) cost the whole night's
    #   meets. Now each timer's meets are landed the moment it is done, and
    #   past --minutes no new meet is started: what is parsed is kept, the rest
    #   is found again tomorrow (pages already read come from the store).
    budget = a.minutes if a.minutes is not None else float(os.environ.get("XCP_TIMERS_MINUTES", "0") or 0)
    deadline = _now() + budget * 60 if budget else None
    print(f"[all] {len(srcs)} timers, meets from {since} on, up to {a.per_source} new each; "
          f"{len(known):,} pages already landed" + (f"; {budget:g}-minute budget" if budget else ""))
    # ! A PDF REPORT NEEDS pdftotext (poppler-utils; deploy/server_setup.sh).
    #   The server had none on 2026-11-07 and every PDF meet failed with
    #   FileNotFoundError, once per meet, quietly. Said once, loudly, here;
    #   and those failures are not counted as a page's tries (noteSeen below).
    import shutil
    if shutil.which("pdftotext") is None:
        print("[all] ⚠ pdftotext is not installed: every PDF results report will fail to parse."
              " Install it: apt-get install -y poppler-utils", flush=True)
    fetcher = Fetcher(RawStore())

    async def one(src):
        try:
            urls, note = await discover.discover(fetcher, src, since, a.per_source, known)
        except Exception as exc:                                    # noqa: BLE001
            return src, [], f"discover failed: {type(exc).__name__}: {exc}"
        got = []
        for u, kind in urls:
            if deadline and _now() > deadline:
                note += f"; budget spent, {len(urls) - len(got)} meets wait for the next run"
                break
            try:
                lm, why = await readMeet(fetcher, src.slug, u, kind, u in retry)
            except Exception as exc:                                # noqa: BLE001
                lm, why = None, f"{type(exc).__name__}: {exc}"
            got.append((u, lm, why))
        return src, got, note

    os.makedirs(os.path.join(_ROOT, "logs"), exist_ok=True)
    out = a.out or os.path.join(_ROOT, "logs", f"timers_{_dt.date.today().isoformat()}.jsonl")
    meets, tally, what = [], Counter(), Counter()

    def report(src, got, note, f):
        ok, mine = 0, []
        seen = []
        for u, lm, why in got:
            # a failure of OURS (a missing tool) is not the page's: not recorded,
            # so it is offered again tomorrow with no rest and no try used
            if not (lm is None and "pdftotext" in str(why)):
                seen.append((u, src.slug, lm, _outcome(lm, why)))
            if lm is not None and lm.races and lm.date and lm.date >= _dt.date.today().isoformat():
                # ★ NOT ON ITS OWN DAY (2026-11-01): promoted is final, so a
                #   meet still being posted would stay short; it is not
                #   landed, so tomorrow's run finds it again
                tally["posted today: waits a day"] += 1
                print(f"  … {src.slug}: {land.summary(lm)} -- dated today, lands tomorrow")
                continue
            if lm is None or not lm.races:
                tally["not parsed"] += 1
                print(f"  ✗ {src.slug}: {u} -- {why if lm is None else 'no individual races'}")
                continue
            # ! NO DATE, NO MEET: a race is rated on its day; and a meet placed
            #   in a Canadian province fits no pool we rate
            if not lm.date or land.stateOf(lm.place, lm.venue) in land.CANADA:
                tally["no date" if not lm.date else "outside the US"] += 1
                print(f"  ✗ {src.slug}: {u} -- " + ("no date on the page" if not lm.date
                                                    else f"outside the US ({lm.place or lm.venue})"))
                continue
            f.write(json.dumps(lm.toJson()) + "\n")
            f.flush()
            mine.append(lm)
            ok += 1
            print(f"  ✓ {src.slug}: {land.summary(lm)}")
        tally["timers with a new meet"] += int(ok > 0)
        print(f"[all] {src.slug:<44} {note}; parsed {ok}", flush=True)
        if conn is not None and (mine or seen):
            with conn.cursor() as cur:
                for lm in mine:
                    what[land.landMeet(cur, lm)[1]] += 1
                for row in seen:
                    noteSeen(cur, *row)
            conn.commit()
        meets.extend(mine)

    # ★ ARROW LIVE RESULTS, ONE JOB FOR EVERY TIMER ON IT (2026-11-10;
    #   sources/arrow.py): 34 timers in the research file post there, none
    #   was read. Same budget, store, rests and fresh re-reads as the timers
    #   above. XCP_ARROW=0 leaves it out; XCP_ARROW_PER_RUN meets a night.
    async def arrowJob():
        from types import SimpleNamespace
        from sources import arrow
        src = SimpleNamespace(slug=arrow.SLUG)
        if (os.environ.get("XCP_ARROW", "1") == "0" or (a.only and arrow.SLUG not in set(a.only))
                or not platforms & {"arrow"}):
            return src, [], "off"
        try:
            meets, note = await arrow.discover(fetcher, since, int(os.environ.get("XCP_ARROW_PER_RUN", "12")),
                                               known)
        except Exception as exc:                                    # noqa: BLE001
            return src, [], f"discover failed: {type(exc).__name__}: {exc}"
        got = []
        for meet in meets:
            if deadline and _now() > deadline:
                note += f"; budget spent, {len(meets) - len(got)} meets wait for the next run"
                break
            try:
                lm, why = await arrow.readMeet(fetcher, meet, RETRY_AGE if meet["url"] in retry else MAX_AGE)
                if lm is not None and _recent(lm.date) and meet["url"] not in retry:
                    lm, why = await arrow.readMeet(fetcher, meet, RECENT_AGE)
            except Exception as exc:                                # noqa: BLE001
                lm, why = None, f"{type(exc).__name__}: {exc}"
            got.append((meet["url"], lm, why))
        return src, got, note

    # ★ RACE ROSTER, THE SAME WAY (2026-11-10; sources/raceroster.py): the
    #   school meets a dozen timers post there (NE, IA, MO, OH, NH, SC, WI
    #   ...). XCP_RACEROSTER=0 leaves it out; XCP_RACEROSTER_PER_RUN a night.
    async def raceRosterJob():
        from types import SimpleNamespace
        from sources import raceroster
        src = SimpleNamespace(slug=raceroster.SLUG)
        if (os.environ.get("XCP_RACEROSTER", "1") == "0" or (a.only and raceroster.SLUG not in set(a.only))
                or not platforms & {"raceroster"}):
            return src, [], "off"
        try:
            todo, note = await raceroster.discover(
                fetcher, f"{since}-08-01", int(os.environ.get("XCP_RACEROSTER_PER_RUN", "15")), known)
        except Exception as exc:                                    # noqa: BLE001
            return src, [], f"discover failed: {type(exc).__name__}: {exc}"
        got = []
        for key, code, eid, name, _date in todo:
            if deadline and _now() > deadline:
                note += f"; budget spent, {len(todo) - len(got)} meets wait for the next run"
                break
            try:
                code = code or await raceroster.resolveCode(fetcher, eid)
                if not code:
                    lm, why = None, "no results code"
                else:
                    lm, why = await raceroster.readMeet(fetcher, code, key,
                                                        RETRY_AGE if key in retry else MAX_AGE)
                    if lm is not None and _recent(lm.date) and key not in retry:
                        lm, why = await raceroster.readMeet(fetcher, code, key, RECENT_AGE)
            except Exception as exc:                                # noqa: BLE001
                lm, why = None, f"{type(exc).__name__}: {exc}"
            got.append((key, lm, why))
        return src, got, note

    async def go(f):
        for fut in asyncio.as_completed([one(s) for s in srcs] + [arrowJob(), raceRosterJob()]):
            report(*(await fut), f)

    with open(out, "w", encoding="utf-8") as f:
        asyncio.run(go(f))
    rows = sum(m.rowCount() for m in meets)
    print(f"[all] parsed {len(meets)} meets, {rows:,} rows -> {out}; {dict(tally)}; fetcher {fetcher.stats}")
    if a.dry or not meets:
        print("[all] dry run -- nothing written" if a.dry else "[all] nothing new to land")
        return 0
    from sources import dedup, promote
    print(f"[all] landed: {dict(what)}")
    dedup.run(conn, write=True, show=a.show)
    promote.run(conn, apply=True, show=a.show)
    print("[all] done. Link them now with: python scripts/link_uploads.py --source timer --apply "
          "(the nightly 04a does it too), then the pipeline rates them")
    return 0


def cmdInit(_a):
    from database import getConn
    with getConn() as conn:
        with conn.cursor() as cur:
            land.initTables(cur)
        conn.commit()
    print("[timers] meets_ext, races_ext, results_ext ready")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    f = sub.add_parser("fetch")
    f.add_argument("--source", required=True, help="the timer's slug (python -m sources.registry --enabled)")
    f.add_argument("--url", action="append")
    f.add_argument("--urls", help="a file of meet urls, one a line")
    f.add_argument("--kind", choices=("meetpro", "hytek"))
    f.add_argument("--out", required=True)
    ld = sub.add_parser("land")
    ld.add_argument("--from", dest="from_", required=True)
    ld.add_argument("--write", action="store_true")
    d = sub.add_parser("dedup")
    d.add_argument("--write", action="store_true")
    d.add_argument("--limit", type=int)
    d.add_argument("--show", type=int, default=40)
    d.add_argument("--ext", action="append", help="judge this ext_meet_id again")
    p = sub.add_parser("promote")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--limit", type=int)
    p.add_argument("--show", type=int, default=40)
    s = sub.add_parser("status")
    s.add_argument("--show", type=int, default=40)
    al = sub.add_parser("all", help="discover + fetch + land + dedup + promote, every enabled timer")
    al.add_argument("--dry", action="store_true", help="parse only; write nothing")
    al.add_argument("--only", action="append", help="this timer's slug (repeatable)")
    al.add_argument("--per-source", type=int, default=10)
    al.add_argument("--since-year", type=int)
    al.add_argument("--out")
    al.add_argument("--show", type=int, default=20)
    al.add_argument("--minutes", type=float, help="start no new meet after this long (XCP_TIMERS_MINUTES)")
    al.add_argument("--try-kind", action="append",
                    help="(--dry only) the sources of this registry kind, switched on or not")
    a = ap.parse_args(argv)
    return {"all": cmdAll, "init": cmdInit, "fetch": cmdFetch, "land": cmdLand, "dedup": cmdDedup,
            "promote": cmdPromote, "status": cmdStatus}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
