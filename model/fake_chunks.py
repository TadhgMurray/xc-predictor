# Project: xc-predictor
# File:    model/fake_chunks.py
# Purpose: Write synthetic feature chunks in feature_extraction.py's exact
#          on-disk format, so model/train.py can be RUN without a database.
#
# ★★ WHY THIS EXISTS. train.py and transformer.py were rewritten on
#    2026-09-02 with no torch in the session and were never executed
#    (HANDOFF section 4, issue #112: "model untested"). The only way to
#    reach them was feature_extraction.py, which needs the database, takes
#    hours, and writes gigabytes -- and whose own header warns that a
#    feature-width mismatch "surfaces as a shape error inside the first
#    Linear AFTER the extraction has written gigabytes".
#
#    So the first execution of the training code cost a full extraction.
#    This makes it cost two seconds. The numbers are noise; the SHAPES,
#    dtypes, keys and layout are the real thing, which is what the smoke
#    test needs.
#
# ⚠ IT IS NOT A DATA SOURCE. A model trained on this learns nothing --
#   the target is built from the sequence plus noise. It proves the chain
#   runs, not that it works.
#
#   python model/fake_chunks.py --out /tmp/fake --chunks 3 --chunk-size 200
import argparse
import os
import pickle

import torch

# ! MUST MATCH transformer.py. That is half the point of the smoke test:
#   if these drift, the fake chunks stop reproducing the real failure.
from transformer import SEQUENCE_FEATURES, CONTEXT_FEATURES, SEQ_NORM_TIME

DEFAULT_CHUNK_SIZE = 200
MAX_HISTORY = 12


def buildChunk(n, n_venues, seed, ragged=True):
    """One chunk dict, exactly as feature_extraction.py writes it.

    Ragged is the current layout: `sequences` is every real row of every
    example stacked, and `offsets` (length n+1) says where each example's
    rows start and stop. The padded layout (`sequences` [n, S, F] plus
    `masks`) is the legacy one train.py still reads, and `ragged=False`
    exercises that path.
    """
    g = torch.Generator().manual_seed(seed)
    lengths = torch.randint(1, MAX_HISTORY + 1, (n,), generator=g)
    # ⚠ ONE EXAMPLE WITH NO HISTORY. collateRagged has a branch for it and
    #   a note about the NaN it used to produce; a smoke run that never
    #   sends an empty sequence never tests that branch.
    if n > 4:
        lengths[3] = 0

    total = int(lengths.sum())
    seq = torch.randn(total, SEQUENCE_FEATURES, generator=g) * 3.0
    # feature 0 is normalized_time in SECONDS -- baselineSeconds divides by
    # it, so it must be positive and realistic or the log-ratio is garbage
    seq[:, SEQ_NORM_TIME] = 900.0 + torch.rand(total, generator=g) * 600.0
    offsets = torch.zeros(n + 1, dtype=torch.long)
    offsets[1:] = torch.cumsum(lengths, dim=0)

    ctx = torch.randn(n, CONTEXT_FEATURES, generator=g)
    # the target is the last race's time nudged a little, so ln(t/last) is
    # a small number the way the real one is
    base = torch.full((n,), 1100.0)
    has = lengths > 0
    base[has] = seq[offsets[1:][has] - 1, SEQ_NORM_TIME]
    targets = base * torch.exp(torch.randn(n, generator=g) * 0.04)

    chunk = {"context": ctx, "targets": targets,
             "venues": torch.randint(0, n_venues, (n,), generator=g)}
    if ragged:
        chunk["sequences"] = seq
        chunk["offsets"] = offsets
    else:
        longest = max(1, int(lengths.max()))
        padded = torch.zeros(n, longest, SEQUENCE_FEATURES)
        masks = torch.zeros(n, longest, dtype=torch.bool)
        for i in range(n):
            k = int(lengths[i])
            if k:
                padded[i, :k] = seq[offsets[i]:offsets[i + 1]]
                masks[i, :k] = True
        chunk["sequences"] = padded
        chunk["masks"] = masks
    return chunk


def write(out_dir, n_chunks=3, chunk_size=DEFAULT_CHUNK_SIZE,
          n_venues=17, ragged=True, seed=0, val_mask=True):
    os.makedirs(out_dir, exist_ok=True)
    for c in range(n_chunks):
        torch.save(buildChunk(chunk_size, n_venues, seed + c, ragged),
                   os.path.join(out_dir, f"chunk_{c:04d}.pt"))
    with open(os.path.join(out_dir, "metadata.pkl"), "wb") as f:
        pickle.dump({"total_examples": n_chunks * chunk_size,
                     "chunk_size": chunk_size,
                     "num_chunks": n_chunks,
                     "max_len": MAX_HISTORY}, f)
    # ! BOTH KEYS. train.py reads n_venues to size the embedding; predict.py
    #   reads `vocab` to map a course to its row, and refuses the whole load
    #   without it -- so a fixture carrying only the first cannot smoke-test
    #   the inference half at all. Found 2026-09-17 doing exactly that.
    with open(os.path.join(out_dir, "venue_vocab.pkl"), "wb") as f:
        pickle.dump({"n_venues": n_venues,
                     "vocab": {f"fake:{i}": i for i in range(n_venues)}}, f)
    # ! AND THE ENCODERS, for the same reason: predict.py wants four
    #   artifacts beside the checkpoint or it will not load one of them.
    with open(os.path.join(out_dir, "encoders.pkl"), "wb") as f:
        pickle.dump({"grade": {"None": 0}, "school": {"None": 0},
                     "pool": {"None": 0}}, f)

    # ★ THE TWO SIDECARS THE REAL EXTRACTION WRITES, because their absence
    #   silently changes what train.py does:
    #     val_mask.pt  the ATHLETE-DISJOINT split. Without it splitTrainVal
    #                  falls back to random_split, which puts the same
    #                  athlete on both sides -- the exact leak the engine's
    #                  row-vs-race holdout had, one directory over. A smoke
    #                  run without this file never exercises the path that
    #                  will actually run.
    #     lengths.pt   history length per example, in global order, used by
    #                  ChunkAwareBatchSampler to group like with like.
    n = n_chunks * chunk_size
    g = torch.Generator().manual_seed(seed + 9999)
    if val_mask:
        # a contiguous tail, the way whole held-out athletes land
        mask = torch.zeros(n, dtype=torch.bool)
        mask[int(n * 0.8):] = True
        torch.save(mask, os.path.join(out_dir, "val_mask.pt"))
    lens = torch.randint(1, MAX_HISTORY + 1, (n,), generator=g,
                         dtype=torch.int32)
    torch.save(lens, os.path.join(out_dir, "lengths.pt"))
    return out_dir


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="/tmp/fake_chunks")
    ap.add_argument("--chunks", type=int, default=3)
    ap.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    ap.add_argument("--venues", type=int, default=17)
    ap.add_argument("--padded", action="store_true",
                    help="write the legacy padded layout instead of ragged")
    ap.add_argument("--no-val-mask", action="store_true",
                    help="omit val_mask.pt, so train.py takes the leaky "
                         "random_split fallback")
    a = ap.parse_args()
    d = write(a.out, a.chunks, a.chunk_size, a.venues,
              ragged=not a.padded, val_mask=not a.no_val_mask)
    print(f"wrote {a.chunks} x {a.chunk_size} examples to {d}")


if __name__ == "__main__":
    main()
