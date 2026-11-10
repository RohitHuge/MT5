import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import MetaTrader5 as mt5
import time
from config.settings import mt5_init

if not mt5_init(mt5):
    raise RuntimeError(mt5.last_error())

try:
    symbols = mt5.symbols_get()

    gold_symbols = [
        s.name for s in symbols
        if "XAU" in s.name.upper() or "GOLD" in s.name.upper()
    ]

    if not gold_symbols:
        print("No Gold symbols found.")
        raise SystemExit(1)

    symbol = gold_symbols[0]
    print("Testing instrument:", symbol)

    if not mt5.symbol_select(symbol, True):
        raise RuntimeError(f"Could not select {symbol}: {mt5.last_error()}")

    print("\nLIVE MARKET DATA")

    for i in range(10):
        tick = mt5.symbol_info_tick(symbol)

        if tick is None:
            print("Tick unavailable:", mt5.last_error())
        else:
            print(
                f"Bid: {tick.bid} | "
                f"Ask: {tick.ask} | "
                f"Spread: {tick.ask - tick.bid:.5f} | "
                f"Time: {tick.time}"
            )

        time.sleep(1)

    print("\nMarket data test completed.")

finally:
    mt5.shutdown()
