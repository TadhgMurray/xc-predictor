# What waits for what (the server's jobs)

Written 2026-11-09 (back-end track). The step-by-step data dependencies inside
one run are in docs/PIPELINE-DEPENDENCIES.md; this file is about the JOBS on
the box and the two things they share: **`.pipeline.lock`** (who may change
the database's tables) and **memory** (125 GB, beside the live site and
Postgres).

## The jobs

| job | started by | lock | memory (reserve) |
|---|---|---|---|
| `run_pipeline.sh` | the owner, in tmux | exclusive, whole run | measured per step; 08_golive reserves `XCP_GOLIVE_GB` (60) |
| `nightly_update.sh` | `xcp-nightly.timer` | exclusive, whole night | — |
| `backup_db.sh` (pg_dump) | `xcp-backup.timer` | queued, dump only (released before the off-server copy) | — |
| `race_model_weekly.sh` | `xcp-race-model.timer` | queued, table build only (~1.5 h); the routes again (~20 min) | race_rows 20, each fit 30 |
| `engine_replay.sh` | the owner, in tmux (one to three sessions) | **none** | 60 each, sacrificial |
| `edge_purge.py --delay 960` | the end of a run | **none** (`9>&-`) | — |

## The lock: `.pipeline.lock`

Every job takes it through `deploy/lockctl.sh` (`lock_take` / `lock_release`).

1. **Never two writers.** A pipeline or the nightly holding the lock makes a
   second one stop at once (the nightly skips the night).
2. **Queued jobs never jump an interactive run.** The backup and the race
   model are *queued*: they poll every 30 s and take the lock only when it is
   free **and** no run has posted `.pipeline.lock.want`. A run started while
   one of them holds the lock waits for it (up to `XCP_LOCK_WAIT_MIN`, 120)
   instead of failing, with the want posted so the next queued job cannot
   slip in between.
3. **Why the backup and the race model still take it at all.** pg_dump holds
   a share lock on every table for its whole run, and every pipeline swap
   (DROP + RENAME) needs an exclusive one: a swap behind a dump queues every
   page behind it. race_rows reads ratings for 1.5 h in one cursor; a go-live
   in the middle would give it half old, half new ratings. Both conflict with
   the whole run, so both hold the lock -- but only for the part that reads
   the database. The off-server copy and the fits run without it.
4. **Nothing the run leaves behind holds it.** A child that outlives the run
   is started with `9>&-` (the delayed edge purge; the nightly's scrapers).
5. **Every holder says who it is.** `.pipeline.lock.holder` has the job, its
   pid, since when, and what it is doing. `bash scripts/who_holds_the_lock.sh`
   reads `/proc/locks`, which tells the holder from the jobs waiting for it,
   prints the systemd unit (a timer's job is not on any screen), the want, and
   the memory ledger.

## Memory: `scripts/jobrun.py`

Every pipeline step runs under `jobrun.py`, which measures it (peak RSS of
its process tree, the lowest MemAvailable while it ran: `summary.log`,
`$LOGDIR/.mem/`, `scripts/pipeline_profile.py`). Memory-heavy jobs are also
**admitted** by it, under one flock, against a ledger
(`.memgate/<pid>.json`) of what running jobs reserved and have not yet grown
into. A check is not a reservation: two replays that both read "70 GB free"
in the same minute both started (1.5 GB left, 2026-11-09).

1. **The go-live goes first.** From the start of a run that will solve
   (`--from` 8 or lower) until its evidence chain is collected, the pipeline
   posts a standing want: no engine replay starts. When 08_golive asks for its
   memory and cannot have it, a running replay stops itself (exit 75), its
   cutoff is unclaimed and solved again later. Anything else in the way is
   waited for up to `XCP_GOLIVE_MEM_WAIT_MIN` (90); then the go-live runs
   anyway, and says so.
2. **Replays one at a time, in practice.** A replay reserves `XCP_REPLAY_GB`
   (60) and starts only with `XCP_REPLAY_MIN_GB` (70) free after everyone
   else's reservations. On the 125 GB box that is one replay at a time
   beside the site, Postgres and a pipeline that is not solving.
3. **A replay never starves the site.** It is stopped when its own tree
   passes `XCP_REPLAY_CAP_GB` (85) or when the box falls under
   `XCP_MEM_FLOOR_GB` (8 GB available).
4. **Model fits wait for room.** race_rows and the race_trees fits reserve
   their size and wait up to 4 h; a replay will not start beside one (its
   `busy()` check).
5. **Pipeline steps are never stopped for memory** -- a half-done swap costs
   more than the memory. They are measured, and a step that takes the box
   under the floor says so in its log.

`python scripts/jobrun.py --status` prints the ledger.

## Quick reference

- "Is something holding the lock?" -- `bash scripts/who_holds_the_lock.sh`
- "Why is my replay not starting?" -- `tail -3 logs/replay/summary.log`, then
  `python scripts/jobrun.py --status`
- "What did each step cost?" -- `python scripts/pipeline_profile.py logs/<run>`
  (two runs: before, after)
