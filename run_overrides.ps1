# run_overrides.ps1 -- rebuild the overrides and take them live, unattended.
#
#     .\run_overrides.ps1
#     .\run_overrides.ps1 -Reset         # after a wipe -- see -Reset below
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

Step "02_pass0"    { python scripts\find_dropped_divisions.py --min-survivors 2 --out pass0.py }
Step "02_pass1"    { python scripts\rebuild_overrides.py --pass 1 --sigma $Sigma @asIf --out pass1.py }
Step "02_pass2"    { python scripts\rebuild_overrides.py --pass 2 --sigma $Sigma @asIf --out pass2.py }
Step "02_pass3"    { python scripts\rebuild_overrides.py --pass 3 --sigma $Sigma @asIf --out pass3.py }

# ⚠ THE VALIDATION GATE. apply_passes execs every proposal against throwaway
#   dicts and refuses on a syntax error, an absurd distance, or a volume past
#   its cap -- BEFORE corrections.py is touched. Running it dry first means
#   the log carries the counts even when the write goes ahead.
Step "03_validate" { python scripts\apply_passes.py }

if ($DryRun) {
    Write-Host "`n  -DryRun: nothing written. Read $dir\03_validate.log" -ForegroundColor Cyan
    exit 0
}

Step "04_apply"    { python scripts\apply_passes.py --write }

# ⚠ NOT A PIPELINE STEP, WHICH IS WHY IT IS HERE. dist_override is rebuilt
#   from corrections.py by this script alone; without it the backfill reads
#   the old table and the night is wasted.
Step "05_dump"     { python engine\dump_overrides.py }

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
