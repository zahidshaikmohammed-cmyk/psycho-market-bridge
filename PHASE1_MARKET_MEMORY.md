# PSYCHO SIGNAL FACTORY — PHASE 1

## Market Memory Engine — v1.0

**Mission:** convert canonical historical market data into reusable historical memory for downstream phases.

### Inputs
- Historical CSV / CSV.GZ datasets
- Underlying candles
- Futures data
- Expired options data
- Contract-level derivatives data

### Outputs
- `phase1_memory/manifest.json`
- `phase1_memory/dataset_catalog.json`
- `phase1_memory/session_memory.jsonl`

### Rules
1. Phase 1 does not generate trading signals.
2. Raw source data is never modified.
3. Every dataset is catalogued with date range, row count, session count and available fields.
4. Session summaries retain OHLC, return, range, volume and OI change where available.
5. Downstream phases must treat historical memory as evidence, not certainty.
6. Credentials and live-market secrets are never stored in the repository.

### Runtime
Set `PSYCHO_RESEARCH_ROOT` to the historical-data root, then run:

`python phase1_market_memory.py`

The engine requires only the Python standard library.
