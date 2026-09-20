import MetaTrader5 as mt5

if not mt5.initialize():
    print("Failed to initialize MT5")
    exit(1)

positions = mt5.positions_get()
print(f"Total open positions in MT5: {len(positions) if positions else 0}")
if positions:
    for p in positions:
        print(f"Ticket: #{p.ticket} | Symbol: {p.symbol} | Type: {'BUY' if p.type == 0 else 'SELL'} | Lots: {p.volume} | Open Price: {p.price_open} | SL: {p.sl} | TP: {p.tp} | Profit: ${p.profit:.2f} | Comment: {p.comment}")
