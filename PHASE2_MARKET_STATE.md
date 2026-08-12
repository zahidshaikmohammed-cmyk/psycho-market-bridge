# Phase 2 — Market State Engine

Consumes Phase 1 canonical 5-minute NIFTY/BANKNIFTY data and produces deterministic market-state features: EMA20/EMA50, ATR14, realized volatility, momentum, trend score, regime, and directional bias.

Regimes: TRENDING_EXPANSION, TRENDING, VOLATILE_RANGE, RANGE, TRANSITION, UNKNOWN.

This phase does not generate trade signals, entries, stops, or targets.