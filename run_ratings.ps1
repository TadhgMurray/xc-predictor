# run_ratings.ps1 -- the RATINGS-ONLY rerun.
#
# ★ WHAT THIS IS FOR. A change to how a rating is COMPUTED from an already
#   solved model -- resultRatings, the tilt, a pool mean -- does not change the
#   pack and does not change the solve. run_pipeline.ps1 rebuilds both anyway,
#   because it is the full rebuild and that is its job. This script is the
#   other case: the model is fine, the arithmetic on top of it changed.
#
#   Measured on the 2026-08 run, the steps this skips:
#
#       07_pack       39.0 min   reads the database, writes packed_XC_TF.npz
#       08 solve       9.0 min   365 CG iterations over 56.2M rows
#
#   Forty-eight minutes, for work whose inputs did not move.
#
# ⚠ AND THE ONE THING THAT MAKES IT SAFE IS THE FINGERPRINT, NOT THIS COMMENT.
#   pair_validate._fingerprint keys the solve cache on row count, cell count,
#   group count, min_degree and the three column sums -- so if anything
#   upstream of y HAS moved, the key changes and the solve re-runs on its own.
#   The cache cannot hand back a stale delta for a corpus it did not solve.
#
#   What it will NOT notice is a changed FORM correction that leaves sum(y)
#   intact -- pair_validate says so itself. If you touched rust_fitness,
#   pair_engine.buildResponse, or the pack writer, use run_pipeline.ps1.
#
# ⚠ NOT FOR: a scrape, a backfill, a grade_fix, a pool change, a new season, or
#   anything that adds or edits ROWS. Those change the pack. Full rebuild.
#
#   ok here:  resultRatings, apply_tilt, pool means, ceilings, the board
#             builders, ranking_results columns, panels.
#   NOT here: rust_fitness, buildResponse, the pack writer, normalize_distance,
#             backfill_normalize, grade_sanity, pro_flag, season_year.
#
# Usage:
#     .\run_ratings.ps1              # 08 -> 13
#     .\run_ratings.ps1 -From 10     # just the site (boards only)
#     .\run_ratings.ps1 -Force       # run even if the caches are missing
#                                    # (08 will re-pack-read and re-solve)

param(
    [int]$From = 8,
    [switch]$Force
)

# See run_pipeline.ps1 for why this is Continue and not Stop: python writes
# ordinary progress to stderr, and 2>&1 would turn every line into a
# terminating error.
$ErrorActionPreference = "Continue"

# ⚠ UTF-8, OR A PRINT KILLS THE RUN. Same reason as run_pipeline.ps1 -- the
#   pipe makes python fall back to cp1252, which has no ⚠, and 08_golive died
#   on exactly that six hours in.
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()

$stamp = Get-Date -Format "yyyy-MM-dd_HHmm"
$dir   = "logs\$stamp-ratings"
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$all   = "$dir\pipeline.log"


# ------------------------------------------------------------------ #
#  PREFLIGHT
# ------------------------------------------------------------------ #

Write-Host "preflight..." -ForegroundColor Cyan

# ★ THE CACHES ARE THE WHOLE POINT, so their absence is reported rather than
#   silently costing 48 minutes. Not fatal -- 08 rebuilds what it needs -- but
#   you should know before you go to bed, because the run is 48 minutes longer
#   than the estimate below.
$pack  = "engine\data\packed_XC_TF.npz"
$solve = "engine\data\pair_solve_cache.npz"
$missing = @()
foreach ($f in @($pack, $solve)) {
    if (-not (Test-Path $f)) { $missing += $f }
}
if ($missing.Count -and $From -le 8) {
    Write-Host "  ⚠ MISSING, so this run does NOT save the time it advertises:" -ForegroundColor Yellow
    $missing | ForEach-Object { Write-Host "      $_" -ForegroundColor Yellow }
    if (-not $Force) {
        Write-Host "`n    Either run .\run_pipeline.ps1 (full rebuild), or pass -Force" -ForegroundColor Yellow
        Write-Host "    to run anyway -- 08 will read the database and re-solve." -ForegroundColor Yellow
        exit 1
    }
    Write-Host "    -Force given; continuing." -ForegroundColor Yellow
} elseif ($From -le 8) {
    $mb = [math]::Round((Get-Item $pack).Length / 1MB)
    $age = [math]::Round(((Get-Date) - (Get-Item $pack).LastWriteTime).TotalHours, 1)
    Write-Host "  pack   $pack  (${mb} MB, ${age}h old)" -ForegroundColor Green
    Write-Host "  solve  $solve  -- CG will be skipped on a fingerprint match" -ForegroundColor Green
}

# ! THE RATING CHAIN'S OWN TEST, RUN BEFORE THE NIGHT IS SPENT ON IT. It needs
#   no database and takes under a second, and it fails if anything
#   athlete-specific has got back into the effective difficulty -- which is
#   the class of bug that made a race's ratings stop sorting by time.
Write-Host "  checking the rating chain..." -ForegroundColor Cyan
& python scripts\check_rating_monotone.py 2>&1 | Out-File -Encoding utf8 "$dir\00_monotone.log"
if ($LASTEXITCODE -ne 0) {
    Write-Host "`n  refusing to start -- scripts\check_rating_monotone.py FAILED." -ForegroundColor Red
    Get-Content "$dir\00_monotone.log" | ForEach-Object { Write-Host "    $_" -ForegroundColor Red }
    exit 1
}
Write-Host "    ratings sort by time within a race" -ForegroundColor Green

# ⚠ AND KILL THE DEV SERVER -- see run_pipeline.ps1. app.py reloads on file
#   changes and holds pooled connections; build_ranking_results died with
#   "server closed the connection unexpectedly" while it was running.
$flask = Get-CimInstance Win32_Process -Filter "Name like '%python%'" -ErrorAction SilentlyContinue |
         Where-Object { $_.CommandLine -like "*app.py*" }
if ($flask) {
    Write-Host "  ⚠ racecast\app.py is running (pid $($flask.ProcessId -join ', '))" -ForegroundColor Yellow
    Write-Host "    stop it before starting."
    exit 1
}
Write-Host "  dev server not running" -ForegroundColor Green
Write-Host "  logging to $dir" -ForegroundColor Cyan


# ------------------------------------------------------------------ #
#  ONE STEP -- identical to run_pipeline.ps1; see its notes.
# ------------------------------------------------------------------ #

function Step($num, $name, $cmd) {
    if ($num -lt $From) {
        Write-Host "`n  -- $name skipped (-From $From)" -ForegroundColor DarkGray
        return
    }
    $log = "$dir\$name.log"
    $t0  = Get-Date
    Write-Host ""
    Write-Host ("=" * 70)
    Write-Host "  $name    $(Get-Date -Format 'HH:mm:ss')" -ForegroundColor Yellow
    Write-Host ("=" * 70)
    "`n$('=' * 70)`n  $name  started $(Get-Date -Format 'HH:mm:ss')`n$('=' * 70)" |
        Out-File -Append -Encoding utf8 $all

    # ! RESET FIRST -- $LASTEXITCODE persists from the previous native command.
    $global:LASTEXITCODE = 0
    & $cmd 2>&1 | Tee-Object -FilePath $log | Tee-Object -Append -FilePath $all
    if ($LASTEXITCODE -ne 0) {
        Write-Host "`n  $name FAILED (exit $LASTEXITCODE) -- stopping." -ForegroundColor Red
        "  $name FAILED (exit $LASTEXITCODE)" | Out-File -Append -Encoding utf8 $all
        Write-Host "  see $log"
        exit 1
    }
    $mins = [math]::Round(((Get-Date) - $t0).TotalMinutes, 1)
    Write-Host "  $name ok  ($mins min)" -ForegroundColor Green
    "  $name ok ($mins min)" | Out-File -Append -Encoding utf8 $all
}


$t_start = Get-Date

# ! NO 06_clear_cache. That step is the whole difference between this script
#   and run_pipeline.ps1: deleting the two npz files is what forces the 39
#   minute re-pack and the 9 minute re-solve.
#
# ! AND NO 07_pack. linkage_check reads engine\data\packed_XC_TF.npz directly
#   (see its __main__), so the pack from the last full run is what 08 loads.
Step 8  "08_golive"   { python engine\linkage_check.py --golive --split }
Step 9  "09_tilt"     { python engine\apply_tilt.py --refresh --write }
Step 10 "10_rankings" { python racecast\build_ranking_results.py }
# ! AFTER RANKINGS -- build_team_season reads athlete_season, which
#   build_ranking_results writes. See run_pipeline.ps1.
Step 11 "11_teams"    { python racecast\build_team_season.py }
Step 12 "12_courses"  { python racecast\build_course_rank.py }
Step 13 "13_panels"   { python racecast\panels.py }


# ------------------------------------------------------------------ #
#  THE NUMBERS WORTH READING IN THE MORNING
# ------------------------------------------------------------------ #
Write-Host "`n$('=' * 70)"
Write-Host "  KEY NUMBERS" -ForegroundColor Cyan
Write-Host ("=" * 70)

$keys = @(
    # did the solve cache actually hit? this is the 9 minutes.
    'cache hit', 'solve:',
    # the sport offset and the difficulty gap
    'sport recentre', 'difficulty gap', 'sport defaults',
    # the gate that could not fire, and the boards' own counts
    'UNCHECKED', 'anchor gate', 'pool ceiling',
    # where the time went -- see build_ranking_results' phase timers
    'phase', 'took', 'minutes',
    # and anything that went wrong quietly
    'Traceback', 'WARNING', 'unusable', '⚠'
)
Select-String -Path "$dir\*.log" -Pattern ($keys -join '|') |
    ForEach-Object { "  {0,-20} {1}" -f $_.Filename, $_.Line.Trim() } |
    Tee-Object -Append -FilePath $all

$total = [math]::Round(((Get-Date) - $t_start).TotalMinutes, 1)
Write-Host "`n  TOTAL $total min" -ForegroundColor Cyan
Write-Host "  logs in $dir"
