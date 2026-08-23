"""Take a verified local copy of corrections.py before anything clears it.

    python scripts/backup_corrections.py            # take one
    python scripts/backup_corrections.py --list     # what is already saved
    python scripts/backup_corrections.py --restore engine/corrections.bak-...

Run from the PROJECT ROOT.

★ WHY A FILE AND NOT JUST GIT. Most of what is in corrections.py at any moment
  is NOT in git. On the machine this was written for, the working file held
  5,248 distance and 2,065 result overrides against HEAD's 4,114 and 623 --
  1.6MB of decisions that exist on one disk. `git checkout` would not bring
  those back, because git never had them.

⚠ AND *.bak IS GITIGNORED, DELIBERATELY. The .gitignore says so: a
  corrections.py.bak pair alone is 102MB. That is the right call and it is
  also the risk -- this copy lives on ONE disk and nothing replicates it.
  If the overrides matter, commit corrections.py as well; this is a seatbelt
  for the next ten minutes, not an archive.

! VERIFIED BY HASH, NOT BY EXIT STATUS. A 52MB copy that silently truncates
  looks exactly like a 52MB copy that worked, and the moment you find out is
  the moment you need it. The copy is re-read and hashed against the source
  before this reports success.
"""
import argparse
import datetime
import hashlib
import io
import os
import shutil
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(_ROOT, "engine", "corrections.py")
PREFIX = "corrections.bak-"

_DICTS = ("_DISTANCE_OVERRIDES_XC", "_DISTANCE_OVERRIDES_TF",
          "_RESULT_OVERRIDE_XC", "_RESULT_OVERRIDE_TF",
          "_GENDER_OVERRIDES_XC", "_GENDER_OVERRIDES_TF",
          "_DISTANCE_DROP_XC", "_DISTANCE_DROP_TF",
          "_RESULT_DROP_XC", "_RESULT_DROP_TF")


def digest(path, chunk=1 << 22):
    h = hashlib.sha256()
    with io.open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                return h.hexdigest()
            h.update(b)


def counts(path):
    """What the file RESOLVES to -- .update() and .clear() order included.

    ! THE POINT OF PRINTING THIS IS THE WIPE. A backup taken after a
      .clear() block preserves the block, so restoring it restores the wipe.
      The counts say which state you actually saved.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location("corrections_backup_probe",
                                                  path)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, os.path.join(_ROOT, "engine"))
    spec.loader.exec_module(mod)
    return {n: len(getattr(mod, n, ()) or ()) for n in _DICTS}


def existing():
    d = os.path.dirname(SOURCE)
    return sorted(os.path.join(d, f) for f in os.listdir(d)
                  if f.startswith(PREFIX))


def show(path):
    size = os.path.getsize(path)
    when = datetime.datetime.fromtimestamp(
        os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M:%S")
    print(f"  {os.path.basename(path):<34}{size:>14,} bytes   {when}")


def main():
    ap = argparse.ArgumentParser(
        description="Verified local backup of engine/corrections.py.")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--restore", metavar="PATH")
    args = ap.parse_args()

    if args.list:
        found = existing()
        if not found:
            print("\n  no backups yet.\n")
            return 0
        print(f"\n  {len(found)} backup(s):\n")
        for p in found:
            show(p)
        print()
        return 0

    if args.restore:
        src = args.restore
        if not os.path.isabs(src):
            src = os.path.join(_ROOT, src)
        if not os.path.exists(src):
            print(f"\n  no such backup: {src}\n")
            return 1
        print(f"\n  restoring {os.path.basename(src)} -> engine/corrections.py")
        print("  what it resolves to:")
        for n, c in counts(src).items():
            print(f"    {n:<26}{c:>12,}")
        shutil.copy2(src, SOURCE)
        if digest(src) != digest(SOURCE):
            print("\n  ⚠ RESTORE DID NOT VERIFY. The copy differs from the "
                  "backup. Do not trust it.\n")
            return 1
        print("\n  restored and verified.")
        print("  ⚠ engine/dump_overrides.py is NOT a pipeline step -- run it "
              "now, or\n    dist_override still holds whatever was there "
              "before.\n")
        return 0

    if not os.path.exists(SOURCE):
        print(f"\n  {SOURCE} does not exist.\n")
        return 1

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(os.path.dirname(SOURCE), f"{PREFIX}{stamp}.py")

    print(f"\n  source: engine/corrections.py "
          f"({os.path.getsize(SOURCE):,} bytes)")
    print("  what it resolves to right now:")
    for n, c in counts(SOURCE).items():
        print(f"    {n:<26}{c:>12,}")

    shutil.copy2(SOURCE, dest)
    a, b = digest(SOURCE), digest(dest)
    if a != b:
        print(f"\n  ⚠ COPY DID NOT VERIFY -- {a[:16]} vs {b[:16]}.\n"
              f"    {dest}\n    is NOT a usable backup. Check disk space.\n")
        return 1

    print(f"\n  saved and verified:")
    show(dest)
    print(f"    sha256 {a}")
    print(f"\n  restore with:\n"
          f"    python scripts/backup_corrections.py --restore "
          f"engine/{os.path.basename(dest)}\n")
    print("  ⚠ *.bak-* is gitignored (a corrections.py.bak pair is 102MB), so "
          "this copy\n    lives on this disk only. If the overrides matter, "
          "commit corrections.py too.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
