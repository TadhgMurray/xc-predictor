"""scripts/page_forensics.py reads a page Postgres refuses and says what
happened to it. Its conclusions are only as good as its port of Postgres's
checksum, so that is tested against real pages first.

The fixture is seven heap pages (blocks 97..103) written by PostgreSQL 16
with data_checksums on: a synthetic table of 120 rows a page, result_ids
from 104980001.

    python -m pytest -q tests/test_page_forensics.py
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
import page_forensics as PF                                     # noqa: E402

_FIX = os.path.join(_ROOT, "tests", "fixtures", "heap_pages_97_103.bin")
FIRST = 97


def pages():
    raw = open(_FIX, "rb").read()
    return [raw[i:i + PF.BLCKSZ] for i in range(0, len(raw), PF.BLCKSZ)]


def test_the_port_reproduces_postgres_on_every_real_page():
    """If this fails, every verdict the script prints is noise."""
    for k, page in enumerate(pages()):
        assert PF.reduceSum(PF.blockSum(page), FIRST + k) == PF.storedSum(page)


def test_the_block_number_is_part_of_the_checksum():
    page = pages()[3]
    assert PF.reduceSum(PF.blockSum(page), FIRST + 4) != PF.storedSum(page)


def test_a_single_flipped_bit_is_found_among_the_candidates():
    page = bytearray(pages()[3])
    blk = FIRST + 3
    page[6000] ^= 1 << 4
    got = PF.singleBitFlips(bytes(page), blk, PF.storedSum(page))
    assert (6000, 4) in got
    # a 16-bit checksum over 65,536 positions: a handful of chance matches
    assert len(got) <= 6


def test_a_torn_page_shows_as_one_dead_half():
    page = bytearray(pages()[3])
    page[4096:] = b"\0" * 4096
    h = PF.header(bytes(page))
    its = PF.items(bytes(page), h)
    first = [ok for _n, off, _f, _l, ok in its if off < 4096]
    second = [ok for _n, off, _f, _l, ok in its if off >= 4096]
    assert PF.headerSane(h) and all(first) and not any(second)


def test_the_page_holds_its_own_ids():
    ids = PF.idHits(pages()[3], 104980001, 104999999)
    assert len(ids) == 120
