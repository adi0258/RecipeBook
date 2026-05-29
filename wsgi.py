import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "recipebook"))

from app import app, init_db  # noqa: F401 — 'app' is the WSGI entrypoint

application = app


