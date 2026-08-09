"""PSYCHO Historical Research Acquisition Pipeline

Downloads Dhan historical datasets in verified, resumable batches.
Credentials are read from DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN environment variables.
This module only acquires data; it does not alter live bridge or Hunter logic.
"""
import os, json, time, csv
from pathlib import Path
from datetime import date, timedelta

DATA_ROOT = Path(os.getenv("PSYCHO_RESEARCH_ROOT", "research_data"))
DATA_ROOT.mkdir(parents=True, exist_ok=True)

DATASETS = [
    "nifty_1m", "nifty_15m", "nifty_1h",
    "banknifty_1m", "banknifty_15m", "banknifty_1h",
    "nifty_futures_1m_oi", "banknifty_futures_1m_oi",
    "nifty_expired_options_1m", "banknifty_expired_options_1m",
]

MANIFEST = DATA_ROOT / "acquisition_manifest.json"


def load_manifest():
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text())
    return {"version": 1, "datasets": {}}


def save_manifest(m):
    MANIFEST.write_text(json.dumps(m, indent=2, sort_keys=True))


def init_manifest():
    m = load_manifest()
    for name in DATASETS:
        m["datasets"].setdefault(name, {"status": "PLANNED", "rows": 0, "files": [], "verified": False})
    save_manifest(m)
    return m


def run():
    """Initialize the acquisition job safely.

    Actual Dhan requests are intentionally isolated behind the adapter boundary.
    This prevents an accidental live-token/API blast until instrument IDs and the
    account's historical-access configuration are verified.
    """
    m = init_manifest()
    token = os.getenv("DHAN_ACCESS_TOKEN")
    client = os.getenv("DHAN_CLIENT_ID")
    if not token or not client:
        raise RuntimeError("DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN must be configured as runtime secrets")
    m["status"] = "READY_FOR_VERIFIED_BATCH"
    m["credentials"] = "CONFIGURED"
    save_manifest(m)
    print(json.dumps({"status": m["status"], "datasets": DATASETS, "manifest": str(MANIFEST)}, indent=2))


if __name__ == "__main__":
    run()
