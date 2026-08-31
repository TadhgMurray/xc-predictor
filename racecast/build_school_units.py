# Project: xc-predictor / racecast
# File:    build_school_units.py
# Purpose: Store what check_school_units measures. Pipeline step 10d.
#
#     python racecast/build_school_units.py            # write school_unit
#     python racecast/build_school_units.py --dry-run  # counts only
#
# ★ WHERE THE UNITS COME FROM. Schools do not publish their league or
#   section anywhere the feeds carry. What they DO carry is attendance:
#   every school runs its own championship series in the late season --
#   the LCD meet -- and the name of that meet says the unit. So the unit
#   is INFERRED FROM WHO SHOWS UP, season by season, and the parser that
#   reads those names lives in scripts/check_school_units.py, which this
#   file imports rather than copies. One parser, two consumers: the
#   census prints it, this stores it, and they cannot drift.
#
# ★ THE OWNER'S RULES, ENCODED:
#     - most recent season is the end-all. Older seasons are provenance;
#       `asof` says which season the verdict came from.
#     - a division can be LOST on the way up (state state/div, section
#       section/div, league), so section_div and state_div are separate
#       columns and never overwrite each other.
#     - college hierarchy is division -> region -> conference.
#
# ⚠ CONFLICT IS RECORDED, NOT RESOLVED. When the latest season itself
#   carries two corroborated units, the richest wins the column and
#   `conflict` is set true. A page can then decline to show a unit it
#   should not be confident about, and --conflicts on the census is the
#   list of them.

import argparse
import sys

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")
sys.path.insert(0, "racecast")

import psycopg2.extras                                  # noqa: E402

from database import getConn                            # noqa: E402
import check_school_units as C                          # noqa: E402
import school_unit_overrides as OV                      # noqa: E402

# HS kinds then college kinds. section_div/state_div stay apart on
# purpose -- see the owner's rule above.
_HS_KINDS = ("league", "section", "section_div", "district", "county",
             "region", "state", "state_div", "class")
_COLLEGE_KINDS = ("conference", "region", "division")
_COLS = ("league", "section", "section_div", "district", "county",
         "region", "state_unit", "state_div", "class",
         "conference", "division")

_DDL = """
DROP TABLE IF EXISTS school_unit;
CREATE TABLE school_unit (
    school      text    NOT NULL,
    state       text    NOT NULL,
    sport       text    NOT NULL,
    league      text,
    section     text,
    section_div text,
    district    text,
    county      text,
    region      text,
    state_unit  text,
    state_div   text,
    class       text,
    conference  text,
    division    text,
    is_college  boolean NOT NULL DEFAULT false,
    conflict    boolean NOT NULL DEFAULT false,
    votes       integer NOT NULL DEFAULT 0,
    asof        text,
    PRIMARY KEY (school, state, sport)
)"""


def _rowFor(school, st, kinds):
    """One school's stored verdict, or None when nothing survived."""
    cells, asof, conflict, votes = {}, "", False, 0
    college = any(k in ("conference", "division") for k in kinds)
    wanted = _COLLEGE_KINDS if college else _HS_KINDS
    for kind in wanted:
        got = C.current(kinds.get(kind))
        if not got:
            continue
        unit, yr, clash = got
        unit = OV.aliasFor(kind, unit)
        # "state" is a column name in its own right; the unit column is
        # state_unit so the schema keeps the school's own state distinct
        cells["state_unit" if kind == "state" else kind] = unit
        asof = max(asof, yr)
        conflict = conflict or clash
        votes += sum(kinds[kind].values())
    cells.update(OV.overrideFor(school, st))
    if not cells:
        return None
    return ([school, st] + [cells.get(c) for c in _COLS]
            + [college, conflict, votes, asof])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--sport", choices=("XC", "TF"))
    args = ap.parse_args()
    sports = (args.sport,) if args.sport else ("XC", "TF")

    with getConn() as conn, conn.cursor() as cur:
        rows, census = [], {}
        for sport in sports:
            votes, _seasons, _eg, (st_, _h, _m) = C.buildVotes(cur, sport)
            n_ok = n_clash = 0
            for (school, st), kinds in votes.items():
                row = _rowFor(school, st, kinds)
                if row is None:
                    continue
                rows.append(row[:2] + [sport] + row[2:])
                n_ok += 1
                n_clash += 1 if row[-3] else 0
            census[sport] = (n_ok, n_clash, st_)
            print(f"  {sport}: {n_ok:,} schools with a unit "
                  f"({n_clash:,} carry a latest-season conflict) "
                  f"from {st_['hits']:,} parsed rows")
        if args.dry_run:
            print("  DRY RUN -- nothing written.")
            return
        if not rows:
            print("  NOTHING PARSED -- school_unit left as it was. "
                  "(Run 10_rankings first: the level call reads "
                  "ranking_results.)")
            return
        cur.execute(_DDL)
        psycopg2.extras.execute_values(
            cur,
            "INSERT INTO school_unit (school, state, sport, " +
            ", ".join(_COLS) +
            ", is_college, conflict, votes, asof) VALUES %s "
            "ON CONFLICT (school, state, sport) DO NOTHING",
            rows, page_size=5000)
        # the pages look a school up by name, and by name+state
        cur.execute("CREATE INDEX idx_school_unit_school "
                    "ON school_unit (school)")

        # ★ AND THE RANKINGS FILTERS READ IT THE OTHER WAY ROUND.
        #   rankings._whereClauses does
        #       school IN (SELECT u.school FROM school_unit u
        #                  WHERE u."division" = ANY(%(division)s))
        #   which scans by UNIT and returns schools -- the opposite direction
        #   from idx_school_unit_school, which finds a unit given a school.
        #   Without these the subquery is a sequential scan of school_unit on
        #   every filtered board request.
        #
        # ! school IS THE SECOND COLUMN, so the index is covering: the planner
        #   answers the whole subquery from the index without touching the
        #   heap, which is the difference between fast and merely indexed.
        # ⚠ EVERY COLUMN rankings.UNIT_COLUMNS FILTERS ON, NOT JUST THE
        #   COLLEGE THREE. This loop used to list division/region/conference
        #   only, so the five HIGH SCHOOL units -- state_div, class, section,
        #   section_div, league -- had no index at all and every HS unit
        #   filter sequentially scanned school_unit on every board request.
        #   The HS units are the ones a high school user actually reaches for.
        #   Kept in step with UNIT_COLUMNS by hand; a column added there and
        #   missed here is a silent sequential scan, not an error.
        for _col in ("division", "region", "conference",
                     "state_div", "class", "section", "section_div",
                     "league"):
            cur.execute(f'CREATE INDEX idx_school_unit_{_col} '
                        f'ON school_unit ("{_col}", school)')
        conn.commit()
    print(f"  school_unit: {len(rows):,} rows written.")


if __name__ == "__main__":
    main()
