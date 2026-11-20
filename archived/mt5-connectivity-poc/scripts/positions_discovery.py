import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import MetaTrader5 as mt5
from config.settings import mt5_init

if not mt5_init(mt5):
    raise RuntimeError(mt5.last_error())

try:
    positions = mt5.positions_get()

    if positions is None:
        raise RuntimeError(mt5.last_error())

    print("OPEN POSITIONS:", len(positions))

    for p in positions:
        print({
            "ticket": p.ticket,
            "symbol": p.symbol,
            "type": p.type,
            "volume": p.volume,
            "price_open": p.price_open,
            "sl": p.sl,
            "tp": p.tp,
            "profit": p.profit
        })

finally:
    mt5.shutdown()
