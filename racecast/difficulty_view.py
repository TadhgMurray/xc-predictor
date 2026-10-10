"""difficulty_view.py -- course difficulty as a reader understands it.

The engine stores difficulty as a multiplier on time, (1 + d), anchored so
the AVERAGE TRACK IS 0.0. This file displays that number and does not move
it.

  ★ ONE ZERO FOR BOTH SPORTS (owner, 2026-09-02: "I wanted them on the same
    scale"), AND THAT ZERO IS A TRACK (owner, 2026-09-10: "the average tf
    course will have difficulty 0.0 and be the baseline"). Both hold at
    once: the two sports are on one line, and the line starts at a flat
    400m oval. An ordinary cross country course therefore reads about +7%,
    and that gap IS the sport gap the engine measured -- it is the answer,
    not an offset to remove.

  ⚠ THIS FILE USED TO SUBTRACT THE CORPUS MEAN, and that quietly undid the
    engine's anchor. The note here claimed "the engine anchors to this same
    row-weighted mean, so the value is ~0 by construction" -- true when it
    was written, false from the moment the engine started anchoring on
    track. Cross country dominates the corpus, so the mean it subtracted
    was about +6%, and a typical TRACK displayed near -6%. The owner asked
    three times why track difficulty was not 0.0; this was why, and the
    engine was innocent every time.

    The zero is defined in ONE place now -- engine/joint_golive.py -- and
    read here as a GUARD, not as a correction.

  ★ A PERCENTAGE, NOT A DECIMAL. "+4% slower than a typical course, about 40
    seconds on a 16-minute 5K" is what +0.04 means, and it is what the
    templates print. The raw value stays available in a title attribute.

Display only. Nothing here changes a rating; ratings already carry the
difficulty, which is why a 133 here and a 133 anywhere else are the same
performance.
"""


import math
import threading
import time

# Seconds of the worked example: a 16-minute 5K, the reference a reader has.
REFERENCE_SECONDS = 960.0
REFERENCE_LABEL = "a 16-minute 5K"

# The zero, re-read this often. course_difficulties changes once a pipeline
# run, so an hour is generous.
_TTL = 3600.0
_state = {"at": 0.0, "mean": 0.0}
_lock = threading.Lock()

# ! THE GUARD READS THE TRACK CELLS, because those are what the engine
#   anchored on. If this drifts far from zero the table was written by an
#   engine with a different anchor, and the site says so in the log rather
#   than silently re-centring -- silently re-centring is what went wrong.
_SQL = """
    SELECT avg(ln(1.0 + difficulty))
    FROM   course_difficulties
    WHERE  difficulty IS NOT NULL AND difficulty > -0.9
      AND  course_name LIKE 'TF:%%'
"""
# how far the average track may sit from zero before the log complains
_DRIFT_WARN = 0.01


def _refresh():
    """Read the corpus mean log-multiplier. A failure leaves the last good
    value in place, or zero on a fresh process: a page must not 500 because
    the database is mid-rebuild."""
    try:
        from database import getConn
        with getConn() as conn, conn.cursor() as cur:
            cur.execute(_SQL)
            row = cur.fetchone()
        _state["mean"] = float(row[0] or 0.0) if row else 0.0
    except Exception:                                    # noqa: BLE001
        _state.setdefault("mean", 0.0)
    _state["at"] = time.time()


def sportMeanLog(sport=None):
    """THE DISPLAY ZERO, WHICH IS NOW ALWAYS 0.0.

    The engine anchors the average track at zero, so the stored number is
    already the number to show. This returns 0 and exists only so every
    caller and template filter keeps working.

    trackDriftLog() is the guard: it reads what the average track actually
    stored, for the log, without moving anything.
    """
    return 0.0


def trackDriftLog():
    """How far the stored average track sits from zero. Should be ~0; a
    large value means the table was written by an engine with a different
    anchor."""
    with _lock:
        if time.time() - _state["at"] > _TTL:
            _refresh()
    return float(_state["mean"])



def relativePct(difficulty, sport="XC"):
    """Percent slower (+) or faster (-) than a typical course of the sport,
    or None."""
    if difficulty is None:
        return None
    try:
        d = float(difficulty)
    except (TypeError, ValueError):
        return None
    if d <= -0.9:
        return None
    return 100.0 * (math.exp(math.log1p(d) - sportMeanLog(sport)) - 1.0)


def diffPct(difficulty, sport="XC"):
    """The template filter: '+4.1%', '0.0%', '-2.3%', or an em dash."""
    pct = relativePct(difficulty, sport)
    if pct is None:
        return " - "
    if abs(pct) < 0.05:
        return "0.0%"
    return f"{pct:+.1f}%"


def diffWords(difficulty, sport="XC"):
    """A sentence: '+4.1% slower than a typical XC course, about 40 seconds
    on a 16-minute 5K'."""
    pct = relativePct(difficulty, sport)
    if pct is None:
        return "no difficulty solved for this course yet"
    secs = REFERENCE_SECONDS * pct / 100.0
    if abs(pct) < 0.05:
        return "about the same as a typical course"
    how = "slower" if pct > 0 else "faster"
    return (f"{abs(pct):.1f}% {how} than a typical course, about "
            f"{abs(secs):.0f} seconds on {REFERENCE_LABEL}")


# ------------------------------------------------------------------ #
#  the course in one word (athlete results, 2026-10-10)
# ------------------------------------------------------------------ #
#
# ★ ONE WORD A PARENT READS -- Fast / Typical / Hard / Very hard -- WITH THE
#   PERCENTAGE IN THE TOOLTIP (owner, 2026-10-10, header redesign B). "+10.4%"
#   in a results row reads as an error to anyone who does not know the
#   scale's zero is a track (see the header): an ordinary cross country
#   course sits near +7%. So the word and its % are measured against the
#   SPORT'S OWN TYPICAL COURSE -- the median cell of that sport -- not the
#   zero. The rating is untouched; this is display only, like the rest.
#
# ★ THE CUT-OFFS ARE READ OFF THE COURSES, NOT TYPED IN. For each sport the
#   course_difficulties cells (XC: every row not named 'TF:...'; track: the
#   'TF:' venue rows) are ordered by log(1 + d), and
#       Fast       the fastest quarter                    ld <  Q25
#       Typical    the middle half                 Q25 <= ld <= Q75
#       Hard       the next slowest, short of the tail  Q75 < ld <= Q90
#       Very hard  the slowest tenth                    Q90 <  ld
#   "Typical" is the middle half because, with no other yardstick, that is
#   what typical means of a distribution: half of all courses sit inside it.
#   The slow side is split again because difficulty is skewed that way (a
#   course can be far harder than a flat one -- hills, mud, sand, altitude
#   -- and barely faster), and its slowest tenth is the tail a reader should
#   be warned about. Each cell counts once: the word places the COURSE
#   among courses, whatever its race count. Quantiles are 'type 7' (numpy's
#   default), the same rule as cuts.quantile.
#
# ! A DISTRIBUTION TOO THIN FOR ITS OWN TOP TENTH IS NO DISTRIBUTION: below
#   1 / (1 - 0.90) = 10 cells the Q90 cut is the slowest course or two, so a
#   sport with fewer cells gets no cut-offs. Derived from the top quantile,
#   so moving that moves the floor with it.
#
# ⚠ FALLBACK: NO TABLE, NO WORD. When course_difficulties cannot be read (a
#   fresh process mid-rebuild, a test with no database) or a sport is too
#   thin, courseWord returns (None, None) and the template prints the old
#   percentage (_explain.html dv) -- never a word measured against nothing.
#   A failed re-read keeps the last good cut-offs.
#
# ! READ ONCE PER PROCESS PER _TTL, ON A CURSOR THE CALLER OWNS (the athlete
#   route's own short connection), never a getConn of this file's own inside
#   a request: a nested one grows the pool and closes the route's
#   connection (cuts._cellOn's note).
COURSE_WORD_Q = (0.25, 0.75, 0.90)
COURSE_WORD_MIN_N = int(round(1.0 / (1.0 - COURSE_WORD_Q[-1])))

_CUTS_SQL = """
    SELECT CASE WHEN course_name LIKE 'TF:%%' THEN 'TF' ELSE 'XC' END AS sport,
           ln(1.0 + difficulty) AS ld
    FROM   course_difficulties
    WHERE  difficulty IS NOT NULL AND difficulty > -0.9
"""
_cuts_state = {"at": 0.0, "cuts": None}


def _quantile(xs, q):
    """'type 7' quantile of a SORTED non-empty list (cuts.quantile's rule;
    not imported, so this display module stays free of the cuts stack)."""
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * float(q)
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def cutoffsFrom(rows):
    """{sport: {'median', 'q25', 'q75', 'q90', 'n'}} in log(1 + d), from
    (sport, log1p(d)) pairs; a sport under COURSE_WORD_MIN_N cells is left
    out. Pure, so a test can hand it a stub distribution."""
    by = {}
    for sport, ld in rows:
        if ld is None:
            continue
        by.setdefault(str(sport or "XC").upper(), []).append(float(ld))
    out = {}
    for sport, xs in by.items():
        if len(xs) < COURSE_WORD_MIN_N:
            continue
        xs.sort()
        q25, q75, q90 = (_quantile(xs, q) for q in COURSE_WORD_Q)
        out[sport] = {"median": _quantile(xs, 0.5), "q25": q25, "q75": q75,
                      "q90": q90, "n": len(xs)}
    return out


def courseCutoffs(cur=None):
    """The cached cut-offs, re-read on `cur` when stale or never read; None
    until a read has succeeded. Never raises."""
    stale = (_cuts_state["cuts"] is None
             or time.time() - _cuts_state["at"] > _TTL)
    if cur is not None and stale:
        try:
            cur.execute(_CUTS_SQL)
            rows = [((r["sport"], r["ld"]) if isinstance(r, dict)
                     else (r[0], r[1])) for r in cur.fetchall()]
            got = cutoffsFrom(rows)
            with _lock:
                if got:
                    _cuts_state["cuts"] = got
                _cuts_state["at"] = time.time()
        except Exception:                                # noqa: BLE001
            try:
                cur.connection.rollback()
            except Exception:                            # noqa: BLE001
                pass
    return _cuts_state["cuts"]


def courseWord(difficulty, sport="XC", cuts=None):
    """(word, pct): ('Hard', '+3.4%') -- the % against the sport's typical
    (median) course -- or (None, None) with no difficulty or no cut-offs."""
    c = (cuts or {}).get(str(sport or "XC").upper())
    if c is None or difficulty is None:
        return None, None
    try:
        d = float(difficulty)
    except (TypeError, ValueError):
        return None, None
    if d <= -0.9:
        return None, None
    ld = math.log1p(d)
    if ld < c["q25"]:
        word = "Fast"
    elif ld <= c["q75"]:
        word = "Typical"
    elif ld <= c["q90"]:
        word = "Hard"
    else:
        word = "Very hard"
    pct = 100.0 * (math.exp(ld - c["median"]) - 1.0)
    return word, ("0.0%" if abs(pct) < 0.05 else f"{pct:+.1f}%")

