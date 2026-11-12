"""
one_ip.py -- the scrapers run from the server's own address, with nothing to
rotate to (owner, 2026-11-10: "delete the ip rotation/proxy code"; the VPN
rotator, scripts/vpn_rotation.py, and its Mullvad setup are gone).

OneIP keeps the surface the scrapers call (launcher.py, run_tfrrs.py,
scrape_results.py), and every call does what the old NO_VPN=1 mode did:
- no tunnel to wait for
- nothing to rotate to
- a stuck session (repeated 429s) pauses so the one address goes quiet

A Cloudflare block or a 429 ends the run politely (polite_stop.py), never a
switch to another address.
"""
import asyncio


class OneIP:
    STUCK_SESSION_PAUSE_SECONDS = 60

    def __init__(self):
        self.rotation_count = 0
        self.rotation_generation = 0

    async def handleStuckSession(self, label):
        print(f"[ip] {label} stuck session (repeated 429s) -- pausing "
              f"{self.STUCK_SESSION_PAUSE_SECONDS}s; there is one address and no other")
        await asyncio.sleep(self.STUCK_SESSION_PAUSE_SECONDS)

    async def waitForTunnel(self, index, last_generation_seen, total_sessions):
        return 0

    async def recordMeet(self):
        return False

    async def rotate(self, label, reason):
        return False

    async def checkRotation(self, label):
        return False

    def removeCurrentServer(self):
        return None
