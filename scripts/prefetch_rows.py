# Project: xc-predictor / scripts
# File:    prefetch_rows.py
# Purpose: read the next block of a stream while the caller works on this one,
#          and write the last block while the caller builds the next.
#
# ★ WHY (2026-11-19, run 20261116_204106). 05_backfill_xc (996 s, the long
#   side of 05) and 09b_fill (XC: 900,843 rows into sr_staging_xc in ~227 s)
#   are each one Python loop that takes turns with the database: FETCH a
#   block of a server-side cursor and wait, classify it, then (05, update
#   path) UPDATE it and wait again. psycopg2 lets go of the GIL while it
#   waits on the server, so the waits can sit under the Python work instead
#   of after it.
#
# ! THE SAME ROWS IN THE SAME ORDER. Prefetch hands back exactly what the
#   cursor's fetchmany returns, in order, one thread reading; Writer runs
#   the caller's own write function on each block, in submission order, one
#   thread writing. Nothing is reordered, merged or dropped. The caller's
#   connection rule still holds: the reading connection is touched by the
#   reader thread alone until the stream ends, the writing one by the writer
#   alone until finish().
# ! BOUNDED: `depth` blocks ahead at most (default 2), plus the one in hand.
import queue
import threading

_END = object()


class _Fail:
    def __init__(self, exc):
        self.exc = exc


class Prefetch:
    """Iterate the rows of `fetch()` blocks (an empty block ends the stream),
    fetched on a background thread at most `depth` blocks ahead."""

    def __init__(self, fetch, depth=2, name="prefetch"):
        self._q = queue.Queue(maxsize=max(1, int(depth)))
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, args=(fetch,),
                                   name=name, daemon=True)
        self._t.start()

    def _put(self, item):
        while not self._stop.is_set():
            try:
                self._q.put(item, timeout=0.5)
                return True
            except queue.Full:
                continue
        return False

    def _run(self, fetch):
        try:
            while True:
                block = fetch()
                if not block:
                    self._put(_END)
                    return
                if not self._put(block):
                    return
        except BaseException as exc:                     # noqa: BLE001 -- re-raised by __iter__
            self._put(_Fail(exc))

    def __iter__(self):
        for block in self.blocks():
            yield from block

    def blocks(self):
        """The fetched blocks themselves, in order."""
        while True:
            item = self._q.get()
            if item is _END:
                return
            if isinstance(item, _Fail):
                raise item.exc
            yield item

    def close(self):
        """Stop reading and wait for the reader (at most the block it is
        fetching). Call before the cursor or its connection is touched."""
        self._stop.set()
        self._t.join()


class Writer:
    """write(block) on one background thread, blocks in submission order, at
    most `depth` waiting. finish() returns the sum of write's results and
    re-raises the first failure (after which later blocks are not written)."""

    def __init__(self, write, depth=1, name="writer"):
        self._write = write
        self._q = queue.Queue(maxsize=max(1, int(depth)))
        self.total = 0
        self.error = None
        self._t = threading.Thread(target=self._run, name=name, daemon=True)
        self._t.start()

    def _run(self):
        while True:
            block = self._q.get()
            if block is _END:
                return
            if self.error is not None:
                continue                                 # keep draining: submit never blocks for good
            try:
                self.total += self._write(block)
            except BaseException as exc:                 # noqa: BLE001 -- re-raised by finish
                self.error = exc

    def submit(self, block):
        if self.error is not None:
            raise self.error
        self._q.put(block)

    def finish(self):
        self._q.put(_END)
        self._t.join()
        if self.error is not None:
            raise self.error
        return self.total
