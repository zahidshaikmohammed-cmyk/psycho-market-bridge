#!/usr/bin/env python3
"""PSYCHO Signal Factory Phase 3 — Opportunity & Authorization Engine."""
import argparse,json
from pathlib import Path
import pandas as pd,numpy as np

def score(d):
    d=d.copy(); trend=np.clip(d.trend_score.fillna(0),-1,1); vol=d.rv20.replace([np.inf,-np.inf],np.nan)
    vol_quality=np.where(vol.isna(),0,np.clip(1-(vol/.03),0,1)); momentum=np.clip(np.tanh(d.mom12.fillna(0)/.006),-1,1)
    d['opportunity_score']=.60*trend+.25*momentum+.15*np.sign(trend)*vol_quality
    d['opportunity_strength']=d.opportunity_score.abs()
    d['authorization']=np.where(d.opportunity_strength<.35,'NO_TRADE',np.where(d.opportunity_score>0,'LONG_CANDIDATE','SHORT_CANDIDATE'))
    d.loc[d.regime=='UNKNOWN','authorization']='INSUFFICIENT_DATA'; return d

def main():
    p=argparse.ArgumentParser();p.add_argument('--input-dir',required=True);p.add_argument('--output-dir',required=True);a=p.parse_args();o=Path(a.output_dir);o.mkdir(parents=True,exist_ok=True);s=[]
    for sym in ('nifty','banknifty'):
        d=score(pd.read_csv(Path(a.input_dir)/f'{sym}_market_state.csv.gz',parse_dates=['datetime']));d.to_csv(o/f'{sym}_opportunity.csv.gz',index=False,compression='gzip');x=d.dropna(subset=['opportunity_score']).iloc[-1]
        s.append({'symbol':sym.upper(),'latest_timestamp':str(x.datetime),'regime':x.regime,'opportunity_score':float(x.opportunity_score),'authorization':x.authorization,'strength':float(x.opportunity_strength),'rows':len(d)})
    (o/'phase3_manifest.json').write_text(json.dumps({'phase':'PHASE_3_OPPORTUNITY_AUTHORIZATION','summaries':s,'signals_generated':False,'execution_details_generated':False},indent=2))
if __name__=='__main__':main()
