# Project: xc-predictor / deploy
# File:    upload_when_ready.ps1
# Purpose: Wait for pg_dump to finish, then upload the dump. Run it in a
#          second PowerShell window the moment the dump starts.
#
#     .\deploy\upload_when_ready.ps1
#     .\deploy\upload_when_ready.ps1 -Target root@1.2.3.4:/srv/
#
# ! SUPERSEDED by ship_when_ready.ps1, which does this AND waits for
#   training to finish and uploads the model beside it. This one still
#   works and is safe to leave running; use the other for a fresh start.
#
# ! THE VERSION GUARD RUNS FIRST, AND IT IS THE POINT. pg_restore will
#   not load a dump written by a NEWER pg_dump than the server's own
#   Postgres. Discovering that after a two-hour upload is the expensive
#   way to learn it, so this refuses to send at all in that case -- and
#   says exactly what to do instead.

param(
    [string]$Dump        = "xc_predictor.dump",
    [string]$Target      = "root@104.243.32.7:/srv/",
    [int]   $ServerMajor = 16,     # ReliableSite box: PostgreSQL 16.15
    [int]   $Retries     = 4
)

$ErrorActionPreference = "Stop"

# ---- 1. version guard ------------------------------------------------ #
$verText = (& pg_dump --version) 2>&1 | Out-String
if ($verText -match '(\d+)\.(\d+)') { $localMajor = [int]$Matches[1] }
else { Write-Host "could not read pg_dump --version; skipping guard" -ForegroundColor Yellow
       $localMajor = 0 }

if ($localMajor -gt $ServerMajor) {
    Write-Host ""
    Write-Host "STOP: local pg_dump is $localMajor, the server runs $ServerMajor." -ForegroundColor Red
    Write-Host "pg_restore cannot load a dump from a NEWER pg_dump. Options:"
    Write-Host "  a) dump with the server's major version -- install the"
    Write-Host "     PostgreSQL $ServerMajor client tools and use that pg_dump.exe"
    Write-Host "  b) upgrade the SERVER to $localMajor before restoring"
    Write-Host "Nothing was uploaded."
    exit 1
}
if ($localMajor -gt 0) {
    Write-Host "version ok: local pg_dump $localMajor -> server $ServerMajor"
}

# ---- 2. wait for the dump to finish ---------------------------------- #
# Waits on the PROCESS, not the file: a dump still being written has a
# plausible size at every moment, and uploading a half-written one
# produces a restore that fails deep into the load.
$proc = Get-Process pg_dump -ErrorAction SilentlyContinue
if ($proc) {
    Write-Host "pg_dump is running (pid $($proc.Id -join ', ')) -- waiting..."
    $proc | Wait-Process
    Write-Host "pg_dump exited."
} else {
    Write-Host "no pg_dump running; assuming the dump is already complete."
}

if (-not (Test-Path $Dump)) {
    Write-Host "STOP: $Dump does not exist. Did the dump fail?" -ForegroundColor Red
    exit 1
}

# belt: the file must stop growing before we trust it
$a = (Get-Item $Dump).Length
Start-Sleep -Seconds 5
$b = (Get-Item $Dump).Length
if ($a -ne $b) {
    Write-Host "STOP: $Dump is still growing. Something is still writing it." -ForegroundColor Red
    exit 1
}
Write-Host ("dump ready: {0:N2} GB" -f ($b / 1GB))

# ---- 3. upload, retrying on network failure -------------------------- #
for ($i = 1; $i -le $Retries; $i++) {
    Write-Host "uploading (attempt $i of $Retries) -- this is the long part..."
    & scp $Dump $Target
    if ($LASTEXITCODE -eq 0) {
        Write-Host ""
        Write-Host "UPLOADED. Now, on the server:" -ForegroundColor Green
        Write-Host "  bash /srv/xc-predictor/deploy/server_restore.sh /srv/$Dump"
        exit 0
    }
    $wait = [math]::Pow(2, $i)
    Write-Host "scp failed (exit $LASTEXITCODE); retrying in $wait s..." -ForegroundColor Yellow
    Start-Sleep -Seconds $wait
}
Write-Host "upload failed after $Retries attempts." -ForegroundColor Red
exit 1
