
import MetaTrader5 as mt5
from datetime import datetime, timezone

if not mt5.initialize():
    print("MT5 initialization failed:", mt5.last_error())
    raise SystemExit

symbol = "XAUUSD"

if not mt5.symbol_select(symbol, True):
    print("Symbol selection failed:", mt5.last_error())
    mt5.shutdown()
    raise SystemExit

tick = mt5.symbol_info_tick(symbol)

if tick is None:
    print("Could not retrieve tick:", mt5.last_error())
    mt5.shutdown()
    raise SystemExit

now_utc = datetime.now(timezone.utc)
tick_utc = datetime.fromtimestamp(tick.time_msc / 1000, timezone.utc)

print("========== MT5 TIME CHECK ==========")
print("Windows UTC       :", now_utc)
print("Windows Local     :", datetime.now().astimezone())
print("Last MT5 Tick UTC :", tick_utc)
print("Tick timestamp    :", tick.time)
print("Tick milliseconds :", tick.time_msc)
print("Tick age (seconds):", round((now_utc - tick_utc).total_seconds(), 2))

mt5.shutdown()
