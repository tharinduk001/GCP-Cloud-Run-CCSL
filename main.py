"""
Cloud Run Demo App
-------------------
A tiny, dependency-light Flask service built specifically to demonstrate
Google Cloud Run concepts in a live session:

  - Reads Cloud Run's built-in runtime environment variables
    (K_SERVICE, K_REVISION, K_CONFIGURATION) to prove the container
    doesn't know anything special - Cloud Run injects this at runtime.
  - Exposes /health for Cloud Run startup/liveness probes.
  - Exposes /api/info as a JSON endpoint (nice for curl demos).
  - Uses an APP_COLOR env var + APP_VERSION so you can visually tell
    "blue" vs "green" revisions apart during a traffic-split demo.
  - Keeps an in-memory request counter to show that each revision/
    instance is independent and stateless (counter resets on new
    instances - great talking point for "don't store state in the
    container" during the session).
"""

import os
import socket
import datetime
from flask import Flask, jsonify, render_template

app = Flask(__name__)

# In-memory counter - intentionally NOT persisted anywhere.
# Demonstrates that Cloud Run instances are ephemeral/stateless.
request_count = 0

APP_COLOR = os.environ.get("APP_COLOR", "blue")
APP_VERSION = os.environ.get("APP_VERSION", "v1.0.0")

COLOR_MAP = {
    "blue": "#2563eb",
    "green": "#16a34a",
    "amber": "#d97706",
}


def get_metadata():
    """Collect Cloud Run + runtime metadata to display/return."""
    return {
        "service": os.environ.get("K_SERVICE", "local-dev"),
        "revision": os.environ.get("K_REVISION", "local-dev-rev"),
        "configuration": os.environ.get("K_CONFIGURATION", "local-dev-config"),
        "region": os.environ.get("CLOUD_RUN_REGION", "unknown"),
        "hostname": socket.gethostname(),
        "color": APP_COLOR,
        "version": APP_VERSION,
        "port": os.environ.get("PORT", "8080"),
        "utc_time": datetime.datetime.utcnow().isoformat() + "Z",
    }


@app.route("/")
def index():
    global request_count
    request_count += 1
    meta = get_metadata()
    meta["hit_count"] = request_count
    return render_template(
        "index.html",
        meta=meta,
        accent=COLOR_MAP.get(APP_COLOR, "#2563eb"),
    )


@app.route("/health")
def health():
    """Used by Cloud Run startup & liveness probes."""
    return jsonify(status="ok"), 200


@app.route("/api/info")
def api_info():
    global request_count
    request_count += 1
    meta = get_metadata()
    meta["hit_count"] = request_count
    return jsonify(meta)


if __name__ == "__main__":
    # Local dev only. In the container, gunicorn serves the app (see Dockerfile).
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=True)
