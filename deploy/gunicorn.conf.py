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
import os

preload_app = os.environ.get("XCP_PRELOAD", "1") == "1"
_WARM = os.environ.get("XCP_PREWARM", "")

if preload_app:
    os.environ["XCP_PREWARM"] = "0"


def when_ready(server):
    """In the master, after the app is imported and before the first fork."""
    if not preload_app:
        return
    import gc
    import database
    try:
        import predict
        predict._loadModel()           # weights and artifacts, no inference
    except Exception as exc:                            # noqa: BLE001
        server.log.warning("preload: model not loaded in the master (%s)", exc)
    database.closePool()               # no socket crosses a fork
    gc.collect()
    gc.freeze()                        # shared pages stay shared
    server.log.info("preload: app and model loaded once; pool closed; gc frozen")


def post_fork(server, worker):
    if not preload_app:
        return
    import threading
    import database
    database._pool = None              # belt and braces: always a fresh pool
    database._slots = None
    if _WARM != "0":
        import app
        threading.Thread(target=app._prewarmPredict, name="predict-warm",
                         daemon=True).start()
