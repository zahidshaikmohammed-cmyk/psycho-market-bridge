import json, os
from flask import Flask, jsonify, request
import joblib

MODEL_FILE=os.environ.get('V6R1_MODEL_FILE','directional_score_model.joblib')
FEATURES=['ret_1','ema_spread','trend_score','atr_pct','rv20','mom12','other_trend','other_rv','trend_bucket','mom_bucket','vol_bucket','other_trend_bucket','deriv_pcr_oi','deriv_pcr_volume','deriv_iv_skew','deriv_contracts','prior_oi_change','deriv_score','futures_prior_oi_change']
model_bundle=joblib.load(MODEL_FILE)
model=model_bundle['model']

app=Flask(__name__)

def classify(score):
    a=abs(score)
    tier='NO_TRADE' if a<.20 else 'D' if a<.30 else 'C' if a<.45 else 'B' if a<.65 else 'A'
    direction='LONG' if score>=.20 else 'SHORT' if score<=-.20 else 'NEUTRAL'
    return direction,tier

@app.get('/')
def root():
    return jsonify({'status':'ONLINE','service':'BANKNIFTY V6R1 MODEL SERVICE','model':'V6R1_RECONSTRUCTION_CANDIDATE','features':len(FEATURES)})

@app.get('/health')
def health():
    return jsonify({'status':'ONLINE','model_loaded':True,'service':'BANKNIFTY V6R1 MODEL SERVICE'})

@app.post('/predict')
def predict():
    payload=request.get_json(silent=True) or {}
    row=payload.get('features',payload)
    missing=[f for f in FEATURES if f not in row]
    if missing: return jsonify({'status':'FEATURES_MISSING','missing':missing}),400
    try:
        x=[[float(row[f]) for f in FEATURES]]
        score=float(model.predict(x)[0])
        direction,tier=classify(score)
        return jsonify({'status':'SIGNAL_READY','instrument':'BANKNIFTY','directional_score':score,'direction':direction,'opportunity_tier':tier,'model_status':'V6R1_RECONSTRUCTION_CANDIDATE'})
    except Exception as e:
        return jsonify({'status':'MODEL_ERROR','error':f'{type(e).__name__}: {e}'}),500

if __name__=='__main__':
    app.run(host='0.0.0.0',port=int(os.environ.get('PORT','10000')))
