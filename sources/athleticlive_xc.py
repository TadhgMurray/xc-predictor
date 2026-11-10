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
#   python -m sources.athleticlive_xc distances [--write] # races landed with no distance
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


# ★ A RACE LABEL WITH NO DISTANCE (owner, 2026-11-02: JV Season Finale,
#   "Fastest JV Boys", no ratings). The distance came from the race's label
#   alone ('5K', '1.7 Mile'); a label that names none landed with no
#   distance, and a race with no distance is never normalised or rated.
#   0. the results document's own distance (docMeters) -- nearly always there
#   1. the meet's other races: when every label that names a distance names
#      the SAME one (meetDistance / siblingDistance)
#   2. else the venue's: the distance DOMINANT_SHARE of its meets at the
#      same level ran, over VENUE_MIN_MEETS meets at least (venueDistance)
#   3. either way the times must be possible at it (plausiblePace)
#   Otherwise it stays without one -- a wrong distance rates every runner
#   wrong, none rates nobody.
DOMINANT_SHARE = 0.8
VENUE_MIN_MEETS = 3
FASTEST_PACE = 0.160          # s per metre: a 13:20 5K; faster is not a runner
SLOWEST_MEDIAN_PACE = 0.50    # s per metre: a 41:40 5K median


_UNIT_M = {"meter": 1.0, "meters": 1.0, "metre": 1.0, "metres": 1.0, "m": 1.0,
           "kilometer": 1000.0, "kilometers": 1000.0, "kilometre": 1000.0, "kilometres": 1000.0,
           "km": 1000.0, "k": 1000.0, "mile": 1609.344, "miles": 1609.344, "mi": 1609.344,
           "yard": 0.9144, "yards": 0.9144, "yd": 0.9144}


def docMeters(doc):
    """The race's distance from its own results document: 'd' in the unit
    'me', else 'mt' (metres, rounded down). None when it states none.

    ★ THE DOCUMENT STATES IT (owner, 2026-11-02: "there's no way this is
      true"). Fastest JV Boys' label names no distance, but its document
      carries d '5000', me 'meter', mt '5000'; the lander read only the
      label. This comes first; the label and the meet are the fallbacks.
    Pure."""
    src = (doc or {}).get("_source", doc) or {}
    try:
        d = float(src.get("d") or 0)
    except (TypeError, ValueError):
        d = 0.0
    unit = _UNIT_M.get(str(src.get("me") or "").strip().lower())
    if d > 0 and unit:
        return round(d * unit, 1)                # 'mt' is rounded down: 2735 for 1.7 mile
    try:
        mt = float(src.get("mt") or 0)
    except (TypeError, ValueError):
        return None
    return mt if mt > 0 else None


def meetDistance(events):
    """The one distance every labelled race of the meet names, or None when
    none names one or they disagree. Pure."""
    seen = {round(m) for m in (metersOf(e.get("ab") or e.get("name") or "")
                               for e in events) if m}
    return float(seen.pop()) if len(seen) == 1 else None


def dominant(counts, share=DOMINANT_SHARE, min_n=1):
    """{distance: n} -> the distance holding `share` of n (of min_n at
    least), else None. Pure."""
    total = sum(counts.values())
    if total < min_n:
        return None
    d, n = max(counts.items(), key=lambda kv: kv[1]) if counts else (None, 0)
    return d if d and n / total >= share else None


def plausiblePace(times, meters):
    """Could this race's times have been run at `meters`? Pure."""
    t = sorted(float(x) for x in times if x and float(x) > 0)
    if not t or not meters:
        return False
    return t[0] / meters >= FASTEST_PACE and t[len(t) // 2] / meters <= SLOWEST_MEDIAN_PACE


def siblingDistance(cur, meet_id, div_id):
    cur.execute("""SELECT round(distance)::int, count(*) FROM meets
                   WHERE meet_id = %s AND div_id <> %s AND distance > 0
                   GROUP BY 1""", (meet_id, div_id))
    seen = dict(cur.fetchall())
    return float(next(iter(seen))) if len(seen) == 1 else None


def venueDistance(cur, course, state, level_mask, meet_id):
    if not course:
        return None
    cur.execute("""SELECT round(distance)::int, count(DISTINCT meet_id) FROM meets
                   WHERE course_name = %s AND state IS NOT DISTINCT FROM %s
                     AND level_mask IS NOT DISTINCT FROM %s
                     AND distance > 0 AND meet_id <> %s
                   GROUP BY 1""", (course, state, level_mask, meet_id))
    got = dominant(dict(cur.fetchall()), min_n=VENUE_MIN_MEETS)
    return float(got) if got else None


def cmdDistances(a):
    """Landed AthleticLIVE XC races with no distance: their results
    document's, else the meet's other races', else the venue's (see the note
    above meetDistance). --write sets
    meets.distance; the night's normalisation (05_normalize_new, rows with
    no normalized_time) then prices them."""
    total = Counter()
    fetcher = A.makeFetcher()
    with A._conn() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT m.meet_id, m.div_id, m.course_name, m.state, m.level_mask,
                              left(m.meet_name, 40), m.division
                       FROM meets m
                       WHERE m.source = 'anet' AND m.div_id >= %s
                         AND COALESCE(m.distance, 0) <= 0""", (A.DIV_BASE,))
        todo = cur.fetchall()
        # 0. the results document's own distance (docMeters)
        evs = [{"kind": "ind", "id": int(r[1]) - A.DIV_BASE} for r in todo]
        got = A._run(fetcher.fetchAll([A.eventUrl(e) for e in evs], max_age=A.MAX_AGE_SETTLED)) \
            if evs else []
        doc_m = {int(r[1]): docMeters(A._json(f)) for r, f in zip(todo, got)}
        for meet_id, div_id, course, state, mask, name, division in todo:
            d, how = doc_m.get(int(div_id)), "its results document"
            if d is None:
                d, how = siblingDistance(cur, meet_id, div_id), "the meet's other races"
            if d is None:
                d, how = venueDistance(cur, course, state, mask, meet_id), "the venue's"
            if d is None:
                total["no distance to give"] += 1
                continue
            cur.execute("""SELECT time_seconds FROM results
                           WHERE source = 'anet' AND meet_id = %s AND div_id = %s
                             AND time_seconds > 0""", (meet_id, div_id))
            times = [r[0] for r in cur.fetchall()]
            if not plausiblePace(times, d):
                total["times impossible at it"] += 1
                print(f"  meet {meet_id} div {div_id} ({name}, {division}): {d:.0f} m from "
                      f"{how}, but the times do not fit; left alone")
                continue
            total[f"from {how}"] += 1
            print(f"  meet {meet_id} div {div_id} ({name}, {division}): {d:.0f} m from {how}")
            if a.write:
                cur.execute("UPDATE meets SET distance = %s WHERE div_id = %s "
                            "AND COALESCE(distance, 0) <= 0", (d, div_id))
        if a.write:
            conn.commit()
        else:
            conn.rollback()
    print(f"[alive xc] distances: {dict(total)}"
          + ("" if a.write else "  (dry run: --write sets them)"))
    return 0


eventDay = A.eventDay           # shared with the track lander (athleticlive.py)


# ★ THE RACES ATHLETIC.NET DOES NOT HAVE (owner, 2026-11-02: Mt. SAC's
#   Saturday, 46 races, never landed). A meet athletic.net already had rows
#   for was left to athletic.net and requeued there -- and when athletic.net's
#   re-scrape still lacked a day, nothing looked again. Now the races whose
#   runners athletic.net's copy does not have land from here, beside its
#   own: a race is athletic.net's when ANET_HAS_RACE of its runners with an
#   athletic.net id are in athletic.net's rows for the meet (dropCovered).
#   Its venue and meet name are athletic.net's (anetMeetInfo), so the course
#   is the same course.
ANET_HAS_RACE = 0.5


def dropCovered(p, have, share=ANET_HAS_RACE):
    """Drop from plan `p` every race athletic.net's athlete ids `have`
    cover. -> races dropped. Pure (mutates p)."""
    ids = {}
    for t in p["results_to_save"]:
        a = t[0].get("AthleteID")
        if a and t[0].get("SortValue"):
            ids.setdefault(t[2], []).append(int(a))
    gone = {d for d, v in ids.items() if sum(1 for a in v if a in have) >= share * len(v)}
    if gone:
        p["results_to_save"] = [t for t in p["results_to_save"] if t[2] not in gone]
        p["meets_to_save"] = [m for m in p["meets_to_save"] if m[1] not in gone]
        p["skipped"]["athletic.net has this race"] += len(gone)
    return len(gone)


ANET_COPIED = 0.9


def anetCopied(athletes, have, share=ANET_COPIED):
    """Are this landed race's runners in athletic.net's own rows? Pure."""
    a = {x for x in athletes if x}
    return bool(a) and len(a & have) >= share * len(a)


def anetAthletes(cur, meet_id):
    cur.execute("""SELECT DISTINCT athlete_id FROM results WHERE meet_id = %s AND source = 'anet'
                     AND result_id > 0 AND result_id < %s AND athlete_id IS NOT NULL""",
                (meet_id, A.RESULT_BASE))
    return {int(r[0]) for r in cur.fetchall()}


def anetMeetInfo(cur, meet_id):
    """athletic.net's own venue and name for this meet, as metaFromAlive's
    keys, or {}."""
    cur.execute("""SELECT meet_name, course_name, gps_lat, gps_long, state, location_id
                   FROM meets WHERE meet_id = %s AND source = 'anet' AND div_id < %s
                     AND course_name IS NOT NULL
                   ORDER BY div_id LIMIT 1""", (meet_id, A.DIV_BASE))
    r = cur.fetchone()
    if not r:
        return {}
    return {"Name": r[0], "Location": {"ID": r[5], "Name": r[1], "Lat": r[2], "Long": r[3],
                                       "State": r[4], "Indoor": False}}


def plan(anet_meet_id, alive, events, blobs, meta=None):
    """Everything to save for one meet, in the track lander's plan shape so
    applyIds / holdUnknown apply unchanged. `meta` overrides the meet info
    AthleticLIVE gives (anetMeetInfo). Pure."""
    meet_info = dict(A.metaFromAlive(alive, anet_meet_id), **(meta or {}))
    p = {"results_to_save": [], "meets_to_save": [], "athletes": [], "own": True,
         "skipped": Counter(), "events": events, "starts": []}
    parsed = {}
    for ev in events:
        doc = blobs.get(ev["id"])
        if not isinstance(doc, dict):
            p["skipped"]["no results document"] += 1
            continue
        parsed[ev["id"]] = A.resultRows(doc, ev)
    merged = mergedEvents({eid: raceKeys(v[0]) for eid, v in parsed.items()})
    meet_m = meetDistance(events)
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
        day = eventDay(ev, alive)
        ev_info = dict(meet_info, MeetDate=day) if day else meet_info
        div = {"IDMeetDiv": div_id,
               "Meters": (docMeters(blobs.get(ev["id"]))
                          or metersOf(ev.get("ab") or ev.get("name") or "") or meet_m),
               "Division": _division(ev), "LevelMask": meet_info.get("LevelMask") or 4,
               "CourseId": 0}
        p["meets_to_save"].append((ev_info, div_id, 0, div))
        p["starts"].append((div_id, 0, A.startUtc(ev)))     # this race's own start
        for r in rows:
            # the track lander's 8-tuple: (row, meet, div, event, short, relay, school, field)
            p["results_to_save"].append((xcRow(r), ev_info, div_id, 0, "XC", False,
                                         r.get("TeamName") or "Unknown", False))
        p["athletes"] += athletes
    return p


def save(meet_id, p, label="[alive xc]"):
    """Write a plan through the athletic.net XC saver. -> rows, or -1."""
    from scrape_results import _saveXCMeet
    meets = [(m[0], m[3]) for m in p["meets_to_save"]]
    results = [(t[0], t[1], t[6], t[2]) for t in p["results_to_save"]]
    n = _saveXCMeet(meet_id, label, meets, [], results)
    if n > 0 and p.get("starts"):
        try:
            from database import getConn
            with getConn() as conn, conn.cursor() as cur:
                A.saveStarts(cur, "XC", meet_id, p["starts"])
                conn.commit()
        except Exception as exc:                 # noqa: BLE001 -- results matter more
            print(f"{label} {meet_id}: start times not saved ({type(exc).__name__}: {exc})")
    return n


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


def checkAnetCopy(cur, fetcher, meet_id, alive, write, requeue=True):
    """athletic.net has rows for this meet: are they all of it? When they are
    short, land the races athletic.net lacks (dropCovered); with none to land,
    put the meet back in athletic.net's queue (requeue). -> (note, rows saved)."""
    anet_n = anetCount(cur, meet_id)
    events, blobs, note = A._run(fetchXC(fetcher, alive["alive_id"], alive["meet_date"]))
    alive_n = aliveCount(events, blobs) if events else 0
    if not (events and anetShort(anet_n, alive_n)):
        return "anet has it; counted", 0
    p = plan(meet_id, alive, events, blobs, meta=anetMeetInfo(cur, meet_id))
    dropCovered(p, anetAthletes(cur, meet_id))
    A.applyIds(p, A.resolveIds(cur, p["athletes"]))
    A.holdUnknown(p)
    races = len({t[2] for t in p["results_to_save"]})
    if p["results_to_save"]:
        print(f"  meet {meet_id} ({str(alive['name'])[:40]}): athletic.net has {anet_n} of "
              f"AthleticLIVE's {alive_n} -- landing the {races} races it lacks "
              f"({len(p['results_to_save'])} results)")
        if not write:
            return f"anet short {anet_n}/{alive_n}; {races} races to land", 0
        n = save(meet_id, p, label="[alive xc fill]")
        if n >= 0:
            A.saveSide(cur, meet_id, p.get("side"))
            return f"anet short {anet_n}/{alive_n}; landed {races} races it lacks", n
    if not requeue:
        return f"anet short {anet_n}/{alive_n}; requeued", 0
    print(f"  meet {meet_id} ({str(alive['name'])[:40]}): athletic.net has {anet_n} of "
          f"AthleticLIVE's {alive_n} -- back in athletic.net's queue")
    if write:
        requeueAnet(cur, meet_id)
    return f"anet short {anet_n}/{alive_n}; requeued", 0


def anetHasXC(cur, meet_id):
    """athletic.net's own XC rows for this meet are already in."""
    cur.execute("""SELECT 1 FROM results WHERE meet_id = %s AND source = 'anet'
                     AND result_id > 0 AND result_id < %s LIMIT 1""", (meet_id, A.RESULT_BASE))
    return cur.fetchone() is not None


# ★ A LIVE-TIMED RACE HAS NO RESULTS DOCUMENT (owner, 2026-11-04: the 2026
#   DSAC XC championships, AthleticLIVE meet 80057 under live.usp-sports.com,
#   "0 results in 0 races"). Chip-timed cross country posts its finish
#   order to the meet's live-timing branch in Firebase --
#       meet_<id>/liveBySplit/results/<run>/split_final/<runner key>
#   one entry per runner: athletic.net athlete id (anli), names, grade,
#   team name, time (m) and place (p) -- and the race's own index document
#   never gets its results list. Read there when the document is empty, in
#   the document's own shape, so the lander's rules apply unchanged.
LIVE_ID_BASE = 500_000_000_000     # result "i" for a live split row: + event * 1e5 + bib


def liveSplitUrl(alive_id, run):
    return (f"{A.FIREBASE}/meet_{int(alive_id)}/liveBySplit/results/"
            f"{run}/split_final.json")


def _clockMs(mark):
    """'16:08.80' -> 968800; None for anything that is not a time. Pure."""
    import re
    m = re.fullmatch(r"\s*(?:(\d+):)?(\d+):(\d{1,2})(?:\.(\d{1,3}))?\s*", str(mark or "")) \
        or re.fullmatch(r"\s*()(\d+):(\d{1,2})(?:\.(\d{1,3}))?\s*", str(mark or ""))
    if not m:
        return None
    h = int(m.group(1) or 0)
    mins, secs = int(m.group(2)), int(m.group(3))
    frac = (m.group(4) or "").ljust(3, "0")[:3]
    return ((h * 60 + mins) * 60 + secs) * 1000 + int(frac or 0)


def liveSplitRows(split_final, event_id):
    """A run's split_final -> the results list of an index document
    (resultRows' "r" shape). Pure."""
    import zlib
    out = []
    for key, x in sorted((split_final or {}).items()):
        if not isinstance(x, dict):
            continue
        bib = A._int(x.get("i")) or A._int(x.get("cm"))
        if bib is None or bib >= 100_000:
            bib = zlib.crc32(str(key).encode()) % 100_000
        g = {"M": "male", "F": "female"}.get(str(x.get("g") or "").upper()[:1])
        out.append({
            "i": LIVE_ID_BASE + int(event_id) * 100_000 + bib,
            "p": x.get("p"),
            "m": x.get("m") or x.get("rtm"),
            "im": _clockMs(x.get("m") or x.get("rtm")),
            "a": {"ani": x.get("anli"), "fn": x.get("fn"), "l": x.get("l"), "y": x.get("y"),
                  "g": g, "t": {"n": x.get("tn"), "f": x.get("tn")}},
        })
    return out


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
    # the races whose document carries no results: their live finish order
    empty = [ev for ev in events if ev.get("run")
             and not ((blobs.get(ev["id"]) or {}).get("_source", blobs.get(ev["id"]) or {}) or {}).get("r")]
    if empty:
        live = await fetcher.fetchAll([liveSplitUrl(alive_id, ev["run"]) for ev in empty], max_age=age)
        for ev, f in zip(empty, live):
            split = A._json(f)
            rows = liveSplitRows(split, ev["id"]) if isinstance(split, dict) else []
            if rows:
                doc = blobs.get(ev["id"]) or {}
                src = dict(doc.get("_source", doc) if isinstance(doc, dict) else {})
                src["r"] = rows
                blobs[ev["id"]] = {"_source": src}
    return events, blobs, ""


def candidates(cur, limit, meets=None):
    if meets:
        return list(meets)
    # ! A MEET TRIED AND NOT LANDED RESTS (2026-11-10; alive_meets.tried_at):
    #   never-tried meets first, then the rested ones, so a pile of held meets
    #   can no longer fill every night's --limit
    cur.execute("""SELECT u.anet_id FROM (
                     SELECT DISTINCT u.anet_id, am.meet_date, COALESCE(am.wanted, false) AS wanted,
                            am.tried_at IS NULL AS fresh
                     FROM alive_meets am CROSS JOIN LATERAL unnest(am.anet_ids) u(anet_id)
                     WHERE am.sport = 'XC' AND am.landed_at IS NULL
                       AND (am.tried_at IS NULL OR am.tried_at < now() - make_interval(days => %s))
                       AND COALESCE(am.date_end, am.meet_date) < %s AND am.state = ANY(%s)
                       AND COALESCE(am.name, '') !~* %s) u
                   -- a meet the owner asked for first (fixes.wantAlive)
                   ORDER BY u.wanted DESC, u.fresh DESC, u.meet_date DESC LIMIT %s""",
                (TRY_REST_DAYS, datetime.date.today().isoformat(), list(A.US_STATES),
                 A.NOT_RUNNING, limit))
    return [r[0] for r in cur.fetchall()]


TRY_REST_DAYS = 3


def _noteTriedFor(cur, meet_id, note):
    """noteTried for every unlanded XC alive meet naming this athletic.net id."""
    cur.execute("""UPDATE alive_meets SET tried_at = now(), landed_note = %s
                   WHERE sport = 'XC' AND landed_at IS NULL AND %s = ANY(anet_ids)""",
                (note, meet_id))


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
                if a.write:
                    _noteTriedFor(cur, meet_id, "not on AthleticLIVE")
                    conn.commit()
                continue
            if anetHasXC(cur, meet_id):
                why, n = checkAnetCopy(cur, fetcher, meet_id, alive, a.write)
                total["athletic.net short: races landed" if "land" in why
                      else "athletic.net short: requeued" if why.startswith("anet short")
                      else "athletic.net already has it"] += 1
                total["written"] += n
                if a.write:
                    A.noteLanded(cur, alive["alive_id"], n, why)
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
                if a.write:
                    A.noteTried(cur, alive["alive_id"],
                                f"held: {p.get('held') or 'no results'}"[:200])
                    conn.commit()
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
            # ★ AND ATHLETIC.NET'S LATER COPY WINS (2026-11-02): a race landed
            #   here because athletic.net lacked it (checkAnetCopy), once
            #   athletic.net posts it, is the same runners twice
            have = anetAthletes(cur, meet_id)
            twice = {d for d, ks in divs.items()
                     if anetCopied({a for a, _t in ks}, have)}
            for div_id in sorted(twice | mergedEvents({d: v for d, v in divs.items()
                                                       if d not in twice})):
                n_div += 1
                print(f"  meet {meet_id} div {div_id}: {len(divs[div_id])} runners "
                      + ("athletic.net has too" if div_id in twice else "all in its other races"))
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
    print(f"[alive xc] {n_div} combined lists or athletic.net copies in {len(by)} landed meets"
          + (f"; {n_rows} rows deleted" if a.write else "  (dry run: --write deletes them)"))
    return 0


def cmdRecheck(a):
    """Meets this lander stood down for in the last --days ("anet has it",
    or athletic.net short and requeued): count both copies again, and land
    the races athletic.net still lacks (checkAnetCopy)."""
    fetcher = A.makeFetcher()
    total = Counter()
    with A._conn() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT DISTINCT u.anet_id, am.alive_id
                       FROM alive_meets am CROSS JOIN LATERAL unnest(am.anet_ids) u(anet_id)
                       WHERE am.sport = 'XC' AND COALESCE(am.landed_n, 0) = 0
                         AND (am.landed_note = 'anet has it'
                              OR am.landed_note LIKE 'anet short%%requeued')
                         AND am.meet_date >= %s
                       ORDER BY 1 DESC""",
                    ((datetime.date.today() - datetime.timedelta(days=a.days)).isoformat(),))
        for meet_id, _alive_id in cur.fetchall():
            alive = A.aliveFor(cur, meet_id, sport="XC")
            if not alive:
                continue
            # ! athletic.net is asked again ONCE: a meet already requeued and
            #   still short gets only the races it lacks landed from here
            cur.execute("SELECT landed_note FROM alive_meets WHERE alive_id = %s",
                        (alive["alive_id"],))
            prior = (cur.fetchone() or [""])[0] or ""
            why, n = checkAnetCopy(cur, fetcher, meet_id, alive, a.write,
                                   requeue=not prior.startswith("anet short"))
            total["races landed" if "land" in why else
                  "requeued" if why.startswith("anet short") else "complete"] += 1
            total["written"] += n
            if a.write:
                A.noteLanded(cur, alive["alive_id"], n, why)
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
    ds = sub.add_parser("distances", help="give landed races with no distance the meet's or venue's")
    ds.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    return {"land": cmdLand, "dedupe": cmdDedupe, "recheck": cmdRecheck,
            "distances": cmdDistances}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
