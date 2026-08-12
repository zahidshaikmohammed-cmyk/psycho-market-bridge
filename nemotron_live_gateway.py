import json, os
from typing import Any, Dict
from nemotron_signal_client import evaluate_market_state

STATE_FILE = os.getenv('NEMOTRON_STATE_FILE', 'banknifty-v6r1-state.json')

def load_state() -> Dict[str, Any]:
    if not os.path.exists(STATE_FILE):
        raise RuntimeError('V6R1_STATE_NOT_AVAILABLE')
    with open(STATE_FILE, 'r', encoding='utf-8') as f:
        state = json.load(f)
    if not isinstance(state, dict) or 'v6r1' not in state:
        raise RuntimeError('V6R1_STATE_SCHEMA_INVALID')
    return state

def evaluate_live_state() -> Dict[str, Any]:
    state = load_state()
    result = evaluate_market_state(state)
    return {
        'status': 'NEMOTRON_DECISION_READY',
        'engine': 'PHASE_4_BANKNIFTY_NEMOTRON',
        'source': 'V6R1_LIVE_STATE',
        'decision': result,
    }
