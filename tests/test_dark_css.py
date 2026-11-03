"""Dark mode (owner, 2026-11-03): dark.css is generated from the light
sheets and must be current; every page sets the theme before first paint."""
import os
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
import build_dark_css as B  # noqa: E402


def test_dark_css_is_current():
    r = subprocess.run([sys.executable, os.path.join(_ROOT, "scripts", "build_dark_css.py"), "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, "run: python scripts/build_dark_css.py\n" + r.stdout


def test_mapping_lands_on_a_few_shades():
    """owner, 2026-11-03: "some things are really white while some are just
    muted grays" -- neutrals land on fixed tiers by role."""
    assert B.darkColor("#ffffff", "background") == "#14161a"          # the page
    assert B.darkColor("#fafbfc", "background") == B.darkColor("#f3f6fa", "background")
    assert B.darkColor("#111418", "color") == B.darkColor("#333", "color") == "#e6e8eb"
    assert B.darkColor("#666", "color") == B.darkColor("#777", "color")
    assert B.darkColor("#e5e7eb", "border-color") == B.darkColor("#ddd", "border-color")
    assert B.darkColor("rgba(0,0,0,.2)", "box-shadow") == "rgba(0, 0, 0, 0.32)"
    assert B.darkColor("var(--x)") == "var(--x)"
    # a link is light and keeps its hue
    assert B.darkColor("#14477d", "--link") > "#6"
    v, changed = B.mapValue("url(#fff) #fff", "background")
    assert v.startswith("url(#fff)") and changed


def test_a_badge_keeps_its_colours():
    out = B.darkSheet(".flag--pr { background: #e0a500; color: #fff; }")
    assert "background: #e0a500" in out and "color: #fff" in out


def test_cascade_is_kept_for_token_rules():
    css = ".d { fill: #ccc } .d.t1 { fill: var(--rt1) }"
    out = B.darkSheet(css)
    assert 'html[data-theme="dark"] .d.t1 { fill: var(--rt1); }' in out
    assert B.prefixSelector(":root, html body, .a") == \
        'html[data-theme="dark"], html[data-theme="dark"] body, html[data-theme="dark"] .a'


def test_every_page_sets_the_theme_before_paint():
    meta = open(os.path.join(_ROOT, "racecast", "templates", "_meta.html")).read()
    assert 'data-theme' in meta and "dark.css" in meta
    top = open(os.path.join(_ROOT, "racecast", "templates", "_topbar.html")).read()
    assert 'id="theme-toggle"' in top and "rc-theme" in top
