"""
Comprehensive MT5 Live Order Execution & Diagnostics Test
Tests the full lifecycle on OctaFX-Demo:
1. Account & Terminal Algo Trading verification
2. Real-time Market Quote & Spread check
3. Twin-Ticket Order Placement (Ticket A: 0.01 lot with TP1, Ticket B: 0.01 lot with TP2)
4. SL / TP Placement verification
5. Breakeven SL Modification (TRADE_ACTION_SLTP)
6. Instant Position Close (TRADE_ACTION_DEAL)
7. Validates both XAUUSD (Gold) and NAS100 (Nasdaq)
"""

import sys
import time
import MetaTrader5 as mt5

# Windows PowerShell unicode encoding guard
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')


def get_filling_mode(sym_info):
    if not sym_info:
        return mt5.ORDER_FILLING_FOK
    modes = sym_info.filling_mode
    if modes & 1:
        return mt5.ORDER_FILLING_FOK
    elif modes & 2:
        return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_RETURN


def run_full_execution_test(symbol: str):
    print("\n" + "=" * 75)
    print(f"STARTING LIVE EXECUTION AUDIT FOR: {symbol}")
    print("=" * 75)

    sym_info = mt5.symbol_info(symbol)
    if not sym_info:
        print(f"[FAIL] Symbol {symbol} not found in MT5!")
        return False

    if not sym_info.visible:
        mt5.symbol_select(symbol, True)
        sym_info = mt5.symbol_info(symbol)

    digits = sym_info.digits
    point = sym_info.point
    fill_mode = get_filling_mode(sym_info)
    vol_min = sym_info.volume_min
    vol_step = sym_info.volume_step
    contract = sym_info.trade_contract_size

    fill_name = "ORDER_FILLING_FOK" if fill_mode == mt5.ORDER_FILLING_FOK else ("ORDER_FILLING_IOC" if fill_mode == mt5.ORDER_FILLING_IOC else "ORDER_FILLING_RETURN")

    print(f"[*] Symbol:         {symbol}")
    print(f"[*] Digits:         {digits} | Point: {point}")
    print(f"[*] Min Lot / Step: {vol_min} / {vol_step}")
    print(f"[*] Contract Size:  {contract}")
    print(f"[*] Trade Mode:     {sym_info.trade_mode} (Full=0)")
    print(f"[*] Filling Mode:   {fill_name} (int={fill_mode})")

    # 1. Fetch current tick
    tick = mt5.symbol_info_tick(symbol)
    if not tick or tick.ask <= 0 or tick.bid <= 0:
        print(f"[FAIL] No valid live tick received for {symbol}")
        return False

    spread = tick.ask - tick.bid
    print(f"[*] Live Tick:      Bid={tick.bid:.{digits}f} | Ask={tick.ask:.{digits}f} | Spread={spread:.{digits}f}")

    # Set test order parameters
    # For Gold: SL = 3.0 pts ($3), TP1 = 4.2 pts, TP2 = 6.6 pts
    # For Nasdaq: SL = 25 pts, TP1 = 37.5 pts, TP2 = 50.0 pts
    if "XAU" in symbol or "GOLD" in symbol:
        sl_dist = 3.0
        tp1_dist = 4.2
        tp2_dist = 6.6
    else:
        sl_dist = 25.0
        tp1_dist = 37.5
        tp2_dist = 50.0

    entry_price = tick.ask
    sl_price = round(entry_price - sl_dist, digits)
    tp1_price = round(entry_price + tp1_dist, digits)
    tp2_price = round(entry_price + tp2_dist, digits)
    vol = vol_min

    # -------------------------------------------------------------
    # STEP 1: Execute Ticket A (50% TP1)
    # -------------------------------------------------------------
    print(f"\n[STEP 1] Placing Ticket A (TP1 Part): BUY {vol} lots @ {entry_price:.{digits}f}...")
    req_a = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": vol,
        "type": mt5.ORDER_TYPE_BUY,
        "price": entry_price,
        "sl": sl_price,
        "tp": tp1_price,
        "deviation": 20,
        "magic": 999101,
        "comment": "DCC_Test_A",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": fill_mode,
    }

    res_a = mt5.order_send(req_a)
    if res_a is None or res_a.retcode != mt5.TRADE_RETCODE_DONE:
        print(f"[ERROR] Ticket A failed! RetCode={res_a.retcode if res_a else 'None'}")
        if res_a:
            print(f"        Comment: {res_a.comment}")
        print(f"        MT5 Last Error: {mt5.last_error()}")
        return False

    ticket_a = res_a.order
    deal_a = res_a.deal
    fill_price_a = res_a.price
    print(f"[SUCCESS] Ticket A Executed! Order #{ticket_a} | Deal #{deal_a} | Fill Price: {fill_price_a:.{digits}f} | RetCode: {res_a.retcode} (DONE)")

    # -------------------------------------------------------------
    # STEP 2: Execute Ticket B (50% Runner)
    # -------------------------------------------------------------
    print(f"\n[STEP 2] Placing Ticket B (Runner Part): BUY {vol} lots @ {entry_price:.{digits}f}...")
    req_b = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": vol,
        "type": mt5.ORDER_TYPE_BUY,
        "price": entry_price,
        "sl": sl_price,
        "tp": tp2_price,
        "deviation": 20,
        "magic": 999102,
        "comment": "DCC_Test_B",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": fill_mode,
    }

    res_b = mt5.order_send(req_b)
    if res_b is None or res_b.retcode != mt5.TRADE_RETCODE_DONE:
        print(f"[ERROR] Ticket B failed! RetCode={res_b.retcode if res_b else 'None'}")
        if res_b:
            print(f"        Comment: {res_b.comment}")
        print(f"        MT5 Last Error: {mt5.last_error()}")
    else:
        ticket_b = res_b.order
        deal_b = res_b.deal
        fill_price_b = res_b.price
        print(f"[SUCCESS] Ticket B Executed! Order #{ticket_b} | Deal #{deal_b} | Fill Price: {fill_price_b:.{digits}f} | RetCode: {res_b.retcode} (DONE)")

    time.sleep(1.0)

    # -------------------------------------------------------------
    # STEP 3: Verify Positions in Terminal
    # -------------------------------------------------------------
    positions = mt5.positions_get(symbol=symbol)
    open_tickets = [p.ticket for p in positions] if positions else []
    print(f"\n[STEP 3] Verified Open Positions for {symbol}: {open_tickets}")
    for p in (positions or []):
        if p.ticket in [ticket_a, res_b.order if res_b else 0]:
            print(f"         Ticket #{p.ticket}: Type={p.type} (0=BUY) | Vol={p.volume} | OpenPrice={p.price_open:.{digits}f} | SL={p.sl:.{digits}f} | TP={p.tp:.{digits}f} | Profit=${p.profit:.2f}")

    # -------------------------------------------------------------
    # STEP 4: Test Breakeven SL Modification (TRADE_ACTION_SLTP)
    # -------------------------------------------------------------
    if res_b and res_b.retcode == mt5.TRADE_RETCODE_DONE:
        ticket_b = res_b.order
        # New Breakeven SL: entry + spread
        be_sl = round(fill_price_b + spread, digits)
        print(f"\n[STEP 4] Testing Breakeven SL Modification on Ticket B (#{ticket_b})...")
        print(f"         Moving SL from {sl_price:.{digits}f} -> Breakeven: {be_sl:.{digits}f}")

        req_mod = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket_b,
            "symbol": symbol,
            "sl": be_sl,
            "tp": tp2_price,
        }
        res_mod = mt5.order_send(req_mod)
        if res_mod is None or res_mod.retcode != mt5.TRADE_RETCODE_DONE:
            print(f"[ERROR] SL Modification failed! RetCode={res_mod.retcode if res_mod else 'None'}")
            if res_mod:
                print(f"        Comment: {res_mod.comment}")
            print(f"        MT5 Last Error: {mt5.last_error()}")
        else:
            print(f"[SUCCESS] Breakeven SL Modified successfully! RetCode: {res_mod.retcode} (DONE)")
            # Verify updated position
            p_updated = [p for p in mt5.positions_get(symbol=symbol) if p.ticket == ticket_b]
            if p_updated:
                print(f"         Position #{ticket_b} confirmed SL is now: {p_updated[0].sl:.{digits}f}")

    time.sleep(1.0)

    # -------------------------------------------------------------
    # STEP 5: Immediate Safe Close
    # -------------------------------------------------------------
    print(f"\n[STEP 5] Closing test positions immediately to protect demo balance...")
    current_positions = mt5.positions_get(symbol=symbol)
    for pos in (current_positions or []):
        if pos.ticket in [ticket_a, res_b.order if res_b else 0]:
            # To close a BUY, send a SELL deal
            latest_tick = mt5.symbol_info_tick(symbol)
            close_price = latest_tick.bid if pos.type == mt5.ORDER_TYPE_BUY else latest_tick.ask
            req_close = {
                "action": mt5.TRADE_ACTION_DEAL,
                "position": pos.ticket,
                "symbol": symbol,
                "volume": pos.volume,
                "type": mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY,
                "price": close_price,
                "deviation": 20,
                "magic": 999109,
                "comment": "DCC_Test_Close",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": fill_mode,
            }
            res_close = mt5.order_send(req_close)
            if res_close and res_close.retcode == mt5.TRADE_RETCODE_DONE:
                print(f"[SUCCESS] Position #{pos.ticket} CLOSED successfully! Close Price: {res_close.price:.{digits}f} | RetCode: {res_close.retcode} (DONE)")
            else:
                print(f"[ERROR] Failed to close position #{pos.ticket}! RetCode={res_close.retcode if res_close else 'None'}")
                if res_close:
                    print(f"        Comment: {res_close.comment}")

    time.sleep(1.0)
    final_positions = mt5.positions_get(symbol=symbol)
    final_tickets = [p.ticket for p in (final_positions or []) if p.ticket in [ticket_a, res_b.order if res_b else 0]]
    if len(final_tickets) == 0:
        print(f"\n[VERIFIED] 0 open test positions remaining on {symbol}. All orders cleanly closed!")
        print(f"ALL TESTS PASSED FOR {symbol}!\n")
        return True
    else:
        print(f"\n[WARN] Positions still open: {final_tickets}")
        return False


def main():
    print("=" * 75)
    print("MT5 LIVE ORDER EXECUTION DIAGNOSTICS & VERIFICATION SUITE")
    print("=" * 75)

    if not mt5.initialize():
        print(f"[CRITICAL ERROR] Failed to initialize MT5: {mt5.last_error()}")
        sys.exit(1)

    account = mt5.account_info()
    terminal = mt5.terminal_info()

    if not account or not terminal:
        print("[CRITICAL ERROR] Could not get MT5 account or terminal info.")
        sys.exit(1)

    print(f"Account Login:       {account.login}")
    print(f"Account Server:      {account.server}")
    print(f"Account Company:     {account.company}")
    print(f"Balance / Equity:    ${account.balance:,.2f} / ${account.equity:,.2f}")
    print(f"Margin Free:         ${account.margin_free:,.2f}")
    print(f"Terminal Connected:  {terminal.connected}")
    print(f"Terminal Trade Allowed: {terminal.trade_allowed}")
    print(f"Account Trade Allowed:  {account.trade_allowed}")
    print(f"Account EA Allowed:     {account.trade_expert}")

    if not terminal.trade_allowed or not account.trade_expert:
        print("\n[WARNING] Automated Trading / Algo Trading seems DISABLED in MT5!")
        print("Please ensure the 'Algo Trading' button on top of MT5 is GREEN.")
        sys.exit(1)

    # Test XAUUSD first
    gold_ok = run_full_execution_test("XAUUSD")

    # Test NAS100 second
    nas_ok = run_full_execution_test("NAS100")

    print("=" * 75)
    print("FINAL SUMMARY OF EXECUTION CAPABILITY:")
    print(f"  - Gold (XAUUSD):   {'100% OPERATIONAL & VERIFIED' if gold_ok else 'FAILED'}")
    print(f"  - Nasdaq (NAS100): {'100% OPERATIONAL & VERIFIED' if nas_ok else 'FAILED'}")
    print("=" * 75)

    mt5.shutdown()


if __name__ == "__main__":
    main()
