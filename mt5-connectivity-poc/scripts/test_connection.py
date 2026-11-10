import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import MetaTrader5 as mt5
from config.settings import mt5_init

print("=" * 60)
print("MT5 TERMINAL CONNECTIVITY TEST")
print("=" * 60)

if not mt5_init(mt5):
    print("FAILED: MT5 initialization")
    print("Error:", mt5.last_error())
    sys.exit(1)

print("\nSUCCESS: MT5 initialized")

terminal = mt5.terminal_info()

if terminal is None:
    print("FAILED: Cannot retrieve terminal information")
    print(mt5.last_error())
    mt5.shutdown()
    sys.exit(1)

print("\nTERMINAL INFORMATION")
print("Connected:", terminal.connected)
print("Company:", terminal.company)
print("Name:", terminal.name)
print("Path:", terminal.path)
print("Data Path:", terminal.data_path)
print("Build:", terminal.build)

if not terminal.connected:
    print("\nFAILED: Terminal is not connected to broker")
    mt5.shutdown()
    sys.exit(1)

account = mt5.account_info()

if account is None:
    print("FAILED: Cannot retrieve account information")
    print(mt5.last_error())
    mt5.shutdown()
    sys.exit(1)

print("\nSUCCESS: ACCOUNT INFORMATION AVAILABLE")
print("Login:", account.login)
print("Broker:", account.company)
print("Server:", account.server)
print("Currency:", account.currency)
print("Leverage:", account.leverage)
print("Balance:", account.balance)
print("Equity:", account.equity)
print("Free Margin:", account.margin_free)

print("\nACCOUNT MODE")

trade_modes = {0: "DEMO", 1: "CONTEST", 2: "REAL"}
margin_modes = {0: "RETAIL NETTING", 1: "EXCHANGE", 2: "RETAIL HEDGING"}

print("Account Type:", trade_modes.get(account.trade_mode, "UNKNOWN"))
print("Margin Mode:", margin_modes.get(account.margin_mode, "UNKNOWN"))

print("\nTRADING PERMISSIONS")
print("Trading Allowed:", account.trade_allowed)
print("Expert Trading Allowed:", account.trade_expert)

print("\nMT5 CONNECTION TEST COMPLETED")

mt5.shutdown()
