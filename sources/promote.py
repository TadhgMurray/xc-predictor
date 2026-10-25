# Project: xc-predictor
# File:    sources/promote.py
# Purpose: Write the landed timer races nobody else has into the tables the
#          pipeline reads -- the same shape an approved upload takes
#          (racecast/uploads.py applyUpload), with source 'timer':
#            cross country  meets (one row per race: distance, division)
#                           + results
#            track          meets_tf_meta (the meet) + meets_tf (one row per
#                           race) + results_tf
#          ids: meet_id -(700,000,000 + ext_meet_id), div_id meet*1000 - race,
#          result_id a negative content hash (sources/land.py). No person_id:
#          04a pass 4 (scripts/link_uploads.py --source timer) links them.
#
# ★ ONLY WHAT DEDUP CLEARED. A meet is promoted once sources/dedup.py has
#   judged it 'new' or 'partial', and then only its races that are not a
#   feed's twin. A 'twin' meet is never written: the feed's copy is the one
#   rated, and the timer's distance waits in races_ext beside it.
#
# ! PROMOTED IS FINAL. A page that changes afterwards is re-landed only as a
#   report (land.landMeet returns 'kept'): the promoted rows may already
#   carry person links, and nothing here rewrites a linked row.

import datetime

from sources.land import SOURCE, divIdFor, meetIdFor, mintResultId

PROMOTABLE = ("new", "partial")


def _slug(source):
    return source.split(":", 1)[1] if ":" in (source or "") else source


def plan(cur, ext):
    """(meet dict, [race dict], {race_n: [row dict]}) for one landed meet --
    the races dedup did not call twins."""
    cur.execute("""SELECT ext_meet_id, source, meet_name, meet_date, venue, place, state
                   FROM meets_ext WHERE ext_meet_id = %s""", (ext,))
    m = dict(zip(("ext", "source", "name", "date", "venue", "place", "state"), cur.fetchone()))
    cur.execute("""SELECT race_n, sport, title, gender, division, meters_stated FROM races_ext
                   WHERE ext_meet_id = %s AND twin_source IS NULL ORDER BY race_n""", (ext,))
    races = [dict(zip(("n", "sport", "title", "gender", "division", "meters"), r)) for r in cur.fetchall()]
    cur.execute("""SELECT ext_result_id, race_n, n, athlete_name, grade, school, place, time_seconds,
                          status FROM results_ext WHERE ext_meet_id = %s ORDER BY race_n, n""", (ext,))
    rows = {}
    for r in cur.fetchall():
        d = dict(zip(("id", "race_n", "n", "name", "grade", "school", "place", "ts", "status"), r))
        rows.setdefault(d["race_n"], []).append(d)
    return m, races, rows


def promoteMeet(cur, ext, now=None):
    """Write one cleared meet. The caller owns the transaction. -> counts."""
    from psycopg2.extras import execute_values
    now = now or datetime.datetime.now(datetime.timezone.utc)
    m, races, rows = plan(cur, ext)
    meet_id = meetIdFor(ext)
    slug = _slug(m["source"])
    out = {"meet_id": meet_id, "XC": 0, "TF": 0, "races": 0}
    xc = [r for r in races if r["sport"] == "XC" and rows.get(r["n"])]
    tf = [r for r in races if r["sport"] == "TF" and rows.get(r["n"])]
    link = []                       # (ext_result_id, result_id)

    def rid(r, race):
        return mintResultId(m["source"], ext, race["n"], r["n"], r["name"], r["ts"], r["place"])

    if xc:
        execute_values(cur, """
            INSERT INTO meets (div_id, meet_id, meet_name, meet_date, course_name, distance, state,
                               division, source, id_system, native_id)
            VALUES %s ON CONFLICT (div_id) DO NOTHING""",
                       [(divIdFor(ext, r["n"]), meet_id, m["name"], m["date"], m["venue"] or m["place"],
                         r["meters"], m["state"], r["division"], SOURCE, slug, ext) for r in xc])
        out["races"] += len(xc)
        res = []
        for race in xc:
            for r in rows[race["n"]]:
                res.append((rid(r, race), None, None, SOURCE, slug, r["id"], r["name"], meet_id,
                            divIdFor(ext, race["n"]), r["ts"], r["grade"] or "", m["date"],
                            r["school"] or "Unknown", SOURCE if r["school"] else None, r["place"], now,
                            r["status"]))
                link.append((r["id"], res[-1][0]))
        execute_values(cur, """
            INSERT INTO results (result_id, athlete_id, person_id, source, id_system, native_id,
                                 athlete_name, meet_id, div_id, time_seconds, grade, date,
                                 school, school_source, place, scraped_at, status)
            VALUES %s ON CONFLICT (result_id) DO NOTHING""", res, page_size=2000)
        out["XC"] = cur.rowcount
        _note(cur, "XC", res, meet_id, m["date"], now)
    if tf:
        cur.execute("""
            INSERT INTO meets_tf_meta (meet_id, meet_name, meet_date, venue_name, state, source,
                                       id_system, native_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (meet_id) DO NOTHING""",
                    (meet_id, m["name"], m["date"], m["venue"] or m["place"], m["state"], SOURCE, slug, ext))
        execute_values(cur, """
            INSERT INTO meets_tf (div_id, meet_id, meet_name, venue_name, event_short, event_id,
                                  distance_meters, state, is_indoor, division, source, id_system)
            VALUES %s ON CONFLICT DO NOTHING""",
                       [(divIdFor(ext, r["n"]), meet_id, m["name"], m["venue"] or m["place"],
                         (r["title"] or "")[:200], r["n"], r["meters"], m["state"], 0, r["division"],
                         SOURCE, slug) for r in tf])
        out["races"] += len(tf)
        res = []
        for race in tf:
            title = (race["division"] or race["title"] or "")[:200]
            for r in rows[race["n"]]:
                res.append((rid(r, race), None, None, SOURCE, slug, r["id"], r["name"], meet_id,
                            divIdFor(ext, race["n"]), race["n"], title, r["ts"], "running", 0,
                            r["grade"] or "", m["date"], 0, r["school"] or "Unknown", r["place"], now,
                            r["status"]))
                link.append((r["id"], res[-1][0]))
        execute_values(cur, """
            INSERT INTO results_tf (result_id, athlete_id, person_id, source, id_system, native_id,
                                    athlete_name, meet_id, div_id, event_id, event_short,
                                    time_seconds, result_kind, is_field, grade, date, is_relay,
                                    school, place, scraped_at, status)
            VALUES %s ON CONFLICT (result_id) DO NOTHING""", res, page_size=2000)
        out["TF"] = cur.rowcount
        _note(cur, "TF", res, meet_id, m["date"], now)
    if link:
        execute_values(cur, """UPDATE results_ext x SET result_id = v.r::bigint
                               FROM (VALUES %s) AS v(e, r) WHERE x.ext_result_id = v.e::bigint""", link)
    cur.execute("""UPDATE meets_ext SET status = 'promoted', live_meet_id = %s, promoted_at = now()
                   WHERE ext_meet_id = %s""", (meet_id, ext))
    return out


def _note(cur, sport, res, meet_id, date, now):
    """New results for What's new and Activity (database.noteScraped), as an
    approved upload does. Best effort."""
    try:
        from database import noteScraped
    except Exception:                                    # noqa: BLE001
        return
    noteScraped(cur.connection, sport, ((SOURCE, meet_id, date, now) for _ in res))


def pending(cur, limit=None):
    cur.execute("SELECT ext_meet_id, meet_date, meet_name, source, status FROM meets_ext "
                "WHERE status = ANY(%s) ORDER BY meet_date, ext_meet_id"
                + (f" LIMIT {int(limit)}" if limit else ""), (list(PROMOTABLE),))
    return cur.fetchall()


def run(conn, apply=False, limit=None, show=20):
    """Promote every cleared meet. Without apply: what would be written."""
    with conn.cursor() as cur:
        todo = pending(cur, limit)
        if not todo:
            print("[promote] nothing cleared to promote")
            conn.rollback()
            return {}
        total = {"meets": 0, "races": 0, "XC": 0, "TF": 0}
        for i, (ext, date, name, source, status) in enumerate(todo):
            if apply:
                out = promoteMeet(cur, ext)
            else:
                _m, races, rows = plan(cur, ext)
                out = {"races": len([r for r in races if rows.get(r["n"])]),
                       "XC": sum(len(rows.get(r["n"], [])) for r in races if r["sport"] == "XC"),
                       "TF": sum(len(rows.get(r["n"], [])) for r in races if r["sport"] == "TF")}
            total["meets"] += 1
            for k in ("races", "XC", "TF"):
                total[k] += out[k]
            if i < show:
                print(f"[promote] {status:<8} {str(date)[:10]} {source:<28} {(name or '')[:48]:<48} "
                      f"{out['races']} races, XC {out['XC']} TF {out['TF']} rows")
        if apply:
            conn.commit()
        else:
            conn.rollback()
    print(f"[promote] {total}" + ("" if apply else "  (dry run: nothing written; --apply to write)"))
    return total
