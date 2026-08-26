# engine/dump_overrides.py
#
# Turn every (meet_id, div_id) -> distance dict in corrections.py into the
# dist_override table the backfill reads.
#
# ★ ON A CONFLICT, THE PROPOSER WINS -- AND THAT IS THE OPPOSITE OF WHAT IT
#   LOOKS LIKE IT SHOULD BE.
#
#   The obvious precedence is "a human wrote it, so it outranks arithmetic".
#   The first two conflicts this file ever produced say otherwise:
#
#       (9640, 2)  stored 3200, hand-written 6000, proposed 5150
#       (9756, 1)  stored 3200, hand-written 6000, proposed 5000
#
#   Both are 5k races. The hand-written 6000s are wrong and the proposals are
#   right, because a proposal is only made when several OTHER divisions at
#   the same meet or course already race that distance -- it is corroborated
#   by the corpus, whereas the hand-written value is one person's recollection
#   from an unknown date.
#
# ⚠ SO THE CONFLICT IS STILL PRINTED, LOUDLY. Preferring the proposal is a
#   default, not a verdict: it is what happens when nobody looks. Every clash
#   is listed with all its values so somebody can, and the losing entry stays
#   in corrections.py rather than being edited away.
#
# ! AND ONE ROW PER DIVISION REACHES THE TABLE. The previous version detected
#   conflicts and then inserted every value anyway. dist_override has no
#   unique key, so backfill_normalize's LEFT JOIN would fan a division out
#   into two rows -- nondeterministic, and silently double-counting.
import sys, os

sys.path.insert(0, "engine"); sys.path.insert(0, "scripts")
import corrections
from database import getConn
from psycopg2.extras import execute_values

# Lower number wins. A name not listed here sorts after everything named,
# then alphabetically, so a new dict is merely last rather than invisible.
_PRECEDENCE = {
    "DIST_PROPOSED": 0,           # corroborated by other divisions
    "_DISTANCE_OVERRIDES_XC": 1,  # hand-written
}


def rank(name):
    return (_PRECEDENCE.get(name, len(_PRECEDENCE) + 1), name)


# Collect every candidate: {(meet_id, div_id): {dict_name: distance}}
found = {}
for name in dir(corrections):
    obj = getattr(corrections, name)
    if not isinstance(obj, dict):
        continue
    for k, v in obj.items():
        if (isinstance(k, tuple) and len(k) == 2
                and all(isinstance(x, int) for x in k)
                and isinstance(v, (int, float)) and v > 0):
            found.setdefault(k, {})[name] = float(v)

rows, clashes = [], []
for key, byname in found.items():
    # ! DISTINCT VALUES, NOT DISTINCT DICTS. The same distance written twice
    #   under two names is agreement, not a conflict, and there are 2.6 copies
    #   of the average key across corrections.py.
    if len({round(v, 3) for v in byname.values()}) > 1:
        clashes.append((key, byname))
    winner = min(byname, key=rank)
    rows.append((key[0], key[1], byname[winner], winner))

print(f"{len(found):,} divisions, {len(rows):,} written, "
      f"{len(clashes)} CONFLICTING")
for key, byname in sorted(clashes)[:40]:
    chosen = min(byname, key=rank)
    parts = ", ".join(f"{n}={v:g}" + (" <-" if n == chosen else "")
                      for n, v in sorted(byname.items(), key=lambda x: rank(x[0])))
    print(f"    {key}  {parts}")
if len(clashes) > 40:
    print(f"    ... and {len(clashes) - 40} more")

with getConn() as conn, conn.cursor() as cur:
    cur.execute("DROP TABLE IF EXISTS dist_override")
    cur.execute("""CREATE TABLE dist_override (
                       meet_id bigint, div_id bigint,
                       distance double precision, dict_name text,
                       -- ! THE KEY IS THE POINT. A duplicate is now a write
                       --   error here rather than a fan-out in the backfill.
                       PRIMARY KEY (meet_id, div_id))""")
    execute_values(cur, "INSERT INTO dist_override VALUES %s", rows,
                   page_size=5000)
    cur.execute("ANALYZE dist_override")

    # ★ THE DROPS TOO, so the SITE can say why a division has no ratings.
    #   backfill_normalize reads _DISTANCE_DROP straight from corrections;
    #   the race page cannot (importing a 1.45M-line module per request is
    #   not a thing), so the same set lands in a table and the page joins
    #   it to render "ratings withheld" instead of an unexplained dash
    #   column. Same lifecycle as dist_override: rebuilt on every dump.
    drop_rows = [(sport, m, d)
                 for sport, drops in corrections._DISTANCE_DROP_BY_SPORT.items()
                 for (m, d) in drops]
    cur.execute("DROP TABLE IF EXISTS dist_drop")
    cur.execute("""CREATE TABLE dist_drop (
                       sport text, meet_id bigint, div_id bigint,
                       PRIMARY KEY (sport, meet_id, div_id))""")
    if drop_rows:
        execute_values(cur, "INSERT INTO dist_drop VALUES %s", drop_rows,
                       page_size=5000)
    cur.execute("ANALYZE dist_drop")
    conn.commit()

print(f"wrote dist_override; dist_drop ({len(drop_rows):,} divisions)")