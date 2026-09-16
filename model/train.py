# Project: xc-predictor
# File:    model/train.py
# Purpose: Trains the XCPredictor network on the chunked tensors that
#          feature_extraction.py wrote to model/data/. Loads the data,
#          z-scores the targets, runs the train/validate loop (forward ->
#          loss -> backward -> optimizer step), and saves the trained
#          weights plus the numbers needed to turn a prediction back into
#          real seconds at inference time.

import contextlib
import os
import pickle
import time

import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader, random_split, Subset

# The model we built in transformer.py. Assumes train.py sits next to it
# in model/ (same folder), matching how feature_extraction.py imports.
# ⚠ EVERYTHING SHARED COMES FROM transformer, WHICH IMPORTS ONLY TORCH.
#   CONTEXT_YEAR_INDEX briefly came from feature_extraction instead, and
#   that made `import train` require a database password and the gitignored
#   corrections.py -- neither of which exists on the GPU box this file is
#   written to run on. tests/test_context_width.py keeps the copies honest.
from transformer import (XCPredictor, SEQUENCE_FEATURES,
                         SEQ_NORM_TIME, SEQ_DAYS_AGO, CONTEXT_YEAR_INDEX,
                         TARGET_Z_CLAMP, CONTEXT_GAP_INDEX,
                         BASELINE_LAST, BASELINE_EWMA, BASELINE_BEST2,
                         BASELINE_HALF_LIFE_DAYS, BASELINE_BEST_WINDOW_DAYS,
                         baselineWeights, ewmaFromPadded, best2FromPadded)

# ★ WHAT PREDICTIONS ARE ANCHORED ON. See transformer.BASELINE_LAST for the
#   measurement that made this a choice: the model beats "their last race
#   alone" and loses to "season mean rating", which is what being anchored on
#   one race looks like from the outside.
#
# ! CHANGING IT NEEDS A RETRAIN, NOT A RE-EXTRACTION, and that is the whole
#   reason it is worth trying. The chunks store raw targets in seconds;
#   _chunkBaselines and XCPredictor.baselineSeconds both DERIVE the baseline
#   from the sequence, and targetZ recomputes ln(target/base) on the fly. The
#   125 GB stays where it is.
BASELINE = BASELINE_LAST
BASELINE_HALF_LIFE = BASELINE_HALF_LIFE_DAYS

# ★ THE HORIZON BANDS VALIDATION IS REPORTED IN (2026-09-16). One MAE over
#   the whole val set averages three different jobs: predicting Saturday
#   from last weekend, predicting next season, and projecting a sophomore
#   into college. The corpus is 30% horizon twins, so the aggregate is
#   dragged well below what the predictions page actually does and well
#   above what the recruiting projection actually does -- and neither
#   number is the one anybody wanted.
#
#   Banded on days_since_last_race, in days. The first band is the
#   predictions page; the last is the college projection.
GAP_BANDS = ((0.0, 42.0, "<=6wk"), (42.0, 365.0, "6wk-1yr"),
             (365.0, 730.0, "1-2yr"), (730.0, float("inf"), "2yr+"))

# ------------------------------------------------------------------ #
# CONSTANTS — the training dials, named once so they don't drift
# ------------------------------------------------------------------ #

# Where feature_extraction.py saved chunk_NNNN.pt + metadata.pkl, and
# where we'll write the trained model.
#
# ★ --data OVERRIDES BOTH, and that is what makes the smoke test possible
#   without touching the real chunks. model/fake_chunks.py writes the same
#   on-disk format to any directory; before this flag existed the only way
#   to reach the training loop was to put those fakes IN model/data, beside
#   (or on top of) gigabytes that cost hours to extract. On a rented pod it
#   also lets the chunks sit on the mounted volume while the code lives on
#   the container's own disk.
DATA_DIR  = "model/data"
MODEL_OUT = os.path.join(DATA_DIR, "model.pt")

# How many training examples go through one forward/backward pass.
# Bigger = smoother gradient estimates but more memory.
#
# ⚠ 64 IS SAFE AND SMALL. This model is four layers at d_model=256 -- a few
#   million parameters -- so at batch 64 a modern GPU finishes the maths
#   long before the next batch arrives and spends the run waiting on disk.
#   512-1024 fits comfortably in 24GB and cuts the STEP COUNT by the same
#   factor, which is what the wall clock is actually made of.
#
# ! CHANGING IT CHANGES THE EFFECTIVE LEARNING RATE. A larger batch is a
#   less noisy gradient, so it can take a bigger step; the usual rule is to
#   scale the LR with the batch. --batch 512 --lr 1e-3 is a reasonable
#   pairing to start from, not a law.
BATCH_SIZE = 64

# Step size for the optimizer — how far each weight moves per update.
# 3e-4 is the standard starting point for Adam on a transformer this
# size: small enough not to diverge, big enough to make progress.
# Adam — the OPTIMIZER, the algorithm that applies the weight updates.
# The split to keep straight: loss.backward() computes the gradients
# (which way to nudge each weight); the optimizer is the separate step
# that actually moves them.
#
# Plain SGD would be: new_weight = old_weight - LEARNING_RATE * gradient
# — one fixed step size for every weight. Adam ("Adaptive Moment
# Estimation" — "moments" = running mean/variance of the gradients,
# NOT a person) improves on that in two ways:
#   1. Momentum    — averages recent gradients, not just the latest one,
#                     so updates don't zigzag through noisy gradients.
#   2. Per-weight  — tracks each weight's recent gradient size and scales
#      step sizes    its step inversely: weights with big gradients take
#                     smaller, steadier steps; quiet weights take larger.
# So LEARNING_RATE is a BASELINE, not one fixed step. Adam trains faster
# and is far less sensitive to the exact LEARNING_RATE than SGD — which
# is why 3e-4 "just works" for a transformer this size.
LEARNING_RATE = 3e-4

# One epoch = one full pass over the training set. A CEILING, not a plan:
# PATIENCE below stops the run once validation loss stops improving, which
# is the thing 20 was a guess at.
EPOCHS = 20

# ★ STOP WHEN IT STOPS LEARNING, rather than at a number picked in advance.
#   Training is billed by the hour on rented hardware, and epochs past
#   convergence cost exactly as much as the ones that helped.
#
#   PATIENCE   epochs of no NEW BEST val loss before giving up. 3 is the
#              usual choice: one bad epoch is noise, three in a row is a
#              trend.
#   MIN_DELTA  how much better counts as better. Without it, an improvement
#              of 1e-7 resets the patience counter forever and early
#              stopping never fires.
#
# ! MEASURED AGAINST THE BEST, NOT THE PREVIOUS EPOCH. Val loss wobbles;
#   comparing to the last epoch stops on the first wobble, comparing to the
#   best stops only when nothing has beaten the best for PATIENCE tries.
PATIENCE = 3
MIN_DELTA = 1e-4

# ★ DATALOADER WORKERS. The dataset reads chunk_NNNN.pt from disk inside
#   __getitem__, so with 0 workers the training process stops and waits for
#   a file read before every batch -- the GPU idles through it. Workers do
#   that reading in parallel, in other processes.
#
# ⚠ EACH WORKER GETS ITS OWN COPY OF THE ONE-CHUNK CACHE, so memory is
#   NUM_WORKERS x chunk. Chunks are 10k examples; that is fine at 4-8 and
#   worth watching above that.
#
# ! 0 IS THE DEFAULT because it is the only value guaranteed to work
#   everywhere (Windows spawn, notebooks, containers with small /dev/shm).
#   Pass --workers 8 on a real machine.
NUM_WORKERS = 0

# Mixed precision: run the forward/backward in bf16 where it is safe and
# keep the weights in fp32. Roughly free on any recent GPU, does nothing on
# CPU. Off by default; --amp turns it on.
USE_AMP = False

# Where to write the resumable checkpoint. None = do not checkpoint, which
# is right for a smoke run and wrong for anything you are paying for.
CHECKPOINT = None

# Fraction of examples held OUT of training, used only to check the
# model is generalising rather than memorising.
# ⚠ ONLY THE FALLBACK. When the chunks carry val_mask.pt (written by
#   feature_extraction), the split is BY ATHLETE from that mask and this
#   number is ignored -- see splitTrainVal for why example-level random
#   splitting quietly rewards memorisation.
VAL_FRACTION = 0.1

# ★ THE SMOKE-RUN KNOB. The full corpus is ~80M examples -- roughly 1.25M
#   steps per epoch at batch 64, days of GPU time. Set this to e.g. 200
#   (2M examples) for a first run that proves the pipeline end to end and
#   gives a loss curve in under an hour; None trains on everything.
#   Chunks are corpus-wide shuffles, so a chunk prefix is a fair sample.
MAX_CHUNKS = None

# ★ A WINDOW OF CHUNKS, NOT ONLY A PREFIX (owner, 2026-09-16: "can we train
#   on the other chunks too separately?"). The corpus is 125 GB and the pod
#   holds 100 GB, so the whole of it can only reach the GPU in shifts: train
#   on 0:3000, swap those files for 3000:6000 on disk, resume from the
#   checkpoint and keep going. The model sees all the data; the disk never
#   holds more than a third of it.
#
# ! WHICH IS MORE TRAINING, NOT A SECOND MODEL. Chunks are corpus-wide
#   shuffles, so every window is an i.i.d. sample of the same corpus -- a
#   shift boundary is not a distribution change and the loss curve runs
#   straight through it. Two models trained on halves and averaged would be
#   strictly worse than one model that saw both.
#
# ⚠ AND THE VALIDATION SET STAYS HONEST ACROSS A SHIFT, which is the part
#   that could have quietly broken. val_mask.pt is keyed by ATHLETE, so a
#   val athlete is a val athlete in every window -- the held-out people
#   never leak into training when the files change. That only holds because
#   the mask is read at the example's TRUE global index, which is what
#   base_index below exists for.
#
# (lo, hi) half-open, in chunk numbers. None means everything.
CHUNK_RANGE = None

# Fixed seed so the random train/val split is identical every run —
# makes results reproducible and comparable.
SEED = 42

# Where we save the two numbers (mean, std) that turn a z-scored
# prediction back into real seconds. Losing this file makes a trained
# model useless for inference.
STATS_OUT = os.path.join(DATA_DIR, "target_stats.pkl")

# ★ THE TARGET IS A LOG RATIO TO THE ATHLETE'S LAST RACE, and the loss is
#   Gaussian negative log-likelihood on its z-score with a LEARNED variance
#   per example (transformer.forwardDist). The model is rewarded for being
#   right and for knowing how unsure it is: a wrecked race costs little when
#   the variance head has already said "this one is uncertain", which is the
#   same protection Huber gave, and the band it learns is a real output.
#   GaussianNLLLoss takes the variance, not its log; VAR_EPS keeps it off 0.
VAR_EPS = 1e-6

# ★ FEATURE AND TARGET STATS COME FROM A CHUNK PREFIX, not the whole corpus.
#   Chunks are corpus-wide shuffles, so the first STATS_CHUNKS (2M examples)
#   are a fair sample, and reading every chunk twice per run is an epoch of
#   disk time spent on two numbers per feature.
STATS_CHUNKS = 200

# ★ OPTIMISER HYGIENE. AdamW decouples weight decay from the gradient
#   (plain Adam's L2 is scaled away by the per-weight step). A linear warmup
#   keeps the first steps small while Adam's moments are still noise, then
#   cosine decay to LR_FLOOR_FRAC of the peak. Gradient clipping bounds one
#   bad batch. None of these change what the model CAN learn; they change
#   whether a multi-hour run gets there.
WEIGHT_DECAY = 0.01
WARMUP_STEPS = 500
LR_FLOOR_FRAC = 0.1
GRAD_CLIP = 1.0

# Where the maths runs. This box is an AMD RX 7900 XTX (RDNA3, gfx1100)
# on ROCm -- verified 2026-08-26 by torch.cuda.get_device_name; an older
# note here claimed a 6900/6950 XT and sent the install down the RDNA2
# staging wheel, which is the wrong channel for this card. The right
# wheel on Windows is the gfx110X index:
#   pip install --pre torch --index-url https://rocm.nightlies.amd.com/v2/gfx110X-all/
# Non-obvious bit: PyTorch's ROCm build REUSES the CUDA API -- the same
# "cuda" device string and the same torch.cuda.is_available(). So this
# line is already correct for AMD; you never write "rocm" anywhere in
# the code. The ONLY thing that differs is which PyTorch you install
# (the ROCm wheel, not the CUDA one). Once ROCm sees the card,
# is_available() returns True; if it ever doesn't, we fall back to CPU.
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")


# _autocast
# Purpose:  the mixed-precision context, or a no-op.
# Detail:
#   ! ONE PLACE THAT KNOWS WHETHER AMP IS ON, so the training loop reads the
#     same whether it is or not. A bare `with torch.autocast(...)` in the
#     loop would need the enabled flag and the device type threaded through
#     it, and would silently do nothing on CPU while looking like it did
#     something.
def _autocast():
    if not USE_AMP or DEVICE.type != "cuda":
        return contextlib.nullcontext()
    return torch.autocast(device_type="cuda", dtype=torch.bfloat16)

# ------------------------------------------------------------------ #
# CHUNK 2 — DATASET: feed chunk_NNNN.pt files to the model
# ------------------------------------------------------------------ #
#
# feature_extraction.py saved the data as many chunk files (up to
# CHUNK_SIZE=10,000 examples each) instead of one huge file, so the full
# dataset never has to live in RAM at once. This Dataset reads them back
# the same way: it figures out which chunk a requested example lives in,
# loads ONLY that chunk, and returns the single example.
#
# A torch Dataset is a contract: implement __len__ (total count) and
# __getitem__ (fetch example i), and the DataLoader handles batching and
# shuffling for free. We add a one-chunk cache so asking for examples
# 0..9999 in a row doesn't reload the same file 10,000 times.


class ChunkedRaceDataset(Dataset):

    # __init__
    # Purpose: Read metadata.pkl (written by feature_extraction.py) so we
    #          know how many examples and chunks exist, WITHOUT loading any
    #          of the actual tensor data yet.
    # Arguments:
    #           data_dir: folder holding chunk_NNNN.pt + metadata.pkl
    def __init__(self, data_dir: str):
        self.data_dir = data_dir

        # metadata.pkl holds {"max_len", "total_examples", "num_chunks"}.
        # We only need the totals here — the heavy tensors stay on disk.
        with open(os.path.join(data_dir, "metadata.pkl"), "rb") as f:
            meta = pickle.load(f)

        self.total_examples = meta["total_examples"]
        self.chunk_size     = meta.get("chunk_size", 10_000)
        self.num_chunks = meta["num_chunks"]

        # ★ WHICH CHUNKS THIS RUN SEES. --max-chunks N is the prefix 0:N,
        #   which is what a smoke run wants; --chunk-range LO:HI is the
        #   general case, and the two are the same mechanism.
        lo, hi = 0, self.num_chunks
        if CHUNK_RANGE is not None:
            lo, hi = CHUNK_RANGE
        elif MAX_CHUNKS is not None:
            hi = MAX_CHUNKS
        lo = max(0, lo)
        hi = min(hi, meta["num_chunks"])
        if lo >= hi:
            raise SystemExit(f"chunk range {lo}:{hi} is empty "
                             f"({meta['num_chunks']} chunks exist)")

        # ⚠ base_index IS THE ONE THING A WINDOW ADDS, AND EVERYTHING ELSE
        #   FOLLOWS FROM IT. Dataset index i is global example
        #   base_index + i; lengths.pt and val_mask.pt are written in GLOBAL
        #   order, so both are sliced to the window here rather than offset
        #   at every lookup -- an offset applied in three places is an
        #   offset forgotten in one of them.
        self.base_index = lo * self.chunk_size
        self.num_chunks = hi - lo
        self.total_examples = (min(hi * self.chunk_size,
                                   meta["total_examples"])
                               - self.base_index)
        if (lo, hi) != (0, meta["num_chunks"]):
            print(f"  chunks {lo}:{hi} of {meta['num_chunks']}: training on "
                  f"{self.total_examples:,} examples "
                  f"(global {self.base_index:,}"
                  f"..{self.base_index + self.total_examples:,})")

        # One-chunk cache: remember the last chunk we loaded so repeated
        # nearby lookups reuse it instead of re-reading the file.
        # Starts empty (-1 = "no chunk loaded yet").
        self._cached_idx  = -1
        self._cached_data = None

        # ★ EVERY EXAMPLE'S LENGTH, WRITTEN BY feature_extraction._saveLengths.
        #   ChunkAwareBatchSampler sorts on this. Absent for chunks written
        #   before ragged storage, in which case the sampler falls back to
        #   plain shuffling and simply runs slower.
        lp = os.path.join(data_dir, "lengths.pt")
        self.lengths = (torch.load(lp) if os.path.exists(lp) else None)
        if self.lengths is not None:
            # sliced to the window, so the sampler indexes it with a plain
            # dataset index like everything else
            self.lengths = self.lengths[
                self.base_index:self.base_index + self.total_examples]

     # __len__
    # Purpose: Tell the DataLoader the total number of examples. It uses
    #          this to know how many batches make one epoch.
    # Output:  int — total examples across all chunks
    def __len__(self) -> int:
        return self.total_examples
    
    # _loadChunk
    # Purpose: Load one chunk file into the cache (if not already cached).
    # Arguments:
    #           chunk_idx: which chunk file to load (0, 1, 2, ...)
    # Output:  the chunk dict {"sequences","masks","context","targets"}
    def _loadChunk(self, chunk_idx: int) -> dict:
        # Cache hit — already in memory, reuse it.
        if chunk_idx == self._cached_idx:
            return self._cached_data
        
        # Cache miss — read chunk_NNNN.pt from disk (:04d = zero-padded
        # to 4 digits, matching how feature_extraction.py named them).
        path = os.path.join(self.data_dir, f"chunk_{chunk_idx:04d}.pt")
        self._cached_data = torch.load(path)
        self._cached_idx  = chunk_idx
        return self._cached_data
    
    # __getitem__
    # Purpose: Return ONE training example by its global index i. This is
    #          the method the DataLoader calls over and over to assemble
    #          batches. We translate the global i into (which chunk, which
    #          row inside that chunk), load that chunk, and slice the row.
    # Arguments:
    #           i: global example index, 0 .. total_examples-1
    # Output:  tuple (sequence, mask, context, target) for example i
    def __getitem__(self, i: int):
        # Which chunk file holds example i, and which row within it.
        # e.g. i=25_000 with chunk_size 10_000 -> chunk 2, row 5_000.
        # ! THROUGH base_index, so a window names the file it really is.
        #   Dataset index 0 of --chunk-range 3000:6000 is chunk_3000 row 0,
        #   not chunk_0000 -- which is not on disk during that shift.
        g = self.base_index + i
        chunk_idx = g // self.chunk_size   # // = integer division
        row       = g %  self.chunk_size   # %  = remainder

        chunk = self._loadChunk(chunk_idx)

        # Pull row `row` out of each of the four tensors in the chunk.
        # These line up because feature_extraction.py saved them in the
        # same order within every chunk.
        # This gives the example numer "row" from this chunk.
        #
        # chunk                     the dict for one file: 
        # {"sequences", "masks", "context", "targets"}
        #
        # chunk["sequences"]        one tensor from that dict: 
        # ALL examples' sequences, shape [N_chunk, S, 17]
        #
        # chunk["sequences"][row]   one example's sequence,                            
        # shape [S, 17]
        # ★ A RAGGED SLICE, NOT A PADDED ROW. Chunks store every sequence
        #   end to end with an offset per example, so this is a VIEW into the
        #   chunk's one big [total_steps, 17] block -- no copy, and no padding
        #   read off the disk that was only ever zeros. collateRagged pads the
        #   batch afterwards, to the batch's own longest sequence.
        #
        # ! STILL READS A PADDED CHUNK IF IT FINDS ONE. Chunks written before
        #   the ragged change carry "masks" and a [N, S, 17] block; there is
        #   no reason to make those unloadable, and the collate handles both
        #   because it only ever sees a [len, 17] sequence either way.
        if "offsets" in chunk:
            o0 = int(chunk["offsets"][row])
            o1 = int(chunk["offsets"][row + 1])
            sequence = chunk["sequences"][o0:o1]      # [len, 17]
        else:
            padded   = chunk["sequences"][row]        # [S, 17]
            keep     = int(chunk["masks"][row].sum())
            sequence = padded[:keep]                  # [len, 17]
        context  = chunk["context"][row]     # [17]
        target   = chunk["targets"][row]     # scalar
        # ★ int64, its own tensor. An embedding index is a lookup key, not a
        #   measurement -- see feature_extraction._saveChunk.
        #   .get() so a chunk written before venues existed still loads: it
        #   yields 0, the UNKNOWN_VENUE row, which is pinned to zeros.
        venue    = (chunk["venues"][row] if "venues" in chunk
                    else torch.zeros((), dtype=torch.long))

        return sequence, context, target, venue
    
# collateRagged
# Purpose: turn a list of ragged examples into one padded batch.
#
# ★ PAD TO THE BATCH, NOT TO THE CORPUS. Attention is O(L^2) and the encoder
#   runs over whatever L it is handed.
#
# ⚠ AND ON A SHUFFLED BATCH THAT BUYS ALMOST NOTHING. The batch max is the
#   ~98th percentile of example lengths, so a shuffled batch of 64 pads to
#   64.0 on average against a cap of 64 -- one long athlete sets L for
#   everyone beside them. It is length-SORTED batching that collects the
#   saving: mean padded width 18.2 instead of 64.0, and 174ms per step
#   against 591ms. This function is what makes that possible; it is not what
#   delivers it. See ChunkAwareBatchSampler.
#
# ! THE MASK IS BUILT HERE, IN THE POLARITY THE ENCODER EXPECTS: True = a
#   real step. transformer.forward inverts it into src_key_padding_mask,
#   where True means ignore. Getting this backwards trains the model on
#   nothing but padding and still runs.
#
# ⚠ A ZERO-LENGTH SEQUENCE WOULD MAKE AN ALL-PADDING ROW, and attention over
#   an entirely masked row is NaN, not zero. buildAthleteExamples starts at
#   index 1 so every example has at least one prior race, but a floor of 1
#   here means a future change upstream cannot silently poison a run.
def collateRagged(items):
    sequences, contexts, targets, venues = zip(*items)
    lengths = [max(1, s.shape[0]) for s in sequences]
    width   = sequences[0].shape[1]
    longest = max(lengths)

    padded = torch.zeros(len(sequences), longest, width, dtype=torch.float32)
    masks  = torch.zeros(len(sequences), longest, dtype=torch.bool)
    for i, seq in enumerate(sequences):
        n = seq.shape[0]
        if n:
            padded[i, :n] = seq
            masks[i, :n]  = True
        else:
            masks[i, 0] = True          # see the NaN note above

    return (padded, masks,
            torch.stack(contexts),
            torch.stack(targets),
            torch.stack(venues))


# ------------------------------------------------------------------ #
# CHUNK 2b — BATCH SAMPLER: shuffle without destroying the chunk cache
# ------------------------------------------------------------------ #
#
# ★ THE PROBLEM THIS SOLVES, AND IT IS NOT A MICRO-OPTIMISATION.
#   ChunkedRaceDataset caches ONE chunk. A plain DataLoader(shuffle=True)
#   draws random GLOBAL indices, so consecutive __getitem__ calls land in
#   different chunk files and the cache misses almost every time. Each miss
#   is a torch.load() of an entire 10,000-example file -- to return ONE row.
#   A 64-example batch reads ~64 whole chunk files. That is not slow
#   training, it is training that never finishes.
#
# ★ THE FIX KEEPS BOTH PROPERTIES. Batches are built WITHIN a chunk, so one
#   file load serves 64 examples. Randomness survives at two levels:
#     - the ORDER of chunks is shuffled every epoch
#     - the rows WITHIN each chunk are shuffled every epoch
#   What is lost is cross-chunk mixing inside a single batch. That matters
#   only if chunks are ordered by something correlated with the target --
#   and feature_extraction writes them in athlete order, so a batch is 64
#   examples from nearby athletes rather than 64 from everywhere.
#
# ⚠ SO IF ATHLETE ORDER EVER CORRELATES WITH THE TARGET (sorted by school,
#   by state, by era) this introduces batch-level bias. The cheap insurance
#   is to shuffle examples ONCE at extraction time; then chunk-local
#   batching is exactly equivalent to global shuffling.


class ChunkAwareBatchSampler:
    """
    Yields lists of indices, each list entirely inside one chunk file.

    A batch_sampler is the torch hook for "I want to choose which indices
    go together", as opposed to `sampler`, which only chooses their order.
    DataLoader calls __iter__ once per epoch, so the reshuffle below happens
    once per epoch automatically.
    """

    def __init__(self, dataset, batch_size: int, shuffle: bool):
        # A Subset (from random_split / the val mask) wraps the real dataset
        # and holds the global indices it owns. We need BOTH: the chunk_size
        # to group by, and the global index of every example this split may
        # touch.
        base = getattr(dataset, "dataset", dataset)
        self.globals_ = list(getattr(dataset, "indices",
                                     range(len(dataset))))
        self.chunk_size = base.chunk_size
        self.batch_size = batch_size
        self.shuffle = shuffle

        # ⚠ YIELD POSITIONS, NOT GLOBAL INDICES. DataLoader hands whatever a
        #   batch_sampler yields straight to dataset[i] -- and when `dataset`
        #   is a Subset, that i is a POSITION into the subset, which the
        #   Subset then maps to its global index itself. The original version
        #   yielded globals, so every Subset row was double-mapped: an
        #   IndexError past len(subset), a silently WRONG example before it.
        #   Grouping still happens by the GLOBAL index's chunk file -- that is
        #   the property that keeps one batch inside one file.
        self.by_chunk = {}
        for pos, g in enumerate(self.globals_):
            self.by_chunk.setdefault(g // self.chunk_size, []).append(pos)

        # ★ AND SORT EACH CHUNK'S INDICES BY SEQUENCE LENGTH, which is what
        #   makes per-batch padding worth anything.
        #
        # ⚠ A BATCH PADS TO ITS LONGEST MEMBER, so one 300-race athlete drags
        #   63 three-race athletes up to L=300 with them. Drawn at random, the
        #   longest of 64 examples is about the 98th percentile of all
        #   lengths, so 95% of shuffled batches pad to the cap and the ragged
        #   storage buys nothing at training time. Sorting first puts long
        #   with long and short with short:
        #
        #       shuffled batch of 64  -> 64.0 wide     591 ms/step
        #       length-sorted         -> 18.2 wide     174 ms/step
        #
        # ! THE RANDOMNESS MOVES UP A LEVEL, IT IS NOT LOST. Chunks are
        #   already shuffled corpus-wide when written, so each is a random
        #   sample; sorting inside one groups similar lengths drawn from that
        #   random sample, and __iter__ then shuffles the BATCH order every
        #   epoch. What an epoch loses is the freedom to put a 3-race and a
        #   300-race athlete in the same batch -- which is the thing that was
        #   costing 3.4x.
        self.bucketed = False
        lengths = getattr(base, "lengths", None)
        if shuffle and lengths is not None:
            # rows are subset POSITIONS; the length lives at the GLOBAL index.
            for cid, rows in self.by_chunk.items():
                rows.sort(key=lambda pos: int(lengths[self.globals_[pos]]))
            self.bucketed = True

    def __iter__(self):
        import random
        chunk_ids = list(self.by_chunk)
        if self.shuffle:
            random.shuffle(chunk_ids)
        # ! BATCHES ARE BUILT FIRST, THEN THEIR ORDER IS SHUFFLED. Shuffling
        #   the rows instead would undo the length sort the constructor did.
        #   When there are no lengths to sort on, this is the old behaviour
        #   exactly: shuffle rows, cut into batches.
        batches = []
        for cid in chunk_ids:
            rows = list(self.by_chunk[cid])
            if self.shuffle and not self.bucketed:
                random.shuffle(rows)
            for start in range(0, len(rows), self.batch_size):
                batches.append(rows[start:start + self.batch_size])
        if self.shuffle and self.bucketed:
            random.shuffle(batches)
        yield from batches

    def __len__(self) -> int:
        # Number of BATCHES, not examples -- DataLoader reports this as
        # len(loader), and a wrong value here silently truncates an epoch.
        return sum((len(v) + self.batch_size - 1) // self.batch_size
                   for v in self.by_chunk.values())


# buildDataLoader
# Purpose: Wrap a Dataset in a DataLoader, which batches examples,
#          optionally shuffles them, and yields ready-to-use batches.
# Arguments:
#           dataset: a ChunkedRaceDataset (or a Subset of one)
#           shuffle: True for training (see each epoch in a new order
#                    so the model can't memorise sequence), False for
#                    validation (order doesn't matter there)
# Output:  a DataLoader yielding (sequences, masks, context, targets)
#          batches, each tensor with a leading batch dimension B
def buildDataLoader(dataset, shuffle: bool) -> DataLoader:
    return DataLoader(
        dataset,
        # ★ batch_sampler REPLACES batch_size + shuffle. Passing all three
        #   is an error in torch, so they are gone from this call.
        batch_sampler=ChunkAwareBatchSampler(dataset, BATCH_SIZE, shuffle),
        # ★ THE READING HAPPENS IN OTHER PROCESSES. __getitem__ torch.loads a
        #   chunk from disk, so with 0 workers the training process stops
        #   dead before every batch and the GPU idles through the read. This
        #   is the single biggest lever on wall clock for this job -- the
        #   model is small enough that the input pipeline, not the maths, is
        #   what the run is made of.
        num_workers=NUM_WORKERS,
        # Page-locked host memory, so the host->device copy can overlap
        # compute instead of blocking on it. Pointless without a GPU.
        pin_memory=(NUM_WORKERS > 0 and DEVICE.type == "cuda"),
        # ! WORKERS ARE KEPT ALIVE BETWEEN EPOCHS. Otherwise every epoch pays
        #   to fork them again and refill their one-chunk caches from cold.
        persistent_workers=(NUM_WORKERS > 0),
        # Each worker reads this many batches ahead of what is being asked
        # for, which is what actually hides the disk latency.
        prefetch_factor=(4 if NUM_WORKERS > 0 else None),
        # ! REQUIRED, NOT OPTIONAL. The dataset now yields ragged
        #   sequences; default_collate would try to stack rows of
        #   different lengths and raise. collateRagged pads to the
        #   batch's own longest and builds the mask.
        collate_fn=collateRagged,
        # DataLoader auto-STACKS the per-example tensors __getitem__
        # returns: 64 sequences of [S,17] become one [64, S, 17], 64
        # masks become [64, S], etc. That leading 64 is the B in every
        # [B, S, 256] shape we traced through the model.
    )


# ------------------------------------------------------------------ #
# CHUNK 3 — TARGET Z-SCORING: put the targets on a clean scale
# ------------------------------------------------------------------ #
#
# feature_extraction.py saved the RAW normalized_time as each example's
# target. Neural nets train better when the target sits near 0 with a
# spread near 1, so before the loss we z-score it:
#
#     z = (target - mean) / std
#
# This chunk only writes the TOOLS for that. It does NOT run them — the
# wiring happens in chunk 4, after the train/val split exists, because
# the mean and std must come from the TRAINING targets ONLY. If
# validation targets leaked into those two numbers, val loss would
# secretly know something about the held-out data and stop being an
# honest "unseen data" score.
#
# ★ THE TARGET IS ln(target / the athlete's last race), z-scored. The
#   stats that define it -- and the per-feature input stats -- are computed
#   once from the TRAIN side of a chunk prefix, saved to target_stats.pkl,
#   and stamped into the model's buffers so model.pt carries them. Inference
#   inverts with model.predictSeconds, never by hand. See transformer.py.
#
# The helpers, one idea each:
#   _trainSideMask     — which global indices are on the TRAIN side
#   _chunkBaselines    — the last-race baseline, the model's own rule
#   computeStats       — input mean/std and log-ratio mean/std, one pass
#   saveTargetStats    — persist them (both key spellings, plus `kind`)


def _trainSideMask(dataset, train_subset) -> torch.Tensor:
    """bool [len(dataset)]: True where the example is on the TRAIN side."""
    m = torch.zeros(len(dataset), dtype=torch.bool)
    idx = getattr(train_subset, "indices", None)
    if idx is None:
        m[:] = True
    else:
        m[torch.as_tensor(list(idx), dtype=torch.long)] = True
    return m


def _chunkBaselines(chunk, mode=None, half_life=None):
    """(baseline seconds [N], real sequence rows [R, F], lengths [N]).

    ★ THE SAME RULE AS XCPredictor.baselineSeconds, and the same weights: the
      ragged layout here and the padded one there both call
      transformer.baselineWeights, so the rule has one definition and two
      reductions. tests/test_baseline_rule.py builds the same history in both
      shapes and asserts the answers match.

    ⚠ AND IT CANNOT READ THE RULE OFF THE MODEL, because this runs BEFORE the
      model exists -- computeStats needs ln(target/baseline) to set the target
      stats the model is then built with. So the caller passes it, and train()
      passes the same values to model.setBaselineRule.
    """
    mode = BASELINE if mode is None else mode
    half_life = BASELINE_HALF_LIFE if half_life is None else half_life
    seqs = chunk["sequences"].to(torch.float32)
    if "offsets" in chunk:
        off = chunk["offsets"].to(torch.long)
        lengths = off[1:] - off[:-1]
        if float(mode) == BASELINE_BEST2:
            base = _best2Ragged(seqs, lengths)
        elif float(mode) == BASELINE_EWMA:
            base = _ewmaRagged(seqs, lengths, half_life)
        else:
            has = lengths > 0
            base = torch.zeros(lengths.shape[0])
            base[has] = seqs[off[1:][has] - 1, SEQ_NORM_TIME]
        return base, seqs, lengths
    masks = chunk["masks"].to(torch.bool)
    lengths = masks.sum(dim=1)
    n = seqs.shape[0]
    # ! THE MODEL'S OWN PADDED REDUCTIONS, imported rather than repeated.
    if float(mode) == BASELINE_BEST2:
        base = best2FromPadded(seqs, masks)
    elif float(mode) == BASELINE_EWMA:
        base = ewmaFromPadded(seqs, masks, half_life)
    else:
        last = (lengths - 1).clamp(min=0)
        base = seqs[torch.arange(n), last, SEQ_NORM_TIME]
        base[lengths == 0] = 0.0
    return base, seqs[masks], lengths


# Purpose:   the best-of-two baseline over the RAGGED chunk layout.
#
# ⚠ THERE IS NO PER-SEGMENT top-k, so this finds the smallest, then the
#   smallest EXCLUDING THAT ONE ROW. Excluding "every row equal to the
#   minimum" would be wrong: two rows tied at the same time are two real
#   races and both should count, and masking on equality would delete both.
#   So the row is excluded by INDEX -- the lowest index achieving the
#   minimum, chosen the same way whatever the ties.
#
# ! AND THE WINDOW WIDENS RATHER THAN EMPTIES, matching _best2Baseline: an
#   athlete whose races are all older than the window keeps them.
def _best2Ragged(seqs, lengths):
    n = lengths.shape[0]
    r = seqs.shape[0]
    t = seqs[:, SEQ_NORM_TIME]
    d = seqs[:, SEQ_DAYS_AGO]
    seg = torch.repeat_interleave(torch.arange(n), lengths)
    valid = t > 0
    inwin = valid & (d <= BASELINE_BEST_WINDOW_DAYS)
    # does this athlete have anything inside the window?
    any_win = torch.zeros(n).index_add_(
        0, seg, inwin.to(torch.float32)) > 0
    use = torch.where(any_win[seg], inwin, valid)

    inf = float("inf")
    lt = torch.log(t.clamp(min=1.0))
    x = torch.where(use, lt, torch.full_like(lt, inf))
    m1 = torch.full((n,), inf).scatter_reduce(0, seg, x, "amin",
                                              include_self=True)
    # the LOWEST-INDEXED row achieving that minimum, so a tie removes one
    rows = torch.arange(r, dtype=torch.float32)
    cand = torch.where(x == m1[seg], rows, torch.full_like(rows, float(r)))
    argm = torch.full((n,), float(r)).scatter_reduce(0, seg, cand, "amin",
                                                     include_self=True)
    x2 = x.clone()
    hit = argm[argm < r].to(torch.long)
    if hit.numel():
        x2[hit] = inf
    m2 = torch.full((n,), inf).scatter_reduce(0, seg, x2, "amin",
                                              include_self=True)

    stack = torch.stack([m1, m2], dim=1)
    finite = torch.isfinite(stack)
    num = torch.where(finite, stack, torch.zeros_like(stack)).sum(dim=1)
    den = finite.sum(dim=1).to(torch.float32)
    out = torch.exp(num / den.clamp(min=1.0))
    return torch.where(den > 0, out, torch.zeros(n))


# Purpose:   the EWMA baseline over the RAGGED chunk layout.
# ! A SEGMENT REDUCTION, because the rows of every example are concatenated
#   into one [R, F] tensor with an offsets index. index_add_ over a segment
#   id is the portable way to do it -- no torch version gates, no padding the
#   whole chunk back out to a rectangle it was flattened to avoid.
def _ewmaRagged(seqs, lengths, half_life):
    n = lengths.shape[0]
    t = seqs[:, SEQ_NORM_TIME]
    d = seqs[:, SEQ_DAYS_AGO]
    w = baselineWeights(d, t > 0, half_life)
    seg = torch.repeat_interleave(torch.arange(n), lengths)
    den = torch.zeros(n).index_add_(0, seg, w)
    num = torch.zeros(n).index_add_(0, seg,
                                    w * torch.log(t.clamp(min=1.0)))
    return torch.where(den > 0, torch.exp(num / den.clamp(min=1e-12)),
                       torch.zeros(n))


def computeStats(dataset, is_train: torch.Tensor,
                 max_chunks: int = STATS_CHUNKS) -> dict:
    """Per-feature mean/std of the inputs, and mean/std of the log-ratio
    target, over TRAIN-side examples in the first max_chunks chunks.

    Output: {"seq_mean","seq_std","ctx_mean","ctx_std": tensors;
             "mean","std": floats for ln(target/baseline);
             "fallback_seconds": float, mean raw target;
             "n_examples","n_rows","n_chunks": ints}
    """
    n_chunks = min(dataset.num_chunks, max_chunks)
    f_seq = SEQUENCE_FEATURES
    seq_sum = torch.zeros(f_seq, dtype=torch.float64)
    seq_sq = torch.zeros(f_seq, dtype=torch.float64)
    seq_n = 0
    ctx_sum = ctx_sq = None
    ctx_n = 0
    lr_sum = lr_sq = 0.0
    lr_n = 0
    raw_sum = 0.0
    raw_n = 0
    max_year = 0.0

    for c in range(n_chunks):
        chunk = dataset._loadChunk(c)
        targets = chunk["targets"].to(torch.float32)
        n = targets.shape[0]
        g0 = c * dataset.chunk_size
        keep = is_train[g0:g0 + n]
        if keep.shape[0] < n:                      # a short mask (MAX_CHUNKS)
            keep = torch.cat([keep, torch.zeros(n - keep.shape[0],
                                                dtype=torch.bool)])
        base, rows, lengths = _chunkBaselines(chunk)

        # sequence rows belonging to kept examples
        row_keep = torch.repeat_interleave(keep, lengths)
        kept_rows = rows[row_keep].to(torch.float64)
        seq_sum += kept_rows.sum(dim=0)
        seq_sq += (kept_rows * kept_rows).sum(dim=0)
        seq_n += kept_rows.shape[0]

        ctx = chunk["context"].to(torch.float64)[keep]
        if ctx_sum is None:
            ctx_sum = torch.zeros(ctx.shape[1], dtype=torch.float64)
            ctx_sq = torch.zeros(ctx.shape[1], dtype=torch.float64)
        ctx_sum += ctx.sum(dim=0)
        ctx_sq += (ctx * ctx).sum(dim=0)
        ctx_n += ctx.shape[0]
        # ★ THE NEWEST YEAR THESE WEIGHTS EVER SAW, carried into
        #   target_stats.pkl so inference can clamp to it. The year feature
        #   is the only one whose value at inference can sit outside the
        #   training range -- a race next spring is later than every row
        #   here by definition -- and a linear layer on a z-scored year
        #   extrapolates whatever trend it found. Guarded on the width so a
        #   chunk set written before the feature existed still trains.
        if ctx.shape[1] > CONTEXT_YEAR_INDEX:
            yr = float(ctx[:, CONTEXT_YEAR_INDEX].max()) if ctx.shape[0] else 0.0
            max_year = max(max_year, yr)

        t = targets[keep]
        b = base[keep]
        raw_sum += float(t.sum())
        raw_n += int(t.shape[0])
        ok = (b > 0) & (t > 0)
        lr = torch.log(t[ok] / b[ok]).to(torch.float64)
        lr_sum += float(lr.sum())
        lr_sq += float((lr * lr).sum())
        lr_n += int(lr.shape[0])

    def _ms(s, sq, n):
        n = max(n, 1)
        mean = s / n
        var = torch.clamp(sq / n - mean * mean, min=0.0)
        return mean.to(torch.float32), torch.sqrt(var).to(torch.float32)

    seq_mean, seq_std = _ms(seq_sum, seq_sq, seq_n)
    ctx_mean, ctx_std = _ms(ctx_sum, ctx_sq, ctx_n)
    lr_mean = lr_sum / max(lr_n, 1)
    lr_std = max((lr_sq / max(lr_n, 1) - lr_mean * lr_mean), 0.0) ** 0.5
    return {"seq_mean": seq_mean, "seq_std": seq_std,
            "ctx_mean": ctx_mean, "ctx_std": ctx_std,
            "mean": float(lr_mean), "std": float(max(lr_std, 1e-6)),
            "fallback_seconds": raw_sum / max(raw_n, 1),
            # 0.0 means "no year feature in these chunks"; predict.py treats
            # a falsy ceiling as no clamp, which is the old behaviour.
            "max_year": max_year,
            "n_examples": lr_n, "n_rows": seq_n, "n_chunks": n_chunks}


def zScore(values: torch.Tensor, mean: float, std: float) -> torch.Tensor:
    """(value - mean) / std. Kept for callers that z-score by hand."""
    return (values - mean) / std


# _statsFromModel
# Purpose: the stats a trained model is ACTUALLY using, read back off its
#          buffers, in the shape saveTargetStats writes.
# Detail:
#   ! THE BUFFERS ARE THE TRUTH. They are what normalises an input and
#     inverts an output inside the network; target_stats.pkl only exists so
#     inference can do the same arithmetic without loading the model. When
#     the two can disagree -- a resume onto different chunks -- the file is
#     what gets corrected, never the model.
#   `fallback` supplies the fields the buffers do not carry (counts, and
#   max_year when an older checkpoint has none).
def _statsFromModel(model, fallback: dict) -> dict:
    out = dict(fallback)
    out["mean"] = float(model.target_mean)
    out["std"] = float(model.target_std)
    out["fallback_seconds"] = float(model.fallback_seconds)
    out["seq_mean"] = model.seq_mean.detach().cpu()
    out["seq_std"] = model.seq_std.detach().cpu()
    out["ctx_mean"] = model.ctx_mean.detach().cpu()
    out["ctx_std"] = model.ctx_std.detach().cpu()
    return out


def saveTargetStats(stats: dict, path: str) -> None:
    """Persist the target stats beside model.pt.

    ★ BOTH KEY SPELLINGS. This used to write target_mean/target_std while
      racecast/predict.py and predict_check.py read mean/std -- a KeyError
      waiting for the first trained model. `kind` says what the numbers
      describe, so an inference path can refuse a model it does not
      understand instead of inverting it wrongly.
    """
    doc = {"kind": "log_ratio",
           "mean": float(stats["mean"]), "std": float(stats["std"]),
           "target_mean": float(stats["mean"]),
           "target_std": float(stats["std"]),
           "fallback_seconds": float(stats["fallback_seconds"]),
           "seq_mean": stats["seq_mean"].tolist(),
           "seq_std": stats["seq_std"].tolist(),
           "ctx_mean": stats["ctx_mean"].tolist(),
           "ctx_std": stats["ctx_std"].tolist(),
           "max_year": float(stats.get("max_year") or 0.0),
           "n_examples": int(stats["n_examples"]),
           "n_chunks": int(stats["n_chunks"])}
    with open(path, "wb") as f:
        pickle.dump(doc, f)


# ------------------------------------------------------------------ #
# CHUNK 4 — SETUP: split the data, build loaders, model, optimizer, loss
# ------------------------------------------------------------------ #
#
# Everything the training loop (chunk 5) needs, assembled in one place:
#   - split the full dataset into train / val (seeded, reproducible)
#   - compute + save the target stats from the TRAIN split only (chunk 3)
#   - wrap each split in a DataLoader
#   - build the model on the device, the optimizer, and the loss
#
# Order matters: the split comes FIRST, then the stats are computed from
# the training indices. Reversing that reintroduces the leakage chunk 3
# exists to prevent.


# splitTrainVal
# Purpose: Randomly divide the full dataset into a training subset and a
#          validation subset, reproducibly (same split every run).
# Arguments:
#           dataset: the full ChunkedRaceDataset
# Output:  (train_subset, val_subset) — two torch Subsets
def splitTrainVal(dataset):

    # ★ THE ATHLETE-DISJOINT SPLIT, WHEN THE CHUNKS CARRY ONE. random_split
    #   over examples puts the same athlete -- and forecast twins of the very
    #   same target -- on both sides, so val loss rewards memorising athletes
    #   and save-on-best keeps an overfit model on purpose. Extraction now
    #   hashes each ATHLETE to a side and writes val_mask.pt in global
    #   example order; splitting on it makes validation actual unseen people.
    vm_path = os.path.join(DATA_DIR, "val_mask.pt")
    if os.path.exists(vm_path):
        # ⚠ SLICED AT THE WINDOW, NOT FROM ZERO. val_mask.pt is written in
        #   GLOBAL example order; a window starting at chunk 3000 that read
        #   from index 0 would hand every example somebody else's verdict,
        #   putting held-out athletes into training and calling the result a
        #   validation score. base is 0 for a prefix, so this is the old
        #   behaviour exactly whenever there is no window.
        base = getattr(dataset, "base_index", 0)
        mask = torch.load(vm_path)[base:base + len(dataset)]
        val_idx   = mask.nonzero(as_tuple=True)[0].tolist()
        train_idx = (~mask).nonzero(as_tuple=True)[0].tolist()
        print(f"  athlete-disjoint split: {len(train_idx):,} train / "
              f"{len(val_idx):,} val (val_mask.pt)")
        return Subset(dataset, train_idx), Subset(dataset, val_idx)

    # Fallback for chunk sets written before the mask existed. Known to leak
    # athletes across the split -- retire it by re-running extraction.
    print("  val_mask.pt not found -- falling back to example-level "
          "random_split (athlete leakage; re-run extraction to fix)")

    # Calculates the amount of training and validation examples
    n_total = len(dataset)
    n_val = int(n_total * VAL_FRACTION)
    n_train = n_total - n_val

    # A generator seeded with SEED makes the random split deterministic:
    # same SEED → identical train/val partition on every run, so results
    # are comparable across runs. Still randomly chooses which are
    # training and which are validation, just stores how it 
    # randomly did that.
    generator = torch.Generator().manual_seed(SEED)

    # random_split shuffles 0..n_total-1 and slices them into two Subsets
    # of the requested sizes. A Subset just remembers its parent dataset
    # + its list of indices; it doesn't copy any data.
    train_subset, val_subset = random_split(
        dataset,
        [n_train, n_val],
        generator=generator,
    )
    return train_subset, val_subset


# ------------------------------------------------------------------ #
# CHUNK 5 — THE TRAIN / VALIDATE LOOP: where learning happens
# ------------------------------------------------------------------ #
#
# One epoch = one full pass over the training data, then one full pass
# over the validation data. Per training batch the cycle is:
#   move to device → forward → loss → zero_grad → backward → step
# The four-line core (zero_grad/backward/step + the forward) is the
# entire act of learning; everything else is bookkeeping.
#
# Validation runs the SAME forward + loss but NO backward/step — it only
# measures. We wrap it in model.eval() (dropout off) + torch.no_grad()
# (skip gradient tracking) so the measurement is clean and cheap.
#
# Watch the two losses: train loss falling while val loss flattens or
# rises = the model memorising rather than generalising (overfitting),
# which is the signal to stop.
#
# ================================================================== #
# HOW ONE BATCH TRAINS — read this once, the rest of the loop is repeats
# ================================================================== #
#
# Everything in this file (Dataset, loaders, z-scoring, the epoch loop)
# exists to feed FOUR lines. This is the whole act of learning:
#
#     optimizer.zero_grad()                      # 1. clear old gradients
#     preds = model(sequences, masks, context)   # 2. forward: guess
#     loss  = criterion(preds, targets)          # 3. measure how wrong
#     loss.backward()                            # 4a. compute gradients
#     optimizer.step()                           # 4b. apply the update
#
# As a story, for ONE batch of 64 athletes:
#
#   1. zero_grad — wipe the slate.
#      Gradients ACCUMULATE in PyTorch: backward ADDS to whatever is
#      already stored on each weight. So we must clear last batch's
#      gradients first, or this update fires on a stale pile-up.
#
#   2. forward — the guess.
#      model(...) runs the whole network (project 17->256 -> attention
#      encoder -> mean-pool -> concat context -> head). Out: preds, [B],
#      one predicted z-scored normalized_time per athlete. While it runs,
#      autograd silently RECORDS every operation onto a tape. That tape
#      is what makes step 4a possible.
#
#   3. loss — the measure.
#      criterion compares 64 guesses to 64 true targets. MSE = mean of
#      (pred - target)^2: square kills the +/- sign and punishes big
#      misses harder than small ones. Collapses to ONE number = how
#      wrong this batch was. The single quantity the loop drives down.
#
#   4a. backward — compute the gradients.
#      Autograd replays its tape IN REVERSE (chain rule) and, for every
#      weight, works out "nudge you which way to shrink loss?" That
#      answer is the weight's gradient, stored ON the weight. backward
#      COMPUTES gradients but MOVES NOTHING yet.
#
#   4b. step — apply the update (the learning).
#      Adam reads those gradients and actually nudges each weight a small
#      step toward lower loss. The ONLY line where weights change. After
#      it, the model is a hair better than one batch ago.
#
# Division of labour worth locking in:
#   - We only ever wrote forward() (in transformer.py).
#   - loss.backward() derives ALL gradients from the tape forward left.
#   - optimizer.step() applies them.
#   That's why transformer.py has zero gradient code: autograd + the
#   optimizer own the entire backward half, for free.
#
# Validation (_validateOneEpoch) is this SAME cycle MINUS 1, 4a, 4b:
# it guesses and measures but never learns. Two guards make that clean
# and cheap:
#   - model.eval()         -> dropout OFF (deterministic full network;
#                             train() leaves dropout ON to regularise).
#   - with torch.no_grad() -> don't record the tape at all (no backward
#                             is coming, so skip the bookkeeping: faster,
#                             less memory).
#   NOTE these two are DIFFERENT: eval() changes what the LAYERS do;
#   no_grad() changes whether the MATH is recorded. Validation wants both.
#
# total_loss / n_batches: loss is already a mean WITHIN a batch (MSE
# averages over the 64). We tally those per-batch means and divide by
# the batch count to get ONE average per epoch. Averaged twice. We add
# loss.item() (the plain float), NOT loss (the tensor) — accumulating
# the tensor would keep its whole autograd tape alive in memory (a leak).
# The number is in z-units, not seconds: a relative quality signal, watch
# the trend, lower = better.
# ================================================================== #


# _trainOneEpoch
# Purpose: Run one full pass over the training data: for each batch do
#          forward → loss → backward → optimizer step, and learn from it.
# Arguments:
#           model:     the XCPredictor (weights live on DEVICE)
#           loader:    train_loader, yielding batches of real examples
#           optimizer: AdamW, applies the weight updates
#           criterion: HuberLoss on the z-scored log ratio
#           scheduler: the warmup/cosine LR schedule, stepped per batch
# Output:  (average training loss, throughput dict)
def _trainOneEpoch(model, loader, optimizer, criterion, scheduler=None):

    # Flips model into training mode, dropout on.
    model.train()
    total_loss = 0.0 # A running tally of each batch's loss (Huber, z units).
    n_batches = 0
    n_clamped = 0    # examples whose target hit the +-TARGET_Z_CLAMP bound
    n_bad = 0        # batches dropped for a non-finite loss

    # ★ THROUGHPUT, MEASURED RATHER THAN GUESSED. `waiting` is the share of
    #   wall clock spent BLOCKED ON THE LOADER rather than computing: if it
    #   is high, a faster GPU buys nothing and the fix is workers or batch.
    t_start = time.time()
    t_wait = 0.0
    n_examples = 0
    _t_batch = time.time()

    for sequences, masks, context, targets, venues in loader:
        t_wait += time.time() - _t_batch

        sequences = sequences.to(DEVICE)
        masks     = masks.to(DEVICE)
        context   = context.to(DEVICE)
        targets   = targets.to(DEVICE)
        venues    = venues.to(DEVICE)

        # ★ THE TARGET THE MODEL DEFINES. z-scored ln(target / last race),
        #   computed by the model from the same tensors it predicts from, so
        #   training and inference cannot disagree about the baseline.
        z_true = model.targetZ(sequences, masks, targets)
        # ★ HOW OFTEN THE CLAMP BITES, SAID OUT LOUD. targetZ bounds the
        #   target at +-TARGET_Z_CLAMP because one corrupt row can end a
        #   run (see the constant). A clamp that fires on a handful of
        #   examples an epoch is doing exactly its job; one firing on a
        #   PERCENT of them means the stats are wrong or the extraction is,
        #   and that is a thing to find out from the log rather than from a
        #   model that trained quietly and predicts badly.
        n_clamped += int((z_true.abs() >= TARGET_Z_CLAMP - 1e-4).sum())

        optimizer.zero_grad()                      # 1. clear old gradients
        with _autocast():
            mu, logvar = model.forwardDist(sequences, masks, context,
                                           venues)             # 2. forward
        # The likelihood in fp32: exp(logvar) under bf16 is too coarse.
        loss = criterion(mu.to(torch.float32), z_true,
                         torch.exp(logvar.to(torch.float32)) + VAR_EPS)

        # ⚠ A NON-FINITE LOSS IS NOT A SMALL PROBLEM TO AVERAGE AWAY. One
        #   inf or nan backpropagates into EVERY weight, and from then on
        #   the whole model is nan -- every later epoch reads `train nan`
        #   and the checkpoint written at the end of it is worthless. The
        #   clamp above should mean this never fires; if it does, the batch
        #   is dropped and counted rather than allowed to end the run.
        if not torch.isfinite(loss):
            n_bad += 1
            optimizer.zero_grad(set_to_none=True)
            if scheduler is not None:
                scheduler.step()
            continue

        loss.backward()                            # 4a. compute gradients
        torch.nn.utils.clip_grad_norm_(model.parameters(), GRAD_CLIP)
        optimizer.step()                           # 4b. apply the update
        if scheduler is not None:
            scheduler.step()                       # 4c. move the LR along

        total_loss += loss.item()   # .item() = pull the float out
        n_batches  += 1
        n_examples += targets.size(0)
        _t_batch = time.time()

    elapsed = max(time.time() - t_start, 1e-9)
    stats = {"examples_per_s": n_examples / elapsed,
             "steps_per_s": n_batches / elapsed,
             "clamped": n_clamped,
             "bad_batches": n_bad,
             "waiting": t_wait / elapsed,
             "elapsed": elapsed}
    return total_loss / max(n_batches, 1), stats

# How close two athletes' baselines must be, in log time, to count as a
# "close pair". 0.02 is 2% -- about 18 seconds on a 15:00 runner, which is
# two teammates rather than two strangers.
CLOSE_PAIR_LN = 0.02


# Purpose:   how often the model orders two SIMILAR athletes correctly, and
#            how often its own baseline does.
# Output:    (model correct, baseline correct, pairs counted).
#
# ⚠ BECAUSE MAE CANNOT SEE THE FAULT THE OWNER IS REPORTING. Measured on a
#   real 152-athlete championship (2026-09-17), within teammate pairs:
#
#       the model            20.7% inverted
#       its baseline alone   20.0%
#       season mean rating   19.1%
#
#   The model is WORSE THAN DOING NOTHING on close pairs -- its correction is
#   noise at that resolution -- and every number train.py printed said the
#   run was fine. "it has me losing to my teamate who I beat in every single
#   race that season" is this, and it has to be visible per epoch or a
#   retrain cannot be judged on it.
#
# ★ PAIRED ON THE BASELINE, NOT ON THE TRUTH. Pairing on what actually
#   happened would select for pairs the model was always going to find hard;
#   pairing on the anchor asks the question the race asks -- two athletes who
#   LOOK the same beforehand, which one wins.
#
# ! SORTED AND ADJACENT, so every example is used once and no RNG enters the
#   validation metric. The gate then keeps only genuinely close neighbours.
def _closePairOrder(base, lr_pred, lr_true):
    lb = torch.log(base.to(torch.float32).clamp(min=1.0))
    order = torch.argsort(lb)
    lb = lb[order]
    # absolute predicted and true log-times: the anchor plus the ratio
    pred = lb + lr_pred.to(torch.float32)[order]
    true = lb + lr_true.to(torch.float32)[order]
    a, b = slice(0, -1), slice(1, None)
    if lb.numel() < 2:
        return 0, 0, 0
    close = (lb[b] - lb[a]).abs() <= CLOSE_PAIR_LN
    d_true = true[b] - true[a]
    close &= d_true != 0                       # a tie orders nobody
    if not bool(close.any()):
        return 0, 0, 0
    s_true = torch.sign(d_true[close])
    s_pred = torch.sign(pred[b][close] - pred[a][close])
    s_base = torch.sign(lb[b][close] - lb[a][close])
    return (int((s_pred == s_true).sum()),
            int((s_base == s_true).sum()),
            int(close.sum()))


# _validateOneEpoch
# Purpose: Run one full pass over the validation data, measuring loss
#          WITHOUT learning — no backward, no optimizer step. Tells us
#          whether the model generalises to data it didn't train on.
# Arguments:
#           model:     the XCPredictor
#           loader:    val_loader
#           criterion: the same HuberLoss as training
# Output:  (val loss, model error %, last-race error %)
def _validateOneEpoch(model, loader, criterion):
    """(val loss, model error %, last-race error %).

    ★ THE TWO PERCENTAGES ARE THE HEADLINE, NOT THE LOSS. A loss in z units
      says nothing on its own. The model's mean absolute error in log units
      (about a percent of the time) is printed beside the error of the
      dumbest possible forecast -- "you will run what you ran last time",
      which in log-ratio space is predicting zero. If the model is not
      clearly under that number, the transformer has not earned its cost.
    """
    model.eval()
    total_loss = 0.0
    n_batches  = 0
    err_model = 0.0
    err_base = 0.0
    sq_err = 0.0
    sq_sig = 0.0
    inside = 0
    n_ex = 0
    band_err = [0.0] * len(GAP_BANDS)   # sum |error| per horizon band
    band_base = [0.0] * len(GAP_BANDS)  # and the last-race baseline's
    band_n = [0] * len(GAP_BANDS)
    pair_model = pair_base = pair_n = 0

    with torch.no_grad():

        for sequences, masks, context, targets, venues in loader:

            sequences = sequences.to(DEVICE)
            masks     = masks.to(DEVICE)
            context   = context.to(DEVICE)
            targets   = targets.to(DEVICE)
            venues    = venues.to(DEVICE)

            z_true = model.targetZ(sequences, masks, targets)

            # Same precision as training, so val loss is comparable to
            # train loss rather than measured on a different arithmetic.
            with _autocast():
                mu, logvar = model.forwardDist(sequences, masks, context,
                                               venues)
            mu = mu.to(torch.float32)
            var = torch.exp(logvar.to(torch.float32)) + VAR_EPS
            loss = criterion(mu, z_true, var)

            std = float(model.target_std)
            mean = float(model.target_mean)
            lr_true = z_true * std + mean                 # ln(t / last race)
            lr_pred = mu * std + mean
            err = lr_pred - lr_true
            sig = torch.sqrt(var) * std                   # predicted sigma
            err_model += float(err.abs().sum())
            err_base += float(lr_true.abs().sum())
            # ★ CALIBRATION. If the variance head is honest, the RMS error
            #   equals the RMS predicted sigma and ~68% of truths fall inside
            #   one predicted sigma. Both are printed per epoch.
            sq_err += float((err * err).sum())
            sq_sig += float((sig * sig).sum())
            inside += int((err.abs() <= sig).sum())
            n_ex += int(targets.shape[0])

            # ! THE BAND COMES OFF THE RAW CONTEXT, not off a normalised
            #   copy. setFeatureStats normalises inside forward(); the
            #   tensor here is what extraction wrote, so this is days.
            # ★ CAN IT ORDER TWO SIMILAR ATHLETES? See _closePairOrder.
            pm, pb, pn = _closePairOrder(
                model.baselineSeconds(sequences, masks), lr_pred, lr_true)
            pair_model += pm
            pair_base += pb
            pair_n += pn

            gap = context[:, CONTEXT_GAP_INDEX]
            for b, (lo, hi, _lab) in enumerate(GAP_BANDS):
                m = (gap >= lo) & (gap < hi)
                if not bool(m.any()):
                    continue
                band_err[b] += float(err[m].abs().sum())
                band_base[b] += float(lr_true[m].abs().sum())
                band_n[b] += int(m.sum())

            total_loss += loss.item()
            n_batches  += 1

    n_ex = max(n_ex, 1)
    bands = [{"label": lab, "n": band_n[b],
              "model_pct": 100.0 * band_err[b] / max(band_n[b], 1),
              "base_pct": 100.0 * band_base[b] / max(band_n[b], 1)}
             for b, (_lo, _hi, lab) in enumerate(GAP_BANDS) if band_n[b]]
    return (total_loss / max(n_batches, 1),
            100.0 * err_model / n_ex, 100.0 * err_base / n_ex,
            {"rmse_pct": 100.0 * (sq_err / n_ex) ** 0.5,
             "sigma_pct": 100.0 * (sq_sig / n_ex) ** 0.5,
             "inside_1s": 100.0 * inside / n_ex,
             "bands": bands,
             # ordering on similar athletes -- see _closePairOrder
             "pair_n": pair_n,
             "pair_model": 100.0 * pair_model / max(pair_n, 1),
             "pair_base": 100.0 * pair_base / max(pair_n, 1)})


# ------------------------------------------------------------------ #
# CHUNK 6 — SAVE + MAIN: run the epochs, keep the best model
# ------------------------------------------------------------------ #
#
# main() ties everything together: build (chunk 4) -> loop the epochs,
# calling _trainOneEpoch + _validateOneEpoch each pass (chunk 5) ->
# save the weights whenever validation improves.
#
# We save on the BEST val loss, not just the last epoch: train loss
# falls forever, but val loss bottoms out and then climbs as the model
# starts memorising (overfitting). The model at that bottom is the one
# that generalises best — so we keep it and let later, worse epochs run
# without overwriting it.


# _saveModel
# Purpose: Write the model's learned weights to disk so a later run (or
#          Flask at inference) can load them without retraining.
# Arguments:
#           model: the trained XCPredictor
#           path:  where to write (MODEL_OUT)
# Output:  none (writes a file)
def _saveModel(model, path: str) -> None:
    # state_dict() = just the WEIGHTS (a dict of every layer's tensors),
    # NOT the code. You rebuild XCPredictor() from transformer.py, then
    # pour these numbers back in with load_state_dict(). Saving weights
    # not the object is the standard, portable way to persist a model.
    torch.save(model.state_dict(), path)


# _saveCheckpoint / _loadCheckpoint
# Purpose:  let a killed run continue instead of starting over.
# Detail:
#   ★ MODEL_OUT IS NOT A CHECKPOINT, and treating it as one loses the run.
#     It holds the WEIGHTS of the best epoch and nothing else: no optimizer
#     state, no epoch number, no best-so-far. Adam's moments -- the running
#     gradient mean and variance that make it Adam rather than SGD -- are
#     rebuilt from zero if you reload weights alone, so "resuming" from
#     MODEL_OUT silently restarts the optimizer and undoes part of the
#     training you paid for.
#
#   ⚠ THIS IS WHAT MAKES SPOT INSTANCES USABLE, which is the whole point.
#     Interruptible capacity is the cheap capacity, and it is worth nothing
#     to a run that cannot survive being interrupted. Written EVERY epoch,
#     not only on an improvement -- an epoch that got worse is still an
#     epoch you do not want to pay for twice.
#
#   ! WRITTEN TO A TEMP FILE AND RENAMED. A checkpoint half-written when the
#     instance was reclaimed is worse than none: it loads, and it is wrong.
#     os.replace is atomic on the same filesystem.
def _saveCheckpoint(path, model, optimizer, epoch, best_val_loss, bad_epochs,
                    scheduler=None):
    tmp = path + ".tmp"
    torch.save({
        "epoch": epoch,                       # epochs COMPLETED
        "model": model.state_dict(),
        "optimizer": optimizer.state_dict(),
        "scheduler": (scheduler.state_dict() if scheduler is not None
                      else None),
        "best_val_loss": best_val_loss,
        "bad_epochs": bad_epochs,
        "batch_size": BATCH_SIZE,
        "lr": LEARNING_RATE,
    }, tmp)
    os.replace(tmp, path)


def _loadCheckpoint(path, model, optimizer, scheduler=None):
    """(next_epoch, best_val_loss, bad_epochs). (0, inf, 0) if there is none."""
    if not path or not os.path.exists(path):
        return 0, float("inf"), 0
    ck = torch.load(path, map_location=DEVICE)
    # ! THE CALIBRATION BUFFERS RIDE IN THE MODEL STATE, so a resume also
    #   restores the feature and target stats the run started with -- the
    #   ones freshly computed above are overwritten, which is correct: a
    #   resumed run must keep scoring the same target.
    model.load_state_dict(ck["model"])
    optimizer.load_state_dict(ck["optimizer"])
    if scheduler is not None and ck.get("scheduler") is not None:
        scheduler.load_state_dict(ck["scheduler"])
    # ⚠ SAID OUT LOUD RATHER THAN SILENTLY HONOURED. Resuming with a
    #   different batch size or learning rate than the checkpoint was
    #   written under is legitimate, but the loss curve either side of the
    #   join is not one curve, and that is worth knowing when you read it.
    if ck.get("batch_size") != BATCH_SIZE or ck.get("lr") != LEARNING_RATE:
        print(f"  ! resuming with batch={BATCH_SIZE} lr={LEARNING_RATE}, "
              f"checkpoint had batch={ck.get('batch_size')} "
              f"lr={ck.get('lr')}")
    print(f"  resumed from {path}: {ck['epoch']} epochs done, "
          f"best val {ck['best_val_loss']:.4f}")
    return ck["epoch"], ck["best_val_loss"], ck.get("bad_epochs", 0)


def _buildScheduler(optimizer, steps_per_epoch: int):
    """Linear warmup over WARMUP_STEPS, then cosine to LR_FLOOR_FRAC of the
    peak over the remaining EPOCHS * steps_per_epoch steps. Stepped once per
    optimizer step."""
    import math as _m
    total = max(EPOCHS * max(steps_per_epoch, 1), WARMUP_STEPS + 1)

    def factor(step):
        if step < WARMUP_STEPS:
            return (step + 1) / WARMUP_STEPS
        frac = min((step - WARMUP_STEPS) / max(total - WARMUP_STEPS, 1), 1.0)
        return LR_FLOOR_FRAC + (1.0 - LR_FLOOR_FRAC) * 0.5 * (
            1.0 + _m.cos(_m.pi * frac))

    return torch.optim.lr_scheduler.LambdaLR(optimizer, factor)

# main
# Purpose: The full training run, start to finish: dataset -> split ->
#          target stats -> loaders -> model/optimizer/loss -> epoch loop
#          -> save the best model. Reads only disk chunks; no database.
# Output:  none (trains and writes model.pt + target_stats.pkl)
def main():

    # 1. Full dataset (reads metadata only; tensors stay on disk).
    dataset = ChunkedRaceDataset(DATA_DIR)

    # 2. Split FIRST, so the stats can use train indices only.
    train_subset, val_subset = splitTrainVal(dataset)

    # ⚠ AN EMPTY VAL SPLIT REPORTS PERFECTION, AND SAVES ON IT. With no val
    #   examples the epoch line reads `val 0.0000`, every epoch is a "new
    #   best", and a later run measuring real val loss can never beat the
    #   zero -- so patience burns out immediately and the best model on disk
    #   is whichever one happened to be saved against nothing.
    #
    # ! ONLY REACHABLE WITH A WINDOW, which is why it appears with one. A
    #   full corpus splits ~10% val by athlete; a window narrow enough to
    #   contain none of them is a mistake, and the run should say so rather
    #   than produce numbers.
    if len(val_subset) == 0:
        raise SystemExit(
            "  no validation examples in this window -- val_mask.pt holds "
            "none of these\n  athletes, so val loss would be meaningless "
            "and early stopping would not work.\n  Widen --chunk-range, or "
            "drop it to train on the whole corpus.")

    # 3. Input and target stats from the TRAIN side of a chunk prefix, saved
    #    beside the model and stamped into the model's buffers.
    is_train = _trainSideMask(dataset, train_subset)
    stats = computeStats(dataset, is_train)
    saveTargetStats(stats, STATS_OUT)
    # ! THE LABEL NAMES THE RULE, because under --baseline ewma "ln(t/last)"
    #   and "last-race error" are both false and the number would be read as
    #   a comparison it is not.
    _bl = {BASELINE_EWMA: "ewma", BASELINE_BEST2: "best2"}.get(
        BASELINE, "last")
    print(f"  stats from {stats['n_chunks']} chunks, "
          f"{stats['n_examples']:,} train examples: ln(t/{_bl}) mean "
          f"{stats['mean']:+.4f} std {stats['std']:.4f}; the baseline's own "
          f"error is about {100.0 * stats['std']:.1f}% of a time")

    # 4. One loader per split.
    train_loader = buildDataLoader(train_subset, shuffle=True)
    val_loader   = buildDataLoader(val_subset,   shuffle=False)

    # 5. The model. ★ SIZED FROM venue_vocab.pkl, WRITTEN BESIDE THE CHUNKS.
    vocab_path = os.path.join(DATA_DIR, "venue_vocab.pkl")
    if os.path.exists(vocab_path):
        with open(vocab_path, "rb") as f:
            n_venues = pickle.load(f)["n_venues"]
        print(f"  venue embedding: {n_venues:,} rows "
              f"(index 0 is the shared unknown bucket)")
    else:
        n_venues = 1
        print("  venue_vocab.pkl not found -- venue embedding disabled")

    model = XCPredictor(n_venues=n_venues)
    # ⚠ BEFORE setTargetStats, AND WITH THE SAME VALUES computeStats USED.
    #   The target is ln(target/baseline), so the stats below are properties
    #   of the rule; measuring under one and un-z-scoring under another puts
    #   every prediction off by the difference between two centres.
    model.setBaselineRule(BASELINE, BASELINE_HALF_LIFE)
    model.setFeatureStats(stats["seq_mean"], stats["seq_std"],
                          stats["ctx_mean"], stats["ctx_std"])
    model.setTargetStats(stats["mean"], stats["std"],
                         stats["fallback_seconds"])
    model = model.to(DEVICE)

    # 6. AdamW + warmup/cosine schedule. See the constants.
    optimizer = torch.optim.AdamW(model.parameters(), lr=LEARNING_RATE,
                                  weight_decay=WEIGHT_DECAY)
    scheduler = _buildScheduler(optimizer, len(train_loader))

    # 7. Gaussian NLL on the z-scored log ratio with the learned variance.
    #    See VAR_EPS. reduction='mean' over the batch, as MSE was.
    criterion = nn.GaussianNLLLoss(eps=VAR_EPS)

    start_epoch, best_val_loss, bad_epochs = _loadCheckpoint(
        CHECKPOINT, model, optimizer, scheduler)

    # ★ AND target_stats.pkl IS REWRITTEN FROM THE RESUMED MODEL, not left
    #   as the numbers computed above (owner, 2026-09-16, training the
    #   corpus in shifts). _loadCheckpoint restores the calibration buffers
    #   -- correctly, a resumed run must keep scoring the same target -- but
    #   saveTargetStats had already written THIS window's numbers to disk.
    #
    # ⚠ THE MODEL WOULD THEN PREDICT IN ONE z-SPACE AND INFERENCE WOULD
    #   INVERT IN ANOTHER. Nothing would error: racecast/predict.py reads the
    #   file, multiplies by a std that is close but not equal, and every
    #   prediction comes back slightly wrong forever. Two sources of truth
    #   for one number, so the model's own buffers are made the only one.
    if start_epoch > 0:
        saveTargetStats(_statsFromModel(model, stats), STATS_OUT)
        print(f"  target_stats.pkl rewritten from the checkpoint: "
              f"mean {float(model.target_mean):+.4f} "
              f"std {float(model.target_std):.4f}")

    for epoch in range(start_epoch, EPOCHS):
        train_loss, st = _trainOneEpoch(model, train_loader, optimizer,
                                        criterion, scheduler)
        val_loss, pct_model, pct_base, cal = _validateOneEpoch(
            model, val_loader, criterion)

        # ! THE TWO PERCENTAGES ARE THE NUMBERS TO READ. `model` is the
        #   model's mean error as a share of the time; `last-race` is the
        #   error of predicting the athlete's previous race unchanged. The
        #   model has to sit clearly under the second to be worth having.
        print(f"epoch {epoch + 1:2d}/{EPOCHS}  "
              f"train {train_loss:.4f}  val {val_loss:.4f}  "
              f"model {pct_model:.2f}%  "
              f"{'last-race' if BASELINE == BASELINE_LAST else 'baseline'} "
              f"{pct_base:.2f}%  "
              f"rmse {cal['rmse_pct']:.2f}% vs sigma {cal['sigma_pct']:.2f}% "
              f"({cal['inside_1s']:.0f}% inside 1s)  "
              + (f"close-pair {cal['pair_model']:.1f}% vs "
                 f"{cal['pair_base']:.1f}% (n={cal['pair_n']:,})  "
                 if cal["pair_n"] else "")
              + f"{st['examples_per_s']:,.0f} ex/s  "
              f"{st['steps_per_s']:.1f} steps/s  "
              f"waiting {st['waiting'] * 100:.0f}%  "
              + (f"clamped {st['clamped']:,}  " if st.get("clamped") else "")
              + (f"DROPPED {st['bad_batches']} non-finite batches  "
                 if st.get("bad_batches") else "")
              + f"({st['elapsed'] / 60:.1f} min)")
        # ★ AND WHAT IT IS GOOD AT, not just how good on average. Each band
        #   against the last-race baseline ON THE SAME ROWS, because the
        #   baseline is far weaker over years than over weeks and comparing
        #   a band's model error to the overall baseline would flatter it.
        if cal.get("bands"):
            print("           by horizon:  " + "   ".join(
                f"{b['label']} {b['model_pct']:.2f}% vs {b['base_pct']:.2f}%"
                f" (n={b['n']:,})" for b in cal["bands"]))

        # ⚠ MIN_DELTA, NOT `<`. An improvement of 1e-9 is not an improvement;
        #   without a threshold it resets the patience counter forever and
        #   early stopping never fires.
        if val_loss < best_val_loss - MIN_DELTA:
            best_val_loss = val_loss
            bad_epochs = 0
            _saveModel(model, MODEL_OUT)
            print(f"  ↳ new best, saved to {MODEL_OUT}")
        else:
            bad_epochs += 1
            print(f"  ↳ no improvement ({bad_epochs}/{PATIENCE})")

        # ! EVERY EPOCH, IMPROVED OR NOT. An epoch that got worse is still an
        #   epoch you do not want to pay for twice.
        if CHECKPOINT:
            _saveCheckpoint(CHECKPOINT, model, optimizer, epoch + 1,
                            best_val_loss, bad_epochs, scheduler)

        if bad_epochs >= PATIENCE:
            print(f"\nstopping: {PATIENCE} epochs with no improvement. "
                  f"Best val {best_val_loss:.4f}, saved to {MODEL_OUT}.")
            break


# Standard Python entry point. This block runs ONLY when you launch the
# file directly (`python train.py`), NOT when something imports it. So
# importing train.py to reuse a helper won't accidentally kick off a full
# training run.
if __name__ == "__main__":
    # --max-chunks 20: the smoke run, without editing this file. The
    # overnight used to rewrite MAX_CHUNKS in place and restore it after,
    # which left the file dirty whenever the night died mid-train.
    import argparse
    _ap = argparse.ArgumentParser(
        description="Train the predictor. Every dial below has a default "
                    "that changes nothing; pass one to change it.")
    _ap.add_argument("--data", default=None,
                     help=f"directory holding chunk_NNNN.pt + metadata.pkl "
                          f"(default {DATA_DIR}). model.pt and "
                          f"target_stats.pkl are written here too, so a "
                          f"smoke run on model/fake_chunks.py output never "
                          f"touches the real chunks.")
    _ap.add_argument("--max-chunks", type=int, default=None,
                     help="train on the first N chunks only (10k examples "
                          "each). Chunks are corpus-wide shuffles, so a "
                          "prefix is a fair sample -- this is the smoke run "
                          "and the cheap-subset run both.")
    _ap.add_argument("--chunk-range", default=None, metavar="LO:HI",
                     help="train on chunks LO..HI-1 instead of a prefix, "
                          "e.g. 3000:6000. For a corpus too big to keep on "
                          "disk: train a window, swap the files, resume "
                          "from --checkpoint with the next window.")
    _ap.add_argument("--batch", type=int, default=None,
                     help=f"batch size (default {BATCH_SIZE}). 512-1024 "
                          f"suits a 24GB GPU; scale --lr with it.")
    _ap.add_argument("--lr", type=float, default=None,
                     help=f"learning rate (default {LEARNING_RATE})")
    _ap.add_argument("--epochs", type=int, default=None,
                     help=f"ceiling on epochs (default {EPOCHS}); early "
                          f"stopping usually gets there first")
    _ap.add_argument("--patience", type=int, default=None,
                     help=f"epochs without a new best before stopping "
                          f"(default {PATIENCE})")
    _ap.add_argument("--workers", type=int, default=None,
                     help=f"DataLoader worker processes (default "
                          f"{NUM_WORKERS}). The biggest lever on wall clock "
                          f"here: with 0 the GPU waits on every disk read.")
    _ap.add_argument("--amp", action="store_true",
                     help="bf16 mixed precision on CUDA. No-op on CPU.")
    _ap.add_argument("--baseline", choices=("last", "ewma", "best2"),
                     default=None,
                     help="what a prediction is anchored on. 'last' is the "
                          "most recent race alone, which is what shipped; "
                          "'ewma' is a recency-weighted geometric mean; "
                          "'best2' is the two fastest recent races, which is "
                          "LACCTiC's rule and the one this engine's "
                          "weighted-mean ability estimator disagrees with. "
                          "Changing it needs a RETRAIN, not a "
                          "re-extraction.")
    _ap.add_argument("--baseline-half-life", type=float, default=None,
                     metavar="DAYS",
                     help="for --baseline ewma: a race this many days ago "
                          f"counts half as much as one today. Default "
                          f"{BASELINE_HALF_LIFE_DAYS:.0f}.")
    _ap.add_argument("--out-dir", default=None, metavar="DIR",
                     help="where to WRITE model.pt and target_stats.pkl. "
                          "Defaults to --data, which is how it has always "
                          "behaved -- pass this to train a second model "
                          "WITHOUT overwriting the one already serving. "
                          "encoders.pkl and venue_vocab.pkl are copied in, "
                          "so the directory is a complete artifact set that "
                          "racecast/predict.py can load on its own.")
    _ap.add_argument("--checkpoint", default=None,
                     help="path to save/resume optimizer+weights+epoch "
                          "every epoch. REQUIRED for spot instances: "
                          "model.pt alone cannot resume, it has no "
                          "optimizer state.")
    _args = _ap.parse_args()

    # ⚠ FIRST, because MODEL_OUT and STATS_OUT are derived from it.
    if _args.data:
        DATA_DIR = _args.data
        MODEL_OUT = os.path.join(DATA_DIR, "model.pt")
        STATS_OUT = os.path.join(DATA_DIR, "target_stats.pkl")
    # ★ AND THE OUTPUT CAN LIVE SOMEWHERE ELSE (2026-09-17). --data set both
    #   the input chunks AND the output, so training a second model to
    #   compare against would have OVERWRITTEN the one currently serving the
    #   site. An A/B that destroys the A is not an A/B.
    if _args.out_dir:
        OUT_DIR = _args.out_dir
        os.makedirs(OUT_DIR, exist_ok=True)
        MODEL_OUT = os.path.join(OUT_DIR, "model.pt")
        STATS_OUT = os.path.join(OUT_DIR, "target_stats.pkl")
        # ! THE SET HAS TO BE COMPLETE OR IT CANNOT BE LOADED.
        #   predict._loadModel reads encoders and the venue vocabulary from
        #   the CHECKPOINT'S OWN DIRECTORY, and refuses when one is missing.
        #   Copying them is cheap and turns the output into something
        #   --model can be pointed at directly.
        import shutil as _shutil
        for _name in ("encoders.pkl", "venue_vocab.pkl"):
            _src = os.path.join(DATA_DIR, _name)
            _dst = os.path.join(OUT_DIR, _name)
            if os.path.exists(_src) and not os.path.exists(_dst):
                _shutil.copy2(_src, _dst)
                print(f"  copied {_name} -> {OUT_DIR}")
    if _args.max_chunks:
        MAX_CHUNKS = _args.max_chunks
    if _args.chunk_range:
        try:
            _lo, _hi = (int(x) for x in _args.chunk_range.split(":"))
        except ValueError:
            raise SystemExit("--chunk-range wants LO:HI, e.g. 3000:6000")
        CHUNK_RANGE = (_lo, _hi)
    if _args.batch:
        BATCH_SIZE = _args.batch
    if _args.lr:
        LEARNING_RATE = _args.lr
    if _args.epochs:
        EPOCHS = _args.epochs
    if _args.patience is not None:
        PATIENCE = _args.patience
    if _args.workers is not None:
        NUM_WORKERS = _args.workers
    USE_AMP = _args.amp
    CHECKPOINT = _args.checkpoint
    if _args.baseline:
        BASELINE = {"ewma": BASELINE_EWMA, "best2": BASELINE_BEST2,
                    "last": BASELINE_LAST}[_args.baseline]
    if _args.baseline_half_life:
        BASELINE_HALF_LIFE = _args.baseline_half_life

    # ⚠ NAMED IN THE HEADER, because a shift boundary is otherwise
    #   invisible in the log and two windows' epochs read as one run.
    print(f"device {DEVICE}  batch {BATCH_SIZE}  lr {LEARNING_RATE}  "
          f"workers {NUM_WORKERS}  amp {USE_AMP}  "
          f"epochs<={EPOCHS} patience {PATIENCE}"
          + (f"  checkpoint {CHECKPOINT}" if CHECKPOINT else
             "  NO CHECKPOINT -- an interrupted run starts over"))
    # ⚠ NAMED TOO, because two runs over the same chunks with different
    #   baselines are not comparable and the log is the only place that says
    #   which one produced a given model.pt.
    print(f"reading chunks from {DATA_DIR}")
    print(f"writing model.pt to   {MODEL_OUT}")
    print("baseline "
          + ("ewma, half-life "
             f"{BASELINE_HALF_LIFE:.0f}d" if BASELINE == BASELINE_EWMA
             else f"best two within {BASELINE_BEST_WINDOW_DAYS:.0f}d"
             if BASELINE == BASELINE_BEST2 else "last race alone")
          + "  (a retrain, not a re-extraction -- see transformer.BASELINE_LAST)")
    main()