#!/usr/bin/env python3
"""
link_feed_twins.py -- one cross country runner, held by two or three persons
because athletic.net and tfrrs each carried her races, becomes one person.

    /srv/venv/bin/python scripts/link_feed_twins.py                 # DRY RUN
    /srv/venv/bin/python scripts/link_feed_twins.py --show 80
    /srv/venv/bin/python scripts/link_feed_twins.py --person 1009408205
    /srv/venv/bin/python scripts/link_feed_twins.py --write         # by hand
    /srv/venv/bin/python scripts/link_feed_twins.py --undo 33412345
    /srv/venv/bin/python scripts/link_feed_twins.py --undo all

★ THE CASE (owner, 2026-10-08: "Seems there is a lot of duplication
  recently!"). The 2026 college women's board:

      1 Unknown, Marshall (WV)          156.9  16:18.9   Queen City Invite
      2 Unknown, Marshall (WV)          153.8  16:11.16  Marshall University
                                                         Thundering Herd XC Inv.
      3 Rachael Withrow, Marshall (WV)  153.7  16:18.9   Queen City Invite
      4 Rachael Withrow, Marshall (WV)  152.9  16:11.2   2026 Thundering Herd ...

  One runner, three persons. tfrrs (athlete 9408205, SR-4, first in both
  races) calls the September 4 meet "2026 Thundering Herd XC Invitational"
  and prints 16:11.2; athletic.net calls it "Marshall University Thundering
  Herd XC Invitational" and prints 16:11.16. So the NAMED rows are the tfrrs
  copies and the two "Unknown" persons are athletic.net profiles -- one per
  meet upload, each a blank placeholder (the cross country saver wrote
  ('', '', '') until 2026-09-25, a5a90091; results.athlete_name is kept only
  since 2026-10-02, 48c842c2). Elon (Abigail Beville and an Unknown at
  146.5) and Middle Tennessee are the same shape.

★ WHY NOTHING JOINED THEM, AND WHY "RECENTLY".
    - The cross-feed link -- entity_links, then merge_links/dedup_launch
      stamping person_id AND canon_meet_id -- runs by hand and has not run
      for 2026; no 2026 meet has a canon_meet_id.
    - 04a (link_tfrrs_rows.py, in the pipeline since 2026-09-25) gives a
      tfrrs row the person its tfrrs id already has (fan-out), a freshman's
      own high school career, or a person MINTED from the tfrrs id. None of
      the three looks at the athletic.net copy of the same race. Before
      04a the 2026 tfrrs rows had no person and were never rated; since 04a
      a transfer or walk-on is minted, rated and ranked BESIDE her anet
      profile.
    - 04c's cross-feed rules: twin_race and twin_person key on
      canon_meet_id (NULL for 2026); twin_same_day (2026-10-06) keys on the
      SAME person. Two persons and no canon meet: no rule fires, and the
      rankings fold (rankings.py, PARTITION BY person_id, canon meet, time)
      is per person too.
    - link_profile_school / link_teamless join anet profiles by NAME; a
      blank placeholder has none.

★ THE RULE. A tfrrs row and an anet row are ONE RUN when:
    1. same day, and the same time as precisely as both feeds print it:
       to the tenth when both carry a fraction (16:11.16 is 16:11.2), to
       the second when either is whole seconds (tfrrs's 13:21 for anet's
       13:21.4, the UVU pair twin_same_day was written for);
    2. the same school: one school's words sit inside the other's once the
       words every school name has are dropped ('Middle Tennessee' inside
       'Middle Tennessee State'; 'Marshall' and 'Marshall');
    3. and each row has exactly ONE such partner. Two teammates in a pack
       one tenth apart give a row two partners, and that row says nothing.
  Persons are joined through their paired rows (a chain: Rachael's tfrrs
  person pairs one anet profile at Queen City and another at Thundering
  Herd, so all three are one GROUP). A group joins when:
    a. the named members agree on the name (link_freshmen.normName; a blank
       profile has no say);
    b. the sexes agree where known (athletes, person_gender, the tfrrs team
       slug);
    c. on any day two members both raced cross country, every one of those
       rows is the same time -- two different times on one day is two
       runners in one field;
    d. it has a target: exactly one CAREER (a person that is neither a lone
       anet profile nor a tfrrs-only minted person) -- else, with no
       career, exactly one MINTED tfrrs person. Every STRAY (a lone anet
       profile on its own seed: link_profile_school's definition) and
       every other minted person moves to it. Two careers is a question
       for a human and is reported, never merged.

⚠ A WRONG MERGE IS WORSE THAN A MISSED ONE. There is no count threshold:
  one paired race moves a stray on the same evidence as five, because the
  evidence is the run itself (day, time to the printed precision, school,
  one partner) and every contradiction (name, sex, a second time that day,
  two careers) refuses the whole group.

! WRONG-PERSON DETECTION STAYS REPORT-ONLY. This joins strays and minted
  tfrrs persons into the one career they duplicate; it never splits, and
  "two careers" is printed, not decided.

★ WHAT --write DOES, IN ONE TRANSACTION, AND --undo TAKES BACK
  (link_profile_school.write's shape):
    - feed_twin_merge: the decision (old, new, kind, name), STICKY -- a
      re-scrape seeds a stray's next row with its own id again and the next
      --write moves it; a minted person's next tfrrs row follows by 04a's
      fan-out, which stamps the person its tfrrs id's rows agree on;
    - person_link_log rule 'feed_twin': every moved result;
    - results / results_tf: a stray's own anet rows, a minted person's tfrrs
      rows -> the target;
    - athletes: a stray's rows -> the target;
    - person_redirect: old -> target, so /athlete/<old> 301s.
  The next 04c then flags the tfrrs copy (twin_same_day: one person, one
  day, one time; anet survives), and the next boards build shows one row
  with the tfrrs name (rankings.nameLateral reads the person's result rows
  when every profile is blank). --undo X puts X back and VETOES it
  (feed_twin_veto); --undo all reverses every merge.

! RUN BY HAND: not a pipeline step until the owner says so. Read-only
  without --write / --undo.
"""
import argparse
import datetime
import math
import os
import re
import sys
from collections import defaultdict, namedtuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT, os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from link_freshmen import LOG_DDL, normName                     # noqa: E402
from link_tfrrs_rows import MINT_BASE, MINT_MAX_NATIVE           # noqa: E402

RULE = "feed_twin"
MATCH = "match"
TABLES = (("XC", "results"), ("TF", "results_tf"))
MINT_LO = min(MINT_BASE.values())
MINT_HI = max(MINT_BASE.values()) + MINT_MAX_NATIVE    # unlink's SPLIT_BASE

STRAY, MINTED, CAREER = "stray", "minted", "career"

# ------------------------------------------------------------------ #
#  ONE RUN: THE TIME AND THE SCHOOL -- pure, and the SQL beside it
# ------------------------------------------------------------------ #
# the words nearly every school name carries; what is left names the school
SCHOOL_STOP = ("university", "univ", "college", "the", "of", "at", "and")


def schoolWords(school):
    """'Middle Tennessee State University' -> {'middle', 'tennessee',
    'state'}; blank -> empty."""
    words = re.split(r"[^a-z0-9]+", str(school or "").lower())
    return frozenset(w for w in words if w and w not in SCHOOL_STOP)


def sameSchool(a, b):
    """One school's words inside the other's, neither empty."""
    wa, wb = schoolWords(a), schoolWords(b)
    return bool(wa) and bool(wb) and (wa <= wb or wb <= wa)


def _whole(t):
    return abs(t - round(t)) < 1e-6


def sameTime(a, b):
    """The same time as precisely as both feeds print it: to the tenth when
    both carry a fraction, to the second when either is whole seconds (the
    other read rounded or cut)."""
    if a is None or b is None:
        return False
    a, b = float(a), float(b)
    # ! HALF UP, AS POSTGRES ROUNDS A NUMERIC: Python's round() is half to
    #   even, and 13.5 / 12.5 would then disagree with _sameTimeSql
    def up(v, k=0):
        return math.floor(v * 10 ** k + 0.5 + 1e-9) / 10 ** k
    if _whole(a) or _whole(b):
        w, x = (a, b) if _whole(a) else (b, a)
        return up(w) in (math.floor(x + 1e-6), up(x))
    return up(a, 1) == up(b, 1)


def _sameTimeSql(a, b):
    """sameTime in SQL, for real columns a and b."""
    wa = f"({a} = trunc({a}))"
    wb = f"({b} = trunc({b}))"
    return f"""(CASE
        WHEN {wa} THEN {a} IN (floor({b}::numeric + 0.000001), round({b}::numeric))
        WHEN {wb} THEN {b} IN (floor({a}::numeric + 0.000001), round({a}::numeric))
        ELSE round({a}::numeric, 1) = round({b}::numeric, 1) END)"""


def _schoolWordsSql(col):
    stop = ", ".join(f"'{w}'" for w in SCHOOL_STOP)
    return (f"ARRAY(SELECT DISTINCT w FROM regexp_split_to_table(lower(COALESCE({col}, '')), "
            f"'[^a-z0-9]+') w WHERE w <> '' AND w NOT IN ({stop}))")


# ------------------------------------------------------------------ #
#  THE DECISION -- pure
# ------------------------------------------------------------------ #
Run = namedtuple("Run", "result_id date ts source")
Member = namedtuple("Member", "pid name genders kind runs")
Verdict = namedtuple("Verdict", "target movers reason", defaults=((), ""))


def decideGroup(members):
    """Verdict(target or None, mover pids, reason) for one group of persons
    joined through paired runs. See the header for the rule."""
    if len(members) < 2:
        return Verdict(None, (), "alone")
    names = {normName(m.name) for m in members if normName(m.name)}
    if len(names) > 1:
        return Verdict(None, (), "names disagree")
    sexes = set().union(*(set(m.genders or ()) for m in members)) - {None, ""}
    if len(sexes) > 1:
        return Verdict(None, (), "sexes disagree")

    # ! TWO TIMES ON ONE DAY IS TWO RUNNERS. Only across members: one
    #   member's own two rows on one day are its own business (04c's).
    by_day = defaultdict(list)
    for m in members:
        for r in m.runs:
            by_day[str(r.date)[:10]].append((m.pid, r.ts))
    for day, xs in by_day.items():
        if len({p for p, _t in xs}) < 2:
            continue
        ts = [t for _p, t in xs if t is not None]
        if any(not sameTime(ts[0], t) for t in ts[1:]):
            return Verdict(None, (), "two times on one day: two runners")

    careers = [m for m in members if m.kind == CAREER]
    minted = [m for m in members if m.kind == MINTED]
    if len(careers) > 1:
        return Verdict(None, (), "two careers: a question for a human")
    if careers:
        target = careers[0]
    elif len(minted) == 1:
        target = minted[0]
    else:
        return Verdict(None, (), "no career and no single minted person")
    movers = tuple(sorted(m.pid for m in members if m.pid != target.pid))
    return Verdict(target.pid, movers, MATCH)


def groupsOf(pairs):
    """[set of person ids] joined through any pair (union-find)."""
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in pairs:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra
    out = defaultdict(set)
    for x in list(parent):
        out[find(x)].add(x)
    return [g for g in out.values() if len(g) > 1]


# ------------------------------------------------------------------ #
#  THE CORPUS SIDE
# ------------------------------------------------------------------ #
def _hasTable(cur, name):
    cur.execute("SELECT to_regclass(%s)", (name,))
    return cur.fetchone()[0] is not None


def seasonStart(seasons, today=None):
    today = today or datetime.date.today()
    ay = today.year if today.month >= 8 else today.year - 1
    return f"{ay - seasons + 1}-08-01"


def pairRows(cur, since, persons=None):
    """[(tfrrs result, tfrrs person, anet result, anet person, day, t ts,
    a ts)]: every one-partner pair of cross country rows (the RULE, 1-3)
    since `since`, on two different persons -- on the days `persons` raced,
    when given. Temp tables only."""
    cur.execute("SET LOCAL work_mem = '1GB'")
    for t in ("ft_t", "ft_a", "ft_c", "ft_days"):
        cur.execute(f"DROP TABLE IF EXISTS {t}")
    days = ""
    if persons is not None:
        cur.execute("""CREATE TEMP TABLE ft_days AS
                       SELECT DISTINCT substr(date, 1, 10) AS d FROM results
                       WHERE person_id = ANY(%(p)s) AND date >= %(since)s""",
                    {"p": sorted(persons), "since": since})
        days = "AND substr(r.date, 1, 10) IN (SELECT d FROM ft_days)"
    keys = ("ARRAY[floor(r.time_seconds::numeric)::bigint, "
            "round(r.time_seconds::numeric)::bigint]")
    for name, src in (("ft_t", "tfrrs"), ("ft_a", "anet")):
        cur.execute(f"""
            CREATE TEMP TABLE {name} AS
            SELECT r.result_id, r.person_id, substr(r.date, 1, 10) AS d,
                   r.time_seconds AS ts, {_schoolWordsSql('r.school')} AS sw,
                   k AS sec
            FROM   results r, unnest({keys}) k
            WHERE  r.source = '{src}' AND r.person_id IS NOT NULL
              AND  r.time_seconds > 0 AND r.time_seconds < 100000
              AND  r.date >= %(since)s
              AND  r.date ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}' {days}
              {"" if src == "tfrrs" else
               "AND substr(r.date, 1, 10) IN (SELECT DISTINCT d FROM ft_t)"}
        """, {"since": since})
        cur.execute(f"CREATE INDEX ON {name} (d, sec)")
        cur.execute(f"ANALYZE {name}")
    cur.execute(f"""
        CREATE TEMP TABLE ft_c AS
        SELECT DISTINCT t.result_id AS t_rid, t.person_id AS t_pid,
               a.result_id AS a_rid, a.person_id AS a_pid, t.d,
               t.ts AS t_ts, a.ts AS a_ts
        FROM   ft_t t JOIN ft_a a ON a.d = t.d AND a.sec = t.sec
        WHERE  {_sameTimeSql('t.ts', 'a.ts')}
          AND  cardinality(t.sw) > 0 AND cardinality(a.sw) > 0
          AND  (t.sw <@ a.sw OR a.sw <@ t.sw)
    """)
    # ★ ONE PARTNER EACH WAY; a pack a tenth apart says nothing. Pairs on
    #   ONE person are counted too (a row already linked is not a stray's).
    cur.execute("""
        SELECT t_rid, t_pid, a_rid, a_pid, d, t_ts, a_ts FROM (
            SELECT c.*, count(*) OVER (PARTITION BY t_rid) AS nt,
                        count(*) OVER (PARTITION BY a_rid) AS na
            FROM ft_c c) x
        WHERE nt = 1 AND na = 1 AND t_pid <> a_pid
        ORDER BY d, t_rid
    """)
    return cur.fetchall()


def members(cur, pids):
    """{pid: Member} with name, sexes, kind and every cross country run."""
    from psycopg2.extras import execute_values
    cur.execute("DROP TABLE IF EXISTS ft_mp")
    cur.execute("CREATE TEMP TABLE ft_mp (person_id bigint PRIMARY KEY)")
    if pids:
        execute_values(cur, "INSERT INTO ft_mp VALUES %s", [(p,) for p in pids])
    pg = ("(SELECT gender FROM person_gender g WHERE g.person_id = m.person_id)"
          if _hasTable(cur, "person_gender") else "NULL::text")
    # ! THE STRAY TEST IS link_profile_school's: an anet profile on its own
    #   seed, every athletes row and every result under its id its own.
    cur.execute(f"""
        SELECT m.person_id,
               COALESCE(
                 (SELECT NULLIF(btrim(concat_ws(' ', a.first_name, a.last_name)), '')
                  FROM athletes a
                  WHERE a.person_id = m.person_id OR a.athlete_id = m.person_id
                  ORDER BY (COALESCE(btrim(a.first_name), '') <> ''
                         OR COALESCE(btrim(a.last_name), '') <> '') DESC LIMIT 1),
                 (SELECT NULLIF(btrim(x.athlete_name), '') FROM results x
                  WHERE x.person_id = m.person_id
                    AND NULLIF(btrim(x.athlete_name), '') IS NOT NULL LIMIT 1)),
               ARRAY(SELECT DISTINCT g FROM (
                   SELECT a.gender AS g FROM athletes a WHERE a.person_id = m.person_id
                   UNION ALL SELECT {pg}
                   UNION ALL SELECT upper(substring(x.team_slug from '_(m|f)_'))
                   FROM results x WHERE x.person_id = m.person_id
                     AND x.source = 'tfrrs') s WHERE g IN ('M', 'F')),
               (m.person_id < %(lo)s
                AND EXISTS (SELECT 1 FROM athletes a WHERE a.athlete_id = m.person_id
                              AND a.person_id = m.person_id)
                AND NOT EXISTS (SELECT 1 FROM athletes a WHERE a.person_id = m.person_id
                                  AND a.athlete_id <> m.person_id)
                AND NOT EXISTS (SELECT 1 FROM results r WHERE r.person_id = m.person_id
                                  AND NOT (r.source = 'anet' AND r.athlete_id = m.person_id))
                AND NOT EXISTS (SELECT 1 FROM results_tf r WHERE r.person_id = m.person_id
                                  AND NOT (r.source = 'anet' AND r.athlete_id = m.person_id))),
               (m.person_id >= %(lo)s AND m.person_id < %(hi)s
                AND NOT EXISTS (SELECT 1 FROM results r WHERE r.person_id = m.person_id
                                  AND r.source <> 'tfrrs')
                AND NOT EXISTS (SELECT 1 FROM results_tf r WHERE r.person_id = m.person_id
                                  AND r.source <> 'tfrrs'))
        FROM ft_mp m
    """, {"lo": MINT_LO, "hi": MINT_HI})
    facts = {p: (nm, list(g or []), STRAY if stray else MINTED if minted else CAREER)
             for p, nm, g, stray, minted in cur.fetchall()}
    runs = defaultdict(list)
    cur.execute("""
        SELECT r.person_id, r.result_id, r.date, r.time_seconds, r.source
        FROM results r JOIN ft_mp m ON m.person_id = r.person_id
        WHERE r.time_seconds > 0 AND r.time_seconds < 100000
    """)
    for p, rid, d, ts, src in cur.fetchall():
        runs[p].append(Run(rid, d, ts, src))
    return {p: Member(p, f[0], f[1], f[2], sorted(runs.get(p, []), key=lambda r: str(r.date)))
            for p, f in facts.items()}


def gather(cur, since, person=None):
    """({group: [Member]}, {group: [pair rows]}); the caller rolls back."""
    veto = set()
    if _hasTable(cur, "feed_twin_veto"):
        cur.execute("SELECT old_id FROM feed_twin_veto")
        veto = {r[0] for r in cur.fetchall()}

    def grouped(persons):
        ps = [p for p in pairRows(cur, since, persons)
              if p[1] not in veto and p[3] not in veto]
        return ps, groupsOf([(p[1], p[3]) for p in ps])

    if person is None:
        pairs, groups = grouped(None)
    else:
        # ! THE CHAIN, NOT ONE PERSON'S DAYS: Rachael's placeholder from
        #   Thundering Herd reaches the Queen City one only through the
        #   tfrrs person's other day, so the days grow until the group stops
        seen = {person}
        while True:
            pairs, groups = grouped(seen)
            groups = [g for g in groups if person in g]
            grown = set().union(seen, *groups)
            if grown == seen:
                break
            seen = grown
    mem = members(cur, sorted(set().union(*groups))) if groups else {}
    out_m, out_p = {}, {}
    for i, g in enumerate(sorted(groups, key=min)):
        out_m[i] = [mem[p] for p in sorted(g)]
        out_p[i] = [p for p in pairs if p[1] in g]
    return out_m, out_p


def judge(groups):
    return {i: decideGroup(ms) for i, ms in groups.items()}


# ------------------------------------------------------------------ #
#  WRITING -- link_profile_school.write's shape
# ------------------------------------------------------------------ #
MERGE_DDL = """
CREATE TABLE IF NOT EXISTS feed_twin_merge (
    old_id    bigint PRIMARY KEY,
    new_id    bigint NOT NULL,
    kind      text   NOT NULL,
    name      text,
    merged_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS feed_twin_veto (
    old_id    bigint PRIMARY KEY,
    vetoed_at timestamptz NOT NULL DEFAULT now()
)"""

# which rows of a mover move: a stray's own anet rows, a minted person's tfrrs
_MOVE = {STRAY: "r.source = 'anet' AND r.athlete_id = m.old_id",
         MINTED: "r.source = 'tfrrs'"}


def decisionsOf(groups, verdicts):
    """[(old_id, new_id, kind, name)] for every matched group."""
    out = []
    for i, v in verdicts.items():
        if v.target is None:
            continue
        name = next((m.name for m in groups[i] if m.name), None)
        for m in groups[i]:
            if m.pid in v.movers:
                out.append((m.pid, v.target, m.kind, name))
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
        cur.execute("SELECT count(*) FROM feed_twin_merge")
        before = cur.fetchone()[0]
        if decisions:
            execute_values(cur, """
                INSERT INTO feed_twin_merge (old_id, new_id, kind, name)
                VALUES %s ON CONFLICT (old_id) DO NOTHING""", decisions)
        cur.execute("SELECT count(*) FROM feed_twin_merge")
        out["decisions new"] = cur.fetchone()[0] - before
        # ! A TARGET CAN MOVE TOO: follow its redirect
        cur.execute("SELECT old_id, new_id FROM feed_twin_merge")
        for old_id, new_id in cur.fetchall():
            moved = follow(cur, new_id)
            if moved and moved != old_id:
                cur.execute("UPDATE feed_twin_merge SET new_id = %s WHERE old_id = %s",
                            (moved, old_id))
        cur.execute("DROP TABLE IF EXISTS ft_map")
        cur.execute("""CREATE TEMP TABLE ft_map AS
                       SELECT old_id, new_id, kind FROM feed_twin_merge
                       WHERE old_id NOT IN (SELECT old_id FROM feed_twin_veto)""")
        for sport, table in TABLES:
            n = 0
            for kind, cond in _MOVE.items():
                cur.execute(f"""
                    INSERT INTO person_link_log (sport, result_id, from_person,
                                                 to_person, rule)
                    SELECT %s, r.result_id, m.old_id, m.new_id, %s
                    FROM   {table} r JOIN ft_map m ON r.person_id = m.old_id
                    WHERE  m.kind = %s AND {cond}
                    ON CONFLICT DO NOTHING""", (sport, RULE, kind))
                cur.execute(f"""
                    UPDATE {table} r SET person_id = m.new_id
                    FROM   ft_map m
                    WHERE  r.person_id = m.old_id AND m.kind = %s AND {cond}""", (kind,))
                n += cur.rowcount
            out[f"{sport} rows moved"] = n
        cur.execute("""
            UPDATE athletes a SET person_id = m.new_id
            FROM   ft_map m
            WHERE  m.kind = 'stray' AND a.athlete_id = m.old_id AND a.person_id = m.old_id""")
        out["athletes rows repointed"] = cur.rowcount
        cur.execute("""
            INSERT INTO person_redirect (old_id, new_id, n_probes)
            SELECT old_id, new_id, 0 FROM ft_map
            ON CONFLICT (old_id) DO UPDATE SET new_id = EXCLUDED.new_id
            WHERE person_redirect.new_id <> EXCLUDED.new_id""")
        out["redirects written"] = cur.rowcount
    conn.commit()
    return out


def undo(conn, which):
    """which: 'all' or one old id (that one is also vetoed)."""
    with conn.cursor() as cur:
        if not _hasTable(cur, "feed_twin_merge"):
            print("[feed-twin] nothing to undo")
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
            FROM   feed_twin_merge m
            WHERE  m.kind = 'stray' AND a.athlete_id = m.old_id
              AND  a.person_id = m.new_id {mcond}
        """, {"one": one})
        back["athletes"] = cur.rowcount
        if _hasTable(cur, "person_redirect"):
            cur.execute(f"""
                DELETE FROM person_redirect p USING feed_twin_merge m
                WHERE p.old_id = m.old_id AND p.new_id = m.new_id {mcond}
            """, {"one": one})
        cur.execute(f"DELETE FROM person_link_log l WHERE l.rule = %(rule)s {cond}",
                    {"rule": RULE, "one": one})
        cur.execute(f"DELETE FROM feed_twin_merge m WHERE true {mcond}", {"one": one})
        if one is not None:
            cur.execute("INSERT INTO feed_twin_veto (old_id) VALUES (%s) "
                        "ON CONFLICT DO NOTHING", (one,))
    conn.commit()
    return back


# ------------------------------------------------------------------ #
#  THE REPORT
# ------------------------------------------------------------------ #
def _clock(ts):
    if ts is None:
        return "-"
    m, s = divmod(float(ts), 60)
    return f"{int(m)}:{s:05.2f}"


def report(groups, pairs, verdicts, show):
    from collections import Counter
    reasons = Counter(v.reason for v in verdicts.values())
    print(f"[feed-twin] {len(verdicts):,} groups of persons holding one run in "
          f"both feeds:")
    for why, n in reasons.most_common():
        print(f"    {n:>8,}  {why}")
    shown = 0
    for i, v in sorted(verdicts.items(), key=lambda kv: (kv[1].reason != MATCH, kv[0])):
        if shown >= show:
            break
        shown += 1
        print(f"\n  {v.reason}")
        for m in groups[i]:
            tag = ("TARGET" if m.pid == v.target else
                   "moves" if m.pid in v.movers else "")
            print(f"    /athlete/{m.pid} {m.name or 'Unknown'} "
                  f"{'/'.join(sorted(m.genders)) or '?'} {m.kind} "
                  f"{len(m.runs)} XC rows  {tag}")
        for t_rid, t_pid, a_rid, a_pid, d, t_ts, a_ts in pairs[i][:6]:
            print(f"      {d}  tfrrs {t_rid} ({t_pid}) {_clock(t_ts)}  =  "
                  f"anet {a_rid} ({a_pid}) {_clock(a_ts)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--person", type=int, help="only the group of this person")
    ap.add_argument("--seasons", type=int, default=1,
                    help="academic years back from this one (default: this one)")
    ap.add_argument("--show", type=int, default=40)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--undo", metavar="ID|all")
    a = ap.parse_args()
    from database import getConn
    with getConn() as conn:
        if a.undo:
            print(f"[feed-twin] undone {a.undo}: {undo(conn, a.undo)}")
            return
        since = seasonStart(a.seasons)
        print(f"[feed-twin] cross country rows since {since}", flush=True)
        with conn.cursor() as cur:
            groups, pairs = gather(cur, since, a.person)
        conn.rollback()                          # temp tables only
        verdicts = judge(groups)
        report(groups, pairs, verdicts, a.show)
        decisions = decisionsOf(groups, verdicts)
        if not a.write:
            print(f"\n[feed-twin] DRY RUN: {len(decisions):,} persons would join; "
                  f"--write to join them")
            return
        out = write(conn, decisions)
        print(f"\n[feed-twin] {out}; logged in person_link_log rule '{RULE}' "
              f"(--undo <id> | all). The next 04c flags the tfrrs copies.")


if __name__ == "__main__":
    main()
