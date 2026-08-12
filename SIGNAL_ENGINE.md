# PSYCHO Signal Engine

Live signal-only engine for NIFTY and BANKNIFTY.

## Current state
- Reads live market data from PSYCHO MARKET BRIDGE.
- Evaluates the Filtered ORB strategy contract.
- Writes `signal-live.json` atomically.
- Does not place orders.
- Strategy is disabled until the exact rulebook is locked.

## Required environment
- `BRIDGE_BASE_URL` — public/internal URL of the running market bridge.
- `SIGNAL_POLL_SECONDS` — polling interval; default 5 seconds.
- `SIGNAL_RULES_FILE` — default `strategy_rules.json`.
- `SIGNAL_OUTPUT_FILE` — default `signal-live.json`.

## Safety invariant
No missing trigger may be invented by the engine. A rule must be explicitly defined before it can authorize a live signal.
