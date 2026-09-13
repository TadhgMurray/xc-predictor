#!/usr/bin/env python3
"""
anet_units.py -- what anet's unit ids MEAN, learned from the units we
already infer. Reads anet_division and school_unit; writes a report, and
optionally a proposals table. Never touches school_unit.

    python scripts/anet_units.py --report
    python scripts/anet_units.py --write        # + anet_unit_map, anet_unit_gap

★ WE DO NOT USE ANET'S NAMES. They are truncated in a way that loses the
  answer -- "North Coast" for NCS, "Valley" for Tri-Valley Area, "East Bay
  Ath." for EBAL -- and there is no competitive division in them at all.
  What anet has that we do not is an EXACT id per unit (`b`, which looks
  stable across seasons while `id` is re-allocated).

  So: take the id, throw away the name, and learn the name from ourselves.
  If 200 schools carry anet b=337 and school_unit says league='EBAL' for
  190 of them, then b=337 IS league EBAL -- named once, by majority vote,
  from units we already trust. Nothing has to be typed by hand, and a unit
  the inference has never seen simply stays unnamed.

★ AND THEN IT CUTS BOTH WAYS. With b -> (column, value) known:
    - a school whose school_unit DISAGREES with its anet id is a flag --
      exactly the kind of wrong-league bug that is invisible otherwise;
    - a school with an anet id and NO value in that column is a gap anet
      can fill, which is where the attendance inference has nothing
      because the school never went to a league championship.

⚠ WHAT IT CANNOT DO. It only ever proposes; nothing here writes to
  school_unit. And a unit the inference has never seen anywhere stays
  unnamed -- there is nothing to learn a name from.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))

# ★ ANET DOES CARRY THE COMPETITIVE DIVISION -- IN SOME STATES (2026-09-13).
#   The first version excluded state_div, section_div and class on the
#   strength of one California team, whose path is US > HS > California >
#   North Coast > Valley > East Bay Ath. with no division anywhere. But the
#   first real run named b=197 "4A" and b=698 "6A" and b=1694 "Division 1"
#   -- Washington, Oregon and Michigan put the class or the division IN the
#   tree. With no division column to match, each of those fell back to its
#   parent and came out as state_unit, which is simply wrong.
#
#   So every column anet's hierarchy can speak to, and it can speak to more
#   than California suggested. What it still never carries is a section's
#   OWN division where the state does not put one in the path.
COLUMNS = ("league", "area", "district", "county", "section", "section_div",
           "region", "state_unit", "state_div", "class", "conference",
           "division")

# widest to narrowest, HS then college. Only used to break a tie: when two
# units contain exactly the same schools in our data they are the same
# partition as far as we can see, and anet's own depth is then the only
# thing that says which of them the id is.
# widest to narrowest, in the order school_units.py already draws the chips
# ("CA D2, NCS D2, Tri-Valley, EBAL"), HS then college.
WIDTH = ("state_unit", "state_div", "class", "division", "region", "section",
         "section_div", "county", "district", "conference", "area", "league")

MIN_SUPPORT = 5        # schools carrying the id before it can be named
MIN_MATCH = 0.55       # ...and how well the two sets of schools coincide

MAP_DDL = """
CREATE TABLE IF NOT EXISTS anet_unit_map (
    base_id  int  NOT NULL,
    sport    text NOT NULL,
    col      text NOT NULL,
    value    text NOT NULL,
    anet_name text,
    support  int  NOT NULL,
    match    real NOT NULL,
    depth    int,
    ambiguous boolean NOT NULL DEFAULT false,
    PRIMARY KEY (base_id, sport))
"""

GAP_DDL = """
CREATE TABLE IF NOT EXISTS anet_unit_gap (
    school text NOT NULL, state text NOT NULL, sport text NOT NULL,
    col    text NOT NULL, value text NOT NULL, base_id int NOT NULL,
    PRIMARY KEY (school, state, sport, col))
"""


def rows(cur):
    """(base_id, sport, depth, anet_name, school, state, unit-row) for every
    school we have both an anet id and an inferred unit for."""
    cur.execute(f"""
        SELECT d.base_id, d.sport, d.depth, d.name AS anet_name,
               t.school, t.state, {', '.join('u.' + c for c in COLUMNS)}
        FROM   anet_division d
        JOIN   anet_team t ON t.team_id = d.team_id
        LEFT   JOIN school_unit u
               ON u.school = t.school AND u.state = t.state AND u.sport = upper(d.sport)
        WHERE  d.depth >= 2                 -- skip "United States" / "High School"
    """)
    out = []
    for r in cur.fetchall():
        if isinstance(r, dict):
            head = (r["base_id"], r["sport"], r["depth"], r["anet_name"],
                    r["school"], r["state"])
            units = {c: r.get(c) for c in COLUMNS}
        else:
            head, units = tuple(r[:6]), dict(zip(COLUMNS, r[6:]))
        out.append(head + (units,))
    return out


def learn(observed, min_support=MIN_SUPPORT, min_match=MIN_MATCH):
    """{(base_id, sport): (col, value, support, match, anet_name, depth,
    ambiguous)}.

    ⚠ A PLAIN MAJORITY VOTE NAMES EVERY LEAGUE AFTER ITS SECTION, and this
      is not hypothetical -- it is what the first version did. Every EBAL
      school is also an NCS school, so section='NCS' wins the vote inside
      b=337 (the league) as easily as inside b=319 (the section). Purity
      alone cannot tell a unit from its parent.

      What separates them is the OTHER direction: nearly every school in
      the EBAL carries b=337, while only a fraction of NCS schools do. So
      the score is the overlap of the two sets of schools both ways --
      |id AND value| / |id OR value| -- and the most specific unit that
      actually coincides with the id wins.

    An id whose schools scatter is left unnamed: that means the inference
    disagrees with itself there, which is not a thing to encode."""
    # one entry per school, however many ids it carries
    units_of, ids, val_schools = {}, {}, {}
    for base, sport, depth, anet_name, school, state, units in observed:
        who = (sport, school, state)
        units_of[who] = units
        ids.setdefault((base, sport), {"who": set(), "name": anet_name,
                                       "depth": depth})["who"].add(who)
    for who, units in units_of.items():
        for col in COLUMNS:
            v = (units.get(col) or "").strip()
            if v:
                val_schools.setdefault((who[0], col, v), set()).add(who)

    # score every candidate for every id, keeping the whole tied set
    tiedFor = {}
    for key, got in ids.items():
        mine, scored = got["who"], []
        candidates = {(col, (units_of[w].get(col) or "").strip())
                      for w in mine for col in COLUMNS
                      if (units_of[w].get(col) or "").strip()}
        for col, value in candidates:
            theirs = val_schools.get((key[1], col, value), set())
            both = len(mine & theirs)
            scored.append((both / float(len(mine | theirs) or 1), both, col, value))
        if scored:
            top = max(scored)[:2]
            tiedFor[key] = sorted((c for c in scored if c[:2] == top),
                                  key=lambda c: WIDTH.index(c[2])
                                  if c[2] in WIDTH else 99)

    # ★ A TIE MEANS A PARENT WITH EXACTLY ONE CHILD. "Marin the area" and
    #   "MCAL the league" hold the same schools, so nothing in the data
    #   separates them -- but anet's own DEPTH does, and the ids that share
    #   a school set are the rungs of one path. Order those by depth and
    #   hand them the tied candidates widest-first, so the area gets the
    #   area and the league gets the league.
    byWho = {}
    for key, got in ids.items():
        byWho.setdefault(frozenset(got["who"]), []).append(key)

    out = {}
    for who, keys in byWho.items():
        for rung, key in enumerate(sorted(keys, key=lambda k: ids[k]["depth"])):
            tied = tiedFor.get(key)
            if not tied:
                continue
            match, both, col, value = tied[min(rung, len(tied) - 1)]
            if both >= min_support and match >= min_match:
                out[key] = (col, value, both, match, ids[key]["name"],
                            ids[key]["depth"], len(tied) > 1)
    return out


def disagreements(observed, learned):
    """[(school, state, sport, col, ours, anet_says)] -- a school whose own
    unit contradicts the id it carries. The interesting output."""
    out = []
    for base, sport, _d, _n, school, state, units in observed:
        got = learned.get((base, sport))
        if not got:
            continue
        col, value = got[0], got[1]
        ours = (units.get(col) or "").strip()
        if ours and ours != value:
            out.append((school, state, sport, col, ours, value))
    return out


def gaps(observed, learned):
    """[(school, state, sport, col, value, base_id)] -- a column anet can
    fill because the inference has nothing there."""
    out = []
    for base, sport, _d, _n, school, state, units in observed:
        got = learned.get((base, sport))
        if not got:
            continue
        col, value = got[0], got[1]
        if not (units.get(col) or "").strip():
            out.append((school, state, sport, col, value, base))
    return out


def report(observed, learned, dis, gap):
    n_ids = len({(b, s) for b, s, *_ in observed})
    agree = sum(1 for b, s, _d, _n, _sc, _st, u in observed
                if (b, s) in learned
                and (u.get(learned[(b, s)][0]) or "").strip() == learned[(b, s)][1])
    out = [f"  {len(observed):,} (school, anet id) pairs over {n_ids:,} ids",
           f"  named {len(learned):,} ids by set overlap "
           f"(>= {MIN_SUPPORT} schools, >= {MIN_MATCH:.0%} coincidence)",
           f"  {agree:,} agree · {len(dis):,} disagree · {len(gap):,} gaps anet could fill",
           f"  {sum(1 for v in learned.values() if v[6]):,} of those names are "
           f"AMBIGUOUS (a parent with one child: nothing in the data separates "
           f"the two rungs, marked ? below)",
           "  by column:"]
    per = {}
    for col, value, n, _p, _an, _d, _amb in learned.values():
        per.setdefault(col, [0, 0])
        per[col][0] += 1
        per[col][1] += n
    for col, (ids, sup) in sorted(per.items(), key=lambda kv: -kv[1][0]):
        out.append(f"    {col:<12} {ids:4,} ids   {sup:6,} schools")
    out.append("  a sample of what was learned (anet's name -> ours):")
    for (base, sport), (col, value, n, p, anet_name, depth, amb) in \
            sorted(learned.items(), key=lambda kv: -kv[1][2])[:15]:
        out.append(f"    b={base:<6} {sport}  d{depth}  "
                   f"{(anet_name or '?')[:22]:<22} -> {col}={value!r} "
                   f"({n:,} schools, {p:.0%}){'  ?' if amb else ''}")
    if dis:
        out.append("  a sample of the disagreements:")
        for school, state, sport, col, ours, theirs in dis[:10]:
            out.append(f"    {school} ({state}) {sport}: we say {col}={ours!r}, "
                       f"anet's id says {theirs!r}")
    return "\n".join(out)


def store(cur, learned, gap):
    from scrape_school_logos import ensureTable
    ensureTable(cur, MAP_DDL)
    ensureTable(cur, GAP_DDL)
    cur.execute("TRUNCATE anet_unit_map")
    cur.execute("TRUNCATE anet_unit_gap")
    cur.executemany("""
        INSERT INTO anet_unit_map (base_id, sport, col, value, anet_name,
                                   support, match, depth, ambiguous)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, [(b, s, v[0], v[1], v[4], v[2], v[3], v[5], v[6])
          for (b, s), v in learned.items()])
    cur.executemany("""
        INSERT INTO anet_unit_gap (school, state, sport, col, value, base_id)
        VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING
    """, gap)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--write", action="store_true",
                    help="store anet_unit_map and anet_unit_gap (NOT school_unit)")
    ap.add_argument("--min-support", type=int, default=MIN_SUPPORT)
    ap.add_argument("--min-match", type=float, default=MIN_MATCH)
    ap.add_argument("--tsv", default=None, help="write the disagreements here")
    args = ap.parse_args()
    if not (args.report or args.write):
        ap.error("pass --report or --write")

    from database import getConn
    with getConn() as conn:
        with conn.cursor() as cur:
            observed = rows(cur)
            learned = learn(observed, args.min_support, args.min_match)
            dis, gap = disagreements(observed, learned), gaps(observed, learned)
            print(report(observed, learned, dis, gap))
            if args.tsv:
                with open(args.tsv, "w", encoding="utf-8") as fh:
                    fh.write("school\tstate\tsport\tcol\tours\tanet\n")
                    for row in dis:
                        fh.write("\t".join(str(x) for x in row) + "\n")
                print(f"  disagreements -> {args.tsv}")
            if args.write:
                store(cur, learned, gap)
                conn.commit()
                print(f"  stored {len(learned):,} names and {len(gap):,} gap fills")


if __name__ == "__main__":
    main()
