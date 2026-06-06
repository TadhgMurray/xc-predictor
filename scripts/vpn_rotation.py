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

# Detect OS and set Mullvad CLI path accordingly.
# Mullvad is installed via their official installer on Windows
# and their .deb package on Ubuntu.
if platform.system() == "Windows":
    MULLVAD_CLI = r"C:\Program Files\Mullvad VPN\resources\mullvad.exe"
elif platform.system() == "Linux":
    # Mullvad CLI on Ubuntu — installed via their .deb package.
    MULLVAD_CLI = "/usr/bin/mullvad"
else:
    raise RuntimeError(f"Unsupported OS: {platform.system()}")

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

# Total meets across ALL sessions before rotating.
# 25 sessions × ~3-5s per meet = ~5-8 meets/second across all sessions.
# 3000 total meets = ~6-10 minutes per IP — aggressive but safe.
GLOBAL_MEETS_PER_ROTATION = 3000

# How many consecutive failures before triggering an emergency rotation.
FAILURE_STREAK_THRESHOLD = 10

# VPNRotator
# Purpose: Manages VPN server roptationa cross all scraping sessions. 
#          One instance is shared by all sessions in launcher.py.
# We use a class because we need a shared state among all the
# sessions, which can be done with one shared instance of this class. We
# also use a class because the sessions need to remember things between
# calls about consecutive failures and # of successes.
class VPNRotator:
    
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

    # runMullvadCommand
    # Purpose: Runs a Mullvad CLI command synchronously using subprocess.
    #          We use subprocess.run (not async) because rotation happens
    #          while holding the lock — other sessions are already waiting,
    #          so blocking is fine here.
    # Arguments:
    #           self: current instance of the class.
    #           args: list of command arguments e.g. ["relay", "set", "location", "us nyc"].
    # Output: Return true if the commands succeeded, False if it failed.
    async def runMullvadCommand(self, args: list) -> bool:

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

            # Sets the current index to the next in the list w/ wraparound.
            self.current_index = (self.current_index + 1) % len(self.locations)

            # Gets new VPN location from the list using the new index.
            new_location = self.locations[self.current_index]

            # Step 1 - set the new relay location.

            # Splits the new location string into a list.
            location_parts = new_location.split()

            # Run the command in the CLI using the new location to change our 
            # IP address to the new VPN server.
            success = await self.runMullvadCommand(["relay", "set", "location"] + location_parts)

            if not success:
                print(f"[VPN] Failed to set location to {new_location}")
                return False
            
            # Step 2 - reconnect on the new server.
            # Runs the command in the CLI to connect to the new IP.
            success = await self.runMullvadCommand(["connect"])

            if not success:
                print(f"[VPN] Failed to connect to {new_location}")
                return False
            
            # Step 3 — wait for the connection to establish.
            # 10 seconds is enough for Mullvad to fully connect and
            # get a new IP before scraping resumes.
            print(f"[VPN] Waiting 10s for connection to establish...")
            await asyncio.sleep(10)

            # Step 4 — verify connection.
            # Runs the command in the CLI to check if we're connected.
            success = await self.runMullvadCommand(["status"])

            if not success:
                print(f"[VPN] Could not verify connection status")

            self.rotation_count += 1
            self.last_rotation_time = time.time()
            print(f"[VPN] Rotation complete. Now on {new_location}")

            return True

    # checkRotation
    # Purpose: Called by each se4ssion after ever meet. Checks if rotation should
    #          be triggered based on meet count or failure streak.
    # Arguments:
    #           self: current instance of this class.
    #           label: session label for printed output.
    #           consecutive_failures: current consecutive failure streak.
    # Output: Returns True if a rotation happened, False otherwise.
    async def checkRotation(self, label: str, consecutive_failures: int) -> bool:
        
        # Record this meet.
        threshold_reached = await self.recordMeet()

        # Check if we've hit the global threshold.
        if threshold_reached:
            return await self.rotate(label, "global meet count")

        # Per-session failure streak check — if one session is getting
        # hammered with failures, rotate immediately regardless of global count.
        if consecutive_failures >= FAILURE_STREAK_THRESHOLD:
            return await self.rotate(label, "failure streak")

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