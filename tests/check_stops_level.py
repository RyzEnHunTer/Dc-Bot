import MetaTrader5 as mt5

if not mt5.initialize():
    print("MT5 Init failed")
    exit()

for s in ["XAUUSD", "NAS100"]:
    info = mt5.symbol_info(s)
    print(f"=== {s} ===")
    print(f"trade_stops_level: {info.trade_stops_level} points")
    print(f"point: {info.point}")
    print(f"spread: {info.spread} points")
    print(f"min stop distance in price: {info.trade_stops_level * info.point}")

mt5.shutdown()
