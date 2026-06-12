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

# Path to the Mullvad CLI on Windows.
MULLVAD_CLI = r"C:\Program Files\Mullvad VPN\resources\mullvad.exe"

# ─── Server List ─────────────────────────────────────────────────────────────

# List of Mullvad server locations to rotate through.
# Format is "country city" as used by the Mullvad CLI.
# Rotate through different continents to maximize IP diversity.
MULLVAD_LOCATIONS = [
    # United States — most options, athletic.net is a US site so these
    # tend to have the least latency and best success rates
    "us nyc",   # New York
    "us lax",   # Los Angeles
    "us chi",   # Chicago
    "us dal",   # Dallas
    "us mia",   # Miami
    "us sea",   # Seattle
    "us atl",   # Atlanta
    "us den",   # Denver
    "us hou",   # Houston
    "us phx",   # Phoenix
    "us slc",   # Salt Lake City
    "us det",   # Detroit
    "us bos",   # Boston
    "us was",   # Washington DC
    "us sjc",   # San Jose
    "us sec",   # Secaucus NJ (new — from server list)
    "us ash",   # Ashburn VA (new — from server list)
    "us ral",   # Raleigh NC (new — from server list)
    "us mca",   # McAllen TX (new — from server list)
    "us kan",   # Kansas City (new — from server list)

    # Canada — confirmed working overnight
    "ca tor",   # Toronto
    "ca van",   # Vancouver
    "ca mon",   # Montreal (new — visible in server list as ca-mon)

    # Latin America — confirmed working overnight, minus bad codes
    "br sao",   # Sao Paulo
    "br for",   # Fortaleza (new — from server list)
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
    "th bkk",   # Bangkok
    "my kul",   # Kuala Lumpur
    "ph mnl",   # Manila (new — from server list)
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
WINDOWS_GLOBAL_MEETS_PER_ROTATION  = 3000

# Path to the proxy executable.
PROXY_PATH = r"C:\Users\Tadhg Murray\cloud-sql-proxy\cloud-sql-proxy.exe"

# The instance connection string tells the proxy which Cloud SQL
# instance to connect to and which local port to listen on.
PROXY_INSTANCE = "project-d8c4b484-c8fa-4e09-9fc:us-west1:free-trial-first-project=tcp:5432"

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

        # Step 4 — restart the Cloud SQL proxy.
        # The proxy loses its connection to Cloud SQL when Mullvad switches
        # servers because the network briefly drops. We kill the old process
        # and start a fresh one so DB connections work on the new IP.
        success = self._restartProxy()
        if not success:
            print(f"[VPN] WARNING: proxy restart failed — DB connections may fail")
            # Non-fatal — scraping can continue and sessions will crash on DB
            # errors rather than here. Better to continue than abort rotation.
 
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
            return self.global_meets_since_rotation >= GLOBAL_MEETS_PER_ROTATION
        
    # rotate
    # Purpose: Rotates to the next Mullvad server location. Only one session
    #          can rotate at a time - others wait for the lock to be released.
    # Arguments:
    #           self: current instance of this class.
    #           label: session lable for printed output e.g. "[Session 1]"
    #           reason: why rotation was triggered e.g. "meet count" or "failure streak".
    # Output: Returns true if rotation succeeded, False if it failed.
    async def rotate(self, label: str, reason: str) -> bool:

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

            # Advance to the next server.
            await self._advanceIndex()
            new_location = self.locations[self.current_index]

            elapsed = time.time() - self.last_rotation_time
            print(
                f"[VPN] {label} rotating → {new_location} "
                f"(reason: {reason}, "
                f"{elapsed:.0f}s since last rotation, "
                f"rotation #{self.rotation_count + 1})"
            )
 
            # Connect to the new server.
            success = await self._connectToLocation(new_location)
            if not success:
                return False

            # TODO: parse stdout to verify "Connected" status — currently only
            # checks that the CLI command ran without crashing, not that the
            # tunnel actually established.
            # Verify we're connected — failure here is non-fatal,
            # we log it and continue since the connect call succeeded.
            await self._runMullvadCommand(["status"])

            # Update shared state.
            self.rotation_count += 1
            self.last_rotation_time = time.time()
 
            # Reset the meet counter — we're on a fresh IP now.
            self.global_meets_since_rotation = 0

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
        
        threshold_reached = await self.recordMeet()
 
        if threshold_reached:
            return await self.rotate(label, "meet count threshold")
 
        return False
    
    # removeCurrentServer
    # Purpose: Removes current server from locations list in case
    #          of cloudflare IP block of VPN IP.
    # Arguments:
    #           self: current instance of this class.
    # Output: None.
    def removeCurrentServer(self):
        
        # Need at least 2 servers to remove one — if only 1 is left
        # we have nowhere to go so we keep it.
        if len(self.locations) <= 1:
            print(f"[VPN] Only one server left, cannot remove")
            return
        
        # Get the name of the server being removed for the log message.
        removed = self.locations[self.current_index]

        # .pop(index) removes the item at that position and shifts
        # everything after it down by one.
        self.locations.pop(self.current_index)

        # Wrap the index in case we just removed the last item in the list.
        # e.g. if index was 9 and list now has 9 items (0-8), 9 % 9 = 0.
        self.current_index = self.current_index % len(self.locations)

        print(f"[VPN] Removed blocked server {removed} — {len(self.locations)} servers remaining")

# ============================================================
# LINUX (VM) — wg-quick / network namespace rotator
# ============================================================

# Directory holding all the .conf files downloaded from Mullvad.
# wg-quick names interfaces after the filename (minus .conf), and
# this whole launcher process runs inside `ip netns exec mullvad`,
# so wg-quick commands here automatically operate on that namespace.
WIREGUARD_DIR = os.path.expanduser("~/xc-predictor/wireguard")
 
# Rotate proactively every this many meets (summed across all sessions).
LINUX_GLOBAL_MEETS_PER_ROTATION = 1000
 
# How long to wait after bringing a new tunnel up for the handshake
# to complete before traffic starts flowing again.
HANDSHAKE_WAIT_SECONDS = 3

# _bringDown
# Purpose: Tears down a WireGuard interface that's currently up.
# Arguments:
#           conf_path: path to the .conf file describing the interface.
# Output: None. Errors are swallowed — fine if nothing was up yet.
async def _bringDown(conf_path: str):
    
    # Launches wq-quick as a separate OS process. 
    # This is equivalent to running wg-quick down /path/to/us-phx-wg-207.conf,
    # wghucg tears down a wireguard interfrace.
    proc = await asyncio.create_subprocess_exec(
        "wg-quick", "down", conf_path,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    await proc.wait()

# _bringUp
# Purpose: Brings up a new WireGuard interface from a .conf file.
# Arguments:
#           conf_path: path to the .conf file describing the interface.
# Output: True if wg-quick succeeded, False otherwise.
async def _bringUp(conf_path: str) -> bool:
 
    proc = await asyncio.create_subprocess_exec(
        "wg-quick", "up", conf_path,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    await proc.wait()
 
    return proc.returncode == 0

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
        
        # join glues directory path and pattern together. glob finds files
        # matching the pattern. It returns a list of matching file paths, which
        # is then order alphabetically by sorted. This makes a sorted list of
        # all the file paths in the Wireguard directory.
        self.configs = sorted(glob.glob(os.path.join(WIREGUARD_DIR, "*.conf")))
 
        if not self.configs:
            raise RuntimeError(f"No .conf files found in {WIREGUARD_DIR}")
 
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
 
        async with self.lock:
 
            old_conf = self._currentConf()
 
            self.current_index = (self.current_index + 1) % len(self.configs)
            new_conf = self.configs[self.current_index]
 
            print(f"[VPN] {label} rotating ({reason}): "
                  f"{os.path.basename(old_conf) if old_conf else 'none'} "
                  f"-> {os.path.basename(new_conf)}")

            # If not first time rotating brings down old wireguard
            # interface before bringing up next one.
            if old_conf:
                await _bringDown(old_conf)
 
            success = await _bringUp(new_conf)
 
            if not success:
                print(f"[VPN] {label} wg-quick up failed for "
                      f"{os.path.basename(new_conf)}")
                return False
 
            await asyncio.sleep(HANDSHAKE_WAIT_SECONDS)
 
            self.rotation_count += 1
            self.last_rotation_time = time.time()
            self.global_meets_since_rotation = 0
 
            print(f"[VPN] Rotation complete. Now on {os.path.basename(new_conf)}")
 
            return True
 
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