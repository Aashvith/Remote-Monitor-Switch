"""
app.py - A tiny Flask web server that controls the Wemo plug.

Flask turns Python functions into web pages/endpoints. This file has two jobs:
  1. Serve the page (GET /) that shows the buttons and status.
  2. Provide a small JSON API the page calls with JavaScript:
        GET  /api/status   -> {"online": true, "on": false, "name": "Lamp"}
        POST /api/on       -> turns the plug on,  returns the new status
        POST /api/off      -> turns the plug off, returns the new status

Run it:   python app.py        then open http://127.0.0.1:5000
"""

import logging
import os
import threading

import pywemo
import urllib3
from pywemo.ouimeaux_device import probe_wemo
from pywemo.ouimeaux_device.api.service import Action
from flask import Flask, jsonify, render_template

# --- Configuration ---------------------------------------------------------
# Settings come from environment variables, or from config.local.env (a
# git-ignored file next to this script; copy config.example.env to create it).
# Real environment variables win over the file.
def _load_local_config():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.local.env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


_load_local_config()

WEMO_HOST = os.environ.get("WEMO_HOST")
if not WEMO_HOST:
    raise SystemExit(
        "WEMO_HOST is not set. Copy config.example.env to config.local.env "
        "and put your plug's IP address in it."
    )

# 127.0.0.1 means "only this laptop can connect". The app has no login, so we
# don't expose it to the rest of the network yet. When we add Tailscale we
# will decide deliberately how to open it up.
BIND_HOST = os.environ.get("BIND_HOST", "127.0.0.1")
BIND_PORT = int(os.environ.get("BIND_PORT", "5000"))

app = Flask(__name__)
log = logging.getLogger("wemo-web")

# --- Talking to the plug ----------------------------------------------------
# We keep one connected device object and reuse it, because building it
# downloads the plug's setup.xml each time. If the plug drops off the network
# (power cut, router restart) the object goes stale, so on any error we throw
# it away and reconnect on the next request. That is what makes the app
# self-healing.
_device = None
# pywemo's defaults (6 retries with backoff, 3 rediscovery rounds) can block
# one call for over a minute when the plug is unreachable. That would hold our
# lock and freeze every request, so fail fast instead; the page retries itself.
Action.max_rediscovery_attempts = 1
_FAST_RETRY = urllib3.Retry(total=1, backoff_factor=0.2, allowed_methods=["GET", "POST"])
_FAST_TIMEOUT = 2.0
# Flask can handle several requests at once in different threads, and the
# plug handles one request at a time, so a lock keeps our calls in line.
_lock = threading.Lock()


def get_device():
    """Return a connected pywemo device, connecting first if needed."""
    global _device
    if _device is None:
        # Probe with a short timeout (pywemo's default is 10 s per port).
        port = probe_wemo(WEMO_HOST, probe_timeout=_FAST_TIMEOUT)
        if port is None:
            raise ConnectionError(f"No Wemo answering at {WEMO_HOST}")
        url = f"http://{WEMO_HOST}:{port}/setup.xml"
        dev = pywemo.device_from_description(url)
        if dev is None:
            raise ConnectionError(f"Could not read device description from {url}")
        dev.session.retries = _FAST_RETRY
        dev.session.timeout = _FAST_TIMEOUT
        _device = dev
        log.info("Connected to %s at %s", dev.name, WEMO_HOST)
    return _device


def read_status():
    """Ask the plug for its state and return a dict for the page."""
    global _device
    try:
        dev = get_device()
        # force_update=True makes a real network call instead of returning
        # pywemo's cached value, so the display reflects reality (including
        # someone pressing the physical button on the plug).
        state = dev.get_state(force_update=True)
        return {"online": True, "on": bool(state), "name": dev.name}
    except Exception as exc:  # any failure -> report offline, reconnect later
        log.warning("Plug unreachable: %s", exc)
        _device = None
        return {"online": False, "on": None, "name": None}


# --- Routes -----------------------------------------------------------------
@app.get("/")
def index():
    # render_template fills in templates/index.html
    return render_template("index.html")


@app.get("/api/status")
def api_status():
    with _lock:
        return jsonify(read_status())


def set_power(turn_on):
    """Switch the plug, then return its confirmed state."""
    global _device
    with _lock:
        try:
            dev = get_device()
            dev.on() if turn_on else dev.off()
        except Exception as exc:
            log.warning("Switch failed: %s", exc)
            _device = None
            return jsonify({"online": False, "on": None, "name": None}), 502
        return jsonify(read_status())


# POST (not GET) for actions that change something: a browser pre-fetching or
# a link scanner following GET links must never be able to flip your lamp.
@app.post("/api/on")
def api_on():
    return set_power(True)


@app.post("/api/off")
def api_off():
    return set_power(False)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    # debug=False is deliberate and hard-coded. Flask's debug mode runs an
    # interactive console that lets anyone who can reach the page execute
    # Python code on this laptop. It must never be on when the app is
    # reachable from anywhere but this machine, so there is no setting for it.
    app.run(host=BIND_HOST, port=BIND_PORT, debug=False)
