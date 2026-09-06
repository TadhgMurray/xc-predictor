"""difficulty_view.py -- course difficulty as a reader understands it.

The engine stores difficulty as a multiplier on time, (1 + d), anchored so
the row-weighted mean over EVERY cell, cross country and track together, is
zero.

  ★ ONE ZERO FOR BOTH SPORTS (owner, 2026-09-02: "I wanted them on the same
    scale"). An earlier version of this file gave cross country and track
    each their own zero -- the mean of the sport's own cells -- so an
    ordinary XC course read 0% and an ordinary track read 0%, and the two
    could not be compared. That was wrong. The zero is the corpus mean,
    read from course_difficulties and cached, so a hard XC course and a fast
    track sit on one line and the gap between them is the sport gap the
    engine measured. An ordinary XC course therefore reads a little above
    0% and an ordinary track a little below, which is the truth.

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

# ! ONE ROW, BOTH SPORTS. The engine anchors its difficulties to this same
#   row-weighted mean, so the value is ~0 by construction and the read is a
#   guard against a table written with a different anchor, not a
#   recentring.
_SQL = """
    SELECT sum(ln(1.0 + difficulty) * n_results) / NULLIF(sum(n_results), 0)
    FROM   course_difficulties
    WHERE  difficulty IS NOT NULL AND difficulty > -0.9
      AND  n_results > 0
"""


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
    """ln(1 + d) of the typical course -- the same zero whatever the sport.
    The argument is kept so every caller and template filter keeps working;
    it no longer selects anything."""
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

