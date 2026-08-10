# PSYCHO INTRADAY OPPORTUNITY ENGINE — production entrypoint
# Engine 2 lifecycle implementation: locked signals, live TP/SL monitoring,
# today's closed-trade ledger, and pullback-only re-entry after a closed trade.
exec(open('opportunity_engine_v3.py', encoding='utf-8').read(), globals())
