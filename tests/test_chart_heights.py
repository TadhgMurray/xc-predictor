"""
The athlete page stacks three .chart-wide slots. At the desktop 380px each
that is ~1,140px of chart before a single result row -- two to three screens
on a phone, which is what buried the tables (owner, 2026-08-31).

This parses athlete-charts.css and pins the narrow-screen heights, so a future
edit cannot quietly put them back.
"""
import os
import re

CSS = os.path.join(os.path.dirname(__file__), "..", "racecast", "static",
                   "athlete-charts.css")

# How many .chart-wide slots athlete.html stacks. If this grows, the budget
# below has to be re-checked rather than silently blown.
WIDE_SLOTS = 3
PHONE_USABLE_PX = 550          # ~a phone viewport minus browser chrome


def _blocks(css):
    """{media condition or None: {selector-ish: {prop: value}}} -- crude but
    enough for custom properties, which is all this file needs."""
    out = {}
    for m in re.finditer(r'@media\s*\(max-width:\s*(\d+)px\)\s*\{(.*?)\n\}',
                         css, re.S):
        out[int(m.group(1))] = m.group(2)
    # everything outside a media query
    out[None] = re.sub(r'@media.*?\n\}', '', css, flags=re.S)
    return out


def _chartH(body, selector):
    m = re.search(re.escape(selector) + r'[^{]*\{[^}]*--chart-h:\s*(\d+)px',
                  body)
    return int(m.group(1)) if m else None


def test_narrow_breakpoints_exist():
    css = open(CSS, encoding="utf-8").read()
    b = _blocks(css)
    assert 700 in b, "no <=700px block -- the site's 'narrow' breakpoint"
    assert 480 in b, "no <=480px block for small phones"
    print("  breakpoints 700 and 480 present ................... OK")


def test_charts_shrink_at_every_step():
    css = open(CSS, encoding="utf-8").read()
    b = _blocks(css)
    for sel in (".chart-slot.chart-wide", ".chart-panel .chart-slot"):
        desktop = _chartH(b[None], sel)
        narrow = _chartH(b[700], sel)
        tiny = _chartH(b[480], sel)
        assert desktop and narrow and tiny, (sel, desktop, narrow, tiny)
        assert narrow < desktop, f"{sel}: {narrow} not below {desktop}"
        assert tiny <= narrow, f"{sel}: {tiny} above {narrow}"
        print(f"  {sel:<26} {desktop} -> {narrow} -> {tiny} .. OK")


def test_the_stack_fits_a_phone_screen():
    """Three wide charts must not exceed one phone screen at the small step."""
    css = open(CSS, encoding="utf-8").read()
    b = _blocks(css)
    tiny = _chartH(b[480], ".chart-slot.chart-wide")
    total = WIDE_SLOTS * tiny
    assert total <= PHONE_USABLE_PX + tiny, (
        f"{WIDE_SLOTS} x {tiny}px = {total}px is more than a screen "
        f"plus one chart ({PHONE_USABLE_PX + tiny}px)")
    print(f"  {WIDE_SLOTS} wide charts = {total}px at the 480 step ...... OK")


def test_breakpoints_reuse_the_sites_narrow_line():
    """700px is what style.css already uses to let tables scroll internally.
    A ninth arbitrary breakpoint is how the existing eight accumulated."""
    style = open(os.path.join(os.path.dirname(CSS), "style.css"),
                 encoding="utf-8").read()
    assert "@media (max-width: 700px)" in style, \
        "style.css no longer uses 700px -- realign athlete-charts.css"
    print("  700px still matches style.css's narrow line ....... OK")


# ------------------------------------------------------------------ #
# The fold (owner, 2026-09-01): charts collapse below 700px so the first
# result table is near the top of a phone screen.
# ------------------------------------------------------------------ #

TPL = os.path.join(os.path.dirname(__file__), "..", "racecast", "templates",
                   "athlete.html")
JS = os.path.join(os.path.dirname(CSS), "athlete-charts.js")


def test_every_wide_chart_is_folded_and_open_by_default():
    tpl = open(TPL, encoding="utf-8").read()
    folds = re.findall(r'<details class="chart-fold"([^>]*)>', tpl)
    assert len(folds) == WIDE_SLOTS, f"{len(folds)} folds, expected {WIDE_SLOTS}"
    # ! `open` IS THE DESKTOP DEFAULT AND MUST LIVE IN THE MARKUP: a <details>
    #   cannot be forced open by CSS, so without it a JS failure would leave
    #   every chart shut on desktop too.
    for attrs in folds:
        assert "open" in attrs, f"a fold is not open by default: {attrs!r}"
    # every wide slot sits inside a fold
    assert tpl.count('chart-slot chart-wide') == WIDE_SLOTS
    print(f"  {WIDE_SLOTS} folds, all open by default ................. OK")


def test_summary_is_hidden_on_desktop_and_shown_when_narrow():
    css = open(CSS, encoding="utf-8").read()
    b = _blocks(css)
    assert re.search(r'\.chart-fold\s*>\s*summary\s*\{[^}]*display:\s*none',
                     b[None]), "summary is not hidden outside the media query"
    assert re.search(r'summary[^{]*\{[^}]*display:\s*block', b[700]), \
        "summary never becomes visible at <=700px"
    print("  summary hidden on desktop, shown at <=700px ....... OK")


def test_js_and_css_agree_on_the_breakpoint():
    """If one moves and the other does not, the summary appears while the
    fold is still forced open -- a control that does nothing."""
    js = open(JS, encoding="utf-8").read()
    m = re.search(r'matchMedia\("\(max-width:\s*(\d+)px\)"\)', js)
    assert m, "initChartFold does not use matchMedia on a max-width"
    assert int(m.group(1)) == 700, f"JS uses {m.group(1)}px, CSS uses 700px"
    print(f"  JS matchMedia {m.group(1)}px == CSS breakpoint ......... OK")


def test_the_fold_closes_when_narrow_not_the_other_way_round():
    js = open(JS, encoding="utf-8").read()
    assert "d.open = !isNarrow" in js, \
        "the fold must CLOSE when narrow -- check the sense of the test"
    print("  narrow => closed (sense of the test is right) ...... OK")


if __name__ == "__main__":
    for fn in [test_narrow_breakpoints_exist,
               test_charts_shrink_at_every_step,
               test_the_stack_fits_a_phone_screen,
               test_breakpoints_reuse_the_sites_narrow_line,
               test_every_wide_chart_is_folded_and_open_by_default,
               test_summary_is_hidden_on_desktop_and_shown_when_narrow,
               test_js_and_css_agree_on_the_breakpoint,
               test_the_fold_closes_when_narrow_not_the_other_way_round]:
        fn()
    print("\nall chart-height tests passed")
