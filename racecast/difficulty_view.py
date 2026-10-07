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

