"""
build_college_list.py -- an EXTERNAL check on school_levels.pkl.

WHY THIS EXISTS
    school_levels.pkl tags Cornell, Arkansas, Liberty, Belmont, Lafayette,
    Washington, Albany, Cerritos, Riverside and Moorpark as 'hs', and Tiffin and
    St. Norbert as 'ms'. Those are universities and community colleges. The
    failure is systematic on SHORT, AMBIGUOUS names -- "Cornell" is also a high
    school, "Washington" is dozens of them -- and whatever built the pickle
    resolved the ambiguity to the majority class, which is high school.

    Consequences, all live:
      - poolFor step 2 pools an unreadable-grade row at Cornell as hs_*.
      - college_flag.py finds only 2,224 universities, because it looks them up
        through the same broken lookup. Every collided name is invisible to it.

★ WHY WIKIPEDIA'S "COMMON NAME" COLUMN IS THE RIGHT SOURCE. An institution list
  like IPEDS carries legal names -- "University of Arkansas", "Cornell
  University" -- which do not match what this corpus stores. The NCAA
  institution lists carry a COMMON NAME column that is the athletics short name:

      Abilene Christian University  ->  Abilene Christian
      Belmont University            ->  Belmont

  That is exactly the string format in results.school.

★ THIS DOES NOT FIX THE COLLISION, IT MEASURES IT. A name on both lists --
  "Cornell" as a university AND as a high school -- is still ambiguous. What
  this produces is the list of names school_levels.pkl currently gets WRONG, so
  the size and shape of the problem is known before anyone rebuilds the pickle.

Writes college_names.txt. Changes nothing else.
"""

import os
import re
import sys

_SOURCES = [
    ("NCAA D-I",   "https://en.wikipedia.org/wiki/List_of_NCAA_Division_I_institutions"),
    ("NCAA D-II",  "https://en.wikipedia.org/wiki/List_of_NCAA_Division_II_institutions"),
    ("NCAA D-III", "https://en.wikipedia.org/wiki/List_of_NCAA_Division_III_institutions"),
    ("NAIA",       "https://en.wikipedia.org/wiki/List_of_NAIA_institutions"),
    ("CCCAA",      "https://en.wikipedia.org/wiki/California_Community_College_Athletic_Association"),
]

_NAME_COLS = ("common name", "school", "institution", "college", "team", "member")


# ------------------------------------------------------------------ #
# CHUNK 1 -- FETCH AND PARSE
# ------------------------------------------------------------------ #

# ⚠ WIKIPEDIA 403s THE DEFAULT USER-AGENT. pandas.read_html(url) fetches through
#   urllib, whose default UA Wikipedia rejects outright. The page must be pulled
#   separately with a real identifying UA -- their policy asks for a contact
#   string -- and the HTML handed to read_html as text.
_UA = ("xc-predictor/1.0 (course difficulty research; "
       "contact: local script) python-requests")


def fetchHtml(url):
    """Page source, with a User-Agent Wikipedia accepts."""
    try:
        import requests
        r = requests.get(url, headers={"User-Agent": _UA}, timeout=30)
        r.raise_for_status()
        return r.text
    except ImportError:
        from urllib.request import Request, urlopen
        req = Request(url, headers={"User-Agent": _UA})
        with urlopen(req, timeout=30) as fh:
            return fh.read().decode("utf-8", "replace")


def fetchNames(url):
    """
    Every plausible school-name cell from every table on a Wikipedia page.

    pandas.read_html handles wiki table markup -- rowspans, footnote markers,
    multi-level headers -- which hand-rolled regex does not. Both the COMMON
    NAME and the full SCHOOL column are taken: the common name matches this
    corpus, the full name catches rows where that column is missing.
    """
    import io

    import pandas as pd

    names = set()
    for table in pd.read_html(io.StringIO(fetchHtml(url))):
        cols = {str(c).strip().lower(): c for c in table.columns}
        for want in _NAME_COLS:
            col = next((cols[c] for c in cols if want in c), None)
            if col is None:
                continue
            for v in table[col].dropna().astype(str):
                v = re.sub(r"\[[^\]]*\]", "", v).strip()     # drop [1] markers
                v = re.sub(r"\s+", " ", v)
                if 2 <= len(v) <= 60 and not v.lower().startswith("nan"):
                    names.add(v)
    return names


def normKey(s):
    """
    The pickle's own key shape, so comparisons are like for like.

    Imported from normalize_distance when available -- re-deriving it is the
    drift the codebase warns about -- with a fallback so the script still runs
    outside the repo.
    """
    try:
        from normalize_distance import normSchoolKey
        return normSchoolKey(s)
    except Exception:
        k = re.sub(r"[^a-z0-9]", "", (s or "").lower())
        return k or None


# ------------------------------------------------------------------ #
# CHUNK 2 -- AUDIT THE PICKLE
# ------------------------------------------------------------------ #

def audit(college_names):
    """
    ★ THE OUTPUT THAT MATTERS. Which names the external list calls a college and
      school_levels.pkl calls something else.

    Three buckets:
      MISLABELLED  pickle says hs/ms/elem -> actively wrong, and poolFor is
                   using it right now
      MISSING      pickle has no entry    -> poolFor falls through to the source
                   fallback, which is right for tfrrs and silent for anet
      AGREES       pickle says college    -> fine
    """
    try:
        from normalize_distance import _SCHOOL_LEVELS
    except Exception as exc:
        print(f"[cmp] cannot load school_levels.pkl ({exc})")
        return

    if not _SCHOOL_LEVELS:
        print("[cmp] school_levels.pkl is empty or absent")
        return

    buckets = {"mislabelled": [], "missing": [], "agrees": []}
    for name in sorted(college_names):
        key = normKey(name)
        if not key:
            continue
        level = _SCHOOL_LEVELS.get(key)
        if level in ("hs", "ms", "elem"):
            buckets["mislabelled"].append((name, level))
        elif level is None:
            buckets["missing"].append((name, None))
        else:
            buckets["agrees"].append((name, level))

    print(f"\n[cmp] {len(college_names):,} college names checked against "
          f"{len(_SCHOOL_LEVELS):,} pickle entries")
    for b in ("agrees", "mislabelled", "missing"):
        print(f"    {b:<12} {len(buckets[b]):>6,}")

    print("\n[cmp] MISLABELLED -- colleges the pickle calls youth "
          "(poolFor is using these now):")
    for name, level in buckets["mislabelled"][:60]:
        print(f"    [{level:<4}] {name}")
    if len(buckets["mislabelled"]) > 60:
        print(f"    ... and {len(buckets['mislabelled']) - 60:,} more")

    return buckets


# ------------------------------------------------------------------ #
# CHUNK 3 -- ENTRY POINT
# ------------------------------------------------------------------ #

def main(out_path="college_names.txt"):
    names = set()
    for label, url in _SOURCES:
        try:
            got = fetchNames(url)
            names |= got
            print(f"[cmp] {label:<10} {len(got):>5,} names")
        except Exception as exc:
            print(f"[cmp] {label:<10} FAILED ({exc})")

    if not names:
        print("[cmp] nothing fetched -- check network access to wikipedia.org")
        return

    with open(out_path, "w", encoding="utf-8") as fh:
        for n in sorted(names):
            fh.write(n + "\n")
    print(f"[cmp] wrote {len(names):,} names to {out_path}")

    audit(names)


if __name__ == "__main__":
    _HERE = os.path.dirname(os.path.abspath(__file__))
    for _p in (_HERE, os.path.dirname(_HERE),
               os.path.join(os.path.dirname(_HERE), "engine")):
        if os.path.isdir(_p) and _p not in sys.path:
            sys.path.insert(0, _p)
    main(sys.argv[1] if len(sys.argv) > 1 else "college_names.txt")