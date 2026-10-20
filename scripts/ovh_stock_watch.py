#!/usr/bin/env python3
"""
ovh_stock_watch.py -- is an OVH US Eco (Rise) server we would move to in
stock? Mails the admins when a configuration comes INTO stock.

    /srv/venv/bin/python scripts/ovh_stock_watch.py --show     # the table, no mail
    /srv/venv/bin/python scripts/ovh_stock_watch.py            # mail what is newly in stock
    sudo bash deploy/install_ovh_watch_timer.sh                # every 30 minutes

★ WHY (owner, 2026-10-20). The server's memory is damaging data (Postgres
  checksum mismatches on a clean NVMe, four incidents in three weeks), and
  the ECC machines that fit -- 128 GB ECC, 1.92 TB+ of mirrored NVMe, a US
  datacenter, under the budget -- go in and out of stock by the hour.

★ OVH's PUBLIC API, NO KEY. /order/catalog/public/eco names the plans and
  prices every add-on; /dedicated/server/datacenter/availabilities says,
  per (plan, memory, storage) and datacenter, "1H-high", "1H-low", "72H",
  "unavailable"... Anything but unavailable/unknown can be ordered.
  ! ONE PLAN PER REQUEST: the unfiltered availability list is ~6 MB and
    arrived cut short through a proxy (2026-10-20).

Mails only on a CHANGE: the configurations in stock last time are kept in
engine/data/ovh_stock_state.json, and a mail goes out when one appears that
was not. XCP_OVH_MAX (default 250) is the monthly ceiling in USD.
"""
import argparse
import json
import os
import re
import sys
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

API = "https://api.us.ovhcloud.com/1.0"
STATE = os.path.join(_ROOT, "engine", "data", "ovh_stock_state.json")
DATACENTERS = {"vin": "Vint Hill, VA", "hil": "Hillsboro, OR"}
# the Rise plans worth moving to, best first (PassMark per-core / all-core
# against the Ryzen 9 5950X it replaces; see the 2026-10-20 notes)
PLANS = (
    ("24rise03-v2-us", "RISE-3 Ryzen 9 5900X 12c", "1.00 / 0.86"),
    ("24rise02-v2-us", "RISE-2 Xeon E-2388G 8c", "0.99 / 0.52"),
    ("24rise05-v2-us", "RISE-5 EPYC 7413 24c", "0.68 / 1.11"),
    ("24rise07-v1-us", "RISE-7 EPYC 7402 24c", "0.61 / 1.02"),
    ("24rise04-v2-us", "RISE-4 EPYC 7313 16c", "0.70 / 0.86"),
    ("24rise01-v2-us", "RISE-1 Xeon E-2386G 6c", "0.98 / 0.43"),
)
MIN_RAM_GB = 128
MIN_NVME_TB = 1.9            # usable, mirrored
OUT = {"unavailable", "unknown", "", None}


def _get(path):
    req = urllib.request.Request(API + path, headers={"Accept": "application/json",
                                                     "Accept-Encoding": "identity"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))


def ramGb(code):
    m = re.search(r"ram-(\d+)g", code or "")
    return int(m.group(1)) if m else 0


def usableNvmeTb(code):
    """Mirrored NVMe capacity of a storage code, in TB: softraid-2x1920nvme
    -> 1.92 (a mirror), 3x/4x as RAID 5/10 at worst -> n-1 / n/2 drives.
    0 for spinning disks, hybrids and no RAID. Pure."""
    m = re.fullmatch(r"softraid-(\d)x(\d+)nvme", code or "")
    if not m:
        return 0.0
    n, gb = int(m.group(1)), int(m.group(2))
    usable = {2: 1, 3: 1, 4: 2}.get(n, 1)        # the most conservative layout
    return usable * gb / 1000.0


def monthly(item):
    for pr in item.get("pricings") or []:
        if (pr.get("interval") == 1 and "renew" in (pr.get("capacities") or [])
                and not pr.get("commitment") and pr.get("mode", "default") == "default"):
            return pr["price"] / 1e8
    return None


def prices(catalog):
    """{planCode: base}, {addonCode: monthly}."""
    plans = {p["planCode"]: monthly(p) for p in catalog.get("plans", [])}
    addons = {a["planCode"]: monthly(a) for a in catalog.get("addons", [])}
    return plans, addons


def candidates(avail, plan, base, addons, max_usd):
    """[(key, dc, memory, storage, usd, availability)] in stock and wanted."""
    out = []
    for a in avail:
        mem, sto = a.get("memory") or "", a.get("storage") or ""
        if ramGb(mem) < MIN_RAM_GB or usableNvmeTb(sto) < MIN_NVME_TB:
            continue
        add = (addons.get(f"{mem}-{plan}") or 0.0) + (addons.get(f"{sto}-{plan}") or 0.0)
        usd = (base or 0.0) + add
        if usd > max_usd:
            continue
        for d in a.get("datacenters") or []:
            dc, av = d.get("datacenter"), d.get("availability")
            if dc in DATACENTERS and av not in OUT:
                out.append((f"{plan}|{mem}|{sto}|{dc}", dc, mem, sto, usd, av))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--show", action="store_true", help="print the table, send nothing")
    ap.add_argument("--max", type=float, default=float(os.environ.get("XCP_OVH_MAX", 250)))
    a = ap.parse_args(argv)

    plans_usd, addons = prices(_get("/order/catalog/public/eco?ovhSubsidiary=US"))
    found = []
    for plan, name, speed in PLANS:
        avail = _get(f"/dedicated/server/datacenter/availabilities?planCode={plan}")
        for key, dc, mem, sto, usd, av in candidates(avail, plan, plans_usd.get(plan),
                                                     addons, a.max):
            found.append((usd, name, speed, DATACENTERS[dc], mem, sto, av, key))
    found.sort()
    lines = [f"  ${usd:>6.0f}/mo  {name:<26} speed {speed}  {where:<15} {mem:<20} {sto:<22} {av}"
             for usd, name, speed, where, mem, sto, av, _k in found]
    print(f"[ovh] {len(found)} configurations in stock with {MIN_RAM_GB}+ GB ECC and "
          f"{MIN_NVME_TB}+ TB mirrored NVMe, under ${a.max:.0f}/month:")
    print("\n".join(lines) if lines else "  (none)")
    if a.show:
        return 0

    try:
        before = set(json.load(open(STATE)))
    except (OSError, ValueError):
        before = set()
    now = {f[-1] for f in found}
    new = [f for f in found if f[-1] not in before]
    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        json.dump(sorted(now), open(STATE, "w"))
    except OSError as exc:
        print(f"[ovh] cannot save {STATE}: {exc}")
    if not new:
        print("[ovh] nothing newly in stock -- no mail")
        return 0
    import accounts
    to = sorted(accounts.adminEmails())
    if not to or not accounts.mailEnabled():
        print("[ovh] no admins or no mail provider -- not sent")
        return 0
    subject = (f"[racecast] OVH: {new[0][1]} in stock, ${new[0][0]:.0f}/mo "
               f"({new[0][3]})" + (f" +{len(new) - 1} more" if len(new) > 1 else ""))
    text = ("Newly in stock (128 GB+ ECC, 1.92 TB+ mirrored NVMe, US):\n\n"
            + "\n".join(f"  ${usd:.0f}/mo  {name}  {where}  {mem}  {sto}  ({av})"
                        for usd, name, _s, where, mem, sto, av, _k in new)
            + "\n\nEverything in stock now:\n" + "\n".join(lines)
            + "\n\nOrder: https://eco.us.ovhcloud.com/rise/  (setup fee = one month; "
              "a 12-month term usually waives it)\n"
              "Speed is per-core / all-core against today's Ryzen 9 5950X.\n")
    for addr in to:
        ok = accounts.sendMail(addr, subject, text)
        print(f"[ovh] mail {'sent' if ok else 'FAILED'} to {addr}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
