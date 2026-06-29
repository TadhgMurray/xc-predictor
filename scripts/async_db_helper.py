# Project: xc-predictor
# Author: Tadhg Murray
# Subset: Database
# Date: 6/6/2026
# File Title: async_db_helper.py
# Purpose: every DB function in database.py (getQueueStatus, markScraped,
# _saveXCMeet, etc.) uses psycopg2, which is SYNCHRONOUS — each call
# blocks whatever thread calls it for the full network round-trip
# (measured at ~100ms per call to Cloud SQL from this machine).
 
# The scraper is asyncio-based: ALL 100 sessions share ONE thread (the
# event loop thread). When session A calls getQueueStatus() directly,
# that ~100ms isn't "session A waiting" — it's the ENTIRE EVENT LOOP
# frozen for 100ms. No other session's code can run AT ALL during that
# window, including sessions that are just waiting on
# asyncio.sleep() — their sleep timers can't even be checked.
 
# THE FIX: run each blocking DB call in a separate thread via
# loop.run_in_executor(). The event loop thread stays free to run other
# sessions' coroutines while a background thread waits on the DB.
 
# THE PATTERN: instead of
#     status = getQueueStatus(meet_id, sport)
# write
#     status = await runDbCall(getQueueStatus, meet_id, sport)
 
# runDbCall works for ANY synchronous database.py function — it's a
# generic wrapper, not specific to getQueueStatus. Use it for every
# blocking DB call inside runSession (getQueueStatus, markScraped,
# _saveXCMeet, etc.) — anywhere database.py is called from inside an
# `async def` function.

import asyncio
from functools import partial
 
 
# ─── Constants ───────────────────────────────────────────────────────────────
 
# How many DB calls can run truly simultaneously, across ALL sessions.
# This is a SEPARATE limit from psycopg2's pool MAX_CONN=150 — this
# caps how many OS threads we spawn for DB work at once. Set comfortably
# below MAX_CONN so the thread pool never tries to check out more
# connections than the pool can hand out (which would just mean some
# threads block on _pool.getconn() instead — harmless, but pointless).
DB_EXECUTOR_MAX_WORKERS = 100

# ─── Module state ────────────────────────────────────────────────────────────
 
# A dedicated thread pool, separate from asyncio's default executor.
# Created lazily (on first use) via _getExecutor() — see below for why.
_db_executor = None

# ─── Helper functions ────────────────────────────────────────────────────────

# _getExecutor
# Purpose: Returns the shared thread pool, creating it on first call.
#          Lazy creation (rather than creating it at import time) means
#          importing this module never has side effects — matches the
#          pattern database.py already uses for _pool in getConn().
# Arguments: None.
# Output: A concurrent.futures.ThreadPoolExecutor.
def _getExecutor():

    # global says modify the global variable, don't create
    # a new local one.
    global _db_executor
    
    # On first call creates the pool and stores in _db_executor,
    # on next call skips creation, returns already-existing pool.
    # Lazy initialization - don't create until it's needed, and
    # only create once.
    if _db_executor is None:
        # Imported here (not at module top) so this module has zero
        # side effects until actually used.
        from concurrent.futures import ThreadPoolExecutor

        # Creates a pool of up to 100 real OS threads to run jobs.
        _db_executor = ThreadPoolExecutor(
            max_workers=DB_EXECUTOR_MAX_WORKERS,
            thread_name_prefix="db-worker",
        )
 
    return _db_executor

# runDbCall
# Purpose: Runs any synchronous database.py function in a background
#          thread, returning control to the asyncio event loop until
#          it completes. This is the ONE function every blocking DB
#          call in runSession should be wrapped with.
# Arguments:
#           func: a synchronous function from database.py, e.g.
#                 getQueueStatus (passed WITHOUT calling it — no
#                 parentheses).
#           *args, **kwargs: arguments to pass to func, exactly as
#                 you'd normally call func(*args, **kwargs).
# Output: Whatever func would normally return — awaiting this gives
#         you the real return value, e.g. an int status or None.
#
# Example:
#     # Before (blocks the WHOLE event loop for ~100ms):
#     status = getQueueStatus(meet_id, sport)
#
#     # After (only this session pauses; others keep running):
#     status = await runDbCall(getQueueStatus, meet_id, sport)
async def runDbCall(func, *args, **kwargs):
    
    # Gets a handle to the event loop that's running this coroutine.
    loop = asyncio.get_running_loop()
 
    # run_in_executor's signature is (executor, func, *args) — it does
    # NOT accept **kwargs directly. functools.partial bundles func
    # together with both *args and **kwargs into a single zero-argument
    # callable, which run_in_executor can call as func().
    bound_func = partial(func, *args, **kwargs)
    
    # Hands func to a background OS thread to run. It returns a Future
    # without waiting for the func to finish. When the thread finished
    # it signals the event loop and resumes the coroutine with that result.
    return await loop.run_in_executor(_getExecutor(), bound_func)