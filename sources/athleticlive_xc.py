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


def plan(anet_meet_id, alive, events, blobs):
    """Everything to save for one meet, in the track lander's plan shape so
    applyIds / holdUnknown apply unchanged. Pure."""
    meet_info = A.metaFromAlive(alive, anet_meet_id)
    p = {"results_to_save": [], "meets_to_save": [], "athletes": [], "own": True,
         "skipped": Counter(), "events": events}
    for ev in events:
        doc = blobs.get(ev["id"])
        if not isinstance(doc, dict):
            p["skipped"]["no results document"] += 1
            continue
        rows, athletes, skipped = A.resultRows(doc, ev)
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
                   ORDER BY am.meet_date DESC LIMIT %s""",
                (datetime.date.today().isoformat(), list(A.US_STATES), limit))
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
                total["athletic.net already has it"] += 1
                if a.write:
                    A.noteLanded(cur, alive["alive_id"], 0, "anet has it")
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


def main(argv=None):
    ap = argparse.ArgumentParser(description="AthleticLIVE cross country -> meets / results")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ld = sub.add_parser("land")
    ld.add_argument("--limit", type=int, default=100)
    ld.add_argument("--meet", type=int, action="append", help="this athletic.net meet id")
    ld.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    return {"land": cmdLand}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
