# Project: xc-predictor / racecast
# File:    h2h.py
# Purpose: The head-to-head PREDICTION on /compare (owner, 2026-10-10, item
#          10): pick a course and a date, and the page asks the individual
#          predictor for both athletes at once and shows the two times, the
#          margin, and what the model's own tests say about trusting it.
#
# ★ NO NEW PREDICTOR. static/h2h.js calls /api/predict/individual in its
#   manual mode with person_id=a,b -- the endpoint already takes a list for
#   exactly this ("comparing two athletes at the same race is the obvious
#   next question"). This module only supplies the sentence under the
#   result, from the run scorecard the About page reads (accuracy.py), so
#   the number quoted here is the number published there.
#
# ! THE SCORECARD IS READ AT MOST EVERY TEN MINUTES PER WORKER: it is one
#   line appended per pipeline run, and /compare is a busy page.
from flask import Blueprint

import ttlcache

bp = Blueprint("h2h", __name__)

_TTL = 600.0

# the two FAIR TESTS a head-to-head leans on (accuracy.TESTS keys): who
# finishes ahead, and how far a single predicted time typically misses
ORDER_KEY = "lacctic_order_ours"
MISS_KEY = "holdout_race_sd"


def accuracyFacts(load=None):
    """{"order": "81%", "miss": "±2.9%", "as_of": date} -- either value may
    be missing -- or None without a scorecard."""
    if load is None:
        from accuracy import latestByKey as load
    got = load()
    if not got:
        return None
    out = {"as_of": got.get("as_of")}
    if got.get(ORDER_KEY):
        out["order"] = got[ORDER_KEY]
    if got.get(MISS_KEY):
        out["miss"] = got[MISS_KEY]
    return out if (out.get("order") or out.get("miss")) else None


@bp.app_template_global("h2h_accuracy")
def h2hAccuracy():
    """The cached facts for templates; None on any failure (a sentence,
    never a page)."""
    def compute():
        try:
            return accuracyFacts() or {}
        except Exception:                               # noqa: BLE001
            return {}
    val, _stamp = ttlcache.get(("h2h-accuracy",), compute, ttl=_TTL)
    return val or None
