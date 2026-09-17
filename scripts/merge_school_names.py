#!/usr/bin/env python3
"""
merge_school_names.py -- two spellings, one team, proved by the athletes.

    python scripts/merge_school_names.py                 # DRY RUN, prints it
    python scripts/merge_school_names.py --show 80
    python scripts/merge_school_names.py --prefix        # also the shortenings
    python scripts/merge_school_names.py --write         # school_name_alias

★ THE BUG (owner, 2026-09-17). One athlete, twice on one board, because her
  team is spelled two ways:

    15  Chiara Dailey  La Jolla (CA)  152.2  9:53.38  San Diego HS vs La Jolla HS
    16  Chiara Dailey  La Jolla-CA    152.1  9:49.57  Brooks PR Invitation

  "please make it so team substrings like this are found and merged (where
  some athletes are the same!)"

⚠ THE SUBSTRING ALONE IS EXACTLY THE WRONG RULE, and the same conversation
  proves it: "Oregon" is a word prefix of "Oregon Episcopal", "Oregon Clay"
  and "Oregon School for the Deaf", which are four schools in three states.
  A containment test on its own would merge the set and undo the split this
  session just built.

★ SO THE NAME PROPOSES AND THE ATHLETES DECIDE -- the owner's own
  parenthesis, and the same join every other thing in this project that
  works is built on: person_id is already merged across the feeds, so
  "the same people wear both spellings" needs no spelling.

  Two bars, because the two candidate classes carry different risk:

    same    the spellings differ only in DECORATION -- a state suffix in
            any of the four shapes the feeds use, or a trailing "HS" /
            "College". Cheap to believe: MIN_SHARED athletes, and they must
            be MIN_FRACTION of the smaller spelling's roster.
    prefix  one name is a strict WORD prefix of the other. This is the
            Oregon Episcopal shape, so it is off unless --prefix and it
            needs PREFIX_MIN_SHARED / PREFIX_MIN_FRACTION instead.

⚠ AND AN anet TEAM ID VETOES EITHER. Two spellings whose rows sit on
  DIFFERENT anet teams are two schools whatever their names look like and
  whoever transferred between them. That check is free -- results.team_id is
  already there -- and it is the one piece of evidence that outranks a count.

WHAT IT WRITES, AND WHAT READS IT
    school_name_alias (variant PK, canonical, n_variant, n_canonical,
                       n_shared, relation, source, built)

! THE RAW TABLES KEEP THE RAW STRINGS. That is this project's standing rule
  (racecast/app.py: "the scrapers would fight anything else; the qualified
  label lives in the derived layer"), and nothing here rewrites results or
  results_tf. build_ranking_results folds the variant onto the canonical as
  it streams, so the BOARDS hold one school and the feeds keep their own
  spelling -- which also means the undo is a rerun with the table dropped.
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE, os.path.join(_ROOT, "racecast"),
           os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from school_name import canonicalOf, nameWords              # noqa: E402

# a decoration difference: believable on modest evidence
MIN_SHARED = 3
MIN_FRACTION = 0.10
# a shortening: the Oregon Episcopal shape, so it has to be overwhelming
PREFIX_MIN_SHARED = 10
PREFIX_MIN_FRACTION = 0.50

DDL = """
CREATE TABLE IF NOT EXISTS school_name_alias (
    variant     text NOT NULL,
    canonical   text NOT NULL,
    n_variant   int  NOT NULL,
    n_canonical int  NOT NULL,
    n_shared    int  NOT NULL,
    relation    text NOT NULL,
    source      text NOT NULL DEFAULT 'coathlete',
    built       date NOT NULL DEFAULT current_date,
    PRIMARY KEY (variant))
"""


def _hasColumn(cur, table, column):
    cur.execute("""SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = %s
                     AND column_name = %s""", (table, column))
    return cur.fetchone() is not None


def rosters(cur, min_athletes=2):
    """{school string: {person_id}} from the raw result tables.

    ! FROM results/results_tf, NOT FROM THE BOARDS. This has to run BEFORE
      build_ranking_results -- the boards are what the alias fixes -- so the
      evidence comes from the tables that exist at that point.

    ! DISTINCT PEOPLE, NOT ROWS. One prolific athlete is not a roster.
    """
    out = {}
    for table in ("results", "results_tf"):
        cur.execute(f"""
            SELECT btrim(r.school) AS school, r.person_id
            FROM   {table} r
            WHERE  r.person_id IS NOT NULL
              AND  r.school IS NOT NULL AND btrim(r.school) <> ''
            GROUP  BY 1, 2
        """)
        for school, pid in cur.fetchall():
            out.setdefault(school, set()).add(pid)
    return {k: v for k, v in out.items() if len(v) >= min_athletes}


def teamIds(cur):
    """{school string: {anet team_id}} -- the veto. Empty where the column
    does not exist, which is then simply no veto."""
    out = {}
    for table in ("results", "results_tf"):
        if not _hasColumn(cur, table, "team_id"):
            continue
        cur.execute(f"""
            SELECT btrim(r.school) AS school, r.team_id
            FROM   {table} r
            WHERE  r.team_id IS NOT NULL AND r.team_id <> 0
              AND  r.school IS NOT NULL AND btrim(r.school) <> ''
            GROUP  BY 1, 2
        """)
        for school, tid in cur.fetchall():
            out.setdefault(school, set()).add(int(tid))
    return out


# ★ THE CANDIDATE PAIRS, WITHOUT COMPARING EVERY NAME TO EVERY OTHER.
#
# ⚠ THE FIRST VERSION BUCKETED ON THE FIRST WORD AND WAS STILL QUADRATIC --
#   its own scale test took 90 seconds on 4,000 names, because a common first
#   word ("saint", "north", "central", "mount") puts thousands of schools in
#   one bucket and the pair scan inside it is n². Against the real corpus's
#   hundreds of thousands of strings that is not a slow pass, it is one that
#   never finishes.
#
# ★ NEITHER RELATION NEEDS A PAIR SCAN AT ALL, because both are defined on
#   the KEY:
#
#     same    two names with the SAME key -- a dict grouping, and every
#             group is one team's handful of spellings.
#     prefix  a name whose key is a proper WORD prefix of another's. So walk
#             each name's own prefixes and ask the dict whether any of them
#             is a key somebody else wears. That is one lookup per word, not
#             one per other school.
#
#   Linear in the number of names, and exact -- no bucket, nothing capped.
def candidates(names, want_prefix=False):
    """[(a, b, relation)] for every pair worth asking the athletes about.
    Pure -- takes the names, not a cursor."""
    by_key = {}
    for name in names:
        words = nameWords(name)
        if words:
            by_key.setdefault(words, []).append(name)

    pairs = []
    for words, group in by_key.items():
        group = sorted(group)
        # every pair of spellings that reduce to one key
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                pairs.append((a, b, "same"))
        if not want_prefix:
            continue
        # ...and every SHORTER key that is a word prefix of this one
        for k in range(1, len(words)):
            for a in by_key.get(words[:k], ()):
                for b in group:
                    pairs.append((a, b, "prefix"))
    return sorted(set(pairs))


def judge(pairs, rosters_by_name, teams_by_name,
          min_shared=MIN_SHARED, min_fraction=MIN_FRACTION,
          prefix_min_shared=PREFIX_MIN_SHARED,
          prefix_min_fraction=PREFIX_MIN_FRACTION):
    """(merged, refused) -- the whole decision, and it is pure so it is
    testable. Each entry is
    (a, b, relation, n_a, n_b, n_shared, fraction, why)."""
    merged, refused = [], []
    for a, b, rel in pairs:
        ra = rosters_by_name.get(a) or set()
        rb = rosters_by_name.get(b) or set()
        shared = len(ra & rb)
        small = min(len(ra), len(rb))
        frac = (shared / small) if small else 0.0
        need_n = prefix_min_shared if rel == "prefix" else min_shared
        need_f = prefix_min_fraction if rel == "prefix" else min_fraction
        row = [a, b, rel, len(ra), len(rb), shared, round(frac, 4), None]
        ta, tb = teams_by_name.get(a) or set(), teams_by_name.get(b) or set()
        # ⚠ THE VETO FIRST. Two spellings on two anet teams are two schools,
        #   however many athletes moved between them.
        if ta and tb and not (ta & tb):
            row[7] = f"different anet teams {sorted(ta)[:3]} vs {sorted(tb)[:3]}"
            refused.append(tuple(row))
        elif shared >= need_n and frac >= need_f:
            merged.append(tuple(row))
        else:
            row[7] = (f"{shared} shared of {small} ({frac:.0%}); "
                      f"needs {need_n} and {need_f:.0%}")
            refused.append(tuple(row))
    merged.sort(key=lambda r: -r[5])
    refused.sort(key=lambda r: -r[5])
    return merged, refused


# ★ A GROUP, NOT A PAIR. Three spellings of one team give three pairs, and
#   writing them as pairs would make "La Jolla (CA)" point at "La Jolla-CA"
#   which points at "La Jolla" -- a chain the reader would have to walk.
#   Union-find, then one canonical per group, so every variant resolves in
#   one lookup.
def groups(merged, rosters_by_name):
    """[(canonical, [(variant, n, relation, n_shared)])] -- one row per
    spelling that is NOT the canonical. Pure."""
    parent = {}

    def find(x):
        while parent.get(x, x) != x:
            x = parent[x]
        return x

    evidence = {}
    for a, b, rel, _na, _nb, shared, _f, _why in merged:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra
        evidence[a] = max(evidence.get(a, 0), shared)
        evidence[b] = max(evidence.get(b, 0), shared)
        evidence[(a, "rel")] = rel
        evidence[(b, "rel")] = rel

    members = {}
    for name in {x for r in merged for x in r[:2]}:
        members.setdefault(find(name), set()).add(name)

    out = []
    for root, names in sorted(members.items()):
        sized = [(n, len(rosters_by_name.get(n) or ())) for n in sorted(names)]
        canon = canonicalOf(sized)
        variants = [(n, sz, evidence.get((n, "rel"), "same"),
                     evidence.get(n, 0))
                    for n, sz in sized if n != canon]
        if variants:
            out.append((canon, sorted(variants)))
    return out


def write(cur, grouped, rosters_by_name):
    from scrape_school_logos import ensureTable
    ensureTable(cur, DDL)
    cur.execute("DELETE FROM school_name_alias WHERE source = 'coathlete'")
    rows = []
    for canon, variants in grouped:
        n_canon = len(rosters_by_name.get(canon) or ())
        for variant, n_var, rel, shared in variants:
            rows.append((variant, canon, n_var, n_canon, shared, rel))
    cur.executemany("""
        INSERT INTO school_name_alias (variant, canonical, n_variant,
                                       n_canonical, n_shared, relation, source)
        VALUES (%s, %s, %s, %s, %s, %s, 'coathlete')
        ON CONFLICT (variant) DO UPDATE
        SET canonical = EXCLUDED.canonical, n_variant = EXCLUDED.n_variant,
            n_canonical = EXCLUDED.n_canonical, n_shared = EXCLUDED.n_shared,
            relation = EXCLUDED.relation, built = current_date
    """, rows)
    return len(rows)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true",
                    help="without this, a dry run")
    ap.add_argument("--show", type=int, default=40)
    ap.add_argument("--prefix", action="store_true",
                    help="also consider a name that is a strict WORD prefix "
                         "of another (the 'Oregon Episcopal' shape) -- a much "
                         "higher bar, and off by default")
    ap.add_argument("--min-shared", type=int, default=MIN_SHARED)
    ap.add_argument("--min-fraction", type=float, default=MIN_FRACTION)
    a = ap.parse_args()

    from database import getConn
    with getConn() as conn, conn.cursor() as cur:
        by_name = rosters(cur)
        print(f"  {len(by_name):,} school strings with two or more athletes")
        pairs = candidates(by_name.keys(), want_prefix=a.prefix)
        print(f"  {len(pairs):,} candidate pairs "
              f"({'decoration and prefixes' if a.prefix else 'decoration only'})")
        by_team = teamIds(cur)
        merged, refused = judge(pairs, by_name, by_team,
                                min_shared=a.min_shared,
                                min_fraction=a.min_fraction)
        grouped = groups(merged, by_name)
        n_variants = sum(len(v) for _c, v in grouped)
        print(f"  {len(merged):,} pairs agree -> {len(grouped):,} groups, "
              f"{n_variants:,} spellings folded onto another\n")
        print(f"  {'canonical':<38}{'variant':<38}{'ath':>5}{'shared':>7}  rel")
        for canon, variants in grouped[:a.show]:
            for variant, n_var, rel, shared in variants:
                print(f"  {canon[:37]:<38}{variant[:37]:<38}{n_var:>5}"
                      f"{shared:>7}  {rel}")
        if refused:
            print(f"\n  NOT MERGED ({len(refused):,}) -- the near misses:")
            for x, y, rel, na, nb, shared, _f, why in refused[:a.show]:
                print(f"  {x[:33]:<34}{y[:33]:<34}{na:>5}{nb:>5}{shared:>6}"
                      f"  {rel}: {why}")
        if not a.write:
            print("\n  DRY RUN -- nothing written. --write to store them.\n")
            return 0
        n = write(cur, grouped, by_name)
        conn.commit()
        print(f"\n  wrote {n:,} rows to school_name_alias.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
