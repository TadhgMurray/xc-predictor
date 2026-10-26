"""build_ranking_results.prepareRow (pooling review P11, 2026-10-26): a rated
row with no rating_pool stamp is not ranked; the resolver fallback is only
for unrated rows (sprints, field events) and databases without the column."""
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_a_rated_row_without_a_stamp_returns_before_the_resolver():
    src = open(os.path.join(_ROOT, "racecast", "build_ranking_results.py")).read()
    body = src[src.index("def prepareRow("):]
    guard = body.index('elif row.speed_rating is not None and _HAVE_RATING_POOL.get(sport):')
    resolver = body.index("pool = resolvePool(row.grade")
    assert guard < resolver
    assert '_GATE[sport]["rated_no_pool"] += 1' in body[guard:resolver]
    assert "_HAVE_RATING_POOL[sport] = have" in src


def test_the_backfill_reads_the_engines_season_levels():
    """pooling review P7: no unanimity filter the engine does not apply"""
    src = open(os.path.join(_ROOT, "backfill", "backfill_normalize.py")).read()
    i = src.index("FROM   athlete_season_level")
    assert "unanimous" not in src[i:i + 120]
