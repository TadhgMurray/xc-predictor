# run_rest.ps1 -- wait for the grade_sanity already running, then do the rest.
#
# ★ IT WAITS RATHER THAN RE-RUNS. grade_sanity is an hour of work that is
#   already in flight; starting a second one would fight the first for the
#   same unlogged tables (allraces, gradekind, raceKinds are no longer TEMP,
#   so two sessions really would collide). This polls until grade_fix has
#   been rebuilt, then continues.
#
# ⚠ NOT $ErrorActionPreference = "Stop". With it set, `2>&1` turns every
#   stderr write from python into a TERMINATING error -- and these scripts
#   write ordinary progress and their own warning lines there. The run would
#   abort on a harmless message. Failure is judged on the EXIT CODE, which is
#   the only thing that distinguishes a traceback from a warning.

$ErrorActionPreference = "Continue"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

$stamp = Get-Date -Format "yyyy-MM-dd_HHmm"
$dir   = "logs\$stamp"
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$all   = "$dir\pipeline.log"


# ------------------------------------------------------------------ #
#  PREFLIGHT -- the check that would have caught two nights ago
# ------------------------------------------------------------------ #
#
# ★ A RUN ONCE SPENT SIX HOURS ON THE OLD CODE because the edited files were
#   copied in mid-run. Each check looks for a string that exists only in the
#   current version, and fails in two seconds instead.
$expect = @{
    "engine\grade_sanity.py"            = "raceKinds"
    "engine\speed_ratings.py"           = "grade_verdict"
    "engine\speed_ratings_db.py"        = "min_time: float = 200.0"
    "engine\normalize_distance.py"      = "def targetFor"
    "engine\pool_resolve.py"            = "no_evidence"
    "engine\pro_flag.py"                = "seasonYearSqlInt"
    "engine\season_year.py"             = "ACADEMIC_START_MONTH"
    "backfill\backfill_normalize.py"    = "_loadGradeFix"
    "racecast\build_ranking_results.py" = "prepareTfStateTemp"
}
$stale = @()
foreach ($f in $expect.Keys | Sort-Object) {
    if (-not (Test-Path $f)) { $stale += "$f  MISSING" }
    elseif (-not (Select-String -Path $f -Pattern $expect[$f] -Quiet -SimpleMatch)) {
        $stale += "$f  STALE (no '$($expect[$f])')"
    }
}
if ($stale.Count) {
    Write-Host "`n  refusing to start -- not the edited files:" -ForegroundColor Red
    $stale | ForEach-Object { Write-Host "    $_" -ForegroundColor Red }
    exit 1
}
Write-Host "  all 9 files current" -ForegroundColor Green

# ⚠ THE DEV SERVER RELOADS ON FILE CHANGES AND HOLDS POOLED CONNECTIONS. A
#   previous run died with "server closed the connection unexpectedly" while
#   it was up.
$flask = Get-CimInstance Win32_Process -Filter "Name like '%python%'" -ErrorAction SilentlyContinue |
         Where-Object { $_.CommandLine -like "*app.py*" }
if ($flask) {
    Write-Host "  ⚠ racecast\app.py is running (pid $($flask.ProcessId -join ', ')) -- stop it." -ForegroundColor Yellow
    exit 1
}


# ------------------------------------------------------------------ #
#  WAIT FOR grade_sanity
# ------------------------------------------------------------------ #
#
# ! IT WATCHES FOR THE NEW METHODS, NOT JUST FOR THE TABLE. grade_fix already
#   exists from the last run; its presence proves nothing. 'bare_field' is
#   written only by the pass added tonight, so seeing it means THIS run
#   finished and wrote.
# ★ IT WATCHES FOR THE PROCESS, NOT FOR A TABLE.
#
#   The first version polled grade_fix for rows with method 'bare_field',
#   reasoning that only tonight's new pass writes them. It skipped the wait
#   entirely -- because an EARLIER run tonight had already written 2.1M of
#   them. A marker that survives the thing it is meant to detect is not a
#   marker.
#
#   A running grade_sanity is unambiguous: it is either alive or it is not.
Write-Host "`n  waiting for grade_sanity to finish..." -ForegroundColor Cyan
$deadline = (Get-Date).AddHours(4)
$sawIt = $false
while ($true) {
    $gs = Get-CimInstance Win32_Process -Filter "Name like '%python%'" -ErrorAction SilentlyContinue |
          Where-Object { $_.CommandLine -like "*grade_sanity*" }
    if ($gs) {
        if (-not $sawIt) {
            Write-Host "  grade_sanity is running (pid $($gs.ProcessId -join ', ')) -- waiting" -ForegroundColor Yellow
            $sawIt = $true
        }
    } else {
        # ⚠ A GAP BEFORE IT EVER APPEARS IS ALSO POSSIBLE. If this script is
        #   started first, grade_sanity may not have launched yet -- so wait
        #   two minutes before believing an empty result means "done".
        if ($sawIt) {
            Write-Host "  grade_sanity finished" -ForegroundColor Green
            break
        }
        Start-Sleep -Seconds 120
        $gs2 = Get-CimInstance Win32_Process -Filter "Name like '%python%'" -ErrorAction SilentlyContinue |
               Where-Object { $_.CommandLine -like "*grade_sanity*" }
        if (-not $gs2) {
            Write-Host "  grade_sanity is not running -- proceeding" -ForegroundColor Green
            break
        }
    }
    if ((Get-Date) -gt $deadline) {
        Write-Host "  gave up after 4h" -ForegroundColor Red
        exit 1
    }
    Start-Sleep -Seconds 60
}

# ! AND IT MUST HAVE SUCCEEDED, NOT MERELY EXITED. A crashed grade_sanity
#   also stops being a running process.
$probe = @"
import sys
sys.path.insert(0, 'scripts'); sys.path.insert(0, 'engine')
try:
    from database import getConn
    with getConn() as c, c.cursor() as cur:
        cur.execute("SELECT to_regclass('grade_fix')")
        if cur.fetchone()[0] is None:
            print(0)
        else:
            cur.execute("SELECT count(*) FROM grade_fix")
            print(cur.fetchone()[0])
except Exception:
    print(-1)
"@
$probe | Out-File -Encoding utf8 "$dir\_probe.py"
$rows = (python "$dir\_probe.py" 2>&1 | Select-Object -Last 1)
if ($rows -notmatch '^\d+$' -or [int]$rows -lt 1000000) {
    Write-Host "  grade_fix has $rows rows -- grade_sanity did not finish cleanly." -ForegroundColor Red
    exit 1
}
Write-Host "  grade_fix holds $rows verdicts" -ForegroundColor Green


# ------------------------------------------------------------------ #
#  ONE STEP
# ------------------------------------------------------------------ #
function Step($name, $cmd) {
    $log = "$dir\$name.log"
    $t0  = Get-Date
    Write-Host ""
    Write-Host ("=" * 70)
    Write-Host "  $name    $(Get-Date -Format 'HH:mm:ss')" -ForegroundColor Yellow
    Write-Host ("=" * 70)
    "`n$('=' * 70)`n  $name  started $(Get-Date -Format 'HH:mm:ss')`n$('=' * 70)" |
        Out-File -Append -Encoding utf8 $all

    # ! RESET FIRST. $LASTEXITCODE persists from the last native command, so a
    #   step with no python call would be judged on the previous step's code.
    $global:LASTEXITCODE = 0
    & $cmd 2>&1 | Tee-Object -FilePath $log | Tee-Object -Append -FilePath $all

    # ! THE EXIT CODE, NOT $?. $? is true whenever the pipeline ran at all,
    #   including when python exited non-zero on a traceback. Three separate
    #   nights have now had a step run happily on a failed predecessor.
    if ($LASTEXITCODE -ne 0) {
        Write-Host "`n  $name FAILED (exit $LASTEXITCODE) -- stopping." -ForegroundColor Red
        "  $name FAILED (exit $LASTEXITCODE)" | Out-File -Append -Encoding utf8 $all
        exit 1
    }
    $mins = [math]::Round(((Get-Date) - $t0).TotalMinutes, 1)
    Write-Host "  $name ok  ($mins min)" -ForegroundColor Green
    "  $name ok ($mins min)" | Out-File -Append -Encoding utf8 $all
}

$t_start = Get-Date

# results_old blocks the merge AFTER the 14-minute rebuild, so clear it first.
Step "01_drop_old"  { python engine\drop_old.py }

# ! THE BACKFILL RUNS AFTER grade_sanity. It resolves pools from grade_fix,
#   and since per-pool anchors a disagreement between its pooling and the
#   engine's writes normalized_time on the wrong SCALE -- a 64% rating error,
#   frozen into the row.
Step "02_backfill"  { python backfill\backfill_normalize.py --sport both --apply }

# Both caches: packed_XC_TF.npz is checked for existence only, and
# pair_solve_cache.npz fingerprints on sum(y), which does not notice a pool
# reassignment that leaves the sum intact.
Step "03_clear"     {
    Remove-Item -ErrorAction SilentlyContinue engine\data\packed_XC_TF.npz
    Remove-Item -ErrorAction SilentlyContinue engine\data\pair_solve_cache.npz
    Write-Output "caches cleared"
}
Step "04_pack"      { python engine\speed_ratings.py --sport merged --cache --pack-only }

# ⚠ NO --split. It hardcodes ridge = 0.0 and stalled at relative residual
#   1.000e+00 on the first CG iteration -- it does not converge on this pack.
Step "05_golive"    { python engine\linkage_check.py --golive }

Step "06_tilt"      { python engine\apply_tilt.py --refresh --write }
Step "07_rankings"  { python racecast\build_ranking_results.py }
# Team boards: reads athlete_season, which 07 has just rebuilt, and writes
# team_season. BEFORE panels, so the two never disagree about a season.
Step "08_teams"     { python racecast\build_team_season.py }
# Course board: reads course_difficulties, which 04_pack wrote.
Step "09_courses"   { python racecast\build_course_rank.py }
Step "10_panels"    { python racecast\panels.py }


# ------------------------------------------------------------------ #
#  THE NUMBERS WORTH READING IN THE MORNING
# ------------------------------------------------------------------ #
Write-Host "`n$('=' * 70)"
Write-Host "  KEY NUMBERS" -ForegroundColor Cyan
Write-Host ("=" * 70)

$keys = @('outside_pool_band', 'census', 'unknown_pool', 'sport defaults',
          'held-out', 'kept', 'wrote', 'dropped', 'Traceback', 'WARNING')
Select-String -Path "$dir\*.log" -Pattern ($keys -join '|') |
    ForEach-Object { "  {0,-18} {1}" -f $_.Filename, $_.Line.Trim() } |
    Tee-Object -Append -FilePath $all

$total = [math]::Round(((Get-Date) - $t_start).TotalMinutes, 1)
Write-Host "`n  TOTAL $total min" -ForegroundColor Cyan
Write-Host "  logs in $dir"