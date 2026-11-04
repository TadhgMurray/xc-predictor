#!/usr/bin/env python3
# Project: xc-predictor / racecast
# File:    build_qualified_fields.py
# Purpose: Work out, overnight, the field of every upcoming championship you
#          qualify for -- this season's qualifiers, predicting the rounds
#          that have not run -- and store it for the pages to read.
#
#     set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
#     $P -u racecast/build_qualified_fields.py                  # write
#     $P -u racecast/build_qualified_fields.py --dry-run        # say, write nothing
#
# ★ WHY (owner, 2026-11-03: "I think the new field option doesn't work ... the
#   individuals always seem to be the same"). A page may not run the model on
#   a championship's rounds (owner, same day: "you just slowed down the site
#   so much"), so a round not yet run counted only once its own forecast was
#   frozen the night before it -- and almost every championship fell back to
#   last year's teams. Here, off the request path, every round not run is
#   predicted (upcoming_preview.qualifiedFieldInfo live=True) and each race's
#   field is written to upcoming_preview.STORED_PATH, which the previews and
#   the predictions page read first.
#
# ★ WHICH MEETS: the predictions page's upcoming championships (last year's
#   meets a year on, upcoming_preview.upcomingChampionships) and every meet
#   posted for the next DAYS days that a published rule or a learned route
#   covers -- keyed as the pages ask: source, meet id, race, date. One
#   championship is worked out once however many keys ask for it (raceKey),
#   and the list itself is stored too (upcoming_preview.storedChampionships:
#   one of each, NXN with it), so the page asks the database nothing.
#
# ★ A ROUND NOT POSTED YET (2026-11-04: NCAA "no race with enough rounds")
#   is last season's round run this year (qualifying.pastRounds).
#
# ! OFF THE && CHAIN (nightly 13h2): a failure keeps last night's file; the
#   file is written beside and swapped, never half-written.
import argparse
import datetime
import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("racecast", "scripts", "engine", "model"):
    p = os.path.join(_ROOT, sub)
    if p not in sys.path:
        sys.path.insert(0, p)

DAYS = 45


def targets(cur, today, champs):
    """[plan] -- meetPlan's shape, dated the day the pages will ask with."""
    import last_edition as LE
    import qualifying as Q
    import upcoming_preview as U
    out = []
    for m in champs:
        plan = U.pastPlan(cur, m["meet_id"], m["source"], m["date"])
        if plan:
            out.append(plan)
    hi = (today + datetime.timedelta(days=DAYS)).isoformat()
    cur.execute("""SELECT 'tfrrs' AS src, meet_id, meet_name, date AS d FROM meets_tfrrs
                   WHERE sport = 'XC' AND date ~ '^(19|20)[0-9]{2}-' AND date > %s AND date <= %s
                   UNION
                   SELECT DISTINCT 'anet', meet_id, meet_name, meet_date FROM meets
                   WHERE meet_date ~ '^(19|20)[0-9]{2}-' AND meet_date > %s AND meet_date <= %s""",
                (today.isoformat(), hi, today.isoformat(), hi))
    routes = Q._loadRoutes()
    for row in cur.fetchall():
        row = tuple(row.values()) if isinstance(row, dict) else tuple(row)
        src, mid, name = row[0], int(row[1]), row[2]
        if Q.ruleFor(name, src) is None and (src, LE.editionName(name)) not in routes:
            continue
        plan = U.meetPlan(cur, mid, src)
        if plan and plan.get("date"):
            out.append(plan)
    return out


# ★ ONE CHAMPIONSHIP, WORKED OUT ONCE (owner's nightly, 2026-11-04: Brooks,
#   MPSSAA, OSAA, MSHSAA and NEIC each listed twice). The predictions page
#   asks for last year's meet a year on, a meet preview for this year's
#   posted meet: two keys, one field. A race is the championship
#   (upcoming_preview.championshipKey), the day, its gender and, for a
#   learned route (which goes by the race's label), the label.
def raceKey(plan, race):
    import last_edition as LE
    import upcoming_preview as U
    group = U.championshipKey(plan.get("name"), plan.get("source"))
    lab = "" if group.startswith("rule:") else LE.editionName(race.get("label"))
    return (group, str(plan["date"])[:10], race.get("gender"), lab)


def describe(why):
    """One line on why a race has no qualifier field. Pure."""
    if why.get("no"):
        return why["no"]
    if why.get("how") == "route":
        return (f"route ({why.get('gender') or '?'}): {why.get('rounds', 0)} of "
                f"{why.get('of', 0)} rounds found (needs 80%) -- this season "
                f"{why.get('this_season', 0)}, last season's {why.get('last_season', 0)}"
                f" ({why.get('last_season_ok', 0)} predicted)")
    if why.get("how"):
        return (f"{why['how']} ({why.get('gender') or 'no gender'}): {why.get('rounds', 0)} "
                f"rounds counted (needs 3) -- this season {why.get('this_season', 0)} found, "
                f"{why.get('counted', 0)} with a field; last season's "
                f"{why.get('last_season', 0)} ({why.get('last_season_ok', 0)} predicted)")
    return "no stored plan for the race"


def main():
    ap = argparse.ArgumentParser(description="Store the qualifier fields of upcoming championships.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--budget-minutes", type=float, default=90)
    a = ap.parse_args()
    t0 = time.time()
    deadline = t0 + 60 * a.budget_minutes
    today = datetime.date.today()

    import psycopg2.extras
    import upcoming_preview as U
    from database import getConn

    fields, n_races = {}, 0
    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SET statement_timeout = '900s'")
            champs = U.upcomingChampionships(cur, today, thorough=True)
            conn.rollback()
            print(f"  {len(champs)} upcoming championship(s) on the predictions page")
            plans = targets(cur, today, champs)
            conn.rollback()
            print(f"  {len(plans)} meet(s) to field: those, and the posted meets a rule "
                  f"or a learned route covers")
            done, whys = {}, {}
            for plan in plans:
                if time.time() > deadline:
                    print("  out of budget; the rest next run")
                    break
                got, nots = [], []
                for race in plan.get("races") or []:
                    n_races += 1
                    if not race.get("gender"):      # who ran it last year
                        race["gender"] = U.lastYearGender(cur, plan, race)
                        conn.rollback()
                    key = raceKey(plan, race)
                    try:
                        if key not in done:
                            why = {}
                            got_ = U.qualifiedFieldInfo(cur, plan, race["div_id"], live=True,
                                                        why=why)
                            done[key] = got_
                            whys[key] = why
                        field, info = done[key]
                    except Exception as exc:            # noqa: BLE001
                        conn.rollback()
                        done[key] = (None, None)
                        print(f"    {plan.get('name')} {race.get('label')}: "
                              f"{type(exc).__name__}: {exc}")
                        continue
                    conn.rollback()
                    if not field:
                        nots.append(f"{race.get('label')}: {describe(whys.get(key) or {})}")
                    if field:
                        fields[U.storedKey(plan["source"], plan["meet_id"], race["div_id"],
                                           plan["date"])] = {
                            "field": {str(k): v for k, v in field.items()}, "info": info,
                            "made": today.isoformat()}
                        got.append(f"{race.get('label')} {len(field)} "
                                   f"({info.get('predicted', 0)}/{info.get('rounds', 0)} predicted)")
                print(f"  {plan['date']}  {plan.get('name')}: "
                      + ("; ".join(got) if got else "no race with enough rounds"))
                # ★ AND WHY NOT, per race (2026-11-04: NCAA DII, DIII, NAIA
                #   "no race with enough rounds" and nothing to go on)
                for line in sorted(set(nots))[:6]:
                    print(f"        {line}")
    print(f"  {len(fields)} race field(s) of {n_races} race(s)"
          + ("" if not a.dry_run else " -- DRY RUN, nothing written"))
    if not a.dry_run:
        tmp = U.STORED_PATH + ".new"
        with open(tmp, "w") as fh:
            json.dump({"made": today.isoformat(), "fields": fields, "champs": champs}, fh)
        os.replace(tmp, U.STORED_PATH)
        print(f"  wrote {U.STORED_PATH}")
    print(f"  done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
