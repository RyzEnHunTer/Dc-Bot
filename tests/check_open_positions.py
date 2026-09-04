import MetaTrader5 as mt5

if mt5.initialize():
    positions = mt5.positions_get()
    print("CURRENT OPEN POSITIONS:", len(positions) if positions else 0)
    for p in (positions or []):
        print(f"Ticket: {p.ticket}, Symbol: {p.symbol}, Type: {p.type}, Vol: {p.volume}, Price: {p.price_open}, Profit: ${p.profit:.2f}")
    acc = mt5.account_info()
    print(f"Balance: ${acc.balance:,.2f} | Equity: ${acc.equity:,.2f} | Free Margin: ${acc.margin_free:,.2f}")
    mt5.shutdown()
