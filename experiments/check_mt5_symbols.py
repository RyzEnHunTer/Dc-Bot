import MetaTrader5 as mt5

if not mt5.initialize():
    print("MT5 Init Failed:", mt5.last_error())
else:
    term_info = mt5.terminal_info()
    acc_info = mt5.account_info()
    print("MT5 Connected Successfully!")
    if acc_info:
        print(f"Login: {acc_info.login}")
        print(f"Broker: {acc_info.company}")
        print(f"Server: {acc_info.server}")
    
    candidates = ['XAUUSD', 'GOLD', 'USDJPY', 'EURUSD', 'GBPNZD', 'GBPCAD', 'GBPJPY', 'US500', 'SPX500', 'US30', 'USTEC', 'NAS100']
    print("\n--- Symbol Availability ---")
    for s in candidates:
        sym = mt5.symbol_info(s)
        if sym:
            print(f"  [EXACT] {s} (Spread: {sym.spread}, Digits: {sym.digits}, MinLot: {sym.volume_min})")
        else:
            matches = mt5.symbols_get(f"*{s}*")
            if matches:
                names = [m.name for m in matches[:3]]
                print(f"  [MATCH] {s} -> {names}")
            else:
                print(f"  [NOT FOUND] {s}")
    
    mt5.shutdown()
