import MetaTrader5 as mt5
from datetime import datetime, timezone
from live_bot import ActiveTwinPosition, CONFIGS

if not mt5.initialize():
    print("MT5 init failed")
    exit(1)

active_positions = {}
symbols = ["XAUUSD", "NAS100"]

recovered_count = 0
for symbol in symbols:
    open_pos = mt5.positions_get(symbol=symbol)
    if not open_pos or len(open_pos) == 0:
        continue

    tickets = list(open_pos)
    ticket_a = None
    ticket_b = None

    for p in tickets:
        comment = (p.comment or "").strip()
        if "TP1" in comment:
            ticket_a = p
        elif "Runner" in comment or "TP2" in comment:
            ticket_b = p

    if not ticket_a and not ticket_b:
        if len(tickets) >= 2:
            tickets_sorted = sorted(tickets, key=lambda p: abs(p.tp - p.price_open) if p.tp > 0 else 999999)
            ticket_a = tickets_sorted[0]
            ticket_b = tickets_sorted[1]
        elif len(tickets) == 1:
            ticket_b = tickets[0]

    ref_p = ticket_a or ticket_b
    if not ref_p:
        continue

    dir_str = "BUY" if ref_p.type == 0 else "SELL"
    entry_price = float(ref_p.price_open)
    entry_time = datetime.fromtimestamp(ref_p.time, tz=timezone.utc)
    sl_price = float(ref_p.sl)
    tp1_price = float(ticket_a.tp) if ticket_a else 0.0
    tp2_price = float(ticket_b.tp) if ticket_b else 0.0
    lots_a = float(ticket_a.volume) if ticket_a else 0.0
    lots_b = float(ticket_b.volume) if ticket_b else 0.0
    t_a_num = int(ticket_a.ticket) if ticket_a else 0
    t_b_num = int(ticket_b.ticket) if ticket_b else 0

    runner_at_be = False
    be_sl = 0.0
    cfg = CONFIGS.get(symbol)
    spread = cfg.max_allowed_spread if cfg else 0.5
    if ticket_b:
        if dir_str == "BUY" and sl_price >= entry_price - 0.1:
            runner_at_be = True
            be_sl = sl_price
        elif dir_str == "SELL" and sl_price > 0 and sl_price <= entry_price + 0.1:
            runner_at_be = True
            be_sl = sl_price

    ticket_a_closed = (ticket_a is None and ticket_b is not None)

    active_positions[symbol] = ActiveTwinPosition(
        symbol=symbol,
        direction=dir_str,
        entry_price=entry_price,
        entry_spread=spread,
        entry_time=entry_time,
        ticket_a=t_a_num,
        ticket_b=t_b_num,
        sl_price=sl_price,
        tp1_price=tp1_price,
        tp2_price=tp2_price,
        lots_a=lots_a,
        lots_b=lots_b,
        be_sl=be_sl,
        ticket_a_closed=ticket_a_closed,
        runner_moved_to_be=runner_at_be
    )
    recovered_count += 1
    print(f"[{symbol} PERSISTENCE RECOVERY] Restored active {dir_str} position:")
    print(f"  Ticket A: #{t_a_num} | Lots: {lots_a} | TP1: {tp1_price}")
    print(f"  Ticket B: #{t_b_num} | Lots: {lots_b} | TP2: {tp2_price}")
    print(f"  Entry: {entry_price} | SL: {sl_price} | BE: {runner_at_be}")

print(f"\nTotal active positions in memory: {len(active_positions)}")
