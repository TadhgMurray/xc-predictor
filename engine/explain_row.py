"""
explain_row.py -- why does THIS row have THAT rating?

    python engine/explain_row.py --meet "First to the Finish"
    python engine/explain_row.py --person 29346285
    python engine/explain_row.py --result 267013398 --sport TF

Run from the PROJECT ROOT. Reads only; nothing here writes.

★ THE QUESTION THIS ANSWERS IS "IDK ABOUT THIS ONE", and it answers it from
  the row's own stored numbers, with no pack, no solve and no pool means.

      speed_rating = pool_mean / (normalized_time / (1 + difficulty))

  so the rating is INVERSELY proportional to the stored normalized_time and
  nothing else in that expression depends on the distance. Recompute what
  normalizeTime WOULD have written for this row's own time and distance on
  the pool it is RATED in, and the ratio between that and what is stored is
  exactly the factor the rating is wrong by:

      rating_on_its_own_anchor = rating * (stored_nt / expected_nt)

  No pool mean is needed to say "this 159 is really a 97", which is why this
  runs against the database in a second instead of needing the engine.

★ AND THE DISTANCE IS SHOWN FROM EVERY SOURCE, because they disagree and the
  disagreement is usually the answer. dist_override beats the scraped column;
  the ranking build joins `meets` on (div_id, source) to mirror the engine
  while the website joins on (meet_id, div_id, source); track keeps its
  distance in the event name and has no meets distance at all. When those
  columns differ, the row was normalised with one of them and is being read
  with another.

⚠ A ROW WITH NO DISTANCE ANYWHERE CANNOT BE CHECKED, and this says so rather
  than reporting agreement. The anchor gate in build_ranking_results has the
  same blind spot -- an unanswerable question is not a pass -- which is why
  that build now prints how many rows it could not check.
"""

import sys
import argparse

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")

from normalize_distance import normalizeTime
from event_parse import distanceFromEventShort
from anchor_check import mismatch, whichPool, TOLERANCE, _POOLS


# The columns every explanation needs, per sport. XC keeps its distance in
# meets/dist_override; TF keeps it in the event name.
_SQL = {
    "XC": """
        SELECT r.result_id, r.person_id, r.date, r.school, r.grade,
               r.time_seconds, r.normalized_time, r.speed_rating,
               r.meet_id, r.div_id, r.source,
               dov.distance                       AS override_distance,
               m3.distance                        AS meets3_distance,
               m2.distance                        AS meets2_distance,
               -- ★ tfrrs KEEPS THE DISTANCE IN A JSON BLOB, keyed by the
               --   per-meet div_id as a STRING. audit_overrides reads the
               --   same field the same way; without it every college XC row
               --   here reported "no distance from any source", which is a
               --   false accusation rather than a finding.
               (mt.division_distances -> r.div_id::text ->> 'distance')::float
                                                  AS tfrrs_distance,
               COALESCE(m3.meet_name, m2.meet_name,
                        mt.meet_name, mt.venue_name)  AS meet_name,
               COALESCE(m3.course_name, m2.course_name,
                        mt.venue_name)                AS course_name,
               NULL::text                         AS event_short,
               k.pool                             AS rated_pool,
               (k.result_id IS NOT NULL)          AS on_the_boards
        FROM   results r
        LEFT   JOIN dist_override dov
                 ON dov.meet_id = r.meet_id AND dov.div_id = r.div_id
        -- Three keys: what the website joins on.
        LEFT   JOIN meets m3
                 ON m3.meet_id = r.meet_id AND m3.div_id = r.div_id
                AND m3.source = r.source
        -- Two keys: what the ranking build and the engine join on. Shown
        -- beside the other so a disagreement is visible rather than assumed
        -- away.
        LEFT   JOIN LATERAL (
                   SELECT distance, meet_name, course_name FROM meets
                   WHERE  div_id = r.div_id AND source = r.source
                   LIMIT  1
               ) m2 ON TRUE
        -- The tfrrs side, guarded in the ON clause so an anet row keeps its
        -- own `meets` data and simply never matches here.
        LEFT   JOIN meets_tfrrs mt
                 ON r.source = 'tfrrs' AND mt.meet_id = r.meet_id
                AND mt.sport = 'XC'
        LEFT   JOIN ranking_results k
                 ON k.result_id = r.result_id AND k.sport = 'XC'
        WHERE  {where}
        ORDER  BY r.speed_rating DESC NULLS LAST
        LIMIT  %(limit)s
    """,
    "TF": """
        SELECT r.result_id, r.person_id, r.date, r.school, r.grade,
               r.time_seconds, r.normalized_time, r.speed_rating,
               r.meet_id, r.div_id, r.source,
               dov.distance              AS override_distance,
               NULL::real                AS meets3_distance,
               NULL::real                AS meets2_distance,
               NULL::float               AS tfrrs_distance,
               mt.meet_name              AS meet_name,
               NULL::text                AS course_name,
               r.event_short             AS event_short,
               k.pool                    AS rated_pool,
               (k.result_id IS NOT NULL) AS on_the_boards
        FROM   results_tf r
        LEFT   JOIN dist_override dov
                 ON dov.meet_id = r.meet_id AND dov.div_id = r.div_id
        LEFT   JOIN LATERAL (
                   SELECT meet_name FROM meets_tf
                   WHERE  meet_id = r.meet_id AND div_id = r.div_id
                     AND  source = r.source
                   LIMIT  1
               ) mt ON TRUE
        LEFT   JOIN ranking_results k
                 ON k.result_id = r.result_id AND k.sport = 'TF'
        WHERE  {where}
        ORDER  BY r.speed_rating DESC NULLS LAST
        LIMIT  %(limit)s
    """,
}

# A meet is named in `meets`, which the results table does not carry -- so a
# name search resolves to meet_ids first and then explains those rows.
_FIND_MEET = {
    # ⚠ `meets` IS ANET-ONLY, AND EVERY COLLEGE MEET IS tfrrs. Searching it
    #   alone reported "No XC meet matches 'Stockton University'" for a race
    #   with 162 finishers, and matched four Illinois middle-school meets for
    #   "Bengal Invite" while missing the Idaho State one entirely. Both
    #   tables, or the tool answers about the wrong sport of racing.
    #
    # ! venue_name TOO. meets_tfrrs.meet_name is sometimes the venue and
    #   venue_name is sometimes the meet -- app.py's own comment says the
    #   column is sparse and confused -- so a name search that reads one of
    #   them finds half of what it should.
    "XC": """
        SELECT meet_id, meet_name, state, count(*) OVER () AS n FROM (
            SELECT DISTINCT m.meet_id, m.meet_name, m.state
            FROM   meets m
            WHERE  m.meet_name ILIKE %(name)s
            UNION
            SELECT DISTINCT mt.meet_id,
                   COALESCE(mt.meet_name, mt.venue_name) AS meet_name,
                   NULL::text AS state
            FROM   meets_tfrrs mt
            WHERE  mt.sport = 'XC'
              AND  (mt.meet_name ILIKE %(name)s OR mt.venue_name ILIKE %(name)s)
        -- ! NOT `both`: it is a reserved word (TRIM(BOTH ...)) and Postgres
        --   rejects it as an alias.
        ) hits
        LIMIT  20
    """,
    "TF": """
        SELECT DISTINCT m.meet_id, m.meet_name, NULL AS state,
               count(*) OVER () AS n
        FROM   meets_tf m
        WHERE  m.meet_name ILIKE %(name)s
        LIMIT  20
    """,
}


def distances(row):
    """Every distance this row could have been normalised with, named.

    ★ IN THE RANKING BUILD'S OWN ORDER OF PRECEDENCE, so the first entry is
      the one the site actually used. dist_override wins, then the scraped
      column; on track there is no scraped column and the event name carries
      it.
    """
    out = []
    if row["override_distance"]:
        out.append(("dist_override", float(row["override_distance"])))
    if row["meets2_distance"]:
        out.append(("meets (div+source, what the build joins on)",
                    float(row["meets2_distance"])))
    if row["meets3_distance"]:
        out.append(("meets (meet+div+source, what the website joins on)",
                    float(row["meets3_distance"])))
    if row.get("tfrrs_distance"):
        out.append(("meets_tfrrs.division_distances",
                    float(row["tfrrs_distance"])))
    if row["event_short"]:
        got = distanceFromEventShort(row["event_short"])
        metres = got[0] if isinstance(got, (tuple, list)) else got
        if metres:
            out.append((f"event name {row['event_short']!r}", float(metres)))
    return out


def joinDisagreement(row):
    """The two `meets` joins, when they do not agree. None when they do.

    ⚠ THE TWO-KEY JOIN CAN MATCH ANOTHER MEET ENTIRELY. The ranking build
      joins meets ON (div_id, source) to mirror the engine, while the website
      joins ON (meet_id, div_id, source). Those are the same row only while
      div_id is unique across meets -- and where it is not, the build reads
      some other meet's distance for this row, normalises against it, and the
      anchor gate then checks a number that was never this race's.

      This is not hypothetical: a fixture with two meets sharing div_id 1
      handed the second meet's rows the first meet's 4828m. Whether it
      happens in the corpus is a question about the data, which is why this
      prints both rather than deciding.
    """
    two, three = row["meets2_distance"], row["meets3_distance"]
    if two is None or three is None or abs(float(two) - float(three)) < 1.0:
        return None
    return float(two), float(three)


def explain(row, sport):
    """One row -> the lines to print about it."""
    lines = []
    who = f"person {row['person_id']}  {row['school'] or '?'}"
    lines.append(f"\n  result {row['result_id']}   {row['date']}   {who}")
    lines.append(f"    meet    {(row['meet_name'] or '?')[:52]}"
                 f"   (meet {row['meet_id']} / div {row['div_id']})")
    if row["course_name"]:
        lines.append(f"    course  {row['course_name'][:52]}")

    t = row["time_seconds"]
    lines.append(f"    time    {t:.1f}s"
                 f"   stored normalized_time {row['normalized_time'] or 0:.1f}s"
                 f"   rating {row['speed_rating'] or 0:.1f}")
    lines.append(f"    pool    rated {row['rated_pool'] or '(not ranked)'}"
                 f"   on the boards: "
                 f"{'yes' if row['on_the_boards'] else 'NO'}")

    found = distances(row)
    clash = joinDisagreement(row)
    if not found:
        lines.append("    distance  NONE FROM ANY SOURCE -- the anchor gate "
                     "cannot check this row, so it is published unchecked")
        return lines
    for name, metres in found:
        lines.append(f"    distance  {metres:>7.0f}m  from {name}")
    if clash:
        lines.append(f"    ⚠ THE TWO meets JOINS DISAGREE: {clash[0]:.0f}m on "
                     f"(div, source) against {clash[1]:.0f}m on "
                     f"(meet, div, source).")
        lines.append("      div_id is not unique across meets here, so the "
                     "build read another meet's distance for this row.")

    pool = row["rated_pool"]
    if not pool:
        lines.append("    (no rated pool: this row is not on a board, so "
                     "there is nothing to check it against)")
        return lines

    # The distance the build itself would use, in its own order of precedence.
    metres = found[0][1]
    is_bad, expected, ratio = mismatch(t, metres, row["normalized_time"],
                                       pool, sport)
    if ratio is None:
        lines.append("    (normalizeTime returned nothing for this row)")
        return lines

    corrected = (row["speed_rating"] or 0) * ratio
    lines.append(f"    check   normalizeTime({t:.1f}s, {metres:.0f}m, {pool}, "
                 f"{sport}) = {expected:.1f}s")
    lines.append(f"            stored is {row['normalized_time']:.1f}s, "
                 f"{ratio - 1:+.1%} against that")
    if is_bad:
        was, off = whichPool(t, metres, row["normalized_time"], _POOLS, sport)
        lines.append(f"    ⚠ MIS-ANCHORED. The stored value is reproduced by "
                     f"{was} (off by {off:.1%}), not by {pool}.")
        lines.append(f"      On its own pool's anchor this race rates "
                     f"{corrected:.1f}, not {row['speed_rating']:.1f}.")
    else:
        lines.append(f"    ok      within {TOLERANCE:.0%}: both stages used "
                     f"the same scale, and the rating is what the engine "
                     f"measured")
    return lines


def main():
    ap = argparse.ArgumentParser(
        description="Explain one row's rating from its own stored numbers.")
    ap.add_argument("--sport", choices=["XC", "TF"], default="XC")
    ap.add_argument("--person", type=int)
    ap.add_argument("--result", type=int)
    ap.add_argument("--meet", help="meet NAME, or part of one")
    ap.add_argument("--limit", type=int, default=12)
    args = ap.parse_args()

    if not (args.person or args.result or args.meet):
        ap.error("give --person, --result or --meet")

    import psycopg2.extras
    from database import getConn

    where, params = [], {"limit": args.limit}
    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if args.meet:
                cur.execute(_FIND_MEET[args.sport], {"name": f"%{args.meet}%"})
                found = cur.fetchall()
                if not found:
                    print(f"\nNo {args.sport} meet matches {args.meet!r}.")
                    return
                print(f"\nMEETS MATCHING {args.meet!r}")
                for m in found:
                    print(f"    {m['meet_id']:>10}  {m['state'] or '  '}  "
                          f"{(m['meet_name'] or '')[:56]}")
                where.append("r.meet_id = ANY(%(meets)s)")
                params["meets"] = [m["meet_id"] for m in found]
            if args.person:
                where.append("r.person_id = %(person)s")
                params["person"] = args.person
            if args.result:
                where.append("r.result_id = %(result)s")
                params["result"] = args.result

            sql = _SQL[args.sport].format(where=" AND ".join(where))
            cur.execute(sql, params)
            rows = cur.fetchall()

    if not rows:
        print("\nNothing matched.")
        return
    print(f"\nTHE {len(rows)} HIGHEST-RATED MATCHING ROWS "
          f"({args.sport}, tolerance {TOLERANCE:.0%})")
    for row in rows:
        print("\n".join(explain(row, args.sport)))

    print("\n  ⚠ A row marked mis-anchored is rated on a scale it was never\n"
          "    measured on. The fix is upstream -- the backfill and the solve\n"
          "    must agree about the pool -- not a distance override.")


if __name__ == "__main__":
    main()
