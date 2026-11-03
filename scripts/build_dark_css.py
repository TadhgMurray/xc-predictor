#!/usr/bin/env python3
"""
build_dark_css.py -- racecast/static/dark.css, the site's dark mode, written
from the light stylesheets themselves.

    python scripts/build_dark_css.py            # rewrite dark.css
    python scripts/build_dark_css.py --check    # exit 1 when dark.css is stale

★ WHY GENERATED (owner, 2026-11-03: "dark mode"). The site's colours live in
  twelve stylesheets and eight page <style> blocks, about half as tokens
  (--ink, --line ...) and half as hex written where they are used. A
  hand-kept dark sheet would drift the first time anyone touched a colour.
  So every rule that sets a colour is copied under html[data-theme="dark"]
  with the colour re-mapped, and tests/test_dark_css.py fails when dark.css
  is older than the sheets it was made from.

★ THE MAPPING: lightness flipped, hue kept. White ground -> near-black,
  near-black ink -> near-white, the navy link -> a light navy that reads on
  dark, a pale hover tint -> a dark one of the same hue. Shadows stay
  shadows (black, a little stronger), never a glow.
! SPECIFICITY: the prefix adds (0,1,1) to every selector, so a dark rule
  always beats the light one it copies; !important is carried over.
"""
import argparse
import colorsys
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
STATIC = os.path.join(_ROOT, "racecast", "static")
TEMPLATES = os.path.join(_ROOT, "racecast", "templates")
OUT = os.path.join(STATIC, "dark.css")
PREFIX = 'html[data-theme="dark"]'

# the sheets, in the order pages load them; dark.css itself is never read
SHEETS = ("style.css", "race.css", "pages2.css", "features.css", "tools.css", "records.css",
          "mypage.css", "contrib.css", "ncaa.css", "previews.css", "state-odds.css",
          "athlete-charts.css")

NAMED = {"white": (255, 255, 255), "black": (0, 0, 0), "gray": (128, 128, 128),
         "grey": (128, 128, 128), "silver": (192, 192, 192), "whitesmoke": (245, 245, 245),
         "gainsboro": (220, 220, 220), "lightgray": (211, 211, 211), "lightgrey": (211, 211, 211)}
_COLOR = re.compile(
    r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)|hsla?\([^)]*\)|\b(?:" + "|".join(NAMED) + r")\b")
_URL = re.compile(r"url\([^)]*\)", re.I)

# ! ALREADY DARK BY DESIGN: the home page's band is a dark ground with light
#   type in BOTH modes -- flipped, it turned into a pale slab on a dark page
KEEP_AS_IS = re.compile(r"\.hm-(?:band|text|search|what|facts)\b")
# ...but lifted off the page by a shade, so the band still reads as a band

GROUND = (0x18, 0x18, 0x18)
INK = (0xf1, 0xf1, 0xf1)


def _parse(tok):
    """(r, g, b, a) for one colour token, or None. Pure."""
    t = tok.strip().lower()
    if t in NAMED:
        return NAMED[t] + (1.0,)
    if t.startswith("#"):
        h = t[1:]
        if len(h) in (3, 4):
            h = "".join(c * 2 for c in h)
        if len(h) not in (6, 8):
            return None
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        a = int(h[6:8], 16) / 255.0 if len(h) == 8 else 1.0
        return r, g, b, a
    m = re.match(r"(rgba?|hsla?)\(([^)]*)\)", t)
    if not m:
        return None
    parts = [p for p in re.split(r"[\s,/]+", m.group(2).strip()) if p]
    if len(parts) < 3 or any("var" in p for p in parts):
        return None
    try:
        a = 1.0
        if len(parts) > 3:
            a = float(parts[3][:-1]) / 100 if parts[3].endswith("%") else float(parts[3])
        if m.group(1).startswith("rgb"):
            r, g, b = (float(p[:-1]) * 2.55 if p.endswith("%") else float(p) for p in parts[:3])
        else:
            hh = float(parts[0].rstrip("deg")) / 360.0
            ss, ll = float(parts[1].rstrip("%")) / 100, float(parts[2].rstrip("%")) / 100
            r, g, b = (c * 255 for c in colorsys.hls_to_rgb(hh, ll, ss))
        return int(round(r)), int(round(g)), int(round(b)), a
    except ValueError:
        return None


def _fmt(r, g, b, a):
    r, g, b = (max(0, min(255, int(round(c)))) for c in (r, g, b))
    if a >= 0.999:
        return f"#{r:02x}{g:02x}{b:02x}"
    return f"rgba({r}, {g}, {b}, {round(a, 3)})"


# ★ A FEW SHADES, NOT A CONTINUUM (owner, 2026-11-03: "it looks off ...
#   how some things are really white while some are just muted grays").
#   Flipping every grey on its own turned the light sheets' dozen near-same
#   greys into a dozen visibly different ones. A neutral now lands on one of
#   a few fixed shades by what it is doing: text (ink, secondary, muted,
#   faint), ground (page, raised, sunk, strong), line (hair, firm).
# ★ THE NEUTRAL CHARCOAL (owner, 2026-11-03, of four schemes: "B or C, but
#   still kind of bad ... the links especially"). Greys with no blue in them,
#   the way YouTube and Google draw dark pages; the blue is kept for links.
TEXT_TIERS = ((0.30, (0xf1, 0xf1, 0xf1)),    # ink: #111 .. #444
              (0.55, (0xc8, 0xc8, 0xc8)),    # secondary: #555 .. #888
              (0.75, (0x9a, 0x9a, 0x9a)),    # muted: #999 .. #bbb
              (1.01, (0x6e, 0x6e, 0x6e)))    # faint: #ccc and lighter
GROUND_TIERS = ((0.985, (0x18, 0x18, 0x18)),  # white: the page itself
                (0.95, (0x1f, 0x1f, 0x1f)),   # zebra, hover, pale panel
                (0.88, (0x27, 0x27, 0x27)),   # a grey panel or chip
                (0.70, (0x30, 0x30, 0x30)))   # a strong grey ground
SELECTED = (0x3a, 0x3a, 0x3a)                 # a selected tab or chip, a black button
# the light sheets' tokens that are DARK colours: a ground of var(--ink) or
# var(--sel) is a selected state, read like a literal dark ground
DARK_TOKENS = ("--ink", "--sel", "--accent")
LINE_TIERS = ((0.85, (0x2c, 0x2c, 0x2c)),     # hairlines
              (0.60, (0x3f, 0x3f, 0x3f)))     # firm lines


def _role(prop):
    p = prop.lower()
    if "shadow" in p:
        return "shadow"
    if p.startswith("--"):
        if re.search(r"bg|ground|zebra|hover|sel-bg|panel|card|surface|fill-bg", p):
            return "ground"
        if re.search(r"line|border|rule|grid", p):
            return "line"
        return "text"
    if p.startswith("background"):
        return "ground"
    if p.startswith(("border", "outline", "column-rule")):
        return "line"
    return "text"                     # color, fill, stroke, caret, decoration


def _tier(l, tiers, lo_first=False):
    for edge, rgb in tiers:
        if (l >= edge) if not lo_first else (l < edge):
            return rgb
    return None


def darkColor(tok, prop=""):
    """The dark-mode twin of one colour token for property `prop`. Pure."""
    c = _parse(tok)
    if c is None:
        return tok
    r, g, b, a = c
    role = _role(prop)
    if role == "shadow":
        return _fmt(0, 0, 0, min(1.0, a * 1.6))
    h, l, s = colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)
    neutral = s < 0.18 or max(r, g, b) - min(r, g, b) < 24
    if neutral and prop in ("fill", "stroke") and l > 0.7:
        # SVG: a pale fill is shading (a chart band) and a pale stroke a rule
        # or a label's halo; a dark one is drawn ink, read as text below
        if prop == "stroke" and l > 0.98:
            return _fmt(*GROUND_TIERS[0][1], a)          # a halo: the page
        role = "ground" if prop == "fill" else "line"
    if neutral:
        if role == "text":
            rgb = _tier(l, TEXT_TIERS, lo_first=True)
            return _fmt(*rgb, a)
        if role == "ground":
            got = _tier(l, GROUND_TIERS)
            if got:
                return _fmt(*got, a)
            # ★ A DARK GROUND (a selected tab, a black button) IS A RAISED
            #   SURFACE, NOT A WHITE SLAB (owner, 2026-11-03: "dark mode still
            #   looks terrible" -- every selected tab was a bright block)
            return _fmt(*SELECTED, a) if l < 0.3 else _fmt(*GROUND_TIERS[-1][1], a)
        got = _tier(l, LINE_TIERS)
        return _fmt(*(got or (0x55, 0x5b, 0x63)), a)
    # a colour with a hue: links, accents, badges, tints
    if role == "text":
        # light, but in the same ORDER as the light sheet's: a ramp (the
        # field's five rating tiers) stays five steps, the navy link is one
        l2, s2 = max(0.58, min(0.86, 0.93 - 0.6 * l)), min(s, 0.55)
    elif role == "ground":
        if l > 0.85:
            l2, s2 = 0.17, s * 0.45           # a pale tint: a dark one of its hue
        elif l < 0.25:
            l2, s2 = 0.30, s * 0.7            # a deep ground: lifted a little
        else:
            return _fmt(r, g, b, a)           # a badge's colour: kept as it is
    else:
        l2, s2 = (0.30, s * 0.6) if l > 0.6 else (0.55, s)
    r2, g2, b2 = colorsys.hls_to_rgb(h, l2, s2)
    return _fmt(r2 * 255, g2 * 255, b2 * 255, a)


def _neutralL(val):
    """Lightness of the first neutral colour in a value, or None. Pure."""
    for m in _COLOR.finditer(val):
        c = _parse(m.group(0))
        if not c:
            continue
        r, g, b, _a = c
        h, l, s = colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)
        if s < 0.18 or max(r, g, b) - min(r, g, b) < 24:
            return l
        return None
    return None


_TOKEN_RX = re.compile(r"var\(\s*(?:" + "|".join(re.escape(t) for t in DARK_TOKENS) + r")\b[^)]*\)")


def _darkToken(val):
    """Does a value use one of the dark tokens (a selected ground)? Pure."""
    return bool(_TOKEN_RX.search(val or ""))


def _lightNeutral(val):
    l = _neutralL(val)
    return l is not None and l > 0.85


def _darkNeutral(val):
    l = _neutralL(val)
    return l is not None and l < 0.3


def keptGround(val):
    """Is this background a badge colour darkColor keeps? (hue, middle
    lightness) Pure."""
    for m in _COLOR.finditer(val):
        c = _parse(m.group(0))
        if not c:
            continue
        r, g, b, _a = c
        h, l, s = colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)
        neutral = s < 0.18 or max(r, g, b) - min(r, g, b) < 24
        if not neutral and 0.25 <= l <= 0.85:
            return True
    return False


def mapValue(value, prop):
    """Every colour in a declaration's value mapped; url(...) left alone.
    (new value, changed?). Pure."""
    keep = {}

    def hold(m):
        keep[f"\x00{len(keep)}\x00"] = m.group(0)
        return f"\x00{len(keep) - 1}\x00"
    v = _URL.sub(hold, value)
    changed = [False]

    def swap(m):
        new = darkColor(m.group(0), prop)
        if new != m.group(0):
            changed[0] = True
        return new
    v = _COLOR.sub(swap, v)
    for k, orig in keep.items():
        v = v.replace(k, orig)
    return v, changed[0]


_COLOR_PROPS = re.compile(
    r"^(?:--[\w-]+|color|background(?:-color|-image)?|border(?:-[\w-]+)?|outline(?:-color)?|"
    r"box-shadow|text-shadow|fill|stroke|caret-color|text-decoration(?:-color)?|"
    r"column-rule(?:-color)?|accent-color|scrollbar-color)$", re.I)


def _splitTop(s, sep):
    """Split at `sep` outside parentheses and brackets. Pure."""
    out, depth, cur = [], 0, []
    for ch in s:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == sep and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return out


def prefixSelector(sel):
    """Each selector of a list under the dark prefix. Pure."""
    out = []
    for s in _splitTop(sel, ","):
        s = s.strip()
        if not s:
            continue
        if s.startswith(":root"):
            out.append(PREFIX + s[5:])
        elif re.match(r"html\b", s):
            out.append(PREFIX + s[4:])
        else:
            out.append(f"{PREFIX} {s}")
    return ", ".join(out)


_NOT_COLOR = re.compile(r"(?:radius|width|style|collapse|spacing|image-(?:slice|outset|repeat|width|source))$",
                        re.I)


def darkDecls(body):
    """The colour declarations of one rule body: literal colours mapped,
    everything else (var(), inherit, transparent) copied as it is. Pure.

    ! COPIED EVEN WHEN NOTHING CHANGES: the prefix raises every dark rule's
      specificity, so a mapped base rule (".d { fill: #ccc }") would beat
      the light sheet's more specific rule that sets the same property
      through a token (".d.t1 { fill: var(--rt1) }") -- the race page's
      field went grey. Copying every colour declaration keeps the light
      sheets' own cascade among the dark rules -- a rule whose colours are
      all tokens is copied too, for the same reason."""
    decls = []
    for d in _splitTop(body, ";"):
        if ":" not in d:
            continue
        prop, _, val = d.partition(":")
        prop = prop.strip()
        if _COLOR_PROPS.match(prop) and not _NOT_COLOR.search(prop):
            decls.append((prop, val.strip()))
    # ! A BADGE KEEPS ITS INK: a rule whose ground keeps its colour (the PR
    #   gold, a sport tag) keeps its text colour too, or white on gold turns
    #   grey on gold
    grounds = [v for p, v in decls if p.lower().startswith("background")]
    badge = any(keptGround(v) for v in grounds)
    # ! WHITE TYPE SITS ON A GROUND: with no ground in its own rule it is on a
    #   badge or a photo set elsewhere and stays white; on a token or dark
    #   ground (a selected chip, which turns light here) it turns dark
    token_ground = any(_darkToken(v) for v in grounds)
    dark_ground = token_ground or any(_darkNeutral(v) for v in grounds)
    out, any_change = [], False
    for prop, val in decls:
        lp = prop.lower()
        if _role(prop) == "text" and not prop.startswith("--"):
            # white type keeps white: on a badge, on a photo (no ground in
            # its rule), and on a selected surface, which stays dark here
            if badge or dark_ground or (lp == "color" and not grounds and _lightNeutral(val)):
                if _lightNeutral(val) or badge or lp != "color":
                    out.append(f"{prop}: {val}")
                    continue
        if token_ground and (lp.startswith("background") or lp.startswith("border")) and _darkToken(val):
            out.append(f"{prop}: {_TOKEN_RX.sub(_fmt(*SELECTED, 1), val)}")
            any_change = True
            continue
        new, changed = mapValue(val, prop.lower())
        any_change = any_change or changed
        out.append(f"{prop}: {new}")
    return out, any_change


def _blocks(css):
    """Yield (prelude, body) for the top level of a stylesheet. Pure."""
    i, n = 0, len(css)
    while i < n:
        j = css.find("{", i)
        if j < 0:
            return
        semi = css.find(";", i)
        if 0 <= semi < j and css[i:semi].strip().startswith("@"):
            i = semi + 1                     # @import / @charset
            continue
        depth, k = 1, j + 1
        while k < n and depth:
            if css[k] == "{":
                depth += 1
            elif css[k] == "}":
                depth -= 1
            k += 1
        yield css[i:j].strip(), css[j + 1:k - 1]
        i = k


def darkSheet(css):
    """The dark rules for one stylesheet's text. Pure."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out = []
    for prelude, body in _blocks(css):
        if prelude.startswith("@"):
            if "prefers-color-scheme" in prelude:
                continue
            if re.match(r"@(media|supports|layer|container)\b", prelude):
                inner = darkSheet(body)
                if inner.strip():
                    out.append(f"{prelude} {{\n{inner}}}\n")
            continue                          # @keyframes, @font-face ...
        if "data-theme" in prelude or "prefers-color-scheme" in prelude:
            continue                          # already a theme's own rule
        if KEEP_AS_IS.search(prelude):
            continue                          # already dark by design
        decls, _changed = darkDecls(body)
        if decls:
            out.append(f"{prefixSelector(prelude)} {{ " + "; ".join(decls) + "; }\n")
    return "".join(out)


# the hand-written part: the ground, form controls, and the marks that are
# drawn dark on transparent (a black logo on a dark page disappears)
BASE = f"""{PREFIX} {{ color-scheme: dark; background: {_fmt(*GROUND, 1)}; color: {_fmt(*INK, 1)}; }}
{PREFIX} body {{ background: {_fmt(*GROUND, 1)}; color: {_fmt(*INK, 1)}; }}
{PREFIX} img.school-crest, {PREFIX} img.unit-logo, {PREFIX} img.unit-chip-logo,
{PREFIX} img.crest, {PREFIX} .crest img {{ background: rgba(255, 255, 255, 0.9); border-radius: 4px; }}
{PREFIX} .hm-band {{ background: #1f1f1f; }}
{PREFIX} main.rc .rc-follow {{ background: #272727; }}
{PREFIX} main.rc .rc-follow.is-on {{ background: #3a3a3a; color: #f1f1f1; }}
"""

# ★ LINKS, LAST, SO NOTHING ABOVE OVERRIDES THEM (owner, 2026-11-03: "the
#   links especially don't work with the black background"). One light blue
#   for a link in a sentence -- Google's own dark-mode link colour -- and the
#   names that fill every table (athletes, schools, meets) in plain ink, the
#   blue only on hover: a page of blue names on black was the glare.
LINKS = f"""
{PREFIX} {{ --link: #8ab4f8; --accent: #8ab4f8; }}
{PREFIX} a {{ color: #8ab4f8; }}
{PREFIX} table a, {PREFIX} .board-grid a, {PREFIX} .bests-panel a, {PREFIX} .pr-panel a,
{PREFIX} .rc-crow a, {PREFIX} .topnav a {{ color: #f1f1f1; text-decoration-color: rgba(241, 241, 241, 0.35); }}
{PREFIX} table a:hover, {PREFIX} .board-grid a:hover, {PREFIX} .bests-panel a:hover,
{PREFIX} .pr-panel a:hover, {PREFIX} .rc-crow a:hover {{ color: #8ab4f8; }}
"""


def build():
    parts = ["/* dark.css -- GENERATED by scripts/build_dark_css.py from the light sheets.\n"
             "   Do not edit: change a light colour and run the script. */\n", BASE]
    for name in SHEETS:
        p = os.path.join(STATIC, name)
        if os.path.exists(p):
            body = darkSheet(open(p, encoding="utf-8").read())
            if body:
                parts.append(f"\n/* ---- {name} ---- */\n{body}")
    for name in sorted(os.listdir(TEMPLATES)):
        if not name.endswith(".html"):
            continue
        txt = open(os.path.join(TEMPLATES, name), encoding="utf-8").read()
        for blk in re.findall(r"<style[^>]*>(.*?)</style>", txt, flags=re.S):
            # ! a page with its own dark mode (the embed) would be flipped
            #   back to light by a second mapping
            if "{{" in blk or "{%" in blk or "prefers-color-scheme" in blk or "data-theme" in blk:
                continue
            body = darkSheet(blk)
            if body:
                parts.append(f"\n/* ---- templates/{name} ---- */\n{body}")
    parts.append("\n/* ---- links, last ---- */" + LINKS)
    return "".join(parts)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    text = build()
    if a.check:
        cur = open(OUT, encoding="utf-8").read() if os.path.exists(OUT) else ""
        if cur != text:
            print("dark.css is stale: run python scripts/build_dark_css.py")
            return 1
        print("dark.css is current")
        return 0
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"[dark] {OUT}: {text.count(chr(10)):,} lines")
    return 0


if __name__ == "__main__":
    sys.exit(main())
