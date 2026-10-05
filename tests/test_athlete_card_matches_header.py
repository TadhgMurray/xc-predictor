"""
The athlete share card must say what the athlete page's header says.

Audited against the live site on 2026-10-05 (cards.athleteCardData):

  - Tim Ross, /athlete/1435404: header 141.1 (the HS-equivalent view the
    page opens on), card 117.3 (the college_m number). Same for best race.
  - Brandon Neifert, /athlete/26582211: header grade 8 (the latest team
    season), card 7 (the rated season's).
  - Races / seasons: header 26 / 10, card 23 / 11 (athlete_season rows).
  - Duncan Hamilton, /athlete/1007045840: a 200 page whose og:image was a
    404, because the card read `athletes` alone and he has no row there.

No database: a scripted cursor answers each of the card's queries, and the
page helpers that need one (get_races, buildRankLine, units, crest) are
replaced with stubs. repFactor needs the pool constants from the database,
so a fixed factor stands in for it.
"""
import io
import os
import re
import sys

os.environ.setdefault("XCP_DB_QUIET", "1")
for k, v in (("NAME", "x"), ("USER", "x"), ("PASSWORD", "x"),
             ("HOST", "127.0.0.1"), ("PORT", "5432")):
    os.environ.setdefault(f"XCP_DB_{k}", v)

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
for sub in ("racecast", "engine", "scripts"):
    sys.path.insert(0, os.path.join(ROOT, sub))

import pytest                                                   # noqa: E402

import app                                                      # noqa: E402
import cards                                                    # noqa: E402
import pool_view                                                # noqa: E402
import school_units                                             # noqa: E402

FACTORS = {"hs_m": 1.0, "college_m": 1.2026, "ms_m": 0.874}


class _Conn:
    def rollback(self):
        pass


class Cur:
    """Answers the card's SQL by what it asks for. `rows` maps a key to the
    row (or None) that query returns."""

    def __init__(self, **rows):
        self.rows = rows
        self.connection = _Conn()
        self._next = None
        self.asked = []

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        if "FROM athletes" in s:
            key = "athletes"
        elif "SELECT athlete_name, school, date FROM results" in s:
            key = "named_rows"
        elif "ORDER BY (mean_rating IS NOT NULL)" in s:
            key = "season"
        elif "NOT LIKE 'unattached" in s:
            key = "latest"
        elif "FROM ranking_results" in s:
            key = "college_school"
        elif "athlete_ratings" in s:
            key = "athlete_ratings"
        elif "count(*) AS seasons" in s:
            key = "agg"
        elif "SELECT best_rating, sport, pool" in s:
            key = "best"
        else:
            raise AssertionError(f"unexpected query: {s[:120]}")
        self.asked.append(key)
        self._next = self.rows.get(key)

    def fetchone(self):
        return self._next


def _race(date, sport="XC", grade=None, school="Missouri"):
    return {"date": date, "sport": sport, "grade": grade, "school": school}


@pytest.fixture(autouse=True)
def stubs(monkeypatch):
    monkeypatch.setattr(pool_view, "repFactor", lambda pool, sport: FACTORS.get(
        (pool or "").split("|", 1)[0]))
    monkeypatch.setattr(app, "buildRankLine", lambda cur, pid, season: [
        {"label": "Nation", "rank": 117}, {"label": "Team", "rank": 3}])
    monkeypatch.setattr(app, "dedupe_races", lambda races: races)
    monkeypatch.setattr(app, "get_races", lambda cur, pid: [
        _race("2006-10-01"), _race("2006-09-01"), _race("2007-04-01", "TF"),
        _race("2005-10-01")])
    monkeypatch.setattr(cards, "crestPath", lambda cur, school, state=None: None)
    monkeypatch.setattr(school_units, "homeStateOf", lambda cur, pid: None)
    monkeypatch.setattr(school_units, "unitsFor", lambda *a, **k: [])
    monkeypatch.setattr(school_units, "unitsForPerson", lambda *a, **k: [])


def _college(**over):
    rows = dict(
        athletes={"name": "Tim Ross", "school": "Missouri"},
        season={"mean_rating": 117.3, "sport": "XC", "pool": "college_m",
                "year": 2006, "n_races": 6, "state": "MO", "school": "Missouri",
                "grade": "SR-4"},
        latest={"school": "Missouri", "pool": "college_m", "sport": "XC",
                "year": 2006, "grade": "SR-4", "state": "MO"},
        agg={"seasons": 11, "races": 23},
        best={"best_rating": 147.7, "sport": "XC", "pool": "hs_m"},
    )
    rows.update(over)
    return rows


def test_rating_is_on_the_hs_scale_the_page_opens_on():
    d = cards.athleteCardData(Cur(**_college()), 1435404)
    assert d["rating"] == pytest.approx(117.3 * FACTORS["college_m"])
    # the best race was a high school race: its factor is 1
    assert d["best"] == pytest.approx(147.7)
    assert d["best_sport"] == "XC"


def test_best_race_takes_its_own_pools_factor():
    d = cards.athleteCardData(Cur(**_college(
        best={"best_rating": 165.4, "sport": "TF", "pool": "ms_m"})), 12136312)
    assert d["best"] == pytest.approx(165.4 * FACTORS["ms_m"])


def test_a_pool_with_no_hs_twin_keeps_its_own_number():
    d = cards.athleteCardData(Cur(**_college(
        season={"mean_rating": 101.0, "sport": "XC", "pool": "unknown_x",
                "year": 2006, "n_races": 4, "state": None, "school": "Missouri",
                "grade": None})), 1)
    assert d["rating"] == pytest.approx(101.0)


def test_races_and_seasons_are_the_strips_counts():
    """Every race, and seasons as distinct labels (2006 XC and 2007 TF are
    two labels; two 2006 XC races are one), not athlete_season's rows."""
    d = cards.athleteCardData(Cur(**_college()), 1435404)
    assert d["races"] == 4
    assert d["seasons"] == len({"2006", "2007", "2005"})


def test_counts_fall_back_to_the_board_table_when_races_fail(monkeypatch):
    def boom(cur, pid):
        raise RuntimeError("statement timeout")
    monkeypatch.setattr(app, "get_races", boom)
    d = cards.athleteCardData(Cur(**_college()), 1435404)
    assert (d["races"], d["seasons"]) == (23, 11)


def test_class_and_team_are_the_latest_team_seasons():
    """Brandon Neifert: the rated season is 2025 XC (grade 7); the latest
    team season is 2026 (grade 8). The page says 8 and the new school."""
    d = cards.athleteCardData(Cur(**_college(
        season={"mean_rating": 121.4, "sport": "XC", "pool": "ms_m", "year": 2025,
                "n_races": 5, "state": "WA", "school": "Old School", "grade": "7"},
        latest={"school": "Cedar Heights", "pool": "ms_m", "sport": "TF",
                "year": 2026, "grade": "8", "state": "WA"})), 26582211)
    assert d["grade"] == "8"
    assert d["school"].startswith("Cedar Heights")


def test_named_from_result_rows_is_a_card_not_a_404():
    """Duncan Hamilton: no athletes row, a name on his results."""
    cur = Cur(**_college(athletes=None,
                         named_rows={"name": "Duncan Hamilton",
                                     "school": "Oregon Track Club"}))
    d = cards.athleteCardData(cur, 1007045840)
    assert d is not None
    assert d["name"] == "Duncan Hamilton" and not d["unnamed"]


def test_no_row_anywhere_is_still_none():
    cur = Cur(athletes=None, named_rows=None)
    assert cards.athleteCardData(cur, 999) is None


@pytest.mark.parametrize("name", [None, "None", "  "])
def test_no_name_reads_as_the_pages_unnamed_athlete(name):
    cur = Cur(**_college(athletes={"name": name, "school": "Missouri"},
                         named_rows=None))
    d = cards.athleteCardData(cur, 5)
    assert d["name"] == cards.UNNAMED == "Unnamed athlete"
    assert d["unnamed"] is True


def test_no_season_takes_the_engines_row_on_its_factor():
    """The page's fallback for a career athlete_season never holds."""
    d = cards.athleteCardData(Cur(**_college(
        season=None, latest=None, best=None,
        athlete_ratings={"speed_rating": 120.0, "pool": "college_m|XC"})), 7)
    assert d["rating"] == pytest.approx(120.0 * FACTORS["college_m"])
    assert d["best"] is None


# ---- the renderer --------------------------------------------------------

def _card(**over):
    d = {"name": "Tim Ross", "unnamed": False, "school": "Missouri (MO)",
         "crest": None, "grade": "SR-4", "units": [], "rating": 141.1,
         "season": "2006 XC season", "best": 147.7, "best_sport": "XC",
         "races": 26, "seasons": 10, "ranks": ["Nation #117", "Team #3"]}
    d.update(over)
    return d


def _png(d):
    from PIL import Image
    png = cards.renderAthleteCard(d)
    img = Image.open(io.BytesIO(png))
    assert img.size == (cards.CARD_W, cards.CARD_H)
    return img


def test_no_best_race_draws_no_best_race_cell():
    """A thrower's card: the page drops the cell; the card drew "-" over "TF".
    RACES moves into the second slot, so the third slot is bare ground."""
    with_best, without = _png(_card()), _png(_card(best=None, rating=None))
    # the columns start at x = 64 + 280 + 40 = 384, then +300, then +210
    third = (894, 280, 1136, 440)
    assert set(without.convert("RGB").crop(third).getdata()) == {(0x11, 0x11, 0x11)}
    assert len(set(with_best.convert("RGB").crop(third).getdata())) > 1


def test_long_names_and_schools_stay_on_the_card():
    _png(_card(name="Maximiliano Alessandro Hernandez-Villanueva de la Cruz",
               school="Saint Ignatius College Preparatory of San Francisco (CA)",
               ranks=[f"Unit {i} #{i * 111:,}" for i in range(12)]))


def _draw():
    from PIL import Image, ImageDraw
    return ImageDraw.Draw(Image.new("RGB", (10, 10)))


def test_a_short_name_is_one_big_line():
    f, lines = cards._nameLines(_draw(), "Tim Ross", 616)
    assert lines == ["Tim Ross"] and f.size == 76


def test_a_long_name_wraps_instead_of_losing_its_surname():
    """At the 40 px floor beside a crest the slot holds ~25 characters; the
    card used to print "Maximiliano Alessandro Herna"."""
    dr = _draw()
    name = "Maximiliano Alessandro Hernandez-Villanueva"
    f, lines = cards._nameLines(dr, name, 616)
    assert " ".join(lines) == name and len(lines) == 2
    assert all(dr.textlength(ln, font=f) <= 616 for ln in lines)


def test_a_name_that_cannot_wrap_is_cut_to_fit():
    dr = _draw()
    f, lines = cards._nameLines(dr, "X" * 80, 616)
    assert len(lines) == 1 and dr.textlength(lines[0], font=f) <= 616


def test_an_unnamed_card_renders():
    _png(_card(name=cards.UNNAMED, unnamed=True, rating=None, best=None,
               school="", grade="", ranks=[]))


# ---- the cache and the URL -----------------------------------------------

def test_the_disk_file_and_the_og_url_carry_the_card_version(tmp_path, monkeypatch):
    monkeypatch.setattr(cards, "CARD_DIR", str(tmp_path))
    monkeypatch.setattr(cards, "athleteCardData", lambda cur, pid: _card())
    path = cards.cachedAthleteCard(None, 42)
    assert os.path.basename(path) == f"athlete-v{cards.ATHLETE_CARD_V}-42.png"
    assert not [p for p in os.listdir(tmp_path) if p.endswith(".tmp")]
    for tpl in ("athlete.html", "recruit.html"):
        with open(os.path.join(ROOT, "racecast", "templates", tpl), encoding="utf-8") as fh:
            src = fh.read()
        m = re.search(r'"/card/athlete/" ~ \w+\.person_id ~ "\.png\?v=(\d+)"', src)
        assert m and int(m.group(1)) == cards.ATHLETE_CARD_V, tpl
