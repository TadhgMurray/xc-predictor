#!/usr/bin/env python3
"""
diag_calibration.py -- is the model's own uncertainty honest, and is it
honest AT THE HORIZON the recruiting projection lives at?

    python scripts/diag_calibration.py
    python scripts/diag_calibration.py --model model/data/model.pt --draws 40000
    python scripts/diag_calibration.py --bands 2,8,20,44,104,208

★ WHY THIS COMES FIRST (owner, 2026-09-16, on the Monte Carlo and the
  win-probability tooltip). Every number race_sim publishes is a functional
  of the per-athlete sigma the model emits. If the model's 68% band really
  covers 50% of outcomes, "73% to win" is confidently wrong and worse than
  no number at all. So: does the band cover what it claims?

★ AND BUCKETED BY HORIZON, WHICH IS THE POINT. An overall 68% can hide 90%
  coverage at two weeks and 40% at two years, and the two live in the same
  model: feature_extraction emits a FORECAST twin at 2-40 weeks and a
  HORIZON twin at 44-208 weeks (HORIZON_GAP_MIN_WEEKS), the second existing
  precisely so high-school-to-college projection is in distribution. A
  sigma that does not widen with the gap is a sigma that is lying about the
  long end -- which is the end the recruiting page would publish.

★ THE SECOND QUESTION, AND IT IS NOT CALIBRATION. With a flat draw over
  44-208 weeks and a Gaussian likelihood, the classic failure is that the
  model learns to predict the POOL MEAN far out: excellent loss, perfect
  calibration, and useless for ranking, because it says everybody lands in
  the same place. The tell is DISPERSION -- the spread of what it predicts
  against the spread of what happened. Reported per band as `spread`, where
  1.00 is honest and 0.30 means "it is shrinking everyone to the middle".

WHAT IT READS
    The extracted chunks and the athlete-disjoint validation split that
    model/train.py already builds (val_mask.pt), so these are unseen
    people. No database. The horizon is read off the context's
    days_since_last_race, which is feature 3 -- the same feature the twins
    are generated against.

⚠ THE NUMBERS ARE ABOUT THE CHECKPOINT AND THE EXTRACTION TOGETHER. ISSUES
  G means the extraction could not see 25.1% of rated college XC, and those
  rows are exactly the hs->college horizon targets, so the long bands here
  are measured on a thinned population until the re-extraction lands.
"""
import argparse
import math
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, os.path.join(_ROOT, "model"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# the context feature that carries the gap; see feature_extraction's
# "[course_difficulty, distance_meters, day_of_year, days_since_last_race, ...]"
GAP_FEATURE = 3

# week edges. 2-40 is the forecast twin's range, 44-208 the horizon twin's,
# so the bands are cut where the generators are.
DEFAULT_BANDS = (0, 2, 8, 20, 44, 104, 208, 10_000)

# the z for a two-sided band, and what it should cover
Z_BANDS = ((1.0, 0.6827), (1.645, 0.90))


# the two keys a valid older checkpoint may be short of, and nothing else;
# see predict._loadModel -- the baseline rule rides in the state dict and a
# file trained before those buffers existed was trained under the default.
TOLERATED_MISSING = ("baseline_mode", "baseline_half_life")


# contiguous runs at this many offsets across the split; see sampleIndices
SAMPLE_BLOCKS = 200


def sampleIndices(total, want, blocks=SAMPLE_BLOCKS):
    """Which validation rows to score: contiguous runs at evenly spaced
    offsets across the WHOLE split.

    ★ NOT range(want), which is what the first run did. The chunks are
      written in athlete order, so the first 50,000 of 10,000,000 is the
      corpus's first 0.5% -- and it topped out at a 50-week horizon, giving
      2,503 rows in the 44-104w band and an empty 104w+ and 208w+. The
      bands that gate the recruiting projection were measured on almost
      nothing.

    ★ AND NOT A RANDOM SAMPLE EITHER. ChunkedRaceDataset caches ONE chunk,
      so 50,000 random global indices is ~50,000 torch.loads of whole
      10,000-example files. Contiguous runs keep that cache working while
      still spanning every chunk.
    """
    if want <= 0 or total <= 0:
        return []
    if want >= total:
        return list(range(total))
    blocks = max(1, min(int(blocks), want))
    per = max(1, want // blocks)
    span = total - per
    out, seen = [], set()
    for k in range(blocks):
        start = 0 if blocks == 1 else int(round(k * span / (blocks - 1)))
        for i in range(start, min(start + per, total)):
            if i not in seen:
                seen.add(i)
                out.append(i)
    return out[:want]


def unwrapState(blob):
    """The weights out of whatever torch.load returned.

    train.py writes model.pt as a BARE state_dict and its resume checkpoint
    as {"epoch", "model", "optimizer", ...}; predict.py additionally accepts
    an older {"state_dict": ...} wrapper. Take any of the three."""
    if not isinstance(blob, dict):
        return blob
    for key in ("state_dict", "model"):
        inner = blob.get(key)
        if isinstance(inner, dict):
            return inner
    return blob


def venueCount(state):
    """n_venues FROM THE CHECKPOINT ITSELF, the way predict._loadModel does
    it. XCPredictor's default is 1, so constructing it bare and then loading
    a real checkpoint is a size mismatch on venue_embedding.weight -- and
    any OTHER guess silently reindexes every venue."""
    w = state["venue_embedding.weight"]
    return int(w.shape[0])


def surpriseKeys(missing, unexpected):
    """! TOLERATED BY NAME, NOT BY strict=False. Accepting any missing key
    would let a genuinely broken checkpoint load and report calibration for
    a model that is partly random."""
    return sorted((set(missing) - set(TOLERATED_MISSING)) | set(unexpected))


def _bandLabel(lo, hi):
    if hi >= 10_000:
        return f"{lo}w+"
    return f"{lo}-{hi}w"


def coverage(resid_log, sigma_log, z):
    """The share of residuals inside +/- z sigma. `resid_log` is
    log(actual) - log(predicted), which is the scale the model's sigma is
    on."""
    import numpy as np
    if not len(resid_log):
        return None
    return float((np.abs(resid_log) <= z * sigma_log).mean())


def report(gaps_weeks, resid_log, sigma_log, pred_log, actual_log,
           bands=DEFAULT_BANDS, out=print):
    """One line per horizon band. Pure: arrays in, printing out, so the
    arithmetic is testable without a model."""
    import numpy as np
    out("")
    out(f"  {'band':>9} {'n':>9} {'cover68':>8} {'cover90':>8} "
        f"{'sigma':>7} {'|err|':>7} {'spread':>7}")
    rows = []
    for lo, hi in zip(bands, bands[1:]):
        m = (gaps_weeks >= lo) & (gaps_weeks < hi)
        n = int(m.sum())
        if not n:
            continue
        c68 = coverage(resid_log[m], sigma_log[m], 1.0)
        c90 = coverage(resid_log[m], sigma_log[m], 1.645)
        sd_pred = float(np.std(pred_log[m]))
        sd_act = float(np.std(actual_log[m]))
        spread = (sd_pred / sd_act) if sd_act > 0 else float("nan")
        rows.append({"band": _bandLabel(lo, hi), "n": n, "c68": c68,
                     "c90": c90, "sigma": float(np.mean(sigma_log[m])),
                     "err": float(np.mean(np.abs(resid_log[m]))),
                     "spread": spread})
        flag68 = "" if abs(c68 - 0.6827) < 0.05 else ("  <-- LOW" if c68 < 0.6827
                                                     else "  <-- WIDE")
        out(f"  {rows[-1]['band']:>9} {n:>9,} {c68:>8.3f} {c90:>8.3f} "
            f"{rows[-1]['sigma']:>7.4f} {rows[-1]['err']:>7.4f} "
            f"{spread:>7.2f}{flag68}")
    out("")
    out("  cover68/90  the share of outcomes inside the band the model claims;"
        " 0.683 and 0.900 are honest")
    out("  sigma       the mean predicted sigma in LOG time -- it must GROW"
        " down the table")
    out("  |err|       mean absolute residual in log time (0.01 = 1%)")
    out("  spread      sd(predicted) / sd(actual). 1.0 is honest;"
        " well under 1 is regression to the mean,")
    out("              which ranks nobody and is the failure a flat"
        " long-horizon draw invites")
    # the monotonicity check, stated rather than left to the eye
    sig = [r["sigma"] for r in rows]
    if len(sig) >= 2 and any(b < a - 1e-9 for a, b in zip(sig, sig[1:])):
        out("\n  ⚠ SIGMA DOES NOT GROW MONOTONICALLY WITH THE HORIZON. A band"
            " further out that claims to be\n    more certain is a band that"
            " is lying; anything race_sim publishes past it is overconfident.")
    return rows


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default=None, help="checkpoint (default: the "
                    "one train.py/predict.py would load)")
    ap.add_argument("--data", default=None, help="chunk directory")
    ap.add_argument("--draws", type=int, default=50_000,
                    help="validation examples to score (default 50,000)")
    ap.add_argument("--batch", type=int, default=512)
    ap.add_argument("--bands", default=None,
                    help="week edges, comma separated "
                         f"(default {','.join(str(b) for b in DEFAULT_BANDS)})")
    a = ap.parse_args()

    import numpy as np
    import torch
    import train as T
    from transformer import XCPredictor

    data_dir = a.data or T.DATA_DIR
    bands = (tuple(int(x) for x in a.bands.split(",")) if a.bands
             else DEFAULT_BANDS)

    ds = T.ChunkedRaceDataset(data_dir)
    _train, val = T.splitTrainVal(ds)
    n = min(len(val), a.draws)
    print(f"[calibration] {data_dir}: {len(val):,} held-out examples, "
          f"scoring {n:,}")

    model_path = a.model or getattr(T, "MODEL_OUT", None) or \
        os.path.join(data_dir, "model.pt")
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    state = unwrapState(torch.load(model_path, map_location=dev))
    model = XCPredictor(n_venues=venueCount(state))
    missing, unexpected = model.load_state_dict(state, strict=False)
    bad = surpriseKeys(missing, unexpected)
    if bad:
        raise RuntimeError("checkpoint does not match the model: "
                           + ", ".join(bad))
    model.to(dev).eval()
    print(f"[calibration] {model_path} on {dev}")

    loader = torch.utils.data.DataLoader(
        torch.utils.data.Subset(val, sampleIndices(len(val), n)),
        batch_size=a.batch, shuffle=False, collate_fn=T.collateRagged)

    gaps, resid, sigma, pred_l, act_l = [], [], [], [], []
    done = 0
    seen_rows = 0
    with torch.no_grad():
        for batch in loader:
            # collateRagged returns (padded, masks, context, target, venues)
            seqs, masks, ctx, tgt = batch[0], batch[1], batch[2], batch[3]
            ven = batch[4] if len(batch) > 4 else None
            seqs, masks, ctx = seqs.to(dev), masks.to(dev), ctx.to(dev)
            if ven is not None:
                ven = ven.to(dev)
            secs, lo, hi, sig = model.predictInterval(seqs, masks, ctx, ven)
            # ★ THE CHUNKS STORE RAW TARGETS IN SECONDS -- train.py says so in
            #   as many words, and the training loop z-scores them on the fly
            #   with model.targetZ(sequences, masks, targets). So the target
            #   IS the answer; there is nothing to undo.
            #
            # ! WHAT THE FIRST RUN PRINTED. Treating it as a z and
            #   exponentiating gives exp(seconds * target_std + target_mean)
            #   -- inf for every row, so |err| was inf, spread was nan, and
            #   cover68 read 0.000 in every band including one with n=27.
            #   That table said nothing about the model.
            true_secs = tgt.to(dev).to(torch.float32).reshape(-1)
            keep = (torch.isfinite(secs) & torch.isfinite(true_secs)
                    & torch.isfinite(sig) & (secs > 0) & (true_secs > 0)
                    & (sig > 0))
            gaps.append(ctx[:, GAP_FEATURE][keep].cpu().numpy())
            resid.append((torch.log(true_secs) - torch.log(secs))[keep]
                         .cpu().numpy())
            sigma.append(sig[keep].cpu().numpy())
            pred_l.append(torch.log(secs)[keep].cpu().numpy())
            act_l.append(torch.log(true_secs)[keep].cpu().numpy())
            done += int(keep.sum())
            seen_rows += int(keep.numel())
    if not done:
        print("nothing scorable")
        return 1
    # ! A TABLE BUILT ON A THIRD OF THE ROWS IS NOT THE ANSWER EITHER. The
    #   drop is silent otherwise, and the first run's whole table was an
    #   artifact of rows that should never have been scored.
    dropped = seen_rows - done
    if dropped:
        print(f"[calibration] dropped {dropped:,} of {seen_rows:,} rows as "
              f"non-finite or non-positive ({dropped / seen_rows:.1%})")
        if dropped > 0.02 * seen_rows:
            print("  ⚠ THAT IS TOO MANY TO IGNORE -- read it as a bug in the"
                  " scoring or the chunks, not as a result.")

    gaps = np.concatenate(gaps) / 7.0            # days -> weeks
    print(f"[calibration] {done:,} scored; horizons "
          f"{gaps.min():.1f}w to {gaps.max():.1f}w")
    report(gaps, np.concatenate(resid), np.concatenate(sigma),
           np.concatenate(pred_l), np.concatenate(act_l), bands)
    print("  Nothing from racecast/race_sim.py should be published while a"
          " band that matters reads LOW.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
