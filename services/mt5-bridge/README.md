# MT5 Bridge

FastAPI service that wraps the MetaTrader 5 Python library and exposes the
terminal over HTTP + WebSocket so all Docker containers can reach it.

**This service runs natively on Windows — it is NOT a Docker container.**

---

## Quick start (manual / dev)

```powershell
# 1. Install dependencies (system Python, same one that connects to MT5)
cd services\mt5-bridge
pip install -r requirements.txt

# 2. Copy and edit environment file
copy ..\..\env.example .env
# Edit .env — set MT5_TERMINAL_PATH if your MT5 is not in the default location

# 3. Make sure MT5 terminal is open and logged in

# 4. Start the bridge
python -m uvicorn main:app --host 0.0.0.0 --port 8000

# 5. Verify
curl http://localhost:8000/health
```

---

## Windows Service install (production)

For the demo server deployment, install as a Windows Service so it runs at
boot and auto-restarts on failure.

**Prerequisites:**
- Run PowerShell as Administrator
- Install NSSM: `choco install nssm` or download from https://nssm.cc

```powershell
# Install
.\install-service.ps1

# Check status
Get-Service MT5Bridge

# View live logs
Get-Content .\logs\mt5-bridge.log -Wait

# Uninstall
.\install-service.ps1 -Uninstall
```

---

## Endpoints

| Method | Path | Description |
|---|---|---|
| GET | /health | Terminal connection status |
| GET | /account | Balance, equity, margin |
| GET | /symbol/{symbol} | Contract spec (point, spread, volume limits) |
| GET | /candles | Historical closed candles |
| GET | /positions | All open positions |
| POST | /order | Place a market order |
| POST | /close/{ticket} | Close an open position |
| WS | /ws/candles | Stream live closed candles |

Full request/response shapes: see `API_SPEC.md`.

---

## Configuration (`.env`)

| Variable | Default | Description |
|---|---|---|
| `MT5_TERMINAL_PATH` | `C:\Program Files\MetaTrader 5\terminal64.exe` | Path to MT5 terminal |
| `LOG_LEVEL` | `INFO` | Logging level (DEBUG/INFO/WARNING) |

---

## How Docker containers reach the bridge

On Docker Desktop for Windows (WSL2 backend), `host.docker.internal` resolves
to the Windows host IP. All containers in `docker-compose.yml` have:

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

They call the bridge at `http://host.docker.internal:8000`.

The bridge listens on `0.0.0.0:8000` so both `localhost` and
`host.docker.internal` resolve correctly.

**Security note:** Port 8000 should be blocked in Windows Firewall for all
external connections. It only needs to be reachable from the local machine.

---

## MT5 thread safety

The MetaTrader5 Python API is not thread-safe. All MT5 calls in this service
are serialised through a single `asyncio.Lock` (`_mt5_lock`). This means
concurrent HTTP requests will queue behind each other for MT5 access, which is
correct behaviour — do not remove this lock.

---

## Troubleshooting

**`MT5 initialize failed`** — MT5 terminal is not running. Open MT5 and log in
before starting the bridge.

**`Symbol not found: XAUUSD`** — Symbol is not visible in MT5 Market Watch.
Right-click Market Watch → Show All, find XAUUSD, enable it.

**`ORDER_FILLING_IOC` rejected** — Some brokers require `ORDER_FILLING_FOK`.
If orders are consistently rejected with a filling error, change
`mt5.ORDER_FILLING_IOC` to `mt5.ORDER_FILLING_FOK` in `main.py` (line in
`place_order`).

**Container cannot reach bridge** — Ensure the bridge is listening on
`0.0.0.0` (not `127.0.0.1`) and that Windows Firewall allows local connections
on port 8000. Test with: `curl http://host.docker.internal:8000/health` from
inside a container.
