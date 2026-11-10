# MT5 Connectivity POC

Read-only connectivity and account discovery validation for MetaTrader 5 via Python.

## Setup

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Usage

Run each script from the project root with the venv activated and MT5 terminal open and logged in.

```powershell
python scripts/test_installation.py
python scripts/test_connection.py
python scripts/account_discovery.py
python scripts/symbol_discovery.py
python scripts/test_market_data.py
```

## Safety

All scripts are strictly read-only. No orders are placed. No credentials are stored in code.
