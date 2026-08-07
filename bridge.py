import json
from datetime import datetime, timezone

data = {
    "status": "BRIDGE_ONLINE",
    "bridge": "PSYCHO_MARKET_BRIDGE",
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "test": {
        "symbol": "NIFTY",
        "timeframe": "5m",
        "open": 24600,
        "high": 24650,
        "low": 24590,
        "close": 24625
    }
}

with open("market-live.json", "w") as file:
    json.dump(data, file, indent=2)

print("PSYCHO MARKET BRIDGE: market-live.json generated")
