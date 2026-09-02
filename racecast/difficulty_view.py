"""difficulty_view.py -- course difficulty as a reader understands it.

The engine stores difficulty as a multiplier on time, (1 + d), anchored so
the row-weighted mean over EVERY cell, cross country and track together, is
zero. Two things make the raw number misleading on a page:

  ★ THE ZERO IS NOT "A TYPICAL COURSE OF THIS SPORT". The sport recentring
    puts XC cells about +0.02 above the corpus zero and track cells about the
    same below it, so a perfectly ordinary XC course reads +0.02 and the
    fastest three-mile course in California displayed as average (owner,
    2026-09-02). Readers compare a course with other courses of its sport, so
    that is the zero this file uses: the row-weighted mean of the sport's own
    cells, read once from course_difficulties and cached.

  ★ A PERCENTAGE, NOT A DECIMAL. "+4% slower than a typical XC course, about
    40 seconds on a 16-minute 5K" is what +0.0589 means, and it is what the
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

# The per-sport zero, re-read this often. course_difficulties changes once a
# pipeline run, so an hour is generous.
_TTL = 3600.0
_state = {"at": 0.0, "mean": {}}
_lock = threading.Lock()

_SQL = """
    SELECT CASE WHEN course_name LIKE 'XC:%%' THEN 'XC' ELSE 'TF' END AS sport,
           sum(ln(1.0 + difficulty) * n_results) / NULLIF(sum(n_results), 0)
               AS mean_log
    FROM   course_difficulties
    WHERE  difficulty IS NOT NULL AND difficulty > -0.9
      AND  n_results > 0
    GROUP  BY 1
"""


def _refresh():
    """Read the per-sport mean log-multiplier. A failure leaves the last
    good values in place, or zeros on a fresh process: a page must not 500
    because the database is mid-rebuild."""
    try:
        from database import getConn
        with getConn() as conn, conn.cursor() as cur:
            cur.execute(_SQL)
            mean = {r[0]: float(r[1] or 0.0) for r in cur.fetchall()}
        _state["mean"] = mean
    except Exception:                                    # noqa: BLE001
        _state.setdefault("mean", {})
    _state["at"] = time.time()


def sportMeanLog(sport):
    """ln(1 + d) of the typical course of this sport."""
    with _lock:
        if time.time() - _state["at"] > _TTL:
            _refresh()
    return _state["mean"].get((sport or "XC").upper(), 0.0)


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
        return "—"
    if abs(pct) < 0.05:
        return "0.0%"
    return f"{pct:+.1f}%"


def diffWords(difficulty, sport="XC"):
    """A sentence: '+4.1% slower than a typical XC course, about 40 seconds
    on a 16-minute 5K'."""
    pct = relativePct(difficulty, sport)
    if pct is None:
        return "no difficulty solved for this course yet"
    kind = "cross country course" if (sport or "XC").upper() == "XC" else "track"
    secs = REFERENCE_SECONDS * pct / 100.0
    if abs(pct) < 0.05:
        return f"about the same as a typical {kind}"
    how = "slower" if pct > 0 else "faster"
    return (f"{abs(pct):.1f}% {how} than a typical {kind}, about "
            f"{abs(secs):.0f} seconds on {REFERENCE_LABEL}")
