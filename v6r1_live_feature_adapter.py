"""Phase 4 BANKNIFTY V6R1 live feature adapter.

This adapter is intentionally fail-closed. It does not invent market features.
The upstream live bridge must provide the exact 19 V6R1 feature names used by the
trained artifact. Once present, this module validates them, preserves provenance,
and writes banknifty-v6r1-state.json for the Nemotron gateway.
"""
import json, os, time
from datetime import datetime, timezone

INPUT_FILE = os.getenv("V6R1_LIVE_INPUT_FILE", "market-live.json")
STATE_FILE = os.getenv("V6R1_STATE_FILE", "banknifty-v6r1-state.json")
FEATURES = [
    "ret_1", "ema_spread", "trend_score", "atr_pct", "rv20", "mom12",
    "other_trend", "other_rv", "trend_bucket", "mom_bucket", "vol_bucket",
    "other_trend_bucket", "deriv_pcr_oi", "deriv_pcr_volume", "deriv_iv_skew",
    "deriv_contracts", "prior_oi_change", "deriv_score", "futures_prior_oi_change",
]

def _read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def _extract_features(raw):
    # The live source may wrap the feature vector under one of these explicit keys.
    candidates = [raw]
    if isinstance(raw, dict):
        for key in ("features", "v6r1", "banknifty_v6r1", "feature_state"):
            value = raw.get(key)
            if isinstance(value, dict):
                candidates.insert(0, value)
    for obj in candidates:
        if isinstance(obj, dict) and all(k in obj for k in FEATURES):
            out = {}
            for k in FEATURES:
                v = obj[k]
                if isinstance(v, bool):
                    raise ValueError(f"BOOLEAN_FEATURE:{k}")
                out[k] = float(v)
            return out
    missing = [k for k in FEATURES if not any(isinstance(o, dict) and k in o for o in candidates)]
    raise RuntimeError("V6R1_FEATURES_NOT_AVAILABLE:" + ",".join(missing))

def build_state(raw):
    features = _extract_features(raw)
    now = datetime.now(timezone.utc).isoformat()
    return {
        "instrument": "BANKNIFTY",
        "generated_at": now,
        "source": "DHAN_LIVE_BRIDGE",
        "adapter": "V6R1_LIVE_FEATURE_ADAPTER",
        "v6r1": features,
    }

def write_state():
    if not os.path.exists(INPUT_FILE):
        raise RuntimeError("V6R1_LIVE_INPUT_NOT_FOUND")
    raw = _read_json(INPUT_FILE)
    if not raw:
        raise RuntimeError("V6R1_LIVE_INPUT_EMPTY")
    state = build_state(raw)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, separators=(",", ":"))
    os.replace(tmp, STATE_FILE)
    return state

if __name__ == "__main__":
    print(json.dumps(write_state(), indent=2))
