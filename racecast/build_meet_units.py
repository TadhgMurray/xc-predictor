"""
build_meet_units.py -- which meets are championships, and of what.

    python racecast/build_meet_units.py            # rebuild meet_unit
    python racecast/build_meet_units.py --dry-run  # count, write nothing

Pipeline step 10e (after the school units). Issue 34: /meets can filter to
championships, and to the championships of one unit -- "NCS", "EBAL",
"SEC" -- and the site search finds units.

★ ONE PARSER, TWO READERS. scripts/check_school_units.parseUnits already
  reads a meet name and race title into (kind, unit) facts to infer what
  unit a SCHOOL is in. This runs the same parser over every meet and
  stores the facts against the MEET, so the two can never disagree about
  what "NCS Tri-Valley Area Championships" is.

  meet_unit (sport, meet_id, source, kind, unit, state)
    one row  kind = 'championship', unit NULL   for every championship meet
    + a row  per parsed (kind, unit)           for the ones the parser reads

! A championship is what the parser's own gate says it is: the name
  matches _CHAMP_RX and not _NEVER_RX (invitationals, festivals, Nike,
  Foot Locker ... are not championships however they are named).
"""
import argparse
import sys

import psycopg2.extras

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")
from database import getConn
from dbfast import swapTable                                   # noqa: E402
import check_school_units as CU                                # noqa: E402
import school_unit_overrides as OV                             # noqa: E402

_DDL = """
CREATE TABLE meet_unit_new (
    sport   text   NOT NULL,
    meet_id bigint NOT NULL,
    source  text,
    kind    text   NOT NULL,
    unit    text,
    state   text
);
"""


def isChampionship(meet_name, state=None):
    name = (meet_name or "").strip()
    if not name or (state or "").upper() in CU._FOREIGN_ST:
        return False
    return bool(CU._CHAMP_RX.search(name)) and not CU._NEVER_RX.search(name)


def unitsForMeet(meet_name, div_title, feed, state, college=None):
    """(is_champ, [(kind, unit)]) for one meet + race title.

    college: True/False when known; None infers it the way the school
    census does -- a tfrrs meet with a college-shaped name."""
    if not isChampionship(meet_name, state):
        return False, []
    st = (state or "").upper() or None
    if college is None:
        college = (feed == "tfrrs" and CU._isCollegeName(meet_name or ""))
    facts = []
    for kind, unit in CU.parseUnits(meet_name, div_title, college=college,
                                    state=st):
        if kind == "state" and unit == "STATE":
            unit = st or "STATE"
        # the same alias merge the school writer applies (NORTH COAST ->
        # NCS, SOUTHEASTERN -> SEC), so a unit filter matches both feeds
        unit = OV.aliasFor(kind, unit) if unit else unit
        if unit and (kind, unit) not in facts:
            facts.append((kind, unit))
    return True, facts


def _meetRows(cur, sport):
    """DISTINCT (meet_id, meet_name, division, state, source) per sport,
    both feeds."""
    if sport == "XC":
        cur.execute("""
            SELECT DISTINCT meet_id, meet_name, division, state, source
            FROM   meets
            WHERE  meet_name IS NOT NULL
        """)
        yield from cur.fetchall()
        cur.execute("""
            SELECT DISTINCT meet_id, meet_name, NULL::text AS division,
                   NULL::text AS state, 'tfrrs'::text AS source
            FROM   meets_tfrrs
            WHERE  sport = 'XC' AND meet_name IS NOT NULL
        """)
        yield from cur.fetchall()
    else:
        cur.execute("""
            SELECT DISTINCT meet_id, meet_name, division, state, source
            FROM   meets_tf
            WHERE  meet_name IS NOT NULL
        """)
        yield from cur.fetchall()


def build(conn, dry_run=False):
    rows = []
    stats = {"XC": [0, 0], "TF": [0, 0]}
    with conn.cursor() as cur:
        for sport in ("XC", "TF"):
            seen = set()
            for meet_id, name, division, state, source in _meetRows(cur, sport):
                champ, facts = unitsForMeet(name, division, source, state)
                if not champ:
                    continue
                key = (sport, meet_id)
                if key not in seen:
                    seen.add(key)
                    rows.append((sport, meet_id, source, "championship",
                                 None, state))
                    stats[sport][0] += 1
                for kind, unit in facts:
                    fk = (sport, meet_id, kind, unit)
                    if fk in seen:
                        continue
                    seen.add(fk)
                    rows.append((sport, meet_id, source, kind, unit, state))
                    stats[sport][1] += 1
    for sport, (n_champ, n_units) in stats.items():
        print(f"  [{sport}] {n_champ:,} championship meets, "
              f"{n_units:,} unit facts")
    if dry_run:
        print("  --dry-run: nothing written")
        return
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS meet_unit_new")
        cur.execute(_DDL)
        psycopg2.extras.execute_values(
            cur, "INSERT INTO meet_unit_new VALUES %s", rows, page_size=5000)
        cur.execute("CREATE INDEX ON meet_unit_new (sport, meet_id)")
        cur.execute("CREATE INDEX ON meet_unit_new (unit)")
        cur.execute("CREATE INDEX ON meet_unit_new (kind, unit)")
    conn.commit()
    # readers see the old table or the new one, and wait for neither
    swapTable(conn, "meet_unit")
    print(f"  meet_unit: {len(rows):,} rows")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    with getConn() as conn:
        build(conn, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
