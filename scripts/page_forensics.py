# Project: xc-predictor / scripts
# File:    page_forensics.py
# Purpose: say WHAT happened to a page Postgres refuses to read -- a single
#          flipped bit, half a page, another page's contents -- from the raw
#          8 KB on disk. Read-only: it opens the data file and never writes.
#
# ⚠ WHY (2026-09-22). results_tf block 2404700 failed its checksum
#   ("calculated checksum 59520 but expected 63314") with fsync,
#   full_page_writes and data_checksums all on, no crash since Aug 26, and an
#   NVMe reporting zero media errors. So Postgres wrote it right and the bytes
#   changed underneath -- and SMART cannot say how, because it only counts the
#   errors the drive itself noticed. The page can:
#
#     1. ONE BIT FLIPPED. If flipping exactly one bit of the page reproduces
#        the checksum Postgres stored, the damage is one bit. That is the
#        signature of memory (non-ECC RAM holding the page in the OS cache
#        before it reached the disk) far more than of NAND, whose errors the
#        drive's own ECC corrects or reports.
#     2. A TORN PAGE. One 4 KB half sane, the other not: a write the device
#        did not complete atomically.
#     3. SOMEBODY ELSE'S PAGE. A structurally perfect page whose checksum
#        matches ANOTHER block number, or whose rows are not the rows that
#        belong here: a misdirected write.
#
# ★ THE CHECKSUM IS PostgreSQL's OWN (src/include/storage/checksum_impl.h),
#   and the script PROVES it before trusting it: every readable neighbour
#   page must reproduce the checksum stored in its own header, or the run
#   stops. A wrong port would otherwise "find" bit flips that are not there.
#
#   sudo python3 scripts/page_forensics.py --relation results_tf \
#       --block 2404700 --expect-ids 104980207-104980252
#
#   (root, because the data directory is postgres-only; --datadir and
#   --filepath skip the psql lookups when psql is not usable)
import argparse
import os
import struct
import subprocess
import sys

import numpy as np

BLCKSZ = 8192
RELSEG_BLOCKS = 131072              # 1 GB segments / 8 KB pages
N_SUMS = 32
FNV_PRIME = np.uint32(16777619)
_BASE = np.array([
    0x5B1F36E9, 0xB8525960, 0x02AB50AA, 0x1DE66D2A, 0x79FF467A, 0x9BB9F8A3,
    0x217E7CD2, 0x83E13D2C, 0xF8D4474F, 0xE39EB970, 0x42C6AE16, 0x993216FA,
    0x7B093B5D, 0x98DAFF3C, 0xF718902A, 0x0B1C9CDB, 0xE58F764B, 0x187636BC,
    0x5D7B3BB1, 0xE73DE7DE, 0x92BEC979, 0xCCA6C0B2, 0x304A0979, 0x85AA43D4,
    0x783125BB, 0x6CA8EAA2, 0xE407EAC6, 0x4B5CFC3E, 0x9FBF8C76, 0x15CA20BE,
    0xF2CA9FD3, 0x959BD756], dtype=np.uint32)
ROWS = BLCKSZ // (4 * N_SUMS)       # 64


def _comp(s, v):
    t = s ^ v
    return (t * FNV_PRIME) ^ (t >> np.uint32(17))


def _words(page):
    """The page as PostgreSQL's checksum sees it: pd_checksum zeroed, 64x32
    little-endian uint32."""
    b = bytearray(page)
    b[8:10] = b"\0\0"
    return np.frombuffer(bytes(b), dtype="<u4").reshape(ROWS, N_SUMS)


def _fold(sums):
    for _ in range(2):
        sums = _comp(sums, np.uint32(0))
    return np.bitwise_xor.reduce(sums, axis=-1)


def blockSum(page):
    with np.errstate(over="ignore"):
        s = _BASE.copy()
        for row in _words(page):
            s = _comp(s, row)
        return int(_fold(s))


def reduceSum(block_sum, blkno):
    return ((block_sum ^ blkno) & 0xFFFFFFFF) % 65535 + 1


def storedSum(page):
    return struct.unpack_from("<H", page, 8)[0]


def singleBitFlips(page, blkno, want):
    """Every (byte, bit) whose flip gives the page checksum `want`.

    A flip in word (i, j) only changes column j's chain from row i on, so each
    column is replayed once for all 64*32 flips it can hold, vectorised.
    """
    w = _words(page)
    hits = []
    with np.errstate(over="ignore"):
        prefix = np.empty((ROWS + 1, N_SUMS), dtype=np.uint32)
        prefix[0] = _BASE
        for i in range(ROWS):
            prefix[i + 1] = _comp(prefix[i], w[i])
        finals = prefix[ROWS]
        folded = [int(_fold(finals[j:j + 1].copy())) for j in range(N_SUMS)]
        total = 0
        for f in folded:
            total ^= f
        bits = (np.uint32(1) << np.arange(32, dtype=np.uint32))
        for j in range(N_SUMS):
            rest = total ^ folded[j]
            for i in range(ROWS):
                s = _comp(np.full(32, prefix[i, j], dtype=np.uint32),
                          w[i, j] ^ bits)
                for k in range(i + 1, ROWS):
                    s = _comp(s, w[k, j])
                for _ in range(2):
                    s = _comp(s, np.uint32(0))
                for bit in np.nonzero(
                        [reduceSum(rest ^ int(x), blkno) == want
                         for x in s])[0]:
                    word = i * N_SUMS + j
                    byte = word * 4 + int(bit) // 8
                    if byte in (8, 9):
                        continue            # pd_checksum: zeroed, not summed
                    hits.append((byte, int(bit) % 8))
    return hits


def header(page):
    lsn_hi, lsn_lo, _ck, flags, lower, upper, special, psv, prune = \
        struct.unpack_from("<IIHHHHHHI", page, 0)
    return {"lsn": f"{lsn_hi:X}/{lsn_lo:08X}", "flags": flags, "lower": lower,
            "upper": upper, "special": special, "pagesize": psv & 0xFF00,
            "version": psv & 0xFF, "prune_xid": prune}


def headerSane(h):
    return (h["pagesize"] == BLCKSZ and h["version"] == 4
            and 24 <= h["lower"] <= h["upper"] <= h["special"] <= BLCKSZ)


def items(page, h):
    """(n, off, flags, len, tuple_ok) per line pointer, where tuple_ok says the
    heap tuple header at `off` is plausible."""
    out = []
    n = max(0, (h["lower"] - 24) // 4) if headerSane(h) else 0
    for k in range(min(n, 400)):
        (lp,) = struct.unpack_from("<I", page, 24 + 4 * k)
        off, fl, ln = lp & 0x7FFF, (lp >> 15) & 3, lp >> 17
        ok = False
        if fl == 1 and 24 <= off and off + ln <= BLCKSZ and ln >= 24:
            xmin, xmax = struct.unpack_from("<II", page, off)
            im2, im, hoff = struct.unpack_from("<HHB", page, off + 18)
            natts = im2 & 0x07FF
            ok = (0 < natts < 200 and 24 <= hoff <= ln and hoff % 8 == 0
                  and xmin >= 3)
        out.append((k + 1, off, fl, ln, ok))
    return out


def idHits(page, lo, hi):
    """Offsets where a little-endian int4 (or an int8's low word) lies in
    [lo, hi] -- the result_ids that belong on this page."""
    arr = np.frombuffer(page, dtype=np.uint8)
    vals = (arr[:-3].astype(np.uint32) | (arr[1:-2].astype(np.uint32) << 8)
            | (arr[2:-1].astype(np.uint32) << 16)
            | (arr[3:].astype(np.uint32) << 24))
    return sorted(set(int(v) for v in vals[(vals >= lo) & (vals <= hi)]))


def _show(b):
    """Printable ASCII, other bytes as dots -- enough to see a typo."""
    return "".join(chr(c) if 32 <= c < 127 else "." for c in b)


def _hex(b, at, width=4):
    """The bytes around `at` in hex, the byte itself bracketed."""
    lo, hi = max(0, at - width), min(len(b), at + width + 1)
    return " ".join(f"[{b[i]:02x}]" if i == at else f"{b[i]:02x}"
                    for i in range(lo, hi))


def _psql(dbname, sql):
    return subprocess.check_output(
        ["sudo", "-u", "postgres", "psql", "-d", dbname, "-XAtc", sql],
        text=True).strip()


def readBlock(datadir, filepath, blkno):
    seg, rem = divmod(blkno, RELSEG_BLOCKS)
    path = os.path.join(datadir, filepath) + (f".{seg}" if seg else "")
    with open(path, "rb") as fh:
        fh.seek(rem * BLCKSZ)
        page = fh.read(BLCKSZ)
    if len(page) != BLCKSZ:
        raise SystemExit(f"short read at block {blkno} of {path}")
    return path, page


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--relation", default="results_tf")
    ap.add_argument("--db", default="xc_predictor")
    ap.add_argument("--block", type=int, required=True)
    ap.add_argument("--expect-ids", default=None,
                    help="lo-hi: the result_ids that belong on this page")
    ap.add_argument("--datadir", default=None)
    ap.add_argument("--filepath", default=None,
                    help="pg_relation_filepath(), e.g. base/76828/631728")
    ap.add_argument("--neighbours", type=int, default=3)
    a = ap.parse_args(argv)

    datadir = a.datadir or _psql(a.db, "SHOW data_directory")
    filepath = a.filepath or _psql(
        a.db, f"SELECT pg_relation_filepath('{a.relation}')")
    path, page = readBlock(datadir, filepath, a.block)
    print(f"block {a.block} of {a.relation}: {path} "
          f"@ {(a.block % RELSEG_BLOCKS) * BLCKSZ}")

    # 1. prove the checksum port on pages Postgres can read
    proved = 0
    for d in range(-a.neighbours, a.neighbours + 1):
        b = a.block + d
        if d == 0 or b < 0:
            continue
        try:
            _p, nb = readBlock(datadir, filepath, b)
        except (OSError, SystemExit):
            continue
        if not any(nb):
            continue
        calc = reduceSum(blockSum(nb), b)
        ok = calc == storedSum(nb)
        print(f"  neighbour {b}: stored {storedSum(nb)} calculated {calc} "
              f"{'ok' if ok else 'MISMATCH'}  lsn {header(nb)['lsn']}")
        if not ok:
            raise SystemExit("the checksum port does not reproduce a readable "
                             "page -- nothing below can be trusted")
        proved += 1
    if not proved:
        raise SystemExit("no readable neighbour to prove the checksum on")

    stored = storedSum(page)
    bsum = blockSum(page)
    calc = reduceSum(bsum, a.block)
    h = header(page)
    print(f"\nthe page: stored checksum {stored}, calculated {calc}"
          f"  (the server log's two numbers)")
    if calc == stored:
        print("  THE CHECKSUM MATCHES: this page is not damaged on disk (a "
              "page Postgres refused may since have been rewritten).")
        return 0
    print(f"  header: {h}  -> {'sane' if headerSane(h) else 'NOT SANE'}")
    if not any(page):
        print("  the page is ALL ZEROES")

    its = items(page, h)
    if its:
        first = [ok for _n, off, _f, _l, ok in its if off < 4096]
        second = [ok for _n, off, _f, _l, ok in its if off >= 4096]
        print(f"  line pointers: {len(its)}; plausible tuples "
              f"{sum(first)}/{len(first)} in the first 4 KB, "
              f"{sum(second)}/{len(second)} in the second")
    ids = []
    first = second = []
    if its:
        first = [ok for _n, off, _f, _l, ok in its if off < 4096]
        second = [ok for _n, off, _f, _l, ok in its if off >= 4096]
    if a.expect_ids:
        lo, hi = (int(x) for x in a.expect_ids.split("-"))
        ids = idHits(page, lo, hi)
        print(f"  result_ids {lo}-{hi} found on the page: {len(ids)}"
              + (f" ({ids[0]}..{ids[-1]})" if ids else ""))

    # 2. one bit?
    print("\nsearching every single-bit flip for the stored checksum...")
    flips = singleBitFlips(page, a.block, stored)
    tuples = [(off, ln, n) for n, off, _f, ln, _ok in its if ln]
    for byte, bit in flips[:10]:
        where = next((f"tuple {n}, byte {byte - off} of {ln}"
                      for off, ln, n in tuples if off <= byte < off + ln),
                     "header / line pointers" if byte < h["lower"]
                     else "free space between line pointers and tuples")
        lo, hi = max(0, byte - 12), min(BLCKSZ, byte + 13)
        now = page[lo:hi]
        was = bytearray(now)
        was[byte - lo] ^= 1 << bit
        print(f"  byte {byte} (0x{byte:04X}) bit {bit}: 0x{page[byte]:02X} "
              f"on disk, 0x{page[byte] ^ (1 << bit):02X} if flipped back  "
              f"[{where}]")
        print(f"      on disk:  {_show(now)}   {_hex(now, byte - lo)}")
        print(f"      restored: {_show(bytes(was))}   {_hex(was, byte - lo)}")

    # 3. somebody else's page?
    near = [b for b in range(max(0, a.block - 200000), a.block + 200000)
            if b != a.block and reduceSum(bsum, b) == stored]

    print("\nVERDICT")
    whole = headerSane(h) and its and all(ok for *_x, ok in its)
    if a.expect_ids:
        whole = whole and len(ids) > 0
    if not headerSane(h) and not any(page[:24]):
        print("  the header is zeroed: a partial or lost write")
    elif (its and first and second
          and ((all(first) and not any(second))
               or (all(second) and not any(first)))) \
            or (headerSane(h) and not any(page[4096:])):
        print("  one 4 KB half is sane and the other is wholly not: a TORN "
              "write -- the device completed half an 8 KB write")
    elif its and 0 < sum(not ok for *_x, ok in its) <= 3:
        bad = [n for n, _o, _f, _l, ok in its if not ok]
        print(f"  LOCALISED DAMAGE: the header and all but {len(bad)} tuple(s) "
              f"(line pointers {bad}) are sane. The damage sits in those rows; "
              f"the rest of the page is fine. A single-bit candidate inside "
              f"them (below) would make it one flip, otherwise a burst of "
              f"several bytes.")
    elif whole:
        print("  the page is STRUCTURALLY INTACT: sane header, every tuple "
              "plausible" + (", and it holds this block's own ids"
                             if a.expect_ids else "") + ".")
        print("  So the damage is small -- a few bits or bytes somewhere in "
              "the row data -- not a torn, lost or misdirected write.")
        print(f"  {len(flips)} single-bit candidate(s). ⚠ WITH A 16-BIT "
              f"CHECKSUM ABOUT ONE CANDIDATE APPEARS BY CHANCE EVEN FOR "
              f"RANDOM DAMAGE, so a candidate is only evidence when its "
              f"'restored' line reads like real data and its 'on disk' line "
              f"does not (a school name with one wrong letter, a date with "
              f"one wrong digit).")
    else:
        print("  the page is NOT structurally intact: read the header, the "
              "tuples and the ids above. Large damage points at the write "
              "path (drive, controller, filesystem) rather than one bit of "
              "memory.")
    print(f"  (block numbers within 200k whose checksum this content would "
          f"match: {len(near)} -- about 6 are expected by chance)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
