import json, os, threading
from flask import Flask, jsonify
import signal_engine

app = Flask(__name__)


def worker():
    signal_engine.run_loop()

@app.get('/')
def root():
    return jsonify({
        'status': 'ONLINE',
        'service': 'PSYCHO SIGNAL ENGINE',
        'endpoints': ['/health', '/signal'],
        'mode': 'SIGNAL_ONLY',
        'nemotron': 'REMOVED'
    })

@app.get('/health')
def health():
    return jsonify({
        'status':'ONLINE',
        'service':'PSYCHO SIGNAL ENGINE',
        'engine':'RUNNING',
        'nemotron_wiring':'REMOVED'
    })

@app.get('/signal')
def signal():
    path = signal_engine.OUTPUT_FILE
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return jsonify(json.load(f))
    except Exception:
        return jsonify({'status':'WAITING','service':'PSYCHO SIGNAL ENGINE'}), 503

if __name__ == '__main__':
    threading.Thread(target=worker, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT','10000')))
