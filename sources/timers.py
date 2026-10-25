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
import urllib.parse

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from sources import hytek, land, meetpro, registry                 # noqa: E402

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
    if "tfmeetpro.com" in u or re.search(r"/mp/", u) or b"MeetPro" in (body or b"")[:20000]:
        return "meetpro"
    return "hytek"


def _isChallenge(body):
    """A bot challenge page: never worked around, only reported."""
    head = (body or b"")[:6000].lower()
    return (b"cf-chl" in head or b"challenge-platform" in head or b"just a moment..." in head
            or b"attention required! | cloudflare" in head)


async def fetchMeet(fetcher, slug, url, kind=None):
    """(LandedMeet | None, note) for one meet url."""
    if registry.offLimits(url):
        return None, "off limits (terms or robots.txt)"
    got = await fetcher.fetch(url, MAX_AGE)
    if not got.body:
        return None, got.note or f"HTTP {got.status}"
    if _isChallenge(got.body):
        return None, "bot challenge -- not worked around"
    kind = kind or kindOf(url, got.body)
    if kind == "meetpro":
        page = got.body.decode("utf-8", "replace")
        idx = meetpro.parseMeetIndex(page)
        results = {}
        for e in idx.events:
            if not e.results_href or e.relay:
                continue
            r = await fetcher.fetch(urllib.parse.urljoin(url, e.results_href), MAX_AGE)
            if r.body:
                results[e.number] = meetpro.parseRounds(r.body.decode("utf-8", "replace"))
        return land.fromMeetPro(idx, results, slug, url), "meetpro"
    listing = directoryReports(got.body.decode("utf-8", "replace"), url) if got.body[:5] != b"%PDF-" else []
    if listing:
        merged = None
        for k, rurl in enumerate(listing[:MAX_PAGES], 1):
            r = await fetcher.fetch(rurl, MAX_AGE)
            if not r.body or _isChallenge(r.body):
                continue
            lm = await _hytekReport(fetcher, slug, rurl, r.body)
            if merged is None:
                merged = land.LandedMeet(slug=slug, url=url, name=lm.name, date=lm.date, venue=lm.venue,
                                         place=lm.place, state=lm.state, licensee=lm.licensee)
            for race in lm.races:            # each report numbers its events from 1
                race.n = k * 1000 + race.n
                merged.races.append(race)
            merged.skipped += lm.skipped
        return merged, f"hytek directory ({len(listing)} reports)"
    return await _hytekReport(fetcher, slug, url, got.body), "hytek"


async def _hytekReport(fetcher, slug, url, body):
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
        r = await fetcher.fetch(cur, MAX_AGE)
        body = r.body
        meta = fetcher.store.meta(cur) or {}
    meet = hytek.parseReport("\n".join(texts))
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
    a = ap.parse_args(argv)
    return {"init": cmdInit, "fetch": cmdFetch, "land": cmdLand, "dedup": cmdDedup,
            "promote": cmdPromote, "status": cmdStatus}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
