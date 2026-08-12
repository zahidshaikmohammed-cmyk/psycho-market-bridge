import json, os, threading, time
from flask import Flask, jsonify
import signal_engine

app = Flask(__name__)


def worker():
    while True:
        try:
            signal_engine.run_once()
        except Exception as e:
            print(f"SIGNAL WORKER ERROR: {type(e).__name__}: {e}", flush=True)
        time.sleep(signal_engine.POLL_SECONDS)


@app.get("/")
def root():
    return jsonify({"service":"PSYCHO SIGNAL ENGINE","status":"ONLINE"})


@app.get("/health")
def health():
    return jsonify({"service":"PSYCHO SIGNAL ENGINE","status":"ONLINE"})


@app.get("/signal")
def signal():
    try:
        with open(signal_engine.OUTPUT_FILE, "r", encoding="utf-8") as f:
            return jsonify(json.load(f))
    except Exception:
        return jsonify({"status":"WAITING","source":"PSYCHO SIGNAL ENGINE"}), 503


if __name__ == "__main__":
    threading.Thread(target=worker, daemon=True, name="psycho-signal-worker").start()
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)
