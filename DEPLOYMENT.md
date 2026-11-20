# MT5 Platform — Deployment Record

**Date:** 2026-11-20  
**Goal achieved:** Full trading platform running on Ubuntu server (`srv`) with zero Windows dependency.

---

## Architecture

```
srv (Ubuntu 22.04)
│
├── HOST PROCESSES (systemd-managed)
│   ├── xvfb.service      — virtual framebuffer on display :99
│   └── mt5.service       — MT5 terminal via Wine, depends on xvfb
│           └── MT5Bridge.mq5 EA — HTTP server on 127.0.0.1:8002
│
└── DOCKER (Coolify-managed, project: ukl02bmavgicbubey8wajoqr)
    ├── mt5-bridge         — Python/httpx, calls host:8002 via host.docker.internal
    ├── candle-feed        — polls mt5-bridge, publishes to Redis
    ├── strategy-ema-scalp — reads Redis candles, emits signals
    ├── strategy-liq-sweep — reads Redis candles, emits signals
    ├── strategy-ob-fvg    — reads Redis candles, emits signals
    ├── executor           — consumes signals, places orders via mt5-bridge
    ├── shadow-tracker     — tracks virtual trades in Postgres
    ├── redis              — candle/signal message bus
    ├── postgres           — trade history, strategy registry
    └── dashboard          — web UI on domain via Coolify proxy
```

**Data flow:**
```
MT5 broker ←→ MT5Bridge EA (:8002) ←→ mt5-bridge ←→ strategies → executor → mt5-bridge
```

---

## Part 1 — Why Wine (not native MT5)

MT5 terminal is a Windows GUI application. It runs on Ubuntu via Wine. We tried the community Docker image (`gmag11/metatrader5_vnc`) first — it failed because MT5 builds from 2025+ have aggressive anti-tamper checks (`IsDebuggerPresent`, `NtQueryInformationProcess`) that crash unpatched Wine.

**Solution:** The official MetaQuotes Linux package ships their own private Wine build (winehq-staging) patched specifically for MT5. No anti-debug issues. This is what is installed on `srv`.

---

## Part 2 — MT5 Installation on `srv`

### What is installed and where

| Component | Path |
|---|---|
| Wine prefix | `~/.mt5/` |
| MT5 terminal binary | `~/.mt5/drive_c/Program Files/MetaTrader 5/terminal64.exe` |
| Wine binary | `/usr/bin/wine` (winehq-staging) |
| EA source | `~/.mt5/drive_c/Program Files/MetaTrader 5/MQL5/Experts/MT5Bridge.mq5` |
| EA compiled | `~/.mt5/drive_c/Program Files/MetaTrader 5/MQL5/Experts/MT5Bridge.mq5x` |
| Virtual display | Xvfb on `:99` |
| Wrapper script | `/usr/local/bin/mt5-start.sh` |

### How it was installed

```bash
# Install Xvfb
sudo apt-get install -y xvfb x11vnc

# Download and run the official MetaQuotes installer
wget https://download.terminal.free/cdn/web/metaquotes.software.corp/mt5/mt5linux.sh
chmod +x mt5linux.sh
DISPLAY=:99 ./mt5linux.sh
```

The installer sets up winehq-staging, Windows 11 registry mode, WebView2, and MT5 terminal.

---

## Part 3 — One-Time Broker Login (VNC)

This was done once and never needs to be repeated. The login credentials are saved in the Wine prefix on disk.

**Broker account:** Vantage Markets, login `26396767`

### How the login was done

**On `srv`:**
```bash
Xvfb :99 -screen 0 1280x800x24 &
sleep 2
DISPLAY=:99 WINEPREFIX=~/.mt5 wine "/root/.mt5/drive_c/Program Files/MetaTrader 5/terminal64.exe" &
sleep 15
x11vnc -display :99 -localhost -nopw -forever &
```

**On local machine (SSH tunnel):**
```bash
ssh -L 5900:127.0.0.1:5900 srv
```

Then connected with TigerVNC Viewer to `localhost:5900`. Inside MT5:
- `File → Login to Trade Account`
- Login: `26396767`, Password: (Vantage Markets password), tick **Save password**

MT5 saved credentials to `~/.mt5`. All future restarts auto-connect without any GUI.

---

## Part 4 — MQL5 EA (MT5Bridge.mq5)

### What it does

The EA runs a TCP HTTP server inside MT5 terminal on port 8002. It exposes 7 endpoints that give full trading API access via plain HTTP — no Windows-only Python package needed.

| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | Terminal connection status and build |
| `/account` | GET | Balance, equity, margin |
| `/symbol/{symbol}` | GET | Tick data for a symbol |
| `/candles` | GET | OHLCV candle data |
| `/positions` | GET | Open positions |
| `/order` | POST | Place a new order |
| `/close/{ticket}` | POST | Close a position |

### Key implementation details

- Uses `#import "ws2_32.dll"` for Windows socket API (MQL5 built-in Socket* only has client-side — no server sockets)
- Non-blocking server via `FIONBIO` (`0x8004667E` — no U suffix in MQL5)
- Client timeout via `SO_RCVTIMEO` / `SOL_SOCKET`
- `OnTimer` fires every **1000ms** (PollMs input) to accept and serve connections
- All JSON helper function signatures use `string` by value, NOT `const string &` (MQL5 rejects temporary string expressions as const ref params)

### Compile errors that were fixed

| Error | Fix |
|---|---|
| 15 errors: "parameter passed as reference" | Changed all function signatures from `const string &` → `string` |
| 10 errors: `SocketListen`/`SocketAccept` undeclared | Rewrote socket layer using `#import "ws2_32.dll"` |
| 3 errors: `0x8004667EU` invalid number | Removed `U` suffix from FIONBIO hex literal |
| `POSITION_COMMISSION` deprecated | Replaced with `double comm = 0` |

### How to recompile (requires VNC)

```bash
# On srv — start VNC
x11vnc -display :99 -localhost -nopw -forever &

# On local machine — SSH tunnel
ssh -L 5900:127.0.0.1:5900 srv
# Connect TigerVNC to localhost:5900
```

In MT5: press F4 → open MT5Bridge.mq5 → F7 to compile → 0 errors.  
To change PollMs: right-click EA smiley on chart → Properties → Inputs.

---

## Part 5 — Systemd Services

Both Xvfb and MT5 auto-start on boot and auto-restart on crash.

### `/etc/systemd/system/xvfb.service`

```ini
[Unit]
Description=Xvfb virtual framebuffer
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/Xvfb :99 -screen 0 1280x800x24
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### `/etc/systemd/system/mt5.service`

```ini
[Unit]
Description=MetaTrader 5 terminal
After=xvfb.service network.target
Requires=xvfb.service

[Service]
Type=simple
User=root
Environment=DISPLAY=:99
Environment=WINEPREFIX=/root/.mt5
ExecStart=/usr/local/bin/mt5-start.sh
Restart=always
RestartSec=15

[Install]
WantedBy=multi-user.target
```

### `/usr/local/bin/mt5-start.sh`

```bash
#!/bin/bash
exec /usr/bin/wine "/root/.mt5/drive_c/Program Files/MetaTrader 5/terminal64.exe"
```

> **Why the wrapper script?** systemd `ExecStart` does not parse shell quotes. The space in `Program Files` made Wine receive only `/root/.mt5/drive_c/Program` and crash with exit code 53. The wrapper script passes the full quoted path correctly.

### Commands

```bash
# Status
systemctl status xvfb mt5

# Stop MT5 only
systemctl stop mt5

# Start MT5 (Xvfb must be running first)
systemctl start mt5

# Stop everything
systemctl stop mt5 xvfb

# Start everything
systemctl start xvfb && sleep 3 && systemctl start mt5

# Verify EA is live
curl http://localhost:8002/health
curl http://localhost:8002/account
```

---

## Part 6 — Docker Stack (Coolify)

### Deployment

Managed by Coolify at `panel.rohithuge.online`. Resource UUID: `ukl02bmavgicbubey8wajoqr`.

- Source: GitHub repo `RohitHuge/MT5`, branch `main`
- Compose file: `docker-compose.yml` (repo root)
- Auto-redeploy on push: enabled

### Environment variables

Set in Coolify UI (not in `.env` file on server):

```
MT5_BRIDGE_URL=http://mt5-bridge:8000
EA_URL=http://host.docker.internal:8002
REDIS_URL=redis://redis:6379
POSTGRES_USER=trader
POSTGRES_PASSWORD=<set in Coolify>
POSTGRES_DB=mt5
POSTGRES_URL=postgresql://trader:<password>@postgres:5432/mt5
LOG_LEVEL=INFO
DASHBOARD_PORT=3000
```

Plus all strategy tuning parameters (EMA, RSI, risk %, etc.).

### How `host.docker.internal` works

The compose file has:
```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

This makes the hostname `host.docker.internal` resolve to the server's host IP from inside any container. `mt5-bridge` uses `EA_URL=http://host.docker.internal:8002` to call the EA running in MT5 on the host.

### Domain

Only the `dashboard` container has a public domain assigned in Coolify (port 3000). All other containers are internal to the Docker network.

---

## Part 7 — Deployment Issues and Fixes

### Port 8000 conflict
`mt5-bridge` originally had `ports: ["8000:8000"]` but port 8000 was already claimed by another Coolify app. Since `mt5-bridge` is internal-only (called by other containers on `mt5-net`), the port mapping was removed.

### Strategy containers: `IndexError: 2`
All three strategy `main.py` files had:
```python
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
```
In Docker, `main.py` is at `/app/main.py`. `parents[2]` doesn't exist — only `parents[0]` (`/app`) and `parents[1]` (`/`).  
**Fix:** Changed `parents[2]` → `parents[0]` in all three files.

### Shadow-tracker: `virtual_trades` table missing
The postgres volume mount `./shared/schema.sql:/docker-entrypoint-initdb.d/init.sql:ro` created a directory instead of a file (Docker behaviour when source path resolution fails at mount time). The init script never ran.  
**Fix:** Schema was applied directly:
```bash
docker exec -i <postgres-container> psql -U trader -d mt5 < schema.sql
```
Future redeploys won't re-run the init script because the postgres volume already has data.

### Dashboard: `TypeError: unhashable type: 'dict'`
Starlette `0.46+` changed the `TemplateResponse` API internals, breaking Jinja2's cache key computation.  
**Fix:** Pinned `starlette>=0.27,<0.46` in `dashboard/requirements.txt`.

---

## Part 8 — Verifying Everything Works

```bash
# EA health (on srv)
curl http://localhost:8002/health
# → {"status":"ok","terminal_connected":true,"terminal_build":6327,...}

curl http://localhost:8002/account
# → {"login":26396767,"balance":907.10,"equity":907.10,...}

# Docker stack (on srv)
docker ps --format '{{.Names}}\t{{.Status}}' | grep ukl02

# mt5-bridge reaching EA
docker logs <mt5-bridge-container> | grep "HTTP Request"
# → httpx: HTTP Request: GET http://host.docker.internal:8002/... "HTTP/1.1 200 OK"
```

---

## Quick Reference

| Task | Command |
|---|---|
| Check MT5 is running | `systemctl status mt5` |
| Check EA is live | `curl http://localhost:8002/health` |
| Restart MT5 | `systemctl restart mt5` |
| VNC into MT5 | Start `x11vnc -display :99 -localhost -nopw -forever &` on srv, then `ssh -L 5900:127.0.0.1:5900 srv` on local, connect TigerVNC to `localhost:5900` |
| Redeploy Docker stack | Push to `main` or hit Redeploy in Coolify |
| View container logs | Coolify dashboard → resource → Logs tab |
| Postgres shell | `docker exec -it <postgres-container> psql -U trader -d mt5` |
