# MT5 Connectivity POC — Final Validation Report

**Date:** 2026-11-09  
**Environment:** Windows 11, Python 3.13.5 (64-bit), MetaTrader5 package 5.0.6231

---

## Final Checklist

| Validation | Status | Detail |
|---|---|---|
| Python installation | PASS | Python 3.13.5, 64-bit, E:\Python\python.exe |
| MetaTrader5 package installation | PASS | v5.0.6231 |
| MT5 terminal found | PASS | C:\Program Files\MetaTrader 5\terminal64.exe (Build 6308) |
| MT5 initialization | PASS | mt5.initialize() succeeded |
| Terminal connected | PASS | connected = True |
| Account information retrieved | PASS | Full account info returned |
| Broker identification | PASS | Vantage Markets (Pty) Ltd |
| Server identification | PASS | VantageMarkets-Demo |
| Account type | PASS | DEMO |
| Currency discovered | PASS | USD |
| Leverage discovered | PASS | 1:500 |
| Account mode discovered | PASS | RETAIL HEDGING |
| Gold instrument discovered | PASS | XAUUSD (primary tradeable symbol) |
| Contract specifications retrieved | PASS | See symbol_report.json |
| Live tick retrieval | PASS | 10 ticks received, prices updating live |
| Existing positions retrieval | PASS | 0 open positions |

---

## Account Summary

- **Account:** 26396767 (OMKAR BHADALE)
- **Broker:** Vantage Markets (Pty) Ltd
- **Server:** VantageMarkets-Demo
- **Balance:** 903.77 USD
- **Equity:** 903.77 USD
- **Leverage:** 1:500
- **Margin Mode:** Retail Hedging

## Gold Instrument

- **Trading Symbol:** `XAUUSD`
- **Contract Size:** 100 oz
- **Min Volume:** 0.01 lots
- **Tick Value:** $1.00 per tick
- **Live Bid/Ask at test:** ~4121.21 / 4121.42 (spread ~0.21)

## Key Finding: Server Name

The account operates on **VantageMarkets-Demo**, NOT VantageMarkets-Live 19.  
This was the root cause of all initial connectivity failures.

---

## Environment Ready

**YES — the environment is fully ready for the next stage: Python-based automated demo trading.**

Awaiting explicit user approval before proceeding to strategy implementation.
