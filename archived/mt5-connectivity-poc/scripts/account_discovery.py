import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import MetaTrader5 as mt5
import json
from config.settings import mt5_init

if not mt5_init(mt5):
    raise RuntimeError(f"MT5 initialization failed: {mt5.last_error()}")

try:
    account = mt5.account_info()
    terminal = mt5.terminal_info()

    if account is None or terminal is None:
        raise RuntimeError(f"Unable to retrieve MT5 information: {mt5.last_error()}")

    trade_modes = {0: "DEMO", 1: "CONTEST", 2: "REAL"}
    margin_modes = {0: "RETAIL NETTING", 1: "EXCHANGE", 2: "RETAIL HEDGING"}

    report = {
        "terminal": {
            "connected": terminal.connected,
            "company": terminal.company,
            "name": terminal.name,
            "build": terminal.build,
            "path": terminal.path
        },
        "account": {
            "login": account.login,
            "broker": account.company,
            "server": account.server,
            "type": trade_modes.get(account.trade_mode, "UNKNOWN"),
            "currency": account.currency,
            "leverage": account.leverage,
            "margin_mode": margin_modes.get(account.margin_mode, "UNKNOWN"),
            "balance": account.balance,
            "equity": account.equity,
            "margin": account.margin,
            "free_margin": account.margin_free,
            "trade_allowed": account.trade_allowed,
            "trade_expert": account.trade_expert
        }
    }

    os.makedirs("reports", exist_ok=True)

    with open("reports/account_report.json", "w", encoding="utf-8") as file:
        json.dump(report, file, indent=4)

    print(json.dumps(report, indent=4))
    print("\nAccount discovery completed.")

finally:
    mt5.shutdown()
