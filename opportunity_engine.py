# PSYCHO OPPORTUNITY ENGINE — precision wrapper
# Load the production engine without executing its blocking __main__ server,
# then register Replay Lab routes on the same Flask app.
source=open("opportunity_engine_v4.py",encoding="utf-8").read()
source=source.replace(
    'c5,c15,c1h=tf(raw,"5M"),tf(raw,"15M"),tf(raw,"1H")',
    'c5=[x for x in tf(raw,"5M") if x["dt"]+timedelta(minutes=5)<=t];c15=[x for x in tf(raw,"15M") if x["dt"]+timedelta(minutes=15)<=t];c1h=[x for x in tf(raw,"1H") if x["dt"]+timedelta(hours=1)<=t]'
)
source=source.replace(
    '<div class="row"><span>Entry / Actual LTP</span><b>{{x.entry}}</b></div>',
    '<div class="row"><span>Entry Price</span><b>{{x.entry}}</b></div><div class="row"><span>Actual LTP</span><b>{{x.live}}</b></div>'
)
# opportunity_engine.py is the actual process entrypoint, so prevent the
# embedded v4 source from calling app.run() before Replay Lab routes load.
source=source.replace('if __name__=="__main__":app.run(host="0.0.0.0",port=10000)', '# embedded server disabled; wrapper starts the app after all routes are registered')
exec(source,globals())
exec(open("replay_lab.py",encoding="utf-8").read(),globals())

if __name__=="__main__":
    app.run(host="0.0.0.0",port=10000)
