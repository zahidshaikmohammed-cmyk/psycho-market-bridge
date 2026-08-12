import json, os, threading
from flask import Flask, jsonify
import signal_engine
from nemotron_live_gateway import evaluate_live_state

app = Flask(__name__)


def worker():
    signal_engine.run_loop()

@app.get('/')
def root():
    return jsonify({
        'status': 'ONLINE',
        'service': 'PSYCHO SIGNAL ENGINE',
        'endpoints': ['/health', '/signal', '/nemotron'],
        'mode': 'SIGNAL_ONLY',
        'nemotron': 'WIRED_FAIL_CLOSED'
    })

@app.get('/health')
def health():
    return jsonify({
        'status':'ONLINE',
        'service':'PSYCHO SIGNAL ENGINE',
        'nemotron_wiring':'READY',
        'nemotron_authority':'NONE'
    })

@app.get('/signal')
def signal():
    path = signal_engine.OUTPUT_FILE
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return jsonify(json.load(f))
    except Exception:
        return jsonify({'status':'WAITING','service':'PSYCHO SIGNAL ENGINE'}), 503

@app.get('/nemotron')
def nemotron():
    try:
        return jsonify(evaluate_live_state())
    except Exception as exc:
        return jsonify({
            'status':'WAITING_FOR_V6R1_STATE',
            'service':'PHASE_4_BANKNIFTY_NEMOTRON',
            'error':f'{type(exc).__name__}: {exc}'
        }), 503

if __name__ == '__main__':
    threading.Thread(target=worker, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT','10000')))
