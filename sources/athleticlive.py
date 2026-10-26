# Project: xc-predictor
# File:    sources/athleticlive.py
# Purpose: Track results from AthleticLIVE instead of athletic.net. AthleticLIVE
#          is the live-results platform 254 timing companies run their meets on
#          (docs/timing_passes/athleticlive_tenants.txt); it publishes every
#          result as a static JSON document, and each meet names the
#          athletic.net meet it belongs to and each runner their athletic.net
#          athlete id. So a meet read from here lands in results_tf in the SAME
#          shape the athletic.net scraper writes: the anet meet id, the anet
#          athlete id, the anet division and event when we know them -- and
#          athletic.net is not asked for any of it.
#
#   python -m sources.athleticlive init                     # alive_meets
#   python -m sources.athleticlive discover [--back 3000]   # new and recent meets
#   python -m sources.athleticlive check  --meets 20        # vs anet rows we have; nothing written
#   python -m sources.athleticlive land   [--limit 50] [--meet ANET_ID] [--write]
#   python -m sources.athleticlive events                   # genders of anet event ids
#                                                           # scraper.EVENT_ID_TO_SHORT lacks
#   python -m sources.athleticlive status
#
# ★ WHY (owner, 2026-10-26: "we're trying to not scrape athletic net as much
#   ... I'd like to avoid athletic in general so when possible use local").
#   2025-26: 46% of anet's track meets are on AthleticLIVE (15,775 of
#   34,593) and 28% of its cross country. athletic.net's whole-meet results
#   call answers 403 without a login since 2026-10; the per-event fallback is
#   one request per event per division, dozens a meet. Here a meet is one
#   summary plus one small document per event, from a CDN.
#
# WHERE IT READS (both public, both honoured through sources.fetch):
#   https://trackmeet-io.firebaseio.com/meet_list/<id>.json       a meet: name, dates,
#        state, timer, and `ani` -- the athletic.net meet id. robots.txt opens
#        every *.json document (sources.fetch.RobotsRules reads its wildcards).
#   https://trackmeet-io.firebaseio.com/meet_<id>/event_summary.json   its events
#   https://athleticlive.blob.core.windows.net/$web/ind_res_list/_doc/<event>
#   https://athleticlive.blob.core.windows.net/$web/rel_res_list/_doc/<event>
#        one event's results: place, mark, wind, heat, and for each runner
#        `a.ani` (athletic.net athlete id), grade, team (`t.ani`, anet team id)
# ! NOT api.athletic.live: it answers 401, and a 401 is a no. Not
#   live.athletic.net either (its robots.txt closes /meets/*/athletes and /live).
# ! AN HONEST USER AGENT (sources.fetch.USER_AGENT), one request at a time
#   per host, INTERVAL seconds apart. These are a CDN and Firebase, not a
#   timer's own server, so the gap is 1 s rather than the timers' 10.
#
# HOW A MEET LANDS (landMeet):
#   1. athletic.net's slots for the meet, when we have them: meets_tf rows
#      (division + event) a meta scrape or the recovery path already wrote.
#      Each AthleticLIVE event is matched to one by event, gender and
#      division name (matchSlots). A matched event lands under anet's own
#      (div_id, event_id, event_short), so recover_tf_events never asks
#      athletic.net for it again (it is logged in tf_scraped_events). An
#      event that matches no slot is NOT landed: the slot stays in the
#      recovery gap, and landing it under keys of our own would make the
#      same race twice the day recovery found it.
#   2. No slots at all (a meet athletic.net was never asked about): the
#      meet's divisions and events are AthleticLIVE's (ownSlot): div_id
#      DIV_BASE + its division id, event_id anet's catalogue id for the event
#      and gender (scraper.EVENT_ID_TO_SHORT), so a race keys the way anet's
#      would. The meta row is written only when there is none.
#   3. A meet that already has athletic.net result rows is skipped: anet has
#      it, and two copies is the one thing this must never make.
#
# IDS
#   result_id   RESULT_BASE + AthleticLIVE's result id: positive (record books
#               read result_id > 0 as "an anet-keyed row", which these are),
#               stable on a re-read, and above any id anet has issued
#   athlete_id  the runner's athletic.net id (`a.ani`); person_id seeds from it
#               exactly as saveAthletesBulk / saveResultsTFBulk seed anet's.
#               A runner AthleticLIVE gives no anet id is not landed (counted).
#
# ! TRACK ONLY FOR NOW. XC meets are in meet_list (`o` = 'xc') but their
#   event_summary is empty; where an XC race's results live is still to find.

import argparse
import asyncio
import datetime
import json
import os
import re
import sys
import zlib
from collections import Counter, defaultdict, namedtuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

FIREBASE = "https://trackmeet-io.firebaseio.com"
BLOB = "https://athleticlive.blob.core.windows.net/$web"
INTERVAL = float(os.environ.get("XCP_ALIVE_INTERVAL", "1"))
PAGE = 200                          # meet_list documents per discovery request
REFRESH_BACK = 3000                 # ids below the newest re-read each discovery
                                    # (a meet gets its anet id and results later)
RESULT_BASE = 3_000_000_000_000     # result_id = RESULT_BASE + AthleticLIVE result id
DIV_BASE = 3_000_000_000_000        # own div_id = DIV_BASE + AthleticLIVE division id
EVENT_BASE = 3_000_000              # an event anet's catalogue lacks: + crc % 1e6
SETTLED_DAYS = 7                    # a meet this old reads its results from the store
MAX_AGE_SETTLED = 365 * 86400

_LEVEL_BITS = {"elementary": 1, "middleschool": 2, "highschool": 4,
               "college": 8, "open": 16}
_STATE_ABBR = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "district of columbia": "DC", "florida": "FL", "georgia": "GA", "hawaii": "HI",
    "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI",
    "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}

Slot = namedtuple("Slot", "event_id div_id event_short gender division")

DDL = """
CREATE TABLE IF NOT EXISTS alive_meets (
    alive_id     bigint PRIMARY KEY,     -- AthleticLIVE's meet id
    anet_ids     bigint[],               -- the athletic.net meet(s) it names
    sport        text,                   -- 'TF' / 'XC'
    indoor       boolean,
    meet_date    text,                   -- ISO, first day
    date_end     text,
    name         text,
    venue        text,                   -- `lo`, the address as AthleticLIVE has it
    place        text,                   -- `ls`, 'Oxnard, CA'
    state        text,                   -- two letters
    timer        text,                   -- the tenant ('speedtiming')
    levels       text[],
    updated      text,                   -- the document's own `ua`
    seen_at      timestamptz NOT NULL DEFAULT now(),
    landed_at    timestamptz,
    landed_n     integer,
    landed_note  text
);
CREATE INDEX IF NOT EXISTS alive_meets_anet ON alive_meets USING gin (anet_ids);
"""


# ------------------------------------------------------------------ #
#  parsing (pure)
# ------------------------------------------------------------------ #

def _stateOf(doc):
    place = str(doc.get("ls") or "")
    tail = place.rsplit(",", 1)[-1].strip() if "," in place else ""
    if len(tail) == 2 and tail.isalpha():
        return tail.upper()
    return _STATE_ABBR.get(str(doc.get("lsa") or "").strip().lower())


def parseMeet(alive_id, doc):
    """One meet_list document -> an alive_meets row (dict), or None."""
    if not isinstance(doc, dict):
        return None
    ids = []
    for v in [doc.get("ani")] + [x.get("ani") for x in (doc.get("anis") or [])
                                 if isinstance(x, dict)]:
        try:
            v = int(v)
        except (TypeError, ValueError):
            continue
        if v > 0 and v not in ids:
            ids.append(v)
    o = str(doc.get("o") or "").lower()
    days = [d for d in (doc.get("md") or []) if isinstance(d, str)]
    first = doc.get("sdy") or (min(days) if days else None)
    return {
        "alive_id": int(alive_id),
        "anet_ids": ids,
        "sport": "XC" if o == "xc" else "TF" if o in ("outdoor", "indoor") else None,
        "indoor": o == "indoor",
        "meet_date": first,
        "date_end": max(days) if days else first,
        "name": doc.get("n") or doc.get("ln"),
        "venue": doc.get("lo"),
        "place": doc.get("ls"),
        "state": _stateOf(doc),
        "timer": doc.get("li") or doc.get("e"),
        "levels": [str(x) for x in (doc.get("mvs") or [])],
        "updated": doc.get("ua"),
    }


def _gender(text):
    t = str(text or "").strip().lower()
    if t in ("boys", "men", "male", "b", "m"):
        return "m"
    if t in ("girls", "women", "female", "g", "f", "w"):
        return "f"
    return None


def parseSummary(summary):
    """event_summary.json -> its events, one dict each, in AthleticLIVE's
    order. kind 'ind' / 'rel' says which blob index holds the results."""
    out = []
    if not isinstance(summary, dict):
        return out
    for key, v in summary.items():
        if not isinstance(v, dict) or v.get("i") is None:
            continue
        relay = str(key).startswith("Relay-") or v.get("ec") == "Relay"
        out.append({
            "kind": "rel" if relay else "ind",
            "id": int(v["i"]),
            "ab": v.get("ab") or "",
            "name": v.get("n") or "",
            "gender": _gender(v.get("gl")) or _gender(v.get("ga")),
            "division": v.get("na") or v.get("dv") or "",
            "round": str(v.get("runm") or "").strip().lower() or None,
            "field": v.get("peg") == "Field" or "Field" in (v.get("egs") or []),
            "xc": bool(v.get("xc")),
            "order": v.get("nu"),
        })
    out.sort(key=lambda e: (e["order"] is None, e["order"] or 0, e["id"]))
    return out


_ALIAS = {
    "sp": "shot", "shotput": "shot", "dt": "discus", "jt": "jav",
    "javelin": "jav", "ht": "hammer", "wt": "weight",
    "mile": "1mile", "1mi": "1mile", "2mi": "2mile",
    "55h": "55mh", "60h": "60mh", "100h": "100mh", "110h": "110mh",
    "300h": "300mh", "400h": "400mh",
}
_RELAY_RX = re.compile(r"(4x\d+)m?r?")


def canonShort(short):
    """One spelling per event for matching: '4x100mR' and '4x100m' meet,
    'SP' and 'shot', '110mH' and '110mh'."""
    t = re.sub(r"\s+", "", str(short or "").lower())
    m = _RELAY_RX.fullmatch(t)
    if m:
        return f"{m.group(1)}m"
    return _ALIAS.get(t, t)


def canonDiv(name):
    return " ".join(str(name or "").lower().replace("-", " ").split())


def matchSlots(events, slots):
    """AthleticLIVE events onto athletic.net's slots for the same meet.
    -> ({alive event id: Slot}, {alive event id: reason}).

    A slot is (event, division). An event takes the slot of its event and
    gender whose division has its name; failing a name match, the ONE slot of
    its event and gender -- unless two AthleticLIVE divisions would share it,
    which is two races in one slot, and neither is landed."""
    by = defaultdict(list)
    for s in slots:
        by[canonShort(s.event_short)].append(s)
    exact, loose, why = {}, {}, {}
    for ev in events:
        cands = [s for s in by.get(canonShort(ev["ab"]), [])
                 if s.gender is None or ev["gender"] is None or s.gender == ev["gender"]]
        if not cands:
            why[ev["id"]] = "no anet slot"
            continue
        named = [s for s in cands if canonDiv(s.division) == canonDiv(ev["division"])]
        if len(named) == 1:
            exact[ev["id"]] = named[0]
        elif len(cands) == 1:
            loose[ev["id"]] = cands[0]
        else:
            why[ev["id"]] = "ambiguous division"
    divs = {ev["id"]: canonDiv(ev["division"]) for ev in events}
    genders = {ev["id"]: ev["gender"] for ev in events}
    taken = defaultdict(set)                      # slot -> divisions landing in it
    for eid, s in exact.items():
        taken[s].add(divs[eid])
    for eid, s in loose.items():
        taken[s].add(divs[eid])
    out = {}
    for eid, s in list(exact.items()) + list(loose.items()):
        if eid in loose and len(taken[s]) > 1:
            why[eid] = "shares a slot with another division"
            continue
        out[eid] = s
    # a slot with no gender of its own must not take both genders
    seen = defaultdict(set)
    for eid, s in out.items():
        seen[s].add(genders[eid])
    for eid in [e for e, s in out.items() if len(seen[s]) > 1]:
        why[eid] = "one slot, both genders"
        del out[eid]
    return out, why


_LEARNED = None


def learnedEvents(cur):
    """{anet event id: (event_short, gender)} from anet_event_gender: the
    events scraper.EVENT_ID_TO_SHORT does not list (60m is 42), gendered by
    who ran them (`events` builds it). {} before it is built."""
    global _LEARNED
    if _LEARNED is None:
        if cur is None:
            return {}                    # nothing to read: do not cache the nothing
        _LEARNED = {}
        cur.execute("SELECT to_regclass('anet_event_gender')")
        if cur.fetchone()[0]:
            cur.execute("SELECT event_id, event_short, gender FROM anet_event_gender")
            _LEARNED = {e: (s, g) for e, s, g in cur.fetchall()}
    return _LEARNED


def _catalogue(cur=None):
    """{(canonShort, gender): (anet event id, anet event_short)}."""
    from scraper import EVENT_ID_TO_SHORT
    out = {}
    for eid, (short, g) in EVENT_ID_TO_SHORT.items():
        out.setdefault((canonShort(short), g), (eid, short))
    for eid, (short, g) in sorted(learnedEvents(cur).items()):
        if g:
            out.setdefault((canonShort(short), g), (eid, short))
    return out


def ownSlot(ev, alive_div_id, catalogue):
    """The slot an event takes when athletic.net has none for the meet."""
    c = canonShort(ev["ab"])
    hit = catalogue.get((c, ev["gender"]))
    if hit:
        eid, short = hit
    else:
        short = c
        eid = EVENT_BASE + zlib.crc32(f"{c}|{ev['gender']}".encode()) % 1_000_000
    if alive_div_id is None:
        alive_div_id = zlib.crc32(canonDiv(ev["division"]).encode()) % 1_000_000_000
    return Slot(eid, DIV_BASE + int(alive_div_id), short, ev["gender"], ev["division"] or None)


def _int(v):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def resultRows(doc, ev):
    """One event's blob -> (results, athletes, skipped Counter). Results are
    the dicts saveResultsTFBulk reads from athletic.net's own JSON."""
    from result_status import normalise
    src = doc.get("_source", doc) if isinstance(doc, dict) else {}
    rows = src.get("rts" if ev["kind"] == "rel" else "r") or []
    results, athletes, skipped = [], [], Counter()
    for x in rows:
        if not isinstance(x, dict) or x.get("i") is None:
            skipped["malformed"] += 1
            continue
        mark = str(x.get("m") or "").strip()
        status = normalise(mark)
        ms = _int(x.get("im"))
        if ev["field"]:
            if not status and not re.search(r"\d", mark):
                skipped["no mark"] += 1
                continue
        elif not status and not (ms and ms > 0):
            skipped["no time"] += 1
            continue
        a = x.get("a") if isinstance(x.get("a"), dict) else {}
        team = (x.get("t") if ev["kind"] == "rel" else a.get("t")) or {}
        r = {
            "IDResult": RESULT_BASE + int(x["i"]),
            "Place": _int(x.get("p")),
            "Wind": x.get("w") or None,
            "Round": ev["round"],
            "Heat": _int(x.get("hn")),
            "TeamID": _int(team.get("ani")),
            "TeamName": team.get("n") or team.get("f"),
            "SortInt": None if (ev["field"] or status) else ms,
            "Result": status if status else (mark if ev["field"] else None),
        }
        if ev["kind"] == "rel":
            legs = sorted((leg for leg in (x.get("rm") or []) if isinstance(leg, dict)),
                          key=lambda leg: leg.get("to") or 0)
            r["FirstName"] = "<BR>".join(str((leg.get("a") or {}).get("n") or "")
                                         for leg in legs)
            r["LastName"] = ""
        else:
            ani = _int(a.get("ani"))
            if not ani:
                skipped["no anet athlete id"] += 1
                continue
            r.update({"AthleteID": ani, "FirstName": a.get("fn"), "LastName": a.get("l"),
                      "Grade": str(a.get("y") or ""),
                      "Gender": {"male": "M", "female": "F"}.get(str(a.get("g") or "").lower())})
            athletes.append({"AthleteID": ani, "FirstName": a.get("fn"),
                             "LastName": a.get("l"), "Gender": r["Gender"],
                             "SchoolName": r["TeamName"]})
        results.append(r)
    return results, athletes, skipped


def buildSave(meet_info, events, slots, blobs, own_div_ids=None):
    """Everything _saveTFMeet takes, for the events that have a slot.
    blobs: {alive event id: blob doc}. -> (meets_to_save, athletes,
    results_to_save, Counter of what was left out and why)."""
    from scrape_results import isRelayEvent
    from event_parse import distanceFromEventShort
    meets_to_save, athletes, results_to_save = [], [], []
    left = Counter()
    seen_slot = set()
    for ev in events:
        slot = slots.get(ev["id"])
        doc = blobs.get(ev["id"])
        if slot is None or doc is None:
            continue
        rows, ath, skipped = resultRows(doc, ev)
        left.update(skipped)
        if not rows:
            continue
        is_relay = 1 if (ev["kind"] == "rel" or isRelayEvent(slot.event_short)) else 0
        is_field = 1 if ev["field"] else 0
        key = (slot.div_id, slot.event_id)
        if key not in seen_slot:
            seen_slot.add(key)
            dist = None if is_field else distanceFromEventShort(slot.event_short)[0]
            meets_to_save.append((meet_info, slot.div_id, slot.event_id, slot.event_short,
                                  dist, slot.division))
        athletes.extend(ath)
        for r in rows:
            results_to_save.append((r, meet_info, slot.div_id, slot.event_id,
                                    slot.event_short, is_relay, r.get("TeamName") or "Unknown",
                                    is_field))
    return meets_to_save, athletes, results_to_save, left


def metaFromAlive(m, anet_meet_id):
    """An anet-shaped meet_info from an alive_meets row, for a meet with no
    athletic.net meta of its own. No Location.ID: anet's venue id is not
    AthleticLIVE's to give (the venue geocoder mints one later)."""
    mask = 0
    for lv in m.get("levels") or []:
        mask |= _LEVEL_BITS.get(str(lv).lower(), 0)
    venue = str(m.get("venue") or "").split(",")[0].strip() or None
    return {"ID": anet_meet_id, "Name": m.get("name"),
            "MeetDate": m.get("meet_date") or "",
            "Location": {"ID": None, "Name": venue, "State": m.get("state"),
                         "Indoor": bool(m.get("indoor"))},
            "LevelMask": mask or None, "Finalized": None}


# ------------------------------------------------------------------ #
#  fetching
# ------------------------------------------------------------------ #

def makeFetcher(store=None, getter=None, robots=None):
    from sources.fetch import Fetcher, HostLimiter, RawStore
    return Fetcher(store or RawStore(), limiter=HostLimiter(interval=INTERVAL),
                   robots=robots, getter=getter)


def _json(fetched):
    if fetched is None or not fetched.body:
        return None
    try:
        return json.loads(fetched.body)
    except (ValueError, UnicodeDecodeError):
        return None


def keyedPage(page):
    """A meet_list page as {key: document}.

    ⚠ FIREBASE ANSWERS SOME PAGES AS A JSON LIST. When a page's keys are
      integers filling more than half of 0..max (the first pages: ids 1, 2,
      3 ...), the REST API returns them as an array -- position = id, a null
      where there is none. The first server run read that as "no page" and
      stopped after one request with 0 meets (2026-10-26)."""
    if isinstance(page, list):
        return {str(i): v for i, v in enumerate(page) if v is not None}
    return page if isinstance(page, dict) else {}


def listUrl(start, limit=PAGE):
    return (f'{FIREBASE}/meet_list.json?orderBy="$key"&startAt="{int(start)}"'
            f"&limitToFirst={int(limit)}")


def summaryUrl(alive_id):
    return f"{FIREBASE}/meet_{int(alive_id)}/event_summary.json"


def eventUrl(ev):
    index = "rel_res_list" if ev["kind"] == "rel" else "ind_res_list"
    return f"{BLOB}/{index}/_doc/{int(ev['id'])}"


def _settled(meet_date):
    try:
        d = datetime.date.fromisoformat(str(meet_date)[:10])
    except ValueError:
        return False
    return (datetime.date.today() - d).days >= SETTLED_DAYS


async def fetchMeet(fetcher, alive_id, meet_date=None):
    """-> (events, {event id: blob}, note). Every event's results document
    is asked for; a settled meet's come from the store when we have them."""
    age = MAX_AGE_SETTLED if _settled(meet_date) else None
    summary = _json(await fetcher.fetch(summaryUrl(alive_id), max_age=age))
    events = [e for e in parseSummary(summary) if not e["xc"]]
    if not events:
        return [], {}, "no track events"
    got = await fetcher.fetchAll([eventUrl(e) for e in events], max_age=age)
    blobs = {}
    for ev, f in zip(events, got):
        doc = _json(f)
        if isinstance(doc, dict):
            blobs[ev["id"]] = doc
    return events, blobs, ""


# ------------------------------------------------------------------ #
#  database
# ------------------------------------------------------------------ #

def initTables(cur):
    cur.execute(DDL)


_UPSERT = """
INSERT INTO alive_meets (alive_id, anet_ids, sport, indoor, meet_date, date_end, name,
                         venue, place, state, timer, levels, updated)
VALUES (%(alive_id)s, %(anet_ids)s, %(sport)s, %(indoor)s, %(meet_date)s, %(date_end)s,
        %(name)s, %(venue)s, %(place)s, %(state)s, %(timer)s, %(levels)s, %(updated)s)
ON CONFLICT (alive_id) DO UPDATE SET
    anet_ids = EXCLUDED.anet_ids, sport = EXCLUDED.sport, indoor = EXCLUDED.indoor,
    meet_date = EXCLUDED.meet_date, date_end = EXCLUDED.date_end, name = EXCLUDED.name,
    venue = EXCLUDED.venue, place = EXCLUDED.place, state = EXCLUDED.state,
    timer = EXCLUDED.timer, levels = EXCLUDED.levels, updated = EXCLUDED.updated,
    seen_at = now()
"""


async def discover(cur, fetcher, back=REFRESH_BACK, start=None, max_pages=None):
    """Walk meet_list upward from (newest known - back), PAGE at a time,
    until a page comes back short. -> (meets read, pages)."""
    if start is None:
        cur.execute("SELECT max(alive_id) FROM alive_meets")
        top = cur.fetchone()[0] or 0
        start = max(0, top - back)
    n = pages = 0
    while max_pages is None or pages < max_pages:
        page = keyedPage(_json(await fetcher.fetch(listUrl(start))))
        pages += 1
        ids = sorted(int(k) for k in page if str(k).isdigit())
        for k in ids:
            row = parseMeet(k, page.get(str(k)))
            if row:
                cur.execute(_UPSERT, row)
                n += 1
        if len(ids) < PAGE:
            break
        start = ids[-1] + 1
    return n, pages


def anetSlots(cur, meet_id):
    """athletic.net's slots for a meet from meets_tf: [Slot]. An event's
    gender is scraper.EVENT_ID_TO_SHORT's, else the learned one
    (anet_event_gender): without it the boys' and girls' 60 m are one
    event to matchSlots and neither lands."""
    from scraper import EVENT_ID_TO_SHORT
    learned = learnedEvents(cur)
    cur.execute("""SELECT DISTINCT event_id, div_id, event_short, division
                   FROM meets_tf WHERE meet_id = %s AND event_id IS NOT NULL
                     AND div_id IS NOT NULL""", (meet_id,))
    out = []
    for eid, did, short, division in cur.fetchall():
        g = (EVENT_ID_TO_SHORT.get(eid) or learned.get(eid) or (None, None))[1]
        out.append(Slot(eid, did, short, g, division))
    return out


EVENT_GENDER_DDL = """
CREATE TABLE IF NOT EXISTS anet_event_gender (
    event_id     bigint PRIMARY KEY,
    event_short  text,
    gender       text,           -- 'm' / 'f' / NULL (mixed: no answer)
    n_m          integer,
    n_f          integer,
    built_at     timestamptz NOT NULL DEFAULT now()
)"""

# up to 20 meets per event, 30 runners per race: enough to say who runs it
_EVENT_GENDER_SQL = """
WITH ev AS (
    SELECT event_id, meet_id, div_id, event_short,
           row_number() OVER (PARTITION BY event_id ORDER BY meet_id DESC) AS rn
    FROM   meets_tf
    WHERE  event_id IS NOT NULL AND NOT (event_id = ANY(%(known)s))
)
SELECT ev.event_id, min(ev.event_short),
       count(*) FILTER (WHERE upper(a.gender) = 'M'),
       count(*) FILTER (WHERE upper(a.gender) = 'F')
FROM   ev
CROSS  JOIN LATERAL (SELECT r.athlete_id FROM results_tf r
                     WHERE r.meet_id = ev.meet_id AND r.div_id = ev.div_id
                       AND r.event_id = ev.event_id AND r.athlete_id IS NOT NULL
                     LIMIT 30) r
JOIN   LATERAL (SELECT gender FROM athletes a WHERE a.athlete_id = r.athlete_id
                LIMIT 1) a ON true
WHERE  ev.rn <= 20
GROUP  BY ev.event_id
"""


def eventGender(n_m, n_f, min_n=10, share=0.9):
    """'m' / 'f' when nearly everyone who ran it is one gender, else None."""
    n = (n_m or 0) + (n_f or 0)
    if n < min_n:
        return None
    if n_m / n >= share:
        return "m"
    if n_f / n >= share:
        return "f"
    return None


def buildEventGender(cur):
    from scraper import EVENT_ID_TO_SHORT
    global _LEARNED
    cur.execute(EVENT_GENDER_DDL)
    cur.execute("SET LOCAL statement_timeout = 0")
    cur.execute(_EVENT_GENDER_SQL, {"known": sorted(EVENT_ID_TO_SHORT)})
    rows = cur.fetchall()
    cur.execute("DELETE FROM anet_event_gender")
    for eid, short, n_m, n_f in rows:
        cur.execute("""INSERT INTO anet_event_gender (event_id, event_short, gender, n_m, n_f)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (eid, short, eventGender(n_m, n_f), n_m, n_f))
    _LEARNED = None
    return rows


def anetHasResults(cur, meet_id):
    """True when results_tf already holds athletic.net's own rows for it."""
    cur.execute("""SELECT 1 FROM results_tf WHERE meet_id = %s AND source = 'anet'
                     AND result_id > 0 AND result_id < %s LIMIT 1""",
                (meet_id, RESULT_BASE))
    return cur.fetchone() is not None


def metaFromDb(cur, meet_id):
    """meets_tf_meta as meet_info, or None."""
    cur.execute("""SELECT meet_name, meet_date, location_id, venue_name, state,
                          gps_lat, gps_long, is_indoor, track_type, track_length,
                          level_mask, finalized
                   FROM meets_tf_meta WHERE meet_id = %s""", (meet_id,))
    row = cur.fetchone()
    if not row:
        return None
    (name, date, loc, venue, state, lat, lng, indoor, ttype, tlen, mask, fin) = row
    return {"ID": meet_id, "Name": name, "MeetDate": str(date or ""),
            "Location": {"ID": loc, "Name": venue, "State": state, "Lat": lat,
                         "Long": lng, "Indoor": bool(indoor), "TrackType": ttype,
                         "TrackLength": tlen},
            "LevelMask": mask, "Finalized": fin}


def aliveFor(cur, meet_id, sport="TF"):
    """The alive_meets row (dict) of this sport naming this athletic.net
    meet, or None. The newest when several do."""
    cur.execute("""SELECT alive_id, anet_ids, sport, indoor, meet_date, date_end, name,
                          venue, place, state, timer, levels, landed_at, landed_n
                   FROM alive_meets WHERE anet_ids @> ARRAY[%s]::bigint[] AND sport = %s
                   ORDER BY alive_id DESC LIMIT 1""", (meet_id, sport))
    row = cur.fetchone()
    if not row:
        return None
    keys = ("alive_id", "anet_ids", "sport", "indoor", "meet_date", "date_end", "name",
            "venue", "place", "state", "timer", "levels", "landed_at", "landed_n")
    return dict(zip(keys, row))


def _divIds(blobs):
    out = {}
    for eid, doc in blobs.items():
        dv = (doc.get("_source", doc) or {}).get("dv")
        if isinstance(dv, dict) and dv.get("i") is not None:
            out[eid] = int(dv["i"])
    return out


def plan(meet_id, alive, events, blobs, slots, meta, catalogue=None):
    """What landing one meet would write. Pure. -> dict with meet_info,
    meets_to_save, athletes, results_to_save, own (True: AthleticLIVE's
    own slots), unmatched {event id: reason}, left Counter."""
    if slots:
        matched, why = matchSlots(events, slots)
        own = False
    else:
        cat = catalogue if catalogue is not None else _catalogue()
        divs = _divIds(blobs)
        matched = {ev["id"]: ownSlot(ev, divs.get(ev["id"]), cat) for ev in events}
        why, own = {}, True
    meet_info = meta or metaFromAlive(alive, meet_id)
    m2s, ath, res, left = buildSave(meet_info, events, matched, blobs)
    for ev in events:
        if ev["id"] in matched and ev["id"] not in blobs:
            why[ev["id"]] = "results document missing"
    return {"meet_info": meet_info, "meets_to_save": m2s, "athletes": ath,
            "results_to_save": res, "own": own, "unmatched": why, "left": left,
            "events": events}


def write(conn, meet_id, p, label="[alive]"):
    """Land a plan. Own slots write meets_tf (and the meta row when there is
    none); anet's slots exist already, so only results go in."""
    from scrape_results import _saveTFMeet
    cur = conn.cursor()
    m2s = p["meets_to_save"]
    if p["own"]:
        cur.execute("SELECT 1 FROM meets_tf_meta WHERE meet_id = %s", (meet_id,))
        if cur.fetchone():
            from database import saveMeetTF
            for mi, div, ev, short, dist, division in m2s:
                saveMeetTF(conn, mi, div, ev, short, dist, division)
            conn.commit()
            m2s = []
    else:
        m2s = []
    return _saveTFMeet(meet_id, label, m2s, p["athletes"], p["results_to_save"])


def noteLanded(cur, alive_id, n, note):
    cur.execute("""UPDATE alive_meets SET landed_at = now(), landed_n = %s, landed_note = %s
                   WHERE alive_id = %s""", (n, note, alive_id))


# ------------------------------------------------------------------ #
#  the athletic.net scraper's hook
# ------------------------------------------------------------------ #

async def landForScrape(meet_id, meet_info, events_dict, event_divs, label="[alive]",
                        fetcher=None):
    """Called by scrape_results.scrapeMeetTF before it asks athletic.net for
    a meet's results. -> None when AthleticLIVE does not have the meet (go
    ahead), else (results saved or -1, the event_divs still uncovered)."""
    from database import getConn
    with getConn() as conn:
        cur = conn.cursor()
        try:
            alive = aliveFor(cur, meet_id)
        except Exception:                                   # noqa: BLE001
            conn.rollback()
            return None                  # no alive_meets table yet: anet as before
        if not alive:
            return None
        if alive["landed_at"] is not None and (alive["landed_n"] or 0) > 0:
            return alive["landed_n"], []         # landed already: never twice
        if anetHasResults(cur, meet_id):
            return None                  # athletic.net's copy is there: its re-scrape
    names = meet_info.get("_divisionByIdDiv") or {}
    slots = []
    for ed in event_divs or []:
        info = (events_dict or {}).get(ed.get("e"))
        if info:
            slots.append(Slot(ed.get("e"), ed.get("d"), info.get("event_short"),
                              info.get("gender"), names.get(ed.get("d"))))
    fetcher = fetcher or makeFetcher()
    events, blobs, note = await fetchMeet(fetcher, alive["alive_id"], alive["meet_date"])
    if not events or not slots:
        return None
    p = plan(meet_id, alive, events, blobs, slots, meet_info)
    if not p["results_to_save"]:
        return None
    covered = {(t[3], t[2]) for t in p["results_to_save"]}       # (event_id, div_id)
    from scrape_results import _saveTFMeet
    from event_parse import distanceFromEventShort
    # every slot athletic.net named, landed or not: the ones AthleticLIVE did
    # not cover stay in the recovery gap (meets_tf without tf_scraped_events)
    m2s = []
    for ed in event_divs:
        info = (events_dict or {}).get(ed.get("e"))
        if not info:
            continue
        m2s.append((meet_info, ed.get("d"), ed.get("e"), info.get("event_short"),
                    distanceFromEventShort(info.get("event_short"))[0],
                    names.get(ed.get("d"))))
    n = _saveTFMeet(meet_id, label, m2s, p["athletes"], p["results_to_save"])
    if n < 0:
        return n, list(event_divs)       # failed: the meet is retried as any failure is
    with getConn() as conn:
        cur = conn.cursor()
        noteLanded(cur, alive["alive_id"], n, "scrape hook")
        conn.commit()
    left = [ed for ed in event_divs if (ed.get("e"), ed.get("d")) not in covered]
    print(f"{label} Meet {meet_id} (TF): {n} results from AthleticLIVE "
          f"{alive['alive_id']}; {len(left)} event-divs left to athletic.net")
    return n, left


# ------------------------------------------------------------------ #
#  commands
# ------------------------------------------------------------------ #

_LOOP = None


def _run(coro):
    """Run a coroutine on ONE event loop for the whole command.

    ⚠ NOT asyncio.run PER MEET. The fetcher's per-host locks and its
      semaphore belong to the loop they were first used on; a second
      asyncio.run makes a new loop and the first lock it meets raises
      "bound to a different event loop" (the server's first check,
      2026-10-26, on its second meet)."""
    global _LOOP
    if _LOOP is None or _LOOP.is_closed():
        _LOOP = asyncio.new_event_loop()
    return _LOOP.run_until_complete(coro)


def _conn():
    from database import getConn, initPool
    initPool()
    return getConn()


def cmdInit(a):
    with _conn() as conn:
        initTables(conn.cursor())
        conn.commit()
    print("[alive] alive_meets ready")
    return 0


def cmdDiscover(a):
    fetcher = makeFetcher()
    with _conn() as conn:
        cur = conn.cursor()
        initTables(cur)
        n, pages = _run(discover(cur, fetcher, back=a.back, start=a.start,
                                 max_pages=a.max_pages))
        conn.commit()
        cur.execute("""SELECT sport, count(*), count(*) FILTER (WHERE cardinality(anet_ids) > 0),
                              max(meet_date) FROM alive_meets GROUP BY 1 ORDER BY 1""")
        rows = cur.fetchall()
    print(f"[alive] {n:,} meets read in {pages} requests ({fetcher.stats})")
    for sport, total, with_anet, last in rows:
        print(f"    {sport or '?':<3} {total:>7,} meets, {with_anet:>7,} name an athletic.net meet, "
              f"latest {last}")
    return 0


def _candidates(cur, a):
    if a.meet:
        return [(m,) for m in a.meet]
    cur.execute("""SELECT DISTINCT u.anet_id, am.meet_date
                   FROM alive_meets am CROSS JOIN LATERAL unnest(am.anet_ids) u(anet_id)
                   WHERE am.sport = 'TF' AND am.landed_at IS NULL
                     AND am.meet_date <= %s
                   ORDER BY am.meet_date DESC LIMIT %s""",
                (datetime.date.today().isoformat(), a.limit))
    return [(r[0],) for r in cur.fetchall()]


def _report(meet_id, alive, p):
    evs = len(p["events"])
    landed_ev = len({(r[2], r[3]) for r in p["results_to_save"]})
    why = Counter(p["unmatched"].values())
    print(f"  meet {meet_id} (alive {alive['alive_id']}, {alive['meet_date']}, "
          f"{alive['name']!s:.40}): {len(p['results_to_save'])} results in "
          f"{landed_ev} races from {evs} events"
          + (" [own slots]" if p["own"] else " [anet slots]")
          + (f"; not landed: {dict(why)}" if why else "")
          + (f"; rows left out: {dict(p['left'])}" if p["left"] else ""))


def cmdLand(a):
    fetcher = makeFetcher()
    total = Counter()
    with _conn() as conn:
        cur = conn.cursor()
        initTables(cur)
        conn.commit()
        for (meet_id,) in _candidates(cur, a):
            alive = aliveFor(cur, meet_id)
            if not alive:
                total["not on AthleticLIVE"] += 1
                continue
            if anetHasResults(cur, meet_id):
                total["anet already has it"] += 1
                if a.write:
                    noteLanded(cur, alive["alive_id"], 0, "anet has it")
                    conn.commit()
                continue
            events, blobs, note = _run(fetchMeet(fetcher, alive["alive_id"],
                                                 alive["meet_date"]))
            if not events:
                total[note or "no events"] += 1
                continue
            p = plan(meet_id, alive, events, blobs, anetSlots(cur, meet_id),
                     metaFromDb(cur, meet_id), catalogue=_catalogue(cur))
            _report(meet_id, alive, p)
            total["results"] += len(p["results_to_save"])
            total["meets"] += 1
            if a.write and p["results_to_save"]:
                n = write(conn, meet_id, p)
                if n < 0:                # the save rolled back: try again next run
                    total["save failed"] += 1
                    continue
                noteLanded(cur, alive["alive_id"], n, "own slots" if p["own"] else "anet slots")
                conn.commit()
                total["written"] += n
    print(f"[alive] {dict(total)}  fetches {fetcher.stats}"
          + ("" if a.write else "  (dry run: nothing written; --write lands them)"))
    return 0


def _secs(r):
    return r["SortInt"] / 1000 if r.get("SortInt") else None


_FT_IN = re.compile(r"^(\d{1,3})\s*(?:'|-)\s*(\d{1,2}(?:\.\d{1,2})?)\s*\"?$")
_METRES = re.compile(r"^(\d{1,3}(?:\.\d{1,3})?)\s*m?$", re.IGNORECASE)


def markMetres(mark):
    """A field mark in metres, whichever way it is written: athletic.net's
    "78' 0", the timers' '55-11.25', AthleticLIVE's '23.77m'. None when it
    is not a mark (NH, FOUL, garbage). For comparing only: what is stored
    is the mark as printed."""
    s = str(mark or "").strip()
    m = _FT_IN.match(s)
    if m:
        inches = float(m.group(2))
        return None if inches >= 12 else int(m.group(1)) * 0.3048 + inches * 0.0254
    m = _METRES.match(s)
    return float(m.group(1)) if m else None


def _sameMark(alive_r, anet_row):
    """AthleticLIVE's row and athletic.net's carry the same mark: a time to
    the hundredth, a field mark to 1.5 cm (a quarter inch is 0.6 cm, and
    each side rounds its own conversion), or the same status."""
    t = _secs(alive_r)
    if t is not None and anet_row[4] is not None:
        return abs(t - anet_row[4]) < 0.011
    res = str(alive_r.get("Result") or "").strip()
    if not res:
        return False
    a, b = markMetres(res), markMetres(anet_row[5])
    if a is not None and b is not None:
        return abs(a - b) < 0.015
    return res.upper() in (str(anet_row[5] or "").strip().upper(),
                           str(anet_row[9] or "").strip().upper())


def compareMeet(have, results_to_save, known=None):
    """athletic.net's rows for a meet against what AthleticLIVE would land
    there. Individual rows only (a relay has no runner to match on).

    have: (result_id, athlete_id, div_id, event_id, time_seconds, mark,
    place, is_relay, event_short, status, name, person_id). known:
    {athlete_id: (person_id, name)} from `athletes`, for both sides' ids.
    -> (Counter, {kind: [example line]}).

    A row athletic.net has and AthleticLIVE does not, by runner id, is one
    of: the same race and the same mark under ANOTHER athlete id (the id
    question, sub-split by whether we know that id and whose it is); a race
    AthleticLIVE did not land at all; the runner in another race; absent."""
    known = known or {}
    theirs, ours = {}, {}
    for h in have:
        if h[1] and not h[7]:
            theirs.setdefault((h[1], h[2], h[3]), h)
    for t in results_to_save:
        if not t[5] and t[0].get("AthleteID"):
            ours.setdefault((t[0]["AthleteID"], t[2], t[3]), t)
    landed = {(k[1], k[2]) for k in ours}
    c, ex = Counter(), defaultdict(list)
    c["anet rows"], c["alive rows"] = len(theirs), len(ours)
    used, left = set(), []

    def nm(aid, fallback=None):
        return str((known.get(aid) or (None, None))[1] or fallback or aid)

    for key, h in theirs.items():
        t = ours.get(key)
        if t is None:
            left.append(h)
            continue
        used.add(key)
        r = t[0]
        c["same runner+race"] += 1
        if _sameMark(r, h):
            c["same mark"] += 1
        else:
            ex["same runner and race, different mark"].append(
                f"{nm(h[1], h[10]):<26} {h[8]}: anet {h[4] or h[5] or h[9]}  alive "
                f"{_secs(r) if _secs(r) is not None else r.get('Result')}")
        if r.get("Place") is not None and r.get("Place") == h[6]:
            c["same place"] += 1
    # the same race and mark, under another id
    spare = defaultdict(list)
    for key, t in ours.items():
        if key not in used:
            spare[(key[1], key[2])].append((key, t))
    by_runner = defaultdict(list)
    for key, t in ours.items():
        by_runner[key[0]].append(t)
    for h in left:
        slot = (h[2], h[3])
        twin = next((kt for kt in spare.get(slot, ())
                     if kt[0] not in used and _sameMark(kt[1][0], h)
                     and (kt[1][0].get("Place") is None or h[6] is None
                          or kt[1][0].get("Place") == h[6])), None)
        if twin:
            used.add(twin[0])
            ani = twin[0][0]
            mine = known.get(ani)
            if mine is None:
                kind = "other id, not in our athletes"
            elif mine[0] is not None and mine[0] == h[11]:
                kind = "other id, same person"
            else:
                kind = "other id, another person"
            c[f"same race+mark, {kind}"] += 1
            ex[f"same race and mark, {kind}"].append(
                f"{h[8]:<6} anet {h[1]} {nm(h[1], h[10])!r} person {h[11]}  |  alive {ani} "
                f"{twin[1][0].get('FirstName')} {twin[1][0].get('LastName')}"
                + (f", ours: person {mine[0]} {mine[1]!r}" if mine else ""))
        elif slot not in landed:
            c["anet: race not landed"] += 1
        elif by_runner.get(h[1]):
            c["anet: runner in another race"] += 1
            o = by_runner[h[1]][0]
            ex["runner in another race"].append(
                f"{nm(h[1], h[10]):<26} anet {h[8]} div {h[2]} ev {h[3]} -> alive {o[4]} "
                f"div {o[2]} ev {o[3]}")
        else:
            c["anet: runner not in alive"] += 1
            ex["anet runner AthleticLIVE does not have"].append(
                f"{nm(h[1], h[10]):<26} {h[8]} div {h[2]}: {h[4] or h[5] or h[9]} place {h[6]}")
    for key, t in ours.items():
        if key in used:
            continue
        r = t[0]
        marked = _secs(r) is not None or markMetres(r.get("Result")) is not None
        if not marked:
            c["alive only, no mark (DNS/SCR...)"] += 1
        else:
            c["alive only, with a mark"] += 1
            ex["alive runner athletic.net does not have"].append(
                f"{r.get('FirstName')} {r.get('LastName')!s:<16} {t[4]}: "
                f"{_secs(r) or r.get('Result')} place {r.get('Place')}")
    return c, ex


def _unlandedLines(meet_id, p, slots):
    """For the events plan() did not land: what AthleticLIVE calls it, and
    the slots athletic.net has for that event -- the division names and
    genders matchSlots had to choose between."""
    out = []
    for ev in p["events"]:
        why = p["unmatched"].get(ev["id"])
        if not why:
            continue
        cands = sorted({(s.event_id, s.gender or "?", s.division or "") for s in slots
                        if canonShort(s.event_short) == canonShort(ev["ab"])})
        out.append(f"meet {meet_id}: {ev['ab']} {ev['gender'] or '?'} [{ev['division']}] -> {why}; "
                   f"anet: {cands or sorted({s.event_short for s in slots})[:12]}")
    return out


def cmdEvents(a):
    with _conn() as conn:
        cur = conn.cursor()
        rows = buildEventGender(cur)
        conn.commit()
    named = [(e, s, eventGender(m, f), m, f) for e, s, m, f in rows]
    print(f"[alive] anet_event_gender: {len(rows)} event ids outside scraper.EVENT_ID_TO_SHORT, "
          f"{sum(1 for r in named if r[2])} with a gender")
    for e, s, g, m, f in sorted(named, key=lambda r: -(r[3] + r[4]))[:a.show]:
        print(f"    {e:>6} {s!s:<22} {g or '-':<2} boys {m:>5} girls {f:>5}")
    return 0


def cmdCheck(a):
    """Meets athletic.net already gave us AND AthleticLIVE has: land them in
    memory and compare with the rows we hold. Nothing is written."""
    fetcher = makeFetcher()
    agg = Counter()
    examples = defaultdict(list)
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT u.anet_id, am.alive_id
                       FROM alive_meets am CROSS JOIN LATERAL unnest(am.anet_ids) u(anet_id)
                       WHERE am.sport = 'TF' AND am.meet_date < %s
                         AND EXISTS (SELECT 1 FROM results_tf r WHERE r.meet_id = u.anet_id
                                     AND r.source = 'anet' AND r.result_id < %s)
                       ORDER BY md5(am.alive_id::text) LIMIT %s""",
                    (datetime.date.today().isoformat(), RESULT_BASE, a.meets))
        pairs = cur.fetchall()
        for meet_id, alive_id in pairs:
            alive = aliveFor(cur, meet_id)
            events, blobs, note = _run(fetchMeet(fetcher, alive_id, alive["meet_date"]))
            slots = anetSlots(cur, meet_id)
            p = plan(meet_id, alive, events, blobs, slots, None)
            cur.execute("""SELECT result_id, athlete_id, div_id, event_id, time_seconds, mark,
                                  place, is_relay, event_short, status, athlete_name, person_id
                           FROM results_tf WHERE meet_id = %s AND source = 'anet'
                             AND result_id < %s""", (meet_id, RESULT_BASE))
            have = cur.fetchall()
            ids = {h[1] for h in have if h[1]} | {t[0].get("AthleteID")
                                                   for t in p["results_to_save"]
                                                   if t[0].get("AthleteID")}
            cur.execute("""SELECT DISTINCT ON (athlete_id) athlete_id, person_id,
                                  btrim(concat_ws(' ', first_name, last_name))
                           FROM athletes WHERE athlete_id = ANY(%s)""", (sorted(ids),))
            known = {a: (pid, name) for a, pid, name in cur.fetchall()}
            res, why = compareMeet(have, p["results_to_save"], known)
            agg.update(res)
            agg["meets"] += 1
            for kind, ex in why.items():
                examples[kind].extend(ex[:max(2, a.show // 4)])     # a few from each meet
            for line in _unlandedLines(meet_id, p, slots)[:4]:
                examples["events not landed (alive event -> reason; anet's slots for it)"].append(line)
            other = sum(v for k, v in res.items() if k.startswith("same race+mark"))
            print(f"  meet {meet_id} ({alive['meet_date']}, {str(alive['name'])[:34]}): "
                  f"anet {res['anet rows']}, alive {res['alive rows']}; "
                  f"{res['same runner+race']} same runner+race ({res['same mark']} same mark); "
                  f"{other} same race+mark under another id; "
                  f"{res['anet: race not landed']} in races not landed, "
                  f"{res['anet: runner in another race']} in another race, "
                  f"{res['anet: runner not in alive']} absent"
                  + (f"; {note}" if note else "")
                  + (f"; events not landed {dict(Counter(p['unmatched'].values()))}"
                     if p["unmatched"] else ""), flush=True)
    for kind, ex in examples.items():
        print(f"\n  {kind} (up to {a.show}):")
        for line in ex[:a.show]:
            print(f"    {line}")
    print(f"[alive] check: {dict(agg)}  fetches {fetcher.stats}  (nothing written)")
    return 0


def cmdStatus(a):
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT sport, count(*), count(*) FILTER (WHERE cardinality(anet_ids) > 0),
                              count(*) FILTER (WHERE landed_at IS NOT NULL),
                              coalesce(sum(landed_n), 0), max(meet_date)
                       FROM alive_meets GROUP BY 1 ORDER BY 1""")
        for sport, n, wa, landed, rows, last in cur.fetchall():
            print(f"  {sport or '?':<3} {n:>7,} meets  {wa:>7,} with an anet id  "
                  f"{landed:>6,} landed  {rows:>9,} results  latest {last}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    d = sub.add_parser("discover")
    d.add_argument("--back", type=int, default=REFRESH_BACK)
    d.add_argument("--start", type=int)
    d.add_argument("--max-pages", type=int)
    c = sub.add_parser("check")
    c.add_argument("--meets", type=int, default=20)
    c.add_argument("--show", type=int, default=12, help="examples of each kind of difference")
    ld = sub.add_parser("land")
    ld.add_argument("--limit", type=int, default=50)
    ld.add_argument("--meet", type=int, action="append", help="this athletic.net meet id")
    ld.add_argument("--write", action="store_true")
    sub.add_parser("status")
    e = sub.add_parser("events")
    e.add_argument("--show", type=int, default=40)
    a = ap.parse_args(argv)
    return {"init": cmdInit, "discover": cmdDiscover, "check": cmdCheck,
            "land": cmdLand, "status": cmdStatus, "events": cmdEvents}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
