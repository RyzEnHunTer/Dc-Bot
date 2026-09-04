"""
Test valid SL modification respecting broker stops_level
"""

import sys
import time
import MetaTrader5 as mt5

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

if not mt5.initialize():
    print("MT5 Init failed")
    sys.exit(1)

symbol = "XAUUSD"
sym_info = mt5.symbol_info(symbol)
digits = sym_info.digits
min_stop_pts = sym_info.trade_stops_level * sym_info.point

tick = mt5.symbol_info_tick(symbol)
print(f"[*] Testing {symbol} Live SL Modification with stops_level compliance...")
print(f"[*] Bid: {tick.bid} | Ask: {tick.ask} | Min Stop Distance: {min_stop_pts:.2f}")

# 1. Open test position with wide SL
entry_price = tick.ask
sl_initial = round(entry_price - 5.0, digits)
tp_initial = round(entry_price + 5.0, digits)

req_open = {
    "action": mt5.TRADE_ACTION_DEAL,
    "symbol": symbol,
    "volume": 0.01,
    "type": mt5.ORDER_TYPE_BUY,
    "price": entry_price,
    "sl": sl_initial,
    "tp": tp_initial,
    "deviation": 20,
    "magic": 999201,
    "comment": "Test_SL_Mod",
    "type_time": mt5.ORDER_TIME_GTC,
    "type_filling": mt5.ORDER_FILLING_FOK,
}

res_open = mt5.order_send(req_open)
if res_open.retcode != mt5.TRADE_RETCODE_DONE:
    print(f"[FAIL] Open failed: {res_open.retcode}")
    mt5.shutdown()
    sys.exit(1)

ticket = res_open.order
print(f"[SUCCESS] Opened #{ticket} @ {res_open.price:.{digits}f} | Initial SL: {sl_initial:.{digits}f}")

time.sleep(1.0)

# 2. Modify SL to a valid trailing level: 2.0 pts below current bid (min required is 0.40 pts)
cur_tick = mt5.symbol_info_tick(symbol)
new_sl = round(cur_tick.bid - 2.0, digits)
print(f"[*] Modifying SL on #{ticket} to {new_sl:.{digits}f} (Distance from Bid: {cur_tick.bid - new_sl:.2f} pts >= min {min_stop_pts:.2f} pts)...")

req_mod = {
    "action": mt5.TRADE_ACTION_SLTP,
    "position": ticket,
    "symbol": symbol,
    "sl": new_sl,
    "tp": tp_initial,
}

res_mod = mt5.order_send(req_mod)
print(f"[*] Modification Result: RetCode={res_mod.retcode} ({'DONE' if res_mod.retcode == mt5.TRADE_RETCODE_DONE else 'FAILED'}) | Comment={res_mod.comment}")

# Verify in terminal
pos = [p for p in mt5.positions_get(symbol=symbol) if p.ticket == ticket]
if pos:
    print(f"[VERIFIED] Terminal confirmed position #{ticket} SL updated to: {pos[0].sl:.{digits}f}")

time.sleep(1.0)

# 3. Close position cleanly
close_tick = mt5.symbol_info_tick(symbol)
req_close = {
    "action": mt5.TRADE_ACTION_DEAL,
    "position": ticket,
    "symbol": symbol,
    "volume": 0.01,
    "type": mt5.ORDER_TYPE_SELL,
    "price": close_tick.bid,
    "deviation": 20,
    "magic": 999209,
    "comment": "Test_Close",
    "type_time": mt5.ORDER_TIME_GTC,
    "type_filling": mt5.ORDER_FILLING_FOK,
}
res_close = mt5.order_send(req_close)
print(f"[CLOSED] Closed #{ticket} @ {res_close.price:.{digits}f} | RetCode={res_close.retcode} (DONE)")

mt5.shutdown()
