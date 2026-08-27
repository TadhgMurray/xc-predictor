# Project: xc-predictor / scripts
# File:    extract_progress.py
# Purpose: Is feature extraction actually doing anything? Answers from
#          the FILES it writes, so it works no matter how the run's
#          stdout is buffered or which window it is in.
#
#     python scripts/extract_progress.py
#     python scripts/extract_progress.py --watch     # every 30s
#
# ★ CHUNKS ARE THE PROGRESS BAR. saveAll writes chunk_NNNN.pt as it
#   streams, 10,000 examples each. A growing count is the run working;
#   a count that has not moved in several minutes while the process is
#   alive means it is stuck in the DB stream, not writing.

import argparse
import os
import time

DATA = os.path.join("model", "data")
_FINAL = ("metadata.pkl", "encoders.pkl", "venue_vocab.pkl")


def snapshot():
    if not os.path.isdir(DATA):
        return None
    chunks = sorted(f for f in os.listdir(DATA)
                    if f.startswith("chunk_") and f.endswith(".pt"))
    total = sum(os.path.getsize(os.path.join(DATA, f)) for f in chunks)
    newest = max((os.path.getmtime(os.path.join(DATA, f)) for f in chunks),
                 default=None)
    done = [f for f in _FINAL if os.path.exists(os.path.join(DATA, f))]
    return chunks, total, newest, done


def report():
    snap = snapshot()
    if snap is None:
        print(f"  {DATA} does not exist yet -- extraction has not started.")
        return
    chunks, total, newest, done = snap
    if not chunks:
        print("  no chunks yet. Extraction builds encoders and the venue "
              "vocab first\n  (DB aggregates, several minutes) before the "
              "first chunk lands.")
    else:
        age = time.time() - newest
        print(f"  {len(chunks):,} chunks   {total / 1e9:.2f} GB   "
              f"~{len(chunks) * 10_000:,} examples")
        print(f"  newest chunk written {age / 60:.1f} min ago", end="")
        print("   <-- STALLED?" if age > 600 else "")
    # ! THESE THREE ARE WRITTEN LAST, so all three present means the
    #   stream finished -- not merely that chunks exist.
    if len(done) == len(_FINAL):
        print("  FINISHED: metadata, encoders and venue vocab all written.")
    elif done:
        print(f"  partial finish markers: {', '.join(done)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", action="store_true")
    args = ap.parse_args()
    while True:
        print(f"\n  [{time.strftime('%H:%M:%S')}] feature extraction")
        report()
        if not args.watch:
            return
        time.sleep(30)


if __name__ == "__main__":
    main()
