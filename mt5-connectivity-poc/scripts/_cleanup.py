import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import MetaTrader5 as mt5
from config.settings import mt5_init
mt5_init(mt5)
positions = mt5.positions_get(symbol="XAUUSD") or []
print("Open positions:", len(positions))
for p in positions:
    t = mt5.symbol_info_tick("XAUUSD")
    close_type = mt5.ORDER_TYPE_SELL if p.type == 0 else mt5.ORDER_TYPE_BUY
    r = mt5.order_send({
        "action": mt5.TRADE_ACTION_DEAL, "symbol": "XAUUSD",
        "volume": p.volume, "type": close_type, "position": p.ticket,
        "price": t.bid if p.type == 0 else t.ask,
        "deviation": 50, "magic": p.magic, "comment": "cleanup",
        "type_time": mt5.ORDER_TIME_GTC, "type_filling": mt5.ORDER_FILLING_IOC,
    })
    print(f"Closed #{p.ticket}: {r.retcode} {r.comment}")
mt5.shutdown()
