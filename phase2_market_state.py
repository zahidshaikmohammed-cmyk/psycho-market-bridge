#!/usr/bin/env python3
"""PSYCHO Signal Factory Phase 2 — Market State Engine."""
import argparse,json
from pathlib import Path
import pandas as pd, numpy as np

def build_state(path):
    d=pd.read_csv(path,compression='gzip',parse_dates=['datetime']).sort_values('datetime').reset_index(drop=True)
    c=d.close
    d['ret_1']=c.pct_change(); d['ema20']=c.ewm(span=20,adjust=False).mean(); d['ema50']=c.ewm(span=50,adjust=False).mean()
    tr=pd.concat([d.high-d.low,(d.high-c.shift()).abs(),(d.low-c.shift()).abs()],axis=1).max(axis=1)
    d['atr14']=tr.rolling(14).mean(); d['atr_pct']=d.atr14/c; d['rv20']=d.ret_1.rolling(20).std()*np.sqrt(78)
    d['mom12']=c.pct_change(12); d['ema_spread']=(d.ema20-d.ema50)/c
    d['trend_score']=(np.tanh(d.ema_spread/.002)+np.tanh(d.mom12/.004))/2
    def cls(r):
        if pd.isna(r.trend_score) or pd.isna(r.rv20): return 'UNKNOWN'
        t=abs(r.trend_score); v=r.rv20
        if t>=.55 and v>=.015:return 'TRENDING_EXPANSION'
        if t>=.55:return 'TRENDING'
        if v>=.015:return 'VOLATILE_RANGE'
        if t<=.20:return 'RANGE'
        return 'TRANSITION'
    d['regime']=d.apply(cls,axis=1); d['directional_bias']=np.where(d.trend_score>.20,'BULLISH',np.where(d.trend_score<-.20,'BEARISH','NEUTRAL'))
    return d

def main():
    p=argparse.ArgumentParser();p.add_argument('--input-dir',required=True);p.add_argument('--output-dir',required=True);a=p.parse_args();o=Path(a.output_dir);o.mkdir(parents=True,exist_ok=True);s=[]
    for sym in ('nifty','banknifty'):
        d=build_state(Path(a.input_dir)/f'{sym}_5m_canonical.csv.gz');d.to_csv(o/f'{sym}_market_state.csv.gz',index=False,compression='gzip');x=d.dropna(subset=['regime']).iloc[-1];s.append({'symbol':sym.upper(),'latest_timestamp':str(x.datetime),'latest_regime':x.regime,'latest_directional_bias':x.directional_bias,'rows':len(d)})
    (o/'phase2_manifest.json').write_text(json.dumps({'phase':'PHASE_2_MARKET_STATE','summaries':s,'signals_generated':False},indent=2))
if __name__=='__main__':main()
