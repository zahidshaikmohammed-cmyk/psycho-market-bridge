#!/usr/bin/env python3
"""BANKNIFTY V6R1 live feature adapter.

Reads the existing read-only Phase-2 bridge JSON files and emits the V6R1
19-feature state. Only formulas recovered and validated against canonical V6
are computed here. Missing/unresolved fields fail closed; they are never
fabricated.
"""
import json, math, sys
from pathlib import Path
import numpy as np
import pandas as pd

FEATURES = [
    "ret_1", "ema_spread", "trend_score", "atr_pct", "rv20", "mom12",
    "other_trend", "other_rv", "trend_bucket", "mom_bucket", "vol_bucket",
    "other_trend_bucket", "deriv_pcr_oi", "deriv_pcr_volume", "deriv_iv_skew",
    "deriv_contracts", "prior_oi_change", "deriv_score", "futures_prior_oi_change",
]


def candles(path):
    obj = json.loads(Path(path).read_text())
    rows = (obj.get("timeframes") or {}).get("5M") or []
    if not rows:
        rows = (obj.get("timeframes") or {}).get("1M") or []
    if not rows:
        raise RuntimeError(f"NO_CANDLES:{path}")
    return pd.DataFrame(rows)


def core(df):
    close = pd.to_numeric(df["close"], errors="coerce")
    high = pd.to_numeric(df["high"], errors="coerce")
    low = pd.to_numeric(df["low"], errors="coerce")
    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    ema_spread = (ema20 - ema50) / close
    mom12 = close.pct_change(12)
    trend_score = (np.tanh(ema_spread / 0.002) + np.tanh(mom12 / 0.004)) / 2
    tr = pd.concat([(high-low), (high-close.shift(1)).abs(),
                    (low-close.shift(1)).abs()], axis=1).max(axis=1)
    atr_pct = tr.rolling(14).mean() / close
    ret_1 = close.pct_change(1)
    rv20 = ret_1.rolling(20).std()
    return {
        "ret_1": float(ret_1.iloc[-1]),
        "ema_spread": float(ema_spread.iloc[-1]),
        "trend_score": float(trend_score.iloc[-1]),
        "atr_pct": float(atr_pct.iloc[-1]),
        "rv20": float(rv20.iloc[-1]),
        "mom12": float(mom12.iloc[-1]),
    }


def option_features(path):
    obj = json.loads(Path(path).read_text())
    strikes = obj.get("strikes") or {}
    ce_oi = pe_oi = ce_vol = pe_vol = 0.0
    iv = []
    contracts = 0
    for row in strikes.values():
        contracts += 1
        for side, acc in (("CE", "ce"), ("PE", "pe")):
            leg = row.get(side) or {}
            oi = float(leg.get("oi") or 0)
            vol = float(leg.get("volume") or 0)
            if side == "CE": ce_oi += oi; ce_vol += vol
            else: pe_oi += oi; pe_vol += vol
            g = leg.get("greeks") or {}
            if g.get("iv") is not None:
                try: iv.append(float(g["iv"]))
                except (TypeError, ValueError): pass
    pcr_oi = pe_oi / ce_oi if ce_oi else np.nan
    pcr_vol = pe_vol / ce_vol if ce_vol else np.nan
    return {
        "deriv_pcr_oi": pcr_oi,
        "deriv_pcr_volume": pcr_vol,
        "deriv_iv_skew": np.nan,  # exact V6 mapping not proven
        "deriv_contracts": float(contracts),
    }


def finite(x):
    try: return math.isfinite(float(x))
    except Exception: return False


def build(data_dir):
    root = Path(data_dir)
    bank = core(candles(root / "banknifty-live.json"))
    nifty = core(candles(root / "nifty-live.json"))
    opt = option_features(root / "banknifty-option-chain.json")
    state = {**bank, "other_trend": nifty["trend_score"], "other_rv": nifty["rv20"], **opt}

    # These mappings are intentionally blocked until the original V6
    # transformation is proven row-by-row.
    unresolved = [
        "trend_bucket", "mom_bucket", "vol_bucket", "other_trend_bucket",
        "prior_oi_change", "deriv_score", "futures_prior_oi_change",
        "deriv_iv_skew",
    ]
    missing = [k for k in FEATURES if k not in state or not finite(state[k])]
    missing = sorted(set(missing + unresolved))
    if missing:
        raise RuntimeError("FAIL_CLOSED_UNRESOLVED_V6_FEATURES:" + ",".join(missing))
    return state


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: python v6r1_live_feature_engine.py <bridge-data-dir> <output.json>")
    result = build(sys.argv[1])
    result.update({"contract": "BANKNIFTY_V6R1_19F_V1", "source": "DHAN_PHASE2_BRIDGE"})
    Path(sys.argv[2]).write_text(json.dumps(result, indent=2))
    print(json.dumps({"status": "OK", "output": sys.argv[2]}))
