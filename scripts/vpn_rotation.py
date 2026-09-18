# Project: xc-predictor
# Author: Tadhg Murray
# Subset: Scraper
# Date: 6/2/2026
# File Title: vpn_rotation.py
# Purpose: Manages automatic Mullvad VPN server rotation during scraping.
#          Rotates the VPN server every 200 meets per session or on a
#          consecutive failure streak of 20+, whichever comes first.
#          Coordinates rotation across all sessions
#          so only one rotates at a time while others wait.

import asyncio
import time
import subprocess
import platform
import os
import glob
import random

# Path to the Mullvad CLI on Windows.
MULLVAD_CLI = r"C:\Program Files\Mullvad VPN\resources\mullvad.exe"

# ─── Server List ─────────────────────────────────────────────────────────────

# List of Mullvad server locations to rotate through.
# Format is "country city" as used by the Mullvad CLI.
# Rotate through different continents to maximize IP diversity.
MULLVAD_LOCATIONS = [
    # United States — most options, athletic.net is a US site so these
    # tend to have the least latency and best success rates
    # "us nyc",   # New York
    # "us lax",   # Los Angeles
    # "us chi",   # Chicago
    # "us dal",   # Dallas
    # "us mia",   # Miami
    # "us sea",   # Seattle
    # "us atl",   # Atlanta
    # "us den",   # Denver
    # "us hou",   # Houston
    # "us phx",   # Phoenix
    # "us slc",   # Salt Lake City
    # "us det",   # Detroit
    # "us bos",   # Boston
    # "us was",   # Washington DC
    # "us sjc",   # San Jose
    # "us sec",   # Secaucus NJ (new — from server list)
    # "us ash",   # Ashburn VA (new — from server list)
    # "us ral",   # Raleigh NC (new — from server list)
    # "us mca",   # McAllen TX (new — from server list)
    # "us kan",   # Kansas City (new — from server list)

    # ─── Western Europe ───
    # "gb lon",   # London
    # # "gb glw",   # Glasgow
    # "gb mnc",   # Manchester
    # "ie dub",   # Dublin
    # "fr par",   # Paris
    # "fr mrs",   # Marseille
    # "fr bod",   # Bordeaux
    # "nl ams",   # Amsterdam
    # "be bru",   # Brussels
    # "lu lux",   # Luxembourg

    # # # ─── Central Europe ───
    # "de fra",   # Frankfurt
    # "de ber",   # Berlin
    # "de dus",   # Dusseldorf
    # "ch zrh",   # Zurich
    # "at vie",   # Vienna
    # "cz prg",   # Prague
    # "pl waw",   # Warsaw
    # "hu bud",   # Budapest
    # "sk bts",   # Bratislava

    # # # ─── Northern Europe ───
    # "se sto",   # Stockholm
    # "se got",   # Gothenburg
    # "se mma",   # Malmo
    # "no osl",   # Oslo
    # "no svg",   # Stavanger
    # "dk cph",   # Copenhagen
    # "fi hel",   # Helsinki
    # "ee tll",   # Tallinn
    # "lv rix",   # Riga
    # "lt vno",   # Vilnius

    # # ─── Southern Europe ───
    # "es mad",   # Madrid
    # "es bcn",   # Barcelona
    # "es vlc",   # Valencia
    # "pt lis",   # Lisbon
    # "it mil",   # Milan
    # "it rom",   # Rome
    # "it pmo",   # Palermo
    # "gr ath",   # Athens
    # "hr zag",   # Zagreb
    # "rs beg",   # Belgrade
    # "ro buh",   # Bucharest
    # "bg sof",   # Sofia

    # Canada — confirmed working overnight
    "ca tor",   # Toronto
    "ca van",   # Vancouver
    "ca mon",   # Montreal (new — visible in server list as ca-mon)

    # Latin America — confirmed working overnight, minus bad codes
    # "br sao",   # Sao Paulo
    # "br for",   # Fortaleza (new — from server list)
    "ar bue",   # Buenos Aires
    "co bog",   # Bogota
    "pe lim",   # Lima
    "mx que",   # Queretaro Mexico (new — server list shows mx-que, not mx-mex)
    "cl san",   # Santiago (new — server list confirms cl-san exists)

    # Asia Pacific — confirmed working overnight
    "jp tyo",   # Tokyo
    "jp osa",   # Osaka
    "sg sin",   # Singapore
    "au syd",   # Sydney
    "au mel",   # Melbourne
    "au per",   # Perth
    "au adl",   # Adelaide (new — from server list)
    "au bne",   # Brisbane (new — from server list)
    "hk hkg",   # Hong Kong
    "nz akl",   # Auckland
    # "th bkk",   # Bangkok
    # "my kul",   # Kuala Lumpur
    # "ph mnl",   # Manila (new — from server list)
    "id jkt",   # Jakarta (new — from server list)

    # Middle East / Africa — confirmed working overnight
    "il tlv",   # Tel Aviv
    "za jnb",   # Johannesburg
    "ng lag",   # Lagos (new — from server list)
]

# ─── Constants ───────────────────────────────────────────────────────────────

# Total meets across ALL sessions before rotating.
# 25 sessions × ~3-5s per meet = ~5-8 meets/second across all sessions.
# 3000 total meets = ~6-10 minutes per IP — aggressive but safe.
WINDOWS_GLOBAL_MEETS_PER_ROTATION  = 1000


# How many seconds to spread the post-rotation PAGE RELOADS across. After a real
# rotation every session reloads its page to refresh Cloudflare clearance on the
# new IP (see _reloadWithCooldown in launcher.py); doing all 200 at once is the
# main cause of the post-rotation 1015 burst. waitForTunnel sleeps each session to
# its slot (index * 90/total) so the reloads ramp evenly over this window —
# ~2.2 sessions/second at 200 sessions.
POST_ROTATION_STAGGER_SECONDS = 270


# Path to the proxy executable.
PROXY_PATH = r"C:\Users\Tadhg Murray\cloud-sql-proxy\cloud-sql-proxy.exe"

# The instance connection string tells the proxy which Cloud SQL
# instance to connect to and which local port to listen on.
PROXY_INSTANCE = "project-d8c4b484-c8fa-4e09-9fc:us-west1:free-trial-first-project=tcp:5432"

# How long to wait after a rotation before allowing ANOTHER rotation.
# The post-rotation 429 burst is EXPECTED and self-resolves in ~30-60s
# (see context doc). Without this cooldown, every session independently
# hits its own consecutive-failure threshold during that burst and each
# tries to rotate again — which restarts the burst — which makes every
# session fail again — an infinite loop. This cooldown breaks the loop
# by making rotate() a no-op (not a failure) while a recent rotation
# is still settling.
ROTATION_COOLDOWN_SECONDS = 90

# ─── VPNRotator ──────────────────────────────────────────────────────────────

# VPNRotatorWindows
# Purpose: Manages VPN server rotation across all scraping sessions on
#          Windows, using the Mullvad CLI. One instance is shared by
#          all sessions in launcher.py.
class VPNRotatorWindows:
    
    # __init__
    # Purpose: This is the constructor(what __init__ does). It sets
    #          up the initial state.
    # Arguments:
    #           self: the current instance of the class.
    # Output: None.
    def __init__(self):

        # Index of the current server in MULLVAD_LOCATIONS.
        self.current_index = 0

        # asyncio.Lock ensures only one session can rotate at
        # a time. Other sessions that call rotate() while the
        # lock is held will wait until the rotation completes before continuing
        # and will then later be stopped from rotating.
        self.lock = asyncio.Lock()

        # Timestamp of the last rotation. Used for logging.
        self.last_rotation_time = time.time()

        # Track how many rotations have happened total.
        self.rotation_count = 0

        # Copy the global list into an instance variable so we can
        # remove blocked servers without modifying the original.
        # list() creates a new list from the existing one — if we just
        # did self.locations = MULLVAD_LOCATIONS, removing from
        # self.locations would also remove from MULLVAD_LOCATIONS
        # because they'd point to the same list object.
        self.locations = list(MULLVAD_LOCATIONS)

        # Global meet counter — incremented by every session after every
        # successful meet. Protected by the lock so concurrent increments
        # don't cause race conditions.
        self.global_meets_since_rotation = 0

        # Incremented every rotation. Each session tracks which generation
        # it last staggered for — only stagger once per rotation, not on
        # every call when meets_since_rotation happens to be 0.
        self.rotation_generation = 0

        # tunnel_ready is an asyncio.Event — a simple green/red light
        # that all sessions share.
        #
        # Set (green) = tunnel is up, sessions can make network requests.
        # Cleared (red) = rotation in progress, sessions must pause.
        #
        # Starts SET because when the scraper first launches, Mullvad
        # is already connected — no rotation is in progress, so sessions
        # should be able to start scraping immediately without waiting.
        self.tunnel_ready = asyncio.Event()
        self.tunnel_ready.set()


    # ── Private helpers ───────────────────────────────────────────────────────\


    # runMullvadCommand
    # Purpose: Runs a Mullvad CLI command synchronously using subprocess.
    #          We use subprocess.run (not async) because rotation happens
    #          while holding the lock — other sessions are already waiting,
    #          so blocking is fine here.
    # Arguments:
    #           self: current instance of the class.
    #           args: list of command arguments e.g. ["relay", "set", "location", "us nyc"].
    # Output: Return true if the commands succeeded, False if it failed.
    async def _runMullvadCommand(self, args: list) -> bool:

        try:
            # Runs an external program from Python, the Mullvad CLI.
            # Like typing a command in the terminal with the first arg
            # being the command, and the other two mean print output here
            # as raw bytes.
            result = subprocess.run(
                [MULLVAD_CLI] + args,
                capture_output = True,
                text = True
            )

            # Every program exits with a return code when it finishes, 0
            # means a success. If it's not 0 it failed so return the args
            # that caused it to fail as a list, and retunr the error
            # output.
            if result.returncode != 0:
                print(f"[VPN] Command failed: {' '.join(args)}")
                print(f"[VPN] Error: {result.stderr.strip()}")
                return False
            
            return True
        
        except Exception as e:
            print(f"[VPN] Exception running mullvad command: {e}")
            return False
        
    # _verifyConnectivity
    # Purpose: After a connect, confirm the tunnel actually routes traffic by
    #          curling TFRRS. `mullvad connect` returning success only means the
    #          CLI command ran — NOT that the tunnel established and traffic
    #          flows. This is the check whose absence let a dead tunnel be
    #          accepted as "rotation complete" and strand every session.
    # Arguments:
    #           self: current instance.
    # Output:   True if curl got any HTTP response, False on timeout/no route.
    async def _verifyConnectivity(self) -> bool:
        try:
            # -s silent, -o NUL discard body (Windows' /dev/null is NUL),
            # -w prints just the status code, --max-time caps the wait.
            result = subprocess.run(
                ["curl", "--max-time", "8", "-s", "-o", "NUL",
                 "-w", "%{http_code}", "https://www.tfrrs.org"],
                capture_output=True, text=True, timeout=12,
            )
            code = result.stdout.strip()
            # "000" = curl couldn't connect (no route/DNS); "" = nothing.
            # Any real HTTP code (200, 403, even a Cloudflare 503) proves the
            # tunnel carries traffic — that's all we're checking.
            return code not in ("", "000")
        except Exception:
            # subprocess timeout or curl missing -> treat as no connectivity.
            return False
    
    # _connectToLocation
    # Purpose: Sets the relay location and connects to the VPN on that server.
    # Arguments:
    #           self: the instance of this class.
    #           location: a string like "us nyc" from MULLVAD_LOCATIONS which is
    #                     the server location to switch to.
    # Output: Returns True if both CLI calls succeed, False otherwise.
    async def _connectToLocation(self, location: str) -> bool:

        # The CLI expects location as separate tokens:
        # `mullvad relay set location us nyc`
        # location.split() turns "us nyc" → ["us", "nyc"]
        location_parts = location.split()

        # Step 1 — tell Mullvad which server to use next.
        success = await self._runMullvadCommand(["relay", "set", "location"] + location_parts)
        if not success:
            print(f"[VPN] Failed to set relay location to {location}")
            return False
 
        # Step 2 — reconnect on the new server.
        success = await self._runMullvadCommand(["connect"])
        if not success:
            print(f"[VPN] Failed to connect to {location}")
            return False
 
        # Step 3 — wait for the tunnel to fully establish before
        # any session resumes scraping. 10s is conservative but safe.
        print(f"[VPN] Waiting 10s for tunnel to establish on {location}...")
        time.sleep(10)
 
        return True
    
    # _restartProxy
    # Purpose: Kills any running cloud-sql-proxy process and starts a fresh one.
    #          Called after every VPN rotation since the proxy loses its Cloud SQL
    #          connection when Mullvad switches servers.
    # Arguments:
    #           self: current instance.
    # Output: True if proxy started successfully, False if it failed.
    def _restartProxy(self) -> bool:

        # Step 1 — kill any existing proxy process.
        # taskkill is a Windows command that terminates a process by name.
        # /F means force kill (don't wait for graceful shutdown).
        # /IM means match by image name (the executable filename).
        # We ignore errors here — if no proxy is running, taskkill fails
        # but that's fine, we just want to make sure it's not running.
        subprocess.run(
            ["taskkill", "/F", "/IM", "cloud-sql-proxy.exe"],
            capture_output=True  # suppress output — failure is expected if not running
        )

        # Brief pause to let the port 5432 free up after killing the old process.
        time.sleep(2)

        # Step 2 — start a fresh proxy process.
        # subprocess.Popen (not subprocess.run) starts a process in the
        # background without waiting for it to finish. We want the proxy
        # to keep running while the scraper runs, so we don't wait.
        # subprocess.run would block here forever since the proxy never exits.
        try:
            subprocess.Popen(
                [PROXY_PATH, f"-instances={PROXY_INSTANCE}"],
                stdout=subprocess.DEVNULL,  # discard proxy's stdout
                stderr=subprocess.DEVNULL   # discard proxy's stderr
            )
        except Exception as e:
            print(f"[VPN] Failed to start proxy: {e}")
            return False

        # Wait for the proxy to finish starting up and begin accepting
        # connections on localhost:5432 before scraping resumes.
        print(f"[VPN] Waiting 5s for proxy to start...")
        time.sleep(5)

        print(f"[VPN] Proxy restarted")
        return True


    # _advanceIndex
    # Purpose: Advances to the next index in the server list.
    # Arguments:
    #           self: the instance of this class.
    # Output: None, change the current index of the class index to the next index.
    async def _advanceIndex(self):
        self.current_index = (self.current_index + 1) % len(self.locations)
    
    # ── Public API ────────────────────────────────────────────────────────────

    # How long to pause ALL sessions when one session looks "stuck" —
    # i.e. GetResultsData3 is 429ing past the point where normal retries
    # should have recovered it. This is separate from a full rotation:
    # we try waiting first, since sometimes the IP just needs a breather,
    # and only rotate if waiting doesn't fix it (see scrape_results.py).
    STUCK_SESSION_PAUSE_SECONDS = 60

    # _pauseAllSessions
    # Purpose: Pauses every session scraper-wide for a fixed number of
    #          seconds, then resumes them. Reuses the SAME tunnel_ready
    #          Event that rotate() uses — sessions calling waitForTunnel()
    #          don't know or care WHY the tunnel is paused, only that it
    #          is. This is what lets us pause-without-rotating using
    #          machinery that already exists, instead of inventing a
    #          second coordination primitive.
    # Arguments:
    #           self: current instance.
    #           seconds: how long to keep all sessions paused.
    # Output: None.
    async def _pauseAllSessions(self, seconds: int):
 
        # Clearing tunnel_ready makes every session currently sitting in
        # (or about to call) waitForTunnel() suspend there — same as
        # during a real rotation, except we're not actually touching
        # Mullvad or the proxy.
        self.tunnel_ready.clear()
 
        await asyncio.sleep(seconds)
 
        # Re-open the gate. Sessions waiting in waitForTunnel() wake up
        # and proceed (with their usual post-rotation stagger, since we
        # don't bump rotation_generation here — see note below).
        self.tunnel_ready.set()

    # handleStuckSession
    # Purpose: Called by a session when GetResultsData3 has failed too
    #          many times in a row (see CONSECUTIVE_FAILURE_THRESHOLD in
    #          scrape_results.py) — a pattern that looks like a dead IP
    #          rather than ordinary rate-limit noise. Pauses every
    #          session for STUCK_SESSION_PAUSE_SECONDS so nobody keeps
    #          hammering a possibly-dead IP while we wait to see if it
    #          recovers on its own.
    #
    #          IMPORTANT: this method does NOT retry the failed call and
    #          does NOT rotate. It only pauses-then-resumes. The caller
    #          (scrape_results.py) is responsible for retrying the
    #          specific event/div once the pause is over, and for
    #          deciding to call rotate() if that retry also fails. This
    #          keeps vpn_rotation.py sport/endpoint-agnostic — it has no
    #          idea what GetResultsData3 is, and shouldn't need to.
    # Arguments:
    #           self: current instance.
    #           label: session label for logging, e.g. "[Session 7]".
    # Output: None.
    async def handleStuckSession(self, label: str):
 
        print(
            f"[VPN] {label} reporting a stuck session (repeated 429s) — "
            f"pausing ALL sessions for {self.STUCK_SESSION_PAUSE_SECONDS}s "
            f"before allowing a retry"
        )
 
        await self._pauseAllSessions(self.STUCK_SESSION_PAUSE_SECONDS)
 
        print(f"[VPN] {label} pause complete — resuming, caller will retry")
    

    # waitForTunnel
    # Purpose: Called by each session before every network request
    #          (i.e., before scrapeMeetBySport). If a rotation is in
    #          progress (tunnel_ready is cleared), this suspends the
    #          calling session until the new tunnel is confirmed up —
    #          WITHOUT blocking the event loop (other sessions' non-
    #          network code can still run while this one waits).
    #          After tunnel_ready fires, adds a small per-session
    #          random stagger before returning, so all waiting sessions
    #          don't pile onto the new IP simultaneously.
    # Arguments:
    #           self: current instance.
    #           index: this session's index (0-99). Used to spread the
    #                  post-rotation stagger — higher-index sessions
    #                  wait slightly longer on average, distributing
    #                  the resume burst across ~POST_ROTATION_STAGGER_SECONDS
    #                  rather than all hitting the new IP at once.
    #           total_sessions: the total number of sessions running, used
    #                           for pausing after rotating
    # Output: None. Returns when the tunnel is ready and this session's
    #         stagger has elapsed.
    async def waitForTunnel(self, index: int, last_generation_seen: int, 
                            total_sessions: int):

        # Wait for tunnel if rotation in progress.
        await self.tunnel_ready.wait()

        # If no NEW rotation happened since this session last returned from
        # waitForTunnel, there is nothing to stagger around — return at once.
        # This is the guard the old comment described but the code never did.
        if self.rotation_generation == last_generation_seen:
            return self.rotation_generation

        # Only stagger if we actually just waited through a NEW rotation.
        # If rotation_generation hasn't advanced since this session last
        # checked, no stagger needed — just return immediately.
        slot_duration  = POST_ROTATION_STAGGER_SECONDS / total_sessions
        my_wait        = index * slot_duration

        await asyncio.sleep(my_wait)

        # Return new generation so caller doesn't stagger again next call.
        return self.rotation_generation

    # recordMeet
    # Purpose: Called by each session after every successful meet to
    #          increment the global counter. Returns True if the counter
    #          has hit the rotation threshold, so the caller knows to rotate.
    # Arguments:
    #           self: current instance.
    # Output: True if rotation threshold reached, False otherwise.
    # Note: Does NOT rotate itself — just increments and signals.
    #       The caller (checkRotation) decides whether to rotate.
    async def recordMeet(self) -> bool:

        # We use the lock here so two sessions don't increment simultaneously
        # and both think they're the one to trigger rotation.
        async with self.lock:
            self.global_meets_since_rotation += 1
            return self.global_meets_since_rotation >= WINDOWS_GLOBAL_MEETS_PER_ROTATION
        
    # rotate
    # Purpose: Rotates to the next Mullvad server location. Only one session
    #          can rotate at a time - others wait for the lock to be released.
    # Arguments:
    #           self: current instance of this class.
    #           label: session lable for printed output e.g. "[Session 1]"
    #           reason: why rotation was triggered e.g. "meet count" or "failure streak".
    # Output: Returns true if rotation succeeded, False if it failed.
    async def rotate(self, label: str, reason: str) -> bool:
        # no tunnel to move; report no change so the caller does not
        # restart the browser expecting a new IP
        if self.disabled:
            return False


        # Snapshot the rotation count before we wait for the lock.
        # If it changes while we wait, another session already rotated.
        count_before = self.rotation_count

        # self.lock stops any other sessions from running rotate instantly.
        # They have to wait in the queue until the first session is done, and
        # they will then be stopped rotating.
        async with self.lock:

            # If another session already rotated while we were waiting,
            # skip this rotation — the IP is already fresh.
            # We check by comparing rotation_count before and after acquiring the lock.
            if self.rotation_count != count_before:
                print(f"[VPN] {label} skipping rotation — already rotated by another session")
                return True
            
            # NEW: cooldown check. self.rotation_count > 0 guards the very
            # first rotation ever (last_rotation_time is just the init
            # timestamp then, not a real rotation) so we don't accidentally
            # block startup.
            seconds_since_last = time.time() - self.last_rotation_time
            if self.rotation_count > 0 and seconds_since_last < ROTATION_COOLDOWN_SECONDS:
                print(
                    f"[VPN] {label} rotation requested ({reason}) but only "
                    f"{seconds_since_last:.0f}s since last rotation — within "
                    f"{ROTATION_COOLDOWN_SECONDS}s cooldown, skipping. "
                    f"This is likely the expected post-rotation 429 burst."
                )
                return True
            
            # NEW: clear tunnel_ready BEFORE starting teardown.
            # From this moment, any session that calls waitForTunnel()
            # will pause here — they won't fire requests on the dying
            # connection or pile onto the new IP before it's ready.
            # Sessions already mid-request will finish their current
            # request (we can't cancel in-flight awaits), but no NEW
            # requests start until we set() below.
            self.tunnel_ready.clear()
            print(f"[VPN] {label} tunnel paused — sessions will wait for new IP")

            # Try servers until one connects AND verifies. Without this loop, a
            # single dead server would open the gate onto a tunnel that carries
            # no traffic and strand every session.
            connected = False
            for _ in range(len(self.locations)):
                await self._advanceIndex()
                new_location = self.locations[self.current_index]
                print(f"[VPN] {label} rotating → {new_location} "
                      f"(reason: {reason}, rotation #{self.rotation_count + 1})")

                # _connectToLocation: set relay + connect + wait. (Proxy step
                # already stripped — local-first Postgres, no proxy.)
                if not await self._connectToLocation(new_location):
                    print(f"[VPN] {label} connect failed on {new_location} — next")
                    continue

                # The load-bearing new check: does traffic actually flow?
                if await self._verifyConnectivity():
                    connected = True
                    break
                print(f"[VPN] {label} {new_location} connected but no traffic "
                      f"— trying next server")

            if not connected:
                # Every server failed. Re-open the gate anyway so sessions aren't
                # frozen forever; they'll error on the dead tunnel and trigger
                # another rotation. (Better than a permanent stall.)
                print(f"[VPN] {label} ALL servers failed to verify — releasing "
                      f"gate onto best-effort tunnel")
                self.tunnel_ready.set()
                return False

            # Update shared state.
            self.rotation_count += 1
            self.rotation_generation += 1
            self.last_rotation_time = time.time()
 
            # Reset the meet counter — we're on a fresh IP now.
            self.global_meets_since_rotation = 0

            # NEW: set tunnel_ready AFTER proxy restart confirms the
            # new IP is working. _connectToLocation already does the
            # 10s tunnel wait + proxy restart — by this point the new
            # IP is confirmed ready. All sessions waiting in
            # waitForTunnel() will now wake up and begin their stagger.
            self.tunnel_ready.set()

            print(f"[VPN] Rotation complete. Now on {new_location}")

            return True

    # checkRotation
    # Purpose: Called by each se4ssion after ever meet. Checks if rotation should
    #          be triggered based on global meet count across all sessions.
    # Arguments:
    #           self: current instance of this class.
    #           label: session label for printed output.
    # Output: Returns True if a rotation happened, False otherwise.
    async def checkRotation(self, label: str) -> bool:
        if self.disabled:
            return False

        
        threshold_reached = await self.recordMeet()
 
        if threshold_reached:
            return await self.rotate(label, "meet count threshold")
 
        return False
    
    # removeCurrentServer
    # Purpose: NO-OP (was: permanently pop the current server from the pool). Kept
    #          as a method so _handleCloudflareBlock's existing call site is
    #          unaffected — it just does nothing now. The 90s rotation cooldown in
    #          rotate() is the sole throttle; servers are never destroyed, so the
    #          pool can't be drained by a 429 burst.
    # Arguments:
    #           self: current instance.
    # Output:   None.
    def removeCurrentServer(self):
        if self.disabled:
            return

        # Intentionally does nothing. See file header for why removing servers on
        # every Cloudflare block drained the pool 46 -> 1. If you ever want
        # bad-server pruning back, gate it behind the same 90s cooldown rotate()
        # uses (only prune when an actual rotation fires), never per-block.
        return

# ============================================================
# LINUX (VM) — wg-quick / network namespace rotator
# ============================================================

# Directory holding all the .conf files downloaded from Mullvad.
# wg-quick names interfaces after the filename (minus .conf), and
# this whole launcher process runs inside `ip netns exec mullvad`,
# so wg-quick commands here automatically operate on that namespace.
#
# ⚠ IT WAS "~/xc-predictor/wireguard", A HOME-RELATIVE PATH BAKED INTO A
#   REPO THAT DOES NOT LIVE IN HOME (owner, 2026-09-18: the launcher died
#   with "No .conf files found in /root/xc-predictor/wireguard" while the
#   checkout sits in /srv/xc-predictor). The deploy moved and this constant
#   did not, so the whole scrape could not start.
#
# ! SEARCHED, IN ORDER, AND THE ERROR NAMES EVERY PLACE IT LOOKED. An
#   explicit WIREGUARD_DIR wins; then the directory beside this checkout,
#   which is where the confs belong for a repo-relative deploy; then the old
#   home path, so an existing box keeps working untouched.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

WIREGUARD_CANDIDATES = [
    os.environ.get("WIREGUARD_DIR") or "",
    os.path.join(_REPO_ROOT, "wireguard"),
    os.path.expanduser("~/xc-predictor/wireguard"),
    "/etc/wireguard",
]


def _wireguardDir():
    """The first candidate that holds at least one .conf, else the first
    that exists, else the repo-relative default."""
    for path in WIREGUARD_CANDIDATES:
        if path and glob.glob(os.path.join(path, "*.conf")):
            return path
    for path in WIREGUARD_CANDIDATES:
        if path and os.path.isdir(path):
            return path
    return os.path.join(_REPO_ROOT, "wireguard")


WIREGUARD_DIR = _wireguardDir()
 
# Rotate proactively every this many meets (summed across all sessions).
LINUX_GLOBAL_MEETS_PER_ROTATION = 1000
 
# How long to wait after bringing a new tunnel up for the handshake
# to complete before traffic starts flowing again.
HANDSHAKE_WAIT_SECONDS = 3

# How long to wait for `wg-quick up`/`wg-quick down` before giving up
# on this config. Real wg-quick calls complete in 1-3s normally —
# 20s is generous headroom for a slow-but-working server, while still
# being short enough that a hang only costs ~20s, not 8+ minutes.
WG_QUICK_TIMEOUT_SECONDS = 20

# How long to wait for the connectivity-check curl. Short — we only
# need to know the tunnel can reach the internet at all, not that
# athletic.net returns real content (a Cloudflare 403 still proves
# the tunnel works).
CONNECTIVITY_CHECK_TIMEOUT_SECONDS = 8

 
# _runWgQuick
# Purpose: Runs `wg-quick <direction> <conf_path>` as a subprocess,
#          with a timeout. If the process doesn't finish within
#          WG_QUICK_TIMEOUT_SECONDS, kills it and returns False instead
#          of hanging forever. This is the single place both _bringDown
#          and _bringUp go through, so the timeout logic exists once.
# Arguments:
#           direction: "up" or "down" — passed straight to wg-quick.
#           conf_path: path to the .conf file describing the interface.
# Output: True if wg-quick exited with code 0 within the timeout,
#         False if it failed OR timed out.
async def _runWgQuick(direction: str, conf_path: str) -> bool:
 
    # Start the subprocess without waiting for it yet — this returns
    # immediately with a handle (proc) we can wait on or kill.
    # Launches wq-quick as a separate OS process. 
    # This is equivalent to running wg-quick direction conf_path,
    # down tears down a wireguard interfrace, up creates one.
    proc = await asyncio.create_subprocess_exec(
        "wg-quick", direction, conf_path,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
 
    try:
        # asyncio.wait_for races proc.wait() against a timer. If
        # proc.wait() finishes first, we get its result normally. If
        # the timeout fires first, wait_for raises TimeoutError and
        # CANCELS the proc.wait() coroutine — but the underlying OS
        # process is still running, so we have to kill it ourselves
        # in the except block below.
        await asyncio.wait_for(proc.wait(), timeout=WG_QUICK_TIMEOUT_SECONDS)
 
    except asyncio.TimeoutError:
        print(f"[VPN] wg-quick {direction} timed out after "
              f"{WG_QUICK_TIMEOUT_SECONDS}s for "
              f"{os.path.basename(conf_path)} — killing it")
 
        # proc.kill() sends SIGKILL — immediate, no cleanup. We don't
        # care about wg-quick's own cleanup at this point; we just
        # need our process tree to not be stuck.
        proc.kill()
 
        # After kill(), the process becomes a zombie until something
        # reads its exit status. await proc.wait() here is now safe —
        # the process is dead, so this returns almost instantly (it's
        # NOT the same hang as before, because the process can no
        # longer ignore us).
        await proc.wait()
 
        return False
 
    # No exception means proc.wait() returned normally within the
    # timeout — check its exit code the normal way.
    return proc.returncode == 0

# _interfaceName
# Purpose: Derives the interface name wg-quick uses from a .conf path —
#          wg-quick names interfaces after the filename minus ".conf".
# Arguments:
#           conf_path: e.g. ".../us-atl-wg-001.conf"
# Output: e.g. "us-atl-wg-001"
def _interfaceName(conf_path: str) -> str:
    # basename strips the directory; [:-5] strips ".conf" (5 chars)
    return os.path.basename(conf_path)[:-5]


# _interfaceExists
# Purpose: Checks whether a network interface with this name currently
#          exists, by asking the kernel directly via `ip link show`.
#          This is how we detect interfaces left behind by a killed
#          wg-quick process — wg-quick's own exit code can't tell us.
# Arguments:
#           name: interface name, e.g. "us-atl-wg-001"
# Output: True if it exists, False otherwise.
async def _interfaceExists(name: str) -> bool:

    proc = await asyncio.create_subprocess_exec(
        "ip", "link", "show", "dev", name,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    await proc.wait()

    # ip link show exits 0 if the device exists, non-zero otherwise.
    return proc.returncode == 0

# _forceRemoveInterface
# Purpose: Deletes a network interface directly via `ip link delete`,
#          bypassing wg-quick entirely. Used when wg-quick down
#          failed/timed out but the interface is still present —
#          this is the fallback that actually removes it.
# Arguments:
#           name: interface name to delete.
# Output: None. Errors are swallowed — if it's already gone, this
#         fails harmlessly.
async def _forceRemoveInterface(name: str):

    print(f"[VPN] Force-removing stale interface {name}")

    proc = await asyncio.create_subprocess_exec(
        "ip", "link", "delete", name,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    await proc.wait()

# _bringDown
# Purpose: Tears down a WireGuard interface that's currently up.
# Arguments:
#           conf_path: path to the .conf file describing the interface.
# Output: None. Errors are swallowed — fine if nothing was up yet.
async def _bringDown(conf_path: str):
    
    name = _interfaceName(conf_path)

    await _runWgQuick("down", conf_path)

    # Verifies the wireguard interface is teared down.
    # If not, it force removes it.
    if await _interfaceExists(name):
        await _forceRemoveInterface(name)

# _bringUp
# Purpose: Brings up a new WireGuard interface from a .conf file.
# Arguments:
#           conf_path: path to the .conf file describing the interface.
# Output: True if wg-quick succeeded, False otherwise.
async def _bringUp(conf_path: str) -> bool:
 
    return await _runWgQuick("up", conf_path)

# _verifyConnectivity
# Purpose: After a tunnel comes up, confirms it actually routes traffic
#          by curling a known URL. wg-quick exiting 0 only means the
#          interface and routes were CONFIGURED — not that the
#          handshake succeeded or the exit IP is reachable. This check
#          was the missing piece that let a dead tunnel be accepted as
#          "rotation complete" before.
# Arguments: None.
# Output: True if curl got any HTTP response, False on timeout/no route.
async def _verifyConnectivity() -> bool:

    # Launches curl as a subprocess, which is a command-line program for
    # making HTTP(S) requests, which returns a page's HTML
    # . -s means silent supressed progress output,
    # dev/null throws away page content. The http_code part means print
    # only request's status after finishing. Pipe captures the stdout
    # so Python can read it, rather than letting it print to terminal.
    proc = await asyncio.create_subprocess_exec(
        "curl", "--max-time", str(CONNECTIVITY_CHECK_TIMEOUT_SECONDS),
        "-s", "-o", "/dev/null", "-w", "%{http_code}",
        "https://www.athletic.net",
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )

    try:
        # Wait for curl to finish and returns it's stdout, stderr output
        # as bytes (status code, empty as we distcard stderr).
        stdout, _ = await asyncio.wait_for(
            proc.communicate(), timeout=CONNECTIVITY_CHECK_TIMEOUT_SECONDS + 2
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return False

    # -w "%{http_code}" prints just the status code, e.g. "200" or "403".
    # "000" means curl couldn't connect at all (no route / DNS failure).
    code = stdout.decode().strip()
    return code not in ("", "000")

# VPNRotatorLinux
# Purpose: Manages VPN tunnel rotation inside the "mullvad" network
#          namespace on the VM, cycling through downloaded .conf files
#          via wg-quick. One instance shared by all sessions.
class VPNRotatorLinux:
 
    # __init__
    # Purpose: Loads the list of available configs and sets up shared
    #          state for cross-session coordination.
    # Arguments:
    #           self: the current instance of the class.
    # Output: None.
    def __init__(self):

        # ★ NO_VPN=1 RUNS WITHOUT A TUNNEL, on the box's own IP. The rotation
        #   surface stays present and every method is a no-op, so nothing
        #   else in the launcher has to know. This is the ENTIRE 429 defence
        #   turned off: one IP, no rotation, and a Cloudflare block has
        #   nothing to rotate to -- fine for a bounded catch-up run on a
        #   host you control, wrong for a full sweep.
        self.disabled = os.environ.get("NO_VPN", "") not in ("", "0", "false")
        if self.disabled:
            print("[VPN] NO_VPN=1 -- no tunnel, no rotation, one IP. "
                  "A Cloudflare block will have nowhere to go.")
            self.configs = []
            self.current_index = -1
            self.lock = asyncio.Lock()
            self.global_meets_since_rotation = 0
            self.rotation_count = 0
            self.last_rotation_time = time.time()
            return

        # join glues directory path and pattern together. glob finds files
        # matching the pattern. It returns a list of matching file paths, which
        # is then order alphabetically by sorted. This makes a sorted list of
        # all the file paths in the Wireguard directory.
        self.configs = sorted(glob.glob(os.path.join(WIREGUARD_DIR, "*.conf")))
 
        if not self.configs:
            looked = "\n  ".join(p for p in WIREGUARD_CANDIDATES if p)
            raise RuntimeError(
                f"No WireGuard .conf files found. Looked in:\n  {looked}\n"
                f"Put the Mullvad .conf files in one of those, or set "
                f"WIREGUARD_DIR=/path/to/confs. To scrape without a VPN at "
                f"all, run the launcher with NO_VPN=1.")
 
        print(f"[VPN] Loaded {len(self.configs)} configs")
 
        # -1 means "no tunnel up yet" — first rotate() moves to index 0.
        self.current_index = -1
 
        self.lock = asyncio.Lock()
        self.global_meets_since_rotation = 0
        self.rotation_count = 0
        self.last_rotation_time = time.time()
 
    # _currentConf
    # Purpose: Returns the path to the currently-active config, or None
    #          if no tunnel is up yet.
    # Arguments:
    #           self: current instance.
    # Output: A path string, or None.
    def _currentConf(self) -> str | None:
        if self.disabled:
            return None

 
        if self.current_index == -1:
            return None
 
        return self.configs[self.current_index]
 
    # removeCurrentServer
    # Purpose: Permanently removes the current config from the rotation
    #          pool — used when a server is confirmed Cloudflare-blocked.
    # Arguments:
    #           self: current instance.
    # Output: None.
    def removeCurrentServer(self):
 
        if self.current_index == -1:
            return
 
        if len(self.configs) <= 1:
            print(f"[VPN] Only one config left, cannot remove")
            return
 
        removed = self.configs.pop(self.current_index)
        print(f"[VPN] Removed {os.path.basename(removed)} from pool "
              f"({len(self.configs)} remaining)")
 
        # Step back so the next rotate() doesn't skip the item that
        # shifted into this index.
        self.current_index -= 1
 
    # rotate
    # Purpose: Tears down the current tunnel (if any) and brings up the
    #          next one in the cycle, wrapping around at the end.
    # Arguments:
    #           self: current instance.
    #           label: session label for log output, e.g. "[Session 3]".
    #           reason: why we're rotating, for logging.
    # Output: True if the new tunnel came up successfully, False otherwise.
    async def rotate(self, label: str, reason: str) -> bool:

        # Snapshot before acquiring the lock. If this changes by the
        # time we get the lock, another session already completed a
        # rotation while we were waiting — the IP is already fresh,
        # so we skip instead of rotating again.
        count_before = self.rotation_count
 
        async with self.lock:

            if self.rotation_count != count_before:
                print(f"[VPN] {label} skipping rotation — already rotated by another session")
                return True
 
            old_conf = self._currentConf()

            if old_conf:
                await _bringDown(old_conf)
 
            # Try up to len(self.configs) configs, starting from the next
            # one after current_index. range(len(self.configs)) gives us
            # one attempt per config in the pool, in case several in a
            # row are down — without this we'd give up after just one.
            for _ in range(len(self.configs)):
    
                self.current_index = (self.current_index + 1) % len(self.configs)
                new_conf = self.configs[self.current_index]
    
                print(f"[VPN] {label} rotating ({reason}): "
                    f"{os.path.basename(old_conf) if old_conf else 'none'} "
                    f"-> {os.path.basename(new_conf)}")
    
                success = await _bringUp(new_conf)
    
                if success:
                    await asyncio.sleep(HANDSHAKE_WAIT_SECONDS)

                    # Verifies it's actually connected.
                    if await _verifyConnectivity():
                        self.rotation_count += 1
                        self.last_rotation_time = time.time()
                        self.global_meets_since_rotation = 0

                        print(f"[VPN] Rotation complete. Now on {os.path.basename(new_conf)}")
                        return True
                    
                    print(f"[VPN] {label} {os.path.basename(new_conf)} came up but "
                        f"failed connectivity check — tearing down and trying next")
                else:
                    # This config failed/timed out — log and loop to try the
                    # next one.
                    print(f"[VPN] {label} wg-quick up failed/timed out for "
                        f"{os.path.basename(new_conf)} — trying next config")
                
                # Whether wg-quick "succeeded" or not, this config isn't
                # usable. Tear it down WITH verification before trying the
                # next one — this is what stops a half-up interface from
                # this attempt sticking around alongside the next one.
                await _bringDown(new_conf)

                old_conf = None
    
            # Every config in the pool failed. Lock still releases (we're
            # exiting the `async with` block normally) — sessions will
            # resume, just with no working tunnel until the NEXT rotation
            # attempt succeeds.
            print(f"[VPN] {label} all {len(self.configs)} configs failed — "
                f"giving up on this rotation")
            
            return False
 
    # checkRotation
    # Purpose: Called by each session after every meet. Once the shared
    #          counter crosses LINUX_GLOBAL_MEETS_PER_ROTATION, triggers
    #          a proactive rotation.
    # Arguments:
    #           self: current instance.
    #           label: session label for log output.
    # Output: True if a rotation happened, False otherwise.
    async def checkRotation(self, label: str) -> bool:
 
        async with self.lock:
            self.global_meets_since_rotation += 1
 
            if self.global_meets_since_rotation < LINUX_GLOBAL_MEETS_PER_ROTATION:
                return False
 
        # Lock released before calling rotate() — rotate() acquires its
        # own lock, so holding this one would deadlock.
        return await self.rotate(label, "meet count threshold")
 
 
# ============================================================
# Platform selection
# ============================================================
#
# launcher.py does `from vpn_rotation import VPNRotator` and calls
# `VPNRotator()` — this picks the right implementation based on OS,
# same pattern as CHROME_PATH in launcher.py.
 
if platform.system() == "Windows":
    VPNRotator = VPNRotatorWindows
elif platform.system() == "Linux":
    VPNRotator = VPNRotatorLinux
else:
    raise RuntimeError(f"Unsupported OS: {platform.system()}")