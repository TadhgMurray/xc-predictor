# run_overrides.ps1 -- rebuild the overrides and take them live, unattended.
#
#     .\run_overrides.ps1
#     .\run_overrides.ps1 -Reset         # after a wipe -- see -Reset below
#     .\run_overrides.ps1 -Reset -Replace # ...and clear an earlier pass block
#     .\run_overrides.ps1 -DryRun        # propose and validate, write nothing
#     .\run_overrides.ps1 -SkipPipeline  # apply, but do not run the 4h rebuild
#
# ★ ONE COMMAND, BECAUSE THE ORDER IS THE PART THAT GOES WRONG. corrections.py
#   -> dump_overrides -> 05_backfill -> the pack -> the solve. Skip
#   dump_overrides and the database never sees the new distances; run
#   run_ratings.ps1 instead of run_pipeline.ps1 and normalized_time is never
#   recomputed, so the whole night rebuilds ratings from the OLD distances.
#   Neither mistake announces itself.
#
# ⚠ IT STOPS AT THE FIRST FAILURE, AND THAT IS THE WHOLE POINT OF RUNNING IT
#   THIS WAY. A half-applied corrections.py followed by four hours of pipeline
#   is the worst outcome available here -- worse than not running at all,
#   because the result LOOKS like a finished rebuild. Every step is checked
#   and the pipeline is the last thing that happens.
#
# Undo, in this order:
#     python scripts\apply_passes.py --undo --write
#     python engine\dump_overrides.py
#     .\run_pipeline.ps1

param(
    [switch]$DryRun,
    [switch]$SkipPipeline,
    [switch]$Reset,
    [switch]$Replace,
    # Owner's call, 2026-08-25: an unattended run ALWAYS applies. The caps
    # still print their warnings into the log, but they do not stop the
    # night -- with every proposal direction clamped downward, an over-cap
    # pass can only over-deflate, never mint fake elites, and a wasted
    # night costs more than a reviewable excess of caution ever saves.
    [switch]$ForceApply,
    [double]$Sigma = 4.5
)

# ---------------------------------------------------------------------- #
#  -Reset -- REBUILDING FROM A WIPE IS A DIFFERENT QUESTION
# ---------------------------------------------------------------------- #
#
# * WITHOUT IT THE PASSES JUDGE THE RATINGS ON DISK, which were solved WITH
#   the current overrides. A CORRECT override therefore sits perfectly on its
#   athletes' own heads -- gap ~ 0 -- and pass 1 declines it, correctly.
#   Wipe corrections.py afterwards and that override is gone with nothing
#   proposed to replace it: every override that was RIGHT is lost and only
#   the wrong ones come back.
#
# * SO A RESET RUN PASSES --as-if-wiped, which reverts each division's
#   ratings to the scraped distance first and re-proposes the correct
#   override from the same evidence that justified it originally.
#
# ! ALL THREE PASSES OR NONE. Pass 2 chains onto pass 1's proposals and pass
#   3 chains onto both; running one of them against a different baseline than
#   the others produces proposals that disagree with each other and no error
#   anywhere. The flag is set once, here, for exactly that reason.
$asIf = @()
if ($Reset) {
    $asIf = @("--as-if-wiped")
    Write-Host "  -Reset: passes judge the ratings a wipe would produce" -ForegroundColor Cyan
}

$ErrorActionPreference = "Continue"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()

$stamp = Get-Date -Format "yyyy-MM-dd_HHmm"
$dir = "logs\$stamp-overrides"
New-Item -ItemType Directory -Force -Path $dir | Out-Null
Write-Host "  logging to $dir" -ForegroundColor Cyan

function Step($name, $cmd) {
    $log = "$dir\$name.log"
    $t0 = Get-Date
    Write-Host ""
    Write-Host ("=" * 70)
    Write-Host "  $name    $(Get-Date -Format 'HH:mm:ss')" -ForegroundColor Yellow
    Write-Host ("=" * 70)
    $global:LASTEXITCODE = 0
    & $cmd 2>&1 | Tee-Object -FilePath $log
    if ($LASTEXITCODE -ne 0) {
        Write-Host "`n  $name FAILED (exit $LASTEXITCODE) -- STOPPING." -ForegroundColor Red
        Write-Host "  Nothing after this ran. See $log" -ForegroundColor Red
        Write-Host "  corrections.py is unchanged unless 04_apply had already " -ForegroundColor Red
        Write-Host "  succeeded; check with: python scripts\apply_passes.py" -ForegroundColor Red
        exit 1
    }
    $mins = [math]::Round(((Get-Date) - $t0).TotalMinutes, 1)
    Write-Host "  $name ok  ($mins min)" -ForegroundColor Green
}

$t_start = Get-Date

# ! THE BACKUP FIRST, ALWAYS. It is hash-verified and it is the only copy of
#   the uncommitted override work on this disk.
Step "01_backup"   { python scripts\backup_corrections.py }

# ★ WHAT IS THERE NOW, so 05c_lost can say what tonight removed. The wipe
#   trusts the passes to re-propose every correct override, and a division
#   whose evidence deleted itself is exactly where that trust fails -- NLC
#   Round #1 2025 was page-verified to 5000 in July, wiped by the 2026-08-25
#   reset, re-proposed by nothing, and found weeks later on a broken page.
Step "01c_snapshot" { python scripts\override_diff.py --snapshot }

# ---------------------------------------------------------------------- #
#  THE STALE PASS BLOCK, CHECKED FIRST RATHER THAN LAST
# ---------------------------------------------------------------------- #
#
# * apply_passes REFUSES TO STACK TWO BLOCKS, correctly -- two sets of
#   proposals for the same division in one file is a silent argument decided
#   by whichever .update() runs last.
#
# ⚠ BUT IT FOUND OUT AT STEP 3, AFTER 28 MINUTES OF PASSES. Nothing was wrong
#   with the passes and nothing they wrote was lost; the run simply stopped
#   at the first thing that touches corrections.py, which is the last step
#   before the pipeline. The check costs a second and belongs up here.
#
# ! AND IT DOES NOT DELETE ANYTHING BY DEFAULT. A pass block is generated and
#   reversible, so removing it is the prescribed lifecycle -- but a stale one
#   can also carry _RESULT_DROP entries that ARE live (drops are additive;
#   the wipe's restate only rebinds the distance dict), so clearing it really
#   does change what the file resolves to. -Replace says you mean it.
# ! Select-String, NOT Get-Content -Raw. corrections.py is 52 MB; -Raw pulls
#   the whole thing into memory to answer a yes/no question. -Quiet streams
#   and stops at the first hit.
$hasBlock = Select-String -Path engine\corrections.py -SimpleMatch -Quiet `
                          -Pattern "# === PASS PROPOSALS (scripts/apply_passes.py) ==="
if ($hasBlock) {
    if ($Replace) {
        Step "01b_unapply" { python scripts\apply_passes.py --undo --write }
    } else {
        Write-Host ""
        Write-Host "  corrections.py already carries a pass block from an earlier run." -ForegroundColor Red
        Write-Host "  apply_passes will refuse to stack a second one, so this would have" -ForegroundColor Red
        Write-Host "  failed at step 03 after ~28 minutes of passes. Stopping now instead." -ForegroundColor Red
        Write-Host ""
        Write-Host "  Remove it and re-run:" -ForegroundColor Yellow
        Write-Host "    python scripts\apply_passes.py --undo --write"
        Write-Host "    .\run_overrides.ps1 -Reset"
        Write-Host ""
        Write-Host "  Or let this script do it: .\run_overrides.ps1 -Reset -Replace" -ForegroundColor Yellow
        Write-Host "  The backup above is your floor either way." -ForegroundColor Yellow
        Write-Host ""
        exit 1
    }
}

Step "02_pass0"    { python scripts\find_dropped_divisions.py --min-survivors 2 --out pass0.py }
Step "02_pass1"    { python scripts\rebuild_overrides.py --pass 1 --sigma $Sigma @asIf --out pass1.py }
Step "02_pass2"    { python scripts\rebuild_overrides.py --pass 2 --sigma $Sigma @asIf --out pass2.py }
# ! --limit 20000, NOT THE DEFAULT 400. The default sizes an interactive
#   eyeball run; the SQL fetches the WORST rows first, and a real corpus has
#   more than 400 above the bar. The 2026-08-23 unattended run proved it:
#   274 group + 76 median + 50 drops + 0 saves = exactly 400 -- saturated,
#   with the whole +90 half-time-corruption band below the fetch horizon.
#   20,000 matches apply_passes' pass-3 cap, so nothing legitimate truncates.
Step "02_pass3"    { python scripts\rebuild_overrides.py --pass 3 --sigma $Sigma @asIf --limit 20000 --out pass3.py }
# Pass 4: mixed divisions -- per-result pins to OTHER races witnessed at the
# same meet (the group-fault queue's fix). Runs after 3 for log order only;
# both stage passes 1+2 themselves.
Step "02_pass4"    { python scripts\rebuild_overrides.py --pass 4 --sigma $Sigma @asIf --out pass4.py }

# ⚠ THE VALIDATION GATE. apply_passes execs every proposal against throwaway
#   dicts and refuses on a syntax error, an absurd distance, or a volume past
#   its cap -- BEFORE corrections.py is touched. Running it dry first means
#   the log carries the counts even when the write goes ahead.
if ($ForceApply) {
    # Validation still RUNS (the counts belong in the log), but its
    # verdict cannot end the night: --force carries past the caps.
    Step "03_validate" { python scripts\apply_passes.py --force }
} else {
    Step "03_validate" { python scripts\apply_passes.py }
}

if ($DryRun) {
    Write-Host "`n  -DryRun: nothing written. Read $dir\03_validate.log" -ForegroundColor Cyan
    exit 0
}

# ⚠ THE WIPE IS WHAT MAKES -Reset A RESET, AND IT WAS MISSING. --as-if-wiped
#   makes the passes JUDGE as if the old overrides were gone, and apply
#   APPENDS what they re-propose -- but nothing ever removed the old
#   entries. A wrong old override is precisely the one that survives that:
#   reverted for judging, the division looks fine at its scraped distance,
#   nothing is proposed, and the absence of a proposal leaves the bad entry
#   standing (Thetford's confirmed-bad 6000-on-a-5k rode through the whole
#   2026-08-24 rebuild this way). wipe_overrides appends a .clear() BEFORE
#   the pass block is appended, keeps the sole-source entries (nothing else
#   carries a distance for those divisions), and is idempotent -- a wipe
#   block already in place is left alone.
if ($Reset) {
    Step "03b_wipe" { python scripts\wipe_overrides.py --write --keep-sole-source }
}

if ($ForceApply) {
    Step "04_apply"    { python scripts\apply_passes.py --write --force }
} else {
    Step "04_apply"    { python scripts\apply_passes.py --write }
}

# ⚠ NOT A PIPELINE STEP, WHICH IS WHY IT IS HERE. dist_override is rebuilt
#   from corrections.py by this script alone; without it the backfill reads
#   the old table and the night is wasted.
Step "05_dump"     { python engine\dump_overrides.py }

# ★ THE LAST THING CHECKABLE BEFORE FOUR HOURS ARE SPENT. Every other override
#   audit measures a written distance against the RATINGS, and the ratings on
#   disk are still the OLD ones until 05_backfill runs -- so none of them can
#   be believed in this window. This one asks whether the overrides agree with
#   EACH OTHER, which needs no rebuild: a venue runs one course, so sibling
#   divisions at it have one distance.
#
# ! REPORT ONLY, NEVER A GATE. A venue legitimately running three distances is
#   common (Van Cortlandt runs 2500, 4023 and 5000), so a split is a prompt to
#   read, not a fault. It sorts the genuinely suspect ones to the top.
Step "05b_coherence" { python scripts\check_override_coherence.py --worst 30 }

# ! REPORT ONLY, like the coherence check. Compares the fresh dist_override
#   against 01c's snapshot and NAMES every override the night lost, with a
#   paste-ready block to restore them -- while there is still time to do it
#   before 05_backfill normalizes the corpus without them.
Step "05c_lost"    { python scripts\override_diff.py --report }

if ($SkipPipeline) {
    Write-Host "`n  -SkipPipeline: overrides are applied and dist_override is" -ForegroundColor Cyan
    Write-Host "  rebuilt, but nothing downstream has been recomputed yet." -ForegroundColor Cyan
    Write-Host "  Run .\run_pipeline.ps1 to take it live." -ForegroundColor Cyan
    exit 0
}

# ★ THE FULL PIPELINE, NOT run_ratings.ps1. Distances change normalized_time,
#   which is upstream of the pack and the solve, so 05_backfill has to run.
Write-Host "`n  starting the full pipeline (~4h). Distances feed the pack, so" -ForegroundColor Cyan
Write-Host "  05_backfill must recompute normalized_time -- run_ratings.ps1" -ForegroundColor Cyan
Write-Host "  would skip it and rebuild from the OLD distances." -ForegroundColor Cyan
Step "06_pipeline" { .\run_pipeline.ps1 }

$total = [math]::Round(((Get-Date) - $t_start).TotalMinutes, 1)
Write-Host "`n$('=' * 70)"
Write-Host "  DONE -- $total min total" -ForegroundColor Green
Write-Host ("=" * 70)
Write-Host "  In the morning:"
Write-Host "    python scripts\check_race_monotone.py --races 20000"
Write-Host "    python scripts\find_dropped_divisions.py --meet 26359"
Write-Host "    python scripts\diag_rating_jumps.py --gap 30"
Write-Host "  Undo:"
Write-Host "    python scripts\apply_passes.py --undo --write"
Write-Host "    python engine\dump_overrides.py"
Write-Host "    .\run_pipeline.ps1"
