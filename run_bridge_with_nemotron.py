import hashlib
import json
import os
import threading
import time
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

from flask import Flask, jsonify

IST = ZoneInfo("Asia/Kolkata")
STATE_FILE = os.getenv("NEMOTRON_STATE_FILE", "banknifty-v6r1-state.json")
AUTOSTART = os.getenv("NEMOTRON_AUTOSTART", "1").strip().lower() in {"1", "true", "yes", "on"}
INTERVAL = max(15, int(os.getenv("NEMOTRON_POLL_SECONDS", "30")))
MAX_STATE_AGE = max(30, int(os.getenv("NEMOTRON_MAX_STATE_AGE_SECONDS", "120")))

app = Flask(__name__)
lock = threading.Lock()
status = {
    "service": "PHASE_4_BANKNIFTY_NEMOTRON",
    "autostart": AUTOSTART,
    "state_file": STATE_FILE,
    "status": "BOOTING",
    "last_run": None,
    "last_state_hash": None,
    "last_error": None,
    "decision": None,
}


def market_open():
    now = datetime.now(IST)
    return now.weekday() < 5 and dt_time(9, 15) <= now.time() <= dt_time(15, 40)


def load_fresh_state():
    if not os.path.exists(STATE_FILE):
        raise RuntimeError("V6R1_STATE_NOT_AVAILABLE")
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        state = json.load(f)
    if not isinstance(state, dict) or "v6r1" not in state:
        raise RuntimeError("V6R1_STATE_SCHEMA_INVALID")
    stamp = state.get("generated_at") or state.get("snapshot_generated_at")
    if stamp:
        parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        age = (datetime.now(parsed.tzinfo) - parsed).total_seconds()
        if age > MAX_STATE_AGE:
            raise RuntimeError("V6R1_STATE_STALE")
        if age < -10:
            raise RuntimeError("V6R1_STATE_TIMESTAMP_INVALID")
    return state


def evaluate_once():
    from nemotron_signal_client import evaluate_market_state

    if not market_open():
        status["status"] = "IDLE_MARKET_CLOSED"
        return
    state = load_fresh_state()
    digest = hashlib.sha256(json.dumps(state, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if digest == status["last_state_hash"]:
        status["status"] = "WAITING_NEW_STATE"
        return
    with lock:
        result = evaluate_market_state(state)
    status.update({
        "status": "NEMOTRON_DECISION_READY",
        "last_run": datetime.now(IST).isoformat(),
        "last_state_hash": digest,
        "last_error": None,
        "decision": result,
    })


def worker():
    status["status"] = "SAFE_AUTOSTART_READY"
    while True:
        try:
            if AUTOSTART:
                evaluate_once()
            else:
                status["status"] = "AUTOSTART_DISABLED"
        except Exception as exc:
            status["status"] = "SAFE_WAIT"
            status["last_error"] = f"{type(exc).__name__}: {exc}"
        time.sleep(INTERVAL)


@app.get("/")
def root():
    return jsonify(status)


@app.get("/health")
def health():
    return jsonify({"status": "ONLINE", "service": status["service"], "worker_status": status["status"]})


if __name__ == "__main__":
    threading.Thread(target=worker, daemon=True, name="nemotron-safe-autostart").start()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
