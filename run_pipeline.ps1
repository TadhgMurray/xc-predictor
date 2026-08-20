# run_pipeline.ps1 -- the full rebuild, logged, with a preflight that refuses
# to start on a stale checkout.
#
# ⚠ $ErrorActionPreference IS DELIBERATELY *NOT* "Stop".
#
#   With it set to Stop, `2>&1` from a native command turns every stderr write
#   into a terminating error. Python writes ordinary progress and warnings to
#   stderr -- psycopg2 notices, numpy warnings, the engine's own "⚠" lines --
#   so the pipeline would abort on the first harmless message and look like a
#   real failure. Failure is detected from the EXIT CODE instead, which is the
#   only thing that actually distinguishes a traceback from a warning.

$ErrorActionPreference = "Continue"

$stamp = Get-Date -Format "yyyy-MM-dd_HHmm"
$dir   = "logs\$stamp"
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$all   = "$dir\pipeline.log"


# ------------------------------------------------------------------ #
#  PREFLIGHT -- the check that would have caught last night
# ------------------------------------------------------------------ #
#
# ★ THE LAST RUN USED THE OLD CODE AND NOBODY KNEW UNTIL IT FINISHED. Flask
#   reported detecting changes to five engine files AFTER the pipeline was
#   done, which means they were copied in mid-run and the six hours before
#   that were spent on the previous versions.
#
#   So: prove the new code is present before spending the night on it. Each
#   check looks for a string that only exists in the edited file -- cheap,
#   specific, and it fails in two seconds rather than six hours.
$expect = @{
    "engine\grade_sanity.py"          = "bare_class"
    "engine\speed_ratings.py"         = "outside_pool_band"
    "engine\speed_ratings_db.py"      = "min_time: float = 200.0"
    "engine\normalize_distance.py"    = "def targetFor"
    "engine\pool_resolve.py"          = "THE PROMOTION GATES ARE GONE"
    "engine\pro_flag.py"              = "seasonYearSqlInt"
    "engine\season_year.py"           = "ACADEMIC_START_MONTH"
    "backfill\backfill_normalize.py"  = "_loadGradeFix"
    "racecast\build_ranking_results.py" = "prepareTfStateTemp"
    "racecast\panels.py"              = "THE TWO PROMOTION-GATE JOINS ARE GONE"
}

Write-Host "preflight..." -ForegroundColor Cyan
$stale = @()
foreach ($f in $expect.Keys | Sort-Object) {
    if (-not (Test-Path $f)) {
        $stale += "$f  MISSING"
    } elseif (-not (Select-String -Path $f -Pattern $expect[$f] -Quiet -SimpleMatch)) {
        $stale += "$f  STALE (no '$($expect[$f])')"
    }
}
if ($stale.Count) {
    Write-Host "`n  refusing to start -- these are not the edited files:" -ForegroundColor Red
    $stale | ForEach-Object { Write-Host "    $_" -ForegroundColor Red }
    exit 1
}
Write-Host "  all 10 files current" -ForegroundColor Green

# ⚠ AND KILL THE DEV SERVER. app.py reloads on every file change and holds
#   pooled connections; last night build_ranking_results died with "server
#   closed the connection unexpectedly" while it was running.
$flask = Get-CimInstance Win32_Process -Filter "Name like '%python%'" -ErrorAction SilentlyContinue |
         Where-Object { $_.CommandLine -like "*app.py*" }
if ($flask) {
    Write-Host "  ⚠ racecast\app.py is running (pid $($flask.ProcessId -join ', '))" -ForegroundColor Yellow
    Write-Host "    stop it before starting -- it reloads on file changes and holds connections."
    exit 1
}
Write-Host "  dev server not running" -ForegroundColor Green
Write-Host "  logging to $dir" -ForegroundColor Cyan


# ------------------------------------------------------------------ #
#  ONE STEP
# ------------------------------------------------------------------ #
#
# Per-step logs plus one combined. A single log makes finding the numbers a
# search problem; per-step files keep the ones that matter named and small,
# and pipeline.log reads top to bottom.
function Step($name, $cmd) {
    $log = "$dir\$name.log"
    $t0  = Get-Date
    Write-Host ""
    Write-Host ("=" * 70)
    Write-Host "  $name    $(Get-Date -Format 'HH:mm:ss')" -ForegroundColor Yellow
    Write-Host ("=" * 70)
    "`n$('=' * 70)`n  $name  started $(Get-Date -Format 'HH:mm:ss')`n$('=' * 70)" |
        Out-File -Append -Encoding utf8 $all

    # ! RESET FIRST. $LASTEXITCODE persists from the previous native command,
    #   so a step containing no native call -- step 05 is only Remove-Item --
    #   would otherwise be judged on the exit code of the step before it.
    $global:LASTEXITCODE = 0

    # 2>&1 folds stderr in so a traceback is captured, not merely printed to a
    # scrollback that is gone by morning.
    & $cmd 2>&1 | Tee-Object -FilePath $log | Tee-Object -Append -FilePath $all

    # ! THE EXIT CODE, NOT $?. $? is true whenever the pipeline itself ran,
    #   including when python exited non-zero on a traceback.
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

# ---- verdicts ------------------------------------------------------- #
# ! unlink.py IS NOT HERE ON PURPOSE. person_split already exists, and
#   unlink --write mints new person_ids from the CURRENT ones -- running it
#   twice would split the splits. It is the one step in this pipeline that is
#   not idempotent.
Step "01_season_year"  { python engine\season_year.py }
Step "02_drop_old"     { python engine\drop_old.py }
Step "03_pro_flag"     { python engine\pro_flag.py --skip-dist --write }
Step "04_grade_sanity" { python engine\grade_sanity.py --write }

# ! THE BACKFILL RUNS AFTER grade_sanity, NOT BEFORE. It resolves pools from
#   grade_fix, and since per-pool anchors a disagreement between its pooling
#   and the engine's writes normalized_time on the wrong SCALE -- measured at
#   a 64% rating error, frozen into the row.
Step "05_backfill"     { python backfill\backfill_normalize.py --sport both --apply }

# ---- pack and solve -------------------------------------------------- #
# Both caches must go: packed_XC_TF.npz is checked for existence only, and
# pair_solve_cache.npz fingerprints on sum(y), which does not notice a pool
# reassignment that leaves the sum intact.
Step "06_clear_cache"  {
    Remove-Item -ErrorAction SilentlyContinue engine\data\packed_XC_TF.npz
    Remove-Item -ErrorAction SilentlyContinue engine\data\pair_solve_cache.npz
    Write-Output "caches cleared"
}
Step "07_pack"         { python engine\speed_ratings.py --sport merged --cache --pack-only }

# ★ --split TURNS ON THE PER-ATHLETE SPORT OFFSET. Without it there is no beta
#   term, so the XC/TF gap has nowhere to go but alpha -- an athlete's ability
#   absorbs whether they happen to race more of one sport. The gap measures
#   ~5%, about 6 rating points at 120.
#
#   linkage_check's own note: "without recentring, minimum-norm CG leaves
#   delta orthogonal to the sport direction, which silently asserts XC and TF
#   have EQUAL mean difficulty. That is certainly false."
#
# ⚠ THIS IS THE ONE UNTESTED FLAG IN THE RUN. If the morning numbers look
#   wrong, drop --split and redo from this step only -- nothing before it
#   depends on the choice.
Step "08_golive"       { python engine\linkage_check.py --golive --split }

Step "09_tilt"         { python engine\apply_tilt.py --refresh --write }

# ---- the site -------------------------------------------------------- #
Step "10_rankings"     { python racecast\build_ranking_results.py }
# ! AFTER RANKINGS, BEFORE PANELS. build_team_season reads athlete_season,
#   which build_ranking_results writes -- run it first and it races last
#   run's athletes. It was in run_rest.ps1 and missing here, so a full
#   pipeline rebuilt every board except the team one, which then served a
#   season older than everything around it.
Step "11_teams"        { python racecast\build_team_season.py }
Step "12_panels"       { python racecast\panels.py }


# ------------------------------------------------------------------ #
#  THE NUMBERS WORTH READING IN THE MORNING
# ------------------------------------------------------------------ #
Write-Host "`n$('=' * 70)"
Write-Host "  KEY NUMBERS" -ForegroundColor Cyan
Write-Host ("=" * 70)

$keys = @(
    # grade_sanity's verdict block -- the new rules print here
    'corroborated grade', 'bare class word', 'level of the field',
    'nobody in the race graded', 'grade never advanced',
    'school grade after college', 'off the progression',
    'mixed seasons', 'numeric values rejected', 'written as',
    # the sport offset: bbar should land near -0.039
    'sport recentre', 'difficulty gap', 'sport defaults',
    # proof the new speed_ratings ran at all
    'census', 'outside_pool_band',
    # and anything that went wrong quietly
    'Traceback', 'WARNING', 'unusable'
)
Select-String -Path "$dir\*.log" -Pattern ($keys -join '|') |
    ForEach-Object { "  {0,-20} {1}" -f $_.Filename, $_.Line.Trim() } |
    Tee-Object -Append -FilePath $all

$total = [math]::Round(((Get-Date) - $t_start).TotalMinutes, 1)
Write-Host "`n  TOTAL $total min" -ForegroundColor Cyan
Write-Host "  logs in $dir"