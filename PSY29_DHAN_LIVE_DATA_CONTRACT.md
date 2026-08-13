# PSY29 — DHAN LIVE DATA CONTRACT V1.0

STATUS: LOCKED
STAGE: 2
PURPOSE: Define the exact live-data contract consumed by the PSY29 orchestrator.

## 1. Source hierarchy

Primary live source: DHAN.
Transport layer: PSYCHO MARKET BRIDGE.
Consumer: PSY29 LIVE ORCHESTRATOR.

The orchestrator must not call arbitrary market-data sources when the DHAN bridge is expected to be available.

## 2. Market session

Timezone: Asia/Kolkata (IST).
Market analysis window: 09:15–15:30 IST.
Bridge collection may continue through 15:40 IST for session completion/audit handling.

## 3. Required stock universe

Exactly the 29 symbols in config/ps29_universe.json.
The live-data layer must resolve each symbol to its current NSE security identifier through the Dhan instrument master or a validated cached mapping.

Unknown symbols are rejected. Missing security IDs are a data error, not a reason to substitute another instrument.

## 4. Required timeframes

Minimum live candle payload per stock:
- 1-minute OHLCV
- 5-minute OHLCV

Required contextual data:
- current session timestamp
- current price / latest close
- session high
- session low
- previous trading-day OHLCV
- opening-range high/low once the opening-range definition is available

The orchestrator may derive indicators from these raw candles. Derived values must retain their source timestamp.

## 5. Canonical candle schema

Each candle must contain:

```json
{
  "timestamp": 0,
  "open": 0.0,
  "high": 0.0,
  "low": 0.0,
  "close": 0.0,
  "volume": 0.0
}
```

Timestamps are Unix timestamps and must be interpreted in the bridge's IST session context.

## 6. Canonical stock envelope

The future stock-live artifact consumed by PSY29 must conform conceptually to:

```json
{
  "schema_version": "1.0",
  "status": "LIVE",
  "source": "DHAN",
  "generated_at": "ISO-8601",
  "market_date": "YYYY-MM-DD",
  "symbol": "NESTLEIND",
  "security_id": "validated-DHAN-id",
  "data_age_seconds": 0,
  "candles": {
    "1m": [],
    "5m": []
  },
  "previous_day": {},
  "session": {
    "open": null,
    "high": null,
    "low": null,
    "opening_range_high": null,
    "opening_range_low": null
  }
}
```

## 7. Freshness rules

Every stock payload must expose generated_at and data_age_seconds.

Freshness status:
- FRESH: <= 90 seconds
- STALE: > 90 seconds and <= 180 seconds
- INVALID: > 180 seconds

A STALE/INVALID payload must not produce a new PSY29 trade signal.

## 8. Completeness rules

The data validator must reject a stock payload when:
- symbol is not in the canonical 29
- security_id is missing or unvalidated
- generated_at is missing or invalid
- required 1m or 5m candles are absent during the live session
- candle timestamps are non-monotonic after normalization
- OHLC fields are missing or numerically invalid
- high < max(open, close) or low > min(open, close)
- volume is negative

One failed stock must not stop the other 28 stocks from being evaluated.

## 9. Fail-closed rule

If critical live data for a stock is unavailable, that stock receives:

```text
DATA_STATUS = INVALID
EDGE_STATUS = INSUFFICIENT_DATA
SIGNAL_STATUS = NO_TRADE
```

The system must never invent, extrapolate, or silently reuse an old candle as current market data.

## 10. Bridge compatibility note

The existing bridge already provides DHAN authentication, 60-second refresh behaviour, candle normalization, session filtering, and live market artifacts. Its current implementation is centered on NIFTY/BANKNIFTY, so Stage 2 locks the contract now; Stage 3 will implement/verify the 29-stock acquisition and validation layer rather than pretending that stock-level live coverage already exists.

## 11. No execution

This contract provides market data only.
It does not authorize trades and does not place orders.

## 12. Stage 2 acceptance criteria

Stage 2 passes only when:
1. The contract is committed.
2. The 29-symbol source of truth is linked to the contract.
3. The required 1m/5m stock-data schema is explicit.
4. Freshness and fail-closed rules are explicit.
5. The existing bridge limitation is documented.

Next implementation stage: STAGE 3 — DATA VALIDATION & NORMALIZATION.
