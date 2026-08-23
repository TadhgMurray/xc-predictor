"""Which corrections.py is the engine actually reading?

    python scripts/diag_corrections.py

Run from the PROJECT ROOT. READ ONLY.

⚠ THE FAILURE THIS EXISTS FOR IS SILENT AND IT CHANGES EVERY RATING.

  Two tools disagreed about the same path on the same clean checkout:
  wipe_overrides.py read the FILE AS TEXT and reported a wipe block in place,
  while dump_overrides.py IMPORTED it and got 4,114 distance overrides -- and
  on another machine, the same import got zero. A disagreement like that means
  the bytes on disk, the bytes git thinks are on disk, and the bytes Python
  executes are not all the same thing, and every one of those is silent.

  The three ways that happens, all checked below:

    1. A CACHED .pyc THAT PYTHON NEVER REVALIDATES. PEP 552 allows
       hash-based bytecode with check_source off -- written by
       `compileall --invalidation-mode unchecked-hash`, among others. Python
       then uses the .pyc forever and NEVER looks at the .py again. Editing
       corrections.py does nothing. This one is invisible to git, to your
       editor, and to every grep.

    2. A TRACKED FILE GIT WAS TOLD TO STOP WATCHING.
       `git update-index --assume-unchanged` / `--skip-worktree` make
       `git status` report a clean tree over a file that has been edited.
       Common on a 52MB generated file, because it makes git fast.

    3. A SECOND corrections.py EARLIER ON sys.path. dump_overrides inserts
       both "engine" and "scripts", so whichever comes first wins.

★ corrections.py IS UPSTREAM OF EVERY NUMBER THE SITE PUBLISHES: it decides
  the distance, the distance decides normalized_time, and normalized_time
  decides the rating. Reading the wrong copy is not a cosmetic problem.
"""
import datetime
import glob
import io
import os
import struct
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(_ROOT, "engine", "corrections.py")
MARK = "# === WIPE FOR REBUILD (scripts/wipe_overrides.py) ==="

_DICTS = ("_DISTANCE_OVERRIDES_XC", "_DISTANCE_OVERRIDES_TF",
          "_RESULT_OVERRIDE_XC", "_RESULT_OVERRIDE_TF",
          "_GENDER_OVERRIDES_XC", "_GENDER_OVERRIDES_TF",
          "_DISTANCE_DROP_XC", "_DISTANCE_DROP_TF",
          "_RESULT_DROP_XC", "_RESULT_DROP_TF")


def when(path):
    return datetime.datetime.fromtimestamp(
        os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M:%S")


def run(*args):
    try:
        return subprocess.run(args, cwd=_ROOT, capture_output=True,
                              text=True, timeout=60).stdout.strip()
    except Exception as exc:                              # noqa: BLE001
        return f"({type(exc).__name__}: {exc})"


def pycMode(path):
    """(label, safe) for one .pyc. See PEP 552."""
    try:
        with io.open(path, "rb") as f:
            head = f.read(16)
        if len(head) < 16:
            return "truncated", False
        flags = struct.unpack("<I", head[4:8])[0]
        if not flags & 0b1:
            return "timestamp (revalidated against the source)", True
        if flags & 0b10:
            return "checked hash (revalidated against the source)", True
        return "UNCHECKED HASH -- the source is NEVER read again", False
    except Exception as exc:                              # noqa: BLE001
        return f"unreadable ({exc})", True


def main():
    print("\nWHICH corrections.py IS THE ENGINE READING?\n")

    # ---- 1. the file on disk -------------------------------------- #
    print("1. THE FILE ON DISK")
    if not os.path.exists(PATH):
        print(f"   {PATH}\n   DOES NOT EXIST.")
        return 1
    text = io.open(PATH, encoding="utf-8", errors="replace").read()
    print(f"   {PATH}")
    print(f"   {os.path.getsize(PATH):,} bytes, modified {when(PATH)}")
    at = text.find(MARK)
    if at < 0:
        print("   wipe block: NOT present")
    else:
        line = text.count("\n", 0, at) + 1
        print(f"   wipe block: PRESENT, at line {line:,}")
    n_clear = text.count(".clear()")
    print(f"   .clear() statements anywhere in the file: {n_clear}")
    tail = [l for l in text.splitlines()[-12:]]
    print("   last 12 lines:")
    for l in tail:
        print(f"     | {l[:96]}")

    # ---- 2. what git thinks ---------------------------------------- #
    print("\n2. WHAT GIT THINKS")
    tracked = run("git", "ls-files", "-v", "--", "engine/corrections.py")
    if not tracked:
        print("   NOT TRACKED by git. Nothing to compare against.")
    else:
        flag = tracked.split()[0]
        print(f"   ls-files -v: {tracked[:80]}")
        if flag == "H":
            print("   normal: git is watching this file.")
        elif flag in ("h", "S"):
            print(f"   ⚠ FLAG '{flag}' -- assume-unchanged / skip-worktree. "
                  f"git status LIES about\n     this file: it reports a clean "
                  f"tree no matter what the bytes are.\n"
                  f"     Undo: git update-index --no-assume-unchanged "
                  f"--no-skip-worktree engine/corrections.py")
        else:
            print(f"   ⚠ unexpected flag '{flag}'.")
        # ⚠ `git diff` IS UNSTAGED-ONLY, AND THAT UNDER-REPORTED BY 1.6MB.
        #   A file with staged-but-uncommitted content shows only the newest
        #   unstaged edits -- 14 lines of wipe block sitting on top of 1,134
        #   staged overrides. HEAD is the comparison that matters here.
        unstaged = run("git", "diff", "--stat", "--",
                       "engine/corrections.py")
        staged = run("git", "diff", "--cached", "--stat", "--",
                     "engine/corrections.py")
        both = run("git", "diff", "HEAD", "--stat", "--",
                   "engine/corrections.py")
        print(f"   unstaged      : {unstaged or '(none)'}")
        print(f"   staged        : {staged or '(none)'}")
        print(f"   vs HEAD (both): {both or '(none)'}")
        # ! THE ONE CHECK THAT SEES THROUGH assume-unchanged. --no-index
        #   compares bytes and never consults the index.
        blob = run("git", "rev-parse", "HEAD:engine/corrections.py")
        here = run("git", "hash-object", "--", "engine/corrections.py")
        print(f"   HEAD blob : {blob}")
        print(f"   file hash : {here}")
        print("   " + ("SAME -- the working file IS what HEAD holds."
                       if blob and blob == here else
                       "⚠ DIFFERENT -- the working file is NOT what HEAD holds."))

    # ---- 3. the bytecode cache ------------------------------------- #
    print("\n3. THE BYTECODE CACHE")
    pycs = sorted(glob.glob(os.path.join(_ROOT, "engine", "__pycache__",
                                         "corrections.*.pyc")))
    if not pycs:
        print("   none. Python will read the source.")
    for p in pycs:
        label, safe = pycMode(p)
        print(f"   {os.path.basename(p)}  {os.path.getsize(p):,} bytes, "
              f"{when(p)}")
        print(f"     invalidation: {label}")
        if not safe:
            print(f"     ⚠ DELETE IT. Python is executing this instead of the "
                  f"source and will\n       keep doing so however many times "
                  f"you edit corrections.py.")

    # ---- 4. what import actually gets ------------------------------ #
    print("\n4. WHAT `import corrections` ACTUALLY GETS")
    print("   (dump_overrides inserts 'engine' then 'scripts'; same order here)")
    others = [p for p in glob.glob(os.path.join(_ROOT, "*", "corrections.py"))
              if os.path.abspath(p) != os.path.abspath(PATH)]
    if others:
        print("   ⚠ ANOTHER corrections.py EXISTS and may shadow this one:")
        for p in others:
            print(f"     {p}")
    sys.path.insert(0, os.path.join(_ROOT, "engine"))
    sys.path.insert(0, os.path.join(_ROOT, "scripts"))
    try:
        import corrections                                # noqa: E402
    except Exception as exc:                              # noqa: BLE001
        print(f"   IMPORT FAILED: {type(exc).__name__}: {exc}")
        return 1
    print(f"   __file__  : {getattr(corrections, '__file__', '?')}")
    print(f"   __cached__: {getattr(corrections, '__cached__', '?')}")
    print()
    for n in _DICTS:
        obj = getattr(corrections, n, None)
        size = "MISSING" if obj is None else f"{len(obj):,}"
        print(f"     {n:<26}{size:>12}")

    # ---- 5. the verdict -------------------------------------------- #
    print("\n5. THE VERDICT")
    from_text = at >= 0
    from_import = len(getattr(corrections, "_DISTANCE_OVERRIDES_XC", ())) == 0
    if from_text == from_import:
        print("   The text and the import AGREE"
              + (" -- the overrides are wiped." if from_text
                 else " -- the overrides are live."))
        return 0
    print("   ⚠ THE TEXT AND THE IMPORT DISAGREE.")
    print(f"     the file {'HAS' if from_text else 'has NO'} a wipe block, "
          f"but the import "
          f"{'sees no overrides' if from_import else 'sees overrides'}.")
    print("     Sections 3 and 4 above say which of the three causes it is.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
