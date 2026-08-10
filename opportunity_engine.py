# PSYCHO INTRADAY OPPORTUNITY ENGINE — production entrypoint
# Engine 2 implementation lives in opportunity_engine_v2.py.
# This thin wrapper keeps the existing Render start command unchanged.
exec(open('opportunity_engine_v2.py', encoding='utf-8').read(), globals())
