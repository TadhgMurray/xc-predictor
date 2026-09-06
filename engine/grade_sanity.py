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
import sys

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


def _advances(prev, cur):
    """Is `cur` the season after `prev` for one athlete: the grade one
    year on (10 after 9, JR after SO, FR after 12), or the same college
    or pro level again (a Senior-5 is still college)."""
    a, b = _gradeStep(prev.get("grade")), _gradeStep(cur.get("grade"))
    if a and b:
        # two readable grades: only the step forward counts; a repeat or
        # a step back is the very disagreement trust exists to catch
        if a[0] == b[0] and b[1] == a[1] + 1:
            return True
        return a == ("n", 12) and b == ("c", 1)
    lp, lc = prev.get("level"), cur.get("level")
    return bool(lp) and lp == lc and lp in ("college", "pro")


def trustByProgression(acad, race_count, min_races=MIN_TRUSTED_RACES):
    """Stamp v['trust'] on every academic-year verdict, in year order.

    high  when the season has min_races or more, OR when the previous
          academic year of the same athlete is trusted and this season
          advances it (_advances). A vouched season can vouch for the
          next, so a career of one-race seasons that step 9, 10, 11, 12
          is trusted throughout, and a run of single field verdicts that
          jump levels (Dominic Colussi, above) is not.
    low   otherwise: rated and shown, off the boards.
    Returns (n_low, n_vouched)."""
    n_low = n_vouched = 0
    for key in sorted(acad):
        v = acad[key]
        pid, ay = key
        if race_count.get(key, 0) >= min_races:
            v["trust"] = "high"
            continue
        prev = acad.get((pid, ay - 1))
        if prev is not None and prev.get("trust") == "high" and _advances(prev, v):
            v["trust"] = "high"
            v["trust_by"] = "progression"
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
    $$ LANGUAGE sql IMMUTABLE;

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
    CREATE OR REPLACE FUNCTION classGrade(g text) RETURNS text AS $$
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
    $$ LANGUAGE sql IMMUTABLE;

    -- isDefiniteClass: does this row use the tfrrs CLASS+ELIGIBILITY form?
    --
    -- ★ §1.6, ENCODED. 'SR-4' can only be a fourth-year collegiate athlete --
    --   the digit is NCAA eligibility and high school has no such thing. A
    --   bare 'Sr' is a senior in either world. Rule 5b already leans on this
    --   distinction; rule 2c can now read it too.
    CREATE OR REPLACE FUNCTION isDefiniteClass(g text) RETURNS boolean AS $$
        SELECT upper(TRIM(g)) ~ '^(FR|SO|JR|SR)-[1-4]$'
    $$ LANGUAGE sql IMMUTABLE;

    -- The banded keys GRADE_TO_LEVEL carries verbatim. They are grades, not
    -- age ranges: '13-14' and '15-16' are ages and stay out.
    CREATE OR REPLACE FUNCTION bandGrade(g text) RETURNS text AS $$
        SELECT CASE TRIM(g)
            WHEN '7-8'   THEN '7-8'
            WHEN '9-10'  THEN '9-10'
            WHEN '11-12' THEN '11-12'
        END
    $$ LANGUAGE sql IMMUTABLE;

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
    $$ LANGUAGE sql IMMUTABLE;

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
    $$ LANGUAGE sql IMMUTABLE;

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
    $$ LANGUAGE sql IMMUTABLE;

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
    $$ LANGUAGE sql IMMUTABLE;

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
               CASE WHEN ab.result_id IS NULL THEN n.norm END     AS grade,
               n.definite                                         AS definite,
               -- ! THE RAW COLUMN RIDES ALONG. Rule 5b needs the FR-1/SR-4
               --   spelling normGrade folds away; carrying it costs one text
               --   column and saves re-reading 225M rows.
               CASE WHEN ab.result_id IS NULL THEN r.grade END    AS raw_grade,
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
               CASE WHEN ab.result_id IS NULL THEN n.norm END,
               n.definite,
               CASE WHEN ab.result_id IS NULL THEN r.grade END,
               meet_id, div_id, source, COALESCE(event_id, -1),
               raceIdent(time_seconds, date, meet_id,
                         COALESCE(event_id, -1))
        FROM   results_tf r
        LEFT   JOIN gradenorm n ON n.raw = r.grade
        /*AGEBAND_TF*/
        LEFT   JOIN result_twin tw ON tw.sport = 'TF' AND tw.result_id = r.result_id
        WHERE  person_id IS NOT NULL AND date IS NOT NULL
          AND  substring(date, 1, 4)::int BETWEEN 1990 AND 2035
          AND  tw.result_id IS NULL;

    -- ! (person_id, acad, race) COVERS THE COUNTS. Rules 2 and 4 both do
    --   count(DISTINCT race) grouped by person and season; a plain
    --   (person_id, acad) index still has to fetch every heap row for the
    --   third column. Including it lets those aggregate from the index.
    CREATE INDEX ON allraces (person_id, acad, race);
    CREATE INDEX ON allraces (meet_id, div_id, source, event_key);
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
    ALTER TABLE allraces ADD COLUMN grade_folded text;

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
    UPDATE allraces a
       SET grade_folded =
           CASE WHEN k.n_num > 0 AND k.n_word > 0
                 AND ((k.n_num >= k.n_word
                       AND a.grade IN ('FR','SO','JR','SR'))
                   OR (k.n_word > k.n_num AND a.grade ~ '^[0-9]+$'))
                THEN NULL ELSE a.grade END
      FROM   gradekind k
     WHERE   k.person_id = a.person_id AND k.acad = a.acad;

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

_FIELD_SQL = f"""
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
    WITH by_grade AS (
        SELECT person_id, acad, grade_folded AS grade,
               count(DISTINCT race) AS n
        FROM   allraces
        WHERE  grade_folded IS NOT NULL
        GROUP  BY 1, 2, 3
    ),
    best AS (
        SELECT person_id, acad, max(n) AS top_n
        FROM   by_grade GROUP BY 1, 2
    ),
    needy AS (
        SELECT a.person_id, a.acad
        FROM   (SELECT DISTINCT person_id, acad FROM allraces) a
        LEFT   JOIN best b
               ON b.person_id = a.person_id AND b.acad = a.acad
        WHERE  b.top_n IS NULL OR b.top_n < %(minc)s
    ),
    -- Only the events those athletes ran, scored from their own entrants.
    wanted AS (
        SELECT DISTINCT a.meet_id, a.div_id, a.source, a.event_key
        FROM   needy n
        JOIN   allraces a ON a.person_id = n.person_id AND a.acad = n.acad
    ),
    event_level AS (
        SELECT w.meet_id, w.div_id, w.source, w.event_key,
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
               CASE WHEN count(*) FILTER (WHERE a.grade IS NOT NULL) = 0
                    THEN 'pro'
                    ELSE mode() WITHIN GROUP (ORDER BY gradeLevel(a.grade))
                             FILTER (WHERE gradeLevel(a.grade) IS NOT NULL)
               END AS level,
               -- Carried for the diagnostic below, not for the decision.
               count(*) AS n_entrants,
               count(*) FILTER (WHERE a.grade IS NOT NULL) AS n_graded
        FROM   wanted w
        JOIN   allraces a
               ON a.meet_id = w.meet_id AND a.div_id = w.div_id
              AND a.source = w.source AND a.event_key = w.event_key
        GROUP  BY 1, 2, 3, 4
        -- ★ EDIT 2, PART 2 -- the gate. Either the event has enough graded
        --   entrants for its mode to mean something, OR it has NONE, which
        --   is itself the evidence.
        --
        -- ⚠ NO FIELD-SIZE FLOOR ON THE SECOND ARM, AS SPECIFIED. A
        --   one-entrant ungraded event therefore votes 'pro' on a field of
        --   one. If reportUngraded() below shows that is common, the floor
        --   is one clause:
        --       OR (count(*) FILTER (WHERE a.grade IS NOT NULL) = 0
        --           AND count(*) >= {MIN_FIELD_GRADED})
        HAVING count(*) FILTER (WHERE gradeLevel(a.grade) IS NOT NULL)
                   >= {MIN_FIELD_GRADED}
            OR count(*) FILTER (WHERE a.grade IS NOT NULL) = 0
    )
    SELECT DISTINCT ON (n.person_id, n.acad)
           n.person_id, n.acad, e.level
    FROM   needy n
    JOIN   allraces a ON a.person_id = n.person_id AND a.acad = n.acad
    JOIN   event_level e
           ON e.meet_id = a.meet_id AND e.div_id = a.div_id
          AND e.source = a.source AND e.event_key = a.event_key
    WHERE  e.level IS NOT NULL
    GROUP  BY n.person_id, n.acad, e.level
    ORDER  BY n.person_id, n.acad, count(*) DESC, e.level
"""


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
    SELECT p.person_id, p.acad
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
    WITH ev AS (
        SELECT meet_id, div_id, source, event_key,
               count(*) FILTER (WHERE gradeLevel(grade_folded)
                                      IN ('elem','ms','hs')) AS n_school,
               count(*) FILTER (WHERE gradeLevel(grade_folded)
                                      IS NOT NULL)           AS n_graded
        FROM   allraces
        GROUP  BY 1, 2, 3, 4
        HAVING count(*) FILTER (WHERE gradeLevel(grade_folded) IS NOT NULL)
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
_COLLEGIATE_SQL = """
    WITH elig AS (
        SELECT person_id, min(acad) AS first_acad
        FROM   allraces
        WHERE  definite
        GROUP  BY 1
    )
    SELECT DISTINCT a.person_id, a.acad
    FROM   allraces a
    JOIN   elig e ON e.person_id = a.person_id
    WHERE  a.acad > e.first_acad
"""


# ------------------------------------------------------------------ #
#  RESOLVE
# ------------------------------------------------------------------ #

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

    cur.execute(_CORROBORATED_SQL, {"minc": MIN_CORROBORATION})
    for pid, ay, grade, n in cur.fetchall():
        acad[(int(pid), int(ay))] = {"grade": grade, "level": None,
                                     "method": "corroborated"}
    n_corr = len(acad)

    # ! RULE 2c, BETWEEN 2 AND 4 ON PURPOSE. It rewrites grades rule 4 is
    #   about to read as field evidence; running it afterwards would leave
    #   every ungraded athlete in those fields pooled off the wrong mode.
    cur.execute(_BARE_CLASS_SQL)
    n_bare = _resolveBareClass(
        acad, [(int(p), int(a)) for p, a in cur.fetchall()])

    # ! TWO ROUNDS, WITH A WRITE-BACK BETWEEN THEM. Round one resolves the
    #   pockets it can see; the write-back makes those races visibly
    #   school-level; round two picks up the races that were just under the
    #   80% line before their pockets were fixed.
    n_bare_field = 0
    for _round in (1, 2):
        cur.execute(_BARE_FIELD_SQL, {"minf": MIN_FIELD_GRADED,
                                      "share": _FIELD_SCHOOL_SHARE})
        got = _resolveBareClassByField(acad, cur.fetchall())
        n_bare_field += got
        # The last write-back also stands for rule 4, which reads the field
        # after this loop.
        moved = _writeBackBareClass(cur, acad)
        print(f"    [2c round {_round}] {got:,} seasons, "
              f"{moved:,} rows relabelled in the field")
        if not got:
            break

    # ! THE METHOD NAME IS DERIVED FROM THE LEVEL, NOT ASKED FOR SEPARATELY.
    #   Edit 2 gives the field rule two ways to speak, and they are not the
    #   same claim: 'the races this athlete ran were mostly high school' is a
    #   mode over evidence, while 'not one entrant anywhere carried a grade'
    #   is the absence of school-ness. Only the second can emit 'pro', so the
    #   level identifies its own origin and the two stay countable apart.
    cur.execute(_FIELD_SQL, {"minc": MIN_CORROBORATION})
    n_nograde = 0
    for pid, ay, level in cur.fetchall():
        key = (int(pid), int(ay))
        if key in acad:
            continue
        method = "no_grades" if level == "pro" else "field"
        n_nograde += (method == "no_grades")
        acad[key] = {"grade": None, "level": level, "method": method}
    n_field = len(acad) - n_corr - n_nograde

    cur.execute(_STALE_SQL, {"maxy": MAX_YEARS_AT_GRADE})
    stale = {(int(p), int(a)) for p, a in cur.fetchall()}
    n_stale = 0
    for key in stale:
        # ! STALE OVERRIDES A CORROBORATED GRADE, and it has to. A record that
        #   says SR for four years IS well corroborated -- that is exactly the
        #   problem with it. Rule 5 is about the grade being unmaintained, not
        #   about it being unattested.
        acad[key] = {"grade": None, "level": "pro", "method": "stale_grade"}
        n_stale += 1

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
    cur.execute(_COLLEGIATE_SQL)
    n_postcoll = 0
    for pid, ay in cur.fetchall():
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
    n_nuked = 0
    for pid, ay in cur.fetchall():
        key = (int(pid), int(ay))
        if key in acad:
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
    if nuked_seasons:
        cur.execute("SELECT person_id, acad, meet_id, div_id, source, event_key "
                    "FROM allraces WHERE grade_folded IS NOT NULL")
        by_event = {}
        for pid, ay, m, d, src, ek in cur.fetchall():
            by_event.setdefault((m, d, src, ek), []).append((int(pid), int(ay)))

        for entrants in by_event.values():
            if len(entrants) < MIN_FIELD_GRADED:
                continue
            bad = sum(1 for k in entrants if k in nuked_seasons)
            if bad < NUKED_FIELD_SHARE * len(entrants):
                continue
            for key in entrants:
                v = acad.get(key)
                if v is not None and v["method"] in _UNCERTAIN_METHODS:
                    acad[key] = {"grade": None, "level": None,
                                 "method": "thin_field"}
                    n_nuked_field += 1

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
    # ⚠ A WINDOW FUNCTION, NOT A CORRELATED SUBQUERY. The first version asked
    #   "how many seasons does this person have?" as a subquery over the same
    #   CTE, which Postgres re-evaluated PER ROW against a materialised result
    #   with no index -- 30M rows each scanning 30M. It ran for 92 minutes and
    #   was killed.
    #
    #   count(*) OVER (PARTITION BY person_id) answers the same question in
    #   the single pass that already sorted the rows.
    cur.execute("""
        WITH per_season AS (
            SELECT person_id, acad, count(DISTINCT race) AS races
            FROM   allraces
            GROUP  BY 1, 2
        ),
        counted AS (
            SELECT person_id, acad, races,
                   count(*) OVER (PARTITION BY person_id) AS seasons
            FROM   per_season
        )
        SELECT person_id, acad
        FROM   counted
        WHERE  races = 1 AND seasons = 1
    """)
    n_lone = 0
    for pid, ay in cur.fetchall():
        key = (int(pid), int(ay))
        v = acad.get(key)
        if v is not None and v["grade"] in ("FR", "SO", "JR", "SR"):
            acad[key] = {"grade": None, "level": None, "method": "lone_word"}
            n_lone += 1

    cur.execute("SELECT DISTINCT person_id, acad FROM allraces")
    n_none = 0
    for pid, ay in cur.fetchall():
        key = (int(pid), int(ay))
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
    cur.execute("""
        SELECT person_id, acad, count(DISTINCT race) AS races
        FROM   allraces GROUP BY 1, 2
    """)
    race_count = {(int(p), int(a)): int(n) for p, a, n in cur.fetchall()}

    #  ★ OR WHEN THE PREVIOUS SEASON VOUCHES FOR IT (owner, 2026-09-06:
    #    "this fails for the current season -- not enough races yet"). The
    #    season in progress is one race deep for weeks, and under the rule
    #    above every athlete in it was off the boards until their second
    #    race. An athlete who was a trusted grade 10 in May and races as a
    #    grade 11 in September is corroborated by the calendar, not by a
    #    second race: see trustByProgression.
    n_untrusted, n_vouched = trustByProgression(acad, race_count)

    out = dict(acad)

    print("\n[grade] academic-year verdicts")
    print(f"    corroborated grade       {n_corr:>10,}")
    print(f"    one race, vouched for by the season before {n_vouched:>8,}")
    print(f"    bare word, own grades    {n_bare:>10,}")
    print(f"    bare word, field's grades{n_bare_field:>10,}")
    print(f"    level of the field       {n_field:>10,}")
    print(f"    nobody in the race graded{n_nograde:>10,}")
    print(f"    grade never advanced     {n_stale:>10,}")
    print(f"    school grade after college{n_postcoll:>9,}")
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
    from psycopg2.extras import execute_values

    cur.execute("DROP TABLE IF EXISTS grade_fix")
    # ⚠ `trust` IS A COLUMN, NOT A SEPARATE TABLE. A consumer that does not
    #   know about it still reads grade and level exactly as before and is
    #   simply not filtering -- which is the old behaviour, not a broken one.
    cur.execute("""CREATE TABLE grade_fix (
                       person_id bigint NOT NULL,
                       season    int    NOT NULL,
                       grade     text,
                       level     text,
                       method    text   NOT NULL,
                       trust     text   NOT NULL DEFAULT 'high',
                       PRIMARY KEY (person_id, season))""")
    execute_values(cur, "INSERT INTO grade_fix VALUES %s",
                   [(p, s, v["grade"], v["level"], v["method"],
                     v.get("trust", "high"))
                    for (p, s), v in resolved.items()], page_size=5000)
    cur.execute("ANALYZE grade_fix")

    cur.execute("DROP TABLE IF EXISTS grade_untrusted")
    cur.execute("""CREATE TABLE grade_untrusted (
                       person_id bigint NOT NULL,
                       season    int    NOT NULL,
                       PRIMARY KEY (person_id, season))""")
    execute_values(cur, "INSERT INTO grade_untrusted VALUES %s",
                   sorted(resolved.keys()), page_size=5000)
    cur.execute("ANALYZE grade_untrusted")
    return len(resolved)


def main(do_write=False, audit=False):
    from database import getConn

    with getConn() as conn:
        with conn.cursor() as cur:
            resolved = resolve(cur, audit=audit)
            if do_write:
                n = write(cur, resolved)
                conn.commit()
                print(f"\n[grade] wrote grade_fix ({n:,} rows)")
            else:
                print("\n[grade] DRY RUN, pass --write to save")


if __name__ == "__main__":
    main(do_write="--write" in sys.argv,
         audit="--audit" in sys.argv)