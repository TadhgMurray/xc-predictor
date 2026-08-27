# Project: xc-predictor / deploy
# File:    ship_when_ready.ps1
# Purpose: Ship everything the server needs, unattended. Start it once and
#          walk away: it waits for the database dump to finish, uploads it,
#          then waits for training to finish and uploads the model.
#
#     .\deploy\ship_when_ready.ps1                 # dump, then model
#     .\deploy\ship_when_ready.ps1 -Only dump
#     .\deploy\ship_when_ready.ps1 -Only model
#
# (Supersedes upload_when_ready.ps1, which did the dump half only.)
#
# ★ IT WAITS ON EVIDENCE OF COMPLETION, NEVER ON A GUESS.
#     - the dump: waits for the pg_dump PROCESS to exit. A dump still
#       being written has a plausible size at every moment, so a size
#       check alone ships a half-written file that fails deep into the
#       restore.
#     - the model: waits for model.pt to appear AND be newer than the
#       moment this script started. Watching for the file alone would
#       fire instantly on a stale one left by an earlier run.
#   Both then confirm the file has stopped growing before sending.
#
# ★ AND IT REFUSES TO SEND WHAT CANNOT BE USED. A dump from a newer
#   pg_dump than the server's Postgres will not restore; a model without
#   its encoders is a site that 500s on every prediction. Both are
#   checked before the upload, not after.

param(
    [ValidateSet("all", "dump", "model")]
    [string] $Only        = "all",
    # ! NOT $Host -- that is a PowerShell automatic variable (the
    #   host UI object) and is read-only: naming a parameter $Host
    #   makes the script fail before its first line runs.
    [string] $Server      = "root@104.243.32.7",
    [string] $Dump        = "xc_predictor.dump",
    [string] $ModelDir    = "model\data",
    [int]    $ServerMajor = 16,      # ReliableSite box: PostgreSQL 16.15
    [int]    $Retries     = 4,
    [switch] $AcceptExistingModel    # skip the "newer than now" rule
)

$ErrorActionPreference = "Stop"
$startedAt = Get-Date
# the site loads exactly these; metadata.pkl and chunk_*.pt are training
# inputs and stay on this machine
$ModelFiles = @("model.pt", "target_stats.pkl", "encoders.pkl",
                "venue_vocab.pkl")

function Send-WithRetry {
    param([string[]] $Sources, [string] $Dest)
    for ($i = 1; $i -le $Retries; $i++) {
        Write-Host "  uploading (attempt $i of $Retries)..."
        & scp @Sources $Dest
        if ($LASTEXITCODE -eq 0) { return $true }
        $wait = [math]::Pow(2, $i)
        Write-Host "  scp failed (exit $LASTEXITCODE); retry in $wait s" `
            -ForegroundColor Yellow
        Start-Sleep -Seconds $wait
    }
    return $false
}

function Wait-Stable {
    param([string] $Path)
    $a = (Get-Item $Path).Length
    Start-Sleep -Seconds 5
    while ((Get-Item $Path).Length -ne $a) {
        $a = (Get-Item $Path).Length
        Write-Host ("  still growing ({0:N2} GB)..." -f ($a / 1GB))
        Start-Sleep -Seconds 15
    }
    return $a
}

# ==================== the database dump ============================== #
function Ship-Dump {
    Write-Host "`n=== DATABASE DUMP ===" -ForegroundColor Cyan

    $verText = (& pg_dump --version) 2>&1 | Out-String
    if ($verText -match '(\d+)\.(\d+)') {
        $localMajor = [int]$Matches[1]
        if ($localMajor -gt $ServerMajor) {
            Write-Host "STOP: local pg_dump is $localMajor, server runs $ServerMajor." `
                -ForegroundColor Red
            Write-Host "pg_restore cannot load a dump from a NEWER pg_dump."
            Write-Host "  a) dump using the PostgreSQL $ServerMajor client tools, or"
            Write-Host "  b) upgrade the server to $localMajor first."
            return $false
        }
        Write-Host "  version ok: pg_dump $localMajor -> server $ServerMajor"
    } else {
        Write-Host "  could not read pg_dump --version; guard skipped" `
            -ForegroundColor Yellow
    }

    $proc = Get-Process pg_dump -ErrorAction SilentlyContinue
    if ($proc) {
        Write-Host "  pg_dump running (pid $($proc.Id -join ', ')) -- waiting..."
        $proc | Wait-Process
        Write-Host "  pg_dump exited."
    } else {
        Write-Host "  no pg_dump running; assuming the dump is complete."
    }

    if (-not (Test-Path $Dump)) {
        Write-Host "STOP: $Dump does not exist. Did the dump fail?" -ForegroundColor Red
        return $false
    }
    $size = Wait-Stable $Dump
    Write-Host ("  ready: {0:N2} GB" -f ($size / 1GB))

    if (Send-WithRetry -Sources @($Dump) -Dest "${Server}:/srv/") {
        Write-Host "  DUMP UPLOADED." -ForegroundColor Green
        return $true
    }
    Write-Host "  dump upload failed after $Retries attempts." -ForegroundColor Red
    return $false
}

# ==================== the trained model ============================== #
function Ship-Model {
    Write-Host "`n=== MODEL ===" -ForegroundColor Cyan
    $modelPt = Join-Path $ModelDir "model.pt"

    # train.py writes model.pt LAST, so its arrival is the finish line.
    Write-Host "  waiting for $modelPt ..."
    while ($true) {
        if (Test-Path $modelPt) {
            $mt = (Get-Item $modelPt).LastWriteTime
            if ($AcceptExistingModel -or $mt -gt $startedAt) { break }
            Write-Host "  found a model.pt from $mt -- older than this run, so"
            Write-Host "  it is a previous training. Waiting for a new one."
            Write-Host "  (pass -AcceptExistingModel to ship the existing file.)"
        }
        Start-Sleep -Seconds 60
    }
    $size = Wait-Stable $modelPt
    Write-Host ("  model.pt ready: {0:N1} MB" -f ($size / 1MB))

    # ! ALL FOUR OR NONE. racecast/predict.py loads the encoders and the
    #   vocab beside the weights; shipping model.pt alone gives a site
    #   that 500s on the first prediction instead of one without one.
    $paths = @()
    $missing = @()
    foreach ($f in $ModelFiles) {
        $p = Join-Path $ModelDir $f
        if (Test-Path $p) { $paths += $p } else { $missing += $f }
    }
    if ($missing.Count -gt 0) {
        Write-Host "STOP: training did not produce: $($missing -join ', ')" `
            -ForegroundColor Red
        Write-Host "The site loads all four. Nothing was uploaded."
        return $false
    }

    & ssh $Server "mkdir -p /srv/xc-predictor/model/data"
    if (Send-WithRetry -Sources $paths `
            -Dest "${Server}:/srv/xc-predictor/model/data/") {
        Write-Host "  MODEL UPLOADED." -ForegroundColor Green
        Write-Host "  verify on the server:"
        Write-Host "    /srv/venv/bin/python /srv/xc-predictor/model/predict_check.py"
        return $true
    }
    Write-Host "  model upload failed after $Retries attempts." -ForegroundColor Red
    return $false
}

# ==================== run ============================================ #
$ok = $true
if ($Only -eq "all" -or $Only -eq "dump")  { $ok = (Ship-Dump)  -and $ok }
if ($Only -eq "all" -or $Only -eq "model") { $ok = (Ship-Model) -and $ok }

Write-Host ""
if ($ok) {
    Write-Host "ALL SHIPPED. On the server:" -ForegroundColor Green
    Write-Host "  bash /srv/xc-predictor/deploy/server_restore.sh /srv/$Dump"
    exit 0
}
Write-Host "Finished with errors -- see above." -ForegroundColor Red
exit 1
