"""sources/anet_twins.py: athletic.net is not scraped for a meet a timer's
own site already gave us (owner, 2026-11-01)."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from sources import anet_twins as T  # noqa: E402


def test_same_name_tolerates_filler_words():
    assert T.sameName("2026 Wayne XC Invitational", "Wayne Cross Country Invite")
    assert T.sameName("W-FL League Championships", "WFL League Championship 2026")
    assert T.sameName("Monroe County Coaches Invitational", "Monroe County Coaches Invite")


def test_different_meets_do_not_match():
    assert not T.sameName("Wayne Invitational", "Wayne Central Invitational")
    assert not T.sameName("Izzo Classic", "Bob Goodell Classic")
    assert not T.sameName("Invitational", "XC Invitational")          # nothing distinctive


def test_match_needs_same_day_state_and_one_hit():
    cands = [(11, "Wayne Cross Country Invite", "2026-09-12", "NY"),
             (12, "Wayne Cross Country Invite", "2026-09-13", "NY")]
    assert T.match("2026 Wayne XC Invitational", "2026-09-12", "NY", cands) == 11
    assert T.match("2026 Wayne XC Invitational", "2026-09-12", "OH", cands) is None
    assert T.match("2026 Wayne XC Invitational", "2026-09-12", None, cands) == 11
    two = cands + [(13, "Wayne XC Invite", "2026-09-12", "NY")]
    assert T.match("Wayne Invitational", "2026-09-12", "NY", two) is None   # ambiguous is no


def test_watermark_ignores_athleticlive_rows():
    import queue_meets as Q

    class Cur:
        def execute(self, sql, args=None):
            self.sql = sql

        def fetchone(self):
            return (5,)
    c = Cur()
    Q.watermark(c, "TF", "anet")
    assert f"result_id < {Q.ALIVE_RESULT_BASE}" in c.sql
    Q.watermark(c, "XC", "tfrrs")
    assert "result_id <" not in c.sql
    from sources import athleticlive
    assert Q.ALIVE_RESULT_BASE == athleticlive.RESULT_BASE


def test_launcher_skips_division_calls_for_a_twin():
    src = open(os.path.join(ROOT, "scripts", "scrape_results.py"), encoding="utf-8").read()
    assert src.count('_localTwin(meet_id, "XC"') == 1 and src.count('_localTwin(meet_id, "TF"') == 1
    assert src.index('_localTwin(meet_id, "XC"') < src.index("_scrapeXCByDivision(page, meet_id, meet_info, divisions")
