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

GROUND = (0x14, 0x16, 0x1a)
INK = (0xe6, 0xe8, 0xeb)


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


def darkColor(tok, prop=""):
    """The dark-mode twin of one colour token for property `prop`. Pure."""
    c = _parse(tok)
    if c is None:
        return tok
    r, g, b, a = c
    if "shadow" in prop:
        return _fmt(0, 0, 0, min(1.0, a * 1.6))
    h, l, s = colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)
    l2 = 0.08 + (1.0 - l) * 0.86
    if l2 < 0.3:
        s *= 0.6                 # a dark ground keeps its hue, not its glare
    r2, g2, b2 = colorsys.hls_to_rgb(h, l2, s)
    return _fmt(r2 * 255, g2 * 255, b2 * 255, a)


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
    out, any_change = [], False
    for d in _splitTop(body, ";"):
        if ":" not in d:
            continue
        prop, _, val = d.partition(":")
        prop = prop.strip()
        if not _COLOR_PROPS.match(prop) or _NOT_COLOR.search(prop):
            continue
        new, changed = mapValue(val.strip(), prop.lower())
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
{PREFIX} main.rc .rc-follow {{ background: #1d2025; }}
{PREFIX} main.rc .rc-follow.is-on {{ background: #e6e8eb; color: #141618; }}
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
