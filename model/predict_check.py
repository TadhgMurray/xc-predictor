"""
predict_check.py -- does the trained model actually produce numbers?

    python model\\predict_check.py            # a few hundred examples
    python model\\predict_check.py --n 2000   # more

The five-minute answer to "can the model be run": load model.pt, run a
real forward pass over held-out-style examples from the feature chunks,
denormalize, and print predictions next to actuals with an error
summary. No database, no training -- just the artifact doing its job.

★ MECHANICS FIRST, QUALITY SECOND. A pass here means the whole chain
  works: chunks -> dataset -> collate -> forward -> denormalize. The
  ERROR numbers mean only what the training run that wrote model.pt
  meant -- a MAX_CHUNKS smoke model prints honest but unimpressive
  errors, and a model trained against a half-rebuilt database prints
  nonsense. The header says which artifact it is judging by mtime.

★ n_venues COMES FROM THE CHECKPOINT, not from venue_vocab.pkl: the
  saved venue_embedding's row count is the one the weights were trained
  with, and rebuilding with any other number either crashes the load or
  silently reindexes every venue.
"""

import argparse
import os
import pickle
import sys
import time

sys.path.insert(0, "model")

import torch

import train as T
from transformer import XCPredictor


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=500,
                    help="examples to run (default 500)")
    args = ap.parse_args()

    for path in (T.MODEL_OUT, T.STATS_OUT):
        if not os.path.exists(path):
            print(f"MISSING {path} -- train first (overnight step 05, or "
                  f"python model\\train.py)")
            sys.exit(1)

    age_h = (time.time() - os.path.getmtime(T.MODEL_OUT)) / 3600
    print(f"model.pt written {age_h:.1f}h ago")

    with open(T.STATS_OUT, "rb") as f:
        stats = pickle.load(f)
    mean = stats.get("mean", stats.get("target_mean"))
    std = stats.get("std", stats.get("target_std"))
    kind = stats.get("kind", "seconds")
    if kind == "log_ratio":
        print(f"target stats: ln(t/last race) mean {mean:+.4f} std {std:.4f}"
              f"  (last-race error alone ~{100 * std:.1f}%)")
    else:
        print(f"target stats: mean {mean:.1f}s, std {std:.1f}s")

    state = torch.load(T.MODEL_OUT, map_location="cpu")
    n_venues = state["venue_embedding.weight"].shape[0]
    model = XCPredictor(n_venues=n_venues)
    model.load_state_dict(state)
    model.eval()
    n_params = sum(p.numel() for p in model.parameters())
    print(f"loaded: {n_params:,} parameters, {n_venues:,} venue rows")

    # a couple of chunks is plenty for a smoke read
    T.MAX_CHUNKS = max(1, (args.n // 4096) + 1)
    dataset = T.ChunkedRaceDataset(T.DATA_DIR)
    n = min(args.n, len(dataset))

    print(f"running {n} examples...")
    preds, actuals, bases, los, his = [], [], [], [], []
    with torch.no_grad():
        batch = []
        for i in range(n):
            batch.append(dataset[i])
            if len(batch) == 256 or i == n - 1:
                sequences, masks, context, targets, venues = \
                    T.collateRagged(batch)
                if kind == "log_ratio" and hasattr(model, "predictInterval"):
                    p, lo, hi, _sig = model.predictInterval(
                        sequences, masks, context, venues)
                    preds.append(p)
                    los.append(lo)
                    his.append(hi)
                    bases.append(model.baselineSeconds(sequences, masks))
                else:
                    out = model(sequences, masks, context, venues)
                    preds.append(out * std + mean)     # z-score -> seconds
                actuals.append(targets)
                batch = []
    preds = torch.cat(preds)
    actuals = torch.cat(actuals)

    print("\n  predicted   actual     error   (normalized 5K seconds)")
    for i in range(min(10, n)):
        p, a = preds[i].item(), actuals[i].item()
        print(f"  {p:9.1f} {a:8.1f} {p - a:+9.1f}")

    err = (preds - actuals).abs()
    print(f"\n  over {n} examples:")
    print(f"  MAE     {err.mean():6.1f}s")
    print(f"  median  {err.median():6.1f}s")
    print(f"  p90     {err.quantile(0.9):6.1f}s")
    print(f"  mean prediction {preds.mean():6.1f}s vs "
          f"mean actual {actuals.mean():6.1f}s")
    if bases:
        # ★ THE NUMBER THAT JUDGES THE MODEL: against "you will run what you
        #   ran last time". Same examples, same units.
        b_err = (torch.cat(bases) - actuals).abs()
        print(f"  last-race baseline MAE {b_err.mean():6.1f}s  "
              f"(the model must beat this to be worth having)")
    if los:
        lo, hi = torch.cat(los), torch.cat(his)
        inside = ((actuals >= lo) & (actuals <= hi)).float().mean()
        print(f"  1-sigma band: mean width {(hi - lo).mean():6.1f}s, "
              f"{100 * inside:.0f}% of actuals inside (68% if calibrated)")
    print("\nthe model runs and outputs. Judge the NUMBERS by what "
          "trained this model.pt (a smoke run is a smoke run).")


if __name__ == "__main__":
    main()
