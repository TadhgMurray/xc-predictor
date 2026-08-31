# Project: xc-predictor / deploy
# File:    db_tunnel.ps1
# Purpose: Hold an ssh tunnel from this workstation to the server's Postgres,
#          restart it when it drops, and SAY SO when it does.
#
#     .\deploy\db_tunnel.ps1                      # 127.0.0.1:5433 -> server 5432
#     .\deploy\db_tunnel.ps1 -RemotePort 5433     # when the server moved clusters
#     .\deploy\db_tunnel.ps1 -NoProbe             # skip the server-side check
#
# Leave it running in its own window. Then scripts/config_local.py points at
# 127.0.0.1:5433 and every tool in the tree -- the engine, the overrides, and
# racecast/app.py run locally -- reaches the server's corpus with no further
# configuration.
#
# ★ 5433 LOCALLY, ON PURPOSE, AND THE DIGIT IS THE WHOLE SAFETY ARGUMENT.
#   config.py's shipped default is 127.0.0.1:5432, which is THIS MACHINE'S
#   cluster. Forwarding onto 5432 would make "which corpus am I querying" a
#   question about whether a background window is still alive, and every
#   --write, drop_old.py and wipe_overrides.py in the tree takes its
#   connection without asking. With the forward on 5433 the rule is fixed and
#   memorable: 5432 is mine, 5433 is the server.
#
# ★ AND THE PORT IS NOT OPENED ON THE SERVER TO MAKE THIS WORK. server_setup.sh
#   allows OpenSSH and Nginx through ufw and nothing else, and Postgres listens
#   on localhost. That is correct and stays: the alternative is the postgres
#   superuser doing password auth across the public internet, which would then
#   owe itself TLS on the Postgres socket as well. A tunnel needs none of it.
#
# ⚠ THIS IS FOR INSPECTION, THE SITE, AND THE OVERRIDE TOOLS -- NOT THE
#   PIPELINE. build_ranking_results streams 61.6M rows in and COPYs them back
#   out and takes 15-25 minutes ON THE BOX; through an ssh channel it is bound
#   by the tunnel, and a drop mid-COPY means re-running the step. Pipeline
#   work belongs in an `ssh root@<server>` shell.
#
# ! NOT $Host FOR A PARAMETER NAME. It is a PowerShell automatic variable (the
#   host UI object) and read-only -- naming a parameter $Host makes the script
#   fail before its first line runs. ship_when_ready.ps1 records the same scar.

param(
    [string] $Server     = "root@104.243.32.7",
    [int]    $LocalPort  = 5433,
    # The far end of the forward, as resolved ON THE SERVER -- so 127.0.0.1
    # here means the server's own loopback, not this machine's.
    [string] $RemoteHost = "127.0.0.1",
    [int]    $RemotePort = 5432,
    [switch] $NoProbe
)

$ErrorActionPreference = "Stop"

Write-Host "== db_tunnel ==" -ForegroundColor Cyan
Write-Host "  127.0.0.1:$LocalPort  ->  ${Server}:$RemoteHost`:$RemotePort"

# ------------------------------------------------------------------ #
#  PREFLIGHT
# ------------------------------------------------------------------ #

# ⚠ A SECOND TUNNEL ON THE SAME PORT IS THE COMMONEST FAILURE AND THE HARDEST
#   TO SEE. ssh still authenticates and holds the session open; only the bind
#   fails. Without ExitOnForwardFailure (set below) you get a window that
#   looks perfectly alive and forwards nothing. Refusing to start is clearer
#   than competing.
$held = Get-NetTCPConnection -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue
if ($held) {
    $owner = (Get-Process -Id $held[0].OwningProcess -ErrorAction SilentlyContinue).ProcessName
    Write-Host "STOP: 127.0.0.1:$LocalPort is already held by $owner (pid $($held[0].OwningProcess))." `
        -ForegroundColor Red
    Write-Host "  Another tunnel is probably already up -- use it, or close it first." `
        -ForegroundColor Yellow
    exit 1
}

# ★ ASK THE SERVER WHAT IS ACTUALLY LISTENING BEFORE BLAMING THE TUNNEL.
#   server_pg_major.sh moves clusters between ports, and a forward onto a port
#   nothing serves fails as "channel N: open failed: connect failed" -- which
#   reads like a network fault and is a version-migration leftover.
if (-not $NoProbe) {
    Write-Host "  probing the server's listeners..."
    $listeners = (& ssh -o BatchMode=yes $Server "ss -lntp 2>/dev/null | grep -E ':543[0-9]' || true") | Out-String
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  could not reach $Server over ssh (exit $LASTEXITCODE)." -ForegroundColor Red
        Write-Host "  BatchMode is on, so this also fails if key auth is not set up:" -ForegroundColor Yellow
        Write-Host "    ssh -v $Server `"hostname`"   # want: Authenticated ... using `"publickey`"" `
            -ForegroundColor Yellow
        exit 1
    }
    if ($listeners.Trim()) {
        $listeners.Trim().Split("`n") | ForEach-Object { Write-Host "    $_" }
        if ($listeners -notmatch ":$RemotePort\b") {
            Write-Host "  WARNING: nothing is listening on $RemotePort there." -ForegroundColor Yellow
            Write-Host "  Re-run with -RemotePort <the port above>." -ForegroundColor Yellow
        }
    } else {
        Write-Host "  WARNING: no postgres listener found at all on 5432-5439." -ForegroundColor Yellow
    }
}

# ------------------------------------------------------------------ #
#  THE LOOP
# ------------------------------------------------------------------ #
#
# ! THE FOUR OPTIONS ARE ALL LOAD-BEARING.
#     ExitOnForwardFailure  -- ssh dies if the bind fails instead of holding a
#                              connected session that forwards nothing.
#     ServerAliveInterval   -- a dropped link is DETECTED. Without it the
#                              session hangs open and queries stall rather
#                              than erroring, which is far worse than a
#                              clean failure.
#     ServerAliveCountMax   -- 3 misses (90s) and it gives up so we can restart.
#     BatchMode             -- never sit at a prompt. Key auth is set up; if it
#                              regresses we want an exit code, not a script
#                              waiting silently for a password nobody is
#                              watching for.
$sshArgs = @(
    "-N",
    "-o", "ExitOnForwardFailure=yes",
    "-o", "ServerAliveInterval=30",
    "-o", "ServerAliveCountMax=3",
    "-o", "BatchMode=yes",
    "-L", "${LocalPort}:${RemoteHost}:${RemotePort}",
    $Server
)

Write-Host "  connecting (Ctrl+C to stop)" -ForegroundColor Cyan
$backoff = 2
while ($true) {
    $openedAt = Get-Date
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] tunnel up on 127.0.0.1:$LocalPort" `
        -ForegroundColor Green

    # ! ssh -N PRINTS NOTHING AND DOES NOT RETURN WHILE IT IS WORKING. Silence
    #   here is the healthy state; the next line only runs once it has died.
    & ssh @sshArgs
    $code = $LASTEXITCODE
    $lived = [int]((Get-Date) - $openedAt).TotalSeconds

    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] tunnel DOWN after ${lived}s (ssh exit $code)" `
        -ForegroundColor Red

    # A session that lived a while was healthy; the drop is a network event
    # and the next attempt should be immediate. A session that died at once
    # is a configuration fault and hammering it helps nobody.
    if ($lived -ge 60) { $backoff = 2 }
    Write-Host "  reconnecting in ${backoff}s..." -ForegroundColor Yellow
    Start-Sleep -Seconds $backoff
    $backoff = [math]::Min($backoff * 2, 30)
}
