"""★ STANDING RULE (owner, 2026-11-02): "make everything everywhere hs
equivalent". Every rating a page prints is on the public (high school)
scale: through rv() (which carries data-hs), a stamped hs_* key, or the
`hs` filter / hs_delta global (app.py) with the rating's pool. This test
fails on a template that formats a rating any other way.

A page whose every rating is a high school pool's (where the HS number IS
the number) is listed in HS_ONLY with the reason; a single value that is
already HS carries a {# hs-ok: why #} note just before it. Nothing else is
exempt."""
import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..", "racecast", "templates")

HS_ONLY = {
    "projections.html": "high school projections: hs pools only",
    "state_odds.html": "state meet odds: high school only",
    "shortlist.html": "the recruit's own (high school) rating",
    "recruiting_school.html": "the typical recruit's HS rating; transfers go through |hs",
    "team_season.html": "the season tracker is a high school page (school.html, nr_track)",
}

_FMT = re.compile(r'"%[+]?\.1f"\|format\(([^)]*?)\)')


def offenders():
    out = []
    for name in sorted(os.listdir(ROOT)):
        if not name.endswith(".html") or name in HS_ONLY:
            continue
        text = open(os.path.join(ROOT, name), encoding="utf-8").read()
        for m in _FMT.finditer(text):
            arg = m.group(1)
            if "rating" not in arg and "rating" not in text[m.start() - 60:m.start()]:
                continue
            if "|hs(" in arg or "hs_" in arg or "hs_delta(" in arg or "(" in arg and "hs" in arg:
                continue
            # an explicit, explained exception right before it: {# hs-ok: why #}
            # -- or the value was set from a stamped hs_* key just above
            before = text[max(0, m.start() - 300):m.start()]
            if "hs-ok" in before or "hs_" in before.split("\n")[-1] or \
                    re.search(r"set\s+" + re.escape(arg.strip()) + r"\s*=\s*[^\n]*hs_", before):
                continue
            if re.search(r"\bgap\b|\bse\b|\bpct\b|_pct|\bgain\b", arg):
                continue          # a gap or an error in points, not a rating on a scale
            line = text.count("\n", 0, m.start()) + 1
            out.append(f"{name}:{line}: {m.group(0)}")
    return out


def test_every_rating_is_on_the_hs_scale():
    bad = offenders()
    assert not bad, "ratings printed off the HS scale:\n" + "\n".join(bad)


def test_rating_breakdown_is_hs_equivalent(monkeypatch):
    """owner, 2026-11-03: the 'How this was rated' panel showed 116.1 on the
    college men scale beside a 140.4 on the page."""
    import sys as _s
    _s.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "racecast"))
    import explain_rating as E
    import pool_view as PV
    monkeypatch.setattr(PV, "toHs", lambda v, pool, sport=None: None if v is None else v * 1.2)
    body = {"race": {"pool": "college_m"}, "start_rating": 100.0, "rating": 110.0,
            "steps": [{"key": "distance", "text": "x: a 100.0 on an average outdoor track"},
                      {"key": "weather", "pts": 4.0}, {"key": "other", "pts": 6.0}]}
    out = E.toHsBody(body, "XC")
    assert out["scale"] == "hs" and out["rating"] == 132.0 and out["start_rating"] == 120.0
    assert "a 120.0 on" in out["steps"][0]["text"]
    assert abs(sum(s["pts"] for s in out["steps"][1:]) - 12.0) < 1e-6
    assert body["rating"] == 110.0                       # the cached body untouched


def test_every_page_that_prints_a_rating_loads_the_hs_swap():
    """2026-11-04, /goal: the template printed every rating through rv() --
    this file's other checks passed -- but the page never loaded
    scale-view.js, so rv's data-hs was never shown and college ratings read
    on the college scale. A full page that prints a rating loads it."""
    import glob
    import re
    here = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "racecast", "templates")
    missing = []
    for path in glob.glob(os.path.join(here, "*.html")):
        src = open(path, encoding="utf-8").read()
        if "<html" not in src:
            continue                      # a partial: its page carries the script
        if re.search(r"\brv\(|data-hs=|\brval\(", src) and "scale-view.js" not in src:
            missing.append(os.path.basename(path))
    assert not missing, f"pages printing ratings without scale-view.js: {missing}"
