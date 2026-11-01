# Project: xc-predictor
# File:    sources/athleticlive_xc.py
# Purpose: Cross country from AthleticLIVE instead of athletic.net -- the
#          track lander's twin (sources/athleticlive.py). An AthleticLIVE XC
#          meet names its athletic.net meet id and each runner's athletic.net
#          athlete id, so it lands in `meets` / `results` in the shape the
#          athletic.net XC scraper writes, and athletic.net is never asked
#          (athleticlive cover closes its queue row afterwards).
#
#   python -m sources.athleticlive_xc land [--limit 100] [--meet ANET_ID] [--write]
#   python -m sources.athleticlive_xc dedupe [--write]   # combined lists landed before 2026-11-01
#
# ★ WHY (owner, 2026-11-01: "it needs to skip everything for athletic live").
#   About 28% of athletic.net's cross country is on AthleticLIVE; until now
#   only track landed from here.
#
# THE SHAPE
#   meets    one row per AthleticLIVE race: div_id = DIV_BASE + its event id
#            (athletic.net's division ids are not AthleticLIVE's to give),
#            distance from the race's own label ('5K', '1.7 Mile')
#   results  result_id = RESULT_BASE + AthleticLIVE's result id, the runner's
#            athletic.net athlete id (or ours, matched by name and school),
#            source 'anet' -- exactly what the track lander writes
# ! RUNNERS WITH NO ATHLETIC.NET ID go through the track lander's own rules
#   (resolveIds, then holdUnknown): a race at least LAND_SHARE named lands
#   without the rest; one under it holds the whole meet back, which then
#   stays athletic.net's (cover only closes meets that landed).
# ! YESTERDAY OR OLDER ONLY. A meet lands once (landed_at); landing a race
#   still being posted would leave it short for good.
import argparse
import datetime
import os
import sys
from collections import Counter

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from sources import athleticlive as A          # noqa: E402
from sources.hytek import metersOf             # noqa: E402


def xcEvents(summary):
    """The individual cross country races of an event_summary."""
    return [e for e in A.parseSummary(summary) if e["xc"] and e["kind"] == "ind"]


def _division(ev):
    return (ev.get("division") or ev.get("name") or "").strip() or None


def xcRow(r):
    """A track-lander result dict -> the athletic.net XC result shape
    (saveResultsBulk reads SortValue, in seconds)."""
    out = dict(r)
    ms = r.get("SortInt")
    out["SortValue"] = round(ms / 1000.0, 2) if ms else None
    return out


# ★ A COMBINED LIST IS NOT A SECOND RACE (owner, 2026-11-01: meet 274532,
#   one runner's 13:46.37 twice -- 34th in his division, 39th in the
#   "combined" list over the divisions). Timers post both; landing both
#   puts every runner in twice. An event whose timed runners (athlete,
#   time) the meet's other kept events already hold goes, biggest first:
#   so a combined list over divisions that are all posted goes and the
#   divisions stay (athletic.net's own shape for the meet, and a combined
#   list can stitch separate heats together), while a division posted only
#   inside a combined list whose other divisions are missing goes instead
#   -- nobody is lost either way.
MERGED_COVER = 0.95


def raceKeys(rows):
    """{(athlete, time)} of an event's timed rows. Pure."""
    return {(r.get("AthleteID"), r.get("SortInt")) for r in rows if r.get("SortInt")}


def mergedEvents(keysets):
    """{event: {(athlete, time)}} -> the events that only repeat runners
    the other kept events already have. Pure."""
    keep = {k: v for k, v in keysets.items() if v}
    drop = set()
    for eid in sorted(keep, key=lambda e: (-len(keep[e]), str(e))):
        ks = keep[eid]
        others = set().union(*(v for k, v in keep.items() if k != eid))
        if len(ks & others) >= MERGED_COVER * len(ks):
            drop.add(eid)
            del keep[eid]
    return drop


def plan(anet_meet_id, alive, events, blobs):
    """Everything to save for one meet, in the track lander's plan shape so
    applyIds / holdUnknown apply unchanged. Pure."""
    meet_info = A.metaFromAlive(alive, anet_meet_id)
    p = {"results_to_save": [], "meets_to_save": [], "athletes": [], "own": True,
         "skipped": Counter(), "events": events}
    parsed = {}
    for ev in events:
        doc = blobs.get(ev["id"])
        if not isinstance(doc, dict):
            p["skipped"]["no results document"] += 1
            continue
        parsed[ev["id"]] = A.resultRows(doc, ev)
    merged = mergedEvents({eid: raceKeys(v[0]) for eid, v in parsed.items()})
    for ev in events:
        if ev["id"] not in parsed:
            continue
        if ev["id"] in merged:
            p["skipped"]["combined list (its runners are in the other races)"] += 1
            continue
        rows, athletes, skipped = parsed[ev["id"]]
        p["skipped"].update(skipped)
        if not rows:
            continue
        div_id = A.DIV_BASE + int(ev["id"])
        div = {"IDMeetDiv": div_id, "Meters": metersOf(ev.get("ab") or ev.get("name") or ""),
               "Division": _division(ev), "LevelMask": meet_info.get("LevelMask") or 4,
               "CourseId": 0}
        p["meets_to_save"].append((meet_info, div_id, 0, div))
        for r in rows:
            # the track lander's 8-tuple: (row, meet, div, event, short, relay, school, field)
            p["results_to_save"].append((xcRow(r), meet_info, div_id, 0, "XC", False,
                                         r.get("TeamName") or "Unknown", False))
        p["athletes"] += athletes
    return p


def save(meet_id, p, label="[alive xc]"):
    """Write a plan through the athletic.net XC saver. -> rows, or -1."""
    from scrape_results import _saveXCMeet
    meets = [(m[0], m[3]) for m in p["meets_to_save"]]
    results = [(t[0], t[1], t[6], t[2]) for t in p["results_to_save"]]
    return _saveXCMeet(meet_id, label, meets, [], results)


# ★ "ATHLETIC.NET HAS IT" IS A COUNT, NOT A YES (owner, 2026-11-01: Mt. SAC's
#   3-mile invitational missing for Flo Adamski "and other athletes").
#   athletic.net posts a big meet race by race; a scrape that came while it
#   was half up left it half in, the meet was marked done, and this lander
#   stood down because SOME athletic.net rows existed. Now the two copies are
#   counted: athletic.net short of AthleticLIVE by more than ANET_SHORT (and
#   by SHORT_MIN runners) puts the meet back in athletic.net's queue at the
#   front -- athletic.net's own copy is complete by then, and its divisions
#   are the ones every other page keys on.
ANET_SHORT = 0.90
SHORT_MIN = 10


def anetCount(cur, meet_id):
    """athletic.net's own XC result rows for this meet."""
    cur.execute("""SELECT count(*) FROM results WHERE meet_id = %s AND source = 'anet'
                     AND result_id > 0 AND result_id < %s""", (meet_id, A.RESULT_BASE))
    return int(cur.fetchone()[0])


def aliveCount(events, blobs):
    """AthleticLIVE's timed individual results across the meet's races."""
    n = 0
    for ev in events:
        doc = blobs.get(ev["id"])
        if isinstance(doc, dict):
            rows, _a, _s = A.resultRows(doc, ev)
            n += sum(1 for r in rows if r.get("SortInt"))
    return n


def anetShort(anet_n, alive_n):
    """Is athletic.net's copy short of AthleticLIVE's? Pure."""
    return alive_n - anet_n >= SHORT_MIN and anet_n < ANET_SHORT * alive_n


def requeueAnet(cur, meet_id):
    cur.execute("""INSERT INTO meet_queue (meet_id, sport, source, scraped, priority)
                   VALUES (%s, 'XC', 'anet', 0, 1)
                   ON CONFLICT (meet_id, sport, source)
                   DO UPDATE SET scraped = 0, priority = 1""", (meet_id,))


def checkAnetCopy(cur, fetcher, meet_id, alive, write):
    """athletic.net has rows for this meet: are they all of it? -> note."""
    anet_n = anetCount(cur, meet_id)
    events, blobs, note = A._run(fetchXC(fetcher, alive["alive_id"], alive["meet_date"]))
    alive_n = aliveCount(events, blobs) if events else 0
    if events and anetShort(anet_n, alive_n):
        print(f"  meet {meet_id} ({str(alive['name'])[:40]}): athletic.net has {anet_n} of "
              f"AthleticLIVE's {alive_n} -- back in athletic.net's queue")
        if write:
            requeueAnet(cur, meet_id)
        return f"anet short {anet_n}/{alive_n}; requeued"
    return "anet has it; counted"


def anetHasXC(cur, meet_id):
    """athletic.net's own XC rows for this meet are already in."""
    cur.execute("""SELECT 1 FROM results WHERE meet_id = %s AND source = 'anet'
                     AND result_id > 0 AND result_id < %s LIMIT 1""", (meet_id, A.RESULT_BASE))
    return cur.fetchone() is not None


async def fetchXC(fetcher, alive_id, meet_date=None):
    age = A.MAX_AGE_SETTLED if A._settled(meet_date) else None
    summary = A._json(await fetcher.fetch(A.summaryUrl(alive_id), max_age=age))
    events = xcEvents(summary)
    if not events:
        return [], {}, "no cross country races"
    got = await fetcher.fetchAll([A.eventUrl(e) for e in events], max_age=age)
    blobs = {}
    for ev, f in zip(events, got):
        doc = A._json(f)
        if isinstance(doc, dict):
            blobs[ev["id"]] = doc
    return events, blobs, ""


def candidates(cur, limit, meets=None):
    if meets:
        return list(meets)
    cur.execute("""SELECT DISTINCT u.anet_id, am.meet_date
                   FROM alive_meets am CROSS JOIN LATERAL unnest(am.anet_ids) u(anet_id)
                   WHERE am.sport = 'XC' AND am.landed_at IS NULL
                     AND COALESCE(am.date_end, am.meet_date) < %s AND am.state = ANY(%s)
                     AND COALESCE(am.name, '') !~* %s
                   ORDER BY am.meet_date DESC LIMIT %s""",
                (datetime.date.today().isoformat(), list(A.US_STATES), A.NOT_RUNNING, limit))
    return [r[0] for r in cur.fetchall()]


def cmdLand(a):
    fetcher = A.makeFetcher()
    total = Counter()
    with A._conn() as conn:
        cur = conn.cursor()
        A.initTables(cur)
        conn.commit()
        for meet_id in candidates(cur, a.limit, a.meet):
            alive = A.aliveFor(cur, meet_id, sport="XC")
            if not alive:
                total["not on AthleticLIVE"] += 1
                continue
            if anetHasXC(cur, meet_id):
                why = checkAnetCopy(cur, fetcher, meet_id, alive, a.write)
                total["athletic.net short: requeued" if why.startswith("anet short")
                      else "athletic.net already has it"] += 1
                if a.write:
                    A.noteLanded(cur, alive["alive_id"], 0, why)
                    conn.commit()
                continue
            events, blobs, note = A._run(fetchXC(fetcher, alive["alive_id"], alive["meet_date"]))
            if not events:
                total[note or "no events"] += 1
                if a.write:
                    A.noteLanded(cur, alive["alive_id"], 0, note or "no events")
                    conn.commit()
                continue
            p = plan(meet_id, alive, events, blobs)
            total["rows given our id"] += A.applyIds(p, A.resolveIds(cur, p["athletes"]))
            A.holdUnknown(p)
            n_res = len(p["results_to_save"])
            print(f"  meet {meet_id} (alive {alive['alive_id']}, {alive['meet_date']}, "
                  f"{str(alive['name'])[:40]}): {n_res} results in {len(p['meets_to_save'])} races"
                  + (f"; held: {p['held']}" if p.get("held", {}).get("races") else "")
                  + (f"; skipped {dict(p['skipped'])}" if p["skipped"] else ""))
            if not n_res:
                total["held or empty"] += 1
                continue
            total["meets"] += 1
            total["results"] += n_res
            if a.write:
                n = save(meet_id, p)
                if n < 0:
                    total["save failed"] += 1
                    continue
                A.saveSide(cur, meet_id, p.get("side"))
                A.noteLanded(cur, alive["alive_id"], n, "xc own slots")
                conn.commit()
                total["written"] += n
    print(f"[alive xc] {dict(total)}  fetches {fetcher.stats}"
          + ("" if a.write else "  (dry run: nothing written; --write lands them)"))
    return 0


_LANDED_SQL = """
    SELECT r.meet_id, r.div_id, r.athlete_id, round(r.time_seconds::numeric, 2)
    FROM   results r
    WHERE  r.source = 'anet' AND r.div_id >= %s AND r.result_id >= %s
      AND  r.time_seconds > 0
      AND  r.meet_id IN (SELECT DISTINCT u FROM alive_meets am
                         CROSS JOIN LATERAL unnest(am.anet_ids) u
                         WHERE am.sport = 'XC' AND am.landed_n > 0)
"""


def cmdDedupe(a):
    """Combined lists landed before mergedEvents existed: the same test on
    the rows already saved; --write deletes them (results and the meets
    row). The rankings drop them on their next build."""
    from collections import defaultdict
    with A._conn() as conn:
        cur = conn.cursor()
        cur.execute(_LANDED_SQL, (A.DIV_BASE, A.RESULT_BASE))
        by = defaultdict(lambda: defaultdict(set))
        for meet_id, div_id, ath, t in cur.fetchall():
            by[meet_id][div_id].add((ath, float(t)))
        n_div = n_rows = 0
        for meet_id, divs in sorted(by.items()):
            for div_id in sorted(mergedEvents(divs)):
                n_div += 1
                print(f"  meet {meet_id} div {div_id}: {len(divs[div_id])} runners all in its other races")
                if a.write:
                    cur.execute("DELETE FROM results WHERE meet_id = %s AND div_id = %s AND source = 'anet'",
                                (meet_id, div_id))
                    n_rows += cur.rowcount
                    cur.execute("DELETE FROM meets WHERE meet_id = %s AND div_id = %s AND source = 'anet'",
                                (meet_id, div_id))
        if a.write:
            conn.commit()
        else:
            conn.rollback()
    print(f"[alive xc] {n_div} combined lists in {len(by)} landed meets"
          + (f"; {n_rows} rows deleted" if a.write else "  (dry run: --write deletes them)"))
    return 0


def cmdRecheck(a):
    """Meets this lander stood down for ("anet has it") in the last
    --days: count both copies again (the check above, for the meets passed
    before it existed)."""
    fetcher = A.makeFetcher()
    total = Counter()
    with A._conn() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT DISTINCT u.anet_id, am.alive_id
                       FROM alive_meets am CROSS JOIN LATERAL unnest(am.anet_ids) u(anet_id)
                       WHERE am.sport = 'XC' AND am.landed_note = 'anet has it'
                         AND am.meet_date >= %s
                       ORDER BY 1 DESC""",
                    ((datetime.date.today() - datetime.timedelta(days=a.days)).isoformat(),))
        for meet_id, _alive_id in cur.fetchall():
            alive = A.aliveFor(cur, meet_id, sport="XC")
            if not alive:
                continue
            why = checkAnetCopy(cur, fetcher, meet_id, alive, a.write)
            total["requeued" if why.startswith("anet short") else "complete"] += 1
            if a.write:
                A.noteLanded(cur, alive["alive_id"], 0, why)     # counted: not asked again
                conn.commit()
    print(f"[alive xc] recheck: {dict(total)}  fetches {fetcher.stats}"
          + ("" if a.write else "  (dry run: --write requeues them)"))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="AthleticLIVE cross country -> meets / results")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ld = sub.add_parser("land")
    ld.add_argument("--limit", type=int, default=100)
    ld.add_argument("--meet", type=int, action="append", help="this athletic.net meet id")
    ld.add_argument("--write", action="store_true")
    rc = sub.add_parser("recheck", help="count athletic.net's copy of meets passed as 'anet has it'")
    rc.add_argument("--days", type=int, default=60)
    rc.add_argument("--write", action="store_true")
    dd = sub.add_parser("dedupe", help="drop combined lists already landed")
    dd.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    return {"land": cmdLand, "dedupe": cmdDedupe, "recheck": cmdRecheck}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
