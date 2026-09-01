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
from transformer import XCPredictor

# ------------------------------------------------------------------ #
# CONSTANTS — the training dials, named once so they don't drift
# ------------------------------------------------------------------ #

# Where feature_extraction.py saved chunk_NNNN.pt + metadata.pkl, and
# where we'll write the trained model.
DATA_DIR  = "model/data"
MODEL_OUT = "model/data/model.pt"

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

# Fixed seed so the random train/val split is identical every run —
# makes results reproducible and comparable.
SEED = 42

# Where we save the two numbers (mean, std) that turn a z-scored
# prediction back into real seconds. Losing this file makes a trained
# model useless for inference.
STATS_OUT = "model/data/target_stats.pkl"

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

        # The smoke-run cap: read only the first MAX_CHUNKS files. Valid
        # because extraction shuffles corpus-wide before chunking, so a
        # prefix of chunks is a fair sample rather than a run of athletes.
        if MAX_CHUNKS is not None and MAX_CHUNKS < self.num_chunks:
            self.num_chunks     = MAX_CHUNKS
            self.total_examples = min(self.total_examples,
                                      MAX_CHUNKS * self.chunk_size)
            print(f"  MAX_CHUNKS={MAX_CHUNKS}: training on "
                  f"{self.total_examples:,} examples")

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
        chunk_idx = i // self.chunk_size   # // = integer division
        row       = i %  self.chunk_size   # %  = remainder

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
# Two numbers, mean and std, are computed once and SAVED to disk. They
# are the only way to undo the scaling later: at inference Flask reverses
# it with  target = z * std + mean  to get back to real normalized_time
# (then the §4 formula turns that into a race time). Lose this file and a
# trained model can't be read back into seconds.
#
# The four helpers, one idea each:
#   _loadAllTargets    — gather every target into one flat tensor
#   computeTargetStats — mean/std from the TRAIN rows only (leakage-free)
#   zScore             — apply (x - mean) / std   (runs every batch)
#   saveTargetStats    — persist mean + std       (runs once)


# _loadAllTargets
# Purpose: Pull just the targets out of every chunk file and stack them
#          into one flat tensor, indexed by global example index.
# Arguments:
#           dataset: a ChunkedRaceDataset (gives us num_chunks + the
#                    chunk-loading helper)
# Output:  a 1-D tensor [total_examples] — every target, in global order
def _loadAllTargets(dataset: ChunkedRaceDataset) -> torch.Tensor:

    per_chunk_targets = []

    # For each chunk file we pull the target normalized times 
    # out and collect them.
    for chunk_idx in range(dataset.num_chunks):
        chunk = dataset._loadChunk(chunk_idx)
        per_chunk_targets.append(chunk["targets"])

    # torch.cat glues the list of [N_chunk] tensors end-to-end into one
    # [total_examples] tensor. dim=0 = join along the single (row) axis.
    return torch.cat(per_chunk_targets, dim=0)
        
# computeTargetStats
# Purpose: Mean and std of the TRAINING targets only (leakage-free), to
#          z-score every target with the same two numbers.
# Arguments:
#           all_targets:   [total_examples] tensor from _loadAllTargets
#           train_indices: the global indices in the TRAIN split
#                          (Subset.indices, produced in chunk 4)
# Output:  (mean, std) as plain Python floats
def computeTargetStats(all_targets: torch.Tensor, train_indices) -> tuple:

    # Out of all the target times (normalized times), pulls out
    # the training amount.
    train_targets = all_targets[train_indices]      # [n_train]

    # Calculates the mean and  std, .item() pulls the lone number
    # out of a 1-element tensor.
    mean = train_targets.mean().item()
    std  = train_targets.std().item()
    
    return mean, std

# zScore
# Purpose: Apply (value - mean) / std. Called in the training loop on
#          each batch of targets, right before the loss.
# Arguments:
#           values: tensor of raw targets (any shape)
#           mean/std: the training stats from computeTargetStats
# Output:  tensor, same shape as values, z-scored
def zScore(values: torch.Tensor, mean: float, std: float) -> torch.Tensor:
    return (values - mean) / std

# saveTargetStats
# Purpose: Persist mean + std. These are the ONLY way to turn a
#          prediction back into seconds, so a trained model is useless
#          without them.
# Arguments:
#           mean/std: the training stats
#           path:     where to write the pickle
# Output:  none (writes a file)
def saveTargetStats(mean: float, std: float, path: str) -> None:
    with open(path, "wb") as f: # "wb" = write, binary
        pickle.dump({"target_mean": mean, "target_std": std}, f)


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
        mask = torch.load(vm_path)[:len(dataset)]   # MAX_CHUNKS may cap us
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
#           optimizer: Adam, applies the weight updates
#           criterion: MSELoss, turns (pred, target) into one number
#           mean/std:  target stats, to z-score each batch's targets
# Output:  float — average training loss over the epoch
def _trainOneEpoch(model, loader, optimizer, criterion, mean, std) -> float:

    # Flips model into training mode, dropout on.
    model.train()
    total_loss = 0.0 # A running tally of each batch's loss. Calculated by MSE.
    n_batches = 0

    # ★ THROUGHPUT, MEASURED RATHER THAN GUESSED. The question this run has
    #   to answer before any hardware is rented is "how many examples a
    #   second", and nothing was printing it. `waiting` is the share of wall
    #   clock spent BLOCKED ON THE LOADER rather than computing: if it is
    #   high, a faster GPU buys nothing and the fix is workers or batch size.
    t_start = time.time()
    t_wait = 0.0
    n_examples = 0
    _t_batch = time.time()

    # Goes over each batch, which is a 4-tuple of tensors. e.g.
    # sequences  [64, S, 17]   the 64 athletes' race histories
    # masks      [64, S]       which rows are real races vs padding
    # context    [64, 17]      the 64 target-race context vectors
    # targets    [64]          the 64 true normalized_times to predict
    for sequences, masks, context, targets, venues in loader:
        # Everything between the previous iteration ending and this one
        # starting was spent waiting for the loader to produce a batch.
        t_wait += time.time() - _t_batch

        # Moves this batch onto the save device as the model.
        sequences = sequences.to(DEVICE)
        masks     = masks.to(DEVICE)
        context   = context.to(DEVICE)
        targets   = targets.to(DEVICE)
        venues    = venues.to(DEVICE)

        # z-score the targets so they match the scale the model predicts
        # in (chunk 3). Same mean/std for every batch.
        targets = zScore(targets, mean, std)

        # --- the four-line core of learning ---
        optimizer.zero_grad()                      # 1. clear old gradients
        # ! bf16, NOT fp16, AND THAT IS WHY THERE IS NO GradScaler. fp16 has
        #   too little exponent range for raw gradients, so it needs loss
        #   scaling to avoid underflow; bf16 keeps fp32's range and drops
        #   mantissa bits instead, which this model does not miss. One less
        #   moving part, and supported on every GPU worth renting.
        with _autocast():
            preds = model(sequences, masks, context, venues)  # 2. forward
            loss  = criterion(preds, targets)      # 3. measure how wrong
        loss.backward()                            # 4a. compute gradients
        optimizer.step()                           # 4b. apply the update
        # --------------------------------------

        total_loss += loss.item()   # .item() = pull the float out
        n_batches  += 1
        n_examples += targets.size(0)
        _t_batch = time.time()

    # ⚠ RETURNS A PAIR NOW. The caller prints the rate beside the losses,
    #   because a loss curve with no throughput beside it cannot answer
    #   "will this finish, and on what".
    elapsed = max(time.time() - t_start, 1e-9)
    stats = {"examples_per_s": n_examples / elapsed,
             "steps_per_s": n_batches / elapsed,
             "waiting": t_wait / elapsed,
             "elapsed": elapsed}
    return total_loss / n_batches, stats

# _validateOneEpoch
# Purpose: Run one full pass over the validation data, measuring loss
#          WITHOUT learning — no backward, no optimizer step. Tells us
#          whether the model generalises to data it didn't train on.
# Arguments:
#           model:     the XCPredictor
#           loader:    val_loader
#           criterion: MSELoss
#           mean/std:  the SAME train-derived stats (chunk 3)
# Output:  float — average validation loss over the epoch
def _validateOneEpoch(model, loader, criterion, mean, std) -> float:

    # Flips model into eval mode, dropout off.
    model.eval()
    total_loss = 0.0
    n_batches  = 0

    # no_grad: don't record operations for backward (faster, less memory)
    # since we never call backward here, gradient tracking switched
    # off.
    with torch.no_grad():

        for sequences, masks, context, targets, venues in loader:

            sequences = sequences.to(DEVICE)
            masks     = masks.to(DEVICE)
            context   = context.to(DEVICE)
            targets   = targets.to(DEVICE)
            venues    = venues.to(DEVICE)

            targets = zScore(targets, mean, std)

            # Same precision as training, so val loss is comparable to
            # train loss rather than measured on a different arithmetic.
            with _autocast():
                preds = model(sequences, masks, context, venues)
            loss  = criterion(preds, targets)

            total_loss += loss.item()
            n_batches  += 1

    return total_loss / n_batches


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
def _saveCheckpoint(path, model, optimizer, epoch, best_val_loss, bad_epochs):
    tmp = path + ".tmp"
    torch.save({
        "epoch": epoch,                       # epochs COMPLETED
        "model": model.state_dict(),
        "optimizer": optimizer.state_dict(),
        "best_val_loss": best_val_loss,
        "bad_epochs": bad_epochs,
        "batch_size": BATCH_SIZE,
        "lr": LEARNING_RATE,
    }, tmp)
    os.replace(tmp, path)


def _loadCheckpoint(path, model, optimizer):
    """(next_epoch, best_val_loss, bad_epochs). (0, inf, 0) if there is none."""
    if not path or not os.path.exists(path):
        return 0, float("inf"), 0
    ck = torch.load(path, map_location=DEVICE)
    model.load_state_dict(ck["model"])
    optimizer.load_state_dict(ck["optimizer"])
    # ⚠ SAID OUT LOUD RATHER THAN SILENTLY HONOURED. Resuming with a
    #   different batch size or learning rate than the checkpoint was
    #   written under is legitimate -- it is how you recover from a bad
    #   guess -- but it means the loss curve either side of the join is not
    #   one curve, and that is worth knowing when you read it later.
    if ck.get("batch_size") != BATCH_SIZE or ck.get("lr") != LEARNING_RATE:
        print(f"  ! resuming with batch={BATCH_SIZE} lr={LEARNING_RATE}, "
              f"checkpoint had batch={ck.get('batch_size')} "
              f"lr={ck.get('lr')}")
    print(f"  resumed from {path}: {ck['epoch']} epochs done, "
          f"best val {ck['best_val_loss']:.4f}")
    return ck["epoch"], ck["best_val_loss"], ck.get("bad_epochs", 0)

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

    # 3. Target stats from the TRAIN split only, then saved to disk
    #    (chunk 3 helpers). mean/std are needed again in the loop to
    #    z-score each batch, and at inference to undo it.
    all_targets = _loadAllTargets(dataset)
    mean, std   = computeTargetStats(all_targets, train_subset.indices)
    saveTargetStats(mean, std, STATS_OUT)

    # 4. One loader per split. Train shuffles (new order each epoch so the
    #    model can't memorise sequence); val doesn't (order is irrelevant
    #    when you're only measuring, not learning).
    train_loader = buildDataLoader(train_subset, shuffle=True)
    val_loader   = buildDataLoader(val_subset,   shuffle=False)

    # 5. The model, moved onto the GPU (or CPU fallback). .to(DEVICE)
    #    sends every weight to that device so model and data live together.
    # ★ SIZED FROM venue_vocab.pkl, WRITTEN BESIDE THE CHUNKS. Counting
    #   distinct venues in the loaded data instead would give a different size
    #   on any subset, and every embedding row would belong to a different
    #   course than the one it was trained for.
    vocab_path = os.path.join(DATA_DIR, "venue_vocab.pkl")
    if os.path.exists(vocab_path):
        with open(vocab_path, "rb") as f:
            n_venues = pickle.load(f)["n_venues"]
        print(f"  venue embedding: {n_venues:,} rows "
              f"(index 0 is the shared unknown bucket)")
    else:
        # No vocabulary means chunks from before this feature. One row, always
        # index 0, so the embedding contributes a constant zero and the model
        # behaves exactly as it did.
        n_venues = 1
        print("  venue_vocab.pkl not found -- venue embedding disabled")

    model = XCPredictor(n_venues=n_venues).to(DEVICE)

    # 6. Adam — applies the weight updates using the gradients
    #    loss.backward() computes. model.parameters() hands it every
    #    learnable weight; lr is the baseline step size.
    optimizer = torch.optim.Adam(model.parameters(), lr=LEARNING_RATE)

    # 7. MSELoss — mean of (pred - target)². The number the whole loop
    #    drives downward. Matches predicting a single continuous value.
    criterion = nn.MSELoss()
    
    # --- the epoch loop (calls chunk 5's helpers) ---
    # Track the best val loss so far. Start at infinity so the very first
    # epoch always counts as an improvement and saves once.
    # Where the run stands: from a checkpoint if one exists, otherwise the
    # start. best_val_loss begins at infinity so the first epoch always
    # counts as an improvement and saves once.
    start_epoch, best_val_loss, bad_epochs = _loadCheckpoint(
        CHECKPOINT, model, optimizer)

    for epoch in range(start_epoch, EPOCHS):
        train_loss, st = _trainOneEpoch(model, train_loader, optimizer,
                                        criterion, mean, std)
        val_loss   = _validateOneEpoch(model, val_loader,
                                       criterion, mean, std)

        # +1 because range starts at 0; humans count epochs from 1.
        # :.4f = 4 decimal places, enough to see the losses move.
        # ! THE RATE IS PRINTED BESIDE THE LOSSES, because "will this
        #   finish, and on what" is answered by the first number and not the
        #   other two. `waiting` is the share of the epoch spent blocked on
        #   the loader: high means a faster GPU buys nothing.
        print(f"epoch {epoch + 1:2d}/{EPOCHS}  "
              f"train {train_loss:.4f}  val {val_loss:.4f}  "
              f"{st['examples_per_s']:,.0f} ex/s  "
              f"{st['steps_per_s']:.1f} steps/s  "
              f"waiting {st['waiting'] * 100:.0f}%  "
              f"({st['elapsed'] / 60:.1f} min)")

        # Save ONLY when val loss hits a new low (see header). Later,
        # worse epochs leave the saved file untouched.
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
                            best_val_loss, bad_epochs)

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
    _ap.add_argument("--max-chunks", type=int, default=None,
                     help="train on the first N chunks only (10k examples "
                          "each). Chunks are corpus-wide shuffles, so a "
                          "prefix is a fair sample -- this is the smoke run "
                          "and the cheap-subset run both.")
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
    _ap.add_argument("--checkpoint", default=None,
                     help="path to save/resume optimizer+weights+epoch "
                          "every epoch. REQUIRED for spot instances: "
                          "model.pt alone cannot resume, it has no "
                          "optimizer state.")
    _args = _ap.parse_args()

    if _args.max_chunks:
        MAX_CHUNKS = _args.max_chunks
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

    print(f"device {DEVICE}  batch {BATCH_SIZE}  lr {LEARNING_RATE}  "
          f"workers {NUM_WORKERS}  amp {USE_AMP}  "
          f"epochs<={EPOCHS} patience {PATIENCE}"
          + (f"  checkpoint {CHECKPOINT}" if CHECKPOINT else
             "  NO CHECKPOINT -- an interrupted run starts over"))
    main()