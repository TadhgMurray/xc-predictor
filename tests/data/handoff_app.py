"""A WSGI app for tests/test_reload_handoff.py: answers its code version."""
import os
import time

VERSION = open(os.environ["HANDOFF_VERSION_FILE"]).read().strip()
time.sleep(float(os.environ.get("HANDOFF_BOOT_S", "0")))     # a slow preload


def app(environ, start_response):
    start_response("200 OK", [("Content-Type", "text/plain")])
    return [f"{VERSION} {os.getppid()}".encode()]
