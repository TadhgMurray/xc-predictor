"""The training script's operational contract, checked without torch.

There is no GPU and no torch in the session that writes this, so nothing
here runs a training step. What it CAN do is hold the properties that decide
whether a rented box finishes the job or wastes the money:

  - a killed run can resume (optimizer state, not just weights)
  - the loader does not idle the GPU on every disk read
  - the run stops when it stops learning
  - every dial has a default that changes nothing

    python tests/test_train_runtime.py
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = io.open(os.path.join(ROOT, "model", "train.py"), encoding="utf-8").read()

failed = []


def ok(cond, msg):
    if not cond:
        failed.append(msg)
    return cond


def body(name):
    i = SRC.index(f"def {name}(")
    return SRC[i:SRC.index("\ndef ", i + 1)]


# ---- 1. RESUME. The one that decides whether spot capacity is usable. ---- #
save = body("_saveCheckpoint")
ok('"optimizer": optimizer.state_dict()' in save,
   "the checkpoint carries OPTIMIZER state -- reloading weights alone "
   "restarts Adam's moments from zero and throws away part of the training "
   "already paid for")
ok('"epoch": epoch' in save, "and the epoch, so a resume does not redo it")
ok('"best_val_loss"' in save and '"bad_epochs"' in save,
   "and the early-stopping state, or a resumed run forgets it was giving up")
ok("os.replace(tmp, path)" in save,
   "written to a temp file and renamed: a checkpoint half-written when the "
   "instance was reclaimed LOADS, and is wrong")

load = body("_loadCheckpoint")
ok("return 0, float(\"inf\"), 0" in load,
   "no checkpoint is not an error -- a first run just starts")
ok("load_state_dict(ck[\"optimizer\"])" in load, "and a resume restores Adam")

# It must be saved every epoch, not only on an improvement: an epoch that
# got worse is still an epoch you do not want to pay for twice.
main = SRC[SRC.index("    for epoch in range(start_epoch, EPOCHS):"):]
i_bad = main.index("bad_epochs += 1")
i_ck = main.index("_saveCheckpoint(")
ok(i_ck > i_bad,
   "the checkpoint is written after BOTH branches, not inside the "
   "improvement one")

# ---- 2. THE LOADER. The biggest lever on wall clock for this model. ---- #
dl = body("buildDataLoader")
ok("num_workers=NUM_WORKERS" in dl,
   "the DataLoader takes workers -- with 0 the training process blocks on "
   "a torch.load from disk before every batch")
ok("persistent_workers=(NUM_WORKERS > 0)" in dl,
   "kept alive between epochs, or every epoch refills their caches cold")
ok("prefetch_factor=(4 if NUM_WORKERS > 0 else None)" in dl,
   "reading ahead, which is what actually hides the disk latency")
ok('pin_memory=(NUM_WORKERS > 0 and DEVICE.type == "cuda")' in dl,
   "pinned host memory only where it means something")

# ---- 3. STOP WHEN IT STOPS LEARNING. ---- #
ok("val_loss < best_val_loss - MIN_DELTA" in SRC,
   "improvement is measured against the BEST with a threshold: `<` alone "
   "lets a 1e-9 gain reset the patience counter forever")
ok("if bad_epochs >= PATIENCE:" in SRC, "and the run stops when it stops")
ok(re.search(r"^PATIENCE = \d+", SRC, re.M) is not None
   and re.search(r"^MIN_DELTA = ", SRC, re.M) is not None,
   "both named as constants rather than buried in the loop")

# ---- 4. THROUGHPUT IS PRINTED. ---- #
tr = body("_trainOneEpoch")
ok('"examples_per_s"' in tr and '"waiting"' in tr,
   "the epoch reports its rate AND the share of it spent blocked on the "
   "loader -- the second is what says whether a faster GPU would help")
ok("t_wait += time.time() - _t_batch" in tr,
   "the wait is measured between iterations, where the blocking happens")
ok("ex/s" in SRC and "waiting" in SRC, "and the loop prints them")

# ---- 5. EVERY DIAL DEFAULTS TO THE OLD BEHAVIOUR. ---- #
ok(re.search(r"^NUM_WORKERS = 0", SRC, re.M) is not None,
   "workers default to 0: the only value that works everywhere (spawn, "
   "notebooks, small /dev/shm)")
ok(re.search(r"^USE_AMP = False", SRC, re.M) is not None, "AMP is opt-in")
ok(re.search(r"^CHECKPOINT = None", SRC, re.M) is not None,
   "checkpointing is opt-in")
ok(re.search(r"^BATCH_SIZE = 64", SRC, re.M) is not None,
   "and the batch size is unchanged until asked")
for flag in ("--batch", "--lr", "--epochs", "--patience", "--workers",
             "--amp", "--checkpoint", "--max-chunks"):
    ok(f'"{flag}"' in SRC, f"{flag} is available without editing the file")

# ---- 6. AMP IS bf16, AND THEREFORE NEEDS NO GradScaler. ---- #
ac = body("_autocast")
ok("torch.bfloat16" in ac,
   "bf16 keeps fp32's exponent range, so gradients cannot underflow and "
   "no loss scaling is needed")
ok("contextlib.nullcontext()" in ac,
   "and it is a no-op when off or on CPU, so the loop reads the same")
# ⚠ THE CALL, NOT THE WORD. The first version of this failed on the comment
#   that explains why there is no GradScaler -- a test that cannot tell code
#   from prose about the code.
ok("GradScaler(" not in SRC,
   "no GradScaler is constructed, which fp16 would have required")
ok(SRC.count("with _autocast():") == 2,
   "training AND validation use it, or the two losses are measured on "
   "different arithmetic")

if failed:
    for f in failed:
        print("  FAIL " + f)
    print(f"\n{len(failed)} check(s) failed")
    sys.exit(1)

print("  a killed run can resume, optimizer and all ........ OK")
print("  the loader reads ahead in other processes ......... OK")
print("  the run stops when it stops learning .............. OK")
print("  every epoch reports its rate and its idle share ... OK")
print("  every dial defaults to the previous behaviour ..... OK")
print("\nall train-runtime checks passed")
