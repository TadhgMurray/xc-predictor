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
    """{max-width: concatenated body}, plus None for everything outside one.

    ! BRACE-MATCHED, NOT REGEX-MATCHED. A non-greedy /\n}/ stops at the first
      line-starting brace, which truncates any media query containing nested
      rules -- and every interesting one here does.
    ! CONCATENATED, because a stylesheet can hold several blocks at the same
      width (style.css has three at 900px) and keying a dict on the width
      would silently keep only the last.
    """
    out, inside = {}, []
    for m in re.finditer(r'@media\s*\(max-width:\s*(\d+)px\)\s*\{', css):
        width = int(m.group(1))
        depth, j = 1, m.end()
        while j < len(css) and depth:
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
            j += 1
        body = css[m.end():j - 1]
        out[width] = out.get(width, "") + "\n" + body
        inside.append((m.start(), j))
    outside, last = [], 0
    for a, b in inside:
        outside.append(css[last:a])
        last = b
    outside.append(css[last:])
    out[None] = "".join(outside)
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


def test_nothing_js_side_closes_the_folds():
    """★ THE FOLDS OPEN ON EVERY WIDTH (owner, 2026-09-01).

    They were auto-closed below 700px to get a result table near the top of a
    phone -- which was solving the wrong problem. The page was unusable
    because .page-layout never stacked; once it does, and once the sidebar is
    ordered between the top graph and the season history, the graphs are
    wanted open. `open` is in the markup, so there is nothing to set on load.
    """
    js = open(JS, encoding="utf-8").read()
    assert "initChartFold" not in js, \
        "initChartFold is back -- the folds should not be closed on load"
    assert "d.open" not in js, "something is still setting a fold's open state"
    print("  no JS closes a fold on load ....................... OK")


def test_the_summary_is_still_a_control_when_narrow():
    """Open by default, but collapsible by hand on a phone."""
    css = open(CSS, encoding="utf-8").read()
    b = _blocks(css)
    assert re.search(r'summary[^{]*\{[^}]*display:\s*block', b[700]), \
        "the summary is no longer a control at <=700px"
    print("  the summary is still tappable at <=700px .......... OK")


def test_the_phone_order_puts_history_last():
    """Stacking alone put every race of every year between the top graph and
    the bests panel -- the detail of a career above the summary of it."""
    css = open(STYLE, encoding="utf-8").read()
    b = _blocks(css)[900]
    assert re.search(r'\.main-col\s*\{[^}]*display:\s*contents', b), \
        ".main-col is not promoted, so order cannot interleave the sidebar"
    def order_of(sel):
        m = re.search(re.escape(sel) + r'\s*\{[^}]*order:\s*(\d+)', b)
        return int(m.group(1)) if m else None
    chart = order_of(".main-col > .chart-fold")
    side = order_of(".sidebar")
    hist = order_of(".main-col > .sport-section")
    assert None not in (chart, side, hist), (chart, side, hist)
    assert chart < side < hist, f"graph {chart}, sidebar {side}, history {hist}"
    print(f"  phone order: graph {chart} < sidebar {side} < history "
          f"{hist} .... OK")


# ------------------------------------------------------------------ #
# The sidebar must actually stack (owner, 2026-09-01)
# ------------------------------------------------------------------ #

STYLE = os.path.join(os.path.dirname(CSS), "style.css")


def test_the_page_layout_stacks_on_narrow_screens():
    """★ THE BUG TWO ROUNDS OF CHART WORK MISSED.

    .page-layout is display:flex with the default flex-wrap:nowrap, so a
    child CANNOT move to its own line however its flex value is set. The old
    rule was `.sidebar { flex: 1 1 auto; }` with a comment saying "stack below
    the main column on phones" -- which it could never do. The sidebar stayed
    in the row at a fixed width and the main column got what was left.
    """
    css = open(STYLE, encoding="utf-8").read()
    b = _blocks(css)
    assert 900 in b, "no <=900px block for the page layout"
    body = b[900]
    assert re.search(r'\.page-layout\s*\{[^}]*flex-direction:\s*column', body), \
        "the layout still cannot stack: no flex-direction on .page-layout"
    print("  .page-layout goes to a column at <=900px ........... OK")


def test_the_sidebar_gives_up_its_fixed_width():
    """.sidebar is `flex: 0 0 340px` further up -- don't grow, don't shrink.
    Overriding width alone leaves the basis, and the strip stays 340px."""
    css = open(STYLE, encoding="utf-8").read()
    body = _blocks(css)[900]
    m = re.search(r'\.sidebar\s*\{([^}]*)\}', body)
    assert m, ".sidebar is not overridden at <=900px"
    decl = m.group(1)
    assert "flex:" in decl, "width alone will not beat `flex: 0 0 340px`"
    assert re.search(r'flex:\s*1\s+1\s+auto', decl), decl
    print("  .sidebar drops its fixed basis, not just its width .. OK")


if __name__ == "__main__":
    for fn in [test_narrow_breakpoints_exist,
               test_charts_shrink_at_every_step,
               test_the_stack_fits_a_phone_screen,
               test_breakpoints_reuse_the_sites_narrow_line,
               test_every_wide_chart_is_folded_and_open_by_default,
               test_summary_is_hidden_on_desktop_and_shown_when_narrow,
               test_nothing_js_side_closes_the_folds,
               test_the_summary_is_still_a_control_when_narrow,
               test_the_phone_order_puts_history_last,
               test_the_page_layout_stacks_on_narrow_screens,
               test_the_sidebar_gives_up_its_fixed_width]:
        fn()
    print("\nall chart-height tests passed")
