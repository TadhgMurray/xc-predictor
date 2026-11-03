"""set_race_course.py: a race moved to another course stays there."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
os.environ.setdefault("XCP_DB_PASSWORD", "x")
import set_race_course as S  # noqa: E402


def test_race_from_link_or_ids():
    assert S.parseRace("278515/3000002940809") == (278515, 3000002940809)
    assert S.parseRace("https://racecast.co/race/xc/278515/3000002940809?r=5#x") == (278515, 3000002940809)


def test_pins_survive_a_rescrape():
    for f in ("deploy/nightly_update.sh", "deploy/run_pipeline.sh"):
        assert "set_race_course.py --apply" in open(os.path.join(_ROOT, f)).read(), f
