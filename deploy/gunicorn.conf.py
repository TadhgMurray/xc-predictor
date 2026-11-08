# deploy/gunicorn.conf.py -- the site's gunicorn settings that need code.
#
#   gunicorn -c /srv/xc-predictor/deploy/gunicorn.conf.py -w 24 ... app:app
#
# ★ ONE COPY OF WHAT EVERY WORKER LOADS (2026-11-08). Without preloading,
#   each of the 24 workers imports the app, torch and the model itself, so
#   the box holds 24 private copies. With preload_app the master imports the
#   app and loads the model once, and the workers are forked from it: those
#   pages stay shared (copy-on-write) until a worker writes to them, and
#   gc.freeze() keeps the garbage collector from writing to all of them.
#   XCP_PRELOAD=0 turns it off (each worker loads its own, as before).
#
# ! WHAT MUST NOT CROSS THE FORK:
#   - database connections: app.py's import reads the school labels through
#     the pool (school_identity.loadLabels), so the master holds one. It is
#     closed in when_ready, before any worker exists, and the master never
#     opens another (max-requests respawns fork from this same master).
#   - torch's thread pool: loading the weights runs no op, so none exists
#     yet; the first prediction (warm-up) runs in each worker, never here.
#   - the predict warm-up thread app.py starts at import: off in the master
#     (XCP_PREWARM=0 before the import) and started per worker in post_fork.
#   - ★ torch's thread count (2026-11-08, tested): a model loaded with
#     torch set to more than one thread leaves an OpenMP pool in the master,
#     and every forked child HUNG on its first forward pass (3 of 3, killed
#     by a 15 s alarm); loaded with one thread, all of them ran. So the
#     master loads with XCP_TORCH_THREADS=1 and post_fork gives each worker
#     the count predict.py would have chosen (cores // 8).
import os

preload_app = os.environ.get("XCP_PRELOAD", "1") == "1"
_WARM = os.environ.get("XCP_PREWARM", "")
_TORCH_THREADS = os.environ.get("XCP_TORCH_THREADS", "")

if preload_app:
    os.environ["XCP_PREWARM"] = "0"
    os.environ["XCP_TORCH_THREADS"] = "1"   # the master's load: one thread


def _workerTorchThreads():
    return int(_TORCH_THREADS or max(1, (os.cpu_count() or 1) // 8))


def when_ready(server):
    """In the master, after the app is imported and before the first fork."""
    _startWarmer(server)
    if not preload_app:
        return
    import gc
    import database
    try:
        import torch
        torch.set_num_threads(1)       # before anything torch runs here
    except Exception:                                   # noqa: BLE001
        pass
    import predict
    # what each worker's warm-up loaded for itself, loaded once: the model
    # (weights and artifacts, no inference), feature extraction with the
    # corrections tables (the largest of them), the forecast, race_sim
    for name, fn in (("model", predict._loadModel), ("features", predict._fx),
                     ("forecast", predict._fc),
                     ("race_sim", lambda: __import__("race_sim"))):
        try:
            fn()
        except Exception as exc:                        # noqa: BLE001
            server.log.warning("preload: %s not loaded in the master (%s)", name, exc)
    database.closePool()               # no socket crosses a fork
    gc.collect()
    gc.freeze()                        # shared pages stay shared
    server.log.info("preload: app and model loaded once; pool closed; gc frozen")


def _startWarmer(server):
    """scripts/warm_site.py in its own process, detached (XCP_WARM_SITE=0:
    off). Never a thread here: the master forks workers for its whole life
    (max-requests), and a thread alive at a fork can leave a lock held in
    the child."""
    if os.environ.get("XCP_WARM_SITE", "1") != "1":
        return
    import subprocess
    import sys
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = os.path.join(root, "scripts", "warm_site.py")
    if not os.path.exists(script):
        return
    try:
        subprocess.Popen([sys.executable, "-u", script], start_new_session=True,
                         stdin=subprocess.DEVNULL)
        server.log.info("warm: scripts/warm_site.py started")
    except Exception as exc:                            # noqa: BLE001
        server.log.warning("warm: not started (%s)", exc)


def post_fork(server, worker):
    if not preload_app:
        return
    import threading
    import database
    database._pool = None              # belt and braces: always a fresh pool
    database._slots = None
    os.environ["XCP_TORCH_THREADS"] = str(_workerTorchThreads())
    try:
        import sys
        if "torch" in sys.modules:
            sys.modules["torch"].set_num_threads(_workerTorchThreads())
    except Exception:                                   # noqa: BLE001
        pass
    if _WARM != "0":
        import app
        threading.Thread(target=app._prewarmPredict, name="predict-warm",
                         daemon=True).start()
