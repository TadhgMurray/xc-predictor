# Project: xc-predictor
# File:    sources/tfrrs_hs.py
# Purpose: TFRRS's high school state sites -- fl.tfrrs.org (Florida's whole
#          official FHSAA series since 2019, Broward weeklies, county meets)
#          and indiana.tfrrs.org (IHSAA, 100+ meets a year) -- measured
#          against what the TFRRS scrape already holds, and the gaps queued
#          for it.
#
#   python -m sources.tfrrs_hs index --out hs_index.csv [--host fl.tfrrs.org ...] [--pages N]
#   python -m sources.tfrrs_hs check --index hs_index.csv           # what we hold, linked how
#   python -m sources.tfrrs_hs queue --index hs_index.csv [--apply] # the missing -> meet_queue,
#                                                                   # and the list -> tfrrs_hs_meets
#   python -m sources.tfrrs_hs store --index hs_index.csv           # the list alone
#   python -m sources.tfrrs_hs refresh [--pages 3]                  # nightly: newest pages, kept + queued
#   python -m sources.tfrrs_hs latest [--pages 3] [--sport XC]      # www.tfrrs.org's newest, queued first
#   then: docs/RUNBOOK-TIMERS-2026-10-25.md A4                      # scrape just those
#
# ★ THE SAME MEETS, THE SAME IDS (checked 2026-10-25). A state site is a
#   view of TFRRS's one database: fl.tfrrs.org/results/xc/28712 and
#   www.tfrrs.org/results/xc/28712 are the same page (Bronco's Ranch,
#   2026-10-25), and the athlete links on it point at florida.tfrrs.org with
#   TFRRS's own athlete ids. So the existing scraper (tfrrs/driver, which
#   walks www.tfrrs.org's whole id range) needs no new host and no new id
#   space: a high school meet is a meet_queue row like any other, and its
#   rows land as source 'tfrrs' and go through 04a's fan-out, freshmen and
#   mint passes. What the state site adds is the LIST -- results_search.html
#   pages every meet the state posts -- which says which ids are high school
#   meets and lets us check none was missed. The list is kept in
#   tfrrs_hs_meets, and 04a pass 2b (scripts/link_tfrrs_hs.py) uses it to
#   join a high schooler's TFRRS rows to their athletic.net career instead of
#   minting a second person.
#
# ! THE INDEX ONLY, AND ONE HOST AT A TIME. All three hosts are one TFRRS
#   server, and the nightly TFRRS scrape is on it too, so the walk is one
#   request in flight in total (sources.fetch, 10 s apart), not one per
#   host name. fl.tfrrs.org's index is ~230 pages, indiana's ~150: about an
#   hour for both. robots.txt on both allows everything (2026-10-25).

import argparse
import asyncio
import csv
import datetime
import html as _html
import os
import re
import sys
from collections import Counter, defaultdict

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

HOSTS = ("fl.tfrrs.org", "indiana.tfrrs.org")
FIELDS = ("host", "meet_id", "sport", "date", "name", "url")
# tfrrs mints persons in these ranges (scripts/link_tfrrs_rows.MINT_BASE)
MINT_LO, MINT_HI = 1_000_000_000, 2_000_000_000

_ROW = re.compile(r"<tr>\s*<td>\s*([0-9/-]+)\s*</td>\s*<td>\s*<a[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>\s*</td>"
                  r"\s*<td>\s*([^<]*?)\s*</td>", re.S)
_HREF = re.compile(r"/results/(xc/)?(\d+)(?:/|$)")
_LAST = re.compile(r"results_search\.html\?page=(\d+)")


def parseIndexPage(page, host):
    """One results_search.html page -> [dict(FIELDS)]. Pure."""
    out = []
    for d, href, name, sport in _ROW.findall(page):
        m = _HREF.search(href)
        if not m:
            continue
        try:
            # '03/19-20/26' is a two-day meet: its first day
            mm, dd, yy = (int(x.split("-")[0]) for x in d.split("/"))
            iso = datetime.date(2000 + yy if yy < 100 else yy, mm, dd).isoformat()
        except ValueError:
            iso = ""
        sp = "XC" if (m.group(1) or "cross country" in sport.lower()) else "TF"
        url = href if href.startswith("http") else f"https://{host}{href}"
        out.append({"host": host, "meet_id": int(m.group(2)), "sport": sp, "date": iso,
                    "name": _html.unescape(re.sub(r"<[^>]+>", "", name)).strip(), "url": url})
    return out


def lastPage(page):
    nums = [int(n) for n in _LAST.findall(page)]
    return max(nums) if nums else 1


async def _walk(host, pages, fetcher):
    first = await fetcher.fetch(f"https://{host}/results_search.html")
    if not first.body:
        print(f"[tfrrs-hs] {host}: {first.note or first.status} -- skipped")
        return []
    text = first.body.decode("utf-8", "replace")
    last = lastPage(text)
    if pages:
        last = min(last, pages)
    rows = parseIndexPage(text, host)
    print(f"[tfrrs-hs] {host}: {last} index pages", flush=True)
    for k in range(2, last + 1):
        got = await fetcher.fetch(f"https://{host}/results_search.html?page={k}")
        if not got.body:
            print(f"[tfrrs-hs] {host} page {k}: {got.note or got.status}")
            continue
        rows += parseIndexPage(got.body.decode("utf-8", "replace"), host)
        if k % 25 == 0:
            print(f"[tfrrs-hs] {host}: page {k}/{last}, {len(rows):,} meets", flush=True)
    return rows


def walk(hosts, pages):
    """The index rows of every host, one host at a time, one request in
    flight in total; duplicates (an id on two hosts) dropped."""
    from sources.fetch import Fetcher, HostLimiter, RawStore
    fetcher = Fetcher(RawStore(), limiter=HostLimiter(), max_in_flight=1)

    async def go():                 # one loop: the fetcher's locks belong to it
        got = []
        for host in hosts:
            got += await _walk(host, pages, fetcher)
        return got

    rows, seen = [], set()
    for r in asyncio.run(go()):
        k = (r["meet_id"], r["sport"])
        if k not in seen:
            seen.add(k)
            rows.append(r)
    return rows, fetcher.stats


def writeIndex(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def cmdIndex(a):
    rows, stats = walk(a.host or HOSTS, a.pages)
    writeIndex(a.out, rows)
    by = Counter((r["host"], r["sport"], r["date"][:4]) for r in rows)
    print(f"[tfrrs-hs] {len(rows):,} meets -> {a.out}  (fetcher {stats})")
    for (h, s, y), n in sorted(by.items()):
        print(f"    {h:<20} {s} {y or '????'} {n:>5}")
    return 0


def readIndex(path):
    with open(path, newline="", encoding="utf-8") as f:
        return [dict(r, meet_id=int(r["meet_id"])) for r in csv.DictReader(f)]


def _stage(cur, rows):
    from psycopg2.extras import execute_values
    cur.execute("DROP TABLE IF EXISTS hs_idx")
    cur.execute("CREATE TEMP TABLE hs_idx (host text, meet_id bigint, sport text, date text, name text)")
    execute_values(cur, "INSERT INTO hs_idx VALUES %s",
                   [(r["host"], r["meet_id"], r["sport"], r["date"], r["name"]) for r in rows])
    cur.execute("CREATE INDEX ON hs_idx (meet_id, sport)")


def facts(cur):
    """Per indexed meet: in meets_tfrrs, queue state, rows, linked, minted,
    on an anet career. One statement per table."""
    out = {}
    cur.execute("""SELECT i.meet_id, i.sport, (m.meet_id IS NOT NULL), q.scraped
                   FROM hs_idx i
                   LEFT JOIN meets_tfrrs m ON m.meet_id = i.meet_id AND m.sport = i.sport
                   LEFT JOIN meet_queue q ON q.meet_id = i.meet_id AND q.sport = i.sport
                                         AND q.source = 'tfrrs'""")
    for mid, sp, meta, q in cur.fetchall():
        out[(mid, sp)] = {"meta": meta, "queue": q, "rows": 0, "linked": 0, "minted": 0, "career": 0}
    for sport, table in (("XC", "results"), ("TF", "results_tf")):
        cur.execute(f"""
            SELECT r.meet_id, count(*), count(r.person_id),
                   count(*) FILTER (WHERE r.person_id >= {MINT_LO} AND r.person_id < {MINT_HI}),
                   count(*) FILTER (WHERE r.person_id IS NOT NULL
                                      AND EXISTS (SELECT 1 FROM athletes a WHERE a.person_id = r.person_id))
            FROM   {table} r
            WHERE  r.source = 'tfrrs'
              AND  r.meet_id IN (SELECT meet_id FROM hs_idx WHERE sport = %s)
            GROUP  BY r.meet_id""", (sport,))
        for mid, n, linked, minted, career in cur.fetchall():
            f = out.get((mid, sport))
            if f is not None:
                f.update(rows=n, linked=linked, minted=minted, career=career)
    return out


def cmdCheck(a):
    from database import getConn
    rows = readIndex(a.index)
    with getConn() as conn:
        with conn.cursor() as cur:
            _stage(cur, rows)
            fx = facts(cur)
        conn.rollback()
    agg = defaultdict(Counter)
    for r in rows:
        f = fx.get((r["meet_id"], r["sport"]), {})
        k = (r["host"], r["sport"], r["date"][:4])
        c = agg[k]
        c["meets"] += 1
        c["with rows"] += 1 if f.get("rows") else 0
        c["meta only"] += 1 if (f.get("meta") and not f.get("rows")) else 0
        c["missing"] += 1 if not (f.get("meta") or f.get("rows")) else 0
        for x in ("rows", "linked", "minted", "career"):
            c[x] += f.get(x, 0)
    hdr = f"{'host':<18} {'sp':<2} {'year':<4} {'meets':>6} {'w/rows':>6} {'empty':>6} {'missing':>7} " \
          f"{'rows':>8} {'linked':>8} {'minted':>8} {'career':>8}"
    print(hdr)
    tot = Counter()
    for k in sorted(agg):
        c = agg[k]
        tot.update(c)
        print(f"{k[0]:<18} {k[1]:<2} {k[2] or '????':<4} {c['meets']:>6} {c['with rows']:>6} {c['meta only']:>6} "
              f"{c['missing']:>7} {c['rows']:>8,} {c['linked']:>8,} {c['minted']:>8,} {c['career']:>8,}")
    print(f"{'TOTAL':<26} {tot['meets']:>6} {tot['with rows']:>6} {tot['meta only']:>6} {tot['missing']:>7} "
          f"{tot['rows']:>8,} {tot['linked']:>8,} {tot['minted']:>8,} {tot['career']:>8,}")
    print("\n  w/rows   meets whose results are in results / results_tf (source tfrrs)")
    print("  empty    in meets_tfrrs with no result rows (scraped before results went up, or a TF/XC id mix)")
    print("  missing  not in meets_tfrrs at all -> `queue` puts these (and the empty ones) back in meet_queue")
    print("  minted   rows on a person 04a minted from the tfrrs id (no athletic.net career found)")
    print("  career   rows on a person with an athletic.net profile")
    states = Counter(str(f.get("queue")) for f in fx.values() if not f.get("rows"))
    if states:
        print(f"\n  meet_queue state of the meets with no rows: {dict(states)}  "
              f"(0 due, 1 done, 2 failed, 3 in progress, 4 not a meet, None not queued)")
    return 0


def store(cur, rows):
    """tfrrs_hs_meets: which TFRRS meet ids are a state site's high school
    meets -- what scripts/link_tfrrs_hs.py (04a pass 2b) reads."""
    from psycopg2.extras import execute_values
    from link_tfrrs_hs import HS_MEETS_DDL
    cur.execute(HS_MEETS_DDL)
    execute_values(cur, """
        INSERT INTO tfrrs_hs_meets (meet_id, sport, host, date, name) VALUES %s
        ON CONFLICT (meet_id, sport) DO UPDATE SET host = EXCLUDED.host, date = EXCLUDED.date,
                                                   name = EXCLUDED.name""",
                   [(r["meet_id"], r["sport"], r["host"], r["date"], r["name"]) for r in rows])
    return len(rows)


def cmdStore(a):
    from database import getConn
    rows = readIndex(a.index)
    with getConn() as conn:
        with conn.cursor() as cur:
            n = store(cur, rows)
        conn.commit()
    print(f"[tfrrs-hs] tfrrs_hs_meets holds the {n:,} indexed meets")
    return 0


def queueMissing(cur, rows, since, apply, redo_done=True):
    """The indexed meets since `since` with no result rows -> meet_queue
    (see cmdQueue). redo_done=False leaves a meet the scrape finished (in
    meets_tfrrs, queue state 1) alone: the nightly refresh would otherwise
    re-queue a field-only meet every night, and the TFRRS seeder already
    retries recent meets with no results. -> (wanted, queued)."""
    from psycopg2.extras import execute_values
    _stage(cur, rows)
    fx = facts(cur)
    def wanted(f):
        if f.get("rows"):
            return False
        return redo_done or not (f.get("meta") and f.get("queue") == 1)
    todo = sorted({(r["meet_id"], r["sport"]) for r in rows
                   if wanted(fx.get((r["meet_id"], r["sport"]), {}))
                   and r["date"] >= since and r["date"] <= datetime.date.today().isoformat()})
    if not apply or not todo:
        return todo, 0
    # ★ PRIORITY 1: TFRRS's own index says these are meets with results, so
    #   the drain takes them before blind forward-walk ids (database.
    #   _claimTFRRSBatch orders by priority first)
    execute_values(cur, """
        INSERT INTO meet_queue (meet_id, sport, source, scraped, priority)
        VALUES %s
        ON CONFLICT (meet_id, sport, source) DO UPDATE SET scraped = 0, priority = 1
        WHERE meet_queue.scraped <> 3""", [(m, s, "tfrrs", 0, 1) for m, s in todo])
    return todo, cur.rowcount


def cmdRefresh(a):
    """The nightly step: the newest index pages, kept and the gaps queued."""
    from database import getConn
    rows, stats = walk(a.host or HOSTS, a.pages)
    if a.out:
        writeIndex(a.out, rows)
    if not rows:
        print(f"[tfrrs-hs] no index rows read (fetcher {stats}) -- nothing kept or queued")
        return 0
    since = (datetime.date.today() - datetime.timedelta(days=a.since_days)).isoformat()
    with getConn() as conn:
        with conn.cursor() as cur:
            store(cur, rows)
            todo, n = queueMissing(cur, rows, since, True, redo_done=False)
        conn.commit()
    print(f"[tfrrs-hs] {len(rows):,} newest meets kept in tfrrs_hs_meets; {len(todo)} since {since} with no rows, "
          f"{n} queued (fetcher {stats})")
    return 0


def cmdLatest(a):
    """★ THE WHOLE OF TFRRS'S NEWEST RESULTS (owner, 2026-10-30: the Mike
    Woods Classic, XC 28447, posted after later ids had been walked and so
    never asked). www.tfrrs.org's results_search.html lists every meet
    newest first; its first pages name what was posted lately, wherever its
    id sits, and the ones with no result rows are queued at priority 1.
    NOT kept in tfrrs_hs_meets: that table says "high school meet" to 04a
    pass 2b, and these are every level."""
    from database import getConn
    rows, stats = walk(("www.tfrrs.org",), a.pages)
    if a.out:
        writeIndex(a.out, rows)
    if not rows:
        print(f"[tfrrs-latest] no index rows read (fetcher {stats}) -- nothing queued")
        return 0
    if a.sport:
        rows = [r for r in rows if r["sport"] == a.sport]
    since = (datetime.date.today() - datetime.timedelta(days=a.since_days)).isoformat()
    with getConn() as conn:
        with conn.cursor() as cur:
            todo, n = queueMissing(cur, rows, since, True, redo_done=False)
        conn.commit()
    print(f"[tfrrs-latest] {len(rows):,} newest meets read; {len(todo)} since {since} with no rows, "
          f"{n} queued at priority 1 (fetcher {stats})")
    for m, sp in todo[:20]:
        print(f"  queued {sp} {m}")
    return 0


def cmdQueue(a):
    """The indexed meets with no result rows -> meet_queue (source 'tfrrs',
    scraped 0). A row already there is reset to due unless a scrape holds it
    (state 3); 'done' (1) and 'not a meet' (4) included: the state site says
    it is a meet, and it has no rows."""
    from database import getConn
    rows = readIndex(a.index)
    since = a.since or ""
    with getConn() as conn:
        with conn.cursor() as cur:
            todo, _n = queueMissing(cur, rows, since, False)
            by = Counter(s for _m, s in todo)
            print(f"[tfrrs-hs] {len(todo):,} indexed meets with no rows{f' since {since}' if since else ''}: "
                  f"{dict(by)}")
            if not a.apply:
                conn.rollback()
                print("[tfrrs-hs] dry run -- nothing queued; --apply to queue them")
                return 0
            store(cur, rows)
            todo, n = queueMissing(cur, rows, since, True)
        conn.commit()
    print(f"[tfrrs-hs] queued {n:,} (source tfrrs, due); tfrrs_hs_meets holds all {len(rows):,} indexed meets. "
          f"Scrape just these:")
    print("    TFRRS_NO_SEED=1 TFRRS_SEED_AHEAD=50 TFRRS_SESSIONS=2 TFRRS_PER_MEET_DELAY=5,10 NO_VPN=1 "
          "timeout 180m xvfb-run -a $P -u tfrrs/driver/launch_tfrrs.py")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("index")
    i.add_argument("--out", required=True)
    i.add_argument("--host", action="append", help=f"default: {', '.join(HOSTS)}")
    i.add_argument("--pages", type=int, help="only the first N index pages (the newest meets)")
    c = sub.add_parser("check")
    c.add_argument("--index", required=True)
    rf = sub.add_parser("refresh")
    rf.add_argument("--host", action="append")
    rf.add_argument("--pages", type=int, default=3)
    rf.add_argument("--since-days", type=int, default=30)
    rf.add_argument("--out")
    st = sub.add_parser("store")
    st.add_argument("--index", required=True)
    q = sub.add_parser("queue")
    q.add_argument("--index", required=True)
    q.add_argument("--since", help="only meets on or after this date (ISO)")
    q.add_argument("--apply", action="store_true")
    lt = sub.add_parser("latest")
    lt.add_argument("--pages", type=int, default=3)
    lt.add_argument("--since-days", type=int, default=60)
    lt.add_argument("--sport", choices=("XC", "TF"))
    lt.add_argument("--out")
    a = ap.parse_args(argv)
    return {"index": cmdIndex, "check": cmdCheck, "store": cmdStore, "queue": cmdQueue,
            "refresh": cmdRefresh, "latest": cmdLatest}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
