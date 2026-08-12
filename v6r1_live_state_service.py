import os
from flask import Flask, jsonify
from v6r1_live_feature_adapter import write_state

app = Flask(__name__)

@app.get('/health')
def health():
    try:
        state = write_state()
        return jsonify({'status':'ONLINE','state':'READY','instrument':'BANKNIFTY','generated_at':state['generated_at']})
    except Exception as e:
        return jsonify({'status':'WAITING','state':'NOT_READY','error':str(e)}), 503

@app.post('/refresh')
def refresh():
    try:
        state = write_state()
        return jsonify({'status':'STATE_READY','state':state})
    except Exception as e:
        return jsonify({'status':'STATE_NOT_READY','error':str(e)}), 503

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT','10000')))
