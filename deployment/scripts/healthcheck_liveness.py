"""Container health check for workers: liveness file must have been touched recently."""

import os
import sys
import time

path = os.environ.get("LIVENESS_FILE", "/tmp/worker-alive")
max_age = int(os.environ.get("LIVENESS_MAX_AGE", "45"))
try:
    sys.exit(0 if time.time() - os.path.getmtime(path) < max_age else 1)
except OSError:
    sys.exit(1)
