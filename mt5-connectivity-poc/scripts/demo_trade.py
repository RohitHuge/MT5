"""
demo_trade.py — Manual trade showcase for XAUUSD on MT5 demo account.

Opens a BUY position, shows live P&L, closes on Enter press.
Safe: 0.01 lots (minimum), hard SL always set.
"""
import sys
import os
import time
import threading
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import MetaTrader5 as mt5
from config.settings import mt5_init

SYMBOL    = "XAUUSD"
LOT       = 0.01
SL_POINTS = 500    # $5.00 risk at 0.01 lots
TP_POINTS = 1000   # $10.00 potential at 0.01 lots
MAGIC     = 111111


def get_symbol_info():
    info = mt5.symbol_info(SYMBOL)
    if info is None:
        raise RuntimeError(f"Cannot get symbol info: {mt5.last_error()}")
    return info


def get_tick():
    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None:
        raise RuntimeError(f"Cannot get tick: {mt5.last_error()}")
    return tick


def open_buy():
    tick = get_tick()
    sym  = get_symbol_info()
    price = tick.ask
    sl    = round(price - SL_POINTS * sym.point, sym.digits)
    tp    = round(price + TP_POINTS * sym.point, sym.digits)

    request = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       SYMBOL,
        "volume":       LOT,
        "type":         mt5.ORDER_TYPE_BUY,
        "price":        price,
        "sl":           sl,
        "tp":           tp,
        "deviation":    50,
        "magic":        MAGIC,
        "comment":      "demo_trade POC",
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    return mt5.order_send(request), price, sl, tp


def close_position(ticket, volume):
    tick  = get_tick()
    price = tick.bid

    request = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       SYMBOL,
        "volume":       volume,
        "type":         mt5.ORDER_TYPE_SELL,
        "position":     ticket,
        "price":        price,
        "deviation":    50,
        "magic":        MAGIC,
        "comment":      "demo_trade CLOSE",
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    return mt5.order_send(request), price


def main():
    print("=" * 60)
    print("  MT5 DEMO TRADE - XAUUSD BUY")
    print("=" * 60)

    if not mt5_init(mt5):
        print("FAILED: MT5 init:", mt5.last_error())
        sys.exit(1)

    if not mt5.symbol_select(SYMBOL, True):
        print(f"FAILED: Cannot select {SYMBOL}")
        mt5.shutdown()
        sys.exit(1)

    print(f"\nOpening BUY {LOT} lot on {SYMBOL}...")
    result, entry_price, sl, tp = open_buy()

    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        retcode = result.retcode if result else "None"
        comment = result.comment if result else ""
        print(f"FAILED: Order rejected (retcode {retcode}) — {comment}")
        print("MT5 error:", mt5.last_error())
        mt5.shutdown()
        sys.exit(1)

    ticket = result.order

    print(f"\n  {'-' * 40}")
    print(f"  TRADE OPEN")
    print(f"  Ticket : #{ticket}")
    print(f"  Symbol : {SYMBOL}")
    print(f"  Entry  : {entry_price}")
    print(f"  SL     : {sl}  (${SL_POINTS * LOT:.2f} risk)")
    print(f"  TP     : {tp}  (${TP_POINTS * LOT:.2f} target)")
    print(f"  {'-' * 40}")
    print(f"\n  Open MT5 >> Toolbox >> Trade tab to see it live.")
    print(f"\n  Press ENTER to CLOSE the trade...\n")

    # Live P&L in background thread
    stop_event = threading.Event()

    def pnl_loop():
        sym = get_symbol_info()
        while not stop_event.is_set():
            try:
                tick = mt5.symbol_info_tick(SYMBOL)
                if tick:
                    pnl = round((tick.bid - entry_price) * LOT * sym.trade_contract_size, 2)
                    print(f"\r  Price: {tick.bid:.2f}  |  P&L: ${pnl:+.2f}   ", end="", flush=True)
            except Exception:
                pass
            time.sleep(1)

    thread = threading.Thread(target=pnl_loop, daemon=True)
    thread.start()

    input()
    stop_event.set()
    thread.join(timeout=2)

    print(f"\n\n  Closing trade #{ticket}...")
    close_result, exit_price = close_position(ticket, LOT)

    if close_result is None or close_result.retcode != mt5.TRADE_RETCODE_DONE:
        retcode = close_result.retcode if close_result else "None"
        comment = close_result.comment if close_result else ""
        print(f"  FAILED to close (retcode {retcode}) — {comment}")
    else:
        sym    = get_symbol_info()
        final  = round((exit_price - entry_price) * LOT * sym.trade_contract_size, 2)
        print(f"  {'-' * 40}")
        print(f"  TRADE CLOSED")
        print(f"  Exit   : {exit_price}")
        print(f"  P&L    : ${final:+.2f}")
        print(f"  {'-' * 40}")

    mt5.shutdown()
    print("\nDone.")


if __name__ == "__main__":
    main()
