#!/usr/bin/env python3
"""
diag_crest_read.py -- which crest the SITE picks for a name, and why.

    python scripts/diag_crest_read.py "Oregon"
    python scripts/diag_crest_read.py "Oregon" --state OR

★ WHY THIS EXISTS (owner, 2026-09-18): "I'm looking at the Oregon(or) page,
  or its athlete pages, or any results it has. It all has the wrong logo but
  right text."

  RIGHT TEXT AND WRONG LOGO IS THE WHOLE CLUE, and it rules out most of what
  had been guessed at before it. The label and the crest are supposed to share
  one resolver (school_identity.contextState -- see crestState's docstring), so
  if the text says (OR) and the badge is Wisconsin's, then either they are NOT
  sharing it, or they agree and the row they agree on holds the wrong picture.
  diag_crest.py shows what is STORED; this shows what is CHOSEN, and then what
  is actually in the chosen file.

! IT ASKS THE SITE'S OWN CODE, not a copy of its logic. Anything that
  re-implemented the resolution here could be right while the site is wrong,
  which is the one outcome that would waste the run.
"""
import argparse
import hashlib
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# The same normaliser anet_teams uses, so a stored source_url can be matched
# back to the anet team whose mascot it is: anet's value is protocol-relative
# and the stored one is what was fetched (scheme added, "=s512" appended).
_URL_KEY = ("regexp_replace(regexp_replace(btrim(lower({c})), "
            "'^//', 'https://'), '=s[0-9]+$', '')")


def fileDigest(path):
    """sha256 of the bytes on disk, so two rows pointing at the same PICTURE
    are visible even when their URLs differ."""
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()[:12]
    except Exception as exc:                          # noqa: BLE001
        return f"unreadable: {exc}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("school")
    ap.add_argument("--state", default=None,
                    help="resolve as if mentioned in this state (default: "
                         "every cluster the name has, plus no state at all)")
    args = ap.parse_args()

    from database import getConn
    import school_identity
    import school_logo

    school_identity.loadLabels(getConn, force=True)
    school_logo.loadCrests(getConn, force=True)

    name = args.school
    clusters = (school_identity._LABELS.get("clusters") or {}).get(name, {})
    primary = (school_identity._LABELS.get("map") or {}).get(name)

    print(f"\n=== how the site resolves {name!r} ===")
    print(f"    primary cluster (the no-context fallback): {primary}")
    print(f"    CONTEXT_MIN_SHARE = {school_identity.CONTEXT_MIN_SHARE}")
    print(f"    clusters: " + ", ".join(
        f"{st} {sh:.4f}" for st, sh in sorted(clusters.items(),
                                              key=lambda kv: -kv[1])) or "none")

    states = [args.state] if args.state else (
        sorted(clusters, key=lambda s: -clusters[s]) + [None])
    print(f"\n    {'asked with':<12} {'contextState':<14} {'crest row':<10} "
          f"{'file bytes':<16} file")
    print(f"    {'-' * 12} {'-' * 14} {'-' * 10} {'-' * 16} {'-' * 20}")
    for st in states:
        ctx = school_identity.contextState(name, st)
        got = school_logo.crestState(name, st)
        if got is None:
            print(f"    {str(st or '(none)'):<12} {str(ctx):<14} "
                  f"{'NO CREST':<10}")
            continue
        row_state, ver, path = got[0], got[1], got[2]
        print(f"    {str(st or '(none)'):<12} {str(ctx):<14} "
              f"{row_state or '(any)':<10} {fileDigest(path):<16} "
              f"{os.path.basename(path)}")

    # ★★ AND WHOSE PICTURE IS IN EACH ROW. The row can be filed under the
    #    right state and still hold another team's mascot, if the crest queue
    #    picked the wrong team when it was written. anet_team.anet_state is
    #    anet's own answer, so this names the team and where anet puts it.
    print(f"\n=== whose mascot each stored row actually holds ===")
    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT l.state, COALESCE(l.level, ''), t.team_id, t.school,
                       t.anet_state, t.mascot, l.sha
                FROM   school_logo l
                LEFT   JOIN anet_team t
                       ON {_URL_KEY.format(c='t.mascot_url')}
                        = {_URL_KEY.format(c='l.source_url')}
                WHERE  l.school = %s
                ORDER  BY l.state, t.team_id
            """, (name,))
            rows = cur.fetchall()
        conn.rollback()
    if not rows:
        print("    no stored rows for that exact name.")
    else:
        print(f"    {'row':<6} {'team':<8} {'anet says it is':<26} "
              f"{'anet_state':<10} mascot")
        print(f"    {'-' * 6} {'-' * 8} {'-' * 26} {'-' * 10} {'-' * 16}")
        for st, _lv, tid, tschool, astate, mascot, _sha in rows:
            flag = ""
            if astate and st and astate.strip().upper() != st.strip().upper():
                flag = "   <-- another state's team"
            print(f"    {st or '--':<6} {str(tid or '?'):<8} "
                  f"{str(tschool or '(no anet team for this image)'):<26} "
                  f"{str(astate or '?'):<10} {str(mascot or '')}{flag}")

    # ⚠⚠ AND ONE CACHE AFTER THE PICK IS NOT KEYED ON THE PICTURE. crestUrl
    #    puts the image's own hash in `v=` precisely so new bytes mean a new
    #    URL -- but thumbPath names the small copy from (file name, px) alone,
    #    and the file name is a hash of (school, state, level), which does NOT
    #    change when the contents do. So a thumbnail drawn from an earlier,
    #    wrong picture keeps answering, at the one size the page actually
    #    asks for, while everything above this line is correct.
    print(f"\n=== what the page asks for, and what the thumb cache holds ===")
    print(f"    THUMB_DIR = {school_logo.THUMB_DIR}")
    print(f"    THUMB_PX  = {sorted(school_logo.THUMB_PX)}")
    for st in states:
        url = school_logo.crestUrl(name, st, px=128)
        got = school_logo.crestState(name, st)
        if got is None:
            continue
        full = got[2]
        print(f"\n    asked with {st or '(none)'}: {url}")
        print(f"      full file   {os.path.basename(full)}  "
              f"bytes {fileDigest(full)}")
        for px in sorted(school_logo.THUMB_PX):
            small = school_logo.thumbPath(full, px)
            if small == full:
                continue
            same = "(same picture)" if (fileDigest(small)[:12]
                                        and os.path.exists(small)) else ""
            # the useful comparison is not the digests matching -- a resized
            # PNG never matches its source -- it is the MTIME order, which is
            # the only thing thumbPath uses to decide staleness.
            try:
                stale = os.path.getmtime(small) < os.path.getmtime(full)
            except OSError:
                stale = None
            print(f"      px={px:<4} {os.path.basename(small):<28} "
                  f"bytes {fileDigest(small)}"
                  + ("   <-- OLDER THAN THE FULL FILE: stale thumb"
                     if stale else ""))

    # ★★ ARE ANY OF THESE STORED FILES THE SAME PICTURE? (owner, 2026-09-18:
    #    "OR shows the IL logo".) Byte digests cannot answer that -- two sizes
    #    of one image differ in every byte -- and a browser answer can come
    #    from its own week-old cache. This compares the PICTURES: grayscale,
    #    32x32, mean absolute difference. Near zero means the same image.
    print(f"\n=== are any stored files the same picture? ===")
    try:
        from PIL import Image
    except Exception as exc:                          # noqa: BLE001
        print(f"    Pillow unavailable ({exc}) -- cannot compare pictures.")
    else:
        thumbs = {}
        for st in [x for x in states if x]:
            got = school_logo.crestState(name, st)
            if got is None:
                continue
            try:
                im = Image.open(got[2]).convert("L").resize((32, 32))
                thumbs[st] = list(im.getdata())
            except Exception as exc:                  # noqa: BLE001
                print(f"    {st}: unreadable ({exc})")
        seen = sorted(thumbs)
        for i, a in enumerate(seen):
            for b in seen[i + 1:]:
                diff = sum(abs(x - y) for x, y in zip(thumbs[a], thumbs[b]))
                diff /= float(len(thumbs[a]))
                verdict = ("THE SAME PICTURE" if diff < 2.0 else
                           "nearly the same" if diff < 8.0 else "different")
                print(f"    {a} vs {b}: mean pixel difference "
                      f"{diff:6.2f}   {verdict}")
        if len(seen) < 2:
            print("    fewer than two stored crests to compare.")

    # ★★ AND WHO ELSE WEARS THIS EXACT IMAGE, with the family each name
    #    counts as -- the number markShared compares against SHARED_MIN. A
    #    crest suppressed as a "district placeholder" is suppressed by THIS
    #    list, so it is the only thing worth reading when one disappears.
    print(f"\n=== who else wears each of these images (the shared count) ===")
    sys.path.insert(0, os.path.join(_ROOT, "scripts"))
    from scrape_school_logos import _family, _isRosterish, SHARED_MIN
    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT state, sha FROM school_logo
                           WHERE school = %s AND sha IS NOT NULL
                           ORDER BY state""", (name,))
            mine = cur.fetchall()
            for st, sha in mine:
                cur.execute("""SELECT school, state FROM school_logo
                               WHERE sha = %s ORDER BY school, state""", (sha,))
                wearers = cur.fetchall()
                fams = {_family(w) for w, _ in wearers if not _isRosterish(w)}
                flag = ("  <-- SUPPRESSED: counts as a placeholder"
                        if len(fams) >= SHARED_MIN else "")
                print(f"\n    {name} ({st}): {len(wearers)} row(s), "
                      f"{len(fams)} famil(ies), SHARED_MIN={SHARED_MIN}{flag}")
                for w, ws in wearers[:12]:
                    mark = " [roster status, not counted]" if _isRosterish(w) else ""
                    print(f"      {w} ({ws or '--'}) -> {_family(w)!r}{mark}")
                if len(wearers) > 12:
                    print(f"      ... and {len(wearers) - 12} more")
        conn.rollback()

    print("\n  read it in this order: contextState decides the state, the "
          "crest row is looked up under it,\n  and the last table says whose "
          "mascot that row's picture really is.")


if __name__ == "__main__":
    main()
