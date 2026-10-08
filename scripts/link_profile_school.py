#!/usr/bin/env python3
"""
link_profile_school.py -- a stray athletic.net profile whose exact name and
school are already on another person's profile set joins that person.

    /srv/venv/bin/python scripts/link_profile_school.py               # DRY RUN
    /srv/venv/bin/python scripts/link_profile_school.py --show 80
    /srv/venv/bin/python scripts/link_profile_school.py --person 31559611
    /srv/venv/bin/python scripts/link_profile_school.py --write       # by hand
    /srv/venv/bin/python scripts/link_profile_school.py --undo 31559611
    /srv/venv/bin/python scripts/link_profile_school.py --undo all

★ THE CASE (owner, 2026-10-07: "merge obviously doesn't work"). Soheib
  Dissa is three people. Person 32309981 holds his Newtown (CT) career,
  2021-2024, his unattached Duke freshman autumn, and eleven profile rows --
  'Newtown', 'Sandy Hook-CT', 'Unattached', and 'UNAT-Duke' and
  'Unattached-Duke' among them. Person 31559611 is ONE profile, 'Soheib
  Dissa', 'UNAT-Duke', with one race (a 1500 at UNAT-Duke, 2026-04-16).
  Person 32891852 is a profile 'Soheib Dissa', 'UNAT-Duke' and
  'Unattached-Duke', with no race at all. Same full name, same sex, the
  same school string, character for character -- and three pages.

★ WHY NOTHING JOINED THEM. anet rows are seeded person_id = athlete_id at
  insert, and two anet profiles are joined by ONE step only,
  link_teamless.py -- which asks something else: a profile whose every row
  is TEAMLESS, near a namesake's REAL-SCHOOL row within WINDOW_DAYS.
    - 'UNAT-Duke' is not teamless there (its prefixes are unatt / unnatt /
      unath; 'unat' counts only as the whole string), so 31559611 has a
      "real team" row and is a career, never a candidate.
    - Were it a candidate, Soheib's real-school rows end at Newtown in
      2024; his whole college career is 'Unattached', so no named-school
      row sits within 60 days of April 2026 ("no named-school namesake in
      the window").
    - 32891852 has no row at all, and a candidate is built from rows.
  And link_teamless never reads the profile's SCHOOL against the namesake's
  PROFILE schools, which is the one fact that says these are one runner.
  (person_collision.py splits, it never joins; link_freshmen and
  link_tfrrs_rows join tfrrs rows to anet, never anet to anet.)

★ THE RULE. A profile key is (normalised full name, school string as
  written). When one key is on the profile sets of two or more persons,
  those persons are one GROUP (keys chain: a person sharing one key with A
  and another with B joins both). A group joins its CAREER when:
    1. the school IDENTIFIES something (identifyingSchool): not blank, not
       a no-team word alone ('Unattached', 'Unknown', '05-Unattached',
       'Unattached (OR)'), not a hometown ('Sandyhook CT'). What is left
       once those words come off -- 'Duke' in 'UNAT-Duke' -- is a school;
    2. the name has two tokens at least (link_freshmen.normName);
    3. exactly ONE member is a career; every other member is a STRAY: a
       lone anet profile still on its own seed -- every athletes row and
       every result under its id is its own athlete's, nothing linked to
       it. Two careers under one key is two people or a merge for a human,
       and is refused. With no career at all, the member with the most
       rows is the target, and a tie is refused;
    4. nothing contradicts: the sexes agree where known; no two members
       raced cross country on the same day (two runners in one field); a
       numeric grade on a stray implies a class within CLASS_SLACK of the
       target's (link_teamless's generation test, imported).

⚠ A WRONG MERGE IS WORSE THAN A MISSED ONE (unlink.py, link_freshmen.py).
  "John Rivera" is 40 people; his name with 'Unattached' joins nobody here,
  because 'Unattached' identifies nothing. His name with one named school is
  a narrow key, and a second career under it refuses the whole group. There
  is no row-count threshold: a stray with ten rows moves on the same
  evidence as one with none, and a career is a career by what is linked to
  it, not by how much it raced.

! WRONG-PERSON DETECTION STAYS REPORT-ONLY. This joins strays; it never
  splits, and it does not decide whether a career is two people
  (level_conflict.py, twin_flag.py, person_collision.py are where that is
  flagged).

★ WHAT --write DOES, IN ONE TRANSACTION, AND --undo TAKES BACK. The same
  shape as link_teamless.write:
    - profile_school_merge: the decision (stray, target, name, school), so
      the step is STICKY -- a re-scrape seeds the stray's next row with its
      own id again, and the next --write moves it without deciding again;
    - person_link_log rule 'profile_school': every moved result;
    - results / results_tf: rows of athlete X still on person X -> target;
    - athletes: X's rows on person X -> target (person_redirects calls an
      id gone only when no athletes row carries it);
    - person_redirect: X -> target, so /athlete/X 301s.
  --undo X puts X back and VETOES it (profile_school_veto); --undo all
  reverses every merge.

! RUN BY HAND, like person_collision.py --write: not a pipeline step until
  the owner says so. Read-only without --write / --undo.
"""
import argparse
import os
import re
import statistics
import sys
from collections import defaultdict, namedtuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT, os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ★ ONE NAME RULE, ONE TEAMLESS VOCABULARY, ONE GENERATION TEST: the
#   teamless linker's. Two linkers that disagree about what a name or a
#   no-team string is would each find "matches" the other refuses.
from link_freshmen import LOG_DDL, normName                     # noqa: E402
from link_teamless import (CLASS_SLACK, NO_TEAM_EXACT, US_STATES,  # noqa: E402
                           _g, _numGrade, classYear, hometownState,
                           nameKeySql)

RULE = "profile_school"
ANET_TOP = 1_000_000_000          # below the tfrrs mint: an anet-seeded person
TABLES = (("XC", "results"), ("TF", "results_tf"))
MATCH = "match"

# ------------------------------------------------------------------ #
#  WHAT A SCHOOL THAT IDENTIFIES SOMETHING IS
# ------------------------------------------------------------------ #
# the words that say "no team" inside a longer string ('UNAT-Duke',
# 'Unattached-Duke', '05-Unattached'); link_teamless's spellings, plus the
# bare 'unat' prefix it only counts as a whole string
_NO_TEAM_WORDS = re.compile(
    r"(?<![a-z])(un-?n?atta?t?ched|unatt\w*|unnatt\w*|unath\w*|unat(?![a-z]))"
    r"|independent|individuals?|no team|no school|unknown|runner")
_PAREN = re.compile(r"\([^)]*\)")
_STATE_CODES = frozenset(s.lower() for s in US_STATES)


def identifyingSchool(school):
    """True when the profile school names something: not blank, not a
    no-team word alone, not a hometown. 'UNAT-Duke' and 'Unattached-Duke'
    are Duke; 'Unattached', '05-Unattached', 'Unattached (OR)' and
    'Sandyhook CT' are nobody's school."""
    s = (school or "").strip()
    low = s.lower()
    # 'Sandy Hook-CT' is the hometown 'Sandy Hook CT' with a hyphen
    town = re.sub(r"\s*-\s*([A-Za-z]{2})$", r" \1", s)
    if not low or low in NO_TEAM_EXACT or hometownState(s) is not None \
            or hometownState(town) is not None:
        return False
    rest = _NO_TEAM_WORDS.sub(" ", _PAREN.sub(" ", low))
    words = [w for w in re.sub(r"[^a-z]+", " ", rest).split()
             if w not in _STATE_CODES]
    return bool(words)


def schoolKey(school):
    """The school as written, for an exact match: trimmed, case folded."""
    return (school or "").strip().lower()


# ------------------------------------------------------------------ #
#  THE DECISION -- pure
# ------------------------------------------------------------------ #
Row = namedtuple("Row", "date sport grade")
# lone: an anet profile on its own seed with nothing else under its id
Member = namedtuple("Member", "pid name gender lone rows")
Verdict = namedtuple("Verdict", "target movers reason", defaults=((), ""))


def _genders(m):
    return {_g(x) for x in (m.gender if isinstance(m.gender, (list, tuple, set))
                            else [m.gender])} - {None}


def decideGroup(members):
    """Verdict(target pid or None, mover pids, reason) for one group of
    persons that share a profile key. See the header for the rule."""
    if len(members) < 2:
        return Verdict(None, (), "alone")
    sexes = set().union(*(_genders(m) for m in members))
    if len(sexes) > 1:
        return Verdict(None, (), "sexes disagree")
    careers = [m for m in members if not m.lone]
    if len(careers) > 1:
        return Verdict(None, (), "two careers share the profile school")
    if careers:
        target = careers[0]
    else:
        most = max(len(m.rows) for m in members)
        top = [m for m in members if len(m.rows) == most]
        if len(top) > 1:
            return Verdict(None, (), "no career and no clear target")
        target = top[0]
    movers = [m for m in members if m.pid != target.pid]

    # ! TWO CROSS COUNTRY RACES ON ONE DAY IS TWO PEOPLE (link_teamless's
    #   rule). Track is left out: one athlete runs several events a day.
    xc_days = {}
    for m in members:
        for d in {str(r.date)[:10] for r in m.rows if r.sport == "XC"}:
            if d in xc_days and xc_days[d] != m.pid:
                return Verdict(None, (), "same cross country day: two runners")
            xc_days[d] = m.pid

    t_cls = [c for c in (classYear(r.date, r.grade) for r in target.rows) if c]
    t_med = statistics.median(t_cls) if t_cls else None
    for m in movers:
        for r in m.rows:
            g = _numGrade(r.grade)
            if g is not None and g > 12:
                return Verdict(None, (), "grade reads as an age")
            cy = classYear(r.date, r.grade)
            if cy is not None and t_med is not None and abs(cy - t_med) > CLASS_SLACK:
                return Verdict(None, (), "generation mismatch")
    return Verdict(target.pid, tuple(sorted(m.pid for m in movers)), MATCH)


def groupsOf(key_persons):
    """[(set of person ids, set of keys)] -- persons joined through any
    shared key (union-find). key_persons: {key: set(person ids)}."""
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for pids in key_persons.values():
        pids = sorted(pids)
        for p in pids[1:]:
            ra, rb = find(pids[0]), find(p)
            if ra != rb:
                parent[rb] = ra
    out = defaultdict(lambda: (set(), set()))
    for key, pids in key_persons.items():
        for p in pids:
            out[find(p)][0].add(p)
            out[find(p)][1].add(key)
    return list(out.values())


# ------------------------------------------------------------------ #
#  THE CORPUS SIDE
# ------------------------------------------------------------------ #
def _hasTable(cur, name):
    cur.execute("SELECT to_regclass(%s)", (name,))
    return cur.fetchone()[0] is not None


def gather(cur, person=None):
    """({group index: [Member]}, {group index: keys}). Temp tables only;
    the caller rolls back."""
    from psycopg2.extras import execute_values
    cur.execute("SET LOCAL work_mem = '512MB'")
    for t in ("ps_prof", "ps_key", "ps_ok", "ps_mp"):
        cur.execute(f"DROP TABLE IF EXISTS {t}")
    only = ""
    if person is not None:
        only = (f"AND {nameKeySql('a.first_name', 'a.last_name')} IN ("
                f"SELECT {nameKeySql('b.first_name', 'b.last_name')} FROM athletes b "
                f"WHERE b.person_id = %(person)s OR b.athlete_id = %(person)s)")
    cur.execute(f"""
        CREATE TEMP TABLE ps_prof AS
        SELECT a.athlete_id, a.person_id, a.gender,
               {nameKeySql('a.first_name', 'a.last_name')} AS nm,
               concat_ws(' ', btrim(a.first_name), btrim(a.last_name)) AS name,
               lower(btrim(a.school)) AS sk
        FROM   athletes a
        WHERE  a.person_id IS NOT NULL AND NULLIF(btrim(a.school), '') IS NOT NULL
          {only}
    """, {"person": person})
    cur.execute("""
        CREATE TEMP TABLE ps_key AS
        SELECT nm, sk, array_agg(DISTINCT person_id) AS pids FROM ps_prof
        WHERE  position(' ' in nm) > 0
        GROUP  BY nm, sk HAVING count(DISTINCT person_id) > 1
    """)
    cur.execute("SELECT nm, sk, pids FROM ps_key")
    keys = {(nm, sk): set(pids) for nm, sk, pids in cur.fetchall()
            if normName(nm) and identifyingSchool(sk)}
    if _hasTable(cur, "profile_school_veto"):
        cur.execute("SELECT old_id FROM profile_school_veto")
        veto = {r[0] for r in cur.fetchall()}
        keys = {k: v - veto for k, v in keys.items()}
        keys = {k: v for k, v in keys.items() if len(v) > 1}
    groups = groupsOf(keys)
    pids = sorted(set().union(*(g[0] for g in groups))) if groups else []
    cur.execute("CREATE TEMP TABLE ps_mp (person_id bigint PRIMARY KEY)")
    if pids:
        execute_values(cur, "INSERT INTO ps_mp VALUES %s", [(p,) for p in pids])
    cur.execute(f"""
        SELECT m.person_id,
               (SELECT array_agg(DISTINCT a.gender) FROM athletes a
                WHERE a.person_id = m.person_id AND a.gender IN ('M', 'F')),
               (SELECT concat_ws(' ', btrim(a.first_name), btrim(a.last_name))
                FROM athletes a WHERE a.person_id = m.person_id
                ORDER BY (a.athlete_id = m.person_id) DESC, a.athlete_id LIMIT 1),
               m.person_id < {ANET_TOP}
               AND EXISTS (SELECT 1 FROM athletes a WHERE a.athlete_id = m.person_id
                             AND a.person_id = m.person_id)
               AND NOT EXISTS (SELECT 1 FROM athletes a WHERE a.person_id = m.person_id
                                 AND a.athlete_id <> m.person_id)
               AND NOT EXISTS (SELECT 1 FROM results r WHERE r.person_id = m.person_id
                                 AND NOT (r.source = 'anet' AND r.athlete_id = m.person_id))
               AND NOT EXISTS (SELECT 1 FROM results_tf r WHERE r.person_id = m.person_id
                                 AND NOT (r.source = 'anet' AND r.athlete_id = m.person_id))
        FROM   ps_mp m
    """)
    facts = {p: (g or [], nm, bool(lone)) for p, g, nm, lone in cur.fetchall()}
    rows = defaultdict(list)
    cur.execute("""
        SELECT 'XC', r.person_id, r.date, r.grade FROM results r
        JOIN ps_mp m ON m.person_id = r.person_id
        UNION ALL
        SELECT 'TF', r.person_id, r.date, r.grade FROM results_tf r
        JOIN ps_mp m ON m.person_id = r.person_id
    """)
    for sport, p, d, g in cur.fetchall():
        rows[p].append(Row(d, sport, g))
    out_m, out_k = {}, {}
    for i, (members, gkeys) in enumerate(groups):
        out_m[i] = [Member(p, facts[p][1], facts[p][0], facts[p][2],
                           sorted(rows.get(p, []), key=lambda r: str(r.date)))
                    for p in sorted(members)]
        out_k[i] = sorted(gkeys)
    return out_m, out_k


def judge(groups):
    return {i: decideGroup(ms) for i, ms in groups.items()}


# ------------------------------------------------------------------ #
#  WRITING -- link_teamless.write's shape
# ------------------------------------------------------------------ #
MERGE_DDL = """
CREATE TABLE IF NOT EXISTS profile_school_merge (
    old_id    bigint PRIMARY KEY,
    new_id    bigint NOT NULL,
    name      text,
    school    text,
    merged_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS profile_school_veto (
    old_id    bigint PRIMARY KEY,
    vetoed_at timestamptz NOT NULL DEFAULT now()
)"""


def decisionsOf(groups, keys, verdicts):
    """[(old_id, new_id, name, school)] for every matched group."""
    out = []
    for i, v in verdicts.items():
        if v.target is None:
            continue
        for p in v.movers:
            name = next(m.name for m in groups[i] if m.pid == p)
            out.append((p, v.target, name, ", ".join(sk for _nm, sk in keys[i])))
    return out


def write(conn, decisions):
    """Record the new decisions, then (re)apply every decision on file."""
    from psycopg2.extras import execute_values
    from person_redirects import DDL as REDIRECT_DDL, follow
    out = {}
    with conn.cursor() as cur:
        cur.execute(LOG_DDL)
        cur.execute(MERGE_DDL)
        cur.execute(REDIRECT_DDL)
        cur.execute("SELECT count(*) FROM profile_school_merge")
        before = cur.fetchone()[0]
        if decisions:
            execute_values(cur, """
                INSERT INTO profile_school_merge (old_id, new_id, name, school)
                VALUES %s ON CONFLICT (old_id) DO NOTHING""", decisions)
        cur.execute("SELECT count(*) FROM profile_school_merge")
        out["decisions new"] = cur.fetchone()[0] - before
        # ! A TARGET CAN MOVE TOO: follow its redirect
        cur.execute("SELECT old_id, new_id FROM profile_school_merge")
        for old_id, new_id in cur.fetchall():
            moved = follow(cur, new_id)
            if moved and moved != old_id:
                cur.execute("UPDATE profile_school_merge SET new_id = %s "
                            "WHERE old_id = %s", (moved, old_id))
        cur.execute("DROP TABLE IF EXISTS ps_map")
        cur.execute("""CREATE TEMP TABLE ps_map AS
                       SELECT old_id, new_id FROM profile_school_merge
                       WHERE old_id NOT IN (SELECT old_id FROM profile_school_veto)""")
        for sport, table in TABLES:
            cur.execute(f"""
                INSERT INTO person_link_log (sport, result_id, from_person,
                                             to_person, rule)
                SELECT %s, r.result_id, m.old_id, m.new_id, %s
                FROM   {table} r JOIN ps_map m ON r.person_id = m.old_id
                WHERE  r.source = 'anet' AND r.athlete_id = m.old_id
                ON CONFLICT DO NOTHING""", (sport, RULE))
            cur.execute(f"""
                UPDATE {table} r SET person_id = m.new_id
                FROM   ps_map m
                WHERE  r.person_id = m.old_id
                  AND  r.source = 'anet' AND r.athlete_id = m.old_id""")
            out[f"{sport} rows moved"] = cur.rowcount
        cur.execute("""
            UPDATE athletes a SET person_id = m.new_id
            FROM   ps_map m
            WHERE  a.athlete_id = m.old_id AND a.person_id = m.old_id""")
        out["athletes rows repointed"] = cur.rowcount
        cur.execute("""
            INSERT INTO person_redirect (old_id, new_id, n_probes)
            SELECT old_id, new_id, 0 FROM ps_map
            ON CONFLICT (old_id) DO UPDATE SET new_id = EXCLUDED.new_id
            WHERE person_redirect.new_id <> EXCLUDED.new_id""")
        out["redirects written"] = cur.rowcount
    conn.commit()
    return out


def undo(conn, which):
    """which: 'all' or one old id (that one is also vetoed)."""
    with conn.cursor() as cur:
        if not _hasTable(cur, "profile_school_merge"):
            print("[profile-school] nothing to undo")
            return {}
        cur.execute(MERGE_DDL)
        one = None if which == "all" else int(which)
        cond = "" if one is None else "AND l.from_person = %(one)s"
        mcond = "" if one is None else "AND m.old_id = %(one)s"
        back = {}
        for sport, table in TABLES:
            cur.execute(f"""
                UPDATE {table} r SET person_id = l.from_person
                FROM   person_link_log l
                WHERE  l.sport = %(sport)s AND l.rule = %(rule)s {cond}
                  AND  r.result_id = l.result_id AND r.person_id = l.to_person
            """, {"sport": sport, "rule": RULE, "one": one})
            back[sport] = cur.rowcount
        cur.execute(f"""
            UPDATE athletes a SET person_id = a.athlete_id
            FROM   profile_school_merge m
            WHERE  a.athlete_id = m.old_id AND a.person_id = m.new_id {mcond}
        """, {"one": one})
        back["athletes"] = cur.rowcount
        if _hasTable(cur, "person_redirect"):
            cur.execute(f"""
                DELETE FROM person_redirect p USING profile_school_merge m
                WHERE p.old_id = m.old_id AND p.new_id = m.new_id {mcond}
            """, {"one": one})
        cur.execute(f"DELETE FROM person_link_log l WHERE l.rule = %(rule)s {cond}",
                    {"rule": RULE, "one": one})
        cur.execute(f"DELETE FROM profile_school_merge m WHERE true {mcond}",
                    {"one": one})
        if one is not None:
            cur.execute("INSERT INTO profile_school_veto (old_id) VALUES (%s) "
                        "ON CONFLICT DO NOTHING", (one,))
    conn.commit()
    return back


# ------------------------------------------------------------------ #
#  THE REPORT
# ------------------------------------------------------------------ #
def report(groups, keys, verdicts, show):
    from collections import Counter
    reasons = Counter(v.reason for v in verdicts.values())
    print(f"[profile-school] {len(verdicts):,} groups of persons sharing a "
          f"(name, school) profile key:")
    for why, n in reasons.most_common():
        print(f"    {n:>8,}  {why}")
    shown = 0
    for i, v in sorted(verdicts.items(), key=lambda kv: kv[1].reason != MATCH):
        if shown >= show:
            break
        shown += 1
        print(f"\n  {v.reason}: {', '.join(sk for _nm, sk in keys[i])[:60]!r}")
        for m in groups[i]:
            tag = ("TARGET" if m.pid == v.target else
                   "moves" if m.pid in v.movers else "")
            print(f"    /athlete/{m.pid} {m.name} {'/'.join(sorted(_genders(m))) or '?'} "
                  f"{'stray' if m.lone else 'career'} {len(m.rows)} rows  {tag}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--person", type=int, help="only the groups of this person's names")
    ap.add_argument("--show", type=int, default=40)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--undo", metavar="ID|all")
    a = ap.parse_args()
    from database import getConn
    with getConn() as conn:
        if a.undo:
            print(f"[profile-school] undone {a.undo}: {undo(conn, a.undo)}")
            return
        with conn.cursor() as cur:
            groups, keys = gather(cur, a.person)
        conn.rollback()                          # temp tables only
        verdicts = judge(groups)
        report(groups, keys, verdicts, a.show)
        decisions = decisionsOf(groups, keys, verdicts)
        if not a.write:
            print(f"\n[profile-school] DRY RUN: {len(decisions):,} profiles would "
                  f"join; --write to join them")
            return
        out = write(conn, decisions)
        print(f"\n[profile-school] {out}; logged in person_link_log rule "
              f"'{RULE}' (--undo <id> | all)")


if __name__ == "__main__":
    main()
