"""scripts/ovh_stock_watch: which OVH configurations count (2026-10-20).

    python -m pytest -q tests/test_ovh_stock_watch.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import ovh_stock_watch as W                                     # noqa: E402


def test_usable_nvme():
    assert W.usableNvmeTb("softraid-2x1920nvme") == 1.92
    assert W.usableNvmeTb("softraid-4x960nvme") == 1.92
    assert W.usableNvmeTb("softraid-2x960nvme") == 0.96
    assert W.usableNvmeTb("softraid-2x4000sa") == 0.0
    assert W.usableNvmeTb("hybridsoftraid-2x6000sa-2x512nvme") == 0.0


def test_candidates_filters_ram_disk_price_and_stock():
    plan = "24rise03-v2-us"
    addons = {f"ram-128g-ecc-2666-{plan}": 28.0, f"softraid-4x960nvme-{plan}": 48.0,
              f"softraid-2x1920nvme-{plan}": 48.0, f"ram-64g-ecc-3200-{plan}": 13.0}
    avail = [
        {"memory": "ram-128g-ecc-2666", "storage": "softraid-4x960nvme",
         "datacenters": [{"datacenter": "vin", "availability": "1H-low"},
                         {"datacenter": "hil", "availability": "unavailable"},
                         {"datacenter": "gra", "availability": "1H-high"}]},
        {"memory": "ram-64g-ecc-3200", "storage": "softraid-2x1920nvme",
         "datacenters": [{"datacenter": "vin", "availability": "1H-high"}]},
        {"memory": "ram-128g-ecc-2666", "storage": "softraid-2x1920nvme",
         "datacenters": [{"datacenter": "hil", "availability": "unknown"}]},
    ]
    got = W.candidates(avail, plan, 110.0, addons, 250)
    assert [(g[1], g[4]) for g in got] == [("vin", 186.0)]
    assert W.candidates(avail, plan, 110.0, addons, 150) == []
