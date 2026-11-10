"""
auto_trader.py — EMA bias-following bot on XAUUSD M1 bars.

Strategy : EMA-3 / EMA-8 bias on 1-minute candles.
  BULL bias (EMA3 > EMA8) and no BUY open  -> open BUY
  BEAR bias (EMA3 < EMA8) and no SELL open -> open SELL
  Bias flips                                -> close old, open new

Trades immediately on start based on current bias.
Safe: 0.01 lots, hard SL on every trade, one position at a time.
Stop: Ctrl+C -> closes all open positions cleanly before exit.
"""
import sys
import os
import time
import signal
from datetime import datetime
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import MetaTrader5 as mt5
import numpy as np
from config.settings import mt5_init

# ── Config ────────────────────────────────────────────────────
SYMBOL         = "XAUUSD"
LOT            = 0.01
EMA_FAST       = 3
EMA_SLOW       = 8
BARS           = 30
SL_POINTS      = 80
TP_POINTS      = 60    # tighter than SL — closes fast on any profitable move
MAGIC          = 222222
CHECK_INTERVAL = 5
COOLDOWN_CYCLES = 2    # cycles to wait after a broker-closed position (SL/TP hit)
# ──────────────────────────────────────────────────────────────

_running = True


def _handle_stop(sig, frame):
    global _running
    print("\n\n  Ctrl+C received -- stopping after this cycle...")
    _running = False


signal.signal(signal.SIGINT, _handle_stop)


# ── EMA helper ────────────────────────────────────────────────

def calc_ema(prices, period):
    k = 2.0 / (period + 1)
    out = np.empty_like(prices)
    out[0] = prices[0]
    for i in range(1, len(prices)):
        out[i] = prices[i] * k + out[i - 1] * (1 - k)
    return out


# ── MT5 helpers ───────────────────────────────────────────────

def our_positions():
    pos = mt5.positions_get(symbol=SYMBOL)
    if pos is None:
        return []
    return [p for p in pos if p.magic == MAGIC]


def close_all_positions():
    for pos in our_positions():
        tick = mt5.symbol_info_tick(SYMBOL)
        is_buy = pos.type == mt5.ORDER_TYPE_BUY
        close_type = mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY
        price = tick.bid if is_buy else tick.ask

        req = {
            "action":       mt5.TRADE_ACTION_DEAL,
            "symbol":       SYMBOL,
            "volume":       pos.volume,
            "type":         close_type,
            "position":     pos.ticket,
            "price":        price,
            "deviation":    50,
            "magic":        MAGIC,
            "comment":      "auto_close",
            "type_time":    mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        res = mt5.order_send(req)
        direction = "BUY" if is_buy else "SELL"
        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            print(f"    Closed {direction} #{pos.ticket} @ {price}  |  P&L: ${pos.profit:+.2f}")
        else:
            comment = res.comment if res else str(mt5.last_error())
            print(f"    FAILED to close #{pos.ticket}: {comment}")


def open_order(order_type):
    tick = mt5.symbol_info_tick(SYMBOL)
    sym  = mt5.symbol_info(SYMBOL)
    is_buy = order_type == mt5.ORDER_TYPE_BUY

    price = tick.ask if is_buy else tick.bid
    sl    = round(price - SL_POINTS * sym.point, sym.digits) if is_buy \
            else round(price + SL_POINTS * sym.point, sym.digits)
    tp    = round(price + TP_POINTS * sym.point, sym.digits) if is_buy \
            else round(price - TP_POINTS * sym.point, sym.digits)

    req = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       SYMBOL,
        "volume":       LOT,
        "type":         order_type,
        "price":        price,
        "sl":           sl,
        "tp":           tp,
        "deviation":    50,
        "magic":        MAGIC,
        "comment":      f"ema{EMA_FAST}/{EMA_SLOW}",
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    res = mt5.order_send(req)
    return res, price, sl, tp


# ── Main loop ─────────────────────────────────────────────────

def main():
    print("=" * 60)
    print(f"  EMA BIAS AUTO-TRADER  |  {SYMBOL} M1")
    print(f"  EMA {EMA_FAST} / {EMA_SLOW}  |  Lot {LOT}  |  SL {SL_POINTS}pts  |  TP {TP_POINTS}pts")
    print(f"  Press Ctrl+C to stop cleanly")
    print("=" * 60)

    if not mt5_init(mt5):
        print("FAILED: MT5 init:", mt5.last_error())
        sys.exit(1)

    if not mt5.symbol_select(SYMBOL, True):
        print(f"FAILED: Cannot select {SYMBOL}")
        mt5.shutdown()
        sys.exit(1)

    print(f"\n  Started. Checking every {CHECK_INTERVAL}s...\n")

    cycle = 0
    prev_pos_count = 0
    cooldown = 0        # cycles remaining before re-entry allowed

    while _running:
        cycle += 1
        now = datetime.now().strftime("%H:%M:%S")

        # ── Fetch candles ────────────────────────────────────
        rates = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M1, 0, BARS)
        if rates is None or len(rates) < max(EMA_FAST, EMA_SLOW) + 2:
            print(f"[{now}] Cannot get rates: {mt5.last_error()}")
            time.sleep(CHECK_INTERVAL)
            continue

        closes   = np.array([r["close"] for r in rates], dtype=float)
        fast_ema = calc_ema(closes, EMA_FAST)
        slow_ema = calc_ema(closes, EMA_SLOW)

        curr_fast = fast_ema[-1]
        curr_slow = slow_ema[-1]
        bull = curr_fast > curr_slow
        bias = "BULL" if bull else "BEAR"

        # ── Current state ────────────────────────────────────
        positions  = our_positions()
        open_count = len(positions)
        total_pnl  = sum(p.profit for p in positions)
        tick       = mt5.symbol_info_tick(SYMBOL)
        price      = tick.bid if tick else 0.0

        has_buy  = any(p.type == mt5.ORDER_TYPE_BUY  for p in positions)
        has_sell = any(p.type == mt5.ORDER_TYPE_SELL for p in positions)

        # ── Detect broker-closed position (SL/TP hit) ────────
        broker_closed = prev_pos_count > 0 and open_count == 0
        if broker_closed and cooldown == 0:
            cooldown = COOLDOWN_CYCLES
            print(f"  [SL/TP hit] Cooldown {cooldown} cycles before re-entry")
        prev_pos_count = open_count

        if cooldown > 0:
            cooldown -= 1

        print(
            f"[{now}] #{cycle:03d}  "
            f"Price: {price:.2f}  "
            f"EMA{EMA_FAST}: {curr_fast:.2f}  "
            f"EMA{EMA_SLOW}: {curr_slow:.2f}  "
            f"Bias: {bias}  "
            f"Pos: {open_count}  "
            f"P&L: ${total_pnl:+.2f}"
            + (f"  [cooldown {cooldown}]" if cooldown > 0 else "")
        )

        # ── Bias-following logic ──────────────────────────────
        if cooldown > 0:
            time.sleep(CHECK_INTERVAL)
            continue

        if bull and not has_buy:
            if has_sell:
                print(f"  >> Bias flipped BULL -- closing SELL, opening BUY")
                close_all_positions()
            else:
                print(f"  >> BULL bias -- opening BUY")
            res, px, sl, tp = open_order(mt5.ORDER_TYPE_BUY)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                print(f"     BUY  #{res.order} opened @ {px}  SL {sl}  TP {tp}")
            else:
                comment = res.comment if res else str(mt5.last_error())
                print(f"     FAILED to open BUY: {comment}")

        elif not bull and not has_sell:
            if has_buy:
                print(f"  >> Bias flipped BEAR -- closing BUY, opening SELL")
                close_all_positions()
            else:
                print(f"  >> BEAR bias -- opening SELL")
            res, px, sl, tp = open_order(mt5.ORDER_TYPE_SELL)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                print(f"     SELL #{res.order} opened @ {px}  SL {sl}  TP {tp}")
            else:
                comment = res.comment if res else str(mt5.last_error())
                print(f"     FAILED to open SELL: {comment}")

        time.sleep(CHECK_INTERVAL)

    # ── Clean shutdown ────────────────────────────────────────
    print("\n  Closing all open positions...")
    close_all_positions()
    mt5.shutdown()
    print("  Stopped cleanly.")


if __name__ == "__main__":
    main()
