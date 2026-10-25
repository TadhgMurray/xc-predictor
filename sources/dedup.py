# Project: xc-predictor
# File:    sources/dedup.py
# Purpose: Is a landed timer meet one we already have? A meet that is also on
#          athletic.net or TFRRS is ONE meet (PLAN-MULTISOURCE.md), and the
#          feed's copy wins: it carries athlete ids, the meet's own page and
#          the re-scrape. So every landed race is judged against the rows the
#          feeds hold for that day, and only the races nobody has are
#          promoted (sources/promote.py).
#
#   judge(landed, feed) -> {race_n: RaceVerdict}      pure, tests/test_sources_timers.py
#   run(conn, write)                                  every meets_ext row still 'landed'
#
# ★ THE RUNNERS ARE THE EVIDENCE, NOT THE NAME OF THE MEET. Timers and feeds
#   name one meet differently ("2026 Thundering Herd XC Invitational" and
#   "Marshall University Thundering Herd XC Invitational", link_feed_twins'
#   own case) and venues are spelt three ways. One run in two copies is the
#   same day and the same time as precisely as both print it
#   (link_feed_twins.sameTime: to the tenth, to the second when either is
#   whole seconds; track to the hundredth), and:
#     - STRONG: the same name (link_freshmen.normName), or
#     - the same school (link_feed_twins.sameSchool), with exactly ONE
#       partner each way -- a pack of teammates a tenth apart says nothing
#       (link_feed_twins rule 3, twin_flag.uploadTwins).
#   A landed race is a TWIN of the feed meet holding most of its runners
#   when STRONG_MIN of them pair by name, or MIN_MATCH and SHARE of its
#   finishers pair at all. Three runners with the same name and the same
#   time on the same day is not a coincidence.
#
# ★ PER RACE, THEN PER MEET. A timer's report carries every race; athletic.net
#   often carries only some (varsity, not the middle school race). The races
#   the feed has are twins and stay out; the others are promoted. meets_ext
#   .status: 'twin' (every race a twin), 'partial', or 'new'.
#
# ★ THE SECOND DISTANCE. A twin race's feed division and that division's
#   distance are written beside the timer's own (races_ext.twin_div_id,
#   twin_meters), so the wrong-distance work has a second witness for a
#   meet we never promote. distanceDisagreements() lists the ones that
#   differ.
#
# ! THE FEEDS SEEN: anet and tfrrs (and approved uploads, and timer meets
#   already promoted) on the landed meet's day, the day before and the day
#   after -- a report's date is its first day, and a two-day meet's second
#   day is in the feed under its own date.

import datetime
import math
import os
import sys
from collections import Counter, defaultdict, namedtuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

FEEDS = ("anet", "tfrrs", "upload", "timer")
STRONG_MIN = 3          # name-and-time pairs that make a race a twin outright
MIN_MATCH = 3           # ... or this many pairs of any kind
SHARE = 0.3             # ... that are at least this share of the race's finishers
DISTANCE_TOL = 0.03     # stated vs feed distance: report past 3%

LRow = namedtuple("LRow", "race_n n name ts school")
FRow = namedtuple("FRow", "source meet_id div_id name ts school")
RaceVerdict = namedtuple("RaceVerdict", "twin source meet_id div_id matched strong finishers")


def _normName(name):
    from link_freshmen import normName
    return normName(name)


def _sameTime(a, b, sport):
    if sport == "XC":
        from link_feed_twins import sameTime
        return sameTime(a, b)
    return a is not None and b is not None and abs(round(float(a), 2) - round(float(b), 2)) < 0.006


def pairs(landed, feed, sport):
    """[(landed (race_n, n), feed index, strong)] -- every pair that reads as
    one run (the block above). Pure."""
    from link_feed_twins import sameSchool
    buckets = defaultdict(list)
    for i, f in enumerate(feed):
        if f.ts is not None and f.ts > 0:
            buckets[int(math.floor(f.ts))].append(i)
    keyed = {}
    raw = []
    for r in landed:
        if r.ts is None or r.ts <= 0:
            continue
        nm = _normName(r.name)
        b = int(math.floor(r.ts))
        for i in buckets.get(b - 1, []) + buckets.get(b, []) + buckets.get(b + 1, []):
            f = feed[i]
            if not _sameTime(r.ts, f.ts, sport):
                continue
            if i not in keyed:
                keyed[i] = _normName(f.name)
            strong = nm is not None and nm == keyed[i]
            if strong or sameSchool(r.school, f.school):
                raw.append(((r.race_n, r.n), i, strong))
    # a school-only pair counts only when it is each side's ONE partner
    nl, nf = Counter(), Counter()
    for lk, i, _s in raw:
        nl[lk] += 1
        nf[i] += 1
    return [(lk, i, s) for lk, i, s in raw if s or (nl[lk] == 1 and nf[i] == 1)]


def judge(landed, feed, sport):
    """{race_n: RaceVerdict} for one landed meet's rows against the feed rows
    of its days. Pure."""
    finishers = Counter(r.race_n for r in landed if r.ts is not None and r.ts > 0)
    races = sorted({r.race_n for r in landed})
    # per race and feed meet: the landed rows that pair (each once), how
    # many by name, and the feed divisions they sit in
    hit = defaultdict(set)
    strong = defaultdict(set)
    divs = defaultdict(Counter)
    for (race_n, n), i, s in pairs(landed, feed, sport):
        f = feed[i]
        k = (race_n, f.source, f.meet_id)
        hit[k].add(n)
        if s:
            strong[k].add(n)
        divs[k][f.div_id] += 1
    out = {}
    for race_n in races:
        cands = [(len(hit[k]), len(strong[k]), k) for k in hit if k[0] == race_n]
        nfin = finishers.get(race_n, 0)
        if not cands:
            out[race_n] = RaceVerdict(False, None, None, None, 0, 0, nfin)
            continue
        m, s, k = max(cands, key=lambda c: (c[0], c[1], FEEDS.index(c[2][1]) * -1))
        twin = s >= STRONG_MIN or (m >= MIN_MATCH and m >= SHARE * nfin) \
            or (0 < nfin < MIN_MATCH and m == nfin)
        div = divs[k].most_common(1)[0][0]
        out[race_n] = RaceVerdict(twin, k[1], k[2], div, m, s, nfin)
    return out


def meetVerdict(verdicts):
    """(status, twin_source, twin_meet_id, share) for a meet's race verdicts."""
    if not verdicts:
        return "new", None, None, 0.0
    twins = [v for v in verdicts.values() if v.twin]
    fin = sum(v.finishers for v in verdicts.values()) or 1
    by = Counter()
    for v in twins:
        by[(v.source, v.meet_id)] += v.matched
    src, mid = (by.most_common(1)[0][0] if by else (None, None))
    share = round(sum(v.matched for v in twins) / fin, 3)
    status = "twin" if len(twins) == len(verdicts) else ("partial" if twins else "new")
    return status, src, mid, share


# ------------------------------------------------------------------ #
#  the database side
# ------------------------------------------------------------------ #
def _days(iso):
    try:
        d = datetime.date.fromisoformat(str(iso)[:10])
    except ValueError:
        return []
    return [(d + datetime.timedelta(days=k)).isoformat() for k in (-1, 0, 1)]


def stageFeed(cur, days, sport):
    """Temp table dd_feed: every feed row on these days, with a name (an anet
    row written before results.athlete_name was kept reads its athletes
    row). One scan of the table for all pending meets."""
    table = "results" if sport == "XC" else "results_tf"
    keep = "" if sport == "XC" else "AND COALESCE(r.is_relay, 0) = 0 AND COALESCE(r.is_field, 0) = 0"
    feeds = ", ".join(f"'{f}'" for f in FEEDS)
    cur.execute("DROP TABLE IF EXISTS dd_feed")
    cur.execute(f"""
        CREATE TEMP TABLE dd_feed AS
        SELECT r.source, r.meet_id, r.div_id, substr(r.date, 1, 10) AS d,
               r.time_seconds AS ts, r.school,
               COALESCE(NULLIF(btrim(r.athlete_name), ''),
                        NULLIF(btrim(concat_ws(' ', a.first_name, a.last_name)), '')) AS name
               {", r.event_id" if sport == "TF" else ", NULL::bigint AS event_id"}
        FROM   {table} r
        LEFT   JOIN athletes a ON a.athlete_id = r.athlete_id AND r.source = 'anet'
        WHERE  r.source IN ({feeds})
          AND  substr(r.date, 1, 10) = ANY(%s)
          AND  r.time_seconds > 0 AND r.time_seconds < 100000 {keep}""", (sorted(days),))
    cur.execute("CREATE INDEX ON dd_feed (d)")


def _feedFor(cur, days, self_meet):
    cur.execute("""SELECT source, meet_id, div_id, name, ts, school FROM dd_feed
                   WHERE d = ANY(%s) AND NOT (source = 'timer' AND meet_id IS NOT DISTINCT FROM %s)""",
                (days, self_meet))
    return [FRow(*r) for r in cur.fetchall()]


def _landedFor(cur, ext):
    cur.execute("""SELECT race_n, n, athlete_name, time_seconds, school FROM results_ext
                   WHERE ext_meet_id = %s ORDER BY race_n, n""", (ext,))
    return [LRow(*r) for r in cur.fetchall()]


def _feedMeters(cur, source, meet_id, div_id, sport):
    """The feed's distance for one division, where the feed states one."""
    if div_id is None:
        return None
    if sport == "XC" and source in ("anet", "upload", "timer"):
        cur.execute("SELECT distance FROM meets WHERE div_id = %s", (div_id,))
    elif sport == "XC" and source == "tfrrs":
        cur.execute("""SELECT NULLIF(division_distances -> %s ->> 'distance', '')::real
                       FROM meets_tfrrs WHERE meet_id = %s AND sport = 'XC'""", (str(div_id), meet_id))
    elif sport == "TF" and source != "tfrrs":
        cur.execute("SELECT min(distance_meters) FROM meets_tf WHERE div_id = %s", (div_id,))
    else:
        return None
    try:
        got = cur.fetchone()
    except Exception:                                   # noqa: BLE001
        return None
    return float(got[0]) if got and got[0] else None


def run(conn, write=False, limit=None, show=20, ext_ids=None):
    """Judge every landed meet ('landed', or the ids given). Read-only unless
    write. -> Counter of statuses."""
    from psycopg2.extras import execute_values
    with conn.cursor() as cur:
        q = "SELECT ext_meet_id, meet_date, meet_name, source FROM meets_ext WHERE "
        if ext_ids:
            cur.execute(q + "ext_meet_id = ANY(%s) ORDER BY ext_meet_id", (list(ext_ids),))
        else:
            cur.execute(q + "status = 'landed' ORDER BY ext_meet_id" + (f" LIMIT {int(limit)}" if limit else ""))
        meets = cur.fetchall()
        if not meets:
            print("[dedup] nothing landed to judge")
            conn.rollback()
            return Counter()
        cur.execute("SELECT ext_meet_id, race_n, sport FROM races_ext WHERE ext_meet_id = ANY(%s)",
                    ([m[0] for m in meets],))
        race_sport = {(e, n): s for e, n, s in cur.fetchall()}
        verdicts = defaultdict(dict)          # ext -> {race_n: RaceVerdict}
        race_rows = []
        for sport in ("XC", "TF"):
            mine = [m for m in meets if (m[0], sport) in {(e, s) for (e, _n), s in race_sport.items()}]
            if not mine:
                continue
            stageFeed(cur, {d for m in mine for d in _days(m[1])}, sport)
            for ext, date, _name, _src in mine:
                landed = [r for r in _landedFor(cur, ext) if race_sport.get((ext, r.race_n)) == sport]
                for race_n, v in judge(landed, _feedFor(cur, _days(date), None), sport).items():
                    verdicts[ext][race_n] = v
                    meters = _feedMeters(cur, v.source, v.meet_id, v.div_id, sport) if v.twin else None
                    race_rows.append((ext, race_n, v.source if v.twin else None,
                                      v.meet_id if v.twin else None, v.div_id if v.twin else None,
                                      v.matched, meters))
        counts, shown = Counter(), 0
        for ext, date, name, source in meets:
            vs = verdicts.get(ext, {})
            st = meetVerdict(vs)
            counts[st[0]] += 1
            if shown < show:
                print(f"[dedup] {st[0]:<8} {str(date)[:10]} {source:<28} {(name or '')[:48]:<48} "
                      f"{len(vs)} races, {sum(v.twin for v in vs.values())} twin"
                      + (f" of {st[1]} {st[2]} ({st[3]:.0%} of finishers)" if st[1] else ""))
                shown += 1
            if write:
                cur.execute("""UPDATE meets_ext SET status = %s, twin_source = %s, twin_meet_id = %s,
                                      twin_share = %s, judged_at = now()
                               WHERE ext_meet_id = %s""", (st[0], st[1], st[2], st[3], ext))
        if write and race_rows:
            execute_values(cur, """
                UPDATE races_ext x SET twin_source = v.s, twin_meet_id = v.m::bigint,
                       twin_div_id = v.dv::bigint, twin_matched = v.k::int, twin_meters = v.mt::real
                FROM (VALUES %s) AS v(e, n, s, m, dv, k, mt)
                WHERE x.ext_meet_id = v.e::bigint AND x.race_n = v.n::int""", race_rows)
        if write:
            conn.commit()
        else:
            conn.rollback()
    print(f"[dedup] {dict(counts)}" + ("" if write else "  (dry run: nothing written; --write to keep)"))
    return counts


def distanceDisagreements(cur, tol=DISTANCE_TOL, limit=200):
    """Twin races whose feed distance differs from the timer's by more than
    tol: [(ext_meet_id, meet_name, date, race title, stated, feed, source,
    feed meet, feed div)]."""
    cur.execute("""
        SELECT x.ext_meet_id, m.meet_name, m.meet_date, x.title, x.meters_stated, x.twin_meters,
               x.twin_source, x.twin_meet_id, x.twin_div_id
        FROM   races_ext x JOIN meets_ext m USING (ext_meet_id)
        WHERE  x.twin_meters > 0 AND x.meters_stated > 0
          AND  abs(x.twin_meters - x.meters_stated) > %s * x.meters_stated
        ORDER  BY m.meet_date DESC LIMIT %s""", (tol, limit))
    return cur.fetchall()
