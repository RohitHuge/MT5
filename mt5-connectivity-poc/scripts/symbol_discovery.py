import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import MetaTrader5 as mt5
from config.settings import mt5_init

if not mt5_init(mt5):
    raise RuntimeError(mt5.last_error())

try:
    symbols = mt5.symbols_get()

    if symbols is None:
        raise RuntimeError(mt5.last_error())

    print("\nGOLD SYMBOL DISCOVERY")

    gold_symbols = [
        symbol.name
        for symbol in symbols
        if "XAU" in symbol.name.upper() or "GOLD" in symbol.name.upper()
    ]

    if not gold_symbols:
        print("No Gold instruments discovered.")

    for symbol_name in gold_symbols:
        print("\n" + "=" * 50)
        info = mt5.symbol_info(symbol_name)

        if info is None:
            continue

        print("Symbol:", info.name)
        print("Description:", info.description)
        print("Contract Size:", info.trade_contract_size)
        print("Minimum Volume:", info.volume_min)
        print("Maximum Volume:", info.volume_max)
        print("Volume Step:", info.volume_step)
        print("Digits:", info.digits)
        print("Point:", info.point)
        print("Tick Size:", info.trade_tick_size)
        print("Tick Value:", info.trade_tick_value)
        print("Spread:", info.spread)
        print("Trade Mode:", info.trade_mode)
        print("Filling Mode:", info.filling_mode)
        print("Execution Mode:", info.trade_exemode)

finally:
    mt5.shutdown()
