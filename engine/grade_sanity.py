"""
grade_sanity.py -- work out what grade an athlete-season should say.

THE SPEC, IN ONE PLACE
    1. A SEASON IS AN ACADEMIC YEAR. Autumn and the following spring are one
       school year and one grade. The reasoning happens on that grouping.

    2. ENOUGH CORROBORATION -> KEEP THE REPEATED GRADE. Corroboration is
       counted in RACES, not rows: a race scraped by two feeds is one race
       and one vote. Counting rows made duplicate coverage into evidence. A value that recurs
       across the year is the grade. This is not a majority vote: two races
       saying 9 and one saying 8 is a typo, not a contest.

    2b. A SEASON HOLDING BOTH NUMBERS AND CLASS WORDS IS ONE SEASON, NOT A
       CONTEST. The majority KIND wins and the minority stops voting.

       ★ NUMBERS LOSE BY BEING SPLIT, NOT BY BEING WRONG. A season reading
         9 twelve times, 10 ten times and Fr fifteen times hands the verdict
         to Fr -- fifteen beats twelve -- even though twenty-two races said a
         number and fifteen said a word. Counting per VALUE lets a word win
         a season the numbers actually own, because a word concentrates on
         one spelling while numbers spread across two.

       ⚠ A BARE Fr/So/Jr/Sr IS AMBIGUOUS: 9-12 in high school, 13-16 in
         college. A number is not. So a tie goes to the number, and a season
         with NO number keeps its words -- which is what leaves a genuine
         collegian alone.

    3. ONE OR TWO ODD VALUES -> FILL THEM FROM THE CORROBORATED ONE. They are
       corrections, not conflicts, and the season keeps its grade.

    3b. A GRADE ADVANCES ONE PER YEAR, AND A SEASON THAT DISAGREES BY MORE
       THAN ONE IS THE ONE THAT IS WRONG. Rule 3 fills odd values from the
       corroborated grade WITHIN a season. The same fact holds ACROSS seasons
       and nothing was using it: a corroborated 6 sitting between two
       corroborated 9s is a typo that happened to be typed twice.

    2c. A BARE CLASS WORD LEANS COLLEGE, AND ONLY EVIDENCE OVERRULES IT.
       §1.6: Fr/So/Jr/Sr are AMBIGUOUS -- 9-12 in high school, 13-16 in
       college -- and only the tfrrs eligibility spelling (FR-1 ... SR-4) is
       definite. A bare word therefore stays college by default. Two things
       can overrule it, in order: the athlete's OWN other seasons, and
       failing that, the company they kept. A race whose graded entrants are
       overwhelmingly ninth to twelfth graders is a high school race, and the
       class words in it are that feed's spelling of 9-12.

    4. NOT ENOUGH TO CORROBORATE ANYTHING -> THE LEVEL OF THE RACES THEY RAN.
       When the athlete cannot say, the field says.

    4b. AN EVENT NOBODY GRADED IS NOT A SCHOOL RACE -> PRO. Not "too few
       grades to be sure" -- NO grades at all, which is a different fact and
       a decisive one. Schools grade their runners; open road races do not.

    5. MORE THAN TWO YEARS AT THE SAME GRADE -> THE RECORD IS STALE, MARK PRO.
       Nobody is a senior three times, and nobody is a sophomore three times
       either. A grade that stops advancing stopped being updated, which
       usually means the athlete left school and kept racing.

    5c. A SEASON THAT CONTRADICTS ITSELF IS NOT EVIDENCE -> NUKE IT. When a
       season holds BOTH numbers and class words and the WORDS win, the two
       spellings disagree about what the athlete is and the count is deciding
       it. A count of spellings is scrape coverage, not evidence about a
       grade, so the season gets no verdict at all rather than the winner's.

    5d. AND A FIELD MOSTLY MADE OF NUKED SEASONS CANNOT VOUCH FOR THE REST.
       If 70% of an event's graded entrants were nuked, the remaining
       uncertain ones -- field, bare_class, bare_field -- lose their verdicts
       too. A corroborated grade survives: it never depended on the field.

    5e. ONE RACE, ONE SEASON, ONE BARE WORD -> NOTHING. A class word is
       ambiguous by §1.6, and a season with a single race and no other season
       anywhere has nothing that could disambiguate it. The collegiate lean
       would be doing all the work, so the season gets no verdict instead.

    7. AND A LEVEL WE BELIEVE IS NOT THE SAME AS A LEVEL WE CAN STAKE A
       NATIONAL BOARD ON. Every verdict carries a TRUST, and there are three
       outcomes rather than two:

         trusted     rated, ranked, and counted toward the pool's 100 point
         untrusted   rated and shown on the athlete's own page, but kept off
                     the boards -- the level is probably right and the
                     evidence behind it is thin
         excluded    no pool at all, because there is no level to pool by

       ★ THE LINE BETWEEN THE LAST TWO IS WHETHER A LEVEL EXISTS. no_evidence,
         contradicted, thin_field and lone_word have none, so there is nothing
         to rate against. A one-race season with a field verdict HAS a level;
         one race is simply not enough to put someone on a national list.

    6. NOTHING TO SAY -> SAY SO. An athlete-season none of the rules above
       could reach gets an EXPLICIT verdict of no evidence, rather than no
       row. Absence and ignorance look identical to a LEFT JOIN, and the
       consumer that has to tell them apart is the one that gets it wrong.

    5b. A SCHOOL GRADE AFTER COLLEGIATE ELIGIBILITY -> PRO. Nobody goes from
       collegiate senior back to twelfth grade. A season claiming a school
       grade 1-12 AFTER the athlete raced with a tfrrs class+eligibility grade
       is not a school season, whatever its own rows say.

! THE ROW IS WRITTEN UNDER THE ACADEMIC YEAR. `season` in this table is the
  July-start school year, not the calendar year, and every consumer must look
  it up that way.

  ★ WHY THIS CHANGED, AND WHAT THE OLD WAY COST. The row used to be written
    under the CALENDAR year -- one verdict spread across the two calendar
    years its academic year touched, later verdict winning any collision.
    That was done so consumers keyed on the calendar year would find it.

    But a calendar year genuinely holds TWO grades. An athlete is grade 8
    through spring and grade 9 from September, and both halves are calendar
    2024. Collapsing them meant the spring half inherited the autumn's grade:
    ms in one half of the season, hs in the other, one athlete split into two
    half-solved unknowns.

    Measured: 2,979 athlete-seasons split hs/ms, 87% of every hs/ms split in
    the corpus, and the whole tail was the same shape -- 7->8, 6->7, 5->6.
    Every ms/hs-crossing pair.

  ⚠ AND THE CONSUMERS HAD ALREADY DIVERGED. panels.py and
    build_ranking_results.py joined this table on the ACADEMIC year and said
    so in their comments; only the engine's poolOf used the calendar year.
    The site and the engine were reading one table on two different keys.
    Academic is the key both now use.

⚠ ONE VERDICT, ONE ROW. An academic year no longer produces two rows, so the
  graduating collision -- a calendar year receiving two verdicts -- cannot
  arise. The reasoning and the key are the same clock.

⚠ `season=` IN poolOf IS STILL THE CALENDAR YEAR, and must stay so:
  pro_athlete_season is built on it. speed_ratings.poolOf therefore keeps two
  keys, one per table. season_year.py's "DO NOT feed this to poolOf's season="
  warning still stands -- it is about that parameter, not about this table.

⚠ normGrade IS STILL WIDER THAN GRADE_TO_LEVEL, AND KNOWINGLY SO. Its
  free-text fallback keeps values no level can be derived from -- relay
  letters A-D, age bands like '13-14', 'UNA', 'GR', and above all '-', which
  is ~18.1M anet TF rows meaning ABSENT.

  Every one of those behaves the way graduation years did: it can be
  corroborated, it suppresses the field rule by making a season look already
  decided, and -- being constant by construction -- it trips the stale rule
  from an athlete's third season. '-' is ~16x the volume of the grad-year
  problem this module was just fixed for.

  Deliberately NOT changed here: making the fallback NULL is a domain change
  with a far larger blast radius than the numeric narrowing, and it wants its
  own measurement first. Size it with:

      SELECT normGrade(grade) AS g, count(*)
      FROM   (SELECT grade FROM results
              UNION ALL SELECT grade FROM results_tf) x
      WHERE  gradeLevel(normGrade(grade)) IS NULL
        AND  normGrade(grade) IS NOT NULL
      GROUP  BY 1 ORDER BY 2 DESC LIMIT 40;

⚠ ONE VERDICT, ONE ROW, ONE CLOCK. An academic year no longer produces two
  rows, so the graduating collision -- a calendar year receiving two verdicts
  -- cannot arise.
"""

import os
import re
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, "scripts")
for _p in (_HERE, _ROOT, os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)


# ------------------------------------------------------------------ #
#  CONSTANTS
# ------------------------------------------------------------------ #

# * TWO RACES CORROBORATE, ONE DOES NOT. A single entry is one unverified
#   claim; a second one agreeing is a fact about the season rather than about
#   one row's typing.
MIN_CORROBORATION = 2

# * MORE THAN TWO YEARS AT ONE GRADE IS NOT A GRADE. Redshirts, medical years
#   and repeated years make a second and even a third year believable; a
#   fourth is a record that stopped being maintained.
MAX_YEARS_AT_GRADE = 2

# An event needs this many graded entries before its level means anything.
MIN_FIELD_GRADED = 4
# ★ AN OPEN RACE'S GRADED FEW DO NOT SPEAK FOR ITS UNGRADED MANY (owner,
#   2026-10-27: Ethan Weiner, Cambridge Running, never a numeric grade,
#   pooled high school and ranked nation #6). At an open meet the high
#   schoolers carry grades and the adults carry none, so rule 4's mode over
#   the GRADED entrants called every race of his 'hs' -- 77 of 83 runners
#   ungraded at Wayland, 32 of 33 at the GBTC Invite. An event where at
#   least this share of the entrants has no level casts no vote for an
#   UNGRADED runner in it; a graded runner there still votes their field's
#   level, and an event nobody graded still says 'pro' as before. With no
#   vote, the season falls to what its team says (a club's gradeless row
#   is professional; a school's is that school's).
OPEN_FIELD_SHARE = 0.5

# * FORGIVING BY ONE GRADE, AND ONLY BY ONE. Implied class year is
#   `acad + (12 - grade)` and is constant for a real athlete. A drift of one
#   is ordinary: a grade is recorded per race and is sometimes a year stale,
#   an athlete can repeat a year, and a season near the seam can read either
#   side. A drift of two is not a stale grade, it is a wrong one.
MAX_GRADE_DRIFT = 1

# * SEVENTY PER CENT, AND APPLIED ONCE. Nuking an event's remainder shrinks
#   the fields rule 4 reads, which can make OTHER events look ungraded, which
#   would cascade. One pass, a high bar, and no iteration.
NUKED_FIELD_SHARE = 0.70

# * TWO RACES TO BE TRUSTED ON A BOARD. One race can be a mismeasured course,
#   a mistyped time or a field that voted wrong, and nothing contradicts it.
#   Two agree or they do not.
#
#   This mirrors a distinction the rating code already draws:
#   pair_write_results.poolMeanPerGroup keeps 2-race athlete-seasons OUT of
#   pool_mean while still rating them -- "the anchor set, not the rated set".
#   Trust is that idea carried one step further, to the boards.
MIN_TRUSTED_RACES = 2

# ⚠ AND THE RACE COUNT IS THE WHOLE TEST -- NO METHOD LIST.
#
#   The first draft also distrusted the field-derived verdicts, on the
#   reasoning that a level inferred from the company an athlete kept is
#   weaker than one they stated themselves. The volumes said otherwise: in
#   the last run `field/ms` alone was 483,555 seasons and `bare_field`
#   2,113,684. Distrusting those would empty the middle school boards and
#   undo the very pass that got the College board right.
#
#   A method says where a verdict came from; it does not say how much of it
#   there is. Colussi is not wrong because his verdicts are field verdicts --
#   plenty of correct ones are -- he is wrong because each rests on a single
#   race. That is what this measures, and nothing else.

# Verdicts that rest on the field rather than on the athlete's own grades.
# A corroborated grade never depended on the company they kept, so 5d leaves
# it alone; so does a pro verdict, which is a claim about eligibility.
_CLASS_ORDER = {"FR": 1, "SO": 2, "JR": 3, "SR": 4}


def _gradeStep(grade):
    """('n', 10) for a school grade, ('c', 2) for SO / SO-2 / Sophomore,
    None for anything else. What "one year on" can be tested against."""
    g = str(grade or "").strip().upper()
    if g.isdigit():
        return ("n", int(g))
    for word, n in _CLASS_ORDER.items():
        if g.startswith(word):
            return ("c", n)
    return None


def _advances(prev, cur, years=1):
    """Is `cur` `years` seasons after `prev` for one athlete: the grade
    that many years on (10 after 9, JR after SO, FR after 12; 12 two
    years after 10), or the same college or pro level again (a Senior-5
    is still college)."""
    a, b = _gradeStep(prev.get("grade")), _gradeStep(cur.get("grade"))
    if a and b:
        # two readable grades: only the step forward counts; a repeat or
        # a step back is the very disagreement trust exists to catch
        if a[0] == b[0] and b[1] == a[1] + years:
            return True
        return a == ("n", 12) and b == ("c", years)
    lp, lc = prev.get("level"), cur.get("level")
    return bool(lp) and lp == lc and lp in ("college", "pro")


_LEVEL_RANK = {"elem": 0, "ms": 1, "hs": 2, "college": 3, "pro": 4}


def _contradicts(prev, cur, years=1):
    """Does `cur` disagree with the trusted season `years` before it:
    two readable grades that do not advance, or a level that goes back or
    skips one (ms straight to college -- the Colussi shape). Nothing
    readable to compare is no contradiction."""
    a, b = _gradeStep(prev.get("grade")), _gradeStep(cur.get("grade"))
    if a and b:
        return not _advances(prev, cur, years)
    lp = _LEVEL_RANK.get(prev.get("level") or "")
    lc = _LEVEL_RANK.get(cur.get("level") or "")
    if lp is None or lc is None:
        return False
    return lc < lp or lc > lp + 1


def trustByProgression(acad, race_count, min_races=MIN_TRUSTED_RACES,
                       open_ay=None):
    """Stamp v['trust'] on every academic-year verdict, in year order.

    high  when the season has min_races or more, OR when the previous
          academic year of the same athlete is trusted and this season
          advances it (_advances). A vouched season can vouch for the
          next, so a career of one-race seasons that step 9, 10, 11, 12
          is trusted throughout, and a run of single field verdicts that
          jump levels (Dominic Colussi, above) is not.
    low   otherwise: rated and shown, off the boards.
    Returns (n_low, n_vouched)."""
    # ★ THE VOUCHER IS THE LAST TRUSTED SEASON, NOT ONLY LAST YEAR (owner,
    #   2026-10-07, agreeing to the +1 rule). A runner with no results last
    #   year -- injured, a year off, a season nobody scraped -- had nothing
    #   to vouch for them, and a trusted grade 9 who comes back as a one-race
    #   grade 11 is exactly as corroborated by the calendar as a grade 10
    #   would have been. So the grade must have moved on by the years that
    #   have actually passed. A thin season between them does not break the
    #   chain: it is low because nothing vouched for IT, not because it
    #   contradicts anything.
    n_low = n_vouched = 0
    last_trusted = {}
    for key in sorted(acad):
        v = acad[key]
        pid, ay = key
        if race_count.get(key, 0) >= min_races:
            v["trust"] = "high"
            last_trusted[pid] = (ay, v)
            continue
        prev_ay, prev = last_trusted.get(pid, (None, None))
        if prev is not None and _advances(prev, v, ay - prev_ay):
            v["trust"] = "high"
            v["trust_by"] = "progression"
            last_trusted[pid] = (ay, v)
            n_vouched += 1
        elif ay == open_ay and not (
                prev is not None and _contradicts(prev, v, ay - prev_ay)):
            # ★ THE SEASON IN PROGRESS IS NOT SHORT OF RACES, IT IS YOUNG
            #   (owner, 2026-10-08: "a lot of the rated not ranked rules
            #   fire bcs they haven't run enough races, on basically
            #   everybody. Completely fucks the boards"). Vouching needs a
            #   readable grade on both sides, so a first season (every
            #   freshman), a field verdict without a grade, or a runner
            #   whose last trusted season said only "hs" could not be
            #   vouched for and sat off the boards until a second race.
            #   In the open academic year one race is enough UNLESS it
            #   contradicts the last trusted season (_contradicts: grades
            #   that do not advance, a level that goes back or skips one).
            #   Once the year closes, the two-race rule judges it as before.
            v["trust"] = "high"
            v["trust_by"] = "season_open"
            n_vouched += 1
        else:
            v["trust"] = "low"
            n_low += 1
    return n_low, n_vouched


def splitStatements(sql):
    """Top-level statements of a SQL script: split on ';' outside
    $$-quoted bodies, single quotes and -- comments. The function bodies
    in _BUILD carry semicolons of their own."""
    out, buf, i, n = [], [], 0, len(sql)
    in_dollar = in_quote = False
    while i < n:
        c = sql[i]
        if in_dollar:
            if sql.startswith("$$", i):
                buf.append("$$"); i += 2; in_dollar = False; continue
        elif in_quote:
            if c == "'":
                in_quote = False
        elif sql.startswith("--", i):
            j = sql.find("\n", i)
            j = n if j < 0 else j
            buf.append(sql[i:j]); i = j; continue
        elif sql.startswith("$$", i):
            buf.append("$$"); i += 2; in_dollar = True; continue
        elif c == "'":
            in_quote = True
        elif c == ";":
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []; i += 1; continue
        buf.append(c); i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def runTimed(cur, sql, label="", slow=1.0):
    """Execute a script statement by statement, printing the ones that
    take longer than `slow` seconds with their first meaningful line."""
    import time as _t
    t_all = _t.time()
    for stmt in splitStatements(sql):
        t0 = _t.time()
        cur.execute(stmt)
        dt = _t.time() - t0
        if dt >= slow:
            head = next((ln.strip() for ln in stmt.splitlines()
                         if ln.strip() and not ln.strip().startswith("--")), "?")
            print(f"    [{dt:7.1f}s] {head[:90]}", flush=True)
    print(f"    [{_t.time() - t_all:7.1f}s] {label} total", flush=True)


# ★ COPY, NOT INSERT ... VALUES (2026-10-25, run log: 349 s from "written as
#   31,638,512 academic-year rows" to the end -- the write and nothing else).
#   execute_values formats every row into an INSERT in Python and the
#   server parses each one back; COPY streams tab-separated lines the server
#   reads as data. The values are the same values: copyText is COPY's own
#   text format -- \N for NULL, and the four characters it reads as escapes
#   (backslash, tab, newline, carriage return) escaped -- so every string
#   arrives byte for byte as the INSERT delivered it, and a Python int is
#   its digits either way.
def copyText(v):
    """One field of COPY's text format."""
    if v is None:
        return "\\N"
    s = str(v)
    if "\\" in s or "\t" in s or "\n" in s or "\r" in s:
        s = (s.replace("\\", "\\\\").replace("\t", "\\t")
              .replace("\n", "\\n").replace("\r", "\\r"))
    return s


def copyLines(rows):
    """The rows as COPY text lines, one str per row. Strings repeat (a
    verdict's method, its level) so each is escaped once; ints are not
    cached -- there are 31M person ids."""
    cache = {None: "\\N"}

    def field(v):
        if type(v) is int:
            return str(v)
        s = cache.get(v)
        if s is None:
            s = cache[v] = copyText(v)
        return s

    for r in rows:
        yield "\t".join([field(v) for v in r]) + "\n"


class _CopySource:
    """A file copy_expert can read: copyLines, a chunk at a time, so 31M
    rows never sit in memory as one string."""

    def __init__(self, rows, encoding="utf-8", chunk_rows=100_000):
        self._lines = copyLines(rows)
        self._enc = encoding
        self._chunk = chunk_rows
        self._buf, self._pos = b"", 0

    def read(self, size=-1):
        if self._pos >= len(self._buf):
            part = []
            for line in self._lines:
                part.append(line)
                if len(part) >= self._chunk:
                    break
            self._buf, self._pos = "".join(part).encode(self._enc), 0
            if not self._buf:
                return b""
        if size is None or size < 0:
            size = len(self._buf) - self._pos
        out = self._buf[self._pos:self._pos + size]
        self._pos += len(out)
        return out


def _copyRows(cur, table, columns, rows):
    """COPY rows (tuples, in `columns` order) into `table`, encoded as the
    connection encodes everything else it sends."""
    enc = "utf-8"
    try:
        from psycopg2.extensions import encodings
        enc = encodings.get(cur.connection.encoding, enc)
    except Exception:                                            # noqa: BLE001
        pass
    cur.copy_expert(f"COPY {table} ({', '.join(columns)}) FROM STDIN",
                    _CopySource(rows, encoding=enc), size=1 << 20)


_UNCERTAIN_METHODS = ("field", "bare_class", "bare_field", "no_evidence")

# * THREE SEASONS TO OVERRULE ONE, NOT TWO. With two disagreeing seasons
#   there is no majority -- each is one vote and nothing says which is the
#   typo. Three is the smallest number that can point at the odd one out.
MIN_SEASONS_FOR_PROGRESSION = 3


# ! THE SEASON BOUNDARY IS NOT DEFINED HERE. season_year.py owns it, and this
#   module asks for the SQL form rather than retyping `substring(date,6,2) >=
#   '07'` twice in the query below. It said July while season_year said
#   October-for-TF and February-for-XC, and the engine's poolOf said July
#   again -- three hand-rolled copies of one boundary, none of which matched.
from season_year import seasonYearSqlInt

_ACAD = seasonYearSqlInt(None, "date")
# ! QUALIFIED, for a query that joins another table with a `date` column
#   (owner's run, 2026-09-29: rule 7's XC query joins `meets`, and a bare
#   `date` there is ambiguous -- 04_grade_sanity died after 2h 17m on it)
_ACAD_R = seasonYearSqlInt(None, "r.date")

# ================================================================== #
#  AGE BANDS -- issue #47, wired.
# ================================================================== #
#
# ★ WHAT THIS DEFENDS AGAINST. engine/age_band_grades.py decides, per
#   division, whether "11-12" is two GRADES or two AGES, and writes the rows
#   that mean AGES into age_band_result. Until something READ that table the
#   tool was a report: Sean McGorty still resolved to hs off a `11-12` in a
#   professional field and still rated 152.4 beside the 129.9 of the man who
#   beat him. This is the read.
#
# ! IT NULLS THE GRADE AT THE SOURCE, WHICH IS WHY IT GOES IN allraces AND
#   NOT IN A RULE. Every rule below counts, corroborates or compares grades;
#   a band left in place would corroborate under rule 2, make the season look
#   decided so the field rule (4) never runs, and -- being constant -- trip
#   the stale rule (5) from the third season. Removing it once, before any
#   rule sees it, is one change instead of five, and it cannot be forgotten
#   by a rule added later.
#
# ! AND IT NULLS raw_grade TOO. Rule 5b reads the unfolded spelling. A band
#   cannot match its FR-1/SR-4 pattern so nothing changes today, but leaving
#   a value in a column named "raw" that the folded column has disowned is
#   the kind of half-state a later rule reads by accident.
#
# ⚠ THE JOIN IS ALWAYS PRESENT, EVEN WHEN THE TABLE IS NOT. The stand-in is
#   an empty relation with the same alias and column, so the CASE in the
#   build is valid either way and there is ONE query shape to reason about
#   rather than two. A missing optional table must degrade, not crash --
#   loadCanonicalNames and speed_ratings_db._chairFilter both do this -- but
#   an unsubstituted token would leave `ab` undefined and raise, which is the
#   failure mode we want if the replace below is ever broken.
_AGE_BAND_TOKENS = ("/*AGEBAND_XC*/", "/*AGEBAND_TF*/")

# ⚠ THE TWO FRAGMENTS MUST EXPOSE THE SAME COLUMNS, AND THEY DID NOT. The
#   real one joined age_band_result whole -- (sport, result_id, person_id,
#   grade) -- while the stand-in exposed result_id alone. _BUILD's big SELECT
#   refers to `person_id` UNQUALIFIED, so the moment #47's table actually
#   existed the build failed with
#
#       column reference "person_id" is ambiguous
#
#   and grade_sanity died on the first pipeline run after age_band_grades
#   --write had ever been run. The stand-in path had been exercised for
#   months; the real one never had, so the two drifted with nothing to catch
#   it.
#
# ! SO THE REAL FRAGMENT NOW EXPOSES result_id AND NOTHING ELSE, matching the
#   stand-in exactly. That is also all the build wants from it -- the CASEs
#   only ask `ab.result_id IS NULL`. Qualifying every bare column in a
#   500-line SQL blob would have been the other fix, and a far bigger one to
#   get right. tests/test_grade_sanity_joins.py holds the two to the same
#   shape.
#
# ! THE PRIMARY KEY IS (sport, result_id), so filtering sport inside the
#   subquery is an index-served range, not a scan.
_AGE_BAND_REAL = ("""LEFT   JOIN (SELECT result_id FROM age_band_result
                           WHERE sport = '{sport}') ab
                      ON ab.result_id = r.result_id""")

_AGE_BAND_NONE = ("""LEFT   JOIN (SELECT NULL::bigint AS result_id
                          WHERE false) ab ON true""")


# _ageBandJoins
# Purpose:   the two LEFT JOINs that expose age_band_result to the build, or
#            empty stand-ins for a database where #47 has not been run.
# Output:    (xc_fragment, tf_fragment)
def _ageBandJoins(cur):
    cur.execute("SELECT to_regclass('public.age_band_result') IS NOT NULL AS ok")
    row = cur.fetchone()
    ready = bool(row["ok"] if isinstance(row, dict) else row[0])
    if not ready:
        print("[grade] age_band_result not found -- banded grades will be read "
              "as GRADES everywhere. Run engine/age_band_grades.py --write "
              "first (issue #47).")
        return _AGE_BAND_NONE, _AGE_BAND_NONE
    # ! SAID OUT LOUD, AND COUNTED FROM THE TABLE ITSELF. A filter that turns
    #   out to match nothing looks exactly like one that was never wired, and
    #   #47 spent a release in that state. Counting allraces instead would
    #   mix these in with every row whose source grade was already NULL.
    cur.execute("""SELECT count(*) AS n,
                          count(*) FILTER (WHERE sport = 'XC') AS xc,
                          count(*) FILTER (WHERE sport = 'TF') AS tf
                   FROM age_band_result""")
    r = cur.fetchone()
    n, xc, tf = ((r["n"], r["xc"], r["tf"]) if isinstance(r, dict)
                 else (r[0], r[1], r[2]))
    print(f"[grade] #47: {n:,} rows ({xc:,} XC, {tf:,} TF) carry a banded "
          f"grade in a division that writes AGES -- they lose it here")
    return (_AGE_BAND_REAL.format(sport="XC"),
            _AGE_BAND_REAL.format(sport="TF"))


# ================================================================== #
#  A NATIONAL TEAM'S GRADE IS NOT A GRADE
# ================================================================== #
#
# ★ WHY (owner, 2026-10-08, John Rivera, /athlete/12652858: Ole Miss FR-1
#   2017 to 2021, a professional since). His World Indoors rows of
#   2025-03-21, school 'Puerto Rico', carry grade '12', and his 2023 Worlds
#   row '10' -- a federation's column, not a school's. Two '12' rows
#   corroborated academic 2024 as a twelfth grader (rule 2), and only the
#   progression arithmetic (rule 3b) happened to turn that into 'graduated'.
#   The athlete page already refuses these (racecast/app.py _season_grade,
#   a860c29); the verdicts underneath it read them as the athlete's own.
#
# ! NULLED AT THE SOURCE, LIKE #47's BANDS AND FOR THE SAME REASON. Every
#   rule counts, corroborates or compares allraces' grade, grade_folded,
#   raw_grade or definite: corroboration (2), the fold and 5c (gradekind),
#   bare class (2c, both passes), the field (4), stale (5), college start
#   (5b, 5e, rule 8's graduation), thin field (5d). One CASE before any of
#   them runs is one change instead of nine, and a rule added later cannot
#   forget it. Rule 7 reads the results tables directly and asks the same
#   table (eliteFieldSeasons).
#
# ★ THE ROW STAYS; ONLY ITS GRADE GOES. A national-team race is still a
#   race the athlete ran, so it still counts in his races per season (5e,
#   trust) and his season still asks its field (rule 4). As an ENTRANT it
#   is ungraded: it does not vote on any field's level, and a field made of
#   nothing else -- a World Indoors final -- has no graded entrant and reads
#   'pro' through rule 4's own no-grade arm, which is what a senior
#   international championship is. The junk '12's used to make that same
#   field a high school race.
#
# ! ONE CLASSIFIER: pool_resolve.isNationalTeam, run in Python over the
#   distinct school strings of graded rows and stored. No SQL copy of the
#   list or of its suffix pattern exists to drift from it (pro_flag's seed
#   carries one, _NT_STRIP, for a different question).
# ⚠ UNLOGGED, NOT TEMP, like allraces: a temp table in the build's join
#   would make its 225M-row scans parallel-restricted.
_NT_TABLE = "ntschool"


# _nationalTeamSchools
# Purpose:   (re)build ntschool -- the exact school strings, on graded rows
#            of either results table, that pool_resolve.isNationalTeam names.
# Output:    how many strings it holds.
def _nationalTeamSchools(cur):
    from pool_resolve import isNationalTeam
    from psycopg2.extras import execute_values
    t0 = time.time()
    cur.execute("""SELECT DISTINCT school FROM results
                   WHERE  school IS NOT NULL AND grade IS NOT NULL
                   UNION
                   SELECT DISTINCT school FROM results_tf
                   WHERE  school IS NOT NULL AND grade IS NOT NULL""")
    rows = cur.fetchall()
    schools = [r["school"] if isinstance(r, dict) else r[0] for r in rows]
    nt = sorted(s for s in schools if isNationalTeam(s))
    cur.execute(f"DROP TABLE IF EXISTS {_NT_TABLE}; "
                f"CREATE UNLOGGED TABLE {_NT_TABLE} (school text PRIMARY KEY)")
    if nt:
        execute_values(cur, f"INSERT INTO {_NT_TABLE} (school) VALUES %s",
                       [(s,) for s in nt], page_size=5000)
    cur.execute(f"ANALYZE {_NT_TABLE}")
    print(f"[grade] national teams: {len(nt):,} of {len(schools):,} graded "
          f"school strings -- their rows lose the grade here "
          f"({time.time() - t0:.0f}s)")
    return len(nt)

_BUILD = f"""
    SET work_mem = '1GB';
    SET temp_buffers = '256MB';
    -- ! PARALLELISM FOR THE SCANS. Every heavy step here is a sequential
    --   scan or a hash aggregate over 225M rows -- exactly what parallel
    --   workers are for. The default of 2 leaves most of the machine idle
    --   during the hour this takes.
    --
    -- ⚠ TEMP TABLES CANNOT BE SCANNED IN PARALLEL, so this helps the reads
    --   of results/results_tf and not the aggregates over allraces. That is
    --   still the largest single cost.
    SET max_parallel_workers_per_gather = 8;
    SET parallel_setup_cost = 0;
    SET parallel_tuple_cost = 0;

    -- ★ EVERY FUNCTION BELOW IS PARALLEL SAFE, AND SAYS SO (2026-10-25,
    --   04_grade_sanity at 4,292 s on the critical path; owner: "you can
    --   multithread/core as necessary. You have freedom as long as it
    --   remains accurate and working").
    --
    -- ! WITHOUT THE LABEL THE SETTINGS ABOVE DID NOTHING FOR HALF THE STEP.
    --   CREATE FUNCTION defaults to PARALLEL UNSAFE, and one unsafe function
    --   anywhere in a statement makes Postgres plan the WHOLE statement on
    --   one core. It reads the labels before it inlines these SQL bodies, so
    --   being inlined does not rescue it. raceIdent kept the allraces build
    --   serial (420 s), gradeLevel kept rule 4 (826 s), rule 2c's field
    --   passes and rule 5f serial, and normGrade kept rule 7's staging
    --   serial (219 s). Checked on a scratch Postgres: EXPLAIN of one
    --   statement is a Seq Scan without the label and a Gather over a
    --   Parallel Seq Scan with it, and the allraces build on a 3.3M-row
    --   synthetic corpus went from 8.2 s to 3.5 s on four cores.
    --
    -- ⚠ THE LABEL IS A PROMISE, AND EACH BODY KEEPS IT: a CASE over TRIM,
    --   regexes, casts, round and hashtext, reading no table and writing
    --   nothing. A parallel plan runs the same expression on the same row in
    --   another process, so every value is the one the serial plan made. A
    --   function added here that reads a table or a setting must NOT carry
    --   the label.

    -- ============================================================== --
    --  GRADE NORMALISATION -- three helpers and a composition.
    --
    --  ★ THE DOMAIN IS normalize_distance.GRADE_TO_LEVEL, AND THIS IS A
    --    MIRROR OF IT. A mirror is only safe while it is NARROWER than the
    --    original. The old numeric branch was WIDER -- it accepted every
    --    integer -- so `2026`, `13`, `19` and `0` all became grades here
    --    that GRADE_TO_LEVEL cannot read.
    --
    --    That is not a cosmetic mismatch. A value that survives normGrade
    --    can be CORROBORATED (rule 2), can suppress the field rule by
    --    making a season look already-decided (rule 4), and -- because a
    --    graduation year is constant by construction -- will ALWAYS trip
    --    the stale rule from an athlete's third season (rule 5). It then
    --    lands in grade_fix.grade, where pool_resolve.resolvePool trusts
    --    it, overwrites the real grade with it, and suppresses the season
    --    verdict. One wide branch, four wrong outcomes.
    --
    --  ⚠ IF GRADE_TO_LEVEL's NUMERIC KEYS CHANGE, schoolGrade's REGEX MUST
    --    CHANGE WITH THEM. There is no way to enforce that from SQL, which
    --    is exactly the drift event_parse._MIN/_MAX_DISTANCE has against
    --    the fitter. Named here so it is at least loud.
    -- ============================================================== --

    -- schoolGrade: the numeric grades, and ONLY the numeric grades that
    -- GRADE_TO_LEVEL knows (1-12).
    --
    -- ! THE RANGE LIVES IN THE REGEX, NOT IN A `BETWEEN`. Two reasons, and
    --   both are about the cast:
    --     1. `TRIM(g)::int` raises on a 20-digit string. Postgres does not
    --        promise the order in which it evaluates a WHEN's operands, so
    --        `WHEN g ~ '^[0-9]+$' AND g::int BETWEEN 1 AND 12` is a cast
    --        that MIGHT run on unbounded input. A THEN, by contrast, is
    --        only ever reached when its WHEN was true.
    --     2. With the range in the pattern, the cast can only ever see a
    --        value already known to be 1-12. It cannot overflow and it
    --        cannot fail.
    --
    --   '^0*([1-9]|1[0-2])$' reads: any leading zeros, then either one
    --   digit 1-9, or a 1 followed by 0, 1 or 2. So '9', '09' and '12'
    --   pass; '0', '13', '19' and '2026' do not.
    -- ! THE ORDINAL TAIL IS PART OF THE DOMAIN, NOT NOISE. normalizeGrade
    --   strips 'th st nd rd . ' before testing, so '9th', '7t' and '12TH' all
    --   reach GRADE_TO_LEVEL. Without the tail here they fell to the free-text
    --   fallback and became '9TH' -- a value that corroborates, suppresses the
    --   field rule, and resolves to no level at all.
    --
    -- ⚠ '13-14' AND '11-12' CANNOT ENTER. Both contain a hyphen, which is not
    --   in the tail class, so neither matches. The bands are handled by
    --   bandGrade below and the age ranges are correctly rejected.
    CREATE OR REPLACE FUNCTION schoolGrade(g text) RETURNS text AS $$
        SELECT CASE
            WHEN TRIM(g) ~* '^0*([1-9]|1[0-2])[thsnrd. ]*$'
            THEN (regexp_replace(TRIM(g), '[^0-9].*$', ''))::int::text
        END
    $$ LANGUAGE sql IMMUTABLE PARALLEL SAFE;

    -- classGrade: the college class words, every spelling onto one key.
    -- 'Sr', 'SR' and 'SR-4' are one value. The eligibility digit in the
    -- tfrrs 'SR-4' form says nothing about level, so it is discarded.
    -- ! THE ALIAS TABLE IS normalize_distance._CLASS_ALIASES, TRANSCRIBED.
    --   Every spelling it knows, plus the tfrrs class+eligibility form. The
    --   trailing '.' and any case are stripped first, so 'Fr.', 'SENIOR' and
    --   'soph' all land.
    --
    -- ★ RS IS COLLEGE AND WAS MISSING. GRADE_TO_LEVEL maps 'RS' -- redshirt --
    --   to college, and this function did not know it, so 67 seasons of RS
    --   corroborated as free text and resolved to nothing.
    --
    -- ★ ANY ELIGIBILITY DIGIT, NOT THE FOUR TEXTBOOK PAIRS (2026-09-29,
    --   NCAA DI 2024: Dylan Schubert SR-5, Lucas Bons SR-5, Rikus Van
    --   Niekerk SO-3, Luke Marsanskis SR-5). tfrrs writes class and year of
    --   eligibility separately, so a redshirt is SO-3 or JR-4 and a fifth
    --   year SR-5. Only 'fr-1' .. 'sr-4' were listed, every other pair fell
    --   to NULL, the season went needy, and it came out in the high school
    --   pool: unrated, and spelled "Grade 12" on the page.
    CREATE OR REPLACE FUNCTION classWord(g text) RETURNS text AS $$
        SELECT CASE lower(rtrim(TRIM(g), '. '))
            WHEN 'fr' THEN 'FR' WHEN 'fresh'     THEN 'FR'
            WHEN 'freshman' THEN 'FR' WHEN 'freshmen' THEN 'FR'
            WHEN 'fr-1' THEN 'FR'
            WHEN 'so' THEN 'SO' WHEN 'soph'      THEN 'SO'
            WHEN 'sophomore' THEN 'SO' WHEN 'so-2' THEN 'SO'
            WHEN 'jr' THEN 'JR' WHEN 'junior'    THEN 'JR'
            WHEN 'jr-3' THEN 'JR'
            WHEN 'sr' THEN 'SR' WHEN 'senior'    THEN 'SR'
            WHEN 'sr-4' THEN 'SR'
            WHEN 'rs' THEN 'RS' WHEN 'redshirt'  THEN 'RS'
        END
    $$ LANGUAGE sql IMMUTABLE PARALLEL SAFE;

    CREATE OR REPLACE FUNCTION classGrade(g text) RETURNS text AS $$
        SELECT CASE
            WHEN lower(TRIM(g)) ~ '^(fr|so|jr|sr)-?[1-6]$'
            THEN upper(left(TRIM(g), 2))
            ELSE classWord(g)
        END
    $$ LANGUAGE sql IMMUTABLE PARALLEL SAFE;


    -- isDefiniteClass: does this row use the tfrrs CLASS+ELIGIBILITY form?
    --
    -- ★ §1.6, ENCODED. 'SR-4' can only be a fourth-year collegiate athlete --
    --   the digit is NCAA eligibility and high school has no such thing. A
    --   bare 'Sr' is a senior in either world. Rule 5b already leans on this
    --   distinction; rule 2c can now read it too.
    CREATE OR REPLACE FUNCTION isDefiniteClass(g text) RETURNS boolean AS $$
        SELECT upper(TRIM(g)) ~ '^(FR|SO|JR|SR)-[1-6]$'
    $$ LANGUAGE sql IMMUTABLE PARALLEL SAFE;

    -- The banded keys GRADE_TO_LEVEL carries verbatim. They are grades, not
    -- age ranges: '13-14' and '15-16' are ages and stay out.
    CREATE OR REPLACE FUNCTION bandGrade(g text) RETURNS text AS $$
        SELECT CASE TRIM(g)
            WHEN '7-8'   THEN '7-8'
            WHEN '9-10'  THEN '9-10'
            WHEN '11-12' THEN '11-12'
        END
    $$ LANGUAGE sql IMMUTABLE PARALLEL SAFE;

    -- isGradYear: DIAGNOSTIC ONLY. Nothing in the build calls this.
    --
    -- ★ IT EXISTS SO THE REJECTION CAN BE AUDITED WITHOUT A REDUNDANT
    --   BRANCH IN THE DECISION PATH. normGrade rejects 2026 because it is
    --   not 1-12, not because it is a year -- the decision needs no
    --   concept of "graduation year" at all. But "always audit what a
    --   filter discarded" needs one, so it lives here, out of the way:
    --
    --     SELECT count(*) FROM results WHERE isGradYear(grade);
    CREATE OR REPLACE FUNCTION isGradYear(g text) RETURNS boolean AS $$
        SELECT TRIM(g) ~ '^(19|20)[0-9][0-9]$'
    $$ LANGUAGE sql IMMUTABLE PARALLEL SAFE;

    -- normGrade: one spelling per grade, so counting works.
    --
    -- ! WHY THIS IS A CASE AND NOT A COALESCE. A COALESCE of the helpers
    --   would read better and be WRONG: one branch has to stop with
    --   NOTHING, deliberately. A numeric value that schoolGrade rejected
    --   must become NULL, and must NOT fall through to the free-text
    --   fallback below -- which would hand back '2026' unchanged and
    --   reintroduce the entire bug through the back door. COALESCE cannot
    --   express "stop here, with nothing". An ordered CASE can.
    CREATE OR REPLACE FUNCTION normGrade(g text) RETURNS text AS $$
        SELECT CASE
            WHEN g IS NULL OR TRIM(g) = ''  THEN NULL
            WHEN schoolGrade(g) IS NOT NULL THEN schoolGrade(g)
            WHEN classGrade(g)  IS NOT NULL THEN classGrade(g)
            -- ★ THE FIX. Numeric, and not a school grade -> NULL. Every
            --   rule below filters `WHERE grade IS NOT NULL`, so one NULL
            --   here removes the value from rules 2, 4 and 5 at once.
            WHEN bandGrade(g)   IS NOT NULL THEN bandGrade(g)
            -- ★ THE DOMAIN IS CLOSED. Anything the three helpers above do not
            --   recognise is NOT A GRADE, and saying so is the whole point.
            --
            --   The old fallback returned upper(TRIM(g)) and admitted 1.6M
            --   seasons of things GRADE_TO_LEVEL cannot read -- 1.5M of them
            --   just '-', anet's ABSENT sentinel, plus '?', 'NA', 'UN', age
            --   bands and relay letters. Every one behaved like a grade: it
            --   corroborated under rule 2, it made the season look decided so
            --   rule 4 never ran, it was constant so rule 5 called it stale,
            --   and resolvePool overwrote the row's real grade with it.
            --
            --   A season that carries only these is now NEEDY, which hands it
            --   to the rule built for exactly that case: the level of the
            --   fields it raced, or 'pro' when no entrant anywhere carried a
            --   grade. An absence is answered by evidence rather than by
            --   being mistaken for a value.
            ELSE NULL
        END
    $$ LANGUAGE sql IMMUTABLE PARALLEL SAFE;

    -- Grade to level, poolFor's boundaries.
    -- Grade to level, poolFor's boundaries. Mirrors GRADE_TO_LEVEL key for
    -- key -- including RS and the three banded keys, both of which were
    -- missing and therefore resolved to no level.
    CREATE OR REPLACE FUNCTION gradeLevel(g text) RETURNS text AS $$
        SELECT CASE
            WHEN g IS NULL THEN NULL
            WHEN g ~ '^[0-9]+$' AND g::int BETWEEN 1  AND 5  THEN 'elem'
            WHEN g ~ '^[0-9]+$' AND g::int BETWEEN 6  AND 8  THEN 'ms'
            WHEN g ~ '^[0-9]+$' AND g::int BETWEEN 9  AND 12 THEN 'hs'
            WHEN g IN ('FR','SO','JR','SR','RS')             THEN 'college'
            WHEN g = '7-8'                                   THEN 'ms'
            WHEN g IN ('9-10','11-12')                       THEN 'hs'
            ELSE NULL
        END
    $$ LANGUAGE sql IMMUTABLE PARALLEL SAFE;

    -- raceIdent: ONE STRING PER ACTUAL RACE, so a race scraped twice
    -- counts once.
    --
    -- ★ THE FEEDS DISAGREE ABOUT EVERYTHING EXCEPT THE CLOCK. Amelio Chio's
    --   single 10 km appears as meet 665942 div 1 event 118 dated 05-09 and
    --   as meet 94719 event 5992169 dated 05-07 -- different meet, different
    --   division, different event, dates two days apart. The ONLY field the
    --   two copies agree on is time_seconds = 1783.29.
    --
    --   So the race key cannot be built from the race's own identifiers, and
    --   (date, time_seconds) is not enough either. Within one athlete, one
    --   academic year and one grade value, an identical finishing time to
    --   the hundredth is the same race.
    --
    -- ⚠ TIME CAN BE NULL, and count(DISTINCT ...) skips NULLs -- a season of
    --   untimed rows would count zero races and look needy. When the clock
    --   is missing there is nothing better than the race's own key, so it
    --   falls back to that and cross-feed copies stay separate. That is the
    --   safe direction: it under-merges rather than over-merges.
    -- ! IT RETURNS bigint, NOT text, AND THAT IS A THIRD OF THE RUNTIME.
    --   This value is GROUP BY'd and count(DISTINCT)'d across 225M rows in
    --   four places. As text every one of those hashed and compared a
    --   string; as an integer they are machine words.
    --
    --   Times are packed as hundredths of a second, which is the resolution
    --   the two feeds agree to. The +1 keeps a genuine 0.00 distinguishable
    --   from the fallback, and the sign keeps the two spaces from colliding:
    --   a timed race is POSITIVE, a fallback key NEGATIVE.
    --
    -- ⚠ hashtext() IS A HASH, SO COLLISIONS ARE POSSIBLE. It is only used
    --   where the clock is missing, and a collision there merges two untimed
    --   races of one athlete in one season -- the same under-count the text
    --   version's fallback already risked, at 1 in 4 billion instead of
    --   never. The timed path, which is almost every row, is exact.
    -- ⚠ DROPPED FIRST, NOT REPLACED. CREATE OR REPLACE cannot change a
    --   function's RETURN TYPE, and this one went from text to bigint. A
    --   database that ran the old version keeps the old signature until it
    --   is dropped by name.
    DROP FUNCTION IF EXISTS raceIdent(double precision, text, bigint, bigint);
    CREATE OR REPLACE FUNCTION raceIdent(t double precision, d text,
                                         m bigint, e bigint)
        RETURNS bigint AS $$
        SELECT COALESCE(round(t::numeric * 100)::bigint + 1,
                        -(abs(hashtext(d || ':' || m::text || ':'
                                       || e::text))::bigint + 1))
    $$ LANGUAGE sql IMMUTABLE PARALLEL SAFE;

    -- ★ NORMALISE THE SPELLINGS, NOT THE ROWS. normGrade is a CASE over
    --   three helper functions, each with its own regex -- four pattern
    --   matches per call. Calling it per row is 225M x 4 regex evaluations
    --   to answer a question with only a few hundred distinct inputs.
    --
    --   Two scans and a hash join on a tiny table replace all of it.
    DROP TABLE IF EXISTS gradenorm;
    CREATE UNLOGGED TABLE gradenorm AS
        SELECT g AS raw, normGrade(g) AS norm, isDefiniteClass(g) AS definite
        FROM  (SELECT DISTINCT grade AS g FROM results     WHERE grade IS NOT NULL
               UNION
               SELECT DISTINCT grade      FROM results_tf  WHERE grade IS NOT NULL) x;
    CREATE INDEX ON gradenorm (raw);
    ANALYZE gradenorm;

    -- ★ UNLOGGED, NOT TEMP. A TEMP table cannot be scanned in parallel --
    --   Postgres will not hand a worker another session's temp buffers -- so
    --   every aggregate over allraces ran single-threaded, and those
    --   aggregates are the bulk of this script. UNLOGGED keeps the write
    --   cheap (no WAL) and lets all of them parallelise.
    --
    -- ⚠ IT IS THEREFORE VISIBLE TO OTHER SESSIONS AND SURVIVES THE RUN. The
    --   DROP at the top handles a leftover from a crash; nothing else reads
    --   it, and an unlogged table is emptied by a server restart anyway.
    DROP TABLE IF EXISTS allraces;

    -- ! BOTH KEYS ON EVERY ROW. `acad` is what the grade is reasoned about;
    --   `cal` is what the row is written under. Carrying both here means no
    --   stage has to recompute either, and no stage can disagree.
    --
    --   The academic year starts in July: a September race is already the new
    --   grade, and the spring that follows it is the same school year.
    CREATE UNLOGGED TABLE allraces AS
        SELECT person_id,
               substring(date, 1, 4)::int                        AS cal,
               {_ACAD}                                            AS acad,
               -- ★ #47: a banded grade in a division that writes AGES is not
               --   a grade at all, and loses both spellings here, before any
               --   rule can count it. See _ageBandJoins.
               --   ★ AND A NATIONAL TEAM'S (2026-10-08): see ntschool.
               CASE WHEN ab.result_id IS NULL AND nt.school IS NULL
                    THEN n.norm END                               AS grade,
               CASE WHEN nt.school IS NULL THEN n.definite END    AS definite,
               -- ! THE RAW COLUMN RIDES ALONG. Rule 5b needs the FR-1/SR-4
               --   spelling normGrade folds away; carrying it costs one text
               --   column and saves re-reading 225M rows.
               CASE WHEN ab.result_id IS NULL AND nt.school IS NULL
                    THEN r.grade END                              AS raw_grade,
               meet_id, div_id, source,
               (-1)::bigint                                       AS event_key,
               -- ★ raceIdent COMPUTED ONCE, HERE. It was being evaluated in
               --   four separate places -- the fold, rule 2, rule 4, and the
               --   race-kind collapse -- each a per-row function call under a
               --   DISTINCT over 225M rows. Storing it makes those four into
               --   plain column reads.
               raceIdent(time_seconds, date, meet_id, (-1)::bigint) AS race
        FROM   results r
        LEFT   JOIN gradenorm n ON n.raw = r.grade
        /*AGEBAND_XC*/
        LEFT   JOIN ntschool nt ON nt.school = r.school
        -- ! A ROW THE TWIN RULES FLAGGED IS NOT EVIDENCE (2026-09-06): the
        --   same race scraped twice would vote twice in every field count.
        --   result_twin is the previous run's (04c runs after this step),
        --   the same dedup the boards showed last time. race_date and
        --   time_seconds are no longer carried: nothing after the build
        --   read them, and 230M rows of them were written and indexed.
        LEFT   JOIN result_twin tw ON tw.sport = 'XC' AND tw.result_id = r.result_id
        WHERE  person_id IS NOT NULL AND date IS NOT NULL
          AND  substring(date, 1, 4)::int BETWEEN 1990 AND 2035
          AND  tw.result_id IS NULL
        UNION ALL
        SELECT person_id,
               substring(date, 1, 4)::int,
               {_ACAD},
               CASE WHEN ab.result_id IS NULL AND nt.school IS NULL
                    THEN n.norm END,
               CASE WHEN nt.school IS NULL THEN n.definite END,
               CASE WHEN ab.result_id IS NULL AND nt.school IS NULL
                    THEN r.grade END,
               meet_id, div_id, source, COALESCE(event_id, -1),
               raceIdent(time_seconds, date, meet_id,
                         COALESCE(event_id, -1))
        FROM   results_tf r
        LEFT   JOIN gradenorm n ON n.raw = r.grade
        /*AGEBAND_TF*/
        LEFT   JOIN ntschool nt ON nt.school = r.school
        LEFT   JOIN result_twin tw ON tw.sport = 'TF' AND tw.result_id = r.result_id
        WHERE  person_id IS NOT NULL AND date IS NOT NULL
          AND  substring(date, 1, 4)::int BETWEEN 1990 AND 2035
          AND  tw.result_id IS NULL;

    -- ★ NO INDEXES YET (run 20261011_150217: 134 s building two indexes
    --   the fold below threw away with the table). The only readers before
    --   the fold are raceKinds' full GROUP BY, which no index serves; the
    --   indexes the rules read -- (person_id, acad, race) covering their
    --   count(DISTINCT race) -- are built once, on the folded table.
    ANALYZE allraces;

    -- ============================================================== --
    --  THE FOLD (rule 2b) -- resolve number-vs-word ONCE, here.
    --
    -- ★ A SEPARATE COLUMN, NOT AN EDIT TO `grade`. Rules 2, 4 and 5 are
    --   about what THIS athlete's own season says, so they read the folded
    --   value. event_level is about what the OTHER entrants in a race say,
    --   and their evidence must not be thinned by a fold that happened for
    --   reasons internal to their seasons -- so it keeps reading `grade`.
    --   One column each, and neither can quietly become the other.
    -- ============================================================== --
    -- ! ONE PASS, NOT TWO. This used to be `SET grade_folded = grade` over
    --   every row, then a second UPDATE nulling the minority -- two full
    --   rewrites of a 225M-row table to produce one column. The column is
    --   now written once, below, from the join.

    -- Which KIND owns the season, counted in races rather than rows.
    -- ★ ONE ROW PER RACE FIRST, THEN COUNT KINDS. THE ORDER IS THE WHOLE
    --   BUG.
    --
    --   The previous version counted DISTINCT races INSIDE each kind: it
    --   deduped the numeric rows against each other and the word rows
    --   against each other, and never asked whether a numeric row and a word
    --   row were the SAME RACE.
    --
    --   They almost always are. An athlete covered by both feeds has every
    --   race stored twice -- anet writing '12', tfrrs writing 'SR', the same
    --   finishing time to the hundredth. Diego Eseverri's academic 2025 is
    --   about twenty races held as forty rows, and the fold read it as 19
    --   numeric votes against 21 word votes. Neither number is evidence
    --   about his grade; they are the two feeds' COVERAGE. tfrrs happened to
    --   catch a race or two more, so 'SR' won, so a Florida twelfth grader
    --   was corroborated as a college senior and pooled college_m.
    --
    -- ! AND THE TWO SPELLINGS AGREE. 'SR' and '12' are the same grade. There
    --   was never a contest to arbitrate -- only a question of which feed
    --   scraped more.
    --
    --   So: collapse to one row per race first. A race carrying both
    --   spellings counts ONCE, as a number, because the number is the
    --   readable one. A race carrying only a word counts as a word.
    DROP TABLE IF EXISTS raceKinds;
    CREATE UNLOGGED TABLE raceKinds AS
        SELECT person_id, acad, race,
               bool_or(grade ~ '^[0-9]+$')                     AS has_num,
               bool_or(grade IN ('FR','SO','JR','SR'))         AS has_word
        FROM   allraces
        WHERE  grade IS NOT NULL
        GROUP  BY 1, 2, 3;
    CREATE INDEX ON raceKinds (person_id, acad);
    ANALYZE raceKinds;

    DROP TABLE IF EXISTS gradekind;
    CREATE UNLOGGED TABLE gradekind AS
        SELECT person_id, acad,
               count(*) FILTER (WHERE has_num)              AS n_num,
               -- ⚠ WORD-ONLY RACES. A race that also carries a number is
               --   already counted above; counting it here too would restore
               --   the double-count this table exists to remove.
               count(*) FILTER (WHERE has_word AND NOT has_num) AS n_word
        FROM   raceKinds
        GROUP  BY 1, 2;
    CREATE INDEX ON gradekind (person_id, acad);
    ANALYZE gradekind;

    -- ! ONLY WHEN BOTH KINDS ARE PRESENT. A season of pure class words is a
    --   collegian and keeps them; a season of pure numbers has nothing to
    --   fold. The fold exists for the mixed case alone.
    --
    -- ! TIES GO TO THE NUMBER. n_num >= n_word, not >. A bare 'Jr' means
    --   grade 11 in high school and a third-year in college, and nothing in
    --   the string says which; '11' says which. When the evidence is
    --   balanced, prefer the value that cannot be read two ways.
    -- ! ROWS WITH NO gradekind MATCH KEEP grade_folded NULL, AND THAT IS
    --   CORRECT. gradekind exists only for person-seasons holding at least
    --   one graded race, so a row that fails this join has grade NULL
    --   anyway. The old first pass set grade_folded = grade for all 225M
    --   rows purely to give those rows a value they already had.
    -- ★ A NEW TABLE, NOT AN UPDATE (run 20261006_120609: the UPDATE took
    --   3,009 s). An UPDATE of nearly every graded row writes a second copy
    --   of each one and leaves the dead copies for every later scan; the
    --   table this step builds from scratch took 335 s. So the column is
    --   written by building allraces again with it, and the indexes are
    --   built on the new table. A row with no gradekind match has grade NULL
    --   (above), so the ELSE gives it NULL exactly as the UPDATE left it.
    DROP TABLE IF EXISTS allraces_folded;
    CREATE UNLOGGED TABLE allraces_folded AS
        SELECT a.*,
               CASE WHEN k.n_num > 0 AND k.n_word > 0
                     AND ((k.n_num >= k.n_word
                           AND a.grade IN ('FR','SO','JR','SR'))
                       OR (k.n_word > k.n_num AND a.grade ~ '^[0-9]+$'))
                    THEN NULL ELSE a.grade END                  AS grade_folded
        FROM   allraces a
        LEFT   JOIN gradekind k ON k.person_id = a.person_id AND k.acad = a.acad;
    DROP TABLE allraces;
    ALTER TABLE allraces_folded RENAME TO allraces;
    CREATE INDEX ON allraces (person_id, acad, race);
    CREATE INDEX ON allraces (meet_id, div_id, source, event_key);
    CREATE INDEX ON allraces (person_id, acad, grade_folded);
    ANALYZE allraces;
"""


# ------------------------------------------------------------------ #
#  RULES 2 AND 3: THE CORROBORATED GRADE
# ------------------------------------------------------------------ #

# The grade that recurs most within the academic year, provided it recurs at
# all. Ties break toward the LATER grade: grades only move forward, so when
# two values are equally attested the higher one is the athlete's current year
# and the lower is the stale half.
_CORROBORATED_SQL = """
    -- ! COUNT RACES, NOT ROWS. count(*) counted how many times a scraper
    --   wrote the athlete down, which is not evidence about their grade.
    --   Chio's ONE race, scraped twice, cleared MIN_CORROBORATION on its own
    --   and corroborated grade 6 -- so his season was never needy, rule 4
    --   never ran, and a college 10 km field never got to speak.
    WITH per_grade AS (
        SELECT person_id, acad, grade_folded AS grade,
               count(DISTINCT race) AS n
        FROM   allraces
        WHERE  grade_folded IS NOT NULL
        GROUP  BY 1, 2, 3
    )
    SELECT DISTINCT ON (person_id, acad)
           person_id, acad, grade, n
    FROM   per_grade
    WHERE  n >= %(minc)s
    ORDER  BY person_id, acad, n DESC,
              -- later grade first: numerics ascending, then class words in
              -- order, so SR beats JR beats a leftover 12.
              (CASE grade WHEN 'FR' THEN 13 WHEN 'SO' THEN 14
                          WHEN 'JR' THEN 15 WHEN 'SR' THEN 16
                          ELSE CASE WHEN grade ~ '^[0-9]+$'
                                    THEN grade::int ELSE 0 END END) DESC
"""


# ------------------------------------------------------------------ #
#  RULE 4: THE LEVEL OF THE FIELD
# ------------------------------------------------------------------ #

# ★ STAGED INTO TABLES, ONE STATEMENT A STEP (2026-10-25, run log: 826 s
#   between "[2c round 2]" and "[4] field levels read", the longest gap in
#   the step). It was one statement of five CTEs. Two things made it slow,
#   and neither was the arithmetic:
#
#   1. gradeLevel was PARALLEL UNSAFE (see _BUILD), so the whole statement
#      ran on one core.
#   2. `needy` is read twice, so Postgres materialises it, and a CTE scan
#      may not run under a Gather: every join against it ran in the leader.
#      Its row estimates were guesses besides, and a guess of one row for
#      event_level turns the final join into a nested loop (reproduced on a
#      synthetic corpus: the unchanged statement had not finished after two
#      minutes on 450K rows, and took 3 s with nested loops turned off).
#
#   Each CTE is now an UNLOGGED table, ANALYZEd, so every step can run in
#   parallel and the next one is planned on real row counts. The SQL of each
#   step is the CTE it replaces; two changes are below, each the same
#   relation by construction.
#
# ★ season_races IS ALSO RULE 5e's race_count (run log: 286 s for that one
#   GROUP BY). needy read `SELECT DISTINCT person_id, acad FROM allraces`, and
#   5e later grouped allraces by the same key again to count races. One
#   table is both: its keys ARE the distinct seasons, and its count is 5e's
#   count(DISTINCT race) verbatim. Nothing between here and 5e writes
#   person_id, acad or race (the 2c write-back, the only UPDATE of allraces,
#   runs before rule 4 and touches grade_folded alone), so the count read at
#   5e is the count 5e used to make.
#
# ★ mode() BECOMES A COUNT PER LEVEL, AND THE ANSWER IS THE SAME. mode() is
#   an ordered-set aggregate: it cannot be split across workers, so the
#   leader read every entrant of every wanted event and ran gradeLevel on
#   each, three times. Counting entrants per (event, level) is an ordinary
#   GROUP BY the workers share. The level is then the one with the most
#   entrants, ties to the FIRST in ORDER BY level -- which is mode()'s own
#   rule: it walks the sorted values and replaces its answer only on a
#   strictly larger count, so a tie keeps the earlier value. Same ORDER BY
#   (the level text, default collation), same tie, same NULLs left out by
#   the FILTER; count(a.grade) per level summed is the old
#   count(*) FILTER (WHERE a.grade IS NOT NULL).
# ★ A TIE GOES TO THE HIGHER LEVEL, BY RANK (owner, 2026-10-26: "I'd prefer
#   for a tie to go to a higher level"). Both tie-breaks below were the level
#   TEXT in sort order -- college, elem, hs, ms, pro -- so a college/hs tie
#   went up but an elem/hs tie went DOWN to elem, and ms beat pro. They now
#   rank pro > college > hs > ms > elem, highest first.
_LEVEL_RANK_SQL = ("CASE {x} WHEN 'pro' THEN 5 WHEN 'college' THEN 4 WHEN 'hs' THEN 3 "
               "WHEN 'ms' THEN 2 WHEN 'elem' THEN 1 ELSE 0 END")

_FIELD_BUILD = f"""
    -- ! ONE PASS, NOT A SUBQUERY PER SEASON. The first version asked
    --   NOT EXISTS (... GROUP BY grade HAVING count(*) >= n) for every
    --   athlete-season, which re-scans allraces ~15M times: it ran 19 minutes
    --   and was still going.
    --
    --   The same question answered by aggregation: group once by
    --   (person, season, grade), keep the largest count per season, and a
    --   season is needy when that largest count is below the threshold. Two
    --   grouped scans of an indexed table instead of 15M correlated ones.
    -- ! THE SAME CHANGE, AND IT MATTERS MORE HERE. by_grade decides who is
    --   NEEDY, so a duplicate-inflated count does not merely pick the wrong
    --   grade -- it removes the season from rule 4 entirely.
    -- ★ NO NESTED LOOPS FOR THESE STAGES (rule 7's and rule 8's setting, for
    --   the same reason). The wanted events are most of the corpus, and the
    --   planner still probed allraces' event index once per event, on one
    --   worker; turned off, it hash-joins on every worker (synthetic corpus:
    --   event_level 9.8 s -> 4.6 s, the final read 6.7 s -> 3.7 s). A plan
    --   setting: the rows are the rows. RESET in _FIELD_DROP.
    SET enable_nestloop = off;

    DROP TABLE IF EXISTS season_races;
    CREATE UNLOGGED TABLE season_races AS
        SELECT person_id, acad, count(DISTINCT race) AS races
        FROM   allraces GROUP BY 1, 2;
    ANALYZE season_races;

    DROP TABLE IF EXISTS fld_best;
    CREATE UNLOGGED TABLE fld_best AS
        SELECT person_id, acad, max(n) AS top_n
        FROM  (SELECT person_id, acad, grade_folded AS grade,
                      count(DISTINCT race) AS n
               FROM   allraces
               WHERE  grade_folded IS NOT NULL
               GROUP  BY 1, 2, 3) by_grade
        GROUP  BY 1, 2;
    ANALYZE fld_best;

    -- the seasons with no grade on MIN_CORROBORATION races
    DROP TABLE IF EXISTS fld_needy;
    CREATE UNLOGGED TABLE fld_needy AS
        SELECT a.person_id, a.acad
        FROM   season_races a
        LEFT   JOIN fld_best b
               ON b.person_id = a.person_id AND b.acad = a.acad
        WHERE  b.top_n IS NULL OR b.top_n < {MIN_CORROBORATION};
    ANALYZE fld_needy;

    -- Only the events those athletes ran, scored from their own entrants.
    DROP TABLE IF EXISTS fld_wanted;
    CREATE UNLOGGED TABLE fld_wanted AS
        SELECT DISTINCT a.meet_id, a.div_id, a.source, a.event_key
        FROM   fld_needy n
        JOIN   allraces a ON a.person_id = n.person_id AND a.acad = n.acad;
    ANALYZE fld_wanted;

    DROP TABLE IF EXISTS fld_event_level;
    CREATE UNLOGGED TABLE fld_event_level AS
        SELECT meet_id, div_id, source, event_key,
               -- ★ EDIT 2, PART 1 -- AN EVENT NOBODY GRADED IS NOT A
               --   SCHOOL RACE. The HAVING below now lets the ungraded
               --   event through, but `mode()` over an empty set is NULL
               --   and the final SELECT drops NULL levels -- so widening
               --   the gate ALONE changes nothing. The verdict has to be
               --   named here too.
               --
               --   'pro' is the name pool_resolve.resolvePool already
               --   documents for it: "nobody in any of their races carried
               --   a grade, so it was not a school race and the season is
               --   pro". The method existed on the read side and was never
               --   emitted on the write side.
               --
               --   Amelio Chio's open 10 km is the case: no entrant
               --   carried a grade, MIN_FIELD_GRADED skipped the event, no
               --   verdict was produced, and his stale grade 6 stood.
               CASE WHEN sum(n_graded) = 0
                    THEN 'pro'
                    -- the modal level: most entrants, ties to the HIGHER
                    -- level (_LEVEL_RANK_SQL; was mode()'s text order)
                    ELSE (array_agg(lvl ORDER BY n DESC, {_LEVEL_RANK_SQL.format(x="lvl")} DESC)
                              FILTER (WHERE lvl IS NOT NULL))[1]
               END AS level,
               -- an open race: most of its entrants have no level (OPEN_FIELD_SHARE)
               (sum(n) - COALESCE(sum(n) FILTER (WHERE lvl IS NOT NULL), 0))
                   >= {OPEN_FIELD_SHARE} * sum(n) AS open_field
        FROM  (SELECT w.meet_id, w.div_id, w.source, w.event_key,
                      gradeLevel(a.grade) AS lvl,
                      count(*)            AS n,
                      count(a.grade)      AS n_graded
               FROM   fld_wanted w
               JOIN   allraces a
                      ON a.meet_id = w.meet_id AND a.div_id = w.div_id
                     AND a.source = w.source AND a.event_key = w.event_key
               GROUP  BY 1, 2, 3, 4, 5) per_level
        GROUP  BY 1, 2, 3, 4
        -- ★ EDIT 2, PART 2 -- the gate. Either the event has enough graded
        --   entrants for its mode to mean something, OR it has NONE, which
        --   is itself the evidence.
        --
        -- ★ AND THE SAME FLOOR ON THE SECOND ARM (pooling review P5,
        --   2026-10-26): a one-entrant ungraded event voted 'pro' on a
        --   field of one. An ungraded field now needs MIN_FIELD_GRADED
        --   entrants to say "not a school race", as a graded one does to
        --   name its level.
        HAVING COALESCE(sum(n) FILTER (WHERE lvl IS NOT NULL), 0)
                   >= {MIN_FIELD_GRADED}
            OR (sum(n_graded) = 0 AND sum(n) >= {MIN_FIELD_GRADED});
    ANALYZE fld_event_level;
"""

# ! RACES, NOT ROWS (pooling review P4): a race scraped by both feeds is
#   one race (allraces.race, raceIdent) and votes once.
_FIELD_SQL = f"""
    SELECT DISTINCT ON (n.person_id, n.acad)
           n.person_id, n.acad, e.level
    FROM   fld_needy n
    JOIN   allraces a ON a.person_id = n.person_id AND a.acad = n.acad
    JOIN   fld_event_level e
           ON e.meet_id = a.meet_id AND e.div_id = a.div_id
          AND e.source = a.source AND e.event_key = a.event_key
    WHERE  e.level IS NOT NULL
      -- an ungraded runner in an open race: its graded few say nothing of them
      AND  NOT (e.open_field AND e.level <> 'pro' AND gradeLevel(a.grade) IS NULL)
    GROUP  BY n.person_id, n.acad, e.level
    ORDER  BY n.person_id, n.acad, count(DISTINCT a.race) DESC,
              {_LEVEL_RANK_SQL.format(x="e.level")} DESC
"""

# rule 4's staging tables; season_races outlives them until rule 5e reads it
_FIELD_DROP = """
    DROP TABLE IF EXISTS fld_best;
    DROP TABLE IF EXISTS fld_needy;
    DROP TABLE IF EXISTS fld_wanted;
    DROP TABLE IF EXISTS fld_event_level;
    RESET enable_nestloop;
"""


# ------------------------------------------------------------------ #
#  RULE 5f: A LONE SEASON ON A HIGH SCHOOL TEAM, IN HIGH SCHOOL FIELDS
# ------------------------------------------------------------------ #
#
# ★ OWNER, 2026-10-14 (/athlete/29642348, "This Athlete is a freshman but
#   appears as a 8th grade"): anet lists him on a high school team, in high
#   school races, as grade 8. One season, so nothing in his own data says
#   the 8 is wrong -- and nothing says it is right either. The owner chose
#   ("2. makes so much sense"): the team's level wins over a school grade
#   below it ONLY when the athlete has no other season to confirm the grade.
#
# ! NOT LUKE MORELLI OR MICHAEL LEIFERMAN (pool_resolve stage 1 and 1b): a
#   K-12 school's middle schoolers race its high school meets on its high
#   school team, and their grades are right. They have other seasons, and a
#   grade that advances 6, 7, 8, 9 is its own confirmation, so this rule
#   never sees them. A real one-season eighth grader on a high school team
#   who races only high school fields is the cost: pooled with the high
#   schoolers whose races he ran.
#
# ! EVERY ROW, BOTH WAYS. Every race of the season was for an anet team whose
#   level is high school (a tfrrs row, team 0 or an unlevelled team fails
#   it), and every event with a graded field (MIN_FIELD_GRADED) was a high
#   school field, with at least one such event.
#
# The verdict is a LEVEL with no grade, like rule 4's: the raw grade stays
# the feed's (the athlete page shows it, asterisked), and the pool is 'hs'.
#
# ★ TWO STATEMENTS AND NO TEMP TABLE (2026-10-25, run log: 379 s from the
#   5e line to the 5f line). tl_cand was TEMP, and a temp table cannot be
#   read by a parallel worker, so the scan of both results tables joined to
#   it ran on one core -- as did everything else here, gradeLevel being
#   PARALLEL UNSAFE until today (_BUILD). tl_cand is UNLOGGED now, like
#   allraces and for the same reason. `mine` was a CTE read twice, which
#   Postgres materialises anyway; it is the table tl_mine instead, so the
#   joins against it can run in workers too. Same rows, same statement.
_TEAM_MINE_SQL = f"""
    CREATE UNLOGGED TABLE tl_mine AS
    WITH teams AS (
        SELECT r.person_id, {_ACAD_R} AS acad,
               COALESCE(t.level = ANY(%(codes)s), false) AS hs
        FROM   results r
        JOIN   tl_cand c ON c.person_id = r.person_id
        LEFT   JOIN anet_team t ON t.team_id = r.team_id
        WHERE  r.date IS NOT NULL
        UNION ALL
        SELECT r.person_id, {_ACAD_R},
               COALESCE(t.level = ANY(%(codes)s), false)
        FROM   results_tf r
        JOIN   tl_cand c ON c.person_id = r.person_id
        LEFT   JOIN anet_team t ON t.team_id = r.team_id
        WHERE  r.date IS NOT NULL
    ),
    team_ok AS (
        SELECT t.person_id, t.acad
        FROM   teams t
        JOIN   tl_cand c ON c.person_id = t.person_id AND c.acad = t.acad
        GROUP  BY 1, 2
        HAVING bool_and(t.hs)
    )
    -- the seasons' own rows: what `mine` was
    SELECT a.person_id, a.acad, a.meet_id, a.div_id, a.source, a.event_key
    FROM   allraces a
    JOIN   team_ok k ON k.person_id = a.person_id AND k.acad = a.acad
"""

_TEAM_LEVEL_SQL = f"""
    WITH wanted AS (
        SELECT DISTINCT meet_id, div_id, source, event_key FROM tl_mine
    ),
    event_level AS (
        SELECT w.meet_id, w.div_id, w.source, w.event_key,
               mode() WITHIN GROUP (ORDER BY gradeLevel(a.grade))
                   FILTER (WHERE gradeLevel(a.grade) IS NOT NULL) AS level
        FROM   wanted w
        JOIN   allraces a
               ON a.meet_id = w.meet_id AND a.div_id = w.div_id
              AND a.source = w.source AND a.event_key = w.event_key
        GROUP  BY 1, 2, 3, 4
        HAVING count(*) FILTER (WHERE gradeLevel(a.grade) IS NOT NULL)
                   >= {MIN_FIELD_GRADED}
    )
    SELECT m.person_id, m.acad
    FROM   tl_mine m
    JOIN   event_level e
           ON e.meet_id = m.meet_id AND e.div_id = m.div_id
          AND e.source = m.source AND e.event_key = m.event_key
    GROUP  BY 1, 2
    HAVING bool_and(e.level = 'hs')
"""


def teamLevelCandidates(acad, seasons_of):
    """The athlete-seasons rule 5f may move: the athlete's only season, a
    school grade 1-8 on the verdict, and not the owner's pin."""
    # ! THE CHEAP TEST FIRST (2026-10-25). This walks every verdict, 31M of
    #   them, and formatted each one's grade before asking the two dict
    #   lookups that turn most of them away. Same three tests, same keys in
    #   the same order out; the string is only built for the seasons that
    #   can still pass.
    out = []
    for key, v in acad.items():
        if seasons_of.get(key[0]) != 1 or v.get("method") == "pinned":
            continue
        g = str(v.get("grade") or "").strip()
        if g.isdigit() and 1 <= int(g) <= 8:
            out.append(key)
    return out


def _hsTeamCodes():
    """anet_team.level codes that name a high school, from the engine's own
    reading of them (speed_ratings_db.loadTeamLevels). Empty -- and rule 5f
    inert -- when it cannot say."""
    try:
        from speed_ratings_db import loadTeamLevels
        _by_team, meaning, _rows = loadTeamLevels()
    except Exception as exc:                                     # noqa: BLE001
        print(f"    [5f] team levels unavailable ({type(exc).__name__}: {exc}); "
              f"rule skipped", flush=True)
        return []
    return sorted(int(c) for c, lv in meaning.items() if lv == "hs")


def teamLevelSeasons(cur, acad, seasons_of, hs_codes=None):
    """{(person_id, acad)} rule 5f moves to 'hs'. See _TEAM_LEVEL_SQL.
    hs_codes: the codes, or a Future of them (resolve reads them in a
    thread while 5e counts), or None to read them here."""
    cand = teamLevelCandidates(acad, seasons_of)
    if not cand:
        return set()
    if hs_codes is None:
        hs_codes = _hsTeamCodes()
    elif hasattr(hs_codes, "result"):
        hs_codes = hs_codes.result()
    if not hs_codes:
        return set()
    cur.execute("DROP TABLE IF EXISTS tl_cand; DROP TABLE IF EXISTS tl_mine")
    cur.execute("CREATE UNLOGGED TABLE tl_cand (person_id bigint, acad int, "
                "PRIMARY KEY (person_id, acad))")
    _copyRows(cur, "tl_cand", ("person_id", "acad"), cand)
    cur.execute("ANALYZE tl_cand")
    params = {"codes": list(hs_codes)}
    cur.execute(_TEAM_MINE_SQL, params)
    cur.execute("ANALYZE tl_mine")
    cur.execute(_TEAM_LEVEL_SQL, params)
    got = {(int(p), int(a)) for p, a in cur.fetchall()}
    cur.execute("DROP TABLE tl_mine; DROP TABLE tl_cand")
    return got


# ------------------------------------------------------------------ #
#  RULE 5: A GRADE THAT STOPPED ADVANCING
# ------------------------------------------------------------------ #

# ! ANY GRADE, NOT JUST SENIOR. Nobody is a sophomore three times either. A
#   grade that repeats past MAX_YEARS_AT_GRADE stopped being maintained, and
#   the athlete has almost always left school and kept racing.
_STALE_SQL = """
    WITH per_year AS (
        SELECT DISTINCT person_id, acad, grade_folded AS grade
        FROM   allraces WHERE grade_folded IS NOT NULL
    ),
    runs AS (
        SELECT person_id, grade,
               min(acad) AS first_year,
               count(*)  AS years
        FROM   per_year GROUP BY 1, 2
    )
    SELECT p.person_id, p.acad, p.grade
    FROM   per_year p
    JOIN   runs r ON r.person_id = p.person_id AND r.grade = p.grade
    WHERE  r.years > %(maxy)s
      AND  p.acad - r.first_year >= %(maxy)s
"""


# ------------------------------------------------------------------ #
#  DIAGNOSTICS -- what the filters discarded, and what edit 2 admitted
# ------------------------------------------------------------------ #

# ★ ALWAYS AUDIT WHAT A FILTER DISCARDED. A guard that prevents a crash is a
#   guard that hides data, and the only way to tell those apart is to count
#   what it swallowed. Both helpers below are REPORTS: they decide nothing.

_REJECTED_SQL = """
    SELECT CASE WHEN isGradYear(g) THEN 'graduation year'
                ELSE 'other out-of-range number' END      AS kind,
           count(*)                                       AS rows,
           count(DISTINCT TRIM(g))                        AS spellings,
           (array_agg(DISTINCT TRIM(g)))[1:6]             AS examples
    FROM   (SELECT grade AS g FROM results
            UNION ALL
            SELECT grade AS g FROM results_tf) x
    WHERE  TRIM(g) ~ '^[0-9]+$'
      AND  schoolGrade(g) IS NULL
    GROUP  BY 1 ORDER BY 2 DESC
"""


def reportRejected(cur):
    """Numeric grades normGrade now discards. Full-corpus scan -- opt in."""
    print("\n[grade] numeric values rejected by schoolGrade:")
    cur.execute(_REJECTED_SQL)
    rows = cur.fetchall()
    if not rows:
        print("    none")
        return
    for kind, n, spellings, examples in rows:
        ex = ", ".join(str(e) for e in (examples or [])[:6])
        print(f"    {kind:<26} {n:>12,} rows  {spellings:>6,} spellings  {ex}")


_UNGRADED_SQL = f"""
    WITH ev AS (
        SELECT meet_id, div_id, source, event_key, count(*) AS n,
               count(*) FILTER (WHERE grade IS NOT NULL) AS n_graded
        FROM   allraces GROUP BY 1, 2, 3, 4
    )
    SELECT count(*)              FILTER (WHERE n_graded = 0),
           count(*)              FILTER (WHERE n_graded = 0
                                           AND n < {MIN_FIELD_GRADED}),
           -- ! FILTER IS A CLAUSE OF THE AGGREGATE, NOT OF A WRAPPER. It
           --   goes inside COALESCE, attached to sum(); outside it there is
           --   no aggregate left to attach to and the parser errors.
           COALESCE(sum(n) FILTER (WHERE n_graded = 0), 0)
    FROM   ev
"""


_FOLD_SQL = """
    SELECT count(*) FILTER (WHERE n_num > 0 AND n_word > 0)          AS mixed,
           count(*) FILTER (WHERE n_num >= n_word AND n_word > 0
                              AND n_num > 0)                         AS num_wins,
           count(*) FILTER (WHERE n_word > n_num AND n_num > 0)      AS word_wins
    FROM   gradekind
"""


def reportFold(cur):
    """How many seasons held both kinds, and which kind took them.

    ★ word_wins IS THE NUMBER TO WATCH. Those are seasons where class words
      out-raced numbers outright -- genuine collegians who also carry a stray
      number, plus any high schooler whose feeds lean word-heavy. If it is a
      large share of `mixed`, the tie rule is doing more work than intended
      and wants a second look.
    """
    cur.execute(_FOLD_SQL)
    mixed, num_wins, word_wins = cur.fetchone()
    print("\n[grade] seasons holding BOTH numbers and class words:")
    print(f"    mixed seasons            {mixed:>10,}")
    print(f"    number takes the season  {num_wins:>10,}")
    print(f"    word takes the season    {word_wins:>10,}")


def reportUngraded(cur):
    """How much of the corpus edit 2's second arm now speaks for.

    ⚠ THE SECOND NUMBER IS THE ONE TO WATCH. Edit 2 puts no field-size floor
      on the ungraded arm, so a one-entrant event votes 'pro' on a field of
      one. If that count is a large share of the first, add the floor -- the
      exact clause is in the HAVING's comment.

    Counts EVERY event, not only the ones needy athletes ran, so it is an
    upper bound on what the rule can reach.
    """
    cur.execute(_UNGRADED_SQL)
    n_ev, n_thin, n_rows = cur.fetchone()
    print("\n[grade] events where nobody carried a grade (edit 2):")
    print(f"    events                   {n_ev:>10,}")
    print(f"    of those, under {MIN_FIELD_GRADED} entrants "
          f"{n_thin:>10,}   <- no floor applied")
    print(f"    rows they hold           {n_rows:>10,}")


# ------------------------------------------------------------------ #
#  RULE 2c: A BARE CLASS WORD, OVERRULED BY THE ATHLETE'S PROGRESSION
# ------------------------------------------------------------------ #

# Seasons that resolved on a BARE word only -- no FR-1/SR-4 row anywhere in
# them. Those are provisional. A season holding even one eligibility spelling
# is definite and is left alone.
_BARE_CLASS_SQL = """
    SELECT person_id, acad
    FROM   allraces
    WHERE  grade_folded IN ('FR','SO','JR','SR')
    GROUP  BY 1, 2
    HAVING count(*) FILTER (WHERE definite) = 0
"""


# ★ THE FIELD'S OWN GRADES, FOR ATHLETES WHOSE SEASONS CARRY NONE.
#
#   The first pass needs the athlete to have a numeric corroborated season
#   somewhere. Florida high schoolers on tfrrs never do -- the feed writes
#   SR/JR/SO/FR year-round -- so the collegiate lean stood and they landed in
#   college_*. Measured on the 2025 board: Ben Perschon, a Lake Central High
#   School SENIOR, ranked FIRST in College (M) XC, with nine of the top 25
#   performances at the 26th FLrunners.com Invitational -- a Florida high
#   school meet.
#
#   Those races are full of evidence, just not the athlete's own. At Holloway
#   Park, 128 rows read a bare class word alongside 95 reading a corroborated
#   9, 10, 11 or 12. A field that is ninety-five numerically graded high
#   schoolers IS a high school race.
#
# ! THE ENTRANTS' VERDICTS, NOT THEIR RAW GRADES. Raw grades are the thing
#   this module exists because it cannot trust. gradeLevel(grade_folded) is
#   what rule 4 already uses to read a field, so the two rules cannot
#   disagree about what an event looks like.
#
# ⚠ THE THRESHOLD IS HIGH ON PURPOSE. A genuine collegiate race with a few
#   high school guests must not flip. Four fifths of the graded entrants have
#   to be school-level before a word is overruled.
_FIELD_SCHOOL_SHARE = 0.80

_BARE_FIELD_SQL = """
    -- ★ ONLY THE EVENTS A BARE WORD RAN IN (run 20261011_150217: each round
    --   took 750s grouping all 225M rows by event, calling gradeLevel on
    --   every one, when the answer is read only for events holding a bare
    --   FR/SO/JR/SR row of a season with no definite row -- the final
    --   SELECT's own filter). Found by one plain scan, then their rows by
    --   allraces' event index. Same events scored, same answer.
    WITH wanted AS (
        SELECT DISTINCT a.meet_id, a.div_id, a.source, a.event_key
        FROM   allraces a
        WHERE  a.grade_folded IN ('FR','SO','JR','SR')
          AND  NOT EXISTS (SELECT 1 FROM allraces d
                           WHERE d.person_id = a.person_id
                             AND d.acad      = a.acad
                             AND d.definite)
    ),
    ev AS (
        SELECT a.meet_id, a.div_id, a.source, a.event_key,
               count(*) FILTER (WHERE gradeLevel(a.grade_folded)
                                      IN ('elem','ms','hs')) AS n_school,
               count(*) FILTER (WHERE gradeLevel(a.grade_folded)
                                      IS NOT NULL)           AS n_graded
        FROM   wanted w
        JOIN   allraces a
               ON a.meet_id = w.meet_id AND a.div_id = w.div_id
              AND a.source  = w.source  AND a.event_key = w.event_key
        GROUP  BY 1, 2, 3, 4
        HAVING count(*) FILTER (WHERE gradeLevel(a.grade_folded) IS NOT NULL)
                   >= %(minf)s
    ),
    -- The modal numeric grade of each qualifying event: what a bare word in
    -- this field most likely spells.
    lvl AS (
        SELECT e.meet_id, e.div_id, e.source, e.event_key,
               mode() WITHIN GROUP (ORDER BY a.grade_folded)
                   FILTER (WHERE a.grade_folded ~ '^[0-9]+$') AS modal_grade
        FROM   ev e
        JOIN   allraces a
               ON a.meet_id = e.meet_id AND a.div_id = e.div_id
              AND a.source  = e.source  AND a.event_key = e.event_key
        WHERE  e.n_school >= %(share)s * e.n_graded
        GROUP  BY 1, 2, 3, 4
    )
    SELECT a.person_id, a.acad,
           mode() WITHIN GROUP (ORDER BY l.modal_grade) AS grade
    FROM   allraces a
    JOIN   lvl l
           ON l.meet_id = a.meet_id AND l.div_id = a.div_id
          AND l.source  = a.source  AND l.event_key = a.event_key
    WHERE  a.grade_folded IN ('FR','SO','JR','SR')
      AND  l.modal_grade IS NOT NULL
      -- ⚠ THE GUARD HAS TO ASK ABOUT THE WHOLE SEASON, NOT ABOUT THE ROWS
      --   THIS QUERY HAPPENS TO BE LOOKING AT.
      --
      --   It was written as a HAVING over the joined set, and the joined set
      --   only holds rows whose EVENT qualified as school-level. Ben Bouie's
      --   ten FR-1 rows are at NCAA championships -- events that never
      --   qualify -- so they were not in the aggregate, the count came back
      --   0, and the guard passed. It asked "are any of the rows I am
      --   looking at definite" when it had to ask "does this athlete-season
      --   have a definite row ANYWHERE".
      --
      --   Measured: Bouie has 10 FR-1 rows and Matthew Binkley 17, and both
      --   were still overruled -- Bouie to grade 12 on the national high
      --   school board, Binkley to grade 8 off NJCAA 8000m races.
      AND NOT EXISTS (SELECT 1 FROM allraces d
                      WHERE d.person_id = a.person_id
                        AND d.acad      = a.acad
                        AND d.definite)
    GROUP  BY 1, 2
    -- ⚠ THE SAME GUARD PASS 1 HAS, AND ITS ABSENCE HERE WAS A REAL BUG.
    --
    --   Pass 1 excludes any season carrying an FR-1/SR-4 row, because §1.6
    --   says the eligibility form is DEFINITELY collegiate -- the digit is
    --   NCAA eligibility and high school has no such thing. That guard was
    --   never carried into this pass, which looked only at grade_folded --
    --   where normGrade has already collapsed 'FR-1' and 'Fr' onto the same
    --   'FR' and the distinction is gone.
    --
    --   Measured: Ben Bouie ran 2019-2024 as grades 7 through 12, then 2025
    --   at Harvard with ten tfrrs rows reading FR-1 and eleven anet rows
    --   reading Fr. Pass 1 correctly refused him. Pass 2 saw 'FR', read his
    --   NCAA championship fields as school-level, and wrote grade 12 --
    --   putting a Harvard runner first on the national HIGH SCHOOL board.
    --
    --   A season the athlete's own feed marks as collegiate cannot be
    --   overruled by the company they kept.

"""


# _resolveBareClassByField
# Purpose:   Overrule a bare word using the fields the athlete raced in.
# Arguments: acad -- the verdict dict, mutated; rows -- (pid, acad, grade).
# Output:    how many seasons were converted.
#
# ! ONLY SEASONS THE FIRST PASS COULD NOT REACH. An athlete with numeric
#   seasons of their own has better evidence than the company they kept, and
#   it has already been applied -- their verdict is no longer a class word, so
#   the guard below skips them.
def _resolveBareClassByField(acad, rows):
    n = 0
    for pid, ay, grade in rows:
        key = (int(pid), int(ay))
        v = acad.get(key)
        if v is None or v["grade"] not in ("FR", "SO", "JR", "SR"):
            continue
        if not (grade and grade.isdigit() and 1 <= int(grade) <= 12):
            continue
        acad[key] = {"grade": grade, "level": None, "method": "bare_field"}
        n += 1
    return n


# _writeBackBareClass
# Purpose:   Push resolved bare-word grades into allraces.grade_folded, so the
#            rules that read the FIELD see the corrected labels.
# Arguments: cur; acad -- the verdict dict; methods -- which verdicts to push.
# Output:    rows updated.
#
# ★ WITHOUT THIS, RULE 4 READS A FIELD THE EARLIER RULES ALREADY FIXED.
#
#   Pass 2 resolves the collegiate-labelled POCKETS inside a high school race
#   -- but it writes into the Python verdict dict, not into allraces. So
#   event_level, which reads grade_folded, still sees those athletes as
#   college and computes the event's modal level from a field that is half
#   mislabelled. A high school race with a few college-labelled entrants is a
#   high school race, and averaging hides it.
#
# ⚠ AND IT MAKES THE PASS WORTH REPEATING. Converting the pockets in one race
#   raises that race's school share, which can carry ANOTHER race over the
#   80% line -- one that was just under it. Two passes catch nearly all of
#   that; a third is diminishing, and an unbounded loop over 225M rows is not
#   worth the tail.
def _writeBackBareClass(cur, acad, methods=("bare_class", "bare_field")):
    rows = [(pid, ay, v["grade"]) for (pid, ay), v in acad.items()
            if v["method"] in methods and v["grade"]]
    if not rows:
        return 0
    # ! IMPORTED HERE, AS write() DOES. psycopg2.extras is not a module-level
    #   import in this file, and adding one would make the import order
    #   differ from every other function that needs it.
    from psycopg2.extras import execute_values

    cur.execute("DROP TABLE IF EXISTS barefix")
    cur.execute("CREATE UNLOGGED TABLE barefix "
                "(person_id bigint, acad int, grade text)")
    execute_values(cur,
                   "INSERT INTO barefix (person_id, acad, grade) VALUES %s",
                   rows, page_size=10000)
    cur.execute("CREATE INDEX ON barefix (person_id, acad)")
    cur.execute("ANALYZE barefix")
    # ! ONLY THE WORD ROWS. The numeric rows in these seasons are already
    #   right, and rewriting them would be a no-op that costs a heap update.
    cur.execute("""
        UPDATE allraces a
           SET grade_folded = b.grade
          FROM barefix b
         WHERE b.person_id = a.person_id
           AND b.acad      = a.acad
           AND a.grade_folded IN ('FR','SO','JR','SR')
    """)
    return cur.rowcount


# _resolveBareClass
# Purpose:   Convert a provisionally-collegiate season to a school grade when
#            the athlete's OTHER seasons imply one, and only then.
# Arguments: acad -- the verdict dict, mutated in place; bare -- the keys.
# Output:    how many seasons were converted.
#
# ★ IT LEANS COLLEGE AND WAITS TO BE PROVED WRONG. The default for a bare word
#   stays college. This fires only when the athlete has numeric, corroborated
#   seasons elsewhere whose implied class year puts them at grade 12 or below
#   in the year in question. Without that evidence nothing happens.
#
# ⚠ AND IT MUST RUN BEFORE RULE 4. Measured at Thetford, a Vermont high school
#   girls' race: half the field reads a graduation year (nulled by normGrade)
#   and half a bare class word. If the words resolve collegiate first, that
#   event's modal gradeLevel is 'college', and the field rule hands the other
#   half a college verdict too -- both halves wrong in the same direction.
#   Maisie Emerson (SO, 20:57.8) rated 128.9 while Evelyn Lefort (2027,
#   20:57.9) rated 149.3: one tenth of a second apart, twenty points apart.
#
#   Resolving the words first makes the field mode hs, repairing both halves
#   from one change.
def _resolveBareClass(acad, bare):
    implied = {}
    for (pid, ay), v in acad.items():
        if v["method"] == "corroborated" and v["grade"] and v["grade"].isdigit():
            implied.setdefault(pid, []).append(ay + (12 - int(v["grade"])))

    n = 0
    for pid, ay in bare:
        key = (pid, ay)
        v = acad.get(key)
        if v is None or v["grade"] not in ("FR", "SO", "JR", "SR"):
            continue
        rows = implied.get(pid)
        if not rows:
            continue                       # no evidence -> the lean stands
        rows = sorted(rows)
        want = 12 - (rows[len(rows) // 2] - ay)
        # ! ONLY INTO RANGE. An implied grade above 12 means the athlete has
        #   left school, which AGREES with the collegiate lean and needs no
        #   change. Below 1 is nonsense.
        if 1 <= want <= 12:
            acad[key] = {"grade": str(want), "level": None,
                         "method": "bare_class"}
            n += 1
    return n


# ------------------------------------------------------------------ #
#  RULE 5b: A SCHOOL GRADE THAT COMES AFTER COLLEGE
# ------------------------------------------------------------------ #

# ★ THE EVIDENCE IS THE RAW `SR-4` FORM, NOT normGrade's `SR`.
#
#   §1.6: a bare Fr/So/Jr/Sr is AMBIGUOUS -- 9-12 in high school, 13-16 in
#   college -- and only the tfrrs class+eligibility spelling (FR-1, SO-2,
#   JR-3, SR-4) may vote as collegiate. normGrade folds both onto 'SR', so the
#   verdict cannot tell them apart and this rule reads the raw column.
#
#   Measured: keying on normGrade's 'SR' flags 97,150 people -- overwhelmingly
#   high schoolers whose feed wrote 'Fr' one year and '11' the next, ordinary
#   progression and evidence of nothing. Keying on the eligibility form flags
#   879 people across 3,212 seasons.
#
# ⚠ AND IT IS DELIBERATELY NOT unlink.py's SIGNAL. unlink refuses word grades
#   on purpose: on a years-of-schooling scale Kerem Ayhan's implied class year
#   runs 2006 to 2012, a six-year spread that would split ONE REAL PERSON in
#   two. He is a Lehigh SR-4 who went pro and whose Garden State TC rows read
#   grade 12. The identity is right and the grade is wrong. This fixes the
#   grade and leaves the identity alone.
#
# ! ABSORBING, WHICH IS THE PHYSICAL FACT -- eligibility is spent once. Every
#   season after the FIRST such row is ineligible for a school grade.
#
#   Same ratchet shape as college_first_season, which was removed from pooling
#   for being dangerous. The difference is direction: that gate ASSERTED a
#   pool from one date and promoted everything after it; this one REMOVES a
#   verdict the data cannot support. A ratchet that subtracts claims fails
#   toward "we do not know"; one that adds them fails toward a seventh grader
#   on the college board.
# ★ COLLEGE BEGAN WHEN THE CLASS SAYS IT DID, NOT AT THE FIRST ROW WE HAVE
#   (owner, 2026-10-06: Elijah Ocegueda, "grade 11" for Performance Elite
#   Running Club in 2024-25 on the CA high school track board, then a JR-3
#   at Cal Poly Pomona in 2025-26). A JR-3 in 2025 is a third year: the
#   first college year was 2023, and the club season after it cannot be
#   high school. The definite spelling carries the year (FR-1 .. SR-6), so
#   the start is the row's academic year less (that number - 1).
# ! NOTHING BEFORE THE START MOVES: a FR-1 row says college began that year,
#   the same as before; only an upper-class first row reaches back.
_COLLEGIATE_SQL = """
    WITH elig AS (
        SELECT person_id,
               min(acad - (substring(upper(TRIM(raw_grade)) FROM '-([1-6])$')::int - 1))
                   AS first_acad
        FROM   allraces
        WHERE  definite
        GROUP  BY 1
    )
    SELECT DISTINCT a.person_id, a.acad
    FROM   allraces a
    JOIN   elig e ON e.person_id = a.person_id
    WHERE  a.acad > e.first_acad
"""


# rule 5b's college start per person, for rule 8
_COLLEGE_START_SQL = """
    SELECT person_id,
           min(acad - (substring(upper(TRIM(raw_grade)) FROM '-([1-6])$')::int - 1))
    FROM   allraces
    WHERE  definite
    GROUP  BY 1
"""


# ------------------------------------------------------------------ #
#  RESOLVE
# ------------------------------------------------------------------ #

# ================================================================== #
#  RULE 7 -- THE ELITE FIELD
# ================================================================== #
#
# ★ EVERY INPUT IS ONE THE POOLING DOES NOT PRODUCE, AND THAT IS THE POINT.
#   The rule reads the RAW grade, the RAW time and distance (a 5 km
#   equivalent by the normaliser's own exponent, ELITE_K), and the race's
#   ceiling from race_top_level -- the teams' own levels (level_graph), not
#   the pools. Judged on last run's pools or normalized times, a repooled
#   athlete would stop matching next run and flip back: a rule that
#   oscillates every night.
#
# ★ THE BAR IS MEASURED: the fastest ELITE_QUANTILE (1 in 10,000) of that
#   grade level's 5 km equivalents in races whose ceiling IS that level --
#   middle schoolers racing middle schoolers. Nothing a real middle schooler
#   runs in a middle school race is excluded by it, give or take one in ten
#   thousand.
#
# ! ELEMENTARY AND MIDDLE SCHOOL ONLY. A national-class high school senior
#   at a college invitational is a high schooler, and the fastest high
#   school times ARE in college races; a high school grade here would sweep
#   them into pro. College athletes with a stray school grade are the
#   college-team rule's (pool_resolve, team_level 'college'), so a school
#   the college directory lists is skipped too.
# ! DISTANCE RACES ONLY (1500 m to 10 km): a 5 km equivalent from a sprint
#   is an extrapolation, not a measurement.
ELITE_QUANTILE = 0.0001
ELITE_K = 1.06
# the senior 5000 m world records (men 12:35.36, women 14:00.21): the floor
# under which a 5 km equivalent is a data error, not a run
WR_5K_S = {"M": 755.36, "F": 840.21}


def _eliteRowsSql(sport, src=None):
    """The elite-field rows of one sport. `src` is the table the rows come
    from: eliteFieldSeasons passes its staged subset (tmp_elite_src)."""
    from level_graph import _raceKeyExpr
    if sport == "XC":
        dist = """COALESCE(dov.distance, m.distance,
                           (mt.division_distances -> r.div_id::text ->> 'distance')::real,
                           mt.distance)::float8"""
        joins = """LEFT JOIN meets m ON m.div_id = r.div_id AND m.source = r.source
                   LEFT JOIN meets_tfrrs mt ON r.source = 'tfrrs' AND mt.meet_id = r.meet_id
                                          AND mt.sport = 'XC'
                   LEFT JOIN dist_override dov ON dov.meet_id = r.meet_id
                                              AND dov.div_id = r.div_id"""
        table, extra = src or "results", ""
    else:
        from speed_ratings_db import _eventMetersSql
        dist = f"({_eventMetersSql('r')})::float8"
        joins = ""
        table, extra = src or "results_tf", "AND COALESCE(r.is_relay, 0) = 0"
    return f"""
        SELECT r.person_id, {_ACAD_R} AS acad, gradeLevel(normGrade(r.grade)) AS lvl,
               g.gender, rl.top_level,
               r.time_seconds::float8 * power(5000.0 / d.dist, {ELITE_K}) AS t5k,
               lower(btrim(COALESCE(r.school, ''))) AS school
        FROM   {table} r
        JOIN   race_top_level rl ON rl.race = {_raceKeyExpr('r')}
        JOIN   tmp_elite_gender g ON g.id = COALESCE(r.person_id, r.athlete_id)
        {joins}
        CROSS JOIN LATERAL (SELECT {dist} AS dist) d
        WHERE  r.person_id IS NOT NULL AND r.date IS NOT NULL
          AND  r.time_seconds > 0 AND d.dist BETWEEN 1500 AND 10000 {extra}
          AND  gradeLevel(normGrade(r.grade)) IN ('elem', 'ms')
          AND  rl.top_level IN ('elem', 'ms', 'college', 'pro')"""


def eliteFieldSeasons(cur):
    """{(person_id, academic year)} whose season holds an elementary or
    middle school grade on a row, in a college or pro race, faster than that
    level's measured bar. Empty (and says so) without race_top_level."""
    cur.execute("SELECT to_regclass('race_top_level')")
    if cur.fetchone()[0] is None:
        print("    [7] race_top_level absent (engine/level_graph.py --write): skipped")
        return set()
    colleges = set()
    cur.execute("SELECT to_regclass('college_directory')")
    if cur.fetchone()[0] is not None:
        cur.execute("SELECT lower(btrim(name)) FROM college_directory")
        colleges = {r[0] for r in cur.fetchall() if r[0]}
    # one gender per id, only where the athletes table agrees with itself
    cur.execute("""
        DROP TABLE IF EXISTS tmp_elite_gender;
        CREATE TEMP TABLE tmp_elite_gender AS
            SELECT athlete_id AS id, min(gender) AS gender
            FROM   athletes WHERE gender IN ('M', 'F')
            GROUP  BY athlete_id HAVING count(DISTINCT gender) = 1;
        CREATE INDEX ON tmp_elite_gender (id);
        ANALYZE tmp_elite_gender;""")
    out = set()
    # ★ STAGE THE FEW ROWS FIRST, THEN JOIN, AND NO NESTED LOOPS (run
    #   20261006_024705: the track half of this sat SIX HOURS in one CREATE
    #   TABLE, no lock wait, where the whole of 00-07 took 2h17m the day
    #   before). The one statement joined every results_tf row to
    #   race_top_level on an md5 expression before the grade filter -- a
    #   SQL function the planner cannot estimate -- so a plan flip to a
    #   nested loop is rows x races. The elementary/middle-school rows are a
    #   small slice: one sequential pass picks them into tmp_elite_src, it is
    #   ANALYZEd, and the joins run on that with nested loops off (the
    #   twin_flag rules' SESSION, for the same reason).
    cur.execute("SET enable_nestloop = off")
    for sport in ("XC", "TF"):
        table = "results" if sport == "XC" else "results_tf"
        relay = "AND COALESCE(r.is_relay, 0) = 0" if sport == "TF" else ""
        t0 = time.time()
        cur.execute(f"""
            DROP TABLE IF EXISTS tmp_elite_src;
            CREATE TEMP TABLE tmp_elite_src AS
                SELECT r.* FROM {table} r
                WHERE  r.person_id IS NOT NULL AND r.date IS NOT NULL
                  AND  r.time_seconds > 0 {relay}
                  -- a plain regex first: every grade normGrade reads as 1-8
                  -- or '7-8' starts with an optional run of zeros and a
                  -- digit 1-8 followed by a non-digit (schoolGrade's own
                  -- pattern, bandGrade's '7-8'), so the function stack
                  -- below runs only on those rows, not on every 9-12
                  AND  r.grade ~ '^\\s*0*[1-8]([^0-9]|$)'
                  AND  gradeLevel(normGrade(r.grade)) IN ('elem', 'ms')
                  -- ★ a national team's grade is not one (ntschool)
                  AND  NOT EXISTS (SELECT 1 FROM {_NT_TABLE} nt
                                   WHERE nt.school = r.school);
            ANALYZE tmp_elite_src;
            SELECT count(*) FROM tmp_elite_src;""")
        print(f"    [7] {sport}: {cur.fetchone()[0]:,} elementary/middle-school rows staged "
              f"({time.time() - t0:.0f}s)", flush=True)
        # ★ NOTHING FASTER THAN THE WORLD RECORD IS A TIME (run
        #   20261006_024705: the track bars read 0:38-0:53 for 5 km). The bar
        #   is the fastest 1 in 10,000, and among 17.5M track rows that
        #   quantile is junk -- field marks and mislabelled distances stored
        #   as times -- so the track half never fired. A 5 km equivalent under
        #   the senior world record for the gender is impossible, so those
        #   rows are out of the bar AND out of the comparison below.
        cur.execute(f"""
            CREATE TEMP TABLE tmp_elite AS
                SELECT * FROM ({_eliteRowsSql(sport, src="tmp_elite_src")}) e
                WHERE  e.t5k >= CASE e.gender WHEN 'F' THEN {WR_5K_S['F']}
                                              ELSE {WR_5K_S['M']} END;
            SELECT lvl, gender,
                   percentile_cont({float(ELITE_QUANTILE)!r}) WITHIN GROUP (ORDER BY t5k)
            FROM   tmp_elite WHERE top_level = lvl GROUP BY 1, 2""")
        # ! NO BIND PARAMETERS IN THAT STATEMENT: _eventMetersSql carries
        #   LIKE '%mile%', which psycopg2 would read as a placeholder.
        bars = {(lvl, g): float(b) for lvl, g, b in cur.fetchall() if b}
        cur.execute("""SELECT person_id, acad, lvl, gender, t5k, school
                       FROM tmp_elite WHERE top_level IN ('college', 'pro')""")
        n_rows = 0
        for pid, ay, lvl, g, t5k, school in cur.fetchall():
            bar = bars.get((lvl, g))
            if bar is None or t5k >= bar or school in colleges:
                continue
            out.add((int(pid), int(ay)))
            n_rows += 1
        cur.execute("DROP TABLE tmp_elite; DROP TABLE tmp_elite_src")
        print(f"    [7] {sport}: bars " + ", ".join(
            f"{lvl}_{g} {int(b // 60)}:{b % 60:04.1f}" for (lvl, g), b in sorted(bars.items()))
              + f" (5 km equivalent); {n_rows:,} rows under them in college/pro races")
    cur.execute("DROP TABLE IF EXISTS tmp_elite_gender")
    cur.execute("RESET enable_nestloop")
    return out


# ================================================================== #
#  RULE 8 -- THE ADULT CLUB'S "12"
# ================================================================== #
#
# ★ WHY (owner, 2026-10-06, the CA 2024 track board: Tanner Chada, "grade
#   12" for Gazelle Sports Elite at Bryan Clay, GVSU and Track Fest, and a
#   tfrrs freshman in 2015). An elite club's grade column is years with the
#   club, the same "6th pro year" rule 7 and the club rules exist for -- but
#   at 12 it reads as a senior and lands on the high school boards.
# ★ THREE SIGNALS, ALL REQUIRED, so an elite high schooler is never moved
#   (owner: "jackson spencer running one race at worlds doesn't mean he's
#   not in hs"):
#     1. no team the season names is a school: every row is a club
#        (CLUB_NAME_RE), unattached or blank, or a country (NOT_SCHOOL_RE);
#        ★ UNATTACHED AND COUNTRIES NO LONGER BLOCK (owner, 2026-10-11:
#        Bobby Poynter -- Tracksmith at the 2024 Trials, "United States" at
#        Portland, Unattached at USATF -- and Tommy Cardenas, Professional
#        Runners Club, both "12" on the HS boys board). Neither is a school,
#        and an unattached senior at elite meets is still kept by signal 2:
#        a real senior's season has races with high schoolers on top;
#     2. no race in the season had high schoolers on top: every race's
#        ceiling (race_top_level) is college or pro -- a real senior runs
#        league, section and state races;
#     3. AND EITHER the club is an adult club -- a majority of its athletes'
#        club seasons come after that athlete's college began, OR a majority
#        were pooled pro or college by the last build -- OR the athlete's own
#        earlier high school grade puts graduation before this season.
# ! A VERDICT, NOT A DELETION: the season pools pro (method 'adult_club'),
#   the rows stay rated and on the athlete's page.
CLUB_NAME_RE = (r"\m(club|tc|ac|xtc|elite|striders|endurance|athletics|"
                r"track (and|&) field|racing|harriers|youth|"
                # Spanish/Portuguese club words (owner, 2026-10-23: "Equipo
                # Albatros Tlalpan", a Mexican club at Bryan Clay, read as a
                # school and pooled hs_m "Grade 12")
                r"equipo|deportivo|atletismo)\M"
                # a brand run into "elite" (rabbitELITE, owner 2026-10-13, Jake
                # Ritter's 2024 season stayed a high school one on it): no word
                # boundary before it, and no school's name ends that way
                r"|elite\M")
ADULT_CLUB_SHARE = 0.5          # a majority, the club rules' own gate
# Rule 8's "not a school" (signal 1): unattached in any spelling, a blank or
# placeholder, or a country -- the same list as college_flag._NOT_SCHOOLS,
# spelled for Postgres (its \b is a backspace there, so no word boundaries).
NOT_SCHOOL_RE = (
    r"^$|unattached|^(una|unat|individual|independent|n/?a|none|no team|-+|\?+|0)$"
    r"|^(united states|united states of america|usa|u\.?s\.?a\.?|great britain"
    r"|canada|japan|kenya|ethiopia|jamaica|australia|new zealand|ireland|mexico"
    r"|puerto rico|south africa|nigeria|germany|france|italy|spain|poland|norway"
    r"|sweden|netherlands|belgium|portugal|brazil|china|india|uganda|eritrea"
    r"|morocco|algeria|bahrain|qatar|turkey|israel|philippines|colombia)$")


# ★ ONE QUESTION FOR EVERY CANDIDATE, NOT ONE PER CANDIDATE (owner's dry
#   run, 2026-10-27: grade_sanity --compare sat four hours on this step, and
#   held the nightly pg_dump's table locks behind it). It was a correlated
#   NOT EXISTS over a UNION ALL, which Postgres runs once per candidate --
#   and under rule 8's enable_nestloop = off each run hashed the whole of
#   race_top_level. Grades 6-12 (e4aa018, 2026-10-24) made the candidates
#   many. Now the schoolers are found in one set-based pass, the shape of
#   the facts query above it, and the rest are never_school.
_NEVER_SCHOOL_SQL = """
    WITH cand AS (SELECT DISTINCT person_id FROM tmp_club_cand),
    schooled AS (
        SELECT r.person_id
        FROM   results r JOIN race_top_level rl ON rl.race = {race_r}
        WHERE  r.person_id IN (SELECT person_id FROM cand)
          AND  rl.top_level IN ('elem', 'ms', 'hs')
        UNION
        SELECT r.person_id
        FROM   results_tf r JOIN race_top_level rl ON rl.race = {race_r}
        WHERE  r.person_id IN (SELECT person_id FROM cand)
          AND  rl.top_level IN ('elem', 'ms', 'hs'))
    SELECT c.person_id
    FROM   cand c LEFT JOIN schooled s ON s.person_id = c.person_id
    WHERE  s.person_id IS NULL"""


def adultClubSeasons(cur, hs_grade, college_start, adult_from=None):
    """{(person_id, acad): school} meeting rule 8's three signals. The
    school is '' for a season moved on the unattached arm (no club row).

    hs_grade: {(pid, acad): int grade} of the high school verdicts;
    college_start: {pid: first college academic year} (rule 5b's start);
    adult_from: {pid: first academic year past school} (priorAdultSeasons).
    Pure apart from the reads; the decision is _adultClubDecide."""
    adult_from = adult_from or {}
    from level_graph import _raceKeyExpr
    cur.execute("SELECT to_regclass('race_top_level')")
    if cur.fetchone()[0] is None:
        print("    [8] race_top_level absent: skipped")
        return {}
    t0 = time.time()
    # every row on a club-shaped team, either sport -- the club side of the
    # question, small next to the corpus
    cur.execute(f"""
        DROP TABLE IF EXISTS tmp_club_rows;
        CREATE TEMP TABLE tmp_club_rows AS
            SELECT r.person_id, {_ACAD_R} AS acad, lower(btrim(r.school)) AS club
            FROM   results r
            WHERE  r.person_id IS NOT NULL AND r.date IS NOT NULL
              AND  lower(r.school) ~ '{CLUB_NAME_RE}'
            UNION ALL
            SELECT r.person_id, {_ACAD_R}, lower(btrim(r.school))
            FROM   results_tf r
            WHERE  r.person_id IS NOT NULL AND r.date IS NOT NULL
              AND  lower(r.school) ~ '{CLUB_NAME_RE}';
        ANALYZE tmp_club_rows;""")
    cur.execute("SELECT DISTINCT person_id, acad, club FROM tmp_club_rows")
    club_seasons = {}
    for pid, ay, club in cur.fetchall():
        club_seasons.setdefault((int(pid), int(ay)), set()).add(club)
    # ★ A CLUB'S INITIALS ARE THE CLUB (owner, 2026-10-23, /athlete/2000003359:
    #   "Royal City Athletics Club" one season, "RCAC" the next, and RCAC
    #   read as a school, so the second season stayed hs_m "Grade 9"). A
    #   team spelled as the initials of a club the same athlete ran for, in
    #   any season, is that club: its rows join the club side here and do
    #   not count as a school in signal 1.
    alias_rows = clubAliases(club_seasons)
    cur.execute("DROP TABLE IF EXISTS tmp_club_alias; "
                "CREATE TEMP TABLE tmp_club_alias (person_id bigint, alias text, club text)")
    if alias_rows:
        from psycopg2.extras import execute_values as _ev
        _ev(cur, "INSERT INTO tmp_club_alias VALUES %s", alias_rows, page_size=10000)
        cur.execute(f"""
            SELECT DISTINCT r.person_id, r.acad, a.club
            FROM   (SELECT r.person_id, {_ACAD_R} AS acad, lower(btrim(r.school)) AS team
                    FROM results r WHERE r.date IS NOT NULL
                      AND r.person_id IN (SELECT person_id FROM tmp_club_alias)
                    UNION ALL
                    SELECT r.person_id, {_ACAD_R}, lower(btrim(r.school))
                    FROM results_tf r WHERE r.date IS NOT NULL
                      AND r.person_id IN (SELECT person_id FROM tmp_club_alias)) r
            JOIN   tmp_club_alias a ON a.person_id = r.person_id AND a.alias = r.team""")
        for pid, ay, club in cur.fetchall():
            club_seasons.setdefault((int(pid), int(ay)), set()).add(club)
    # the clubs' adult share, two ways, either enough:
    #   (a) of each club's (athlete, season)s, how many come after that
    #       athlete's college began;
    #   (b) how many the last build pooled pro or college (athlete_season).
    # ! (b) BECAUSE (a) ALONE MISSED ZAP ENDURANCE (owner's diag_board_people
    #   --team, 2026-10-06: 23%). Most pros carry no FR-1..SR-4 row in the
    #   corpus, so "after college" cannot see them -- but their seasons are
    #   already pooled pro by the no-grade and club rules. The candidates'
    #   own (high school) seasons are in the count and pull it DOWN, so a
    #   club reads adult only when its other athletes outnumber them.
    members = {}
    for (pid, ay), clubs in club_seasons.items():
        cs = college_start.get(pid)
        for c in clubs:
            m = members.setdefault(c, [0, 0])
            m[1] += 1
            m[0] += int(cs is not None and ay >= cs)
    adult = {c for c, (a, n) in members.items() if n and a / n > ADULT_CLUB_SHARE}
    cur.execute("SELECT to_regclass('athlete_season')")
    if cur.fetchone()[0] is not None:
        cur.execute(f"""
            SELECT c.club,
                   count(DISTINCT (c.person_id, c.acad))
                       FILTER (WHERE s.pool ~ '^(pro|college)_')      AS adult,
                   count(DISTINCT (c.person_id, c.acad))              AS n
            FROM   (SELECT DISTINCT person_id, acad, club FROM tmp_club_rows) c
            JOIN   athlete_season s ON s.person_id = c.person_id AND s.year = c.acad
            GROUP  BY 1""")
        adult |= {c for c, a, n in cur.fetchall() if n and a / n > ADULT_CLUB_SHARE}
    # the candidates: high school verdicts in a season with a club row, or
    # (the unattached arm) a season after the athlete was already past school
    grads = _graduations(hs_grade)
    cand = [k for k in hs_grade
            if k in club_seasons or _pastSchoolBy(k, grads, adult_from)]
    if not cand:
        print(f"    [8] no high school season on a club ({time.time() - t0:.0f}s)")
        return {}
    cur.execute("DROP TABLE IF EXISTS tmp_club_cand; "
                "CREATE TEMP TABLE tmp_club_cand (person_id bigint, acad int)")
    from psycopg2.extras import execute_values
    execute_values(cur, "INSERT INTO tmp_club_cand VALUES %s", cand, page_size=10000)
    cur.execute("ANALYZE tmp_club_cand")
    # signals 1 and 2 for the candidates only: any non-club school, and any
    # race whose ceiling is not college or pro
    cur.execute("SET enable_nestloop = off")
    cur.execute(f"""
        SELECT c.person_id, c.acad,
               -- a SCHOOL row blocks the move; a club, unattached, blank or
               -- country row does not (signal 1, owner 2026-10-11)
               bool_or(NOT (lower(btrim(COALESCE(r.school, ''))) ~ '{CLUB_NAME_RE}'
                            OR lower(btrim(COALESCE(r.school, ''))) ~ '{NOT_SCHOOL_RE}'
                            OR ca.alias IS NOT NULL))
                                                                           AS has_school,
               bool_and(rl.top_level IN ('college', 'pro'))                AS all_senior,
               bool_or(rl.top_level = 'pro')                               AS any_pro
        FROM   tmp_club_cand c
        JOIN   (SELECT r.person_id, {_ACAD_R} AS acad, r.school, r.meet_id, r.div_id, r.source
                FROM results r WHERE r.person_id IN (SELECT person_id FROM tmp_club_cand)
                UNION ALL
                SELECT r.person_id, {_ACAD_R}, r.school, r.meet_id, r.div_id, r.source
                FROM results_tf r WHERE r.person_id IN (SELECT person_id FROM tmp_club_cand)) r
               ON r.person_id = c.person_id AND r.acad = c.acad
        LEFT   JOIN race_top_level rl ON rl.race = {_raceKeyExpr('r')}
        LEFT   JOIN tmp_club_alias ca ON ca.person_id = r.person_id
                                     AND ca.alias = lower(btrim(COALESCE(r.school, '')))
        GROUP  BY 1, 2""")
    facts, pro_seasons = {}, set()
    for p, a, hs, sen, pro in cur.fetchall():
        facts[(int(p), int(a))] = (bool(hs), bool(sen))
        if pro:
            pro_seasons.add((int(p), int(a)))
    # the candidates who never ran a race with school runners on top, any
    # season, either sport (_adultClubDecide's never_school)
    cur.execute(_NEVER_SCHOOL_SQL.format(race_r=_raceKeyExpr("r")))
    never_school = {int(p) for (p,) in cur.fetchall()}
    cur.execute("RESET enable_nestloop")
    cur.execute("DROP TABLE tmp_club_rows; DROP TABLE tmp_club_cand; DROP TABLE tmp_club_alias")
    out = _adultClubDecide(cand, facts, club_seasons, adult, hs_grade, adult_from,
                           never_school, pro_seasons)
    n_un = sum(1 for v in out.values() if not v)
    print(f"    [8] {len(cand):,} high school seasons on a club or past school, "
          f"{len(adult):,} adult clubs, {len(out) - n_un:,} moved on a club, "
          f"{n_un:,} unattached ({time.time() - t0:.0f}s)")
    return out


# ================================================================== #
#  RULE 5e -- A COLLEGE FIELD IS NOT A COLLEGE SEASON PAST ELIGIBILITY
# ================================================================== #
#
# ⚠ THE FAULT (owner, 2026-10-07, John Rivera, /athlete/12652858). Ole
#   Miss FR-1 in academic 2017, senior in 2021, a Brooks Beasts professional
#   since. His 2026 season -- two 800s in August, one for Brooks Beasts, one
#   for Puerto Rico, both grade '-' -- has no grade, so the field rule read
#   the races' level and answered 'college', trust high, and the rows pooled
#   college_m at 124.1. Rule 5b exists for exactly this athlete and passed
#   him by: it overturns only a BELOW-college verdict ("EVERYTHING ELSE IS
#   LEFT ALONE"), because a college verdict after a college start is
#   usually a collegian still racing.
#
# ★ THE CALENDAR SETTLES IT, NOT THE SCHOOL STRING. NCAA competition is
#   five seasons inside six years, and the 2020 COVID year added one: no
#   academic year POST_ELIGIBILITY_SEASONS or more after the first college
#   season can be a college season. The first season is _COLLEGE_START_SQL's,
#   the earliest academic year minus the tfrrs eligibility digit, so a
#   redshirt only moves it later -- the rule can only fire later, never
#   earlier. No school name is read: poolFor's note on why a club string
#   never sets a pool stands.
#
# ! A FIELD VERDICT ONLY. That is the verdict with no evidence of the
#   athlete's own -- "the level of the races they ran" -- and a college
#   invitational's level is what a professional opening his summer there
#   inherits. A corroborated class word or a majority is the athlete's own
#   rows speaking and is left to the rules that read them.
POST_ELIGIBILITY_SEASONS = 7


# the verdicts whose level pastSchoolLevel decides
_PAST_SCHOOL = ("graduated", "past_school", "stale_class")


def staleVerdict(grade):
    """Rule 5's verdict for a grade held too long. A school grade is a
    record nobody maintains: professional. A COLLEGE CLASS WORD is
    'stale_class', which the past-school re-level decides by the season's
    races (pastSchoolLevel) -- still pro there unless they were college.

    ★ WHY (owner, 2026-10-18, /athlete/24413256: Ryan Wilson's fifth MIT
      season, NCAA DIII nationals, labelled Professional and unrated).
      The COVID year made a third 'SR' ordinary: a senior in 2021, a fifth-
      year senior in 2022, and the feed's word does not move. Three years
      at SR is not a professional; on college start lines it is a college
      season with no class to trust."""
    try:
        from normalize_distance import isCollegeClass
        college = isCollegeClass(grade)
    except Exception:                                    # noqa: BLE001
        college = False
    return {"grade": None, "level": "pro",
            "method": "stale_class" if college else "stale_grade"}


def pastEligibilitySeasons(acad, college_start, gap=None):
    """Rule 5e's decision, pure: the (person_id, acad) keys whose verdict is
    a field-derived 'college' with no grade, at least `gap` academic years
    (POST_ELIGIBILITY_SEASONS) after the person's first college season."""
    gap = POST_ELIGIBILITY_SEASONS if gap is None else gap
    out = []
    for (pid, ay), v in acad.items():
        if v.get("method") != "field" or v.get("level") != "college" \
                or v.get("grade") is not None:
            continue
        first = college_start.get(pid)
        if first is not None and ay - first >= gap:
            out.append((pid, ay))
    return sorted(out)


_ALIAS_SKIP = {"the", "and", "of", "&", "de", "del", "la", "el", "y"}


def clubAliases(club_seasons):
    """[(person_id, initials, club)] for every club a person ran for whose
    initials are 3+ letters ("royal city athletics club" -> "rcac"). Pure."""
    out = set()
    for (pid, _ay), clubs in club_seasons.items():
        for c in clubs:
            words = [w for w in re.split(r"[^a-z0-9]+", c or "") if w and w not in _ALIAS_SKIP]
            ini = "".join(w[0] for w in words)
            if len(ini) >= 3:
                out.add((pid, ini, c))
    return sorted(out)


def _graduations(hs_grade):
    """{pid: [academic year each high school grade says they graduate]}."""
    grads = {}
    for (pid, ay), g in hs_grade.items():
        grads.setdefault(pid, []).append(ay + (12 - g))
    return grads


def _pastSchoolBy(key, grads, adult_from):
    """Was this athlete already past school before this season? Their own
    earlier high school grade graduates them first, or an earlier season of
    theirs is already professional or college."""
    pid, ay = key
    first = adult_from.get(pid)
    return (any(gy < ay for gy in grads.get(pid, ()))
            or (first is not None and first < ay))


def _adultClubDecide(cand, facts, club_seasons, adult, hs_grade, adult_from=None,
                     never_school=None, pro_seasons=None):
    """Rule 8's decision, pure. {(pid, acad): club} of the seasons moved;
    the club is '' for the unattached arm (a season with no club row).

    ★ never_school / pro_seasons (owner, 2026-10-23, /athlete/28420869: a
      Mexican club runner, only ever at Bryan Clay, "Grade 12" two years
      running): a club nobody else in the corpus runs for cannot be shown
      adult by its members. An athlete who has NEVER run a race with school
      runners on top (never_school: persons), on a club, in a season with a
      professional race in it (pro_seasons: keys), is an adult club runner
      too. A junior on a youth club at college meets has no pro race, and
      one school race anywhere keeps them."""
    grads = _graduations(hs_grade)
    adult_from = adult_from or {}
    never_school = never_school or set()
    pro_seasons = pro_seasons or set()
    out = {}
    for key in cand:
        has_school, all_senior = facts.get(key, (True, False))
        if has_school or not all_senior:
            continue
        clubs = club_seasons.get(key, set())
        past = _pastSchoolBy(key, grads, adult_from)
        if clubs:
            if clubs & adult or past or (key[0] in never_school and key in pro_seasons):
                out[key] = sorted(clubs)[0]
        elif past:
            out[key] = ""
    return out


def priorAdultSeasons(acad, cur=None):
    """({pid: first adult academic year}, {pid: first professional one}).

    Adult is a verdict of level pro or college, or a college class word;
    with `cur`, the last build's pro_/college_ pools (athlete_season) too --
    most professionals carry no class row, rule 8's own reason for reading
    that table."""
    adult, pro = {}, {}

    def _put(d, pid, ay):
        if d.get(pid) is None or ay < d[pid]:
            d[pid] = ay

    for (pid, ay), v in acad.items():
        if v.get("level") == "pro":
            _put(adult, pid, ay)
            _put(pro, pid, ay)
        elif v.get("level") == "college" or v.get("grade") in ("FR", "SO", "JR", "SR"):
            _put(adult, pid, ay)
    if cur is not None:
        cur.execute("SELECT to_regclass('athlete_season')")
        if cur.fetchone()[0] is not None:
            cur.execute("""
                SELECT person_id, min(year),
                       min(year) FILTER (WHERE pool ~ '^pro_')
                FROM   athlete_season WHERE pool ~ '^(pro|college)_'
                GROUP  BY 1""")
            for pid, a, p in cur.fetchall():
                _put(adult, int(pid), int(a))
                if p is not None:
                    _put(pro, int(pid), int(p))
    return adult, pro


def seasonCeilings(cur, keys):
    """{(pid, acad): (college races, pro races)} by race_top_level, for the
    given seasons only (rule 8's facts query, narrowed to the counts)."""
    from level_graph import _raceKeyExpr
    keys = list(keys)
    if not keys:
        return {}
    cur.execute("SELECT to_regclass('race_top_level')")
    if cur.fetchone()[0] is None:
        return {}
    from psycopg2.extras import execute_values
    cur.execute("DROP TABLE IF EXISTS tmp_ceil_keys; "
                "CREATE TEMP TABLE tmp_ceil_keys (person_id bigint, acad int)")
    execute_values(cur, "INSERT INTO tmp_ceil_keys VALUES %s", keys, page_size=10000)
    cur.execute("ANALYZE tmp_ceil_keys")
    cur.execute("SET enable_nestloop = off")
    cur.execute(f"""
        SELECT c.person_id, c.acad,
               count(*) FILTER (WHERE rl.top_level = 'college'),
               count(*) FILTER (WHERE rl.top_level = 'pro')
        FROM   tmp_ceil_keys c
        JOIN   (SELECT r.person_id, {_ACAD_R} AS acad, r.meet_id, r.div_id, r.source
                FROM results r WHERE r.person_id IN (SELECT person_id FROM tmp_ceil_keys)
                UNION ALL
                SELECT r.person_id, {_ACAD_R}, r.meet_id, r.div_id, r.source
                FROM results_tf r WHERE r.person_id IN (SELECT person_id FROM tmp_ceil_keys)) r
               ON r.person_id = c.person_id AND r.acad = c.acad
        JOIN   race_top_level rl ON rl.race = {_raceKeyExpr('r')}
        GROUP  BY 1, 2""")
    out = {(int(p), int(a)): (int(nc), int(np_)) for p, a, nc, np_ in cur.fetchall()}
    cur.execute("RESET enable_nestloop")
    cur.execute("DROP TABLE tmp_ceil_keys")
    return out


def pastSchoolLevel(key, ceilings, pro_from, school_out=None):
    """The level of a season past school: college when most of its races
    had a college ceiling, the athlete has not already been a professional,
    and it is inside NCAA eligibility of their first year out of school
    (school_out, POST_ELIGIBILITY_SEASONS, rule 5e's calendar) -- a redshirt
    freshman racing unattached at college meets. Professional otherwise."""
    pid, ay = key
    first_pro = pro_from.get(pid)
    out = (school_out or {}).get(pid)
    n_college, n_pro = ceilings.get(key, (0, 0))
    if n_college > n_pro and (first_pro is None or first_pro >= ay) \
            and (out is None or ay - out < POST_ELIGIBILITY_SEASONS):
        return "college"
    return "pro"


# ★ RULE 5d IN SQL (run 20261006_120609: five hours between rule 7 and the
#   verdicts). It fetched every graded row of allraces -- most of 225M --
#   into Python to count, per event, how many entrants were contradicted
#   seasons. An event passes only with MIN_FIELD_GRADED entrants of whom
#   NUKED_FIELD_SHARE are contradicted, so it must hold at least one: the
#   events are found from the contradicted seasons by allraces' (person_id,
#   acad, race) index, and only their rows are counted. Same counts as the
#   loop it replaces -- rows, not people, a person twice in one event twice --
#   and the same float comparison (float8, as Python's).
def thinFieldSeasons(cur, nuked_seasons):
    """{(person_id, acad)} entered in an event at least MIN_FIELD_GRADED
    graded entrants strong of which NUKED_FIELD_SHARE are contradicted."""
    if not nuked_seasons:
        return set()
    from psycopg2.extras import execute_values
    cur.execute("DROP TABLE IF EXISTS tmp_nuked; "
                "CREATE TEMP TABLE tmp_nuked (person_id bigint, acad int, "
                "PRIMARY KEY (person_id, acad))")
    execute_values(cur, "INSERT INTO tmp_nuked VALUES %s",
                   sorted(nuked_seasons), page_size=10000)
    cur.execute("ANALYZE tmp_nuked")
    cur.execute("""
        WITH ev AS (
            SELECT DISTINCT a.meet_id, a.div_id, a.source, a.event_key
            FROM   tmp_nuked n
            JOIN   allraces a ON a.person_id = n.person_id AND a.acad = n.acad
            WHERE  a.grade_folded IS NOT NULL
        ),
        thin AS (
            SELECT a.meet_id, a.div_id, a.source, a.event_key
            FROM   ev
            JOIN   allraces a ON a.meet_id = ev.meet_id AND a.div_id = ev.div_id
                             AND a.source = ev.source AND a.event_key = ev.event_key
            LEFT   JOIN tmp_nuked n ON n.person_id = a.person_id AND n.acad = a.acad
            WHERE  a.grade_folded IS NOT NULL
            GROUP  BY 1, 2, 3, 4
            HAVING count(*) >= %(minf)s
               AND count(n.person_id)::float8 >= %(share)s::float8 * count(*)::float8
        )
        SELECT DISTINCT a.person_id, a.acad
        FROM   thin t
        JOIN   allraces a ON a.meet_id = t.meet_id AND a.div_id = t.div_id
                         AND a.source = t.source AND a.event_key = t.event_key
        WHERE  a.grade_folded IS NOT NULL
    """, {"minf": MIN_FIELD_GRADED, "share": NUKED_FIELD_SHARE})
    out = {(int(p), int(a)) for p, a in cur.fetchall()}
    cur.execute("DROP TABLE tmp_nuked")
    return out


def resolve(cur, audit=False):
    """{(person_id, calendar_year): {grade, level, method}}.

    Reasoned per academic year, then written to every calendar year that
    academic year touches. Where a calendar year receives two verdicts -- the
    graduating case -- the LATER academic year wins, because grades only move
    forward.
    """
    print("[grade] building the shared race table...")
    # ! SUBSTITUTED, NOT FORMATTED. _BUILD is already an f-string and its SQL
    #   carries `$$`-quoted function bodies; a second .format() pass over it
    #   would have to escape every brace in the file. A plain replace of two
    #   comment-shaped tokens touches nothing else.
    xc_join, tf_join = _ageBandJoins(cur)
    # the build anti-joins result_twin, which 04c writes AFTER this step:
    # on a database that has never run 04c the table is created empty here
    from twin_flag import ensureTable as _ensureTwin
    _ensureTwin(cur)
    # the national-team strings the build's CASEs null (see ntschool)
    _nationalTeamSchools(cur)
    build = (_BUILD.replace(_AGE_BAND_TOKENS[0], xc_join)
                   .replace(_AGE_BAND_TOKENS[1], tf_join))
    for _tok in _AGE_BAND_TOKENS:
        assert _tok not in build, f"age-band token {_tok} was not substituted"
    # ★ ONE STATEMENT AT A TIME, EACH TIMED (2026-09-06). The build ran as
    #   one 300-line execute and the step took four to five hours with no
    #   line saying where; the log could not tell the 230M-row table from
    #   its indexes from the self-joins after it. Now every statement that
    #   takes over a second prints its first line and its seconds.
    runTimed(cur, build, label="build")

    # Reports only. reportRejected scans both results tables in full, so it
    # is opt-in; reportUngraded reads the temp table and is cheap.
    if audit:
        reportRejected(cur)
    reportFold(cur)
    reportUngraded(cur)

    # The academic-year verdicts, in precedence order.
    acad = {}

    _tq = time.time()
    cur.execute(_CORROBORATED_SQL, {"minc": MIN_CORROBORATION})
    print(f"    [2] corroborated grades read ({time.time() - _tq:.0f}s)", flush=True)
    for pid, ay, grade, n in cur.fetchall():
        acad[(int(pid), int(ay))] = {"grade": grade, "level": None,
                                     "method": "corroborated"}
    n_corr = len(acad)

    # ★ RULE 0: THE OWNER'S PINS, BEFORE EVERY RULE AND AFTER THEM ALL
    #   (2026-10-10). An athlete reported a wrong grade ("Suggest a fix",
    #   racecast/fixes.py) and the owner approved it; the answer lives in
    #   grade_pin (scripts/person_pins.py), because grade_fix is rebuilt from
    #   scratch here every run and a correction written into it would last
    #   one night. Laid in HERE, beside the corroborated grades, so the rules
    #   below that read a season's neighbours (3b, 5, 5b) see the pinned
    #   season as settled evidence; laid in AGAIN after trust, so no rule can
    #   overwrite it. method 'pinned', trust 'high'.
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                    "scripts"))
    import person_pins as _PP
    grade_pins = _PP.loadGradePins(cur)
    n_pinned = _PP.applyGradePins(acad, grade_pins)
    print(f"    [0] owner's grade pins laid in: {n_pinned:,}", flush=True)

    # ! RULE 2c, BETWEEN 2 AND 4 ON PURPOSE. It rewrites grades rule 4 is
    #   about to read as field evidence; running it afterwards would leave
    #   every ungraded athlete in those fields pooled off the wrong mode.
    _tq = time.time()
    cur.execute(_BARE_CLASS_SQL)
    print(f"    [2c] bare class words read ({time.time() - _tq:.0f}s)", flush=True)
    n_bare = _resolveBareClass(
        acad, [(int(p), int(a)) for p, a in cur.fetchall()])

    # ! TWO ROUNDS, WITH A WRITE-BACK BETWEEN THEM. Round one resolves the
    #   pockets it can see; the write-back makes those races visibly
    #   school-level; round two picks up the races that were just under the
    #   80% line before their pockets were fixed.
    n_bare_field = 0
    for _round in (1, 2):
        _tq = time.time()
        cur.execute(_BARE_FIELD_SQL, {"minf": MIN_FIELD_GRADED,
                                      "share": _FIELD_SCHOOL_SHARE})
        got = _resolveBareClassByField(acad, cur.fetchall())
        n_bare_field += got
        _tw = time.time()
        # The last write-back also stands for rule 4, which reads the field
        # after this loop.
        moved = _writeBackBareClass(cur, acad)
        print(f"    [2c round {_round}] {got:,} seasons, "
              f"{moved:,} rows relabelled in the field "
              f"({_tw - _tq:.0f}s field, {time.time() - _tw:.0f}s write-back)", flush=True)
        if not got:
            break

    # ! THE METHOD NAME IS DERIVED FROM THE LEVEL, NOT ASKED FOR SEPARATELY.
    #   Edit 2 gives the field rule two ways to speak, and they are not the
    #   same claim: 'the races this athlete ran were mostly high school' is a
    #   mode over evidence, while 'not one entrant anywhere carried a grade'
    #   is the absence of school-ness. Only the second can emit 'pro', so the
    #   level identifies its own origin and the two stay countable apart.
    _tq = time.time()
    runTimed(cur, _FIELD_BUILD, label="[4] staging")
    cur.execute(_FIELD_SQL)
    _field_rows = cur.fetchall()
    cur.execute(_FIELD_DROP)
    print(f"    [4] field levels read ({time.time() - _tq:.0f}s)", flush=True)
    n_nograde = 0
    for pid, ay, level in _field_rows:
        key = (int(pid), int(ay))
        if key in acad:
            continue
        method = "no_grades" if level == "pro" else "field"
        n_nograde += (method == "no_grades")
        acad[key] = {"grade": None, "level": level, "method": method}
    n_field = len(acad) - n_corr - n_nograde

    _tq = time.time()
    cur.execute(_STALE_SQL, {"maxy": MAX_YEARS_AT_GRADE})
    print(f"    [5] stale grades read ({time.time() - _tq:.0f}s)", flush=True)
    stale = {(int(p), int(a)): g for p, a, g in cur.fetchall()}
    n_stale = 0
    for key, g in stale.items():
        # ! STALE OVERRIDES A CORROBORATED GRADE, and it has to. A record that
        #   says SR for four years IS well corroborated -- that is exactly the
        #   problem with it. Rule 5 is about the grade being unmaintained, not
        #   about it being unattested.
        acad[key] = staleVerdict(g)
        n_stale += 1

    # ★ RULE 7, THE ELITE FIELD (owner, 2026-09-26, the David Hemery Valentine
    #   3000: "a lot of them should be pooled as pro but def not as msers").
    #   A middle- or elementary-school grade on a row in a college or pro
    #   race, run faster than that level has ever run in its own races, is
    #   not a school grade -- on a club it is usually years with the club.
    #   Overrides a corroborated grade for the same reason rule 5 does: a
    #   club's "8" is consistent every season, which is not the same as true.
    n_elite = 0
    for key in eliteFieldSeasons(cur):
        acad[key] = {"grade": None, "level": "pro", "method": "elite_field"}
        n_elite += 1
    print(f"    [7] {n_elite:,} seasons: a school grade that out-ran its own "
          f"level in a college or pro race -> pro")

    # ============================================================== #
    #  RULE 3b: THE GRADE THAT DOES NOT FIT THE PROGRESSION
    #
    #  ★ IMPLIED CLASS YEAR IS THE INVARIANT, AND IT IS unlink.py's SIGNAL
    #    USED FOR THE OPPOSITE PURPOSE. There, a wide spread means one id
    #    holds two people. Here -- once the identity is settled -- a narrow
    #    spread with one outlier means one season holds a wrong grade. Same
    #    arithmetic, opposite conclusion, and the two do not conflict:
    #    unlink runs on raw seasons and splits identities, this runs on
    #    verdicts and corrects grades.
    #
    #  ! CORRECTED, NOT DELETED. Rule 3 already says an odd value near a
    #    corroborated one is a correction rather than a conflict. This is
    #    that sentence applied across years instead of within one, so the
    #    season keeps a grade -- the grade the progression implies.
    #
    #  ⚠ ONLY CORROBORATED NUMERIC VERDICTS PARTICIPATE, IN BOTH DIRECTIONS.
    #    A field verdict carries no grade to test and no grade to correct. A
    #    college class word has no year offset that survives this arithmetic
    #    -- a Senior in 2018 and a Senior in 2023 are both legitimately
    #    senior, which is exactly why unlink.py refuses word grades too.
    # ============================================================== #
    n_progress = 0
    by_person = {}
    for (pid, ay), v in acad.items():
        if v["method"] == "corroborated" and v["grade"] and v["grade"].isdigit():
            by_person.setdefault(pid, []).append((ay, int(v["grade"])))

    for pid, rows in by_person.items():
        if len(rows) < MIN_SEASONS_FOR_PROGRESSION:
            continue
        implied = sorted(ay + (12 - g) for ay, g in rows)
        # Median, not mode: robust to one outlier without needing an exact
        # repeat, and with three or more seasons the majority owns it.
        mid = implied[len(implied) // 2]
        for ay, g in rows:
            if abs((ay + (12 - g)) - mid) <= MAX_GRADE_DRIFT:
                continue
            want = 12 - (mid - ay)
            if not 1 <= want <= 12:
                # Past twelfth grade on this athlete's own progression. Rule
                # 5b reaches the same verdict from collegiate evidence; this
                # reaches it from arithmetic.
                # ! PRO HERE, AND RE-LEVELLED AFTER RULE 8 (_PAST_SCHOOL):
                #   college when the season's races are college ones.
                acad[(pid, ay)] = {"grade": None, "level": "pro",
                                   "method": "graduated"}
            else:
                acad[(pid, ay)] = {"grade": str(want), "level": None,
                                   "method": "progression"}
            n_progress += 1

    # ! RULE 5b RUNS LAST, AND OVERRIDES A CORROBORATED GRADE. A record
    #   reading '12' for three post-collegiate years IS well corroborated --
    #   that is exactly what is wrong with it. Like rule 5, this is about the
    #   grade being impossible, not about it being unattested.
    #
    # ! ONLY A BELOW-COLLEGE VERDICT IS OVERTURNED, BY GRADE *OR* BY LEVEL.
    #
    #   A numeric grade is the obvious case, but the field rule reaches the
    #   same wrong conclusion without one: an athlete with no grade who raced
    #   somewhere school-heavy gets level 'hs'. Measured over the 879 people
    #   this rule sees -- 1,606 seasons carry a numeric grade and a further
    #   704 carry a school LEVEL with no grade at all. Checking only the grade
    #   would leave 30% of them behind.
    #
    # ⚠ EVERYTHING ELSE IS LEFT ALONE, AND THE VOLUMES SAY WHY. The same
    #   population holds 518,818 seasons corroborated FR/SO/JR/SR -- athletes
    #   still racing collegiately, which is exactly what should follow an
    #   SR-4 -- plus 21,935 already stale_grade and 17,627 already college.
    #   This is a narrow subtraction, not a sweep: it fires on 2,310 of
    #   roughly 563,000 seasons behind these athletes.
    _SCHOOL_LEVELS = ("elem", "ms", "hs")
    t5b = time.time()
    cur.execute(_COLLEGIATE_SQL)
    n_postcoll = 0
    _coll_rows = cur.fetchall()
    print(f"    [5b] {len(_coll_rows):,} seasons after a college start "
          f"({time.time() - t5b:.0f}s)", flush=True)
    for pid, ay in _coll_rows:
        key = (int(pid), int(ay))
        v = acad.get(key)
        if v is None:
            continue
        g, lv = v["grade"], v["level"]
        below = (g is not None and g.isdigit()) or lv in _SCHOOL_LEVELS
        if not below:
            continue
        acad[key] = {"grade": None, "level": "pro",
                     "method": "post_collegiate"}
        n_postcoll += 1

    # ★ RULE 5e, PAST ELIGIBILITY (see pastEligibilitySeasons). With 5b,
    #   on the field verdicts 5b leaves alone; the college starts it reads
    #   are rule 8's too.
    cur.execute(_COLLEGE_START_SQL)
    college_start = {int(p): int(a) for p, a in cur.fetchall()}
    moved5e = pastEligibilitySeasons(acad, college_start)
    for key in moved5e:
        acad[key] = {"grade": None, "level": "pro",
                     "method": "post_collegiate"}
    print(f"    [5e] {len(moved5e):,} field 'college' verdicts "
          f"{POST_ELIGIBILITY_SEASONS}+ seasons after a college start "
          f"made professional", flush=True)

    # ★ RULE 8, THE ADULT CLUB (see adultClubSeasons). After 5b, on the high
    #   school verdicts still standing; college starts are 5b's own.
    # ★ GRADES 6-12, NOT 9-12 (owner, 2026-10-23, /athlete/2000003359: a
    #   Royal City AC adult at Track Night NYC, "Grade 7", rated ms_m 159.7).
    #   A club's grade column is years with the club at any number; signal 2
    #   (only college and pro races) is what keeps a real middle schooler.
    hs_grade = {k: int(v["grade"]) for k, v in acad.items()
                if v["grade"] and str(v["grade"]).isdigit() and 6 <= int(v["grade"]) <= 12}
    # ★ AND THE UNATTACHED ARM (owner, 2026-10-12, /athlete/2000002438 and
    #   2000002910): Jason Intravaia, a professional in 2024 (PDC), then
    #   "Grade 10" unattached at the UW indoor meets and #3 on the HS
    #   sophomore board; Jack Falkowski, a redshirt freshman racing unattached
    #   at college meets, "Grade 9". Two unattached rows agreeing on a grade
    #   corroborate it (rule 2), and a corroborated grade overrules the
    #   race's college ceiling in resolvePool (Kitchen's screen) -- so the
    #   season pooled high school. A grade only moves forward: a season with
    #   no school row, only college and pro races, AFTER the athlete was past
    #   school (graduated by their own grade, or an earlier adult season) is
    #   not a high school season. College when its field is college and no
    #   earlier season was professional; professional otherwise.
    adult_from, pro_from = priorAdultSeasons(acad, cur)
    moved8 = adultClubSeasons(cur, hs_grade, college_start, adult_from)
    n_unatt = 0
    for key, club in moved8.items():
        if club:
            acad[key] = {"grade": None, "level": "pro", "method": "adult_club"}
        else:
            acad[key] = {"grade": None, "level": "pro", "method": "past_school"}
            n_unatt += 1
    # ★ THE LEVEL OF A SEASON PAST SCHOOL (rule 3b's 'graduated' and the
    #   unattached arm): college when most of its races were college races
    #   and no earlier season was professional -- Jack Falkowski's redshirt
    #   year at college meets -- else professional, as before. The earlier
    #   professional seasons are read without these two methods' own.
    _relevel = [k for k, v in acad.items() if v["method"] in _PAST_SCHOOL]
    _ceil = seasonCeilings(cur, _relevel)
    _keep = {k: v for k, v in acad.items() if v["method"] not in _PAST_SCHOOL}
    _a, pro_before = priorAdultSeasons(_keep, cur)
    # the first academic year out of school: after the earliest graduation a
    # school grade implies, or the first college season
    school_out = dict(college_start)
    for (pid, ay), v in _keep.items():
        g = v.get("grade")
        if g and str(g).isdigit() and 1 <= int(g) <= 12:
            gy = ay + (12 - int(g)) + 1
            if school_out.get(pid) is None or gy < school_out[pid]:
                school_out[pid] = gy
    n_to_college = 0
    for key in _relevel:
        lvl = pastSchoolLevel(key, _ceil, pro_before, school_out)
        acad[key]["level"] = lvl
        n_to_college += lvl == "college"
    print(f"    [8] {len(_relevel):,} seasons past school, {n_to_college:,} of "
          f"them college by their races", flush=True)
    n_adult = len(moved8)
    for (pid, ay), club in list(moved8.items())[:12]:
        print(f"        e.g. person {pid} season {ay}: {club}")

    # ! NO CALENDAR SPREAD. The verdict is reasoned per academic year and now
    #   written per academic year -- one row, one clock. The old two-row
    #   spread collided on the shared calendar year and handed the spring
    #   half of a season the following autumn's grade.
    # ============================================================== #
    #  RULE 6: AN EXPLICIT VERDICT OF "NO EVIDENCE"
    #
    #  ★ ABSENCE AND IGNORANCE LOOK IDENTICAL TO A LEFT JOIN. A season with
    #    no row here reaches resolvePool as fixed_grade=None,
    #    fixed_level=None -- exactly what a season whose verdict carries no
    #    grade also looks like. So the pool falls through to the raw grade,
    #    and then to the SCHOOL NAME.
    #
    #    Measured: Alexa Hernandez has ONE race, its grade reads '-', and
    #    normGrade correctly nulls it. No corroboration, no field verdict, no
    #    row. resolvePool then read her school -- "Stockton Rising Club" --
    #    off the race-graph levels and put her third on the national middle
    #    school girls performance board at 159.8, on the strength of a club
    #    name and nothing else.
    #
    #  ! SO THE GAP IS WRITTEN DOWN. A row with grade NULL, level NULL and
    #    method 'no_evidence' says "this athlete-season was examined and
    #    nothing could be established", which is a different sentence from
    #    silence and can be acted on.
    #
    #  ⚠ SMALL, AND THAT IS THE POINT. Measured at 3,407 athlete-seasons
    #    since August 2024 -- 0.02% of the corpus. The rules above reach
    #    almost everything; this names the remainder instead of letting the
    #    weakest signal in the system decide it by default.
    # ============================================================== #
    #  RULE 5c: THE SEASON THAT CONTRADICTS ITSELF
    #
    #  ★ WHEN THE WORD WINS A MIXED SEASON, NOTHING WON. gradekind counts a
    #    season's numeric races against its word races and keeps the larger.
    #    That is defensible when the numbers win -- a number is unambiguous
    #    and a bare word is not (§1.6). It is not defensible the other way:
    #    the athlete's own feeds disagree about whether they are in school,
    #    and the tiebreak is how many races each feed happened to scrape.
    #
    #    So the season is thrown away rather than decided. No verdict, no
    #    pool, no rating -- which for these is the right answer, because
    #    every rule downstream would be building on a coin flip.
    #
    #  ! IT ONLY FIRES ON MIXED SEASONS. A word-only season has nothing
    #    contradicting it and keeps its collegiate lean; a numeric-only
    #    season was never in doubt.
    cur.execute("""
        SELECT person_id, acad FROM gradekind
        WHERE  n_num > 0 AND n_word > n_num
    """)
    #  ! A PRO VERDICT STANDS (pooling review P5, 2026-10-26). Rules 3, 4 and
    #    the age floor run before this one; a season they already called
    #    professional (stale_grade, elite_field, post_collegiate, adult_club,
    #    no_grades) is a claim about eligibility, not a guess from its grades,
    #    and the mixed grades are exactly what an adult with a stale profile
    #    looks like. Nuking it left the row with no pool at all.
    n_nuked = 0
    for pid, ay in cur.fetchall():
        key = (int(pid), int(ay))
        if key in acad and acad[key].get("level") != "pro":
            acad[key] = {"grade": None, "level": None,
                         "method": "contradicted"}
            n_nuked += 1

    # ============================================================== #
    #  RULE 5d: A FIELD MOSTLY MADE OF NUKED SEASONS
    #
    #  ★ AN EVENT IS ONLY AS GOOD AS THE ENTRANTS VOUCHING FOR IT. Rules 4,
    #    2c and 6 all read a field to decide what an athlete is. If most of
    #    that field has just been thrown away as self-contradictory, the
    #    handful of verdicts that came FROM that field inherit its
    #    unreliability.
    #
    #  ! A CORROBORATED GRADE SURVIVES, AND SO DOES A PRO VERDICT. Neither
    #    ever depended on the company the athlete kept -- one is the
    #    athlete's own repeated grade, the other a claim about eligibility.
    #    Only the field-derived verdicts go.
    #
    #  ⚠ ONE PASS, NO ITERATION. Nuking a field's remainder shrinks the
    #    fields the other rules read, which could make further events look
    #    thin, which would cascade. The bar is high and it runs once.
    nuked_seasons = {k for k, v in acad.items() if v["method"] == "contradicted"}
    n_nuked_field = 0
    t5d = time.time()
    for key in thinFieldSeasons(cur, nuked_seasons):
        v = acad.get(key)
        if v is not None and v["method"] in _UNCERTAIN_METHODS:
            acad[key] = {"grade": None, "level": None, "method": "thin_field"}
            n_nuked_field += 1
    print(f"    [5d] {len(nuked_seasons):,} contradicted seasons, {n_nuked_field:,} "
          f"field verdicts in their fields dropped ({time.time() - t5d:.0f}s)", flush=True)

    # ============================================================== #
    #  RULE 5e: A BARE WORD WITH NOTHING AROUND IT
    #
    #  ★ THE LEAN NEEDS SOMETHING TO LEAN ON. A bare Fr/So/Jr/Sr means 9-12
    #    in high school and 13-16 in college, and rule 2c resolves it from
    #    the athlete's other seasons or from the fields they raced. An
    #    athlete with ONE season holding ONE race has neither: no
    #    progression, and one field is not a corroborating set.
    #
    #    Measured: 13,990 such seasons. Athletes with a single race but
    #    several seasons -- 40,000 more -- keep their verdicts, because their
    #    progression can still speak for them.
    #
    #  ⚠ IT DOES NOT REACH THE MIDDLE-SCHOOL CASE. Dominic Colussi reads
    #    "Senior" at Springboro MS in 2018 across five seasons, so this rule
    #    leaves him alone and he stays collegiate. Only the school string
    #    says middle school, and that is a separate decision.
    # ⚠ NEVER A CORRELATED SUBQUERY for "how many seasons does this person
    #   have?": the first version re-evaluated it per row, 30M rows each
    #   scanning 30M, and was killed after 92 minutes. It became a window
    #   function, and is now a count over race_count's keys (below).
    # ★ ONE PASS FOR 5e, RULE 6 AND THE TRUST RULE (2026-10-07). Each of the
    #   three read allraces whole: this window for 5e, a SELECT DISTINCT
    #   (person_id, acad) for rule 6, and count(DISTINCT race) per season for
    #   trust -- three passes over 225M rows for one table of 31M seasons.
    #   race_count IS that table; the other two are read off it.
    # ★ AND IT IS ALREADY BUILT (2026-10-25): rule 4 made season_races, the
    #   same GROUP BY, and nothing has written allraces' person_id, acad or
    #   race since (see _FIELD_BUILD). This is a read, not a 225M-row count.
    # ★ RULE 5f's TEAM LEVELS ARE READ MEANWHILE (2026-10-25, run log: 379 s
    #   from here to the 5f line). _hsTeamCodes opens its own connection and
    #   groups results by team code; nothing it reads is anything this step
    #   writes, so it runs in a thread while this connection streams 31M
    #   seasons into Python, and 5f takes its answer when it gets there.
    from concurrent.futures import ThreadPoolExecutor
    _bg = ThreadPoolExecutor(max_workers=1)
    hs_codes = _bg.submit(_hsTeamCodes)
    _bg.shutdown(wait=False)
    t_rc = time.time()
    cur.execute("SELECT person_id, acad, races FROM season_races")
    race_count = {(int(p), int(a)): int(n) for p, a, n in cur.fetchall()}
    cur.execute("DROP TABLE IF EXISTS season_races")
    seasons_of = {}
    for (pid, _ay) in race_count:
        seasons_of[pid] = seasons_of.get(pid, 0) + 1
    print(f"    [5e] races per season counted: {len(race_count):,} seasons "
          f"({time.time() - t_rc:.0f}s)", flush=True)
    n_lone = 0
    for key, races in race_count.items():
        if races != 1 or seasons_of[key[0]] != 1:
            continue
        v = acad.get(key)
        if v is not None and v["grade"] in ("FR", "SO", "JR", "SR"):
            acad[key] = {"grade": None, "level": None, "method": "lone_word"}
            n_lone += 1

    # RULE 5f: a lone season on a high school team, in high school fields
    _tq = time.time()
    moved5f = teamLevelSeasons(cur, acad, seasons_of, hs_codes=hs_codes)
    for key in moved5f:
        acad[key] = {"grade": None, "level": "hs", "method": "team_level"}
    n_team = len(moved5f)
    print(f"    [5f] {n_team:,} lone seasons on a high school team in high school "
          f"fields: grade below 9 set aside ({time.time() - _tq:.0f}s)", flush=True)

    n_none = 0
    for key in race_count:
        if key in acad:
            continue
        acad[key] = {"grade": None, "level": None, "method": "no_evidence"}
        n_none += 1

    # ============================================================== #
    #  RULE 7: TRUST
    #
    #  ★ RATED IS NOT THE SAME QUESTION AS RANKED. A verdict can be the best
    #    available reading of the evidence and still be too thin to headline
    #    a national board. Until now the only choices were "pool it" and
    #    "throw it away", so every marginal season had to be one or the
    #    other.
    #
    #    Measured: Dominic Colussi has five seasons, ONE RACE EACH, every
    #    verdict a field verdict. Each race put him in whatever field he
    #    happened to enter, and five separate single-race levels made him a
    #    collegian since 2018 -- from a middle school. Nothing about his own
    #    data supports that, and nothing contradicts it either.
    #
    #  ! A SEASON IS TRUSTED WHEN IT HAS MORE THAN ONE RACE. One race can be
    #    a mismeasured course, a mistyped time or a field that voted wrong,
    #    and nothing in the season contradicts it. Two agree or they do not.
    # race_count: counted once, before rule 5e

    #  ★ OR WHEN THE PREVIOUS SEASON VOUCHES FOR IT (owner, 2026-09-06:
    #    "this fails for the current season -- not enough races yet"). The
    #    season in progress is one race deep for weeks, and under the rule
    #    above every athlete in it was off the boards until their second
    #    race. An athlete who was a trusted grade 10 in May and races as a
    #    grade 11 in September is corroborated by the calendar, not by a
    #    second race: see trustByProgression.
    # the academic year still being raced (season_year's August clock)
    import datetime as _dt
    from season_year import academicYear
    n_untrusted, n_vouched = trustByProgression(
        acad, race_count, open_ay=academicYear(_dt.date.today()))

    # ★ RULE 0 AGAIN: the pins win over every rule above (see the first pass)
    _PP.applyGradePins(acad, grade_pins)

    out = dict(acad)

    print("\n[grade] academic-year verdicts")
    print(f"    owner's pin (grade_pin)  {n_pinned:>10,}")
    print(f"    corroborated grade       {n_corr:>10,}")
    print(f"    one race, vouched for by the season before {n_vouched:>8,}")
    print(f"    bare word, own grades    {n_bare:>10,}")
    print(f"    bare word, field's grades{n_bare_field:>10,}")
    print(f"    level of the field       {n_field:>10,}")
    print(f"    team's level, lone season{n_team:>10,}")
    print(f"    nobody in the race graded{n_nograde:>10,}")
    print(f"    grade never advanced     {n_stale:>10,}")
    print(f"    school grade after college{n_postcoll:>9,}")
    print(f"    adult club's '12'        {n_adult - n_unatt:>10,}")
    print(f"    unattached, past school  {n_unatt:>10,}")
    print(f"    off the progression      {n_progress:>10,}")
    print(f"    season contradicts itself{n_nuked:>10,}")
    print(f"    field mostly nuked       {n_nuked_field:>10,}")
    print(f"    lone word, lone race     {n_lone:>10,}")
    print(f"    nothing to say           {n_none:>10,}")
    print(f"    --- of which UNTRUSTED   {n_untrusted:>10,}"
          f"  (rated, kept off the boards)")
    print(f"    total                    {len(acad):>10,}")
    print(f"[grade] written as {len(out):,} academic-year rows")
    return out


def write(cur, resolved):
    """grade_fix carries the answer; grade_untrusted keeps consumers working.

    ⚠ `season` HOLDS THE ACADEMIC (AUGUST-START, per season_year) YEAR. The
      column name is
      unchanged because panels.py and build_ranking_results.py already join on
      it academically; renaming it would break correct code. speed_ratings'
      poolOf is the consumer that had to move.
    """
    cur.execute("DROP TABLE IF EXISTS grade_fix")
    # ⚠ `trust` IS A COLUMN, NOT A SEPARATE TABLE. A consumer that does not
    #   know about it still reads grade and level exactly as before and is
    #   simply not filtering -- which is the old behaviour, not a broken one.
    # ★ THE KEY IS ADDED AFTER THE ROWS (2026-10-25, with the COPY above
    #   copyText). Built once from sorted keys instead of grown by 31M
    #   inserts in dict order. ADD PRIMARY KEY names it grade_fix_pkey, the
    #   name the inline constraint had, on the same two columns, so the table
    #   a consumer sees is the same table; a duplicate key would fail here
    #   instead of at the INSERT, and abort the same transaction.
    cur.execute("""CREATE TABLE grade_fix (
                       person_id bigint NOT NULL,
                       season    int    NOT NULL,
                       grade     text,
                       level     text,
                       method    text   NOT NULL,
                       trust     text   NOT NULL DEFAULT 'high')""")
    _copyRows(cur, "grade_fix",
              ("person_id", "season", "grade", "level", "method", "trust"),
              ((p, s, v["grade"], v["level"], v["method"], v.get("trust", "high"))
               for (p, s), v in resolved.items()))
    cur.execute("ALTER TABLE grade_fix ADD PRIMARY KEY (person_id, season)")
    cur.execute("ANALYZE grade_fix")

    # ! FROM grade_fix, NOT FROM PYTHON AGAIN. Its rows are every key of
    #   `resolved` -- sorted(resolved.keys()) was exactly that set -- so the
    #   server copies the two columns across in the same sorted order rather
    #   than Python sorting and sending 31M pairs a second time. The key goes
    #   on after the rows, as grade_fix's does.
    cur.execute("DROP TABLE IF EXISTS grade_untrusted")
    cur.execute("""CREATE TABLE grade_untrusted (
                       person_id bigint NOT NULL,
                       season    int    NOT NULL)""")
    cur.execute("""INSERT INTO grade_untrusted (person_id, season)
                   SELECT person_id, season FROM grade_fix
                   ORDER  BY person_id, season""")
    cur.execute("ALTER TABLE grade_untrusted ADD PRIMARY KEY (person_id, season)")
    cur.execute("ANALYZE grade_untrusted")
    return len(resolved)


def compareVerdicts(old, new):
    """{(person, season): (level, method)} twice -> Counter of the changes,
    '<old level>/<old method> -> <new ...>', a missing verdict as '-'. Pure."""
    from collections import Counter
    out = Counter()
    for key in set(old) | set(new):
        a, b = old.get(key), new.get(key)
        if a == b:
            continue
        out[f"{'/'.join(map(str, a)) if a else '-'} -> {'/'.join(map(str, b)) if b else '-'}"] += 1
    return out


def compare(cur, resolved, show=12):
    """Print how `resolved` differs from the grade_fix table now in place:
    the dry run a rule change is judged on before --write."""
    cur.execute("SELECT to_regclass('grade_fix')")
    if not cur.fetchone()[0]:
        print("[grade] no grade_fix to compare with")
        return
    cur.execute("SELECT person_id, season, level, method FROM grade_fix")
    old = {(p, s): (lv, m) for p, s, lv, m in cur.fetchall()}
    new = {k: (v["level"], v["method"]) for k, v in resolved.items()}
    changes = compareVerdicts(old, new)
    print(f"\n[grade] compared with grade_fix: {sum(changes.values()):,} seasons change")
    for what, n in changes.most_common(20):
        print(f"    {n:>9,}  {what}")
    moved = [k for k in set(old) | set(new) if old.get(k) != new.get(k)
             and (old.get(k) or (None,))[0] == "hs"]
    if moved:
        cur.execute("""SELECT DISTINCT ON (person_id) person_id, first_name, last_name, school
                       FROM athletes WHERE person_id = ANY(%s)""", (sorted(moved)[:5000],))
        names = {p: (f, l, sc) for p, f, l, sc in cur.fetchall()}
        print(f"\n  seasons that were 'hs' and change (up to {show}):")
        for key in sorted(moved, key=lambda k: -k[1])[:show]:
            f, l, sc = names.get(key[0], ("?", "?", "?"))
            print(f"    {key[0]} {key[1]} {f} {l} ({sc}): {old.get(key)} -> {new.get(key)}")


def main(do_write=False, audit=False, do_compare=False):
    from database import getConn

    with getConn() as conn:
        with conn.cursor() as cur:
            resolved = resolve(cur, audit=audit)
            if do_compare:
                compare(cur, resolved)
            if do_write:
                n = write(cur, resolved)
                conn.commit()
                print(f"\n[grade] wrote grade_fix ({n:,} rows)")
            else:
                print("\n[grade] DRY RUN, pass --write to save")


if __name__ == "__main__":
    main(do_write="--write" in sys.argv,
         audit="--audit" in sys.argv,
         do_compare="--compare" in sys.argv)