# Phase 4 BANKNIFTY Nemotron Integration Contract

## Runtime
- Hosted NVIDIA endpoint: `https://integrate.api.nvidia.com/v1`
- Model default: `nvidia/nemotron-3-nano-30b-a3b`
- Secret: Render environment variable `NVIDIA_API_KEY`

## Scope
Nemotron is the reasoning layer after deterministic V6R1 state generation. It does not replace V6R1, select option contracts, or calculate execution prices.

## Safety
- Missing API key: fail closed.
- Invalid/non-JSON model response: fail closed.
- Invalid decision schema: fail closed.
- No automatic trade is authorized solely by a model response; downstream signal/contract/execution validation remains required.

## Output
`decision`, `direction`, `confidence`, `signal_quality`, `reason_codes`, `invalidations`.
